#!/usr/bin/env python3
"""A new background every day from free picture APIs, without a library on Jarvis (Joel, 2026-11-02: "fler
bakgrundsbilder utan att ta utrymme, via apier"). Each source is a choice in select.jarvis_bakgrund
(server/agents/background.py DAILY):

  Dagens målning  a public-domain (CC0) painting from the Cleveland Museum of Art's open access API
                  (openaccess-api.clevelandart.org, no key), a different one each Stockholm day, landscape only,
                  never one of the last RECENT
  Dagens foto     Wikimedia's featured picture of the day (api.wikimedia.org feed, no key; Commons licences,
                  credited on the plaque); a portrait day keeps the last landscape picture
  Bing            Bing's photo of the day (bing.com/HPImageArchive, 1920x1080, Microsoft's wallpaper)
  NASA Jordbild   NASA Earth Observatory's image of the day (its RSS feed, weekdays; Joel asked 2026-11-02),
                  satellite pictures, so a squarer one is cropped too (min_ratio 1.0)

Every source is a Provider in PROVIDERS: its find() returns the newest entry, the rest is shared. Each keeps
only its latest picture, scaled to at most KEEP_WIDTH px wide, in DIR/<slug>/ with a meta.json (title, credit,
the plaque's text, the source's own day). A source that has today's picture is not asked again; one that fails
or has nothing new keeps the last picture. A picture that looks like one any source kept before (a 64-bit
difference hash in DIR/seen.json, the last SEEN) is not kept again. When the source, or Växlande (the daily
rotation, background.shown), is the chosen background, the picture is published at once (background.publish
bakes it like an uploaded picture).
The Art Institute of Chicago was tried first: its image server answered 403 to this honest User-Agent.

Run by hallpanel-daily-pictures.timer every three hours, and once by tools/deploy_jarvis.sh.
Log: journalctl --user-unit hallpanel-daily-pictures, one line per source and run.

  daily_pictures.py [--dir DIR] [--only SLUG]
          default --dir ~/.local/share/hallpanel/daily (JARVIS_DAILY_PICTURES)
"""
import argparse
import html
import io
import json
import os
import random
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent / "agents"))
import background  # noqa: E402

AGENT = background.AGENT
TZ = ZoneInfo("Europe/Stockholm")
MAX_DOWNLOAD = 25 * 1024 * 1024
MIN_WIDTH = 1200                # narrower than this is blurry on the 1024x600 screen after the glass crop
MIN_RATIO = 1.3                 # width/height; the screen is 1.71, a squarer picture loses too much
KEEP_WIDTH = 1920               # the kept picture; bake.py crops it to 1024x600
RECENT = 120                    # Dagens målning does not repeat one of its last RECENT paintings
TEXT_MAX = 1200                 # Tavla's plaque shows at most ~20 lines of 16 px

CLEVELAND = "https://openaccess-api.clevelandart.org/api/artworks/"
CLEVELAND_DEPARTMENTS = ("European Painting and Sculpture", "American Painting and Sculpture",
                         "Modern European Painting and Sculpture")
WIKIMEDIA = "https://api.wikimedia.org/feed/v1/wikipedia/en/featured/{y}/{m:02d}/{d:02d}"
COMMONS = "https://commons.wikimedia.org/w/api.php"
BING = "https://www.bing.com/HPImageArchive.aspx?format=js&idx=0&n=1&mkt=sv-SE"
EARTH_FEED = "https://science.nasa.gov/feed/earth-observatory/image-of-the-day/"
SEEN, SAME = 500, 6             # pictures remembered for the duplicate check; hash bits that may differ


def fetch(url, data=None, timeout=30, limit=MAX_DOWNLOAD):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read(limit + 1)
    if len(body) > limit:
        raise ValueError(f"more than {limit} bytes")
    return body


def fetch_json(url):
    return json.loads(fetch(url, limit=5_000_000))


