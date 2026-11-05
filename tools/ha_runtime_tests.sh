#!/bin/sh
# Tests that need Home Assistant's own runtime (they skip on the Mac), run inside the homeassistant container on
# Jarvis against this checkout's files. Copies to /tmp there and removes it again; installs nothing.
#   sh tools/ha_runtime_tests.sh [test_media_ha ...]
set -eu
ROOT=$(cd "$(dirname "$0")/.." && pwd)
HOST=${JARVIS_SSH:-user@jarvis.local}
SSH="$ROOT/tools/jarvis_ssh.sh"
export RSYNC_RSH="sh '$SSH'"
[ $# -gt 0 ] || set -- test_media_ha
# The names build a docker exec command line on Jarvis; only test-module names may go through (2026-11-02).
for t in "$@"; do
  case "$t" in *[!A-Za-z0-9_-]*) echo "test name may only contain letters, digits, _ and -: $t" >&2; exit 2;; esac
done
"$SSH" "$HOST" 'rm -rf /tmp/hp-hatest && mkdir -p /tmp/hp-hatest/tests /tmp/hp-hatest/ha'
for t in "$@"; do rsync -a "$ROOT/tests/$t.py" "$HOST:/tmp/hp-hatest/tests/"; done
rsync -a --exclude __pycache__ "$ROOT/ha/custom_components" "$HOST:/tmp/hp-hatest/ha/"
"$SSH" "$HOST" "docker cp /tmp/hp-hatest homeassistant:/tmp/hp-hatest >/dev/null &&
  docker exec -w /tmp/hp-hatest/tests homeassistant python3 -m unittest $*; s=\$?;
  docker exec homeassistant rm -rf /tmp/hp-hatest; rm -rf /tmp/hp-hatest; exit \$s"
