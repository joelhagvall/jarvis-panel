#!/bin/sh
# Put the Jarvis side of the hallway panel on Jarvis, from the Mac. Nothing in the firewall changes:
# the panel talks only to Home Assistant, which reaches the server on 127.0.0.1.
#
#   tools/deploy_jarvis.sh                  server + images + Home Assistant integration files; restarts
#                                           Home Assistant only when files it loads at startup changed
#   tools/deploy_jarvis.sh --restart-ha     always restart Home Assistant
#   tools/deploy_jarvis.sh --no-restart-ha  never restart it, even when its files changed
#
# What goes where on Jarvis:
#   ~/hallpanel-live/server/                         the server, run by the user service hallpanel.service
#   ~/hallpanel-live/venv/                           the Python the service runs: Jarvis's own python3 plus
#                                                    server/requirements.txt (the anthropic brain's SDK, numpy, Pillow)
#   ~/hallpanel-live/styles/glass-v2/                bake.py and gen.py, for hallpanel-apod (NASA's picture daily)
#   ~/.config/hallpanel/server.env                   its token and agent settings, made once (mode 600)
#   ~/.config/hallpanel/profile.json                 what Jarvis knows about Joel (server/memory.py), by hand, never deployed
#   ~/.local/share/hallpanel/piper/                  Piper's Swedish voice, the voice without the internet (server/voice.py)
#   ~/docker/homeassistant/config/custom_components/jarvis_agent/   the Home Assistant integration
#   ~/docker/homeassistant/config/www/hallpanel/     the panel's images, at http://<jarvis>:8123/local/hallpanel/
#   ~/hallpanel-live/ha/ha_tool.py                   adds the integration and the Assist pipeline "Jarvis"
#   hallpanel-mpd.service                            MPD for the Bluetooth speaker, once server/bluetooth/setup.sh
#                                                    has installed it (docs/bluetooth-speaker.md)
set -eu
HOST=${JARVIS_SSH:-user@jarvis.local}
ROOT=$(cd "$(dirname "$0")/.." && pwd)
SSH="$ROOT/tools/jarvis_ssh.sh"
export RSYNC_RSH="sh '$SSH'"
HA=docker/homeassistant/config

RESTART_HA=
case "${1:-}" in
  --restart-ha) RESTART_HA=--restart-ha ;;
  --no-restart-ha|"") ;;
  *) echo "usage: $0 [--restart-ha|--no-restart-ha]" >&2; exit 2 ;;
esac
PYTHON=python3
[ ! -x "$ROOT/.venv/bin/python" ] || PYTHON="$ROOT/.venv/bin/python"
# Validate the candidate locally and in HA's runtime before staging/copying/restarting anything.
"$PYTHON" "$ROOT/tools/check.py" --ha "$HOST"
# server.env is read by systemd and also sourced with `.` in the remote step: a value with | ( ) or spaces
# must be quoted, or the deploy stops halfway (2026-11-03). Check it before anything is copied.
"$SSH" "$HOST" '[ ! -f ~/.config/hallpanel/server.env ] || sh -c ". ~/.config/hallpanel/server.env" >/dev/null 2>&1' \
  || { echo "~/.config/hallpanel/server.env on $HOST does not parse as sh: quote values that hold | ( ) or spaces" >&2; exit 1; }
# The working tree is what ships. Say which uncommitted files go along, possibly from another session.
DIRTY=$(git -C "$ROOT" status --short -- server ha styles/glass-v2 tools 2>/dev/null || true)
if [ -n "$DIRTY" ]; then
  printf 'Deploying on top of %s with %s uncommitted file(s):\n%s\n' "$(git -C "$ROOT" rev-parse --short HEAD)" \
    "$(printf '%s\n' "$DIRTY" | wc -l | tr -d ' ')" "$DIRTY"
fi
# Home Assistant loads the integration and the wakeup YAML only at startup. Compare contents, not times.
if [ "${1:-}" = "" ]; then
  HA_CHANGES=$(rsync -rcn --itemize-changes --delete --exclude __pycache__ \
    "$ROOT/ha/custom_components/jarvis_agent/" "$HOST:$HA/custom_components/jarvis_agent/")
  if "$SSH" "$HOST" "test -f $HA/jarvis_wakeup.yaml"; then
    HA_CHANGES="$HA_CHANGES$(rsync -rcn --itemize-changes "$ROOT/ha/jarvis_wakeup.yaml" \
      "$ROOT/ha/jarvis_wakeup_dashboard.yaml" "$HOST:$HA/")"
  fi
  if [ -n "$HA_CHANGES" ]; then
    printf 'Home Assistant files changed, so Home Assistant restarts after the copy:\n%s\n' "$HA_CHANGES"
    RESTART_HA=--restart-ha
  fi
