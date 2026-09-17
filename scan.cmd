@echo off
rem Open Wallpaper Finder. The Windows half of `scan`.
rem
rem Double-clicking this runs it with a console window behind it, which is
rem where its output goes -- and that output is the only account of what the
rem page did. Use scan-quiet.cmd to start it without one.
rem
rem The program itself lives in program\, and so does everything it needs.
rem A virtual environment in program\.venv is used if there is one.
cd /d "%~dp0program"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" scanner.py %*
) else (
  py scanner.py %*
)
