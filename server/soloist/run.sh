#!/bin/sh
# Spotify Soloist as the Spotify Connect speaker "Jarvis", playing on the JBL through PipeWire's "jbl" sink
# (server/soloist/pipewire-jbl.conf -> BlueALSA). Its WebSocket control API listens on 127.0.0.1 only: it
# has no authentication (Spotify's docs), so it must never be exposed. docs/spotify.md.
set -eu
BIN="$HOME/.local/share/soloist/bin/soloist"
KEY_FILE="$HOME/.config/soloist/api-key"
[ -s "$KEY_FILE" ] || { echo "soloist: no API key at $KEY_FILE" >&2; exit 1; }
exec "$BIN" --device-name Jarvis --api-key "$(tr -d '\n\r ' < "$KEY_FILE")" \
  --data-dir "$HOME/.local/share/soloist/data" --cache-dir "$HOME/.cache/soloist" --cache-size 500 \
  --pipewire-device jbl --ws 127.0.0.1:9431 "$@"