fi
# Native artifacts are built and pixel-tested in isolation on the target CPU.
# A failed build/test must leave the installed Python and library untouched.
NATIVE_STAGE=$(sh "$ROOT/tools/prepare_image_sampling.sh" "$HOST")
trap '"$SSH" "$HOST" "rm -rf -- \"$NATIVE_STAGE\""' EXIT HUP INT TERM
"$PYTHON" "$ROOT/server/stage.py"
"$SSH" "$HOST" "mkdir -p hallpanel-live/server hallpanel-live/ha hallpanel-live/styles/glass-v2 $HA/www/hallpanel"
# Install native features before any Python renderer or image-conversion code that imports them.
"$SSH" "$HOST" "cp '$NATIVE_STAGE/styles/glass-v2/libhallpanel_image_sampling.so' hallpanel-live/styles/glass-v2/.libhallpanel_image_sampling.so.tmp && mv hallpanel-live/styles/glass-v2/.libhallpanel_image_sampling.so.tmp hallpanel-live/styles/glass-v2/libhallpanel_image_sampling.so"
rsync -a "$ROOT/styles/glass-v2/image_sampling.py" "$HOST:hallpanel-live/styles/glass-v2/"
rsync -a "$ROOT/styles/glass-v2/bake.py" "$ROOT/styles/glass-v2/gen.py" "$HOST:hallpanel-live/styles/glass-v2/"
rsync -a "$ROOT/styles/glass-v2/now_playing.py" "$ROOT/styles/glass-v2/now_playing.h" "$ROOT/styles/glass-v2/tavla_earth.py" "$ROOT/styles/glass-v2/weather_page.py" "$ROOT/styles/glass-v2/calendar_page.py" "$ROOT/styles/glass-v2/reminders_page.py" "$ROOT/styles/glass-v2/ai_page.py" "$HOST:hallpanel-live/styles/glass-v2/"
rsync -a --delete --exclude __pycache__ --exclude www "$ROOT/server/" "$HOST:hallpanel-live/server/"
rsync -a --delete --exclude __pycache__ "$ROOT/ha/custom_components/jarvis_agent/" "$HOST:$HA/custom_components/jarvis_agent/"
if "$SSH" "$HOST" "test -f $HA/jarvis_wakeup.yaml"; then
  rsync -a "$ROOT/ha/jarvis_wakeup.yaml" "$ROOT/ha/jarvis_wakeup_dashboard.yaml" "$HOST:$HA/"
fi
sh "$ROOT/tools/deploy_assets.sh" "$ROOT/server/www" "$HOST:$HA/www/hallpanel"
rsync -a "$ROOT/ha/ha_tool.py" "$HOST:hallpanel-live/ha/"
# The Jina key for web search, if there is one on the Mac (mode 600, never printed).
if [ -f "$HOME/.config/hallpanel/jina.env" ]; then
  "$SSH" "$HOST" "umask 077 && mkdir -p .config/hallpanel && cat > .config/hallpanel/jina.env" < "$HOME/.config/hallpanel/jina.env"
fi
# The paintings Joel can pick as the background in Home Assistant (server/agents/background.py).
"$SSH" "$HOST" "mkdir -p hallpanel-live/styles/glass-v2/assets"
rsync -a "$ROOT"/styles/glass-v2/assets/bg_starry.png "$ROOT"/styles/glass-v2/assets/art_starry.png \
  "$ROOT"/styles/glass-v2/assets/bg_almond.png "$ROOT"/styles/glass-v2/assets/art_almond.png \
  "$HOST:hallpanel-live/styles/glass-v2/assets/"

"$SSH" "$HOST" RESTART_HA="$RESTART_HA" sh -s <<'REMOTE'
set -eu
mkdir -p ~/.config/hallpanel ~/.config/systemd/user ~/.local/share/hallpanel
chmod 700 ~/.config/hallpanel
if [ ! -f ~/.config/hallpanel/server.env ]; then
  umask 077
  cat > ~/.config/hallpanel/server.env <<ENV