def plain(text, limit=TEXT_MAX):
    """Plain text without HTML, cut after a whole sentence when long."""
    text = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", str(text or ""))).split())
    text = re.sub(r"\s+([.,;:!?])", r"\1", text)
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "))
    return cut[:end + 1] if end > limit // 2 else cut.rsplit(" ", 1)[0] + " …"


def landscape(width, height, ratio=MIN_RATIO):
    return int(width) >= MIN_WIDTH and int(width) >= ratio * int(height)


# Each source returns (day, entry) for its newest picture: day is the source's own date (YYYY-MM-DD), entry is
# None when that day has no usable picture, else {id, urls (tried in order), title, credit, text, page}.

def cleveland(today, recent):
    """A painting chosen by the Stockholm day, landscape and not one of the recent ones."""
    rng = random.Random(f"hallpanel-painting-{today}")
    departments = list(CLEVELAND_DEPARTMENTS)
    rng.shuffle(departments)
    fields = "id,title,creators,creation_date,images,wall_description,tombstone,url"
    for department in departments:
        query = {"cc0": 1, "type": "Painting", "has_image": 1, "department": department, "limit": 1, "fields": "id"}
        total = int(fetch_json(CLEVELAND + "?" + urllib.parse.urlencode(query))["info"]["total"])
        if not total:
            continue
        query.update(limit=50, skip=rng.randrange(max(1, total - 50)), fields=fields)
        for art in fetch_json(CLEVELAND + "?" + urllib.parse.urlencode(query))["data"]:
            image = (art.get("images") or {}).get("print") or {}
            if not image.get("url") or str(art.get("id")) in recent:
                continue
            if not landscape(image.get("width") or 0, image.get("height") or 0):
                continue
            creators = art.get("creators") or [{}]
            maker = str(creators[0].get("description") or "").split(" (")[0] or "Okänd konstnär"
            made = str(art.get("creation_date") or "")
            return today, {"id": str(art["id"]), "urls": [image["url"]], "title": plain(art.get("title"), 200),
                           "credit": f"{maker}{', ' + made if made else ''} · Cleveland Museum of Art",
                           "text": plain(art.get("wall_description") or art.get("tombstone")),
                           "page": art.get("url") or ""}
    return today, None


def commons_file(title):
    """(thumbnail url at KEEP_WIDTH, width, height) of a Commons file from its imageinfo."""
    query = {"action": "query", "format": "json", "titles": title, "prop": "imageinfo",
             "iiprop": "url|size|mime", "iiurlwidth": KEEP_WIDTH}
    page = next(iter(fetch_json(COMMONS + "?" + urllib.parse.urlencode(query))["query"]["pages"].values()))
    info = page["imageinfo"][0]
    if info.get("mime") not in ("image/jpeg", "image/png", "image/webp"):
        return None, 0, 0
    return info.get("thumburl") or info["url"], int(info["width"]), int(info["height"])


def wikimedia(today, recent):
    """Wikimedia's featured picture for today's UTC date (the feed's day)."""
    day = datetime.strptime(today, "%Y-%m-%d").date()
    feed = fetch_json(WIKIMEDIA.format(y=day.year, m=day.month, d=day.day))
    image = feed.get("image") or {}
    title = str(image.get("title") or "")
    if not title:
        return day.isoformat(), None
    url, width, height = commons_file(title)
    if not url or not landscape(width, height):
        return day.isoformat(), None
    artist = plain((image.get("artist") or {}).get("text"), 80)
    licence = str((image.get("license") or {}).get("type") or "")
    credit = " · ".join(part for part in (artist, licence, "Wikimedia Commons") if part)
    name = re.sub(r"^File:|\.[A-Za-z0-9]+$", "", title).replace("_", " ")
    description = plain((image.get("description") or {}).get("text"))
    return day.isoformat(), {"id": title, "urls": [url], "title": plain(name, 120), "credit": credit,
                             "text": description, "page": image.get("file_page") or ""}


