#!/usr/bin/env python3
"""Every background choice for the simulator's Inställningar list, without touching Joel's choice on Jarvis.

Copies Jarvis's panel folder (www/hallpanel), NASA's cache and the daily pictures (server/daily_pictures.py)
over tools/jarvis_ssh.sh, read only, then publishes each choice with server/agents/background.py into its own
folder under OUT/www (served as the simulator's --remote): the Jarvis files as symlinks, bg/art/voice_bg
(.png and .qoi) as real files. OUT/www/choices.json lists them; styles/glass-v2/jarvis_orb.py's sim_choices()
turns that into the Bakgrund list, and picking one downloads that folder's pictures. Växlande and uploaded pictures are left out (no rotation without days; the
uploads stay on Jarvis). Used by `tools/sim.sh --backgrounds`, which also serves OUT/www on 127.0.0.1.

  tools/sim_backgrounds.py OUT [--host user@jarvis.local]
"""
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server" / "agents"))
import background  # noqa: E402

FOLDERS = {"www": "~/docker/homeassistant/config/www/hallpanel", "apod": "~/.local/share/hallpanel/apod",
           "daily": "~/.local/share/hallpanel/daily"}
PICKS = 8                       # choice_list::MAX / gen.PICKS: the panel's list holds at most 8


def mirror(host, remote, target):
    """Copy a folder from Jarvis with tar over the shared ssh transport; nothing on Jarvis changes."""
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    ssh = subprocess.Popen([str(ROOT / "tools/jarvis_ssh.sh"), host, f"tar -C {remote} -cf - ."], stdout=subprocess.PIPE)
    subprocess.run(["tar", "-xf", "-", "-C", str(target)], stdin=ssh.stdout, check=True)
    if ssh.wait():
        raise SystemExit(f"copying {remote} from {host} failed")


def folder_for(name, www):
    """A folder that serves Jarvis's files, with its own background files once published."""
    slug = "choice-" + "".join(ch if ch.isascii() and ch.isalnum() else "-" for ch in name.lower()).strip("-")
    folder = www / slug
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir()
    for f in www.iterdir():
        if f.is_file():
            (folder / f.name).symlink_to(f)
    return slug, folder


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--host", default="user@jarvis.local")
    args = ap.parse_args(argv)
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    for key, remote in FOLDERS.items():
        mirror(args.host, remote, out / key)
    www = out / "www"
    background.STATE, background.CUSTOM = out / "state.json", out / "custom"
    background.APOD_CACHE, background.DAILY_DIR = out / "apod", out / "daily"
    background.UPLOADS = out / "no-uploads"
    choices = [{"name": "Som på Jarvis", "dir": "", "title": "", "credit": ""}]
    for name in [n for n in background.choices() if n != background.ROTATION][:PICKS - 1]:
        slug, background.OUT = folder_for(name, www)
        background.choose(name)
        title, credit = background.art(www)
        choices.append({"name": name, "dir": slug, "title": title, "credit": credit})
        print(f"{name}: {title} ({credit})", flush=True)
    (www / "choices.json").write_text(json.dumps(choices, ensure_ascii=False, indent=1))
    print(f"{len(choices)} choices in {www}/choices.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
