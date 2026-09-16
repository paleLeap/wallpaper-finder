#!/usr/bin/env python3
"""wallpaper-scanner - find wallpapers, look at cheap previews, keep the good ones.

PHASE ONE: the window only. Nothing talks to a real source yet. The previews
are generated here, on a timer, so the layout can be judged rather than
described -- and so the progressive fill is exercised by the same code path
the real one will use.

This is harbor's and arbor's sibling and is built from their template, but it
is a separate program. Nothing here reads, writes or imports anything under
`wake/`. Where the three need the same value -- the palette, the corner radii
-- this one carries its own copy, deliberately, so the others can change
without it noticing.

Four things about this desktop are load-bearing here, all on file in door
rather than assumed:

  Scaling.  Xft.dpi is 192, a 2x TEXT scale and nothing else. WebKit turns
  that into "1 CSS px = 2 device px", which once made a panel authored
  1600x900 render 3200x1800 inside its own window and cost a full session to
  diagnose. This process forces its own GTK DPI back to 96, so one CSS pixel
  is one real pixel and the stylesheet's numbers are the screen's numbers.
  (door/01-ui-ux/high-dpi-and-gtk-scaling.md, 2026-08-16)

  Opacity.  Deliberately NOT set. picom applies active-opacity 0.96 to any
  window it holds no rule for, so leaving it alone makes this window breathe
  like every other app here.
  (door/01-ui-ux/drawing-your-own-window-here.md, 2026-08-18)

  Naming.  A prgname that collides with a picom rule is the most repeated bug
  in this desktop's history. `pale-wallscan` contains no rule's substring --
  checked against picom.conf 2026-08-22, including `class_i *= 'pale-dock'`.

  Tooltips.  WebKit turns any `title` attribute into a black GTK tooltip in
  the system font that no stylesheet in the page can reach. The markup here
  uses aria-label only, and the host refuses tooltips as well, which is the
  half a future edit cannot undo.
  (door/01-ui-ux/webkit-title-tooltips.md, 2026-08-20)

Everything this program writes stays inside its own folder, except the
wallpapers themselves -- which phase one does not download, because there is
nothing real to download yet.
"""

import cairo  # noqa: E402
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("WebKit2", "4.1")
from gi.repository import Gtk, WebKit2, Gdk, GLib  # noqa: E402

# The signal helper moved namespaces. Both exist here, but the old one warns
# twice on every start, and this program's stdout is the only instrument the
# session that wrote it has -- it cannot see the screen -- so the log is kept
# clean rather than left to shout.
try:
    gi.require_version("GLibUnix", "2.0")
    from gi.repository import GLibUnix  # noqa: E402
    _signal_add = GLibUnix.signal_add
except (ValueError, ImportError):
    _signal_add = GLib.unix_signal_add

import json  # noqa: E402
import os  # noqa: E402
import random  # noqa: E402
import re  # noqa: E402
import signal  # noqa: E402
import sys  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
import urllib.parse  # noqa: E402
import urllib.request  # noqa: E402

PRGNAME = "pale-wallscan"
GLib.set_prgname(PRGNAME)

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
BAR_W, BAR_H = 1180, 360          # stage one: sources, themes, colour, SEARCH
BAR_MIN_W, BAR_MIN_H = 820, 300
ASK_W, ASK_H = 360, 196           # stage two: "How many?"
RESULTS_W, RESULTS_H = 1760, 1180  # stage three: the grid
RESULTS_MIN_W, RESULTS_MIN_H = 900, 420

# What the window shades to when the roll-up square is pressed. Must match
# --strip-h in ui/scanner.css.
STRIP_H = 86

# Must match --radius in ui/scanner.css. The shape below is what makes the
# corners real; the stylesheet only draws them.
RADIUS = 26

