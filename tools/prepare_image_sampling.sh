#!/bin/sh
# Build/test a candidate on Jarvis before any live deployment. Print its private
# staging directory on stdout; the caller installs it and removes it on exit.
set -eu
HOST=${1:?SSH host required}
ROOT=$(cd "$(dirname "$0")/.." && pwd)
SSH="$ROOT/tools/jarvis_ssh.sh"
export RSYNC_RSH="sh '$SSH'"
STAGE=$("$SSH" "$HOST" 'mktemp -d /tmp/hallpanel-image-native.XXXXXX')
case "$STAGE" in /tmp/hallpanel-image-native.*) ;; *) echo 'invalid native staging directory' >&2; exit 1;; esac
trap '"$SSH" "$HOST" "rm -rf -- \"$STAGE\""' EXIT HUP INT TERM
"$SSH" "$HOST" "mkdir -p '$STAGE/native/image_sampling' '$STAGE/styles/glass-v2' '$STAGE/tests' '$STAGE/tools/image_experiment' '$STAGE/art'"
rsync -a --exclude target "$ROOT/native/image_sampling/" "$HOST:$STAGE/native/image_sampling/"
rsync -a "$ROOT/styles/glass-v2/bake.py" "$ROOT/styles/glass-v2/gen.py" \
  "$ROOT/styles/glass-v2/image_sampling.py" "$HOST:$STAGE/styles/glass-v2/"
rsync -a "$ROOT/styles/glass-v2/now_playing.py" "$ROOT/styles/glass-v2/now_playing.h" "$ROOT/styles/glass-v2/tavla_earth.py" "$ROOT/styles/glass-v2/weather_page.py" "$ROOT/styles/glass-v2/calendar_page.py" "$ROOT/styles/glass-v2/reminders_page.py" "$ROOT/styles/glass-v2/ai_page.py" "$HOST:$STAGE/styles/glass-v2/"
rsync -a "$ROOT/tests/test_bake.py" "$ROOT/tests/test_image_sampling.py" "$ROOT/tests/test_qoi.py" "$ROOT/tests/test_blur.py" "$HOST:$STAGE/tests/"
rsync -a "$ROOT/tools/image_experiment/reference_bake.py" "$HOST:$STAGE/tools/image_experiment/"
rsync -a "$ROOT/art/almond.jpg" "$HOST:$STAGE/art/"
"$SSH" "$HOST" sh -s -- "$STAGE" <<'REMOTE' >&2
set -eu
STAGE=$1
export PATH="$HOME/.cargo/bin:$PATH"
cd "$STAGE/native/image_sampling"
cargo fmt --check
cargo clippy --locked --all-targets -- -D warnings
cargo test --locked
cargo build --locked --release
cp target/release/libhallpanel_image_sampling.so "$STAGE/styles/glass-v2/"
cd "$STAGE"
"$HOME/hallpanel-live/venv/bin/python" -m unittest discover -s tests -p test_image_sampling.py -v
"$HOME/hallpanel-live/venv/bin/python" -m unittest discover -s tests -p test_qoi.py -v
"$HOME/hallpanel-live/venv/bin/python" -m unittest discover -s tests -p test_blur.py -v
"$HOME/hallpanel-live/venv/bin/python" -m unittest discover -s tests -p test_bake.py -v
REMOTE
trap - EXIT HUP INT TERM
printf '%s\n' "$STAGE"
