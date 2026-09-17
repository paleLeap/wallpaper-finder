# Wallpaper Finder

Searches several wallpaper sites at once, shows you small previews, and
downloads only the ones you tick. Also does GIFs.

Runs on Linux and Windows.

## Install

You need Python 3.9 or newer. Everything else is one package.

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

No git? Green **Code** button above → Download ZIP, unpack, run the same
commands inside the folder.

The one package is PySide6 (~250 MB — a whole browser engine comes with it).

## Use

1. Choose **Images** or **GIFs**.
2. Tick the sources you want. Each card says what it is and what it needs.
3. Pick themes, or type your own words. Set a minimum size and a colour if you
   want them — **Specific size…** takes exact dimensions, for a banner or an
   ultrawide.
4. **Search**, say how many, and watch the grid fill.
5. Tick the good ones and press **Submit selections**. It asks which folder to
   put them in, then downloads those at full size.

**Stop** pauses; you can only submit while it's paused. The **?** in the corner
explains the program in three sentences. Nothing is downloaded at full size
until you tick it.

## Keys

wallhaven and Wikimedia Commons need nothing at all. Pexels and GIPHY need a
free key — click the source and a window opens explaining why, with a link to
the sign-up page and a box to paste the key into. That's all there is to it.

Commons works better if you put an email address or a URL in
`program/contact.txt` (copy `contact.txt.example`). Wikimedia sends roughly
twice as many previews to a tool that says how to reach its owner.

## Where things go

- Wallpapers go to the folder you choose when you save. It remembers it.
- Everything else stays in `program/` — previews, the log, your keys, and the
  record of what you've been shown.
- It won't offer you the same picture twice, or anything already in your
  folder. **Forget what I've seen** clears that.
- It saves wallpapers. It doesn't set them — point whatever changes your
  desktop at the folder.

## If something's wrong

Run `./scan` (or `scan.cmd`) from a terminal rather than a shortcut. The
program narrates what it's doing, and the last few lines usually say it
outright. On Windows, `scan-quiet.cmd` writes the same thing to
`program/cache/launch.log`.

Two known things: the rounded corners need a compositor, which Windows always
has and most Linux desktops run; and the window is drawn at real pixel sizes,
so on a very high-DPI screen set `WALLSCAN_SCALE=1.5` to make it bigger.

*Tested on Linux at four screen sizes. Written and checked for Windows but not
yet run there — if you're first, the terminal will tell you what broke.*
