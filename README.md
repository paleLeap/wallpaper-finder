# Wallpaper Finder

A small desktop window that searches several wallpaper sources at once, shows you
cheap thumbnails, and downloads only the ones you tick.

Pick some sources, pick a theme and a colour if you want one, say how many to
fetch, and it fills a grid with previews as they arrive — each labelled with its
real resolution. Tick the ones you like, press **Submit selections**, and only
those are downloaded at full size into `~/Pictures/wallpapers`.

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
desktop at `~/Pictures/wallpapers`.

## Requirements

Linux with X11, and:

- Python 3.9+
- GTK 3, WebKit2GTK 4.1, PyGObject, pycairo
- `xdotool` (only used by `launch.sh`, for the desktop entry)

On Debian/Ubuntu:

```bash
sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-3.0 gir1.2-webkit2-4.1 xdotool
```

On Arch:

```bash
sudo pacman -S python-gobject python-cairo gtk3 webkit2gtk-4.1 xdotool
```

No pip packages — everything else is the standard library.

## Install

```bash
git clone https://github.com/paleLeap/wallpaper-finder.git
cd wallpaper-finder
./scan
```

That's it. `./scan` runs it in a terminal, which is where its output goes.

To put it in your application menu instead, edit `wallpaper-scanner.desktop` so
`Exec=` points at this folder's `launch.sh`, then copy it to
`~/.local/share/applications/`. `launch.sh` is the menu-safe launcher: pressing
the menu entry twice raises the open window rather than starting a second copy.

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

Two environment variables, both mainly for testing:

- `WALLSCAN_LIBRARY` — where wallpapers are saved (default `~/Pictures/wallpapers`)
- `WALLSCAN_OFFERED` — where the offered record lives (default `offered.txt` here)

## A note on where this came from

This was built for one particular desktop — a 4K panel running openbox and
picom — and a few of its choices are tuned to that: it forces its own text
scaling back to normal, draws its own window frame, and leaves compositor
opacity alone. It should behave on any X11 desktop, but that's where it grew up.
