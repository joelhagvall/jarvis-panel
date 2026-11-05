#!/usr/bin/env python3
"""Make the spoken error replies the panel plays from its own flash when a voice turn fails (board_config/voice.py,
on_error), in Jarvis's finished voice: Mattias from Home Assistant Cloud, trimmed, with the AI tone (server/voice.py
speech()). They are on the panel, not fetched per error, because "Nätet är nere" must play without the internet.
16 kHz mono 16-bit, the panel's format. Joel asked for them aloud on 2026-11-03: until then the reason was text only
and a silent wake felt like nothing happened.

  python3 assets/sounds/make_errors.py      renders every file below on Jarvis and writes it next to this file

The texts are ERROR_SOUNDS in board_config/voice.py; change them there and run this again.
"""
import base64
import json
import shlex
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))
from board_config.voice import ERROR_SOUNDS  # noqa: E402

REMOTE = r"""
import base64, json, sys
sys.path.insert(0, "/srv/demo/hallpanel-live/server")
import voice
texts = json.loads(sys.stdin.readline())
out = {}
for name, text in texts.items():
    wav, source = voice.speech(text)
    out[name] = [base64.b64encode(wav).decode(), source]
print(json.dumps(out))
"""


def main():
    texts = {name: text for name, (_, text) in ERROR_SOUNDS.items()}
    cmd = ("set -a; . ~/.config/hallpanel/server.env; set +a; cd ~/hallpanel-live/server && "
           "exec ~/hallpanel-live/venv/bin/python -c " + shlex.quote(REMOTE))
    out = subprocess.run([str(ROOT / "tools" / "jarvis_ssh.sh"), "user@jarvis.local", cmd],
                         input=json.dumps(texts) + "\n", capture_output=True, text=True)
    if out.returncode:
        sys.exit(f"rendering on Jarvis failed: {out.stderr.strip()[-600:]}")
    out = out.stdout
    rendered = json.loads(out.strip().splitlines()[-1])
    if any(source != "cloud" for _, source in rendered.values()):     # Piper's offline voice is not Jarvis's
        sys.exit(f"not Mattias from the cloud: { {n: src for n, (_, src) in rendered.items()} }; try again later")
    for name, (data, _) in rendered.items():
        path = HERE / ERROR_SOUNDS[name][0]
        path.write_bytes(base64.b64decode(data))
        print(f"{path.name}: {texts[name]!r}, {path.stat().st_size} bytes")


if __name__ == "__main__":
    main()
