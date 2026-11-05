#!/bin/sh
# A worktree of an older commit that tools/flash_panel.sh can check, build and install, for an A/B on the panel
# (docs/responsiveness.md). It shares this checkout's .venv, .panel-ci and .panel-visual, gets a private copy of
# secrets.yaml, and an APFS clone of the ESPHome build cache: without the cache tools/check.py fails on missing
# ESPHome/LVGL dependencies before anything is uploaded (2026-10-30).
#   sh tools/old_build_worktree.sh COMMIT NEW_DIR
#   cd NEW_DIR && sh tools/flash_panel.sh        # then measure; flash this checkout again afterwards
#   git worktree remove --force NEW_DIR
set -eu
COMMIT=${1:?commit required}
DIR=${2:?new directory required}
ROOT=$(cd "$(dirname "$0")/.." && pwd)
[ ! -e "$DIR" ] || { echo "$DIR exists; give a new directory" >&2; exit 1; }
git -C "$ROOT" worktree add -q --detach "$DIR" "$COMMIT"
for shared in .venv .panel-ci .panel-visual; do
  ln -s "$ROOT/$shared" "$DIR/$shared"
done
install -m 600 "$ROOT/styles/glass-v2/secrets.yaml" "$DIR/styles/glass-v2/secrets.yaml"
cp -c -R "$ROOT/styles/glass-v2/.esphome" "$DIR/styles/glass-v2/.esphome"
echo "$DIR at $(git -C "$DIR" rev-parse --short HEAD); flash with: cd $DIR && sh tools/flash_panel.sh"
