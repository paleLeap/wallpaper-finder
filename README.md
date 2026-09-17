# Wallpaper Finder

A small desktop window that searches several wallpaper sites at once, shows you
cheap thumbnails, and downloads only the ones you tick.

Pick your sources and themes, set a minimum size and a main colour if you want
them, and say how many to find. It fills a grid with previews as they arrive,
each labelled with its real resolution. Tick the ones you like, press **Submit
selections**, and only those are downloaded at full size — into a folder it
asks you about.

Runs on **Linux and Windows**.

There's a **?** button in the top corner, next to minimize and close, if you
want the short version in the window itself.

## What's in this folder

    scan, scan.cmd, scan-quiet.cmd   what you run
    README.md                        this
    program/                         the program and everything it needs

Everything else lives in `program/` — the code, the interface, the theme list,
the example config files, and anything the program writes while it runs. It
never writes outside that folder, except the wallpapers themselves.

## Install

You need **Python 3.9 or newer**. Everything else is one package — PySide6,
which is Qt: the window, the browser engine the interface is drawn in, and the
painter behind the placeholder previews. It's a big download (~250 MB) because
a whole Chromium comes with it. Nothing else is required.

**Linux**

```bash
git clone https://github.com/paleLeap/wallpaper-finder.git
cd wallpaper-finder
python3 -m venv program/.venv && program/.venv/bin/pip install -r program/requirements.txt
./scan
```

**Windows**

```
git clone https://github.com/paleLeap/wallpaper-finder.git
cd wallpaper-finder
py -m venv program\.venv
program\.venv\Scripts\pip install -r program\requirements.txt
scan.cmd
```

(No git? Use the green **Code** button on GitHub → Download ZIP, unpack it, and
run the same commands inside the folder.)

`scan` and `scan.cmd` both keep a console, which is where the program says what
it did — worth having the first few times. `scan-quiet.cmd` starts it with no
console, which is what a Windows shortcut should point at: right-click it →
**Send to** → **Desktop (create shortcut)**.

On Linux, for an application-menu entry, edit `program/wallpaper-finder.desktop`
so `Exec=` points at this folder's `program/launch.sh`, then copy it to
`~/.local/share/applications/`.

Pressing a menu entry or shortcut twice raises the open window instead of
starting a second copy — two scanners would be two searches writing into one
record.

## The sources

Only sources the program can actually search are listed, and each card says
what it is and what it needs. A source that couldn't be made to work without
editing the code isn't shown at all, greyed out or otherwise — so nothing on
that panel is a dead end.

| Source | Needs |
|---|---|
| **wallhaven** | Nothing. No account, no key. The biggest pool — about 59,000 at 4K or better |
| **wallhaven · loved** | Nothing. The same wallpapers, ordered by how many people kept them |
| **Wikimedia Commons** | Nothing, but see below. Big files (5–30 MB) and rarely 16:9 |
| **Pexels** | A free key. Register at https://www.pexels.com/api/ and put `pexels = <your key>` in `program/keys.txt` |

**Commons and `contact.txt`.** Wikimedia refuses most Commons previews unless
the request says how to reach whoever is running the tool — measured here as 8
of 8 previews succeeding with a contact and 4 of 8 without, same search, same
pacing. Copy `program/contact.txt.example` to `program/contact.txt` and put one
line in it: an email address or a URL. It goes in the request header to
Wikimedia and wallhaven, nowhere else. Commons works without it, just half as
well.

`keys.txt` and `contact.txt` are both gitignored, and the program never prints
a key.

*Not included, and why:* Unsplash needs a key reviewed by hand over 5–10 working
days; Pixabay's free key only downloads 1280px however big the picture actually
is; Reddit needs a registered app. None of the three has any code behind it
here, so all three would be buttons that could only ever say no.

## Sizes

The **Minimum size** dropdown is a floor: Any size, Full HD, 1440p, 4K
(the default), 5K, 8K.

**Specific size…** is the last entry, and it's different — it's exactly that
size, not that size or bigger, which is what you want for a banner or an
ultrawide. Type a width and a height. wallhaven answers this properly: 90,858
wallpapers at exactly 1920×1080, 2,230 at exactly 3440×1440. Commons and Pexels
have no such search, so they're filtered here and will rarely match at all —
and a size nobody uploads returns nothing from anywhere.

## Where they get saved

Pressing **Submit selections** asks where to put them, with the last folder you
chose already filled in — so the usual answer is to press Save. The button
showing the path opens your system's own folder chooser. The folder is created
if it isn't there, files already in it are never overwritten, and the choice is
remembered in `program/savedir.txt`.

It saves wallpapers. It does not set them — point whatever already rotates your
desktop at the folder you chose.

## What else it does

- **Only large wallpapers**, by whatever floor you set.
- **Previews first.** A preview is 14–32 KB against roughly 4 MB for the real
  file, so browsing a hundred candidates costs a couple of megabytes.
- **It remembers what it showed you**, in `program/offered.txt`, so it stops
  repeating itself. **Forget what I've seen** clears it; so does deleting the
  file.
- **It won't re-download what you have.** The check is a listing of your save
  folder, and it re-reads that folder when you change it.
- **Safe searches only.** wallhaven is queried as General / SFW.

**No GIFs.** wallhaven holds JPEG and PNG only — measured, not assumed — and
Pexels is photographs. Commons does have GIFs and they are searchable, but they
are lab animations and diagrams rather than anything you'd put on a desktop.
Animated wallpapers need a different kind of program anyway; a GIF set as an
ordinary desktop wallpaper doesn't move.

## Settings

Environment variables, all optional:

- `WALLSCAN_SCALE` — makes the whole window bigger, e.g. `1.5`. The layout is
  authored in real pixels and draws at 1:1 by default, which on a high-DPI
  laptop can come out small.
- `WALLSCAN_LIBRARY` — the save folder offered before you've ever chosen one
  (default `~/Pictures/wallpapers`)
- `WALLSCAN_OFFERED` — where the offered record lives

`python program/scanner.py --harvest` refreshes the theme list from the sources.
It takes about fifteen minutes, and the program does it by itself in the
background when the shipped list is over 30 days old, so you shouldn't need to.

## A note on where this came from

This was built for one particular desktop — a 4K panel running openbox and
picom — and then ported to run on Windows too. The port swapped the window
underneath it from GTK to Qt, because the browser engine the Linux version used
(WebKit2GTK) doesn't exist on Windows at all. The interface itself is the same
page on both.

One consequence worth knowing: the rounded corners need a compositor. Windows
always has one. Linux needs picom or similar running, which most desktops do —
without one, the corners come out black instead of transparent.

**The Linux side has been run and tested; the Windows side has been written but
not yet run on Windows.** If you're the first to try it there and something is
wrong, run `scan.cmd` rather than the quiet one and open an issue with what it
printed.
