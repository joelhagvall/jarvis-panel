#!/usr/bin/env python3
"""The panel's background, chosen in Home Assistant (select.jarvis_bakgrund, from the jarvis_agent integration):
NASA's picture of the day, one of the paintings, a new picture every day from a free API, a picture Joel
uploaded, or one from a link. Standard library
only; the glass is baked by styles/glass-v2/bake.py in a separate Python (numpy and Pillow), so the server
itself stays small. Glass geometry is bounded to each widget; cold-pipeline CPU and peak-memory evidence
is in docs/baseline/image-experiment-2026-10-25.md.

  Uploaded   pictures in Home Assistant's media folder "hallpanel" (Media in the side menu; it is
             /config/hallpanel-bilder, JARVIS_UPLOADS), each one a choice named after its file
  Egen bild  the picture behind a link set in text.jarvis_bakgrund_lank (from_link): downloaded (at most
             25 MB), checked to be a picture, kept in ~/.local/share/hallpanel/custom (JARVIS_CUSTOM_BG)
  Dagens målning, Dagens foto, Bing, NASA Jordbild
             a new picture every day from server/daily_pictures.py (Joel, 2026-11-02: more backgrounds without
             storage): a public-domain painting from the Cleveland Museum of Art, Wikimedia Commons' picture of
             the day, Bing's photo of the day and NASA Earth Observatory's image of the day. Each keeps only its
             latest picture in ~/.local/share/hallpanel/daily/<slug> (JARVIS_DAILY_PICTURES); a choice appears
             once it has one.
  Växlande   another source every Stockholm day: NASA and the daily sources in turn, skipping a picture the
             rotation has already shown (shown() resolves it; the day's pick is kept in background.json)
  All of these are cropped to 1024x600 like the rest, baked once and kept by their content's hash.

  current()          the choice, kept in ~/.local/share/hallpanel/background.json (JARVIS_BACKGROUND_FILE); NASA
  choices()          NASA, the paintings, the uploaded pictures and Egen bild once there is a link
  choose(name)       keep a new choice and publish it at once
  from_link(url)     download a picture, make it Egen bild and publish it
  version()          changes every time another picture is published; the panel fetches anew when it does
  publish()          put the chosen picture where the panel downloads it: bg.png (with the glass) and art.png
                     (the picture alone, for Tavla) in Home Assistant's www/hallpanel. NASA's come from
                     server/apod.py's cache (its latest.json), the paintings from styles/glass-v2/assets.
  art()              (title, credit) of what the panel shows, for Tavla's plaque and for the agent
  art_text()         NASA's English explanation, for Tavla's plaque (a tap opens it)
  earth()            Tavla's Earth view: server/earth.py's EPIC picture line and today's closest asteroids

server/apod.py calls publish() every morning after it has fetched NASA's picture, so a painting stays when
it is the choice, and tools/deploy_jarvis.sh's copy of the Van Gogh images is put right again.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import unicodedata
from datetime import datetime
from zoneinfo import ZoneInfo
import urllib.parse
import urllib.request
from pathlib import Path

NASA = "NASA"
# name in Home Assistant -> (file stem in styles/glass-v2/assets, title, credit)
PAINTINGS = {"Stjärnenatten": ("starry", "Stjärnenatten", "Vincent van Gogh, 1889"),
             "Mandelblom": ("almond", "Mandelblom", "Vincent van Gogh, 1890")}
LINK = "Egen bild"
# name in Home Assistant -> folder in DAILY_DIR (server/daily_pictures.py fills it, one picture a day)
DAILY = {"Dagens målning": "painting", "Dagens foto": "photo", "Bing": "bing", "NASA Jordbild": "earth"}
ROTATION = "Växlande"
ROTATION_SEEN = 60                     # the rotation does not show one of its last ROTATION_SEEN pictures again
CHOICES = [NASA, *PAINTINGS]           # the fixed ones; choices() adds the daily ones, uploads and Egen bild
PICTURES = {".jpg", ".jpeg", ".png", ".webp"}
MAX_DOWNLOAD, KEEP_BAKED = 25 * 1024 * 1024, 40
STATE = Path(os.environ.get("JARVIS_BACKGROUND_FILE", "~/.local/share/hallpanel/background.json")).expanduser()
OUT = Path(os.environ.get("JARVIS_PANEL_WWW", "~/docker/homeassistant/config/www/hallpanel")).expanduser()
APOD_CACHE = Path(os.environ.get("JARVIS_APOD_CACHE", "~/.local/share/hallpanel/apod")).expanduser()
GLASS = Path(__file__).resolve().parents[2] / "styles" / "glass-v2"
ASSETS = Path(os.environ.get("JARVIS_PAINTINGS", GLASS / "assets"))
UPLOADS = Path(os.environ.get("JARVIS_UPLOADS", "~/docker/homeassistant/config/hallpanel-bilder")).expanduser()
CUSTOM = Path(os.environ.get("JARVIS_CUSTOM_BG", "~/.local/share/hallpanel/custom")).expanduser()
DAILY_DIR = Path(os.environ.get("JARVIS_DAILY_PICTURES", "~/.local/share/hallpanel/daily")).expanduser()
AGENT = "hallpanel/1 (Jarvis, a home voice assistant; https://github.com/joelhagvall/jarvis-panel)"


def _state():
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {}


def _save(**changes):
    state = {**_state(), **changes}
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, ensure_ascii=False))


def uploads():
    """{name: path} of the pictures in the upload folder, by file name without its ending."""
    try:
        files = sorted(f for f in UPLOADS.iterdir() if f.is_file() and f.suffix.lower() in PICTURES)
    except OSError:
        return {}
    return {f.stem: f for f in files if f.stem not in CHOICES and f.stem not in DAILY and f.stem not in (LINK, ROTATION)}


def daily(name):
    """The meta.json of a daily source's latest picture (title, credit, text, file), or None before its first."""
    folder = DAILY_DIR / DAILY[name]
    try:
        meta = json.loads((folder / "meta.json").read_text())
    except (OSError, ValueError):
        return None
    return meta if isinstance(meta, dict) and meta.get("file") and (folder / meta["file"]).is_file() else None


