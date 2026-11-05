#!/bin/sh
# The root half of Jarvis's Bluetooth speaker (docs/bluetooth-speaker.md). Run once, in Joel's own terminal:
#
#   ssh jarvis 'sudo sh ~/hallpanel-live/server/bluetooth/setup.sh'      (or from an ssh session on Jarvis)
#
# It turns on BlueZ, installs BlueALSA (Bluetooth audio for ALSA) and MPD, and switches off Debian's
# system-wide MPD so only hallpanel-mpd.service (a user service, installed by tools/deploy_jarvis.sh) plays.
# Safe to run again. Nothing here opens a port: MPD listens on 127.0.0.1 only.
set -eu
[ "$(id -u)" = 0 ] || { echo "run it with sudo" >&2; exit 1; }
USER_NAME=${SUDO_USER:-joel}

apt-get install -y --no-install-recommends bluez-alsa-utils mpd
# Debian starts MPD system-wide on port 6600; the speaker's MPD is a user service on 6601.
systemctl disable --now mpd.service mpd.socket 2>/dev/null || true
systemctl enable --now bluetooth.service
systemctl enable --now bluealsa.service
# BlueALSA answers members of the audio group, BlueZ everyone; check the user that runs the services.
id -nG "$USER_NAME" | tr ' ' '\n' | grep -qx audio || usermod -aG audio "$USER_NAME"
bluetoothctl power on >/dev/null
echo "Bluetooth: $(systemctl is-active bluetooth.service), BlueALSA: $(systemctl is-active bluealsa.service)"
echo "Next: tools/deploy_jarvis.sh from the Mac, then pair the speaker (docs/bluetooth-speaker.md)."
