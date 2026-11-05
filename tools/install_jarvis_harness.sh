#!/bin/sh
# Install this checkout's scheduled panel harness on Jarvis: hallpanel-health.timer and hallpanel-smoke.timer
# run ~/.local/share/hallpanel-harness/current/tools/*. Each install is a new release directory cloned from the
# current one (its secrets.yaml, .venv and .panel-ci links stay), with these files replaced. The candidate must
# import and read the live panel's /status before `current` moves to it in one rename; the old release stays.
# Run it whenever tools/panel_remote.py's protocol changes (2026-10-27: signed requests), before a flash.
#
#   sh tools/install_jarvis_harness.sh
set -eu
HOST=${JARVIS_SSH:-user@jarvis.local}
ROOT=$(cd "$(dirname "$0")/.." && pwd)
SSH="$ROOT/tools/jarvis_ssh.sh"
export RSYNC_RSH="sh '$SSH'"
BASE=.local/share/hallpanel-harness
# Every local module the shipped tools import, also the ones imported inside a function
# (2026-10-27: panel_ci's pruning imports panel_recovery, which was missing, so the smoke timer failed).
# tests/test_panel_remote.py checks this list against the import graph.
FILES="tools/panel tools/panel_ci.py tools/panel_ci_logs.py tools/panel_ci_store.py tools/panel_lock.py tools/panel_metrics.py tools/panel_remote.py tools/panel_diag.py
tools/panel_scheduled_smoke.py tools/panel_ui_test.py tools/panel_recovery.py tools/panel_visual.py
tools/panel_audio_check.py tools/panel_responsiveness.py tools/panel_throughput.py tests/performance/policy.json"
# shellcheck disable=SC2086
ID=$(cd "$ROOT" && cat $FILES | shasum -a 256 | cut -c1-16)

STATE=$("$SSH" "$HOST" sh -s -- "$ID" <<'REMOTE'
set -eu
cd ~/.local/share/hallpanel-harness/releases
if [ -d "$1" ]; then echo exists; exit 0; fi
rm -rf "$1.tmp"
cp -a "$(readlink -f ../current)" "$1.tmp"
rm -rf "$1.tmp/tools/__pycache__"
echo new
REMOTE
)
if [ "$STATE" = new ]; then
  # shellcheck disable=SC2086
  (cd "$ROOT" && rsync -a --relative $FILES "$HOST:$BASE/releases/$ID.tmp/")
fi
"$SSH" "$HOST" sh -s -- "$ID" <<'REMOTE'
set -eu
cd ~/.local/share/hallpanel-harness/releases
if [ -d "$1.tmp" ]; then
  url=$(sed -n 's/.*--url \(http[^ ]*\).*/\1/p' ~/.config/systemd/user/hallpanel-health.service | head -1)
  (cd "$1.tmp" && .venv/bin/python -c '
import sys
sys.path.insert(0, "tools")
import panel_ci, panel_lock, panel_metrics, panel_remote, panel_scheduled_smoke, panel_ui_test
import panel_recovery  # the scheduled smoke prunes runs through it
print("candidate reads the panel:", panel_remote.PanelClient(sys.argv[1]).status()["page"])' "$url")
  mv "$1.tmp" "$1"
fi
cd ..
ln -sfn "releases/$1" current.new
mv -T current.new current
echo "harness release $1 is current"
REMOTE
