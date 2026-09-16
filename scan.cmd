@echo off
rem Open Wallpaper Finder. The Windows half of `scan`.
rem
rem Double-clicking this runs it with a console window behind it, which is
rem where its output goes -- and that output is the only account of what the
rem page did. Use scan-quiet.cmd to start it without one.
rem
rem A virtual environment beside this file is used if there is one, so
rem `py -m venv .venv` then `.venv\Scripts\pip install -r requirements.txt`
rem is enough of an install.
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" scanner.py %*
) else (
  py scanner.py %*
)
