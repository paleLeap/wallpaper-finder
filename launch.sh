#!/usr/bin/env bash
# What the right-click menu runs.
#
# Not just `./scan`, for two reasons.
#
# A menu button gets pressed twice. The second press has to bring the window
# back rather than open a second one -- two scanners would be two searches
# writing into one offered-record and one thumbnail cache. `xdotool search
# --classname` matches the prgname scanner.py sets, which is `pale-wallscan`.
#
# And a menu gives a program no terminal, so its output would go nowhere. This
# program's stdout is the only instrument anyone has for what its page did, so
# it is kept -- inside this folder, not in ~/.cache, because everything this
# program writes stays here except the wallpapers themselves.
set -uo pipefail
DIR=$(dirname "$(readlink -f "$0")")

WID=$(xdotool search --classname pale-wallscan 2>/dev/null | head -1 || true)
if [ -n "$WID" ]; then
  # Already open. Un-minimise it and bring it to the front, on whichever
  # desktop the pointer is on -- a window that comes back somewhere else is a
  # window you have to go looking for.
  xdotool set_desktop_for_window "$WID" "$(xdotool get_desktop)" 2>/dev/null || true
  xdotool windowmap "$WID" 2>/dev/null || true
  xdotool windowactivate "$WID" 2>/dev/null || true
  exit 0
fi

mkdir -p "$DIR/cache"
exec setsid "$DIR/scan" >> "$DIR/cache/launch.log" 2>&1 < /dev/null &