def choices():
    """In the panel's order: it lists the first 8 (PANEL_ENTRIES in the HA select), so Växlande comes early."""
    link = _state().get("link")
    days = [name for name in DAILY if daily(name)]
    return [NASA, *([ROTATION] if days else []), *PAINTINGS, *days, *uploads(),
            *([LINK] if link and Path(link).is_file() else [])]


def _picture_id(name, cache=None):
    """What a rotation source shows now, to tell a new picture from one already shown; None when it has none."""
    if name == NASA:
        try:
            return "NASA:" + json.loads(((Path(cache) if cache else APOD_CACHE) / "latest.json").read_text())["date"]
        except (OSError, ValueError, KeyError):
            return None
    meta = daily(name)
    return f"{name}:{meta.get('id') or meta.get('day')}" if meta else None


def shown(today=None, cache=None):
    """The source Växlande shows today: NASA and the daily sources in turn by the day's number, skipping a
    picture it has shown before. The pick is kept for the day, so a source that gets a new picture later in
    the day does not change it."""
    today = today or datetime.now(ZoneInfo("Europe/Stockholm")).date().isoformat()
    state = _state()
    if state.get("rotation_day") == today and _picture_id(state.get("rotation_pick"), cache):
        return state["rotation_pick"]
    pool = [name for name in (NASA, *DAILY) if _picture_id(name, cache)]
    if not pool:
        return NASA
    seen = list(state.get("rotation_seen") or [])
    start = datetime.strptime(today, "%Y-%m-%d").toordinal() % len(pool)
    turn = pool[start:] + pool[:start]
    pick = next((name for name in turn if _picture_id(name, cache) not in seen), turn[0])
    picture = _picture_id(pick, cache)
    _save(rotation_day=today, rotation_pick=pick, rotation_seen=[picture, *[s for s in seen if s != picture]][:ROTATION_SEEN])
    return pick


def current():
    choice = _state().get("choice")
    return choice if choice in choices() else NASA


def choose(name):
    """Keep name as the choice and publish it. Raises ValueError for a name that is not a choice."""
    if name not in choices():
        raise ValueError(f"no background called {name!r}; choose one of {', '.join(choices())}")
    _save(choice=name)
    return publish()