def bing(today, recent):
    """Bing's photo of the day; its title and the photographer from its copyright line."""
    image = (fetch_json(BING).get("images") or [{}])[0]
    start = str(image.get("startdate") or "")
    if not re.fullmatch(r"\d{8}", start) or not image.get("url"):
        raise ValueError("Bing answered without a picture")
    day = f"{start[:4]}-{start[4:6]}-{start[6:]}"
    line = str(image.get("copyright") or "")
    match = re.match(r"(.*?)\s*\((©[^)]*)\)\s*$", line)
    about, owner = (match.group(1), match.group(2)) if match else (line, "")
    return day, {"id": str(image.get("hsh") or image["url"]), "urls": [urllib.parse.urljoin("https://www.bing.com", image["url"])],
                 # Bing's own "title" was just "Info" on 2026-11-02; the copyright line says what the photo is
                 "title": plain(about, 120), "credit": " · ".join(part for part in (owner, "Bing") if part),
                 "text": "", "page": image.get("copyrightlink") or ""}


def _tag(item, name):
    found = re.search(rf"<{name}>(.*?)</{name}>", item, re.S)
    return re.sub(r"^<!\[CDATA\[|\]\]>$", "", found.group(1).strip()) if found else ""


def earth_observatory(today, recent):
    """The newest Earth Observatory post in NASA's feed (the feed mixes in Photojournal posts; its XML had a
    namespace typo on 2026-11-02, so it is read with patterns, not an XML parser). Its pictures are tried in
    the post's order at KEEP_WIDTH."""
    feed = fetch(EARTH_FEED, limit=5_000_000).decode("utf-8", "replace")
    for item in re.findall(r"<item>(.*?)</item>", feed, re.S):
        link = _tag(item, "link")
        if "/earth-observatory/" not in link:
            continue
        day = parsedate_to_datetime(_tag(item, "pubDate")).astimezone(TZ).date().isoformat()
        urls = []
        for src in re.findall(r'src="(https://assets\.science\.nasa\.gov/dynamicimage/[^"?]+\.(?:jpe?g|png))', item):
            url = urllib.parse.quote(html.unescape(src), safe=":/%") + f"?w={KEEP_WIDTH}&h={KEEP_WIDTH}&fit=clip"
            if url not in urls:
                urls.append(url)
        about = re.sub(r"\s*The post .*$", "", plain(_tag(item, "description")))
        return day, ({"id": link, "urls": urls[:4], "title": plain(_tag(item, "title"), 120),
                      "credit": "NASA Earth Observatory", "text": about, "page": link} if urls else None)
    raise ValueError("no Earth Observatory post in the feed")


@dataclass(frozen=True)
class Provider:
    """One daily source: its find(today, recent ids), how it counts today, and how square a picture may be."""
    find: Callable
    zone: object = TZ               # Wikimedia's feed counts UTC days
    min_ratio: float = MIN_RATIO

    def today(self, now):
        return now.astimezone(self.zone).date().isoformat()


# slug (background.DAILY's folder) -> Provider
PROVIDERS = {
    "painting": Provider(cleveland),
    "photo": Provider(wikimedia, zone=timezone.utc),
    "bing": Provider(bing),
    "earth": Provider(earth_observatory, min_ratio=1.0),
}
NAMES = {slug: name for name, slug in background.DAILY.items()}


