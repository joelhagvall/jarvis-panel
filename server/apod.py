#!/usr/bin/env python3
"""NASA's Astronomy Picture of the Day as the panel's background.

The scheduled timer starts at 00:10 New York and retries unpublished days every 15 minutes.
Once today is handled, later ticks skip the API. It downloads a valid picture,
bakes the glass widgets into it with styles/glass-v2/bake.py, and only when all of that worked puts the
result in OUT/bg.png, the background the panel downloads (Home Assistant's /local/hallpanel/bg.png), and
the picture alone, without glass, in OUT/art.png for the Tavla page. Both go through
server/agents/background.py: when a painting is the background chosen in Home Assistant, that stays instead.
A day whose APOD is a video, or a day NASA does not answer, keeps the last good picture. OUT/apod.json
holds its date, title, credit and NASA's explanation (English, for Tavla's plaque), for the agent and the frame
mode.

NASA moved APOD to science.nasa.gov in September 2026. The new endpoint is wp-json/wp/v2/apod-basic;
the legacy api.nasa.gov/planetary/apod goes offline on 1 December 2026, and its image link already
points at the NASA logo. The free DEMO_KEY is enough for one call a day; NASA_API_KEY overrides it.

Needs numpy and Pillow for the bake (server/requirements.txt, in ~/hallpanel-live/venv on Jarvis).

  apod.py [--out DIR] [--cache DIR] [--date YYYY-MM-DD]
          defaults: --out ~/docker/homeassistant/config/www/hallpanel, --cache ~/.local/share/hallpanel/apod
"""
import argparse
import html
import importlib.util
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent / "agents"))
import background  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
GLASS = Path(os.environ.get("HALLPANEL_GLASS_DIR", ROOT / "styles" / "glass-v2"))
API = "https://science.nasa.gov/wp-json/wp/v2/apod-basic/"
AGENT = "hallpanel/1 (Jarvis, a home voice assistant; https://github.com/joelhagvall/jarvis-panel)"
MIN_SIDE = 800                  # smaller than this is not a picture to put on a 1024x600 screen
NEW_YORK = ZoneInfo('America/New_York')
MAX_DAILY_POLLS = 40            # DEMO_KEY allows 50 API calls/day; leave room for manual work


def new_york_now():
    return datetime.now(NEW_YORK)


def save_poll(cache, state):
    path = cache / 'poll.json'
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(state))
    os.replace(tmp, path)


def begin_poll(cache, now=None):
    """Reserve a bounded API attempt; completed days need no further API requests."""
    now = (now or new_york_now()).astimezone(NEW_YORK)
    if now.hour == 0 and now.minute < 10:
        return None
    try:
        state = json.loads((cache / 'poll.json').read_text())
    except (OSError, ValueError):
        state = {}
    day = now.date().isoformat()
    if not isinstance(state, dict) or state.get('date') != day:
        state = {'date': day, 'attempts': 0, 'complete': False}
    attempts = state.get('attempts', 0)
    if type(attempts) is not int or not 0 <= attempts <= MAX_DAILY_POLLS:
        raise ValueError('Invalid APOD poll budget; refusing an unbounded retry')
    if state.get('complete') is True or attempts >= MAX_DAILY_POLLS:
        return None
    state['attempts'] = attempts + 1
    save_poll(cache, state)  # reserve before HTTP, including failures or process interruption
    return state


def complete_poll(cache, state, result):
    if state is not None:
        save_poll(cache, {**state, 'complete': True, 'result': result})


def fetch(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(40_000_000)


def plain(text):
    return " ".join(html.unescape(re.sub(r"<[^>]+>", "", text or "")).split())


EXPLANATION_MAX = 1200          # Tavla's plaque shows it in 16 px over at most ~20 lines


def explanation(text):
    """NASA's explanation as plain text without its "Explanation:" lead, cut after a whole sentence when long."""
    text = re.sub(r"^Explanation:\s*", "", plain(text))
    # APOD ends with a teaser for the next day ("Tomorrow's picture: What's next?"), not about this picture
    text = re.sub(r"\s*Tomorrow's picture:.*$", "", text)
    if len(text) <= EXPLANATION_MAX:
        return text
    cut = text[:EXPLANATION_MAX]
    end = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "))
    return cut[:end + 1] if end > EXPLANATION_MAX // 2 else cut.rsplit(" ", 1)[0] + " …"


def todays_entry(date=None):
    params = {"api_key": os.environ.get("NASA_API_KEY", "DEMO_KEY")}
    if date:
        params["date"] = date
    data = json.loads(fetch(API + "?" + urllib.parse.urlencode(params)))
    if isinstance(data, list):
        if not data:
            raise ValueError('NASA returned no entries')
        if date:
            return next((entry for entry in data if entry.get('date') == date), data[0])
        return data[0]
    if not isinstance(data, dict):
        raise ValueError('NASA returned an invalid entry')
    return data


def sized(url, side=1600):
    """NASA's image service scales on request; ask for at most `side` px on the long edge."""
    parts = urllib.parse.urlsplit(url)
    query = dict(urllib.parse.parse_qsl(parts.query))
    if "w" in query or "assets.science.nasa.gov/dynamicimage" in url:
        query.update(w=str(side), h=str(side), fit="clip")
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(query)))


