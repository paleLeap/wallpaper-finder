#!/usr/bin/env python3
"""Wallpaper Finder - find wallpapers, look at cheap previews, keep the good ones.

The interface is a web page; this file is the host that draws it, and the only
side that can reach the network or the disk. `ui/scanner.js` knows what the
pointer is over, because it owns the DOM. Neither reaches into the other
except through the one channel at the top of that file.

Runs on Linux and on Windows. The host is Qt -- PySide6, with a WebEngine view
inside it -- and that is the whole reason it is Qt: this began as GTK with
WebKit2GTK, which is the right tool on Linux and does not exist on Windows at
all. Every place where the port had to find a GTK trick again in Qt is marked
`host:` in the code, so the seams can be read rather than guessed at.

Four things about the window are load-bearing, and all four were learned the
hard way on the desktop this was written for:

  Scaling.  The stylesheet is authored in device pixels, so one CSS pixel must
  be one real pixel. This desktop runs a 2x TEXT scale, which once made a
  panel authored 1600x900 render 3200x1800 inside its own window and cost a
  full session to diagnose. GTK was forced back to 96 DPI; Qt is told not to
  scale at all. WALLSCAN_SCALE puts it back for anyone who wants it bigger.

  Transparency.  The window has no background of its own and the page paints
  its own rounded ground onto that nothing. This needs a compositor: Windows
  always has one, Linux needs picom or equivalent.

  Opacity.  Deliberately NOT set. picom applies active-opacity 0.96 to any
  window it holds no rule for, so leaving it alone makes this window breathe
  like every other app here.

  Tooltips.  A browser turns any `title` attribute into a tooltip in the
  system font that no stylesheet in the page can reach. The markup uses
  aria-label only. That was true of WebKit and it is true of Chromium.

Everything this program writes stays inside its own folder, except the
wallpapers themselves -- which it downloads only for pictures you ticked, on a
button you pressed.
"""

import io
import json
import os
import pathlib
import random
import re
import signal
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request

# Qt rather than GTK, and the reason is Windows.
#
# This interface is a web page in a host window, and the host used to be GTK
# with WebKit2GTK inside it. That is the right tool on Linux and does not
# exist on Windows at all: WebKitGTK does not target Windows, and MSYS2 has no
# maintained package for it. Qt's WebEngine exists on both, arrives from one
# `pip install pyside6` on either, and puts the same Chromium under the same
# page on each -- so the stylesheet is not being asked to behave twice.
#
# Every window trick this program leans on had to be found again in Qt. Each
# one is marked `host:` where it lands, so the seams are readable rather than
# buried.
from PySide6.QtCore import (QFile, QObject, QRectF, Qt, QTimer,
                            QUrl, Signal, Slot)
from PySide6.QtGui import (QColor, QImage, QLinearGradient, QPainter)
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import (QWebEnginePage, QWebEngineScript,
                                     QWebEngineSettings)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication, QFileDialog, QMainWindow

PRGNAME = "pale-wallscan"

HERE = os.path.dirname(os.path.abspath(__file__))
HTML_PATH = os.path.join(HERE, "ui", "index.html")

# Where previews land while they are being looked at. Inside this folder, and
# emptied on the "stop and delete thumbnails" answer -- which is the whole
# reason they are real files rather than something drawn in the page: a
# question about deleting them should have something to delete.
THUMBS = os.path.join(HERE, "cache", "thumbs")

# Every wallpaper this program has ever put in front of him, one `source:id` a
# line. He asked for a rough record so it stops offering him the same things,
# and "rough" is the right word: it is a plain text file, appending costs one
# line, and deleting it forgets everything and starts again. That last property
# is why it is not a database.
#
# WALLSCAN_OFFERED points it somewhere else, for the same reason
# WALLSCAN_LIBRARY exists: a test run that writes into the real record would
# quietly retire wallpapers he was never shown. This one was caught after the
# fact -- two soak runs put 120 ids in his record before anyone noticed --
# which is why the override is here and the tests use it.
OFFERED = os.environ.get("WALLSCAN_OFFERED") or os.path.join(
    HERE, "offered.txt")

# The wallpapers themselves are the one thing that leaves this folder.
#
# WALLSCAN_LIBRARY points it somewhere else, which is how the save path gets
# tested without putting two wallpapers he did not choose into the folder his
# desktop rotates through. A test that has to dirty the real thing to prove
# itself is a test nobody runs twice.
LIBRARY = os.environ.get("WALLSCAN_LIBRARY") or os.path.expanduser(
    "~/Pictures/wallpapers")

# Where the last save went, so the folder only has to be chosen once.
#
# Beside the program rather than in a config directory, because everything
# this program writes stays in its own folder -- and a friend who wants to
# start again deletes one file they can see rather than hunting through
# ~/.config or AppData. One line, a path, nothing else.
SAVEDIR_FILE = os.environ.get("WALLSCAN_SAVEDIR_FILE") or os.path.join(
    HERE, "savedir.txt")


def setup_output():
    """Make sure this program has somewhere to print to. Windows, mostly.

    Two things go wrong there and both are silent.

    Started from `scan-quiet.cmd`, the program runs under pythonw.exe, which
    has no console at all and leaves sys.stdout as None. Every line this
    program writes -- and its log is the only account of what its page did --
    would go nowhere. They go to cache/launch.log instead, which is the same
    file the Linux menu launcher redirects into, so there is one place to look
    on either system.

    And a console on Windows is not necessarily UTF-8. This program's status
    lines are full of em dashes and middle dots; printing one to a cp1252
    console raises UnicodeEncodeError, which would take the program down from
    inside a print statement. Replacing an unprintable character is always
    better than dying of one.
    """
    if sys.stdout is None:
        os.makedirs(os.path.join(HERE, "cache"), exist_ok=True)
        log = io.open(os.path.join(HERE, "cache", "launch.log"), "a",
                      encoding="utf-8", errors="replace", buffering=1)
        sys.stdout = log
        sys.stderr = log
        return
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass                        # an older stream, or already wrapped


def pretty_path(path):
    """A path short enough to read on a button.

    The home folder becomes ~ on Linux, where that is the ordinary way to
    write it. On Windows it is left alone: nobody there reads ~ as a folder,
    and `C:\\Users\\you\\Pictures` is already the short form.
    """
    home = os.path.expanduser("~")
    if os.name != "nt" and home and path.startswith(home):
        return "~" + path[len(home):]
    return path


def load_savedir():
    """The remembered save folder, or the default library if there is none.

    A remembered folder that has since been deleted or unplugged is ignored
    rather than offered: the question the page asks is "where shall I put
    these", and answering it with a path that no longer exists is worse than
    answering it with the default.
    """
    try:
        with open(SAVEDIR_FILE, encoding="utf-8") as handle:
            line = handle.read().strip()
    except OSError:
        return LIBRARY
    if line and os.path.isdir(line):
        return line
    return LIBRARY


def save_savedir(path):
    try:
        with open(SAVEDIR_FILE, "w", encoding="utf-8") as handle:
            handle.write(path + "\n")
    except OSError as exc:
        print("scanner: could not remember the save folder: %s" % exc,
              flush=True)


# Device pixels on a 3840x2160 panel. One CSS px == one device px, so these
# are the same numbers the stylesheet uses. His note on the design says the
# sizes are not exact and to use common sense for scale, which is why every
# window here is resizable.
# The bar gained a colour strip under the two panes, so it is taller than the
# 250 it was. The strip went below the panes rather than beside them because
# the themes list is the one that grows now that it comes from the sites, and
# a third column would have taken that growth back off it again.
#
# 360 rather than the 306 the strip alone needs, and the extra 54 is not
# padding: at 306 the panes had 166px between them against the 192 they had
# before, so adding the colour row had quietly made the themes list SHORTER
# at the very moment it went from eighteen entries to hundreds. Seen on screen
# 2026-08-26. At 360 they have 220px, which is more room than they started
# with. It is still only the opening size -- the window resizes.
# 585 rather than the 360 it was, and the extra 225 is not padding. The
# sources are cards now -- a name, what the source is, and what it needs --
# and at 360 only two of the four were on screen, so Commons and Pexels
# existed only for whoever thought to scroll a pane that does not look like it
# scrolls. Seen on screen 2026-09-16, not reasoned about, and 470 was tried
# first and still cut the fourth card off, 520 clipped its last line, and
# 545 stopped fitting once the Images/GIFs toggle took its 37px off the top.
# Every one of those was the same defect found the same way -- the self-test
# measures the list against the pane and says so. The themes list
# gains the same 110px, which it can always use.
BAR_W, BAR_H = 1180, 585          # stage one: sources, themes, size, colour
BAR_MIN_W, BAR_MIN_H = 820, 360
ASK_W, ASK_H = 360, 196           # stage two: "How many?"
RESULTS_W, RESULTS_H = 1760, 1180  # stage three: the grid
RESULTS_MIN_W, RESULTS_MIN_H = 900, 420

# What the window shades to when the roll-up square is pressed. Must match
# --strip-h in ui/scanner.css.
STRIP_H = 86

# Must match --radius in ui/scanner.css. The shape below is what makes the
# corners real; the stylesheet only draws them.
RADIUS = 26

# host: the eight grips down the window's edges. Qt takes a combination of
# edges where GTK took one named corner, so a corner is two flags ORed rather
# than a ninth constant.
EDGES = {
    "n": Qt.TopEdge,
    "s": Qt.BottomEdge,
    "e": Qt.RightEdge,
    "w": Qt.LeftEdge,
    "nw": Qt.TopEdge | Qt.LeftEdge,
    "ne": Qt.TopEdge | Qt.RightEdge,
    "sw": Qt.BottomEdge | Qt.LeftEdge,
    "se": Qt.BottomEdge | Qt.RightEdge,
}