def load(path):
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_json(path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    os.replace(tmp, path)


def difference_hash(picture):
    """64-bit dHash: survives other sizes and recompression, so the same picture from two sources matches."""
    small = picture.convert("L").resize((9, 8)).tobytes()      # 72 grey bytes, row by row
    return sum(1 << i for i, (a, b) in enumerate((small[r * 9 + c], small[r * 9 + c + 1])
                                                  for r in range(8) for c in range(8)) if a > b)


def keep_picture(data, target, ratio=MIN_RATIO):
    """Check that data is a landscape picture and keep it at most KEEP_WIDTH wide as a JPEG; returns its hash."""
    from PIL import Image
    with Image.open(io.BytesIO(data)) as im:
        im.load()
        if not landscape(*im.size, ratio):
            raise ValueError(f"the picture is {im.size[0]}x{im.size[1]}, not a wide enough landscape")
        picture = im.convert("RGB")
    if picture.width > KEEP_WIDTH:
        picture = picture.resize((KEEP_WIDTH, round(picture.height * KEEP_WIDTH / picture.width)), Image.LANCZOS)
    tmp = target.with_suffix(".tmp.jpg")
    picture.save(tmp, "JPEG", quality=90)
    os.replace(tmp, target)
    return difference_hash(picture)


def update(slug, root, now):
    """Fetch the source's newest picture when it has not got today's; returns a one-line log."""
    provider, folder = PROVIDERS[slug], root / slug
    today = provider.today(now)
    folder.mkdir(parents=True, exist_ok=True)
    meta = load(folder / "meta.json")
    if meta.get("checked") == today:
        return "today's is in"
    day, entry = provider.find(today, set(meta.get("recent") or []))
    if day < today:
        return f"nothing new yet (newest {day})"
    if entry is None:
        write_json(folder / "meta.json", {**meta, "checked": day})
        return f"{day} has no landscape picture; keeping the last"
    if entry["id"] == meta.get("id") and (folder / str(meta.get("file"))).is_file():
        write_json(folder / "meta.json", {**meta, "checked": day})
        return f"{day} is the picture already in"
    name, problems = f"{day}.jpg", []
    candidate = folder / f".new-{name}"
    for url in entry["urls"]:
        try:
            picture_hash = keep_picture(fetch(url, timeout=60), candidate, provider.min_ratio)
            break
        except ValueError as e:                 # not a picture, or not landscape: try the post's next one
            problems.append(str(e))
    else:
        write_json(folder / "meta.json", {**meta, "checked": day})
        return f"{day} has no landscape picture ({'; '.join(problems)}); keeping the last"
    seen = load(root / "seen.json").get("pictures") or []
    twin = next((s for s in seen if bin(s["hash"] ^ picture_hash).count("1") <= SAME and s["id"] != f"{slug}:{entry['id']}"), None)
    recent = [entry["id"], *[r for r in meta.get("recent") or [] if r != entry["id"]]][:RECENT]
    if twin:
        candidate.unlink()
        write_json(folder / "meta.json", {**meta, "checked": day, "recent": recent})
        return f"{day}: {entry['title']} looks like {twin['id']}, shown before; keeping the last"
    os.replace(candidate, folder / name)
    write_json(root / "seen.json", {"pictures": [{"hash": picture_hash, "id": f"{slug}:{entry['id']}"}, *seen][:SEEN]})
    write_json(folder / "meta.json", {**entry, "day": day, "checked": day, "file": name, "recent": recent})
    for old in folder.glob("*.jpg"):
        if old.name != name:
            old.unlink()                        # only the latest picture is kept
    if background.current() == NAMES[slug]:
        background.publish()
        return f"{day}: {entry['title']} ({entry['credit']}), published"
    return f"{day}: {entry['title']} ({entry['credit']})"


def main(argv=None, now=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=str(background.DAILY_DIR))
    ap.add_argument("--only", choices=sorted(PROVIDERS))
    args = ap.parse_args(argv)
    root, now = Path(args.dir).expanduser(), now or datetime.now(timezone.utc)
    slugs = [args.only] if args.only else list(PROVIDERS)
    failed = 0
    for slug in slugs:
        try:
            line = update(slug, root, now)
        except (OSError, ValueError, KeyError, IndexError, StopIteration, urllib.error.URLError) as e:
            failed += 1
            line = f"failed, keeping the last picture ({type(e).__name__}: {e})"
        print(f"{NAMES[slug]}: {line}", flush=True)
    if background.current() == background.ROTATION:
        # a new day turns the rotation even when no source changed; an unchanged pick republishes nothing new
        today, before = now.astimezone(TZ).date().isoformat(), background.version()
        pick = background.shown(today)
        background.publish(today=today)
        print(f"{background.ROTATION}: {pick}" + (", published" if background.version() != before else ""), flush=True)
    return 1 if failed == len(slugs) else 0


if __name__ == "__main__":
    sys.exit(main())