def load_bake():
    spec = importlib.util.spec_from_file_location("glass_bake", GLASS / "bake.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="~/docker/homeassistant/config/www/hallpanel")
    ap.add_argument("--cache", default="~/.local/share/hallpanel/apod")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--date", help="YYYY-MM-DD (default: today's APOD)")
    mode.add_argument('--scheduled', action='store_true', help='Early New York day, bounded retries until published')
    args = ap.parse_args()
    out, cache = Path(args.out).expanduser(), Path(args.cache).expanduser()
    cache.mkdir(parents=True, exist_ok=True)
    poll = begin_poll(cache) if args.scheduled else None
    if args.scheduled and poll is None:
        return 0
    requested_date = poll['date'] if poll is not None else args.date
    from PIL import Image

    def keep_last(reason):
        print(f"APOD: {reason}; keeping the last picture", file=sys.stderr)
        last = cache / "latest.json"
        if last.is_file():
            meta = json.loads(last.read_text())
            if (cache / meta["file"]).is_file() and out.is_dir():
                (out / "apod.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
        if not args.scheduled:
            background.publish(out, cache)
        return 0

    try:
        entry = todays_entry(requested_date)
    except (OSError, ValueError, urllib.error.URLError) as e:
        return keep_last(f"NASA did not answer ({e})")
    if requested_date and entry.get('date') != requested_date:
        return keep_last(f"{requested_date} is not published yet (received {entry.get('date')})")
    if entry.get('media_type') == 'video':
        complete_poll(cache, poll, 'video')
        return keep_last(f"{entry.get('date')} is a video, not a picture")
    if entry.get("media_type") != "image" or not (entry.get("hdurl") or entry.get("url")):
        return keep_last(f"{entry.get('date')} is a {entry.get('media_type')}, not a picture")
    day = entry["date"]
    meta = {"date": day, "title": plain(entry.get("title")), "credit": plain(entry.get("credit") or entry.get("copyright")),
            "explanation": explanation(entry.get("explanation")),
            "page": entry.get("permalink"), "file": f"bg_{day}.png", "art": f"art_{day}.png"}
    picture = cache / f"apod_{day}.jpg"
    if not picture.is_file():
        try:
            picture.write_bytes(fetch(sized(entry.get("hdurl") or entry["url"])))
        except (OSError, urllib.error.URLError) as e:
            return keep_last(f"the picture did not download ({e})")
    try:
        with Image.open(picture) as im:
            if max(im.size) < MIN_SIDE:
                picture.unlink()
                return keep_last(f"the picture is only {im.size[0]}x{im.size[1]}")
    except OSError as e:
        picture.unlink(missing_ok=True)
        return keep_last(f"the picture does not open ({e})")
    baked = cache / meta["file"]
    if not baked.is_file():
        tmp = baked.with_suffix(".tmp.png")
        load_bake().bake(str(picture), str(tmp))
        os.replace(tmp, baked)
    art = cache / meta["art"]
    if not art.is_file():
        tmp = art.with_suffix(".tmp.png")
        load_bake().frame(str(picture), str(tmp))
        os.replace(tmp, art)
    (cache / "latest.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    for old in sorted(cache.glob("apod_*.jpg"))[:-7] + sorted(cache.glob("bg_*.png"))[:-7] + sorted(cache.glob("art_*.png"))[:-7]:
        old.unlink()                            # a week of pictures is plenty
    if out.is_dir():
        (out / "apod.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    background.publish(out, cache)                # NASA's picture, or the painting chosen in Home Assistant
    complete_poll(cache, poll, 'image')
    print(f"APOD {day}: {meta['title']} ({meta['credit']}) -> {out / 'bg.png' if out.is_dir() else baked}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
