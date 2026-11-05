#!/bin/sh
# What the server on Jarvis gives the panel right now: GET /panel's fields (the attributes of
# sensor.jarvis_hemmet), read on Jarvis with the server's own token, which is never printed.
# Use it after a deploy to see that a new or changed field arrives, before HA's 15 s read and a flash.
#
#   sh tools/panel_fields.sh                 every field
#   sh tools/panel_fields.sh cal_head house  only these
#
# Read-only. HA's sensor can lag the server by up to 15 s, and more right after an HA restart.
set -eu
HOST=${JARVIS_HOST:-user@jarvis.local}
HERE=$(cd "$(dirname "$0")" && pwd)
"$HERE/jarvis_ssh.sh" "$HOST" "set -a; . ~/.config/hallpanel/server.env; set +a; python3 - $*" <<'EOF'
import json, os, sys, urllib.request
req = urllib.request.Request("http://127.0.0.1:8765/panel", headers={"X-Hallpanel-Token": os.environ["HALLPANEL_TOKEN"]})
fields = json.load(urllib.request.urlopen(req, timeout=10))
keys = sys.argv[1:] or sorted(fields)
print(json.dumps({k: fields.get(k) for k in keys}, ensure_ascii=False, indent=1))
EOF
