#!/usr/bin/env bash
# Record a simulator config as an animated WebP, with each frame's real duration.
#
# Usage: tools/record.sh <config-dir> <config.yaml> <build-name> <seconds> <out.webp>
#   The config should play its own flow from boot (e.g. a gen.py --demo output).
#   <build-name> is the config's esphome name, which is also its build folder.
# Needs: ESPHome (ESPHOME, default: esphome on PATH), Python with Pillow (PYTHON), ImageMagick,
# Swift for the window id (tools/winid.swift), and macOS screencapture.
set -euo pipefail
DIR="$1"; YAML="$2"; NAME="$3"; SECS="$4"; OUT="$(cd "$(dirname "$5")" && pwd)/$(basename "$5")"
HERE="$(cd "$(dirname "$0")" && pwd)"
ESPHOME="${ESPHOME:-esphome}"; PYTHON="${PYTHON:-python3}"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

cd "$DIR"
"$ESPHOME" compile "$YAML" > "$TMP/build.log" 2>&1 || { tail -20 "$TMP/build.log"; exit 1; }
"./.esphome/build/$NAME/.pioenvs/$NAME/program" > /dev/null 2>&1 &
PID=$!
W=""
for _ in $(seq 1 40); do
  W=$(swift "$HERE/winid.swift" $PID 2>/dev/null | awk '$2>500{print $1; exit}') || true
  [ -n "$W" ] && break
done
[ -n "$W" ] || { kill $PID; echo "no simulator window"; exit 1; }

START=$(perl -MTime::HiRes=time -e 'printf "%.3f", time')
n=0
while :; do
  NOW=$(perl -MTime::HiRes=time -e 'printf "%.3f", time')
  awk -v a="$NOW" -v b="$START" -v s="$SECS" 'BEGIN{exit !(a-b < s)}' || break
  screencapture -x -o -l "$W" "$TMP/raw_$(printf %04d $n).png"
  echo "$NOW" >> "$TMP/times.txt"
  n=$((n+1))
done
kill $PID

"$PYTHON" - "$TMP" "$n" "$OUT" <<'EOF'
import sys
from pathlib import Path
from PIL import Image
tmp, n, out = Path(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
times = [float(t) for t in (tmp / "times.txt").read_text().split()]
frames, durs = [], []
for i in range(n):
    im = Image.open(tmp / f"raw_{i:04d}.png").convert("RGB")
    w, h = im.size
    im = im.crop((0, h - 1200, 2048, h)).resize((640, 375), Image.LANCZOS)
    frames.append(im)
    durs.append(int(round(((times[i + 1] - times[i]) if i + 1 < n else 0.2) * 1000)))
frames[0].save(out, save_all=True, append_images=frames[1:], duration=durs, loop=0, quality=80, method=4)
print(f"{n} frames in {sum(durs) / 1000:.1f} s -> {out}")
EOF
