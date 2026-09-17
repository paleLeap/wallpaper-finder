@echo off
rem Open Wallpaper Finder with no console window -- what a Start Menu or
rem desktop shortcut should point at.
rem
rem pythonw.exe is python without a console attached. Nothing is printed
rem anywhere in this mode, so when something is wrong, run scan.cmd instead
rem and read what it says.
cd /d "%~dp0program"
if exist ".venv\Scripts\pythonw.exe" (
  start "" ".venv\Scripts\pythonw.exe" scanner.py %*
) else (
  start "" pyw scanner.py %*
)
