# PyInstaller recipe for the installed Windows copy (it also builds on Linux,
# which is how the frozen paths get tested without a Windows machine).
#
#   pyinstaller packaging/wallpaper-finder.spec
#
# One folder, not one file: a one-file build unpacks a whole Chromium to a
# temporary folder on every launch, which is slow and leaves litter. The folder
# lands in dist/Wallpaper Finder, and packaging/installer.iss wraps it.
import os

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
PROGRAM = os.path.join(ROOT, "program")

a = Analysis(
    [os.path.join(PROGRAM, "scanner.py")],
    pathex=[PROGRAM],
    datas=[
        (os.path.join(PROGRAM, "ui"), "ui"),
        (os.path.join(PROGRAM, "cache", "themes.json"), "cache"),
        (os.path.join(PROGRAM, "keys.txt.example"), "."),
        (os.path.join(PROGRAM, "contact.txt.example"), "."),
    ],
    excludes=["tkinter"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Wallpaper Finder",
    console=False,
    icon=os.path.join(SPECPATH, "icon.ico"),
)
coll = COLLECT(exe, a.binaries, a.datas, name="Wallpaper Finder")
