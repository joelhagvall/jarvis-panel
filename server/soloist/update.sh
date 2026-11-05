#!/bin/sh
# Keep Spotify Soloist current: every build stops working 90 days after it was built (exit code 10), so this
# fetches Spotify's latest arm64 build daily and restarts the speaker only when the binary changed.
set -eu
DIR="$HOME/.local/share/soloist"
URL=https://soloist-builds.spotifycdn.com/soloist_release_arm64.tar.gz
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
curl --fail --silent --show-error --location --max-time 300 -o "$TMP/soloist.tar.gz" "$URL"
tar -xzf "$TMP/soloist.tar.gz" -C "$TMP"
"$TMP/soloist" --version >/dev/null
mkdir -p "$DIR/bin"
if [ -f "$DIR/bin/soloist" ] && cmp -s "$TMP/soloist" "$DIR/bin/soloist"; then
  echo "soloist: up to date ($("$DIR/bin/soloist" --version))"
  exit 0
fi
install -m 755 "$TMP/soloist" "$DIR/bin/soloist.new"
mv -f "$DIR/bin/soloist.new" "$DIR/bin/soloist"
echo "soloist: updated to $("$DIR/bin/soloist" --version)"
# A restart leaves Spotify paused and the phone in charge: 2026-10-30 a run by hand cut the music on the JBL. Play on
# afterwards when Jarvis was the active device and playing (as watch.py does after its restarts).
CTL="$DIR/bin/soloist ctl --ws ${JARVIS_SOLOIST_WS:-127.0.0.1:9431}"
WAS_PLAYING=$($CTL now --json 2>/dev/null | python3 -c 'import json, sys
d = json.load(sys.stdin)
print("yes" if d.get("status") == "playing" and d.get("is_active") is True else "")' 2>/dev/null || true)
if systemctl --user is-active --quiet hallpanel-soloist.service; then
  systemctl --user restart hallpanel-soloist.service
  if [ -n "$WAS_PLAYING" ]; then
    for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
      sleep 1
      if $CTL status 2>/dev/null | grep -q 'logged in: yes'; then break; fi
    done
    if $CTL activate && $CTL play; then echo "soloist: playing on after the restart"
    else echo "soloist: could not play on after the restart" >&2; fi
  fi
fi