# Which sources can be reached without an account. Measured with live calls on
# 2026-08-22 and recorded in HANDOFF.md; the two that are off are off because
# they answered 401 and 403, not because of a preference.
def _keys():
    """API keys, from `keys.txt` beside this file. One `name = value` a line.

    A file rather than anything else for one reason: he never has to type a key
    where a session can see it. Nothing here ever prints a key, and keys.txt is
    in .gitignore, so it cannot reach the Mantle repo either.

    A source with no key is not offered. It says why on its own pill rather
    than failing at the first request.
    """
    found = {}
    try:
        with open(os.path.join(HERE, "keys.txt"),
                  encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                name, _, value = line.partition("=")
                value = value.strip()
                if value:
                    found[name.strip().lower()] = value
    except OSError:
        pass
    return found


KEYS = _keys()


# Two kinds of search, because a GIF is not a wallpaper and the difference
# runs deeper than the file extension. A GIF is 200 to 500 pixels wide, so the
# 4K floor that is right for a wallpaper returns nothing at all; the sources
# that hold them are not the sources that hold wallpapers; and neither GIF
# source reports a colour. Each kind therefore carries its own sources, its own
# sizes and its own default, and the panel swaps when you switch.
KINDS = [
    {"id": "images", "name": "Images"},
    {"id": "gifs", "name": "GIFs"},
]
DEFAULT_KIND = "images"

SOURCES = [
    # One line each, and they are short on purpose: four cards have to fit the
    # panel without scrolling, and a panel that hides half its sources behind
    # a scroll nobody expects is the same as not listing them. The long
    # version of any of this lives in the README and in the key window.
    #
    # `key` names the line in keys.txt that turns the source on. A source with
    # one is listed whether or not the key is there -- a key is something a
    # person can go and get, which is the whole difference between this and
    # the three that are not here at all.
    {"id": "wallhaven", "name": "wallhaven", "ready": True,
     "what": "The biggest pool here — about 59,000 at 4K or better.",
     "needs": "Needs nothing: no account, no key."},
    {"id": "wallhaven-fav", "name": "wallhaven · loved", "ready": True,
     "what": "The same wallpapers, ordered by how many people kept them.",
     "needs": "Needs nothing."},
    {"id": "commons", "name": "Wikimedia Commons", "ready": True,
     "what": "Photographs and scans. Big files, and rarely 16:9.",
     "needs": "Needs nothing, but an email or URL in contact.txt roughly "
              "doubles what comes back."},
    {"id": "pexels", "name": "Pexels", "key": "pexels",
     "what": "A free stock photo library. Landscapes and cities mostly.",
     "needs": "Needs a free key from pexels.com/api, put in keys.txt.",
     "signup": "https://www.pexels.com/api/",
     "why": [
         "Pexels answers 401 — \u201cMissing API key\u201d — to a call without "
         "one. Every call, not just some: an early version of this program "
         "believed Pexels worked keyless, because the 200s it was reading "
         "came from their edge cache rather than their API.",
         "The key is free and instant. Register, copy the key from your "
         "dashboard, and paste it below.",
     ]},

    # ---- GIFs ----
    {"id": "giphy", "name": "GIPHY", "kind": "gifs", "key": "giphy",
     "what": "The big GIF library. Reactions, loops, clips.",
     "needs": "Needs a free key from developers.giphy.com — instant, but "
              "rate limited to 100 searches an hour.",
     "signup": "https://developers.giphy.com/",
     "why": [
         "GIPHY answers 401 Unauthorized to a call without a key. Measured "
         "here, so it is what will happen rather than what is documented.",
         "The key is free and instant: create an account, press Create an "
         "App, choose the API rather than the SDK, and copy the key. It is "
         "rate limited to 100 searches an hour and 1,000 a day, which this "
         "program stays inside — one search costs a call or two, because a "
         "page is fifty GIFs.",
     ]},
]

# Everything without a kind of its own is a wallpaper source.
for _s in SOURCES:
    _s.setdefault("kind", "images")


def sources_for(kind):
    """The sources for one kind, each with its readiness worked out now.

    Computed rather than stored, because a key can arrive while the window is
    open: the key window writes one and this has to start saying yes without a
    restart.
    """
    out = []
    for source in SOURCES:
        if source["kind"] != (kind or DEFAULT_KIND):
            continue
        row = dict(source)
        name = row.get("key")
        row["ready"] = (not name) or bool(KEYS.get(name))
        out.append(row)
    return out

# Unsplash, Pixabay and Reddit used to be listed here, greyed out, explaining
# themselves. They are gone, and the rule that removed them is his: a source
# that cannot be made to work without editing this file should not be on the
# panel at all.
#
# None of the three has an adapter -- there is no code here that could read
# them even holding a key -- so every one of them was a button that could only
# ever say no. What each would need, if any of them is ever built:
#
#   Unsplash     a key, applied for by hand and reviewed in 5-10 working days
#   Pixabay      more than a key. A free key only downloads `largeImageURL`,
#                which came back 1280x853 for a picture the API called
#                6000x4000 (2026-08-22). Sub-HD files under a 6000px label is
#                worse than not having the source.
#   Reddit       a registered app rather than a key, and the plain JSON feed
#                answers 403 without one (2026-08-22).
#
# Tenor is the other GIF library and is not here either, for a harder reason
# than a key: Google stopped issuing Tenor API keys on 13 January 2026 and cut
# off third-party access entirely on 30 June 2026 -- the shutdown that broke
# the GIF pickers in Discord, WhatsApp and X. There is no key to go and get,
# so by the same rule it is not on the panel. GIPHY is the one that is left.

# Which site a pool belongs to. This is what a saved file is named after and
# what "do I already have this" is keyed on -- NOT the pool.
#
# Load-bearing: wallhaven's random pool and its loved pool are the same 59,118
# wallpapers in a different order, so the same picture reached two ways must
# produce the same filename. Naming files after the pool would have saved
# `wallhaven-fav-abc123.jpg`, which the already-have check -- built on his
# folder's existing `wallhaven-<id>` habit -- would never have matched, and he
# would have been re-offered wallpapers he already owned.
SITE = {
    "wallhaven": "wallhaven", "wallhaven-fav": "wallhaven",
    "commons": "commons", "pexels": "pexels", "giphy": "giphy",
}

# The eighteen words this program shipped with. They are no longer the theme
# list -- they are the floor under it, used only when the cache is missing.
STARTER_THEMES = ["City", "Night", "Mountain", "Road", "Forest", "Rain",
                  "Neon", "Coast", "Desert", "Snow", "Fog", "Ruins", "Bridge",
                  "Storm", "Harbour", "Skyline", "Valley", "Aurora"]

# The themes really come from here: a file harvested from the sources
# themselves and shipped alongside the program.
#
# HANDOFF.md said the panel could not be filled from wallhaven because it has
# no endpoint listing its tags. The endpoint half is true and the conclusion
# was wrong; the correction is written up in HANDOFF.md's Limits section
# rather than quietly patched away. What is true (measured 2026-08-26):
#
#   * `/api/v1/tag/<n>` works and the ids are sequential, so the whole index
#     CAN be walked. It should not be. The space runs past 120,000 with gaps
#     (100, 50,000 and 200,000 all answer 404), which is roughly 45 HOURS at
#     wallhaven's own 45 requests a minute -- and the contents are wrong for
#     this panel anyway. Tag 20,000 is a model's name and tag 80,000 is a
#     pornstar's. A blind walk would fill his themes panel with people.
#
#   * `/api/v1/w/<id>` returns the tags of ONE wallpaper, each with the
#     category wallhaven files it under. Run against wallpapers that already
#     passed this program's own filters -- 3840x2160 and up, General, SFW --
#     every word it yields is a word attached to the kind of picture he is
#     actually looking for, and the category field is what keeps the people
#     out. About 600 wallpapers is a quarter of an hour, not two days.
#
# The undocumented `/autocomplete` endpoint the website uses was tried first
# and answers 404 to this program (2026-08-26).
THEME_CACHE = os.path.join(HERE, "cache", "themes.json")

# Past this, a cached harvest is refreshed -- in the background, never at
# startup. A window that waits on a quarter of an hour of network before it
# draws is the exact failure this program's own comments say to avoid.
THEME_MAX_AGE_DAYS = 30

# Which of wallhaven's own tag categories are themes for a wallpaper, and
# which are the names of people. Measured against live tag data 2026-08-26.
# `Anime & Manga` and `Characters` are dropped as well: this program searches
# `categories=100`, which is General only, so an anime tag here would be a
# word that returns nothing.
THEME_CATEGORIES_KEEP = {
    "Nature", "Landscapes", "Animals", "Plants", "Weather", "Space",
    "Architecture", "Cities", "Miscellaneous", "Digital", "Other",
    "Cars & Motorcycles", "Aircraft", "Boats", "Technology", "Food",
    "Games", "Movies", "Music", "Sports", "Abstract", "Textures",
}

# Commons hands out its category tree, which is a genuine source of theme
# words. These are the branches worth reading; their sub-categories are the
# words. `research/commons2.json` is the working search query this pairs with.
COMMONS_THEME_SEEDS = ["Landscapes", "Nature", "Weather", "Skies",
                       "Mountains", "Water", "Cityscapes", "Seasons"]

# A tag has to earn its pill. These three rules were tuned against the first
# real harvest and each one throws away a different kind of rubbish:
#   - seen at least twice, which separates a theme ("mountains") from one
#     wallpaper's own trivia ("Audi R18")
#   - short enough to read on a pill
#   - actual words, which drops bare model numbers and stray punctuation
THEME_MIN_HITS = 2
THEME_MAX_LEN = 22

# Commons' category tree is a genuine source of theme words and also a filing
# cabinet, and the first harvest brought back both: alongside `Mountains` and
# `Dark Skies` it returned `Quality Images`, `Aqua (Text)`, `Pronunciation`,
# `Gain-Mapped Hdr Images` and `Boil Water Advisories` (measured 2026-08-26).
# Those are Wikimedia's housekeeping, not his themes, and each one would be a
# pill that returns nothing. Three rules clear nearly all of it: no brackets,
# no more than two words, and none of the filing words below.
COMMONS_STOPWORDS = {
    "categories", "category", "media", "file", "files", "image", "images",
    "photo", "photos", "photographs", "picture", "pictures", "video",
    "videos", "animation", "animations", "diagram", "diagrams", "map",
    "maps", "svg", "png", "jpeg", "gallery", "galleries", "template",
    "templates", "user", "users", "wikipedia", "wikimedia", "commons",
    "featured", "quality", "valued", "pronunciation", "history", "book",
    "books", "text", "texts", "list", "lists", "unidentified", "vanished",
    "topics", "subcategories", "metadata", "cc", "pd", "audio", "sound",
    "sounds", "logos", "icons", "flags", "coats", "arms", "stamps", "seals",
    "documents", "scans", "sources", "works", "authors", "artists", "people",
    "models", "portraits",
}


# The nouns a two-word Commons category may end on and still be a theme.
COMMONS_HEADS = {
    "landscape", "landscapes", "sky", "skies", "season", "seasons",
    "cityscape", "cityscapes", "mountain", "mountains", "water", "waters",
    "weather", "nature", "cloud", "clouds", "forest", "forests", "sea",
    "seas", "coast", "coasts", "river", "rivers", "lake", "lakes", "light",
    "lights", "night", "sunset", "sunrise", "storm", "storms", "snow",
    "rain", "fog", "mist", "desert", "deserts", "valley", "valleys",
    "island", "islands", "field", "fields", "hill", "hills", "rock",
    "rocks", "tree", "trees", "flower", "flowers", "view", "views",
}


def _commons_ok(word):
    """The extra strictness Commons needs and wallhaven's tags do not.

    wallhaven's words are already filtered twice -- by its own tag category
    and by having to appear on two different wallpapers. Commons' categories
    have neither check behind them, so they get this one instead.
    """
    if "(" in word or ")" in word:
        return False
    parts = word.split()
    if len(parts) > 2:
        return False
    if any(p.strip(",.'-").lower() in COMMONS_STOPWORDS for p in parts):
        return False
    # A two-word category is a theme when the noun it ends on is one of the
    # branches being walked -- `Dark Skies`, `Dry Season`, `Night Cityscapes`
    # -- and Wikimedia's filing when it is not: `Mountain Mailboxes`,
    # `Landscape Gardeners`, `Mountain Signs` all came back from the first
    # harvest and all end on something that is not a theme (2026-08-26).
    if len(parts) == 2:
        return parts[-1].strip(",.'-").lower() in COMMONS_HEADS
    return True


def _theme_ok(word):
    """Is this tag a theme, or is it one wallpaper's trivia?"""
    if not word or len(word) > THEME_MAX_LEN or len(word) < 3:
        return False
    if not re.search(r"[A-Za-z]", word):
        return False
    # A word carrying digits is nearly always a model number or a year.
    if re.search(r"\d", word):
        return False
    return True


def load_themes():
    """The theme list, from the shipped cache, falling back to the starters.

    Reads a file and nothing else -- no network, no waiting. Whatever this
    returns is on screen the moment the window opens.
    """
    try:
        with open(THEME_CACHE, encoding="utf-8") as handle:
            blob = json.load(handle)
        words = [w for w in blob.get("themes") or [] if _theme_ok(w)]
        if words:
            return words
    except (OSError, ValueError):
        pass
    return list(STARTER_THEMES)


def theme_cache_age_days():
    """How old the harvest is, in days. None if there is no cache."""
    try:
        with open(THEME_CACHE, encoding="utf-8") as handle:
            stamp = json.load(handle).get("harvested")
        then = time.mktime(time.strptime(stamp, "%Y-%m-%d"))
        return (time.time() - then) / 86400.0
    except (OSError, ValueError, TypeError):
        return None


def _harvest_wallhaven_tags(budget, stopflag=None, note=print):
    """Theme words from the tags of wallpapers that already pass our filters.

    One request per wallpaper, paced under wallhaven's own 45 a minute. Every
    word comes back with the category wallhaven files it under, and the
    person-name categories are dropped here rather than shown to him.
    """
    hits = {}
    seed = None
    page = 1
    looked = 0
    while looked < budget:
        if stopflag is not None and stopflag.is_set():
            break
        try:
            payload = wallhaven_page("", page, seed, "random")
        except Exception as exc:
            note("theme harvest: wallhaven page %d failed: %s" % (page, exc))
            break
        seed = (payload.get("meta") or {}).get("seed") or seed
        rows = payload.get("data") or []
        if not rows:
            break
        page += 1
        for row in rows:
            if looked >= budget:
                break
            if stopflag is not None and stopflag.is_set():
                break
            time.sleep(API_MIN_GAP)
            try:
                blob = fetch("%s/%s" % (WALLHAVEN_WALL, row.get("id")),
                             limits_for="wallhaven")
                tags = (json.loads(blob.decode("utf-8")).get("data")
                        or {}).get("tags") or []
            except Exception:
                continue                   # one wallpaper is not the harvest
            looked += 1
            for tag in tags:
                if (tag.get("category") or "") not in THEME_CATEGORIES_KEEP:
                    continue
                word = (tag.get("name") or "").strip()
                if _theme_ok(word):
                    hits[word.title()] = hits.get(word.title(), 0) + 1
            if looked % 50 == 0:
                note("theme harvest: %d wallpapers read, %d words so far"
                     % (looked, len(hits)))
        time.sleep(API_MIN_GAP)
    return {w: n for w, n in hits.items() if n >= THEME_MIN_HITS}, looked


def _harvest_commons_categories(stopflag=None, note=print):
    """Theme words from Commons' own category tree.

    The sub-categories of a handful of branches, with Commons' housekeeping
    words trimmed off the front and back -- `Category:Mountains of Norway`
    is a theme once it is just `Mountains`.
    """
    words = {}
    for seed in COMMONS_THEME_SEEDS:
        if stopflag is not None and stopflag.is_set():
            break
        params = {"action": "query", "list": "categorymembers",
                  "cmtitle": "Category:" + seed, "cmtype": "subcat",
                  "cmlimit": "500", "format": "json"}
        try:
            blob = fetch(COMMONS_API + "?" + urllib.parse.urlencode(params))
            members = ((json.loads(blob.decode("utf-8")).get("query") or {})
                       .get("categorymembers") or [])
        except Exception as exc:
            note("theme harvest: commons %s failed: %s" % (seed, exc))
            continue
        for entry in members:
            name = (entry.get("title") or "").replace("Category:", "")
            # `Mountains of Norway` -> `Mountains`. What follows `by`, `of`,
            # `in`, `with` or `from` is a place or a qualifier, not a theme.
            name = re.split(
                r"\s+(?:by|of|in|with|from|and|about|on|for|at)\s+",
                name)[0]
            name = name.strip()
            if _theme_ok(name) and _commons_ok(name):
                words[name.title()] = words.get(name.title(), 0) + 1
        time.sleep(SOURCE_GAP.get("commons", API_MIN_GAP))
    return words


def harvest_themes(budget=600, stopflag=None, note=print):
    """Fill the theme cache from the sources. Slow, and never on the main loop.

    Writes `cache/themes.json`, which carries its own count and the date it
    was harvested, so the panel can always be asked where its words came from.
    """
    note("theme harvest: starting, budget %d wallpapers (about %d minutes)"
         % (budget, int(budget * API_MIN_GAP / 60) + 1))
    wh, looked = _harvest_wallhaven_tags(budget, stopflag, note)
    cm = _harvest_commons_categories(stopflag, note)
    merged = dict(wh)
    for word, n in cm.items():
        merged[word] = merged.get(word, 0) + n
    words = sorted(merged, key=lambda w: (-merged[w], w))
    blob = {
        "harvested": time.strftime("%Y-%m-%d"),
        "count": len(words),
        "wallpapers_read": looked,
        "from": {"wallhaven": len(wh), "commons": len(cm)},
        "note": ("Harvested from the sources themselves: wallhaven tags "
                 "carried by wallpapers that pass this program's own filters "
                 "(3840x2160+, General, SFW), and Wikimedia Commons' category "
                 "tree. Person-name categories are excluded."),
        "themes": words,
    }
    os.makedirs(os.path.dirname(THEME_CACHE), exist_ok=True)
    tmp = THEME_CACHE + ".part"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(blob, handle, indent=1)
    os.replace(tmp, THEME_CACHE)           # never a half-written cache
    note("theme harvest: %d themes written to %s (%d from wallhaven, %d from "
         "Commons, %d wallpapers read)"
         % (len(words), THEME_CACHE, len(wh), len(cm), looked))
    return blob


THEMES = load_themes()

# ---- the main colour ----------------------------------------------------
#
# wallhaven does the colour filtering at its end, and it will only do it for
# its own fixed swatches: `colors=123456` and `colors=ff00ff` both come back
# with a total of 0, so the filter is not being ignored -- it is a closed set
# (measured 2026-08-26). These thirty are the swatches wallhaven actually
# returned across 95 live results the same day, which is why they are written
# down rather than invented.
#
# Two things this palette is NOT:
#
#   * It is not a claim that Pexels understands any of it. Pexels' documented
#     `color=` parameter is accepted and then ignored: `color=white` and
#     `color=black` returned a byte-identical list of 39 photo ids on the live
#     API with its rate-limit headers present, so this is not the edge cache
#     that caught us before (measured 2026-08-26). Pexels is filtered here
#     instead, on the `avg_color` it does report.
#
#   * It is not chrome. The house rule that there is no red in this program is
#     about telling Stop from Go without a warning colour; it is not about
#     which colours he may go looking for in a photograph.
#
# Ordered for the eye -- neutrals, blues, greens, warms, browns, reds, pinks,
# purples -- rather than by hex, because this is a row he reads along.
COLOURS = [
    {"hex": "#000000", "name": "Black"},
    {"hex": "#424153", "name": "Slate"},
    {"hex": "#999999", "name": "Grey"},
    {"hex": "#cccccc", "name": "Light grey"},
    {"hex": "#ffffff", "name": "White"},
    {"hex": "#abbcda", "name": "Pale blue"},
    {"hex": "#0099cc", "name": "Sky blue"},
    {"hex": "#0066cc", "name": "Blue"},
    {"hex": "#333399", "name": "Indigo"},
    {"hex": "#66cccc", "name": "Turquoise"},
    {"hex": "#336600", "name": "Dark green"},
    {"hex": "#669900", "name": "Green"},
    {"hex": "#77cc33", "name": "Bright green"},
    {"hex": "#666600", "name": "Olive"},
    {"hex": "#999900", "name": "Mustard"},
    {"hex": "#cccc33", "name": "Yellow"},
    {"hex": "#ffcc33", "name": "Amber"},
    {"hex": "#ff9900", "name": "Orange"},
    {"hex": "#cc6633", "name": "Terracotta"},
    {"hex": "#e7d8b1", "name": "Sand"},
    {"hex": "#996633", "name": "Brown"},
    {"hex": "#663300", "name": "Dark brown"},
    {"hex": "#cc0000", "name": "Red"},
    {"hex": "#cc3333", "name": "Brick"},
    {"hex": "#990000", "name": "Dark red"},
    {"hex": "#660000", "name": "Maroon"},
    {"hex": "#ea4c88", "name": "Pink"},
    {"hex": "#fdadc7", "name": "Pale pink"},
    {"hex": "#993399", "name": "Magenta"},
    {"hex": "#663399", "name": "Purple"},
]

COLOUR_HEXES = [c["hex"] for c in COLOURS]
COLOUR_NAMES = {c["hex"]: c["name"] for c in COLOURS}

# How hard the colour is held, and what happens when it runs out.
#
# This matters more than it sounds, and it is measured. Asking wallhaven for a
# colour means "this colour appears in the picture", not "the picture is
# mostly this colour" -- and wallhaven returns its five swatches strongest
# first. Across 48 white results, white was the strongest swatch in 5 of them
# and the weakest in 16 (2026-08-26). Taking the site's answer as given would
# have handed him dark pictures with a white patch and called them white.
#
# So the search starts strict -- the colour must be the dominant swatch -- and
# widens a step only when strict has nothing left to give, saying so when it
# does. His choice, made 2026-08-26 when the measurement was put to him.
COLOUR_LEVELS = [0, 1, 4]
COLOUR_LEVEL_NAMES = ["mostly", "strongly", "containing"]

# How many turns a source may come back with nothing that clears the current
# strictness before the search widens. Two rather than one: a single page of
# 24 that happens to contain no dominant-white picture is ordinary, and
# widening on it would make "strict" mean almost nothing.
COLOUR_DRY_TURNS = 2


def _rgb(hexstr):
    h = hexstr.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _colour_gap(a, b):
    """How far apart two colours are. Plain squared distance in RGB.

    Not a perceptual metric, and it does not need to be: it is only ever used
    to rank the same thirty fixed swatches against one photograph's average,
    and the swatches are far enough apart that a better metric would order
    them the same way.
    """
    x, y = _rgb(a), _rgb(b)
    return sum((x[i] - y[i]) ** 2 for i in range(3))


def colour_rank_from_average(avg, colour):
    """Where `colour` comes in the ranking of swatches nearest this average.

    Pexels reports one average colour per photo and nothing else, so "is this
    photo mostly white" has to be answered here. 0 means the chosen swatch is
    the closest of the thirty to the photo's average -- as near as this source
    can get to "mostly".
    """
    if not avg or not colour:
        return None
    try:
        order = sorted(COLOUR_HEXES, key=lambda h: _colour_gap(h, avg))
    except (ValueError, IndexError):
        return None
    return order.index(colour) if colour in order else None


WALLHAVEN_SEARCH = "https://wallhaven.cc/api/v1/search"
# One wallpaper, with its tags. The search endpoint does not carry
# tags, which is why the harvest above pays a request per wallpaper.
WALLHAVEN_WALL = "https://wallhaven.cc/api/v1/w"

# Measured against the live API 2026-08-22: no key is needed for safe content,
# 24 results come back a page, and the response's own `x-ratelimit-limit`
# header reads 45. The gap below keeps this under that ceiling rather than on
# it -- one user is not worth being rate-limited over.
API_MIN_GAP = 1.5

# Seconds between search calls, per source. Every figure below was read off the
# service's own response headers on 2026-08-22, not taken from documentation.
#
#   wallhaven  X-RateLimit-Limit: 45 a minute      -> 1.34s, rounded up
#   pexels     X-RateLimit-Limit: 25000, reset far out; their published free
#              tier is 200 an hour, which is the tighter of the two and the one
#              worth respecting -> 18s would be the letter of it, but a search
#              here makes one call per page, so 2s and a hard stop on the
#              remaining-count is both safe and usable
#   commons    no published number; held to the same pace as wallhaven, which
#              is far below anything they would notice
# GIPHY's free key allows 100 searches an hour, which is the tightest limit
# of anything here -- 36 seconds a call if it were spent evenly. It is not
# spent evenly: a page is 50 GIFs, so an ordinary search costs one or two
# calls. 3 seconds keeps a long session well inside it without making the
# grid crawl.
SOURCE_GAP = {"wallhaven": 1.5, "wallhaven-fav": 1.5, "commons": 1.5,
              "pexels": 2.0, "giphy": 3.0}

# Stop using a source when its own remaining-count gets this low, rather than
# discovering the limit by being refused. His words: hardwire it so I do not
# get locked out.
RATE_FLOOR = 20

# `large` is 432x243 and about 10 KB, against 4.7 MB for the full file --
# both measured on one real result 2026-08-22. That ratio is the entire reason
# previews exist, and why nothing is downloaded at size until it is ticked.
THUMB_SIZE = "large"

# The floor, and now a choice rather than a constant.
#
# 4K stays the default, because every one of the 66 images already in his
# library is 3840 wide or more and anything under it would be the first thing
# he threw away. The others are here because his friends' screens are not this
# screen: a 1080p laptop asking for 4K gets a tenth of the results and a much
# slower search for pictures it will only scale down.
#
# `atleast` is wallhaven's own parameter and does the rejecting at their end.
# `w` is the same number for the two sources that have no such parameter and
# must be filtered here. Zero means no floor at all.
SIZES = [
    {"id": "any", "name": "Any size", "atleast": "", "w": 0},
    {"id": "1920x1080", "name": "1920 × 1080 · Full HD",
     "atleast": "1920x1080", "w": 1920},
    {"id": "2560x1440", "name": "2560 × 1440 · 1440p",
     "atleast": "2560x1440", "w": 2560},
    {"id": "3840x2160", "name": "3840 × 2160 · 4K",
     "atleast": "3840x2160", "w": 3840},
    {"id": "5120x2880", "name": "5120 × 2880 · 5K",
     "atleast": "5120x2880", "w": 5120},
    {"id": "7680x4320", "name": "7680 × 4320 · 8K",
     "atleast": "7680x4320", "w": 7680},
    # One exact size, typed in. Everything above is a floor -- "this big or
    # bigger" -- and a banner is not a floor: 1920x480 asked for as a minimum
    # returns every 4K wallpaper on the site, because they all clear it.
    #
    # wallhaven answers this properly and it is measured, not assumed:
    # `resolutions=1920x1080` came back with 90,858 results, every one of them
    # exactly 1920x1080, and `resolutions=3440x1440` with 2,230, same (both
    # 2026-09-16). The other two sources have no such parameter and are
    # filtered here, where an exact size will nearly always come back empty --
    # which the page says on the panel rather than leaving to be discovered.
    {"id": "exact", "name": "Specific size…", "atleast": "", "w": 0,
     "exact": True},
]
# GIFs live at a completely different scale. A 4K floor on a GIF search
# returns nothing, so this list starts at "any" and that is also its default:
# most of GIPHY is between 200 and 500 pixels wide.
GIF_SIZES = [
    {"id": "any", "name": "Any size", "atleast": "", "w": 0},
    {"id": "480w", "name": "480 wide or more", "atleast": "", "w": 480},
    {"id": "720w", "name": "720 wide or more", "atleast": "", "w": 720},
    {"id": "1080w", "name": "1080 wide or more", "atleast": "", "w": 1080},
]

DEFAULT_SIZE = "3840x2160"
GIF_DEFAULT_SIZE = "any"
# The exact-size entry is shared: "1920 by 480 and nothing else" means the
# same thing whichever kind is being searched.
EXACT_ENTRY = [z for z in SIZES if z.get("exact")]
GIF_SIZES = GIF_SIZES + EXACT_ENTRY

SIZE_BY_ID = {z["id"]: z for z in SIZES}
SIZE_BY_ID.update({z["id"]: z for z in GIF_SIZES})


def sizes_for(kind):
    """The size list and its default, for one kind of search."""
    if kind == "gifs":
        return GIF_SIZES, GIF_DEFAULT_SIZE
    return SIZES, DEFAULT_SIZE


def size_or_default(size):
    """The chosen size as a dict, whatever form it arrives in.

    A dict passes straight through -- that is a specific size someone typed,
    built by `exact_size` below. An id is looked up. Anything this program has
    never written down becomes 4K rather than quietly becoming "any size": a
    floor that silently disappears is the one failure nobody would notice.
    """
    if isinstance(size, dict):
        return size
    return SIZE_BY_ID.get(size or "", SIZE_BY_ID[DEFAULT_SIZE])


def exact_size(width, height):
    """One typed-in size, as the fetchers want it.

    Clamped rather than rejected: the page only sends digits, but a pasted
    50000 would be a search no source can answer and a 0 would be a filter
    that lets everything through.
    """
    width = max(1, min(int(width or 0), 20000))
    height = max(1, min(int(height or 0), 20000))
    return {"id": "exact", "name": "%d × %d exactly" % (width, height),
            "atleast": "", "w": width, "h": height, "exact": True}


ATLEAST = SIZE_BY_ID[DEFAULT_SIZE]["atleast"]

# Says who is calling. A scanner that hides what it is deserves the block it
# gets; Reddit already returns 403 to anything it does not recognise.
#
# Wikimedia goes further and requires a way to *contact* whoever is running
# the tool. Measured 2026-08-22, same search, same 0.7s gap between requests:
# with a contact in the User-Agent 8 of 8 thumbnails came back; without one,
# 4 of 8, the rest 429 citing their robot policy by name. It is the header
# that decides it, not the speed.
#
# The contact is read from `contact.txt` beside this file rather than written
# in, because it is personal information leaving this machine and that is his
# choice to make, not this program's. No file, no contact, and Commons is
# offered with a warning instead of quietly failing half its previews.
def _contact():
    path = os.path.join(HERE, "contact.txt")
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line and not line.startswith("#"):
                    return line
    except OSError:
        pass
    return os.environ.get("WALLSCAN_CONTACT", "").strip()


CONTACT = _contact()
USER_AGENT = ("wallpaper-scanner/0.1 (%s)"
              % (CONTACT if CONTACT
                 else "one person, personal desktop use, no contact given"))

# How long to leave between two picture fetches from one source. wallhaven
# serves previews off a CDN and does not mind; Wikimedia does. Only the search
# calls were paced before, which is why a Commons page of 24 thumbnails
# arrived as a burst and got most of itself refused.
THUMB_GAP = {"commons": 0.7, "wallhaven": 0.0, "giphy": 0.2}

COMMONS_API = "https://commons.wikimedia.org/w/api.php"

# Commons' search understands a width filter, which does the rejecting at
# their end instead of over the wire: `filew:>3839` returned six results all
# 4524 wide or more, measured 2026-08-22. `filetype:bitmap` keeps out the SVGs
# and PDFs that also live in the file namespace.
def commons_filter(min_w, min_h=0):
    """Commons' search understands width and height filters, so the rejecting
    happens at their end. `filetype:bitmap` keeps out the SVGs and PDFs that
    also live in the file namespace, and is wanted whatever the size.

    For an exact size this narrows to "at least", and the exact match is made
    on the rows themselves -- Commons has no "exactly this" to ask for.
    """
    bits = ["filetype:bitmap"]
    if min_w > 0:
        bits.append("filew:>%d" % (min_w - 1))
    if min_h > 0:
        bits.append("fileh:>%d" % (min_h - 1))
    return " ".join(bits)

# Commons needs something to search for. Everything else here runs happily on
# an empty query; this one returns nothing at all without a term.
COMMONS_DEFAULT = "landscape"

# Commons' curated corners were built and then removed the same day, and the
# reason is worth keeping. `incategory:"Featured pictures on Wikimedia Commons"`
# and `incategory:"Quality images"` both work -- a nonsense category returns 0,
# so the filter is not being ignored -- but at 3840 wide and up they return
# what the plain search already returns: 24 of 24 shared with plain for
# featured, 20 of 24 for quality (2026-08-22). Above 4K, Commons' high-
# resolution uploads very largely ARE its curated ones. Three pools returning
# the same wallpapers is not three sources.
COMMONS_POOL = {"commons": ""}

# The floor as a number, for checking rows a source filtered itself. The
# chosen size carries it now; this is what a call that was given no size at
# all falls back to.
MIN_WIDTH = SIZE_BY_ID[DEFAULT_SIZE]["w"]

# Placeholders instead of the network. The self-test needs a run that does not
# depend on wallhaven being up, and a deterministic one it can assert against.
# `WALLSCAN_SELFTEST=live` runs the same scripted pass against the real API,
# which is the only way this session can watch the network path work: it can
# neither see the screen nor touch the pointer.
FAKE = (os.environ.get("WALLSCAN_FAKE", "") == "1"
        or os.environ.get("WALLSCAN_SELFTEST", "") in ("1", "layout"))

# The shape of a placeholder. Real results are mixed, so these are too: the
# grid has to survive a portrait image and a cinemascope one, and a phase-one
# set that is all 16:9 would prove nothing about that.
FAKE_SIZES = [(3840, 2160), (5120, 2880), (3840, 2160), (6144, 3456),
              (5640, 3760), (3840, 1600), (4096, 2160), (3840, 2160),
              (2160, 3840), (7680, 3216), (4480, 2520), (3840, 2400)]


# ---- the main loop, in Qt's terms ---------------------------------------
#
# The hunting thread and the saving thread hand their results back to the
# window rather than touching it: calling into a web view from a worker thread
# is a crash waiting for a busy moment. GTK's name for that hand-over is
# `GLib.idle_add`, and it is used in about thirty places below. Rather than
# rewrite thirty call sites into Qt's spelling, the two helpers are provided
# here under their old names and the call sites are left alone -- one seam
# instead of thirty.

_TIMERS = set()      # keeps repeating timers alive; Qt collects an unowned one


class _Invoker(QObject):
    """Runs a function on the main thread, whoever asks.

    A queued signal is Qt's thread-safe door into the main loop, and this is
    the whole of it: a worker emits, the main thread runs. Created in `main`
    once the application exists, because a QObject before a QApplication is
    not a thing Qt allows.
    """

    fired = Signal(object)

    def __init__(self):
        super().__init__()
        self.fired.connect(self._run, Qt.QueuedConnection)

    @staticmethod
    def _run(job):
        fn, args = job
        fn(*args)


_INVOKER = None


def idle_add(fn, *args):
    """Run `fn(*args)` on the main thread, soon. Safe from any thread."""
    if _INVOKER is not None:
        _INVOKER.fired.emit((fn, args))


def timeout_add(ms, fn):
    """Call `fn` every `ms` until it returns something false.

    GLib's repeat-while-true contract, kept, because the placeholder feed
    below is written to it: `feed_one` returning False is how a fake run ends.
    """
    timer = QTimer()
    timer.setInterval(ms)

    def tick():
        if not fn():
            timer.stop()
            _TIMERS.discard(timer)

    timer.timeout.connect(tick)
    _TIMERS.add(timer)
    timer.start()
    return timer


def source_remove(timer):
    if timer is not None:
        timer.stop()
        _TIMERS.discard(timer)


def file_uri(path):
    """A `file:` URL for a path on disk, correct on both systems.

    Load-bearing on Windows and invisible on Linux. The old code built these
    by writing "file://" in front of the path, which is right for
    `/home/you/x.jpg` and wrong for `C:\\Users\\you\\x.jpg` -- the drive letter
    ends up read as a hostname and the picture never loads. Every preview in
    the grid arrives as one of these, so on Windows the old spelling would
    have produced a grid of broken images and no error anywhere.
    """
    return pathlib.Path(path).absolute().as_uri()


def run_js(webview, script):
    """Run a snippet in the page."""
    webview.page().runJavaScript(script)


# host: where the rounded corners went.
#
# GTK had to cut the window to a rounded shape, because the frame it drew
# underneath was square -- a region built from one thin rectangle per row of
# the corner, stepped rather than smooth. Qt is asked instead for a window
# with no background at all (`WA_TranslucentBackground`, set in `main`), and
# the page paints its own rounded ground onto that nothing. The corners come
# out antialiased, which the old region could not manage.
#
# The trade is that a rounded corner now needs a compositor. Windows always
# has one. Linux needs picom or equivalent, which this desktop runs; without
# one the corners go black rather than transparent.


def already_have(where=None):
    """The ids of wallpapers already in the folder, read off their filenames.

    63 of his 66 images are named `wallhaven-<id>.jpg`, so for that source "do
    I already have this" costs a directory listing and nothing else. Any source
    added later keeps the habit, which is why this reads `<source>-<id>`
    generally rather than wallhaven alone.

    The prefix must be a source this program actually knows. An earlier version
    accepted any `word-word` filename and counted `onyx-glass.png` as source
    "onyx", id "glass" -- 64 ids where the folder holds 63. A hand-named file
    is not a source id, and a loose pattern here would silently hide a real
    result whose id happened to collide.

    Re-measured 2026-08-22: 66 images, 63 wallhaven-named. The prompt's count
    of 67 included a `.desktop` shortcut sitting in with the pictures.
    """
    seen = set()
    where = where or LIBRARY
    if not os.path.isdir(where):
        return seen
    known = "|".join(re.escape(name) for name in sorted(set(SITE.values())))
    pattern = re.compile(r"^(%s)-([A-Za-z0-9]+)$" % known)
    for name in os.listdir(where):
        stem, ext = os.path.splitext(name)
        if ext.lower() not in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
            continue
        match = pattern.match(stem)
        if match:
            seen.add("%s:%s" % (match.group(1), match.group(2)))
    return seen


# What each service last said it had left, read off its own response. Nothing
# here guesses: a source is retired when it says it is nearly out, not when it
# refuses us.
LIMITS = {}


def fetch(url, timeout=25, headers=None, limits_for=None):
    """Bytes from a URL, with this program named in the request.

    `headers` is for the sources that authenticate with one. A key never
    reaches a URL here -- Pexels wants it in an Authorization header, and a key
    in a query string ends up in every proxy log between here and there.
    """
    head = {"User-Agent": USER_AGENT}
    if headers:
        head.update(headers)
    request = urllib.request.Request(url, headers=head)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if limits_for:
            left = (response.headers.get("X-RateLimit-Remaining")
                    or response.headers.get("x-ratelimit-remaining"))
            if left is not None:
                try:
                    LIMITS[limits_for] = int(left)
                except ValueError:
                    pass
        return response.read()


def wallhaven_page(query, page, seed, sorting="random", colour=None,
                   size=None):
    """One page of wallhaven results, as the API returns it.

    `sorting=random` with a held seed is what makes paging coherent: without
    the seed every page is a fresh shuffle and the same wallpaper comes back
    on page 2 that was already on page 1. The seed arrives in the first
    response and is fed back into every one after it.

    Categories 100 is General only -- not anime, not people. Purity 100 is
    safe content only, which is also the half of the API that needs no key.
    """
    params = [("categories", "100"), ("purity", "100"),
              ("sorting", sorting), ("page", str(page))]
    # `resolutions` is exactly-this-size and `atleast` is this-or-bigger.
    # They are different parameters and only one of them belongs on any given
    # search. Neither is sent empty: wallhaven reads an empty resolution as one
    # it cannot parse and answers a total of 0 rather than an error.
    size = SIZE_BY_ID[DEFAULT_SIZE] if size is None else size_or_default(size)
    if size.get("exact"):
        params.insert(0, ("resolutions", "%dx%d" % (size["w"], size["h"])))
    elif size.get("atleast"):
        params.insert(0, ("atleast", size["atleast"]))
    if seed:
        params.append(("seed", seed))
    if query:
        params.append(("q", query))
    # wallhaven wants its swatch without the hash, and will only accept one of
    # its own thirty. Anything else answers a total of 0 rather than an error,
    # which is why COLOURS is a written-down list and not a colour picker.
    if colour:
        params.append(("colors", colour.lstrip("#")))
    url = WALLHAVEN_SEARCH + "?" + urllib.parse.urlencode(params)
    # The whole request, in the log. He asked to be able to see what was
    # actually sent rather than be told about it, and a search that quietly
    # dropped his typed words would look identical to one that used them.
    print("scanner: -> %s" % url, flush=True)
    return json.loads(fetch(url, limits_for="wallhaven").decode("utf-8"))


def wallhaven_rows(query, cursor, pool="wallhaven", colour=None, size=None):
    """One page of wallhaven, normalised. Returns (rows, next cursor).

    A cursor rather than a page number, because the two sources page
    differently -- wallhaven counts pages and carries a shuffle seed, Commons
    carries an offset it hands back itself. The worker does not need to know
    which, and a third source can page however it likes.
    """
    page = cursor.get("page", 1)
    seed = cursor.get("seed")
    # `favorites` is the same 59,118 wallpapers ordered by how many people
    # kept them, rather than shuffled -- measured 2026-08-22, both pools report
    # the same total. `toplist` was not offered: it returned 356, because it
    # defaults to a short time window, and a pool that small runs dry in one
    # search.
    sorting = "favorites" if pool == "wallhaven-fav" else "random"
    payload = wallhaven_page(query, page, seed, sorting, colour, size)
    seed = (payload.get("meta") or {}).get("seed") or seed
    rows = []
    for r in payload.get("data") or []:
        thumbs = r.get("thumbs") or {}
        if not thumbs.get(THUMB_SIZE) or not r.get("path"):
            continue
        # wallhaven lists a wallpaper's five swatches strongest first, so
        # where the chosen colour sits in that list IS how dominant it is.
        # This is the number the strictness levels are compared against; the
        # filtering itself happens in the hunt, because the level can widen
        # part-way through a search and a row already fetched should not have
        # to be fetched again to be re-judged.
        crank = None
        if colour:
            swatches = r.get("colors") or []
            crank = swatches.index(colour) if colour in swatches else 99
        rows.append({
            "ident": str(r.get("id")),
            "source": pool,
            "w": r.get("dimension_x"),
            "h": r.get("dimension_y"),
            "thumb": thumbs[THUMB_SIZE],
            "full": r.get("path"),
            "bytes": r.get("file_size"),
            "crank": crank,
        })
    return rows, {"page": page + 1, "seed": seed}


def commons_rows(query, cursor, pool="commons", colour=None, size=None):
    """One page of Wikimedia Commons, normalised. Returns (rows, next cursor).

    A next cursor of None means the search is exhausted. Commons says so by
    leaving `continue` out of its answer, which is more honest than wallhaven,
    where running out only shows as an empty page.

    The width filter rides in the search string, so most of the rejecting
    happens at their end. The check below is the belt to that braces: `filew`
    is an index, and an index can lag the file it describes.
    """
    want = size_or_default(size)
    min_w = want["w"]
    params = {
        "action": "query",
        "generator": "search",
        "gsrsearch": "%s %s %s" % (commons_filter(min_w,
                                                  want.get("h", 0)
                                                  if want.get("exact") else 0),
                                   COMMONS_POOL.get(pool, ""),
                                   query or COMMONS_DEFAULT),
        "gsrnamespace": "6",
        "gsrlimit": "24",
        "gsroffset": str(cursor.get("offset", 0)),
        "prop": "imageinfo",
        "iiprop": "url|size",
        "iiurlwidth": "432",
        "format": "json",
    }
    payload = json.loads(fetch(
        COMMONS_API + "?" + urllib.parse.urlencode(params)).decode("utf-8"))
    pages = (payload.get("query") or {}).get("pages") or {}
    rows = []
    for entry in pages.values():
        info = (entry.get("imageinfo") or [{}])[0]
        if not info.get("thumburl") or not info.get("url"):
            continue
        if (info.get("width") or 0) < min_w:
            continue
        if want.get("exact") and (info.get("width") != want["w"]
                                  or info.get("height") != want["h"]):
            continue
        rows.append({
            "ident": str(entry.get("pageid")),
            "source": pool,
            "w": info.get("width"),
            "h": info.get("height"),
            "thumb": info["thumburl"],
            "full": info["url"],
            "bytes": info.get("size"),
            # Commons reports no colour at all -- not in any capture in
            # `research/`, not in any live response checked 2026-08-26. A rank
            # of None means "cannot be judged on colour", and his answer, given
            # 2026-08-26 when the choice was put to him, is that such a row
            # passes through rather than being dropped. So a colour search
            # including Commons will contain some pictures that are not that
            # colour, and that is the trade he chose.
            "crank": None,
        })
    nxt = (payload.get("continue") or {}).get("gsroffset")
    return rows, ({"offset": nxt} if nxt is not None else None)


PEXELS_SEARCH = "https://api.pexels.com/v1/search"
PEXELS_CURATED = "https://api.pexels.com/v1/curated"

# `medium` is 525x350 and about 20 KB; `original` is the real thing and matches
# the width the API reports -- 6480x4320 measured against a live result
# 2026-08-22. That check mattered: Pixabay reports 6000x4000 and will only
# serve 1280x853 to an ordinary key, which would have put sub-HD files in a 4K
# folder under a 6000px label. Pexels was verified before being trusted, and
# Pixabay was dropped for failing the same test.
PEXELS_THUMB = "medium"


def pexels_rows(query, cursor, pool="pexels", colour=None, size=None):
    """One page of Pexels, normalised. Returns (rows, next cursor).

    The key travels in an Authorization header, never in the URL. Pexels has no
    minimum-size parameter, so the 3840 floor is applied here and a page can
    come back mostly empty -- 18 of 20 cleared it on the query measured, but a
    narrow query will do worse.
    """
    page = cursor.get("page", 1)
    want = size_or_default(size)
    min_w = want["w"]
    params = {"per_page": "80", "page": str(page)}
    if query:
        params["query"] = query
        url = PEXELS_SEARCH
    else:
        url = PEXELS_CURATED            # their editors' picks; no query needed
    blob = fetch(url + "?" + urllib.parse.urlencode(params),
                 headers={"Authorization": KEYS.get("pexels", "")},
                 limits_for="pexels")
    payload = json.loads(blob.decode("utf-8"))
    rows = []
    for photo in payload.get("photos") or []:
        src = photo.get("src") or {}
        if (photo.get("width") or 0) < min_w:
            continue
        if want.get("exact") and (photo.get("width") != want["w"]
                                  or photo.get("height") != want["h"]):
            continue
        if not src.get(PEXELS_THUMB) or not src.get("original"):
            continue
        # Pexels' own `color=` parameter is not used, because it does not
        # work: white and black returned the same 39 ids (2026-08-26, live API,
        # rate-limit headers present). What Pexels does report honestly is one
        # average colour per photo, so the judging happens here. This is the
        # worse of the two arrangements -- the page is paid for and then thrown
        # away -- and it is the only one available.
        rows.append({
            "ident": str(photo.get("id")),
            "source": pool,
            "w": photo.get("width"),
            "h": photo.get("height"),
            "thumb": src[PEXELS_THUMB],
            "full": src["original"],
            "bytes": None,              # Pexels does not report a file size
            "crank": colour_rank_from_average(photo.get("avg_color"), colour),
        })
    nxt = {"page": page + 1} if payload.get("next_page") else None
    return rows, nxt


GIPHY_SEARCH = "https://api.giphy.com/v1/gifs/search"
GIPHY_TRENDING = "https://api.giphy.com/v1/gifs/trending"

# GIPHY's renditions, and why these two.
#
# `fixed_width` is 200px across and a few tens of kilobytes -- the same bargain
# every other source here is preview-first for, and it animates, which a still
# frame of a GIF would not. `original` is the real file at the real size, and
# it is the only rendition whose dimensions match what the API reports.
#
# Load-bearing: GIPHY reports width and height as STRINGS ("480", not 480).
# Compared against a number, every one of them would be "not big enough" and a
# size filter would silently empty the grid. They are converted on the way in.
GIPHY_THUMB = "fixed_width"

# 50 is the most a free key may ask for, and a free key is the only kind a
# friend can get instantly.
GIPHY_PAGE = 50

# `g` is GIPHY's own all-ages rating. This program searches wallhaven as
# General/SFW and there is no reason for the GIF half to be laxer.
GIPHY_RATING = "g"


def _giphy_int(value):
    """One of GIPHY's stringly-typed numbers, as a number."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def giphy_rows(query, cursor, pool="giphy", colour=None, size=None):
    """One page of GIPHY, normalised. Returns (rows, next cursor).

    Trending when there is nothing typed, exactly as Pexels falls back to its
    curated list: GIPHY's search endpoint wants a term and answers an empty one
    with an empty page rather than with everything.

    GIPHY reports no colour at all -- there is no average-colour field on a GIF
    object, and nothing in the search parameters to filter by one. A rank of
    None means "cannot be judged on colour", which is the same answer Commons
    gives and passes through for the same reason: dropping every GIF from a
    colour search would make the colour dropdown silently empty the grid.
    """
    offset = cursor.get("offset", 0)
    want = size_or_default(size)
    min_w = want["w"]
    params = {"api_key": KEYS.get("giphy", ""), "limit": str(GIPHY_PAGE),
              "offset": str(offset), "rating": GIPHY_RATING}
    if query:
        params["q"] = query[:50]        # their documented maximum
        url = GIPHY_SEARCH
    else:
        url = GIPHY_TRENDING
    payload = json.loads(fetch(url + "?" + urllib.parse.urlencode(params),
                               limits_for="giphy").decode("utf-8"))
    rows = []
    for gif in payload.get("data") or []:
        images = gif.get("images") or {}
        thumb = images.get(GIPHY_THUMB) or {}
        full = images.get("original") or {}
        if not thumb.get("url") or not full.get("url"):
            continue
        width = _giphy_int(full.get("width"))
        height = _giphy_int(full.get("height"))
        if width < min_w:
            continue
        if want.get("exact") and (width != want["w"] or height != want["h"]):
            continue
        rows.append({
            "ident": str(gif.get("id")),
            "source": pool,
            "w": width,
            "h": height,
            "thumb": thumb["url"],
            "full": full["url"],
            "bytes": _giphy_int(full.get("size")) or None,
            "crank": None,
        })
    # GIPHY pages by offset and stops at 4999, which it will not say; asking
    # past it answers an error rather than an empty page, so the end is worked
    # out here instead.
    page = payload.get("pagination") or {}
    nxt = offset + GIPHY_PAGE
    total = page.get("total_count")
    if not payload.get("data") or nxt > 4999 or (
            total is not None and nxt >= total):
        return rows, None
    return rows, {"offset": nxt}


# Which function fetches which source. A source with no entry here is one the
# window can list and cannot search -- which is what a disabled card is.
def _pool(fn, pool):
    """Bind a pool to its adapter. A plain lambda in the dict below would close
    over the loop variable and every entry would fetch the last pool."""
    return lambda query, cursor, colour=None, size=None: fn(
        query, cursor, pool, colour, size)


def _adapter(name):
    if name.startswith("wallhaven"):
        return wallhaven_rows
    if name.startswith("commons"):
        return commons_rows
    if name.startswith("giphy"):
        return giphy_rows
    return pexels_rows


# A source with no key gets no entry, so it can be listed and not searched.
# A source whose key is missing gets no entry, so it can be listed and not
# searched.
def _build_fetchers():
    return {name: _pool(_adapter(name), name)
            for name in SITE
            if name not in ("pexels", "giphy") or KEYS.get(name)}


FETCHERS = _build_fetchers()

KEYS_PATH = os.path.join(HERE, "keys.txt")


def save_key(name, value):
    """Write one key into keys.txt and turn its source on.

    Rewritten rather than appended to, so that pasting a second key over a
    first leaves one line and not two -- and a commented example line for the
    same name is treated as the place it goes, because that is where anyone
    reading the file would look for it.

    The file is written 0600. It is the only file this program owns that is
    worth anything to anyone else.

    Nothing here prints the key. The log says which source was given one and
    how long it was, which is enough to tell a paste from an empty box and
    tells a reader of the log nothing they could use.
    """
    value = (value or "").strip()
    if not value:
        return False, "nothing was pasted"
    if "\n" in value or "\r" in value:
        return False, "that looks like more than one line"

    try:
        existing = io.open(KEYS_PATH, encoding="utf-8").read().splitlines()
    except OSError:
        # No keys.txt yet. The example beside it is the better starting point
        # than an empty file: it carries the sign-up links and the warning
        # that this file is not for sharing.
        try:
            existing = io.open(KEYS_PATH + ".example",
                               encoding="utf-8").read().splitlines()
        except OSError:
            existing = ["# API keys. One `name = value` a line."]

    line = "%s = %s" % (name, value)
    # `giphy =` or `# giphy =`, and nothing else. Anchored and requiring the
    # `=` so that a line of documentation which merely mentions the name --
    # the sign-up links at the top of the file each begin with one -- is not
    # mistaken for the setting and overwritten.
    slot = re.compile(r"^\s*#?\s*%s\s*=" % re.escape(name), re.I)
    out, written = [], False
    for row in existing:
        if not written and slot.match(row):
            out.append(line)
            written = True
        else:
            out.append(row)
    if not written:
        out.append(line)

    try:
        with io.open(KEYS_PATH, "w", encoding="utf-8") as handle:
            handle.write("\n".join(out).rstrip("\n") + "\n")
        os.chmod(KEYS_PATH, 0o600)
    except OSError as exc:
        return False, "could not write keys.txt: %s" % exc

    # The source can be searched from here on, with no restart: the key table
    # and the fetcher table are both rebuilt in place, because the hunting
    # thread holds a reference to the one it was given.
    KEYS[name] = value
    FETCHERS.clear()
    FETCHERS.update(_build_fetchers())
    print("scanner: %s key saved to keys.txt (%d characters) — the source is "
          "on now, no restart" % (name, len(value)), flush=True)
    return True, ""


def load_offered():
    """Everything already shown to him, from previous runs and this one."""
    shown = set()
    try:
        with open(OFFERED, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    shown.add(line)
    except OSError:
        pass                                # no file yet is not a problem
    return shown


def record_offered(key):
    """Append one id. Opened and closed per line on purpose.

    A held-open handle would lose whatever was buffered if the window were
    closed mid-search, which is exactly when the record matters most -- he
    stops a search precisely because he has seen enough of it.
    """
    try:
        with open(OFFERED, "a", encoding="utf-8") as handle:
            handle.write(key + "\n")
    except OSError as exc:
        print("scanner: could not record %s: %s" % (key, exc), flush=True)


def make_placeholder(path, w, h, index):
    """Draw one stand-in preview at the size a real thumbnail comes back at.

    Real previews arrive as ~10 KB JPEGs about 432 px wide -- measured against
    wallhaven 2026-08-22 -- so these are drawn at the same scale. They are
    deliberately dull: the palette is the library's own median luma of 55 and
    chroma of 28, so a grid of them looks like a grid of real results rather
    than a test card, and the layout is judged against what it will hold.

    Drawn with QPainter, which came in with the window. cairo drew these
    before and is a Linux habit; Pillow would work on both and is one more
    thing for a friend to install for a rectangle and a gradient.
    """
    tw = 432
    th = max(1, int(round(tw * h / w)))
    image = QImage(tw, th, QImage.Format_RGB32)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing, True)

    # A dark, low-chroma wash. hue walks with the index so the grid does not
    # read as one repeated tile.
    hue = (index * 37) % 360
    base = 0.10 + 0.13 * ((index % 5) / 4.0)
    grad = QLinearGradient(0, 0, tw, th)
    for stop, lift in ((0.0, 0.0), (0.55, 0.09), (1.0, -0.03)):
        r, g, b = _hue_rgb(hue, 0.11, max(0.03, base + lift))
        grad.setColorAt(stop, QColor.fromRgbF(r, g, b))
    painter.fillRect(0, 0, tw, th, grad)

    # A horizon and a few uprights: enough shape that a thumbnail reads as a
    # picture at a glance, which is what the grid has to be judged on.
    painter.fillRect(QRectF(0, th * 0.62, tw, 1.5), QColor(255, 255, 255, 13))
    rng = random.Random(index)
    for _ in range(rng.randint(3, 9)):
        bx = rng.uniform(0, tw)
        bw = rng.uniform(6, 34)
        bh = rng.uniform(th * 0.06, th * 0.42)
        shade = QColor(255, 255, 255, int(round(rng.uniform(0.02, 0.07) * 255)))
        painter.fillRect(QRectF(bx, th * 0.62 - bh, bw, bh), shade)

    painter.end()
    if not image.save(path, "PNG"):
        raise OSError("could not write %s" % path)


def _hue_rgb(hue, sat, val):
    """HSV to RGB in floats. Small enough to write than to import."""
    h = (hue % 360) / 60.0
    c = val * sat
    x = c * (1 - abs(h % 2 - 1))
    m = val - c
    r, g, b = [(c, x, 0), (x, c, 0), (0, c, x),
               (0, x, c), (x, 0, c), (c, 0, x)][int(h) % 6]
    return r + m, g + m, b + m


class Page(QWebEnginePage):
    """The page, with its console wired to this program's stdout.

    Without this a page that throws on load looks exactly like a page that
    worked: the window still opens, the process still exits 0, and nothing
    anywhere says the layout never ran. WebKit had a switch for it; Qt wants
    the method overridden.
    """

    def javaScriptConsoleMessage(self, level, message, line, source):
        print("scanner: page console [%s:%s] %s"
              % (os.path.basename(source or "page"), line, message),
              flush=True)


class Bridge(QObject):
    """The one door the page speaks through.

    WebKit gave every channel its own named handler and the page picked one by
    name. Qt gives the page one object over a web channel, so the channel name
    comes in as an argument instead. The page's side of this is a single
    function -- `send` at the top of ui/scanner.js -- which is the reason this
    port did not have to touch the interface.
    """

    def __init__(self, handlers):
        super().__init__()
        self._handlers = handlers

    @Slot(str, str)
    def send(self, channel, payload):
        handler = self._handlers.get(channel)
        if handler is None:
            print("scanner: page sent on unknown channel %r" % channel,
                  flush=True)
            return
        handler(payload)


class Window(QMainWindow):
    """The frame. Undecorated, transparent, and it asks before it closes."""

    def __init__(self, on_close):
        super().__init__()
        self._on_close = on_close
        self.setWindowTitle("Wallpaper Finder")
        # host: no title bar, no border. Qt.Window keeps it a real top-level
        # window with a taskbar entry -- Qt.FramelessWindowHint alone on some
        # window managers gives a window that cannot be focused.
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
        # host: the ground the page paints its rounded rect onto. Both halves
        # are needed -- the attribute makes the widget's own background
        # absent, and the page's background is set to transparent separately,
        # or Chromium paints white underneath and the corners are square.
        self.setAttribute(Qt.WA_TranslucentBackground, True)

    def closeEvent(self, event):
        """The window manager's close button, routed to the same teardown as
        the page's own X, so there is one shutdown rather than two."""
        event.ignore()
        self._on_close()


def main():
    # host: text scaling.
    #
    # The stylesheet's numbers are the screen's numbers -- the whole layout is
    # authored in device pixels, and a window that renders at 2x hangs outside
    # its own frame. On Linux that meant forcing GTK's DPI back to 96 against
    # this desktop's 2x text scale. Qt would apply the same scale from the
    # other direction, so it is turned off here and the page is left at 1:1.
    #
    # WALLSCAN_SCALE=1.5 puts it back for anyone who wants the window bigger,
    # which on a high-DPI Windows laptop is a reasonable thing to want. Both
    # have to be set before the application exists; after it, they do nothing.
    # host: the name the window manager sees.
    #
    # GTK took this from `set_prgname`. Qt's X11 backend takes the instance
    # half of WM_CLASS from RESOURCE_NAME and falls back to argv[0], which made
    # this window announce itself as "scanner.py". That is not cosmetic here: a
    # name that collides with a picom rule is the most repeated bug in this
    # desktop's history, `pale-wallscan` was chosen against picom.conf so that
    # it collides with none of them, and anything that finds this window by
    # class looks for that name. Windows ignores all of it.
    os.environ.setdefault("RESOURCE_NAME", PRGNAME)

    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")
    scale = os.environ.get("WALLSCAN_SCALE")
    if scale:
        os.environ["QT_SCALE_FACTOR"] = scale

    app = QApplication(sys.argv)
    app.setApplicationName(PRGNAME)
    app.setDesktopFileName(PRGNAME)

    global _INVOKER
    _INVOKER = _Invoker()

    guard = single_instance()
    if guard is None:
        print("scanner: already running -- asked that window to come forward",
              flush=True)
        return 0

    window = Window(lambda: shutdown())

    webview = QWebEngineView()
    page = Page(webview)
    webview.setPage(page)
    window.setCentralWidget(webview)

    # host: transparent, not the ground colour. The page paints the ground
    # itself on a rounded rect, and anything outside that rect has to be
    # genuinely absent or the corners are square again.
    page.setBackgroundColor(Qt.transparent)

    # host: refuse Qt's own context menu outright. It is a light menu in a
    # dark window, carrying reloads and inspectors this program has no use
    # for. This is the half that cannot be reasoned around by the page.
    webview.setContextMenuPolicy(Qt.NoContextMenu)

    # host: the page is loaded from a file, and every preview in the grid is a
    # file too. Chromium refuses one local file reading another unless told
    # otherwise, and without this the grid fills with broken images.
    view_settings = webview.settings()
    view_settings.setAttribute(
        QWebEngineSettings.LocalContentCanAccessFileUrls, True)
    view_settings.setAttribute(
        QWebEngineSettings.ShowScrollBars, False)

    # host: WebKit turned any `title` attribute into a tooltip the page could
    # not style, and the markup carries aria-label only for that reason. Qt
    # would do the same with a title; the markup is still the real fix.
    #
    # The web channel is what the page's `send` reaches this side through, and
    # qwebchannel.js has to be in the page before that call can work. It ships
    # inside Qt as a resource, so it is read from there rather than vendored
    # into ui/ where it could drift out of step with the installed PySide6.
    channel = QWebChannel(page)
    page.setWebChannel(channel)

    qwebchannel_js = ""
    channel_file = QFile(":/qtwebchannel/qwebchannel.js")
    if channel_file.open(QFile.ReadOnly | QFile.Text):
        qwebchannel_js = bytes(channel_file.readAll().data()).decode("utf-8")
        channel_file.close()
    else:
        print("scanner: qwebchannel.js missing from PySide6 -- the page will "
              "load and no button will do anything", flush=True)

    # The shim the page's `send` lands on. It queues anything sent before the
    # channel finishes connecting: the page's own script runs first, and a
    # control pressed in that window would otherwise vanish without a word.
    shim = """
    window._wallscan_queue = [];
    window._wallscan_send = function (channel, payload) {
      window._wallscan_queue.push([channel, payload]);
    };
    new QWebChannel(qt.webChannelTransport, function (ch) {
      var host = ch.objects.host;
      window._wallscan_send = function (channel, payload) {
        host.send(channel, payload);
      };
      var queued = window._wallscan_queue;
      window._wallscan_queue = [];
      for (var i = 0; i < queued.length; i++) {
        host.send(queued[i][0], queued[i][1]);
      }
    });
    """
    for name, source, point in (
            ("qwebchannel", qwebchannel_js,
             QWebEngineScript.DocumentCreation),
            ("wallscan-bridge", shim, QWebEngineScript.DocumentCreation)):
        script = QWebEngineScript()
        script.setName(name)
        script.setSourceCode(source)
        script.setInjectionPoint(point)
        script.setWorldId(QWebEngineScript.MainWorld)
        script.setRunsOnSubFrames(False)
        page.scripts().insert(script)

    # WALLSCAN_SELFTEST=1 makes the page press its own controls, in order, and
    # print what each one did. It exists because the session that built this
    # cannot see the screen or touch the pointer: "every control fires" is
    # otherwise a claim with nothing behind it. Off unless asked for, and it
    # drives the same handlers a real click does rather than a parallel path.
    url = QUrl.fromLocalFile(HTML_PATH)
    _mode = os.environ.get("WALLSCAN_SELFTEST", "")
    if _mode in ("1", "live", "layout", "soak"):
        url.setQuery("selftest=" + _mode)
        print("scanner: SELF-TEST -- the page will press its own buttons",
              flush=True)
    webview.load(url)

    state = {"closing": False, "shape": None, "rolled": False,
             "size_before_roll": None, "stage": "bar", "running": False,
             "want": 0, "found": 0, "index": 0, "timer": None,
             "sources": [], "themes": [], "typed": "", "colour": "",
             # Images or GIFs. It decides which sources are on the panel and
             # which sizes the dropdown offers, so it is the one setting that
             # changes what the other two can say.
             "kind": DEFAULT_KIND,
             # The minimum resolution, as an id from SIZES. 4K unless the
             # dropdown says otherwise.
             "size": DEFAULT_SIZE,
             # Where Submit will put them. Asked about on every save, so it
             # can be changed there; remembered so it usually needs no answer.
             "savedir": load_savedir(),
             # How hard the colour is being held right now. It only ever
             # loosens, and only when the sources have nothing left at the
             # current level -- see COLOUR_LEVELS.
             "colour_level": 0, "colour_dry": 0,
             # where the hunt has got to, so Resume continues rather than
             # starting the same search again
             "cursors": {}, "skipped": 0, "rows": {},
             "buffers": {}, "turn": 0,
             "skipped_owned": 0, "skipped_shown": 0, "dupes": 0,
             "fresh": set(),
             "stopflag": None, "worker": None, "savestop": None,
             "maximized": False}

    seen = already_have(state["savedir"])
    offered = load_offered()
    print("scanner: %d wallpapers already in %s have a readable id"
          % (len(seen), pretty_path(state["savedir"])), flush=True)

    def relearn_folder(where):
        """Read the new folder's filenames after the save folder changes.

        Without this, "do I already have this" would go on answering for the
        folder that was chosen when the window opened, and the first search
        after a change would re-offer wallpapers sitting in the new one. The
        set is emptied and refilled rather than rebound, because the hunting
        thread and the saver both close over this exact object.
        """
        seen.clear()
        seen.update(already_have(where))
        print("scanner: %d wallpapers already in %s have a readable id"
              % (len(seen), pretty_path(where)), flush=True)
    print("scanner: %d already offered to you before (offered.txt — delete it "
          "to start being shown them again)" % len(offered), flush=True)
    if OFFERED != os.path.join(HERE, "offered.txt"):
        print("scanner: RECORDING TO %s, not the real record" % OFFERED,
              flush=True)
    if LIBRARY != os.path.expanduser("~/Pictures/wallpapers"):
        print("scanner: SAVING TO %s, not the real library" % LIBRARY,
              flush=True)
    if not CONTACT:
        print("scanner: no contact.txt — Wikimedia will refuse most Commons "
              "previews (429, robot policy). wallhaven is unaffected.",
              flush=True)
    print("scanner: previews come from %s"
          % ("drawn placeholders (WALLSCAN_FAKE)" if FAKE
             else "wallhaven, live, no key"), flush=True)

    def say(fn, *args):
        """Say something to the page. Every call goes through here so that a
        renamed page function fails in one place rather than fifteen."""
        script = ("window.%s && window.%s(%s)"
                  % (fn, fn, ", ".join(json.dumps(a) for a in args)))
        idle_add(run_js, webview, script)

    def on_screen(w, h):
        """One stage's size, cut down to the screen it will open on.

        These numbers were chosen on a 3840x2160 panel, where the results
        window at 1760x1180 is less than a third of the screen. On a 1920x1080
        laptop -- which is what a friend is likely to have -- 1180 tall is
        taller than the whole display, so the window would open with its
        bottom edge, and the Submit button on it, off the screen.

        Nothing is scaled: the layout is authored in real pixels and the panes
        reflow. It is only ever made smaller, and only when it would not fit.
        """
        screen = app.primaryScreen()
        if screen is None:
            return w, h
        area = screen.availableGeometry()
        return (min(w, max(320, area.width() - 40)),
                min(h, max(240, area.height() - 40)))

    def resize_to(w, h, min_w, min_h):
        """Move the window to the size a stage needs.

        The minimum has to be lowered before the resize or the window is
        clamped to the old one and simply does not shrink -- true of GTK and
        true of Qt, and the same trap arbor's roll-up hits.
        """
        w, h = on_screen(w, h)
        min_w, min_h = on_screen(min_w, min_h)
        window.setMinimumSize(min_w, min_h)
        window.resize(w, h)
        keep_on_screen()

    def keep_on_screen():
        """Nudge the window back inside the display if a resize pushed it out.

        A window that grows from the bar to the results grid keeps its top-left
        corner, so on a small screen the growth all happens off the bottom and
        right. There is no titlebar to drag it back by -- the page is the drag
        handle, and the part of the page you would grab may be the part that is
        now off the screen.
        """
        screen = app.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        frame = window.frameGeometry()
        x = min(max(frame.x(), area.x()), area.x() + area.width() - frame.width())
        y = min(max(frame.y(), area.y()), area.y() + area.height() - frame.height())
        if (x, y) != (frame.x(), frame.y()):
            window.move(x, y)

    # host: the window is no longer cut to a rounded shape on every resize --
    # it is transparent and the page draws its own corners. The size-allocate
    # handler that did the cutting is gone with it, and with it the chance of
    # the shape getting out of step with the real size.

    # ---- the window's own furniture ------------------------------------

    def on_drag(_payload):
        # host: Qt asks the window manager to take over the drag, which is
        # what GTK's begin_move_drag did. Neither needs to be told where the
        # pointer is -- the system already knows -- so the seat no longer has
        # to be asked, and `pointer_position` went away with the question.
        handle = window.windowHandle()
        if handle is not None:
            handle.startSystemMove()

    def on_resize(payload):
        edge = EDGES.get(payload)
        if edge is None:
            return
        handle = window.windowHandle()
        if handle is not None:
            handle.startSystemResize(edge)

    def on_minimize(_payload):
        window.showMinimized()
        print("scanner: minimised", flush=True)

    def on_maxtoggle(_payload):
        # Rolled up and maximised are mutually exclusive: maximising while
        # shaded would give a full-width strip, which is neither state.
        if state["rolled"]:
            unroll()
            say("scanner_setRolled", False)
        if state.get("maximized"):
            window.showNormal()
        else:
            window.showMaximized()
        state["maximized"] = not state.get("maximized")
        print("scanner: %s" % ("maximised" if state["maximized"]
                               else "restored"), flush=True)

    def unroll():
        width, height = state["size_before_roll"] or (RESULTS_W, RESULTS_H)
        window.setMinimumSize(RESULTS_MIN_W, RESULTS_MIN_H)
        window.resize(width, height)
        state["rolled"] = False

    def roll():
        size = window.size()
        state["size_before_roll"] = (size.width(), size.height())
        # The minimum height has to come off first, or the resize is clamped
        # to it and the window does not shade.
        window.setMinimumSize(RESULTS_MIN_W, STRIP_H)
        window.resize(state["size_before_roll"][0], STRIP_H)
        state["rolled"] = True

    def on_rolltoggle(_payload):
        unroll() if state["rolled"] else roll()
        say("scanner_setRolled", state["rolled"])
        print("scanner: rolled %s" % ("up" if state["rolled"] else "down"),
              flush=True)

    # ---- stage one: what to search -------------------------------------

    def send_kind(kind):
        """Hand the page the sources and sizes for one kind of search.

        Both lists go together and neither is any use without the other: the
        GIF sources with the wallpaper sizes would be a 4K floor over a library
        whose widest entry is 500 pixels, which is a search that can only come
        back empty.
        """
        state["kind"] = kind
        sizes, default = sizes_for(kind)
        say("scanner_setSources", sources_for(kind))
        say("scanner_setSizes", sizes, default)
        state["size"] = default

    def on_savekey(raw):
        """A key was pasted into the key window.

        The page is answered either way. A save that fails silently would
        leave someone looking at a source that still says it needs a key,
        with nothing anywhere saying why.
        """
        payload = json.loads(raw)
        name = payload.get("source") or ""
        known = {s["key"] for s in SOURCES if s.get("key")}
        if name not in known:
            say("scanner_keySaved", name, False, "that is not a source that "
                                                 "takes a key")
            return
        ok, why = save_key(name, payload.get("key"))
        if ok:
            # The panel is rebuilt so the card it was opened from is ticked
            # and tickable, rather than still sitting there saying no.
            say("scanner_setSources", sources_for(state["kind"]))
        say("scanner_keySaved", name, ok, why)

    def on_openurl(payload):
        """Open a sign-up page in whatever browser this machine uses.

        Only ever a URL this program wrote down itself -- the page sends the
        source's id, not an address -- so nothing the window is shown can talk
        this into opening something else.
        """
        wanted = {s["id"]: s.get("signup") for s in SOURCES}
        url = wanted.get(payload)
        if not url:
            return
        print("scanner: opening %s" % url, flush=True)
        try:
            if os.name == "nt":
                os.startfile(url)              # noqa: S606 -- Windows' own opener
            else:
                subprocess.Popen(["xdg-open", url],
                                 stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
        except Exception as exc:
            print("scanner: could not open it: %s" % exc, flush=True)

    def on_kind(payload):
        kind = payload if payload in [k["id"] for k in KINDS] else DEFAULT_KIND
        send_kind(kind)
        live = [s["id"] for s in sources_for(kind) if s["ready"]]
        print("scanner: looking for %s — %d source%s ready (%s)"
              % (kind, len(live), "" if len(live) == 1 else "s",
                 ", ".join(live) if live else "none: see the cards"),
              flush=True)

    def on_search(raw):
        """SEARCH was pressed. Carry the picks forward and ask how many."""
        payload = json.loads(raw)
        state["sources"] = payload.get("sources", [])
        state["themes"] = payload.get("themes", [])
        state["typed"] = (payload.get("typed") or "").strip()
        colour = (payload.get("colour") or "").strip().lower()
        # Only a swatch this program wrote down. A colour arriving from the
        # page that wallhaven would refuse is dropped here rather than turned
        # into a search that silently returns nothing.
        state["colour"] = colour if colour in COLOUR_HEXES else ""
        # Same rule as the colour: a size this program has not written down
        # becomes the default rather than a search nobody can account for.
        # A specific size arrives as two numbers beside the id; anything else
        # is one of the written-down floors.
        if payload.get("size") == "exact":
            state["size"] = exact_size(payload.get("width"),
                                       payload.get("height"))
        else:
            state["size"] = size_or_default(payload.get("size"))["id"]
        print("scanner: search wants sources=%s themes=%s typed=%r colour=%s "
              "size=%s"
              % (state["sources"] or ["<none>"], state["themes"] or ["<none>"],
                 state["typed"],
                 ("%s %s" % (COLOUR_NAMES.get(state["colour"], "?"),
                             state["colour"])) if state["colour"] else "any",
                 size_or_default(state["size"])["name"]),
              flush=True)
        state["stage"] = "ask"
        resize_to(ASK_W, ASK_H, ASK_W, ASK_H)
        say("scanner_setStage", "ask")

    def on_cancelask(_payload):
        """Backed out of the how-many box. Return to the bar."""
        state["stage"] = "bar"
        resize_to(BAR_W, BAR_H, BAR_MIN_W, BAR_MIN_H)
        say("scanner_setStage", "bar")
        say("scanner_setSeenCount", len(offered))
        print("scanner: how-many cancelled, back to the bar", flush=True)

    # ---- stage two: how many, then the grid ----------------------------

    def on_howmany(raw):
        """A number was confirmed. Open the results window and start filling."""
        try:
            want = int(json.loads(raw))
        except (ValueError, TypeError):
            want = 10
        # No cap, at his instruction -- his words were that he is not going to
        # type a hundred thousand, and would stop it long before it got there
        # if he did. One is still the floor, because zero results is not a
        # search.
        want = max(1, want)
        state.update(want=want, found=0, index=0, running=True, stage="grid",
                     cursors={}, skipped=0, skipped_owned=0, skipped_shown=0,
                     dupes=0, fresh=set(), rows={}, buffers={},
                     turn=0)
        os.makedirs(THUMBS, exist_ok=True)
        resize_to(RESULTS_W, RESULTS_H, RESULTS_MIN_W, RESULTS_MIN_H)
        say("scanner_setStage", "grid")
        say("scanner_reset", want)
        wanted = [w for w in (list(state["themes"])
                              + [state["typed"]]) if w]
        if state.get("colour"):
            wanted.append("mostly %s"
                          % COLOUR_NAMES[state["colour"]].lower())
        say("scanner_setStatus", "searching for %s…"
            % (", ".join(wanted) if wanted else "anything"))
        print("scanner: asked for %d, results window open" % want, flush=True)
        start_feed()

    # ---- what comes back from the hunting thread ------------------------
    #
    # Every one of these runs on the main loop, handed over by idle_add.
    # Calling into a WebView from a worker thread is a crash waiting for a
    # busy moment, so the thread below computes and these deliver.

    def deliver(row, delivered, skipped):
        """One preview has landed. Keep it, then show it.

        The row is held here as well as sent to the page because the page only
        knows what it was told; the full-size URL never goes there. When he
        submits, this side already has everything it needs to download.
        """
        state["rows"][row["id"]] = row
        # One line per result, so which source a grid came from is answerable
        # after the fact by someone who could not watch it fill.
        # The key is the site, so two pools of one site log identically and a
        # run that never reached the second pool looks like one that did.
        # The pool goes on the line as well.
        pool = row.get("source")
        via = "" if pool == row["id"].split(":")[0] else " via %s" % pool
        print("scanner: #%d %s %sx%s%s"
              % (delivered, row["id"], row["w"], row["h"], via), flush=True)
        state["found"] = delivered
        state["skipped"] = skipped
        say("scanner_addResult", row)
        say("scanner_setCount", row["source"], delivered, state["want"])
        return False

    def remember_cursors(cursors):
        """Where each source has got to, so Resume continues rather than
        starting the same search and re-offering the same wallpapers."""
        state["cursors"] = cursors
        return False

    def hunt_done(delivered, owned_skips, shown_skips, dupes):
        state["running"] = False
        state["skipped_owned"] = owned_skips
        state["skipped_shown"] = shown_skips
        state["dupes"] = dupes
        say("scanner_setRunning", False)
        bits = []
        if owned_skips:
            bits.append("%d already yours" % owned_skips)
        if shown_skips:
            bits.append("%d shown before" % shown_skips)
        if dupes:
            bits.append("%d sent twice" % dupes)
        tail = (" — skipped " + ", ".join(bits)) if bits else ""
        say("scanner_setStatus", "%d of %d%s" % (delivered, state["want"], tail))
        print("scanner: finished, %d delivered, %d skipped as owned, %d as "
              "already shown, %d as duplicates the source sent twice"
              % (delivered, owned_skips, shown_skips, dupes), flush=True)
        return False

    def hunt_failed(why):
        """The network said no. Say so on the strip, not only in the log.

        A search that dies with the grid half full and the button still
        reading Stop is indistinguishable from a slow one, and he cannot see
        this program's log.
        """
        state["running"] = False
        say("scanner_setRunning", False)
        say("scanner_setStatus", "could not search — " + why)
        print("scanner: search failed: %s" % why, flush=True)
        return False

    def hunt_exhausted(delivered, skipped):
        state["running"] = False
        say("scanner_setRunning", False)
        say("scanner_setStatus",
            "ran out — %d of %d found. Fewer themes would widen it."
            % (delivered, state["want"]))
        print("scanner: sources exhausted after %d delivered, %d skipped"
              % (delivered, skipped), flush=True)
        return False

    def hunt_note(line):
        print("scanner: %s" % line, flush=True)
        return False

    def set_status(text):
        say("scanner_setStatus", text)
        return False

    # ---- the hunting thread itself --------------------------------------

    def hunt(stopflag):
        """Walk every ticked source in turn until enough previews are delivered.

        On its own thread, because a network call on the main loop is a frozen
        window and the window is the thing he is looking at. Nothing here
        touches the page directly -- every result goes back through idle_add.

        A page from each source in turn rather than one source drained before
        the next begins: he asked for several sources, and a grid that is all
        wallhaven until the fortieth result is not several sources.
        """
        # The ticked pills and whatever he typed, glued into the one string
        # every source already receives. There is no per-source work in this:
        # `wallhaven_rows`, `commons_rows` and `pexels_rows` all take the same
        # query, so a typed word reaches all three the moment it is added here.
        query = " ".join([w for w in (list(state["themes"])
                                      + [state["typed"]]) if w]).strip()
        colour = state.get("colour") or ""
        size = state.get("size") or DEFAULT_SIZE
        state["colour_level"] = 0
        state["colour_dry"] = 0
        idle_add(hunt_note, "search string sent to the sources: %r | at "
                 "least %s%s"
                 % (query if query else "",
                    size_or_default(size)["name"],
                    (" | colour %s (%s), held %s"
                     % (COLOUR_NAMES.get(colour, "?"), colour,
                        COLOUR_LEVEL_NAMES[0])) if colour else ""))
        want = state["want"]
        delivered = state["found"]
        owned_skips = state.get("skipped_owned", 0)
        shown_skips = state.get("skipped_shown", 0)
        dupes = state.get("dupes", 0)
        colour_skips = 0
        # What this search has already put on screen. Kept in state rather than
        # locally so Stop and Resume do not start a fresh one and re-offer what
        # is already in the grid.
        #
        # Both sources repeat themselves across pages -- measured 2026-08-22
        # over five pages each with the shuffle seed correctly held: wallhaven
        # returned 120 results with 112 distinct, one id three times; Commons
        # 120 with 115 distinct. That is 6 to 8 duplicates per hundred, and it
        # is their paging, not ours. He saw it before this existed.
        fresh = state.setdefault("fresh", set())

        live = [name for name in state["sources"] if name in FETCHERS]
        if not live:
            idle_add(hunt_failed, "no source selected that I can search")
            return

        cursors = dict(state.get("cursors") or {})
        for name in live:
            cursors.setdefault(name, {})

        # A page from wallhaven is 24 results, so a request for nine used to
        # be satisfied entirely on the first turn and Commons never got asked
        # -- several sources in the settings and one source in the grid. Each
        # source's page is buffered instead, and only a row of the grid is
        # taken from it per turn, so the sources interleave. Found 2026-08-22
        # by a live run whose whole grid came back wallhaven.
        # Both survive Stop and Resume, in state rather than locally.
        #
        # They used to be local, and resuming therefore restarted the rotation
        # at the first source and threw away every buffered page. Since pausing
        # is the whole way he works -- stop, look, tick, resume -- the last
        # source in the list was starved: a nine-result run with three pools
        # reached the third one never, because it stopped at eight and began
        # again at the first. Found 2026-08-22 by a live run whose log showed
        # Commons selected, never fetched and never failed.
        buffers = state.setdefault("buffers", {})
        for name in live:
            buffers.setdefault(name, [])
        per_turn = 4
        # Per source, not one clock for all of them. wallhaven's 45 a minute is
        # wallhaven's; waiting on it should not also hold up Commons, and a
        # single shared gap made the slowest limit everyone's limit.
        last_call = {}
        turn = state.get("turn", 0)

        while not stopflag.is_set() and delivered < want and live:
            name = live[turn % len(live)]
            turn += 1
            state["turn"] = turn

            # Only fetch when this source has nothing left over from last
            # time. Paced against wallhaven's 45 a minute, the tighter of the
            # two limits; Commons is far more generous and is held to the same
            # rate rather than given its own budget to get wrong.
            exhausted = False
            if not buffers[name]:
                site = SITE.get(name, name)
                # Retire a source before it refuses us, on its own count. His
                # instruction: hardwire it so I do not get locked out.
                left = LIMITS.get(site)
                if left is not None and left <= RATE_FLOOR:
                    idle_add(hunt_note,
                                  "%s is near its rate limit (%d left) — "
                                  "leaving it alone" % (name, left))
                    live.remove(name)
                    turn = 0
                    continue
                wait_for = SOURCE_GAP.get(name, API_MIN_GAP)
                gap = time.monotonic() - last_call.get(name, 0.0)
                if gap < wait_for and stopflag.wait(wait_for - gap):
                    return                  # stopped while waiting our turn
                last_call[name] = time.monotonic()

                try:
                    rows, nxt = FETCHERS[name](query, cursors[name], colour,
                                               size)
                except Exception as exc:
                    # One source falling over is not the end of the search. It
                    # is dropped, said out loud, and the others carry on.
                    idle_add(hunt_note, "%s failed: %s" % (name, exc))
                    live.remove(name)
                    turn = 0
                    continue

                buffers[name] = list(rows)
                cursors[name] = nxt or {}
                idle_add(remember_cursors, dict(cursors))
                # No cursor back, or a page with nothing usable on it, means
                # this source is spent. Commons says so by omitting
                # `continue`; wallhaven only shows it as an empty page.
                exhausted = (nxt is None or not rows)

            taken = 0
            colour_skips_before = colour_skips
            # Conditions tested before the row leaves the buffer, not after:
            # popping first and then breaking would quietly throw away the
            # result that tipped the count over.
            while (buffers[name] and taken < per_turn
                   and delivered < want and not stopflag.is_set()):
                row = buffers[name].pop(0)
                site = SITE.get(row["source"], row["source"])
                key = "%s:%s" % (site, row["ident"])
                # Three ways a wallpaper can be old news, counted apart
                # because they mean different things: the source just sent it
                # twice, it is already on his disk, or he has looked at it in
                # an earlier run and passed over it. All three are skipped
                # rather than dimmed -- he asked for it to stop showing him the
                # same things, and a grid of greyed-out ones is still a grid to
                # read past.
                #
                # The colour gate. `crank` is how dominant the chosen
                # colour is in this picture -- 0 is "mostly" -- and Commons
                # sends None, meaning it cannot be judged, which passes.
                if colour and row.get("crank") is not None:
                    if row["crank"] > COLOUR_LEVELS[state["colour_level"]]:
                        colour_skips += 1
                        continue
                if key in fresh:
                    dupes += 1
                    continue
                if key in seen:
                    owned_skips += 1
                    continue
                if key in offered:
                    shown_skips += 1
                    continue
                path = os.path.join(
                    THUMBS, "%s-%s.jpg" % (site, row["ident"]))
                pause = THUMB_GAP.get(name, 0.3)
                if pause and stopflag.wait(pause):
                    return
                try:
                    blob = fetch(row["thumb"])
                except Exception as exc:
                    idle_add(hunt_note,
                                  "preview %s failed: %s" % (key, exc))
                    continue                # one bad preview is not the end
                with open(path, "wb") as handle:
                    handle.write(blob)
                delivered += 1
                taken += 1
                # Recorded here, the moment it is put in front of him, not when
                # he does something about it. Passing over a wallpaper is the
                # commonest outcome and leaves no other trace.
                fresh.add(key)
                if not FAKE:
                    offered.add(key)
                    record_offered(key)
                idle_add(deliver,
                              dict(row, id=key, url=file_uri(path),
                                   have=False),
                              delivered, owned_skips + shown_skips)

            # A whole turn in which everything the source offered was the
            # right colour but not enough of it. Two of those and the search
            # widens one step rather than ending short and blaming the sources
            # -- which is what he chose when the measurement was put to him,
            # and it says so on the status strip when it happens, because a
            # widened search that looks like a strict one is a lie by omission.
            if colour and taken == 0 and colour_skips > colour_skips_before:
                state["colour_dry"] += 1
                if (state["colour_dry"] >= COLOUR_DRY_TURNS
                        and state["colour_level"] < len(COLOUR_LEVELS) - 1):
                    state["colour_level"] += 1
                    state["colour_dry"] = 0
                    word = COLOUR_LEVEL_NAMES[state["colour_level"]]
                    name_of = COLOUR_NAMES.get(colour, colour)
                    idle_add(
                        hunt_note,
                        "%s ran out at %d — widening to pictures %s %s"
                        % (name_of, delivered, word, name_of.lower()))
                    idle_add(
                        set_status,
                        "widened — %s ran out at %d of %d, now showing "
                        "pictures %s %s"
                        % (name_of.lower(), delivered, want, word,
                           name_of.lower()))
            elif taken:
                state["colour_dry"] = 0

            if exhausted and not buffers[name]:
                live.remove(name)
                turn = 0
                idle_add(hunt_note, "%s has no more results" % name)

        if stopflag.is_set():
            return
        if colour:
            idle_add(hunt_note,
                          "colour %s: %d pictures passed over for not being "
                          "enough of it" % (COLOUR_NAMES.get(colour, colour),
                                            colour_skips))
        if delivered < want:
            idle_add(hunt_exhausted, delivered,
                          owned_skips + shown_skips + dupes)
        else:
            idle_add(hunt_done, delivered, owned_skips, shown_skips,
                          dupes)

    def start_feed():
        """Start looking. Placeholders when asked for, the real thing otherwise."""
        stop_feed()
        if FAKE:
            state["timer"] = timeout_add(260, feed_one)
            return
        flag = threading.Event()
        state["stopflag"] = flag
        # Daemon: a search still running when the window closes must not hold
        # the process open behind a window that is already gone.
        thread = threading.Thread(target=hunt, args=(flag,), daemon=True)
        state["worker"] = thread
        thread.start()

    def stop_feed():
        if state["timer"] is not None:
            source_remove(state["timer"])
            state["timer"] = None
        flag = state.get("stopflag")
        if flag is not None:
            flag.set()                      # the thread notices and unwinds
        state["stopflag"] = None
        state["worker"] = None

    def feed_one():
        if not state["running"] or state["found"] >= state["want"]:
            state["timer"] = None
            if state["running"]:
                state["running"] = False
                say("scanner_setRunning", False)
                print("scanner: finished, %d of %d delivered"
                      % (state["found"], state["want"]), flush=True)
            return False

        i = state["index"]
        state["index"] = i + 1
        w, h = FAKE_SIZES[i % len(FAKE_SIZES)]
        ident = "ph%04d" % i
        source = (state["sources"] or ["wallhaven"])[i % max(
            1, len(state["sources"] or ["wallhaven"]))]
        path = os.path.join(THUMBS, "%s-%s.png" % (source, ident))
        try:
            make_placeholder(path, w, h, i)
        except Exception as exc:            # a preview that will not draw is
            print("scanner: preview %s failed: %s" % (ident, exc), flush=True)
            return True                     # not a reason to stop the search
        # Through the same door a real result comes in by, so the fake run
        # exercises the row store and the submit path rather than a shortcut
        # past them.
        deliver({"id": "%s:%s" % (source, ident), "ident": ident,
                 "source": source, "w": w, "h": h,
                 "thumb": None, "full": None, "bytes": None,
                 "url": file_uri(path), "have": False},
                state["found"] + 1, state.get("skipped", 0))
        return True

    def on_stopresume(_payload):
        """One button. Stopped shows SUBMIT SELECTIONS; running hides it."""
        state["running"] = not state["running"]
        if state["running"]:
            start_feed()
        else:
            stop_feed()
        say("scanner_setRunning", state["running"])
        print("scanner: %s" % ("resumed" if state["running"] else "stopped"),
              flush=True)

    def on_choosedir(_payload):
        """The "Choose folder" button in the save panel.

        A real folder chooser rather than a box to type a path into: a typed
        path with one letter wrong is a save that fails at the last step,
        after the downloads. This is the one dialog in the program that is not
        drawn by the page, because a file chooser is the system's own job and
        every one written in a web page is worse than the one already there.
        """
        chosen = QFileDialog.getExistingDirectory(
            window, "Save wallpapers to", state["savedir"] or LIBRARY)
        if not chosen:
            print("scanner: folder chooser cancelled, still saving to %s"
                  % state["savedir"], flush=True)
            return
        state["savedir"] = chosen
        save_savedir(chosen)
        relearn_folder(chosen)
        say("scanner_setSaveDir", pretty_path(chosen), chosen)
        print("scanner: saving to %s from now on" % chosen, flush=True)

    def on_submit(raw):
        """Download the ticked wallpapers at full size into his library.

        This is the one thing this program writes outside its own folder, and
        it only happens here -- on a button he pressed, for pictures he chose.
        Nothing is ever downloaded at size on the strength of a search.
        """
        payload = json.loads(raw)
        picks = payload.get("picks") or []
        # The folder the page had on its panel when the button was pressed.
        # Trusted only as far as it being a real folder: anything else falls
        # back to what this side already had rather than failing per file.
        where = payload.get("dir") or state["savedir"] or LIBRARY
        if where != state["savedir"]:
            state["savedir"] = where
            save_savedir(where)
            relearn_folder(where)
        rows = [state["rows"][key] for key in picks if key in state["rows"]]
        print("scanner: submit -- %d selected for %s: %s"
              % (len(picks), where, ", ".join(picks) if picks else "<none>"),
              flush=True)

        if FAKE:
            # A placeholder has no real picture behind it. Copying one into his
            # wallpaper folder to prove the button works would be a completion
            # claim with a forged artifact, and the randomiser would then put
            # it on his desktop.
            say("scanner_setStatus",
                "%d selected — nothing saved: this run is placeholders"
                % len(rows))
            print("scanner: submit ignored -- placeholders have no real file "
                  "behind them, nothing written", flush=True)
            # Still answer the page. Anything waiting on the save -- the
            # self-test does -- would otherwise wait for a reply that this
            # branch was never going to send.
            say("scanner_saved", 0, 0, 0)
            return

        if not rows:
            say("scanner_setStatus", "nothing to save")
            return

        flag = threading.Event()
        state["savestop"] = flag
        threading.Thread(target=save_all, args=(rows, flag, where),
                         daemon=True).start()

    def saved_done(saved, existing, failed, megabytes, where):
        parts = ["%d saved to %s" % (saved, pretty_path(where))]
        if existing:
            parts.append("%d already there" % existing)
        if failed:
            parts.append("%d failed" % failed)
        line = ", ".join(parts) + (" — %.1f MB" % megabytes if saved else "")
        say("scanner_setStatus", line)
        say("scanner_saved", saved, existing, failed)
        if saved:
            say("scanner_clearPicks")
        print("scanner: %s" % line, flush=True)
        return False

    def save_all(rows, stopflag, where):
        """Fetch each chosen wallpaper at full size, on its own thread.

        Three things worth their lines:

        The name is `<source>-<id>`, which is the habit his folder already
        keeps -- 63 of his 66 images are named that way. Keeping it is what
        makes "do I already have this" cost a directory listing forever.

        Each file is written as `.part` and renamed once it is whole. The
        randomiser reads that folder on a timer and globs for images; a
        half-downloaded jpg appearing there would eventually be set as the
        desktop. `.part` matches no image glob, and the rename is atomic
        because it lands on the same filesystem.

        A file already present is never overwritten. He may have edited or
        replaced one, and a scanner is not entitled to an opinion about that.
        """
        saved = existing = failed = 0
        total = 0
        # The library folder is made if it is not there. On this desktop it
        # has always existed and this line never fired; on a machine where
        # the program has never saved anything -- which is every friend's
        # machine on the first run -- every save would otherwise fail one at
        # a time, and the status strip would blame the download.
        try:
            os.makedirs(where, exist_ok=True)
        except OSError as exc:
            idle_add(hunt_failed, "cannot make %s: %s" % (where, exc))
            return
        for n, row in enumerate(rows, 1):
            if stopflag.is_set():
                break
            path = urllib.parse.urlparse(row["full"]).path
            ext = os.path.splitext(path)[1].lower()
            if ext not in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
                ext = ".jpg"
            # The site, never the pool. wallhaven's shuffled and loved pools
            # are the same wallpapers in a different order, and the same
            # picture reached either way must land on one filename.
            name = "%s-%s%s" % (SITE.get(row["source"], row["source"]),
                                row["ident"], ext)
            target = os.path.join(where, name)

            if os.path.exists(target):
                existing += 1
                continue

            idle_add(set_status, "saving %d of %d — %s" % (n, len(rows), name))
            try:
                blob = fetch(row["full"], timeout=180)
            except Exception as exc:
                failed += 1
                idle_add(hunt_note, "saving %s failed: %s" % (name, exc))
                continue

            part = target + ".part"
            try:
                with open(part, "wb") as handle:
                    handle.write(blob)
                os.replace(part, target)
            except Exception as exc:
                failed += 1
                if os.path.exists(part):
                    os.remove(part)
                idle_add(hunt_note, "writing %s failed: %s" % (name, exc))
                continue

            saved += 1
            total += len(blob)
            # It is his now, so it never comes back in a later search.
            seen.add("%s:%s" % (SITE.get(row["source"], row["source"]),
                                row["ident"]))
            idle_add(hunt_note, "saved %s (%.1f MB)"
                          % (name, len(blob) / 1048576.0))

        idle_add(saved_done, saved, existing, failed, total / 1048576.0,
                 where)

    # ---- closing, and the question that has to be asked -----------------

    def on_askdiscard(_payload):
        """The X in the results window. Never closes without asking first."""
        stop_feed()
        was = state["running"]
        state["running"] = False
        say("scanner_setRunning", False)
        say("scanner_confirmDiscard", was)
        print("scanner: X pressed, asking before deleting thumbnails",
              flush=True)

    def on_keepgoing(_payload):
        """Answered no to the discard question. Leave everything where it is."""
        say("scanner_confirmClosed")
        print("scanner: discard declined, thumbnails kept", flush=True)

    def on_discard(_payload):
        """Answered yes. Empty the thumbnail cache and go back to the bar.

        Only this program's own cache folder is touched. The wallpaper library
        is never a candidate -- it is not under this path and nothing here
        walks upwards out of it.
        """
        removed = 0
        if os.path.isdir(THUMBS):
            for name in os.listdir(THUMBS):
                target = os.path.join(THUMBS, name)
                if os.path.isfile(target):
                    os.remove(target)
                    removed += 1
        state.update(running=False, found=0, index=0, stage="bar",
                     rolled=False, size_before_roll=None,
                     cursors={}, skipped=0, skipped_owned=0, skipped_shown=0,
                     dupes=0, fresh=set(), rows={}, buffers={},
                     turn=0)
        resize_to(BAR_W, BAR_H, BAR_MIN_W, BAR_MIN_H)
        say("scanner_setStage", "bar")
        say("scanner_setSeenCount", len(offered))
        print("scanner: %d thumbnails deleted, back to the bar" % removed,
              flush=True)

    def on_selftest_done():
        """End the run once the scripted pass has been through everything."""
        print("scanner: SELF-TEST finished, closing", flush=True)
        shutdown()
        return False

    def on_forget(_payload):
        """Throw away the record of what he has been shown.

        His own words for why: after four or five scans he may feel he passed
        over something too quickly, and there was no way back to it.

        Three things are deliberately NOT cleared. The wallpapers in his folder
        stay excluded -- they are read off the filenames at every launch, so
        there is nothing here to clear and re-offering what he already owns is
        not what he asked for. The within-run duplicate guard stays, because no
        version of "show me more" wants one scan showing the same wallpaper
        twice. And nothing is deleted from his wallpaper folder, ever.

        The leftover previews go too. They are this program's own cache and
        they are what the record was about.
        """
        had = len(offered)
        offered.clear()
        try:
            if os.path.exists(OFFERED):
                os.remove(OFFERED)
        except OSError as exc:
            print("scanner: could not clear the record: %s" % exc, flush=True)
        thumbs = 0
        if os.path.isdir(THUMBS):
            for name in os.listdir(THUMBS):
                target = os.path.join(THUMBS, name)
                if os.path.isfile(target):
                    os.remove(target)
                    thumbs += 1
        say("scanner_setSeenCount", 0)
        print("scanner: forgot %d already-shown wallpapers and %d cached "
              "previews. %d in your folder are still excluded."
              % (had, thumbs, len(seen)), flush=True)
        return False

    def on_log(text):
        """The page's own record of a control firing.

        Phase one's second definition of done is that every control writes a
        line proving it fired. The ones the host handles print above; the ones
        that never leave the page -- a tick box, a theme -- come through here,
        so the log is a complete account either way.
        """
        print("scanner: page -- %s" % text, flush=True)
        if text == "selftest complete":
            timeout_add(400, on_selftest_done)

    def refreshed_themes(words):
        """A finished harvest, handed to the page without a restart."""
        say("scanner_setThemes", words)
        print("scanner: theme panel refreshed to %d themes" % len(words),
              flush=True)
        return False

    def start_theme_refresh():
        """Top the theme cache up, on a thread, if it is old or missing.

        Never at startup and never on the main loop: the harvest is a quarter
        of an hour of network, and this program's own notes say a frozen
        window is the thing to avoid. The window is already drawn and already
        usable with whatever the cache last held before this begins, and the
        panel is only swapped when there is something better to put in it.
        """
        if FAKE or os.environ.get("WALLSCAN_NO_HARVEST") == "1":
            return
        age = theme_cache_age_days()
        if age is not None and age < THEME_MAX_AGE_DAYS:
            return
        stop = threading.Event()
        state["harveststop"] = stop

        def work():
            try:
                blob = harvest_themes(
                    600, stop,
                    lambda line: print("scanner: %s" % line, flush=True))
            except Exception as exc:
                print("scanner: theme refresh failed: %s" % exc, flush=True)
                return
            if not stop.is_set() and blob.get("themes"):
                idle_add(refreshed_themes, blob["themes"])

        print("scanner: theme cache is %s — refreshing in the background, "
              "the window is not waiting on it"
              % ("missing" if age is None else "%d days old" % int(age)),
              flush=True)
        threading.Thread(target=work, daemon=True).start()

    def shutdown(*_args):
        """Take the view down before the window goes, and only then quit.

        Tearing the window down with the view still inside it leaves the
        browser painting into a surface that no longer exists -- on GTK that
        was measured here as a frame-clock assertion and an X error, every
        time. Qt is quieter about it and the order is kept anyway: the threads
        are told to stop, the view is dropped, and only then does the loop
        end. Both the in-page close button and the window manager's close
        arrive here, so there is one teardown rather than two.
        """
        if state.get("closing"):
            return True
        state["closing"] = True
        stop_feed()
        if state.get("savestop") is not None:
            state["savestop"].set()
        if state.get("harveststop") is not None:
            state["harveststop"].set()
        webview.setParent(None)
        webview.deleteLater()
        window.hide()
        app.quit()
        return True

    def on_close(_payload):
        shutdown()

    handlers = {
        "drag": on_drag,
        "resize": on_resize,
        "close": on_close,
        "minimize": on_minimize,
        "rolltoggle": on_rolltoggle,
        "search": on_search,
        "howmany": on_howmany,
        "cancelask": on_cancelask,
        "stopresume": on_stopresume,
        "submit": on_submit,
        "choosedir": on_choosedir,
        "kind": on_kind,
        "savekey": on_savekey,
        "openurl": on_openurl,
        "askdiscard": on_askdiscard,
        "discard": on_discard,
        "keepgoing": on_keepgoing,
        "forget": on_forget,
        "maxtoggle": on_maxtoggle,
        "log": on_log,
    }
    bridge = Bridge(handlers)
    # Held on the window as well: a bridge that only the channel refers to is
    # collected while the page is still talking to it, and the buttons stop
    # working a few seconds in for no reason the log would show.
    window._bridge = bridge
    channel.registerObject("host", bridge)

    def on_loaded(ok):
        """Hand the page its lists once it can receive them."""
        if not ok:
            print("scanner: the page failed to load", flush=True)
            return
        say("scanner_setKinds", KINDS, DEFAULT_KIND)
        send_kind(DEFAULT_KIND)
        say("scanner_setThemes", THEMES)
        say("scanner_setColours", COLOURS)
        say("scanner_setSaveDir", pretty_path(state["savedir"]),
            state["savedir"])
        say("scanner_setSeenCount", len(offered))
        age = theme_cache_age_days()
        print("scanner: page loaded, %d sources, %d themes and %d colours "
              "delivered (themes %s)"
              % (len(sources_for(DEFAULT_KIND)), len(THEMES), len(COLOURS),
                 ("harvested %d days ago" % int(age)) if age is not None
                 else "are the built-in starters — no cache yet"), flush=True)
        start_theme_refresh()

    webview.loadFinished.connect(on_loaded)

    # host: Ctrl-C, and why it needs a timer.
    #
    # Qt's event loop sits in C++ and does not return to Python while it is
    # idle, so a signal arriving during the wait is noted by the interpreter
    # and acted on only when Python next runs -- which, in a window nobody is
    # touching, is never. A timer that does nothing gives it that chance five
    # times a second. SIGTERM is registered only where it exists; Windows has
    # no such signal and asking for it there is an error, not a no-op.
    signal.signal(signal.SIGINT, lambda *_a: shutdown())
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, lambda *_a: shutdown())
    heartbeat = QTimer()
    heartbeat.timeout.connect(lambda: None)
    heartbeat.start(200)
    _TIMERS.add(heartbeat)

    # host: the second press of a menu button.
    #
    # A menu entry gets pressed twice, and the second press has to bring this
    # window back rather than open a second one -- two scanners would be two
    # searches writing into one offered-record and one thumbnail cache. On
    # Linux that was launch.sh calling xdotool, which Windows does not have.
    # It is done here instead, through a local socket that exists on both, so
    # the rule holds however the program was started.
    def on_second_instance():
        connection = guard.nextPendingConnection()
        if connection is None:
            return
        connection.disconnectFromServer()
        print("scanner: a second copy was started -- raising this one instead",
              flush=True)
        if state["rolled"]:
            on_rolltoggle(None)
        window.showNormal()
        window.raise_()
        window.activateWindow()

    guard.newConnection.connect(on_second_instance)

    # Sized and centred before it is shown, so it does not appear at one size
    # and jump to another. GTK centred by asking the window manager; Qt is
    # told where, which is also the only way it happens on Windows.
    resize_to(BAR_W, BAR_H, BAR_MIN_W, BAR_MIN_H)
    _area = app.primaryScreen().availableGeometry() if app.primaryScreen() \
        else None
    if _area is not None:
        window.move(_area.x() + (_area.width() - window.width()) // 2,
                    _area.y() + (_area.height() - window.height()) // 2)

    window.show()
    print("scanner: window open at %dx%d on a %s screen, prgname %s"
          % (window.width(), window.height(),
             ("%dx%d" % (_area.width(), _area.height())) if _area else "?",
             PRGNAME), flush=True)
    app.exec()
    print("scanner: closed cleanly", flush=True)


def single_instance():
    """The local socket that makes a second launch raise the first window.

    Returns the listening server, or None if this is the second copy -- in
    which case it has already asked the first one to come forward and the
    caller should simply exit.

    The stale-socket line matters on Linux: a crash leaves the socket file
    behind, and without removing it every later launch would believe a scanner
    was already running and quietly refuse to start. Windows named pipes go
    when their process goes and never need it.
    """
    probe = QLocalSocket()
    probe.connectToServer(PRGNAME)
    if probe.waitForConnected(300):
        probe.disconnectFromServer()
        return None
    QLocalServer.removeServer(PRGNAME)
    server = QLocalServer()
    server.listen(PRGNAME)
    return server


if __name__ == "__main__":
    setup_output()
    # `--harvest` fills the theme cache and exits. It is a quarter of an hour
    # of network and it must never be something the window waits on, so it is
    # its own run rather than a startup step. `--harvest 200` shortens it.
    if "--harvest" in sys.argv:
        _at = sys.argv.index("--harvest")
        _budget = 600
        if len(sys.argv) > _at + 1 and sys.argv[_at + 1].isdigit():
            _budget = int(sys.argv[_at + 1])
        harvest_themes(_budget)
        sys.exit(0)
    sys.exit(main() or 0)
