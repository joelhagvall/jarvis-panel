#!/usr/bin/env python3
"""Tavla's Earth view (Joel, 2026-10-29): NASA EPIC's newest picture of the whole Earth and today's closest
asteroids from NASA NeoWs.

EPIC (epic.gsfc.nasa.gov, no key) photographs the sunlit Earth about 13 times a day from DSCOVR, 1.5 million km
away, and publishes a day or two late. Of the newest day this takes the picture that faces Europe (its centre
longitude nearest 15° E), crops the disc square and makes it RGB565-ready (panel_image.py) as OUT/earth.qoi
and earth.png, EARTH_SIDE px. NeoWs (api.nasa.gov, NASA_API_KEY or DEMO_KEY) lists the asteroids that pass
the Earth today; the three closest go to OUT/earth.json with the picture's time and distance. /panel reads that
file (hallpanel_server.py earth_fields) and the panel downloads the picture once a day, when `version` changes.

DEMO_KEY allowed 10 requests an hour per IP on 2026-10-29, shared with the Mac on the same line: NeoWs is asked
once per Stockholm day, at most MAX_NEO_TRIES times. A failure keeps the last good file; nothing is deleted.
Run by hallpanel-earth.timer every three hours; logs one line per run (journalctl --user-unit hallpanel-earth).

  earth.py [--out DIR] [--cache DIR]
          defaults: --out ~/docker/homeassistant/config/www/hallpanel, --cache ~/.local/share/hallpanel/earth
"""
import argparse
import hashlib
import json
import math
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

AGENT = "hallpanel/1 (Jarvis, a home voice assistant; https://github.com/joelhagvall/jarvis-panel)"
EPIC = "https://epic.gsfc.nasa.gov/api/natural"
EPIC_JPG = "https://epic.gsfc.nasa.gov/archive/natural/{y}/{m}/{d}/jpg/{image}.jpg"
NEO = "https://api.nasa.gov/neo/rest/v1/feed"
EARTH_SIDE = 480                # the panel's picture: the disc fills it, centred on Tavla's black page
FACE_LON = 15.0                 # Sweden's longitude: the picture that shows Europe
MAX_NEO_TRIES = 4               # NeoWs requests per Stockholm day, failures included
TZ = ZoneInfo("Europe/Stockholm")
MONTHS = ["januari", "februari", "mars", "april", "maj", "juni", "juli", "augusti", "september", "oktober",
          "november", "december"]


