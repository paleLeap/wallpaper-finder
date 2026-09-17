#!/usr/bin/env bash
# What a Linux menu entry runs.
#
# Not just `../scan`, because a menu gives a program no terminal and its output
# would go nowhere. This program's stdout is the only instrument anyone has for
# what its page did, so it is kept -- inside this folder, not in ~/.cache,
# because everything this program writes stays here except the wallpapers.
#
# The other thing this script used to do is gone. A menu button gets pressed
# twice, and the second press has to raise the open window rather than start a
# second copy; that was `xdotool search --classname`, which does not exist on
# Windows. scanner.py does it itself now, over a local socket, so the rule
# holds on both systems and however the program was started.
set -uo pipefail
DIR=$(dirname "$(readlink -f "$0")")
mkdir -p "$DIR/cache"
exec setsid "$DIR/../scan" >> "$DIR/cache/launch.log" 2>&1 < /dev/null &
