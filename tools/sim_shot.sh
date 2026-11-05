#!/usr/bin/env bash
# One still of the simulator, cropped to the 1024x600 screen. For README pictures: run it on an exported
# tree (tools/publish_public.py) so the demo data is the public one (docs/public-export.md).
#
# Usage: tools/sim_shot.sh [--tree DIR] [--wait S] OUT.png [jarvis_orb.py args...]
#   tools/sim_shot.sh out.png                                    the dashboard
#   tools/sim_shot.sh out.png --then '[{"lvgl.page.show": {"id": "page_hemmet"}}]' --wait 11
#   tools/sim_shot.sh out.png --orb --card --then '[{"lvgl.widget.hide": "bar"}]' --wait 11
# Needs the repo's .venv with ESPHome, SDL2, ImageMagick and Screen Recording for the terminal.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TREE="$ROOT"; WAIT=5
while [ $# -gt 0 ]; do
  case "$1" in
    --tree) TREE="$(cd "$2" && pwd)"; shift 2 ;;
    --wait) WAIT="$2"; shift 2 ;;
    *) break ;;
  esac
done
OUT="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"; shift
LOG="$(mktemp)"; trap 'rm -f "$LOG"' EXIT
cd "$TREE/styles/glass-v2"
python3 jarvis_orb.py "$@" > jarvis-orb.yaml
"$ROOT/.venv/bin/esphome" compile jarvis-orb.yaml > "$LOG" 2>&1 || { tail -30 "$LOG"; exit 1; }
./.esphome/build/style-glass-jarvis/.pioenvs/style-glass-jarvis/program > /dev/null 2>&1 &
PID=$!
trap 'rm -f "$LOG"; kill $PID 2>/dev/null || true' EXIT
W=""
for _ in $(seq 1 40); do
  W=$(swift "$ROOT/tools/winid.swift" $PID 2>/dev/null | awk '$2>500{print $1; exit}') || true
  [ -n "$W" ] && break
done
[ -n "$W" ] || { echo "sim_shot: no simulator window"; exit 1; }
sleep "$WAIT"
screencapture -x -o -l "$W" "$OUT" || { echo "sim_shot: screencapture failed (Screen Recording permission?)"; exit 1; }
# The window is the screen plus a title bar on top: keep the bottom 1024:600 part.
H=$(magick identify -format "%w" "$OUT" | awk '{print int($1*600/1024)}')
magick "$OUT" -gravity south -crop "x${H}+0+0" +repage -resize 1024x600 "$OUT" 2>/dev/null
echo "sim_shot: $OUT"