HALLPANEL_TOKEN=$(openssl rand -hex 24)
JARVIS_AGENT=inprocess
JARVIS_CALENDAR_MCP=http://jarvis.local:3020/mcp
JARVIS_CALENDAR_MCP_TOKEN_FILE=$HOME/.config/mcp-portal/calendar.token
ENV
  echo "made ~/.config/hallpanel/server.env"
fi
# Old names (2026-10-23): the settings lost their LUNA_ prefix and JARVIS_AGENT=luna became inprocess.
if grep -q '^LUNA_MCP\|^LUNA_TIER=\|^JARVIS_AGENT=luna$' ~/.config/hallpanel/server.env; then
  sed -i 's/^LUNA_MCP/JARVIS_CALENDAR_MCP/; s/^LUNA_TIER=/JARVIS_BRAIN_TIER=/; s/^JARVIS_AGENT=luna$/JARVIS_AGENT=inprocess/' \
    ~/.config/hallpanel/server.env
  echo "renamed old settings in ~/.config/hallpanel/server.env"
fi
if grep -q '^LUNA_' ~/.config/hallpanel/server.env; then
  echo "~/.config/hallpanel/server.env still has LUNA_ settings that nothing reads any more:"
  grep -o '^LUNA_[A-Z_]*' ~/.config/hallpanel/server.env
fi
# The read-only home status (server/agents/home_status.py) reads Home Assistant with its token on Jarvis.
if ! grep -q '^JARVIS_HOME_TOKEN_FILE=' ~/.config/hallpanel/server.env; then
  echo "JARVIS_HOME_TOKEN_FILE=$HOME/.config/homeassistant/token" >> ~/.config/hallpanel/server.env
  echo "turned on the home status in ~/.config/hallpanel/server.env"
fi
# Jarvis pushes reminders, and what Joel asks to have sent, to his iPhone (server/notify.py).
if ! grep -q '^JARVIS_PHONE=' ~/.config/hallpanel/server.env; then
  echo "JARVIS_PHONE=mobile_app_demo_phone" >> ~/.config/hallpanel/server.env
  echo "turned on pushes to the phone in ~/.config/hallpanel/server.env"
fi
# Once Home Assistant has adopted the panel, Jarvis speaks on it (reminders, alarms, the door ...). Add to server.env:
#   JARVIS_PANEL_SATELLITE=assist_satellite.<the panel's entity>
# The TV by voice (on again 2026-10-27, Joel's wish; it was off from 2026-10-22 to keep the prompt short):
# Jarvis gets the tv connector's voice-safe tools (agent_core.TV_TOOLS). Delete both lines to turn it off.
if ! grep -q '^JARVIS_TV_MCP=' ~/.config/hallpanel/server.env && [ -s ~/.config/mcp-portal/tv.token ]; then
  printf '%s\n' "JARVIS_TV_MCP=http://jarvis.local:3010/mcp" "JARVIS_TV_MCP_TOKEN_FILE=$HOME/.config/mcp-portal/tv.token" \
    >> ~/.config/hallpanel/server.env
  echo "turned on the TV by voice in ~/.config/hallpanel/server.env"
fi
# The venv the service runs in; pip runs only when server/requirements.txt changed.
VENV=~/hallpanel-live/venv REQ=~/hallpanel-live/server/requirements.txt
[ -x $VENV/bin/python3 ] || python3 -m venv $VENV
if ! cmp -s $REQ $VENV/requirements.installed; then
  $VENV/bin/pip install -q --disable-pip-version-check -r $REQ
  cp $REQ $VENV/requirements.installed
  echo "installed server/requirements.txt in $VENV"
fi
# Piper's Swedish voice (63 MB), downloaded once.
PIPER=~/.local/share/hallpanel/piper
if [ ! -f $PIPER/sv_SE-nst-medium.onnx ]; then
  mkdir -p $PIPER
  $VENV/bin/python3 -m piper.download_voices --download-dir $PIPER sv_SE-nst-medium
fi
cp ~/hallpanel-live/server/hallpanel.service ~/hallpanel-live/server/hallpanel-apod.service \
   ~/hallpanel-live/server/hallpanel-apod.timer ~/hallpanel-live/server/hallpanel-sl-check.service \
   ~/hallpanel-live/server/hallpanel-earth.service ~/hallpanel-live/server/hallpanel-earth.timer \
   ~/hallpanel-live/server/hallpanel-daily-pictures.service ~/hallpanel-live/server/hallpanel-daily-pictures.timer \
   ~/hallpanel-live/server/hallpanel-sl-check.timer ~/.config/systemd/user/
