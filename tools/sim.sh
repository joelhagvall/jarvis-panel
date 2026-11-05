#!/usr/bin/env bash
# Build and open the panel in ESPHome's SDL simulator on the Mac, as the board will look: glass-v2 with the
# Jarvis orb, with today's background and orb frames downloaded from Jarvis like the board does.
#
# Usage: tools/sim.sh [jarvis_orb.py args...]
#   tools/sim.sh                          the dashboard
#   tools/sim.sh --page installningar     the dashboard with Inställningar open (also hemmet, natt, tavla)
#   tools/sim.sh --orb --card             the voice page with Jarvis's cards
#   tools/sim.sh --local                  the compiled-in Stjärnenatten instead of Jarvis's pictures
#   tools/sim.sh --backgrounds            every background choice in Inställningar's Bakgrund list, served from
#                                         the Mac (tools/sim_backgrounds.py); Joel's choice on Jarvis stays
# Without --remote or --local it adds --remote http://jarvis.local:8123/local/hallpanel.
# Needs the repo's .venv with ESPHome and SDL2 (see README, "Everyday commands").
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REMOTE="http://jarvis.local:8123/local/hallpanel"
NAME="style-glass-jarvis"

args=()
remote=1
backgrounds=0
for a in "$@"; do
  case "$a" in
    --local) remote=0 ;;
    --backgrounds) backgrounds=1 ;;
    --remote) remote=0; args+=("$a") ;;
    *) args+=("$a") ;;
  esac
done
if [ "$backgrounds" = 1 ] && [ "$remote" = 1 ]; then
  CHOICES="${TMPDIR:-/tmp}/hallpanel-sim-backgrounds"
  echo "Hämtar bakgrunderna från Jarvis …"
  "$ROOT/.venv/bin/python" "$ROOT/tools/sim_backgrounds.py" "$CHOICES" | grep -v '^Wrote'
  python3 -m http.server 8799 --bind 127.0.0.1 --directory "$CHOICES/www" >/dev/null 2>&1 &
  SERVER=$!
  trap 'kill $SERVER 2>/dev/null || true' EXIT
  sleep 1
  REMOTE="http://127.0.0.1:8799"
fi
[ "$remote" = 1 ] && args+=(--remote "$REMOTE")

cd "$ROOT/styles/glass-v2"
python3 jarvis_orb.py ${args[@]+"${args[@]}"} > jarvis-orb.yaml
LOG="$(mktemp)"; trap 'rm -f "$LOG"; [ -n "${SERVER:-}" ] && kill "$SERVER" 2>/dev/null; true' EXIT
echo "Bygger simulatorn …"
"$ROOT/.venv/bin/esphome" compile jarvis-orb.yaml > "$LOG" 2>&1 || { tail -30 "$LOG"; exit 1; }
echo "Öppnar fönstret (stäng det för att avsluta)."
"./.esphome/build/$NAME/.pioenvs/$NAME/program"