def fetch(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(20_000_000)


def num(x, decimals=1):
    return f"{x:.{decimals}f}".replace(".", ",")


def facing_europe(entries):
    """The EPIC entry whose centre longitude is nearest Sweden's."""
    def off(entry):
        lon = float(entry["centroid_coordinates"]["lon"])
        return abs((lon - FACE_LON + 180) % 360 - 180)
    return min(entries, key=off)


def earth_line(entry):
    """("Jorden 26 oktober", "NASA EPIC · kl 11:55 · 1,5 miljoner km bort") for the plaque."""
    when = datetime.strptime(entry["date"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).astimezone(TZ)
    p = entry.get("dscovr_j2000_position") or {}
    km = math.sqrt(sum(float(p.get(k, 0)) ** 2 for k in "xyz"))
    away = f" · {num(km / 1e6)} miljoner km bort" if km else ""
    return f"Jorden {when.day} {MONTHS[when.month - 1]}", f"NASA EPIC · kl {when:%H:%M}{away}"


def crop_disc(picture):
    """The Earth's disc, square with a thin black margin, at EARTH_SIDE px."""
    import numpy as np
    from PIL import Image
    im = picture.convert("RGB")
    lit = np.asarray(im).max(axis=2) > 24
    ys, xs = np.nonzero(lit)
    if not len(xs):
        raise ValueError("EPIC picture is black")
    cx, cy = (xs.min() + xs.max()) / 2, (ys.min() + ys.max()) / 2
    half = max(xs.max() - xs.min(), ys.max() - ys.min()) / 2 * 1.03
    box = tuple(round(v) for v in (cx - half, cy - half, cx + half, cy + half))
    return im.crop(box).resize((EARTH_SIDE, EARTH_SIDE), Image.LANCZOS)


def size_text(neo):
    m = neo["estimated_diameter"]["meters"]
    d = (float(m["estimated_diameter_min"]) + float(m["estimated_diameter_max"])) / 2
    return f"~{num(d / 1000)} km" if d >= 1000 else f"~{round(d, -1 if d >= 100 else 0):.0f} m"


def asteroids(feed, day):
    """("N asteroider passerar i dag", [3 rows]) from a NeoWs feed for `day` (YYYY-MM-DD)."""
    rows = []
    for neo in feed.get("near_earth_objects", {}).get(day, []):
        approach = next((a for a in neo.get("close_approach_data", []) if a.get("orbiting_body") == "Earth"), None)
        if approach:
            rows.append((float(approach["miss_distance"]["lunar"]), neo))
    rows.sort(key=lambda r: r[0])
    lines = []
    for lunar, neo in rows[:3]:
        name = neo["name"].strip().strip("()")
        far = num(lunar) if lunar < 10 else f"{lunar:.0f}"
        lines.append(f"{name} · {far} månavstånd · {size_text(neo)}")
    n = len(rows)
    head = f"{n} asteroid passerar i dag" if n == 1 else f"{n} asteroider passerar i dag" if n else ""
    return head, lines


def load(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def write_json(path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    os.replace(tmp, path)


def update_picture(meta, cache, out):
    """EPIC's newest Europe-facing picture into out/earth.qoi; True when a new one was published."""
    entries = json.loads(fetch(EPIC))
    if not entries:
        raise ValueError("EPIC listed no pictures")
    entry = facing_europe(entries)
    if entry["image"] == meta.get("image") and (out / "earth.qoi").is_file():
        return False
    y, m, d = entry["date"][:10].split("-")
    jpg = cache / f"{entry['image']}.jpg"
    if not jpg.is_file():
        jpg.write_bytes(fetch(EPIC_JPG.format(y=y, m=m, d=d, image=entry["image"])))
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import panel_image
    from PIL import Image
    with Image.open(jpg) as im:
        prepared = panel_image.prepare(crop_disc(im))
    from image_sampling import encode_qoi_rgb
    import numpy as np
    for suffix, data in ((".png", None), (".qoi", encode_qoi_rgb(np.asarray(prepared)))):
        tmp = out / f".earth{suffix}.tmp"
        prepared.save(tmp, format="PNG") if data is None else tmp.write_bytes(data)
        os.replace(tmp, out / f"earth{suffix}")
    title, line = earth_line(entry)
    meta.update(image=entry["image"], taken=entry["date"], title=title, line=line)
    for old in sorted(cache.glob("epic_*.jpg"))[:-3]:
        old.unlink()
    return True


def update_neo(meta, now):
    """Today's closest asteroids into meta; False when today's are already there or tries ran out."""
    day = now.astimezone(TZ).date().isoformat()
    if meta.get("neo_day") == day:
        return False
    tries = meta.get("neo_tries", {})
    if tries.get("day") != day:
        tries = {"day": day, "n": 0}
    if tries["n"] >= MAX_NEO_TRIES:
        return False
    tries["n"] += 1
    meta["neo_tries"] = tries
    query = urllib.parse.urlencode({"start_date": day, "end_date": day,
                                    "api_key": os.environ.get("NASA_API_KEY", "DEMO_KEY")})
    head, lines = asteroids(json.loads(fetch(f"{NEO}?{query}")), day)
    meta.update(neo_day=day, neo_head=head, neo=lines)
    return True


def main(argv=None, now=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="~/docker/homeassistant/config/www/hallpanel")
    ap.add_argument("--cache", default="~/.local/share/hallpanel/earth")
    args = ap.parse_args(argv)
    out, cache = Path(args.out).expanduser(), Path(args.cache).expanduser()
    cache.mkdir(parents=True, exist_ok=True)
    if not out.is_dir():
        print(f"earth: {out} is missing", file=sys.stderr)
        return 1
    meta = load(cache / "state.json")
    notes, failed = [], False
    for name, step in (("EPIC", lambda: update_picture(meta, cache, out)),
                       ("NeoWs", lambda: update_neo(meta, now or datetime.now(timezone.utc)))):
        try:
            notes.append(f"{name} {'new' if step() else 'unchanged'}")
        except (OSError, ValueError, KeyError, urllib.error.URLError) as e:
            failed = True
            notes.append(f"{name} failed ({e}); keeping the last")
    write_json(cache / "state.json", meta)
    public = {k: meta.get(k, "") for k in ("title", "line", "neo_head")}
    public["neo"] = meta.get("neo", [])
    public["version"] = hashlib.sha256(meta["image"].encode()).hexdigest()[:16] if meta.get("image") else ""
    write_json(out / "earth.json", public)
    print("earth: " + "; ".join(notes) + (f" -> {meta.get('title')}, {meta.get('neo_head') or 'no asteroids'}"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