# The Bluetooth speaker (docs/bluetooth-speaker.md), once Joel has run server/bluetooth/setup.sh with sudo.
SPEAKER_PORT=
if command -v mpd >/dev/null && command -v bluealsa >/dev/null; then
  cp ~/hallpanel-live/server/hallpanel-mpd.service ~/.config/systemd/user/
  SPEAKER_PORT=6601
  if ! grep -q '^JARVIS_SPEAKER_PLAYER=' ~/.config/hallpanel/server.env; then
    echo "JARVIS_SPEAKER_PLAYER=media_player.bluetooth_hogtalare" >> ~/.config/hallpanel/server.env
    echo "turned on the Bluetooth speaker in ~/.config/hallpanel/server.env"
  fi
fi
# Spotify on the JBL (docs/spotify.md), once Joel's Soloist API key is on Jarvis. Its units, the updater timer and
# the PipeWire/WirePlumber configs follow the repository; a restart cuts the music, so only when they changed.
SOLOIST=
if [ -s ~/.config/soloist/api-key ] && [ -x ~/.local/share/soloist/bin/soloist ]; then
  SOLOIST=1
  # The data directory holds Joel's stored Spotify session (Spotify: "restrict access to the data directory"); they
  # were 775 from the umask until 2026-10-30. Private directories cover the files in them without a restart.
  chmod 700 ~/.local/share/soloist ~/.local/share/soloist/data ~/.cache/soloist
  cp ~/hallpanel-live/server/soloist/hallpanel-soloist.service ~/hallpanel-live/server/soloist/hallpanel-soloist-update.service \
     ~/hallpanel-live/server/soloist/hallpanel-soloist-update.timer ~/hallpanel-live/server/soloist/hallpanel-soloist-watch.service \
     ~/hallpanel-live/server/soloist/hallpanel-soloist-watch.timer ~/.config/systemd/user/
  mkdir -p ~/.config/pipewire/pipewire.conf.d ~/.config/wireplumber/wireplumber.conf.d
  cp ~/hallpanel-live/server/soloist/pipewire-jbl.conf ~/.config/pipewire/pipewire.conf.d/90-jbl.conf
  cp ~/hallpanel-live/server/soloist/wireplumber-jarvis.conf ~/.config/wireplumber/wireplumber.conf.d/90-jarvis.conf
  if ! grep -q '^JARVIS_SOLOIST_WS=' ~/.config/hallpanel/server.env; then
    echo "JARVIS_SOLOIST_WS=127.0.0.1:9431" >> ~/.config/hallpanel/server.env
    echo "turned on Spotify through Soloist in ~/.config/hallpanel/server.env"
  fi
fi
systemctl --user daemon-reload
if [ -n "$SOLOIST" ]; then
  systemctl --user enable hallpanel-soloist.service >/dev/null 2>&1
  systemctl --user enable --now hallpanel-soloist-update.timer >/dev/null 2>&1
  # Restarts a Soloist that says "playing" while no sound reaches the JBL (watch.py, docs/spotify.md).
  systemctl --user enable --now hallpanel-soloist-watch.timer >/dev/null 2>&1
  SOLOIST_SUM=$(cat ~/hallpanel-live/server/soloist/hallpanel-soloist.service ~/hallpanel-live/server/soloist/run.sh | sha256sum)
  if [ "$SOLOIST_SUM" != "$(cat ~/.config/hallpanel/soloist.sum 2>/dev/null || true)" ]; then
    systemctl --user restart hallpanel-soloist.service
    echo "$SOLOIST_SUM" > ~/.config/hallpanel/soloist.sum
    echo "restarted hallpanel-soloist (its unit or start script changed)"
  else
    systemctl --user start hallpanel-soloist.service
  fi
