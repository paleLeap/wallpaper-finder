# Wallpaper Finder

A small desktop window that searches several wallpaper sources at once, shows you
cheap thumbnails, and downloads only the ones you tick.

Pick some sources, pick a theme and a colour if you want one, say how many to
fetch, and it fills a grid with previews as they arrive — each labelled with its
real resolution. Tick the ones you like, press **Submit selections**, and only
those are downloaded at full size into `~/Pictures/wallpapers`.

Runs on **Linux and Windows**.

A few things it does on purpose:

- **Only large wallpapers.** Nothing below 3840×2160 is offered.
- **Previews first.** A preview is 14–32 KB against roughly 4 MB for the real
  file, so browsing a hundred candidates costs a couple of megabytes.
- **It remembers what it showed you.** Every wallpaper offered is recorded in
  `offered.txt` so it stops repeating itself. Delete that file to forget.
- **It won't re-download what you have.** The check is a listing of your
  wallpaper folder, so files you already own are skipped.
- **Safe searches only.** wallhaven is queried as General / SFW.
- **Everything it writes stays in its own folder** — thumbnails, logs, the
  offered record — except the wallpapers themselves.

It saves wallpapers. It does not set them; point whatever already rotates your
desktop at your wallpapers folder.

## Install

You need **Python 3.9 or newer**. Everything else is one package — PySide6,
which is Qt: the window, the browser engine the interface is drawn in, and the
painter behind the placeholder previews. It's a big download (~250 MB) because
a whole Chromium comes with it. Nothing else is required.

**Linux**

```bash
git clone https://github.com/paleLeap/wallpaper-finder.git
cd wallpaper-finder
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./scan
```

**Windows**

```
git clone https://github.com/paleLeap/wallpaper-finder.git
cd wallpaper-finder
py -m venv .venv
.venv\Scripts\pip install -r requirements.txt
scan.cmd
```

(No git? Use the green **Code** button on GitHub → Download ZIP, unpack it, and
run the same two commands inside the folder.)

`scan` and `scan.cmd` both keep a console, which is where the program says what
it did — worth having the first few times. `scan-quiet.cmd` starts it with no
console, which is what a Windows shortcut should point at: right-click it →
**Send to** → **Desktop (create shortcut)**.

On Linux, for an application-menu entry, edit `wallpaper-finder.desktop` so
`Exec=` points at this folder's `launch.sh`, then copy it to
`~/.local/share/applications/`.

Pressing a menu entry or shortcut twice raises the open window instead of
starting a second copy — two scanners would be two searches writing into one
record.

## Accounts and keys

Three sources work with no account at all: **wallhaven**, **wallhaven · loved**,
and **Wikimedia Commons**.

**Wikimedia Commons needs a contact.** Wikimedia refuses most Commons previews
unless the request says how to reach whoever is running the tool — measured here
as 8 of 8 previews succeeding with a contact, 4 of 8 without. Copy
`contact.txt.example` to `contact.txt` and put one line in it: an email address
or a URL. It is sent in the request header to Wikimedia and wallhaven, and
nowhere else. Commons still works without it, just less reliably.

**Optional keys** go in `keys.txt` (copy `keys.txt.example`), one
`name = value` per line. All free to register:

| Source | Sign up | Notes |
|---|---|---|
| Pexels | https://www.pexels.com/api/ | Works, and is used if a key is present |
| Unsplash | https://unsplash.com/developers | Reviewed by hand, 5–10 working days |
| Pixabay | https://pixabay.com/api/docs/ | Off: a free key only downloads 1280px |
| Flickr | https://www.flickr.com/services/apps/create/ | |
| Smithsonian | https://api.data.gov/signup/ | |
| Reddit | https://www.reddit.com/prefs/apps | Needs a registered app, not just a key |

A source with no key isn't offered, and its button says why rather than failing
when you press Search. `keys.txt` and `contact.txt` are both gitignored, and the
program never prints a key.

## Settings

Environment variables, all optional:

- `WALLSCAN_SCALE` — makes the whole window bigger, e.g. `1.5`. The layout is
  authored in real pixels and draws at 1:1 by default, which on a high-DPI
  laptop can come out small.
- `WALLSCAN_LIBRARY` — where wallpapers are saved (default `~/Pictures/wallpapers`)
- `WALLSCAN_OFFERED` — where the offered record lives (default `offered.txt` here)

`python scanner.py --harvest` refreshes the theme list from the sources. It
takes about fifteen minutes and the program does it by itself in the background
when the shipped list is over 30 days old, so you shouldn't need to.

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