EDGES = {
    "n": Gdk.WindowEdge.NORTH,
    "s": Gdk.WindowEdge.SOUTH,
    "e": Gdk.WindowEdge.EAST,
    "w": Gdk.WindowEdge.WEST,
    "nw": Gdk.WindowEdge.NORTH_WEST,
    "ne": Gdk.WindowEdge.NORTH_EAST,
    "sw": Gdk.WindowEdge.SOUTH_WEST,
    "se": Gdk.WindowEdge.SOUTH_EAST,
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
        with open(os.path.join(HERE, "keys.txt")) as handle:
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


SOURCES = [
    {"id": "wallhaven",        "name": "wallhaven",          "ready": True},
    {"id": "wallhaven-fav",    "name": "wallhaven · loved",  "ready": True},
    {"id": "commons",          "name": "Commons",            "ready": True},
    # Ready only if a key is in keys.txt. A pill that lights up without one
    # would fail at the first request instead of saying why.
    {"id": "pexels",           "name": "Pexels",             "ready": "pexels" in KEYS,
     "why": "needs a key you register for"},
    # These three need an account. Not a preference -- they answered 401, 401
    # and 403 to a keyless call, measured 2026-08-22.
    {"id": "unsplash",  "name": "Unsplash",  "ready": False,
     "why": "key applied for; they review, 5–10 working days"},
    # A key for this exists and works. It is off anyway, and the reason is
    # measured rather than assumed: the API reports 6000x4000 and an ordinary
    # key can only download `largeImageURL`, which came back 1280x853
    # (2026-08-22). `fullHDURL` and `imageURL` are not served to it. Including
    # it would have put sub-HD files in a 4K folder under a 6000px label --
    # worse than not having the source at all.
    {"id": "pixabay",   "name": "Pixabay",   "ready": False,
     "why": "free key only downloads 1280px"},
    {"id": "reddit",    "name": "Reddit",    "ready": False,
     "why": "needs an app you register for"},
]

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
    "commons": "commons", "pexels": "pexels",
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
        with open(THEME_CACHE) as handle:
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
        with open(THEME_CACHE) as handle:
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
    with open(tmp, "w") as handle:
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
SOURCE_GAP = {"wallhaven": 1.5, "wallhaven-fav": 1.5, "commons": 1.5,
              "pexels": 2.0}

# Stop using a source when its own remaining-count gets this low, rather than
# discovering the limit by being refused. His words: hardwire it so I do not
# get locked out.
RATE_FLOOR = 20

# `large` is 432x243 and about 10 KB, against 4.7 MB for the full file --
# both measured on one real result 2026-08-22. That ratio is the entire reason
# previews exist, and why nothing is downloaded at size until it is ticked.
THUMB_SIZE = "large"

# The floor. Every one of the 66 images already in his library is 3840 wide or
# more, so anything under it would be the first thing he threw away.
ATLEAST = "3840x2160"

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
        with open(path) as handle:
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
THUMB_GAP = {"commons": 0.7, "wallhaven": 0.0}

COMMONS_API = "https://commons.wikimedia.org/w/api.php"

# Commons' search understands a width filter, which does the rejecting at
# their end instead of over the wire: `filew:>3839` returned six results all
# 4524 wide or more, measured 2026-08-22. `filetype:bitmap` keeps out the SVGs
# and PDFs that also live in the file namespace.
COMMONS_FILTER = "filetype:bitmap filew:>3839"

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

# The floor as a number, for checking rows a source filtered itself. Same
# value as ATLEAST above.
MIN_WIDTH = 3840

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


def run_js(webview, script):
    """Run a snippet in the page, preferring the call that is not deprecated.

    WebKit2 4.1 deprecates run_javascript in favour of evaluate_javascript.
    Both exist here; the older one warns on every call, and this log is the
    only instrument for what the page did.
    """
    if hasattr(webview, "evaluate_javascript"):
        webview.evaluate_javascript(script, -1, None, None, None, None, None)
    else:
        webview.run_javascript(script)


def rounded_region(w, h, r):
    """A cairo region covering one rounded rectangle at the origin.

    A region is made of whole rectangles, so the curve is approximated by one
    thin rectangle per row of the corner. At radius 26 the stepping is under a
    pixel.
    """
    region = cairo.Region()
    r = max(0, min(r, w // 2, h // 2))
    if w <= 0 or h <= 0:
        return region
    if r == 0:
        region.union(cairo.RectangleInt(0, 0, w, h))
        return region
    region.union(cairo.RectangleInt(0, r, w, h - 2 * r))
    for i in range(r):
        dy = r - i - 0.5
        dx = r - (r * r - dy * dy) ** 0.5
        inset = int(round(dx))
        span = w - 2 * inset
        if span <= 0:
            continue
        region.union(cairo.RectangleInt(inset, i, span, 1))
        region.union(cairo.RectangleInt(inset, h - 1 - i, span, 1))
    return region


def kill_tooltips(view):
    """Stop this view drawing GTK's own tooltip, whatever the page says.

    Two halves, because either alone can be undone: the widget is told it has
    no tooltip, and the query is answered "nothing to show" every time WebKit
    asks. Returning False from query-tooltip is what actually suppresses it.
    (door/01-ui-ux/webkit-title-tooltips.md, 2026-08-20)
    """
    view.set_has_tooltip(False)
    view.connect("query-tooltip", lambda *_a: False)


def pointer_position():
    """Root-window coordinates of the pointer, right now.

    begin_move_drag and begin_resize_drag both want the position the gesture
    started from. The page cannot tell us -- its coordinates are its own -- so
    the seat is asked directly.
    """
    seat = Gdk.Display.get_default().get_default_seat()
    _screen, px, py = seat.get_pointer().get_position()
    return px, py


def already_have():
    """The ids of wallpapers already in the library, read off their filenames.

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
    if not os.path.isdir(LIBRARY):
        return seen
    known = "|".join(re.escape(name) for name in sorted(set(SITE.values())))
    pattern = re.compile(r"^(%s)-([A-Za-z0-9]+)$" % known)
    for name in os.listdir(LIBRARY):
        stem, ext = os.path.splitext(name)
        if ext.lower() not in (".jpg", ".jpeg", ".png", ".webp"):
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


def wallhaven_page(query, page, seed, sorting="random", colour=None):
    """One page of wallhaven results, as the API returns it.

    `sorting=random` with a held seed is what makes paging coherent: without
    the seed every page is a fresh shuffle and the same wallpaper comes back
    on page 2 that was already on page 1. The seed arrives in the first
    response and is fed back into every one after it.

    Categories 100 is General only -- not anime, not people. Purity 100 is
    safe content only, which is also the half of the API that needs no key.
    """
    params = [("atleast", ATLEAST), ("categories", "100"), ("purity", "100"),
              ("sorting", sorting), ("page", str(page))]
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


def wallhaven_rows(query, cursor, pool="wallhaven", colour=None):
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
    payload = wallhaven_page(query, page, seed, sorting, colour)
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


def commons_rows(query, cursor, pool="commons", colour=None):
    """One page of Wikimedia Commons, normalised. Returns (rows, next cursor).

    A next cursor of None means the search is exhausted. Commons says so by
    leaving `continue` out of its answer, which is more honest than wallhaven,
    where running out only shows as an empty page.

    The width filter rides in the search string, so most of the rejecting
    happens at their end. The check below is the belt to that braces: `filew`
    is an index, and an index can lag the file it describes.
    """
    params = {
        "action": "query",
        "generator": "search",
        "gsrsearch": "%s %s %s" % (COMMONS_FILTER, COMMONS_POOL.get(pool, ""),
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
        if (info.get("width") or 0) < MIN_WIDTH:
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


def pexels_rows(query, cursor, pool="pexels", colour=None):
    """One page of Pexels, normalised. Returns (rows, next cursor).

    The key travels in an Authorization header, never in the URL. Pexels has no
    minimum-size parameter, so the 3840 floor is applied here and a page can
    come back mostly empty -- 18 of 20 cleared it on the query measured, but a
    narrow query will do worse.
    """
    page = cursor.get("page", 1)
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
        if (photo.get("width") or 0) < MIN_WIDTH:
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


# Which function fetches which source. A source with no entry here is one the
# window can list and cannot search -- which is what a disabled pill is.
def _pool(fn, pool):
    """Bind a pool to its adapter. A plain lambda in the dict below would close
    over the loop variable and every entry would fetch the last pool."""
    return lambda query, cursor, colour=None: fn(query, cursor, pool, colour)


def _adapter(name):
    if name.startswith("wallhaven"):
        return wallhaven_rows
    if name.startswith("commons"):
        return commons_rows
    return pexels_rows


# A source with no key gets no entry, so it can be listed and not searched.
FETCHERS = {name: _pool(_adapter(name), name)
            for name in SITE
            if name != "pexels" or "pexels" in KEYS}


def load_offered():
    """Everything already shown to him, from previous runs and this one."""
    shown = set()
    try:
        with open(OFFERED) as handle:
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
        with open(OFFERED, "a") as handle:
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

    Drawn with cairo, which is already imported for the window shape. PIL is
    on this machine but is not needed for a rectangle and a gradient.
    """
    tw = 432
    th = max(1, int(round(tw * h / w)))
    surface = cairo.ImageSurface(cairo.FORMAT_RGB24, tw, th)
    ctx = cairo.Context(surface)

    # A dark, low-chroma wash. hue walks with the index so the grid does not
    # read as one repeated tile.
    hue = (index * 37) % 360
    base = 0.10 + 0.13 * ((index % 5) / 4.0)
    grad = cairo.LinearGradient(0, 0, tw, th)
    for stop, lift in ((0.0, 0.0), (0.55, 0.09), (1.0, -0.03)):
        r, g, b = _hue_rgb(hue, 0.11, max(0.03, base + lift))
        grad.add_color_stop_rgb(stop, r, g, b)
    ctx.set_source(grad)
    ctx.paint()

    # A horizon and a few uprights: enough shape that a thumbnail reads as a
    # picture at a glance, which is what the grid has to be judged on.
    ctx.set_source_rgba(1, 1, 1, 0.05)
    ctx.rectangle(0, th * 0.62, tw, 1.5)
    ctx.fill()
    rng = random.Random(index)
    for _ in range(rng.randint(3, 9)):
        bx = rng.uniform(0, tw)
        bw = rng.uniform(6, 34)
        bh = rng.uniform(th * 0.06, th * 0.42)
        ctx.set_source_rgba(1, 1, 1, rng.uniform(0.02, 0.07))
        ctx.rectangle(bx, th * 0.62 - bh, bw, bh)
        ctx.fill()

    surface.write_to_png(path)


def _hue_rgb(hue, sat, val):
    """HSV to RGB in floats. Small enough to write than to import."""
    h = (hue % 360) / 60.0
    c = val * sat
    x = c * (1 - abs(h % 2 - 1))
    m = val - c
    r, g, b = [(c, x, 0), (x, c, 0), (0, c, x),
               (0, x, c), (x, 0, c), (c, 0, x)][int(h) % 6]
    return r + m, g + m, b + m


def main():
    window = Gtk.Window()
    window.set_title("Wallpaper scanner")
    window.set_decorated(False)
    window.set_default_size(BAR_W, BAR_H)
    window.set_size_request(BAR_MIN_W, BAR_MIN_H)
    # Load-bearing: a non-resizable GTK window grows but never shrinks, so
    # every stage change would be silently one-way.
    window.set_resizable(True)
    window.set_position(Gtk.WindowPosition.CENTER)

    # An RGBA visual. picom clips this window at corner-radius 10 and that is
    # not ours to change, so rounding beyond 10 can only come from the window
    # drawing its own shape -- and it can only draw a shape if the corners can
    # be transparent. It also removes the white flash while resizing: GTK
    # clears newly exposed area to the theme's window colour, which is light,
    # before WebKit has painted anything there.
    screen = window.get_screen()
    visual = screen.get_rgba_visual()
    if visual is not None and screen.is_composited():
        window.set_visual(visual)
    window.set_app_paintable(True)

    provider = Gtk.CssProvider()
    provider.load_from_data(b"window { background-color: transparent; }")
    Gtk.StyleContext.add_provider_for_screen(
        screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    # Neutralise the 2x text scale for this process only. GTK stores DPI in
    # 1024ths, so a bare 96 would be nonsense.
    settings = Gtk.Settings.get_default()
    if settings is not None:
        settings.set_property("gtk-xft-dpi", 96 * 1024)

    content = WebKit2.UserContentManager()
    channels = ("drag", "resize", "close", "minimize", "rolltoggle",
                "search", "howmany", "cancelask", "stopresume", "submit",
                "askdiscard", "discard", "keepgoing", "forget", "maxtoggle",
                "log")
    for channel in channels:
        content.register_script_message_handler(channel)

    webview = WebKit2.WebView.new_with_user_content_manager(content)

    # Send the page's console -- including uncaught JavaScript errors -- to
    # this process's stdout. Without it a page that throws on load looks
    # exactly like a page that worked: the window still opens, the process
    # still exits 0, and nothing anywhere says the layout never ran. That
    # matters more here than usual, because the session that wrote this cannot
    # see the screen.
    view_settings = webview.get_settings()
    view_settings.set_enable_write_console_messages_to_stdout(True)
    kill_tooltips(webview)

    # Refuse WebKit's own context menu outright. It is a light GTK menu in a
    # dark window, carrying reloads and inspectors this program has no use
    # for. This is the half that cannot be reasoned around by the page.
    webview.connect("context-menu", lambda *_a: True)

    # Transparent, not the ground colour: the page paints the ground itself on
    # a rounded rect, and anything outside that rect has to be genuinely
    # absent or the corners are square again.
    webview.set_background_color(Gdk.RGBA(0, 0, 0, 0))
    # WALLSCAN_SELFTEST=1 makes the page press its own controls, in order, and
    # print what each one did. It exists because the session that built this
    # cannot see the screen or touch the pointer: "every control fires" is
    # otherwise a claim with nothing behind it. Off unless asked for, and it
    # drives the same handlers a real click does rather than a parallel path.
    uri = "file://" + HTML_PATH
    _mode = os.environ.get("WALLSCAN_SELFTEST", "")
    if _mode in ("1", "live", "layout", "soak"):
        uri += "?selftest=" + _mode
        print("scanner: SELF-TEST -- the page will press its own buttons",
              flush=True)
    webview.load_uri(uri)
    window.add(webview)

    state = {"closing": False, "shape": None, "rolled": False,
             "size_before_roll": None, "stage": "bar", "running": False,
             "want": 0, "found": 0, "index": 0, "timer": None,
             "sources": [], "themes": [], "typed": "", "colour": "",
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

    seen = already_have()
    offered = load_offered()
    print("scanner: %d wallpapers already in the library have a readable id"
          % len(seen), flush=True)
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
        GLib.idle_add(run_js, webview, script)

    def resize_to(w, h, min_w, min_h):
        """Move the window to the size a stage needs.

        The minimum has to be lowered before the resize or GTK clamps to the
        old one and the window simply does not shrink -- the same trap arbor's
        roll-up hits, and the reason it lowers the request first.
        """
        window.set_size_request(min_w, min_h)
        window.resize(w, h)

    def apply_shape(*_a):
        """Cut the window to its own rounded rectangle whenever it resizes.

        This window is always a single rounded rect, so the page is never
        asked for the shape and there is no message to get out of step with
        the real size.
        """
        gdk_window = window.get_window()
        if gdk_window is None:
            return
        w, h = window.get_size()
        if state.get("shape") == (w, h):
            return
        state["shape"] = (w, h)
        gdk_window.shape_combine_region(rounded_region(w, h, RADIUS), 0, 0)

    window.connect("size-allocate", apply_shape)

    # ---- the window's own furniture ------------------------------------

    def on_drag(_cm, _message):
        px, py = pointer_position()
        window.begin_move_drag(1, px, py, Gtk.get_current_event_time())

    def on_resize(_cm, message):
        edge = EDGES.get(message.get_js_value().to_string())
        if edge is None:
            return
        px, py = pointer_position()
        window.begin_resize_drag(edge, 1, px, py, Gtk.get_current_event_time())

    def on_minimize(_cm, _message):
        window.iconify()
        print("scanner: minimised", flush=True)

    def on_maxtoggle(_cm, _message):
        # Rolled up and maximised are mutually exclusive: maximising while
        # shaded would give a full-width strip, which is neither state.
        if state["rolled"]:
            unroll()
            say("scanner_setRolled", False)
        if state.get("maximized"):
            window.unmaximize()
        else:
            window.maximize()
        state["maximized"] = not state.get("maximized")
        print("scanner: %s" % ("maximised" if state["maximized"]
                               else "restored"), flush=True)

    def unroll():
        width, height = state["size_before_roll"] or (RESULTS_W, RESULTS_H)
        window.set_size_request(RESULTS_MIN_W, RESULTS_MIN_H)
        window.resize(width, height)
        state["rolled"] = False

    def roll():
        state["size_before_roll"] = window.get_size()
        # The minimum height has to come off first, or the resize is clamped
        # to it and the window does not shade.
        window.set_size_request(RESULTS_MIN_W, STRIP_H)
        window.resize(state["size_before_roll"][0], STRIP_H)
        state["rolled"] = True

    def on_rolltoggle(_cm, _message):
        unroll() if state["rolled"] else roll()
        say("scanner_setRolled", state["rolled"])
        print("scanner: rolled %s" % ("up" if state["rolled"] else "down"),
              flush=True)

    # ---- stage one: what to search -------------------------------------

    def on_search(_cm, message):
        """SEARCH was pressed. Carry the picks forward and ask how many."""
        payload = json.loads(message.get_js_value().to_string())
        state["sources"] = payload.get("sources", [])
        state["themes"] = payload.get("themes", [])
        state["typed"] = (payload.get("typed") or "").strip()
        colour = (payload.get("colour") or "").strip().lower()
        # Only a swatch this program wrote down. A colour arriving from the
        # page that wallhaven would refuse is dropped here rather than turned
        # into a search that silently returns nothing.
        state["colour"] = colour if colour in COLOUR_HEXES else ""
        print("scanner: search wants sources=%s themes=%s typed=%r colour=%s"
              % (state["sources"] or ["<none>"], state["themes"] or ["<none>"],
                 state["typed"],
                 ("%s %s" % (COLOUR_NAMES.get(state["colour"], "?"),
                             state["colour"])) if state["colour"] else "any"),
              flush=True)
        state["stage"] = "ask"
        resize_to(ASK_W, ASK_H, ASK_W, ASK_H)
        say("scanner_setStage", "ask")

    def on_cancelask(_cm, _message):
        """Backed out of the how-many box. Return to the bar."""
        state["stage"] = "bar"
        resize_to(BAR_W, BAR_H, BAR_MIN_W, BAR_MIN_H)
        say("scanner_setStage", "bar")
        say("scanner_setSeenCount", len(offered))
        print("scanner: how-many cancelled, back to the bar", flush=True)

    # ---- stage two: how many, then the grid ----------------------------

    def on_howmany(_cm, message):
        """A number was confirmed. Open the results window and start filling."""
        raw = message.get_js_value().to_string()
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
    # Every one of these runs on the main loop, handed over by GLib.idle_add.
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
        state["colour_level"] = 0
        state["colour_dry"] = 0
        GLib.idle_add(hunt_note, "search string sent to the sources: %r%s"
                      % (query if query else "",
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
            GLib.idle_add(hunt_failed, "no source selected that I can search")
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
                    GLib.idle_add(hunt_note,
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
                    rows, nxt = FETCHERS[name](query, cursors[name], colour)
                except Exception as exc:
                    # One source falling over is not the end of the search. It
                    # is dropped, said out loud, and the others carry on.
                    GLib.idle_add(hunt_note, "%s failed: %s" % (name, exc))
                    live.remove(name)
                    turn = 0
                    continue

                buffers[name] = list(rows)
                cursors[name] = nxt or {}
                GLib.idle_add(remember_cursors, dict(cursors))
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
                    GLib.idle_add(hunt_note,
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
                GLib.idle_add(deliver,
                              dict(row, id=key, url="file://" + path,
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
                    GLib.idle_add(
                        hunt_note,
                        "%s ran out at %d — widening to pictures %s %s"
                        % (name_of, delivered, word, name_of.lower()))
                    GLib.idle_add(
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
                GLib.idle_add(hunt_note, "%s has no more results" % name)

        if stopflag.is_set():
            return
        if colour:
            GLib.idle_add(hunt_note,
                          "colour %s: %d pictures passed over for not being "
                          "enough of it" % (COLOUR_NAMES.get(colour, colour),
                                            colour_skips))
        if delivered < want:
            GLib.idle_add(hunt_exhausted, delivered,
                          owned_skips + shown_skips + dupes)
        else:
            GLib.idle_add(hunt_done, delivered, owned_skips, shown_skips,
                          dupes)

    def start_feed():
        """Start looking. Placeholders when asked for, the real thing otherwise."""
        stop_feed()
        if FAKE:
            state["timer"] = GLib.timeout_add(260, feed_one)
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
            GLib.source_remove(state["timer"])
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
                 "url": "file://" + path, "have": False},
                state["found"] + 1, state.get("skipped", 0))
        return True

    def on_stopresume(_cm, _message):
        """One button. Stopped shows SUBMIT SELECTIONS; running hides it."""
        state["running"] = not state["running"]
        if state["running"]:
            start_feed()
        else:
            stop_feed()
        say("scanner_setRunning", state["running"])
        print("scanner: %s" % ("resumed" if state["running"] else "stopped"),
              flush=True)

    def on_submit(_cm, message):
        """Download the ticked wallpapers at full size into his library.

        This is the one thing this program writes outside its own folder, and
        it only happens here -- on a button he pressed, for pictures he chose.
        Nothing is ever downloaded at size on the strength of a search.
        """
        picks = json.loads(message.get_js_value().to_string())
        rows = [state["rows"][key] for key in picks if key in state["rows"]]
        print("scanner: submit -- %d selected: %s"
              % (len(picks), ", ".join(picks) if picks else "<none>"),
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
        threading.Thread(target=save_all, args=(rows, flag),
                         daemon=True).start()

    def saved_done(saved, existing, failed, megabytes):
        where = LIBRARY.replace(os.path.expanduser("~"), "~")
        parts = ["%d saved to %s" % (saved, where)]
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

    def save_all(rows, stopflag):
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
        for n, row in enumerate(rows, 1):
            if stopflag.is_set():
                break
            path = urllib.parse.urlparse(row["full"]).path
            ext = os.path.splitext(path)[1].lower()
            if ext not in (".jpg", ".jpeg", ".png", ".webp"):
                ext = ".jpg"
            # The site, never the pool. wallhaven's shuffled and loved pools
            # are the same wallpapers in a different order, and the same
            # picture reached either way must land on one filename.
            name = "%s-%s%s" % (SITE.get(row["source"], row["source"]),
                                row["ident"], ext)
            target = os.path.join(LIBRARY, name)

            if os.path.exists(target):
                existing += 1
                continue

            GLib.idle_add(set_status, "saving %d of %d — %s" % (n, len(rows), name))
            try:
                blob = fetch(row["full"], timeout=180)
            except Exception as exc:
                failed += 1
                GLib.idle_add(hunt_note, "saving %s failed: %s" % (name, exc))
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
                GLib.idle_add(hunt_note, "writing %s failed: %s" % (name, exc))
                continue

            saved += 1
            total += len(blob)
            # It is his now, so it never comes back in a later search.
            seen.add("%s:%s" % (SITE.get(row["source"], row["source"]),
                                row["ident"]))
            GLib.idle_add(hunt_note, "saved %s (%.1f MB)"
                          % (name, len(blob) / 1048576.0))

        GLib.idle_add(saved_done, saved, existing, failed, total / 1048576.0)

    # ---- closing, and the question that has to be asked -----------------

    def on_askdiscard(_cm, _message):
        """The X in the results window. Never closes without asking first."""
        stop_feed()
        was = state["running"]
        state["running"] = False
        say("scanner_setRunning", False)
        say("scanner_confirmDiscard", was)
        print("scanner: X pressed, asking before deleting thumbnails",
              flush=True)

    def on_keepgoing(_cm, _message):
        """Answered no to the discard question. Leave everything where it is."""
        say("scanner_confirmClosed")
        print("scanner: discard declined, thumbnails kept", flush=True)

    def on_discard(_cm, _message):
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

    def on_forget(_cm, _message):
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

    def on_log(_cm, message):
        """The page's own record of a control firing.

        Phase one's second definition of done is that every control writes a
        line proving it fired. The ones the host handles print above; the ones
        that never leave the page -- a tick box, a theme -- come through here,
        so the log is a complete account either way.
        """
        text = message.get_js_value().to_string()
        print("scanner: page -- %s" % text, flush=True)
        if text == "selftest complete":
            GLib.timeout_add(400, on_selftest_done)

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
                GLib.idle_add(refreshed_themes, blob["themes"])

        print("scanner: theme cache is %s — refreshing in the background, "
              "the window is not waiting on it"
              % ("missing" if age is None else "%d days old" % int(age)),
              flush=True)
        threading.Thread(target=work, daemon=True).start()

    def shutdown(*_args):
        """Take the view down before the window goes, and only then quit.

        Letting GTK destroy the window with the WebView still inside it leaves
        WebKit painting into a surface that no longer exists -- measured on
        this machine as a GdkWindow warning, a failed frame-clock assertion
        and an X RenderBadPicture error, every time. Both the in-page close
        button and the window manager's close arrive here, so there is one
        teardown rather than two.
        """
        if state.get("closing"):
            return True
        state["closing"] = True
        stop_feed()
        if state.get("savestop") is not None:
            state["savestop"].set()
        if state.get("harveststop") is not None:
            state["harveststop"].set()
        window.hide()
        Gtk.main_quit()
        return True        # handled: do not also run GTK's default destroy

    def on_close(_cm, _message):
        shutdown()

    for channel, handler in (
            ("drag", on_drag),
            ("resize", on_resize),
            ("close", on_close),
            ("minimize", on_minimize),
            ("rolltoggle", on_rolltoggle),
            ("search", on_search),
            ("howmany", on_howmany),
            ("cancelask", on_cancelask),
            ("stopresume", on_stopresume),
            ("submit", on_submit),
            ("askdiscard", on_askdiscard),
            ("discard", on_discard),
            ("keepgoing", on_keepgoing),
            ("forget", on_forget),
            ("maxtoggle", on_maxtoggle),
            ("log", on_log)):
        content.connect("script-message-received::" + channel, handler)

    def on_loaded(_view, event):
        """Hand the page the two lists once it can receive them."""
        if event != WebKit2.LoadEvent.FINISHED:
            return
        say("scanner_setSources", SOURCES)
        say("scanner_setThemes", THEMES)
        say("scanner_setColours", COLOURS)
        say("scanner_setSeenCount", len(offered))
        age = theme_cache_age_days()
        print("scanner: page loaded, %d sources, %d themes and %d colours "
              "delivered (themes %s)"
              % (len(SOURCES), len(THEMES), len(COLOURS),
                 ("harvested %d days ago" % int(age)) if age is not None
                 else "are the built-in starters — no cache yet"), flush=True)
        start_theme_refresh()

    webview.connect("load-changed", on_loaded)

    window.connect("delete-event", shutdown)
    window.connect("destroy", shutdown)
    _signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, shutdown)
    _signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, shutdown)

    window.show_all()
    apply_shape()
    print("scanner: window open at %dx%d, prgname %s"
          % (BAR_W, BAR_H, PRGNAME), flush=True)
    Gtk.main()
    print("scanner: closed cleanly", flush=True)


if __name__ == "__main__":
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
    main()