fi
# The turntable's 45 rpm pitch filter (server/soloist/jbl45.conf), only once rubberband-ladspa is installed (apt,
# Joel's terminal). It opens no port and does nothing while Spotify plays at 33; restarted when its files change.
if [ -n "$SOLOIST" ] && [ -f /usr/lib/ladspa/ladspa-rubberband.so ]; then
  cp ~/hallpanel-live/server/soloist/hallpanel-jbl45.service ~/.config/systemd/user/
  systemctl --user daemon-reload
  systemctl --user enable hallpanel-jbl45.service >/dev/null 2>&1
  JBL45_SUM=$(cat ~/hallpanel-live/server/soloist/hallpanel-jbl45.service ~/hallpanel-live/server/soloist/jbl45.conf | sha256sum)
  if [ "$JBL45_SUM" != "$(cat ~/.config/hallpanel/jbl45.sum 2>/dev/null || true)" ]; then
    systemctl --user restart hallpanel-jbl45.service
    echo "$JBL45_SUM" > ~/.config/hallpanel/jbl45.sum
    echo "restarted hallpanel-jbl45 (the 45 rpm pitch filter changed)"
  else
    systemctl --user start hallpanel-jbl45.service
  fi
fi
if [ -n "$SPEAKER_PORT" ]; then
  systemctl --user enable hallpanel-mpd.service >/dev/null 2>&1
  # A restart cuts the radio on the speaker: only when MPD's own unit or config changed.
  MPD_SUM=$(cat ~/hallpanel-live/server/hallpanel-mpd.service ~/hallpanel-live/server/bluetooth/mpd.conf | sha256sum)
  if [ "$MPD_SUM" != "$(cat ~/.config/hallpanel/mpd.sum 2>/dev/null || true)" ]; then
    systemctl --user restart hallpanel-mpd.service
    echo "$MPD_SUM" > ~/.config/hallpanel/mpd.sum
    echo "restarted hallpanel-mpd (its unit or config changed)"
  else
    systemctl --user start hallpanel-mpd.service
  fi
fi
systemctl --user enable hallpanel.service >/dev/null 2>&1
systemctl --user enable --now hallpanel-apod.timer >/dev/null 2>&1
systemctl --user enable --now hallpanel-earth.timer >/dev/null 2>&1
systemctl --user enable --now hallpanel-daily-pictures.timer >/dev/null 2>&1
# The panel check pushes "panelen svarar inte": paused while Joel has the panel unplugged on purpose (touch
# ~/.config/hallpanel/sl-check.paused; remove it and enable the timer when the panel is back). Every deploy turned
# it back on (2026-10-28).
if [ -f ~/.config/hallpanel/sl-check.paused ]; then
  systemctl --user disable --now hallpanel-sl-check.timer >/dev/null 2>&1 || true
  echo "the panel's SL check stays paused (~/.config/hallpanel/sl-check.paused)"
else
  systemctl --user enable --now hallpanel-sl-check.timer >/dev/null 2>&1
fi
systemctl --user restart hallpanel-apod.timer
# The images were just copied over, the Van Gogh background with them: put today's NASA picture back.
systemctl --user start hallpanel-apod.service || echo "the NASA background failed, see journalctl --user-unit hallpanel-apod"
# Tavla's Earth view (server/earth.py): skips EPIC's picture and NeoWs when today's are already in.
systemctl --user start hallpanel-earth.service || echo "the Earth view failed, see journalctl --user-unit hallpanel-earth"
# The daily background choices (server/daily_pictures.py): skips a source that already has today's picture.
systemctl --user start hallpanel-daily-pictures.service || echo "the daily pictures failed, see journalctl --user-unit hallpanel-daily-pictures"
systemctl --user restart hallpanel.service
sleep 2
systemctl --user is-active hallpanel.service
if [ "$RESTART_HA" = "--restart-ha" ]; then
  docker exec homeassistant python -m homeassistant --script check_config -c /config >/tmp/ha-check.txt 2>&1 \
    || { echo "Home Assistant config check failed, not restarting:"; tail -20 /tmp/ha-check.txt; exit 1; }
  docker restart homeassistant >/dev/null
  echo "restarted Home Assistant"
  for i in $(seq 1 60); do curl -s -o /dev/null -m 2 http://127.0.0.1:8123/ && break; sleep 2; done
  sleep 5
fi
# Add the integration and the pipeline (does nothing when both are already right).
docker cp ~/hallpanel-live/ha/ha_tool.py homeassistant:/tmp/ha_tool.py
. ~/.config/hallpanel/server.env
printf '{"ha_token": "%s", "jarvis_token": "%s", "speaker_port": "%s"}\n' "$(cat ~/.config/homeassistant/token)" \
  "$HALLPANEL_TOKEN" "$SPEAKER_PORT" \
  | docker exec -i homeassistant python /tmp/ha_tool.py setup
REMOTE