def from_link(url):
    """Download the picture at url, make it Egen bild and publish it. Raises ValueError with a Swedish reason."""
    url = str(url or "").strip()
    if urllib.parse.urlparse(url).scheme not in ("http", "https"):
        raise ValueError("Länken ska börja med https://.")
    req = urllib.request.Request(url, headers={"User-Agent": AGENT, "Accept": "image/*"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data = r.read(MAX_DOWNLOAD + 1)
    except OSError as e:
        raise ValueError(f"Bilden gick inte att hämta ({type(e).__name__}).") from e
    if len(data) > MAX_DOWNLOAD:
        raise ValueError("Bilden är större än 25 MB.")
    if not _is_picture(data):
        raise ValueError("Länken pekar inte på en bild (jpg, png eller webp).")
    CUSTOM.mkdir(parents=True, exist_ok=True)
    picture = CUSTOM / f"link_{hashlib.sha1(data).hexdigest()[:12]}"
    picture.write_bytes(data)
    _save(link=str(picture), link_url=url)
    return choose(LINK)


def _is_picture(data):
    return data[:3] == b"\xff\xd8\xff" or data[:8] == b"\x89PNG\r\n\x1a\n" or (data[:4] == b"RIFF" and data[8:12] == b"WEBP")


def baked(picture):
    """(bg, art) for an uploaded or linked picture: baked once by bake.py in a separate Python, then kept by the
    picture's hash. Raises ValueError when the picture cannot be baked."""
    key = hashlib.sha1(Path(picture).read_bytes()).hexdigest()[:12]
    bg, art = CUSTOM / f"bg_{key}.png", CUSTOM / f"art_{key}.png"
    if not (bg.is_file() and art.is_file()):
        CUSTOM.mkdir(parents=True, exist_ok=True)
        code = ("import sys, bake\nbake.bake(sys.argv[1], sys.argv[2])\nbake.frame(sys.argv[1], sys.argv[3])")
        done = subprocess.run([sys.executable, "-c", code, str(picture), str(bg) + ".tmp.png", str(art) + ".tmp.png"],
                              cwd=GLASS, capture_output=True, text=True, timeout=120)
        if done.returncode != 0:
            raise ValueError(f"Bilden gick inte att göra till bakgrund: {done.stderr.strip().splitlines()[-1:]}")
        os.replace(str(bg) + ".tmp.png", bg)
        os.replace(str(art) + ".tmp.png", art)
        for old in sorted(CUSTOM.glob("*_*.png"), key=lambda f: f.stat().st_mtime)[:-KEEP_BAKED]:
            old.unlink()                            # the oldest bakes go; a picture chosen again is baked anew
    return bg, art


def version():
    """Changes every time another picture is published (the panel's cue to fetch it again)."""
    return str(_state().get("version", ""))


def sources(choice=None, cache=None):
    """(bg, art) source files for the choice; for NASA from the APOD cache, (None, None) before its first day."""
    choice, cache = choice or current(), Path(cache) if cache else APOD_CACHE
    if choice == ROTATION:
        choice = shown(cache=cache)
    if choice in PAINTINGS:
        stem = PAINTINGS[choice][0]
        return ASSETS / f"bg_{stem}.png", ASSETS / f"art_{stem}.png"
    if choice == LINK:
        return baked(_state()["link"])
    if choice in DAILY:
        meta = daily(choice)
        return baked(DAILY_DIR / DAILY[choice] / meta["file"]) if meta else (None, None)
    if choice in (up := uploads()):
        return baked(up[choice])
    try:
        meta = json.loads((cache / "latest.json").read_text())
    except (OSError, ValueError):
        return None, None
    return cache / meta["file"], (cache / meta["art"]) if meta.get("art") else None


def publish(out=None, cache=None, today=None):
    """Copy the chosen picture to out/bg.png and out/art.png; returns the choice. Nothing is copied when a
    file is missing, so the panel keeps what it has. today (YYYY-MM-DD) is the day Växlande turns to."""
    out = Path(out) if out else OUT
    choice = current()
    if not out.is_dir():
        return choice
    resolved = shown(today, cache) if choice == ROTATION else choice
    files = sources(resolved, cache)
    voice = voice_backdrop(files[1]) if files[1] is not None and files[1].is_file() else None
    prepared = [panel_image(src) if src is not None and src.is_file() else None for src in (*files, voice)]
    for src, name in zip(prepared, ("bg.png", "art.png", "voice_bg.png")):
        if src is not None:
            for suffix in ('.png', '.qoi'):
                target = Path(name).with_suffix(suffix).name
                tmp = out / f".{target}.tmp"
                shutil.copyfile(src.with_suffix(suffix), tmp)
                os.replace(tmp, out / target)
    if files[0] is not None:
        # bg, Tavla and the voice backdrop have independent caches. A repaired/replaced art image
        # must invalidate the panel's download chain even when the baked dashboard did not change.
        identity = "\n".join(src.name if src is not None else "-" for src in prepared)
        revision = hashlib.sha256(identity.encode()).hexdigest()[:24]
        _save(version=f"{choice}:{revision}:images-v4", **({"rotation_shown": resolved} if choice == ROTATION else {}))
    return choice


def panel_image(picture):
    """Cache device-ready RGB565 dithering for every source, including old baked images."""
    script = Path(__file__).resolve().parents[1] / 'panel_image.py'
    key = hashlib.sha256(script.read_bytes() + Path(picture).read_bytes()).hexdigest()[:16]
    result = CUSTOM / f'rgb565_{key}.png'
    if result.is_file() and result.with_suffix('.qoi').is_file():
        return result
    CUSTOM.mkdir(parents=True, exist_ok=True)
    tmp = result.with_suffix('.tmp.png')
    done = subprocess.run([sys.executable, str(script), str(picture), str(tmp)],
                          capture_output=True, text=True, timeout=30)
    if done.returncode:
        raise ValueError('Panelbilden kunde inte förberedas för RGB565.')
    os.replace(tmp, result)
    os.replace(tmp.with_suffix('.qoi'), result.with_suffix('.qoi'))
    for old in sorted(CUSTOM.glob('rgb565_*.png'), key=lambda path: path.stat().st_mtime)[:-KEEP_BAKED * 3]:
        old.unlink()
        old.with_suffix('.qoi').unlink(missing_ok=True)
    return result


def voice_backdrop(picture):
    """Cache the lightly frosted voice background outside the small server process."""
    key = hashlib.sha1(Path(picture).read_bytes()).hexdigest()[:12]
    result = CUSTOM / f"voice_v3_{key}.png"
    if result.is_file():
        return result
    CUSTOM.mkdir(parents=True, exist_ok=True)
    tmp = result.with_suffix('.tmp.png')
    done = subprocess.run([sys.executable, str(Path(__file__).resolve().parents[1] / 'voice_background.py'),
                           str(picture), str(tmp)], capture_output=True, text=True, timeout=30)
    if done.returncode:
        raise ValueError('Röstbakgrunden kunde inte förberedas.')
    os.replace(tmp, result)
    for old in sorted(CUSTOM.glob('voice_v*_*.png'), key=lambda path: path.stat().st_mtime)[:-KEEP_BAKED]:
        old.unlink()
    return result


# The characters Tavla's plaque fonts have (styles/glass-v2/gen.py PLAQUE_GLYPHS; tests keep them equal).
PLAQUE_GLYPHS = frozenset([chr(c) for c in range(0x20, 0x7F)] + list("åäöÅÄÖ°µ·–³…→éèüÜæøÆØ’‘”“—€")
                          + [chr(c) for c in range(0xA1, 0x180) if c not in (0xAD, 0x149)])


def plaque_text(text):
    """text the plaque can draw: a letter its fonts lack loses its accent (ő -> o), anything else
    becomes "?", so the panel never shows a box."""
    out = []
    for ch in text:
        if ch in PLAQUE_GLYPHS:
            out.append(ch)
            continue
        base = "".join(c for c in unicodedata.normalize("NFKD", ch) if not unicodedata.combining(c))
        out.append(base if base and all(c in PLAQUE_GLYPHS for c in base) else "?")
    return "".join(out)


def art(out=None):
    """(title, credit) of the picture the panel shows; ("", "") when not known."""
    choice = current()
    if choice == ROTATION:              # the plaque follows the published picture, not a pick made before it
        choice = _state().get("rotation_shown") or shown()
    if choice in PAINTINGS:
        return PAINTINGS[choice][1:]
    if choice == LINK:
        return LINK, urllib.parse.urlparse(_state().get("link_url", "")).hostname or ""
    if choice in DAILY:
        meta = daily(choice) or {}
        return str(meta.get("title") or ""), str(meta.get("credit") or "")
    if choice in uploads():
        return choice.replace("_", " ").replace("-", " "), "Uppladdad i Home Assistant"
    try:
        meta = json.loads(((Path(out) if out else OUT) / "apod.json").read_text())
    except (OSError, ValueError):
        return "", ""
    # The source after who made it (Joel, 2026-10-29: "borde inte nasa bilden också ha källa"), as Jorden's "NASA EPIC"
    credit = str(meta.get("credit") or "")
    return str(meta.get("title") or ""), f"{credit} · NASA APOD" if credit else "NASA APOD"


def art_text(out=None):
    """NASA's English explanation of its picture when NASA is the choice, or a daily source's text about its
    picture, for Tavla's plaque; else ""."""
    choice = current()
    if choice == ROTATION:              # the plaque follows the published picture, not a pick made before it
        choice = _state().get("rotation_shown") or shown()
    if choice in DAILY:
        return str((daily(choice) or {}).get("text") or "")
    if choice != NASA:
        return ""
    try:
        meta = json.loads(((Path(out) if out else OUT) / "apod.json").read_text())
    except (OSError, ValueError):
        return ""
    return str(meta.get("explanation") or "")


def earth(out=None):
    """Tavla's Earth view from server/earth.py's earth.json: earth_title, earth_line, earth_version, neo_head and
    neo0..neo2, all "" when not known (the panel then hides the Jorden button)."""
    try:
        meta = json.loads(((Path(out) if out else OUT) / "earth.json").read_text())
    except (OSError, ValueError):
        meta = {}
    rows = [str(r) for r in meta.get("neo") or []][:3] if isinstance(meta.get("neo"), list) else []
    fields = {"earth_title": meta.get("title"), "earth_line": meta.get("line"), "earth_version": meta.get("version"),
              "neo_head": meta.get("neo_head"), **{f"neo{i}": (rows + ["", "", ""])[i] for i in range(3)}}
    return {k: plaque_text(str(v or "")) for k, v in fields.items()}
