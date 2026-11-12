#!/usr/bin/env bash
# Screenshot for the click loop: full screen or a region, optionally with the mouse pointer.
# Usage: shot.sh <out.png> [x,y,w,h] [--cursor]
#   x,y,w,h   region in screen points (screencapture -R); the image pixels then start at (x,y)
#   --cursor  include the pointer (screencapture -C), to confirm a move landed on the button
# Prints the image size in pixels: compare it with the screen size in points to get --scale for smooth_click.py.
set -euo pipefail

if [ "$(uname -s)" != "Darwin" ]; then
  echo "shot.sh: macOS only" >&2
  exit 2
fi

out="${1:?usage: shot.sh <out.png> [x,y,w,h] [--cursor]}"
shift
region=""
cursor=""
for arg in "$@"; do
  case "$arg" in
    --cursor) cursor="-C" ;;
    *,*,*,*) region="$arg" ;;
    *) echo "shot.sh: unknown argument: $arg" >&2; exit 2 ;;
  esac
done

opts=(-x)
if [ -n "$cursor" ]; then
  opts+=("$cursor")
fi
if [ -n "$region" ]; then
  opts+=(-R "$region")
fi

echo "shot.sh: capturing to $out" >&2
/usr/sbin/screencapture "${opts[@]}" "$out"
if [ ! -s "$out" ]; then
  echo "shot.sh: no image written; grant Screen Recording to the terminal (System Settings > Privacy & Security)" >&2
  exit 1
fi
size="$(sips -g pixelWidth -g pixelHeight "$out" | awk '/pixel/{printf "%s ", $2}')"
echo "shot: $out (${size% }px)"
