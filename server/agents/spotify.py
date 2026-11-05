"""Spotify on the JBL through Spotify Soloist, the Spotify Connect speaker "Jarvis" on this Pi (docs/spotify.md).

Soloist owns the Spotify login and the playback; this tool only drives it with `soloist ctl` over its WebSocket
API on 127.0.0.1 (no authentication there, so it never leaves loopback). Soloist has no search: "spela X" asks
Spotify's Web API when SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET are in server.env (an app without a user login,
search only: 0.4 s against 1.1-3.1 s for the web search, 2026-10-28), else, or when that fails, finds the
open.spotify.com link with the web search. Enabled by JARVIS_SOLOIST_WS in server.env.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

import lookup
import speaker

TOOL = {"type": "function", "name": "spotify", "strict": False,
        "description": ("Spotify på JBL-högtalaren (Spotify Connect-högtalaren Jarvis). Spela en låt, ett album, en "
                        "spellista eller en artist; pausa, fortsätt, nästa, föregående, sätt volym, slå på/av blandning "
                        "(shuffle), lägg en låt i kön (add_to_queue) eller läs vad som spelas. Skicka en Spotify-länk/URI, eller query med titel och artist så söker verktyget. "
                        "Joels gillade låtar (Liked Songs): action play med query \"mina gillade låtar\". "
                        "Blandat (\"shuffla mina gillade låtar\"): ett anrop, action play med shuffle true. "
                        "Använd inte radio för att styra Spotify. Bekräfta lyckad styrning med en kort mening, utan att "
                        "förklara hur du tolkade eller sökte."),
        "parameters": {"type": "object", "properties": {
            "action": {"type": "string", "enum": ["play", "pause", "resume", "next", "previous", "status", "volume", "search",
                                                     "shuffle", "add_to_queue"]},
            "query": {"type": "string", "description": "Låttitel och artist, artistnamn, album eller spellista"},
            "media_type": {"type": "string", "enum": ["track", "artist", "album", "playlist"]},
            "uri": {"type": "string", "description": "Exakt Spotify-URI eller open.spotify.com-länk"},
            "level": {"type": "integer", "minimum": 0, "maximum": 100, "description": "Vid volume: 0-100"},
            "enabled": {"type": "boolean", "description": "Vid shuffle: true på, false av"},
            "shuffle": {"type": "boolean", "description": "Vid play: true spelar blandat, false i ordning"}},
            "required": ["action"]}}
TYPES = ("track", "album", "playlist", "artist")
COMMANDS = {"pause": "pause", "resume": "play", "next": "next", "previous": "prev"}
ACK = {"pause": "Pausar Spotify.", "resume": "Fortsätter Spotify.", "next": "Nästa låt.", "previous": "Föregående låt."}
# Soloist's `now` mirrors the whole account: these act on whatever device is active, so on Joel's iPhone when it has
# the session. They run only while this speaker is the active device (2026-10-29: a pause paused the phone).
HERE_ONLY = ("pause", "next", "previous", "volume", "shuffle", "add_to_queue")
ELSEWHERE = "Spotify spelar inte på högtalaren just nu, så jag rör det inte."
SETTLE_S = 3.0      # how long to wait for Soloist to report the new item, for the spoken name
LINK = re.compile(r"https://open\.spotify\.com/(?:intl-[a-z]{2}/)?(track|album|artist|playlist)/([A-Za-z0-9]{22})")


class SpotifyError(Exception):
    """A safe, user-facing failure."""


def configured():
    return bool(os.environ.get("JARVIS_SOLOIST_WS"))


def binary():
    return os.environ.get("JARVIS_SOLOIST_BIN") or str(Path.home() / ".local/share/soloist/bin/soloist")


def ctl(*args, timeout=8):
    """Run `soloist ctl` against the configured loopback WebSocket; its stdout, or SpotifyError."""
    ws = os.environ.get("JARVIS_SOLOIST_WS", "")
    host, _, port = ws.rpartition(":")
    if host not in ("127.0.0.1", "localhost") or not port.isdigit():
        raise SpotifyError("Spotify-högtalaren är inte korrekt inställd.")
    try:
        result = subprocess.run([binary(), "ctl", "--ws", ws, *args], capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise SpotifyError("Spotify-högtalaren är inte installerad på Jarvis.") from None
    except subprocess.TimeoutExpired:
        # Never retry a command after a lost answer: the first one may have run.
        raise SpotifyError("Spotify-högtalaren svarade inte. Jag kan inte bekräfta åtgärden.") from None
    if result.returncode == 2:          # soloist ctl: connection failed (the daemon is down or restarting)
        raise SpotifyError("Spotify-högtalaren går inte att nå just nu.")
    if result.returncode != 0:
        reason = " ".join((result.stderr or result.stdout).split())[:120]
        raise SpotifyError(f"Spotify-högtalaren vägrade ({reason or result.returncode}).")
    return result.stdout


# "Mina gillade låtar", "liked songs": the account's own collection, which no search finds. The web search and
# the API found a public playlist named "Mina gillade låtar" by someone else and played it (2026-10-28).
LIKED = re.compile(r"(?:(?:mina|min|my)\s+)?(?:gillade|gillad|likeade|sparade|liked|saved)\s+(?:låtar|songs|tracks)"
                   r"|(?:mina|my)\s+(?:favoritlåtar|favoriter|favourites|favorites)", re.I)


def liked(query):
    """The collection's URI when the query asks for the liked songs, else None. JARVIS_SPOTIFY_USER is the
    Spotify account Soloist is logged in as (its log: "logged in as ...")."""
    user = os.environ.get("JARVIS_SPOTIFY_USER", "")
    if not query or not LIKED.fullmatch(str(query).strip(" .!?")) or not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", user):
        return None
    return f"spotify:user:{user}:collection"


def spotify_uri(value):
    """spotify:type:id for a Spotify catalogue item only; no arbitrary URLs, local paths or other services."""
    value = str(value or "").strip()
    if match := re.fullmatch(r"spotify:(track|album|artist|playlist):([A-Za-z0-9]{22})", value):
        return value
    if match := re.fullmatch(r"spotify://(track|album|artist|playlist)/([A-Za-z0-9]{22})", value):
        return f"spotify:{match[1]}:{match[2]}"
    parsed = urllib.parse.urlsplit(value)
    if (parsed.scheme == "https" and parsed.netloc == "open.spotify.com" and
            (match := re.fullmatch(r"/(?:intl-[a-z]{2}/)?(track|album|artist|playlist)/([A-Za-z0-9]{22})/?", parsed.path))):
        return f"spotify:{match[1]}:{match[2]}"
    raise SpotifyError("Använd en Spotify-länk till en låt, artist, ett album eller en spellista.")


def _title(raw):
    """'Flying Without Wings - song and lyrics by Westlife | Spotify' -> 'Flying Without Wings med Westlife'."""
    raw = raw.split(" | ")[0].strip()
    raw = re.sub(r"^Listen to ", "", raw)       # the web search's page titles ("Listen to Ghost Stories med Coldplay")
    if m := re.match(r"(.+?) - (?:song|single|album|playlist|song and lyrics)(?: and lyrics)? by (.+)$", raw, re.I):
        return f"{m[1].strip()} med {m[2].strip()}"
    return raw.removesuffix(" - Spotify").strip() or "Spotify"


API_TIMEOUT_S = 4
_token = {"value": None, "until": 0.0}


def api_configured():
    return bool(os.environ.get("SPOTIFY_CLIENT_ID") and os.environ.get("SPOTIFY_CLIENT_SECRET"))


def _api_token(urlopen):
    """A client-credentials token, kept until a minute before it runs out. It searches only; it plays nothing."""
    if _token["value"] and time.monotonic() < _token["until"]:
        return _token["value"]
    import base64
    pair = f"{os.environ['SPOTIFY_CLIENT_ID']}:{os.environ['SPOTIFY_CLIENT_SECRET']}".encode()
    request = urllib.request.Request("https://accounts.spotify.com/api/token", data=b"grant_type=client_credentials",
                                     headers={"Authorization": "Basic " + base64.b64encode(pair).decode()})
    answer = json.load(urlopen(request, timeout=API_TIMEOUT_S))
    _token.update(value=answer["access_token"], until=time.monotonic() + int(answer.get("expires_in", 3600)) - 60)
    return _token["value"]


def api_search(query, media_type="track", urlopen=None):
    """[{name, uri, type}] from Spotify's Web API over all four types, the asked type first; SpotifyError on any
    failure so search() can fall back to the web search. The name reads like the web search's ("X med Artist")."""
    urlopen = urlopen or urllib.request.urlopen
    words = " ".join(w for w in re.findall(r"\w+", query) if w.casefold() not in STOP_WORDS) or query
    url = "https://api.spotify.com/v1/search?" + urllib.parse.urlencode(
        {"q": words, "type": ",".join(TYPES), "limit": 5, "market": "SE"})
    for attempt in (1, 2):
        try:
            request = urllib.request.Request(url, headers={"Authorization": "Bearer " + _api_token(urlopen)})
            answer = json.load(urlopen(request, timeout=API_TIMEOUT_S))
            break
        except urllib.error.HTTPError as err:
            _token["value"] = None
            if err.code != 401 or attempt == 2:      # 401: the token ran out early, so one new token
                raise SpotifyError(f"Spotify-sökningen svarade {err.code}.") from None
        except (OSError, ValueError, KeyError) as err:
            raise SpotifyError(f"Spotify-sökningen misslyckades ({type(err).__name__}).") from None
    found = []
    for kind in sorted(TYPES, key=lambda t: t != media_type):
        for rank, item in enumerate((answer.get(kind + "s") or {}).get("items") or []):
            if not item or not spotify_uri_ok(item.get("uri")):
                continue
            names = [a.get("name") for a in item.get("artists") or []][:2]
            if kind == "playlist":
                names = [(item.get("owner") or {}).get("display_name")]
            by = " och ".join(n for n in names if n)      # spoken: "Starboy med The Weeknd och Daft Punk"
            found.append({"name": f"{item['name']} med {by}" if by else item["name"], "uri": item["uri"], "type": kind,
                          "rank": rank})      # Spotify's own order within the type; the API gives no popularity
    return found


def spotify_uri_ok(value):
    return bool(re.fullmatch(r"spotify:(track|album|artist|playlist):[A-Za-z0-9]{22}", str(value or "")))


def search(query, media_type="track", websearch=None):
    """[{name, uri, type}], the asked type first: Spotify's Web API when configured, else the web search for
    open.spotify.com links (also when the API fails, logged)."""
    if websearch is None and api_configured():
        try:
            return api_search(query, media_type)
        except SpotifyError as err:
            print(f"spotify: Web API search failed, using the web search: {err}", flush=True)
    ok_or_error, text = (websearch or lookup.websearch)(f"open.spotify.com {media_type} {query}")
    if ok_or_error:
        raise SpotifyError(text)
    found = []
    for line in text.splitlines():
        if not line.startswith("- "):
            continue
        link = LINK.search(line)
        if not link:
            continue
        uri = f"spotify:{link[1]}:{link[2]}"
        if any(f["uri"] == uri for f in found):
            continue
        found.append({"name": _title(line[2:].split(": ", 1)[0]), "uri": uri, "type": link[1]})
    found.sort(key=lambda f: f["type"] != media_type)
    return found


STOP_WORDS = {"av", "med", "by", "the", "och", "feat"}


def _words(text):
    return {w for w in re.findall(r"[0-9a-zåäöéèüæø]+", text.casefold()) if w not in STOP_WORDS}


def confident(query, found, media_type="track"):
    """The match to play without the model, else None: a direct play must never start the wrong music
    (quick_command.py). Every word of the query must be in the match ("Ghost Stories av Coldplay" -> "Ghost Stories
    med Coldplay"). Then, by Spotify's order (rank) within each type, measured on Jarvis 2026-10-28:
    - an asked album, playlist or artist ("spela albumet Parachutes") before the other types;
    - a bare artist name ("Westlife") is the artist when Spotify lists it first and its top song is by it, so
      "Starboy" plays The Weeknd's song, not the artist StarBoy;
    - else the best-ranked song or album, a song on a tie."""
    want = _words(query)
    hits = [f for f in found if want and want <= _words(f["name"])]
    if not hits:
        return None
    rank = lambda f: f.get("rank", 0)
    if media_type != "track" and (same := [f for f in hits if f.get("type") == media_type]):
        return min(same, key=rank)
    top_song = next((f for f in found if f.get("type") == "track" and rank(f) == 0), None)
    for f in hits:
        if (f.get("type") == "artist" and rank(f) == 0 and _words(f["name"]) == want
                and (top_song is None or want <= _words(top_song["name"]))):
            return f
    order = {"track": 0, "album": 1, "playlist": 2}
    pool = [f for f in hits if f.get("type") != "artist"] or hits
    return min(pool, key=lambda f: (rank(f), order.get(f.get("type"), 3)))


def choose(query, media_type="track", websearch=None):
    """What the direct path plays for a query, or None to leave it to the model: the liked songs, else a confident
    search match. One function for quick_play and `spotify.py try`, which once picked differently (2026-10-28)."""
    if collection := liked(query):
        return {"uri": collection, "name": "dina gillade låtar", "type": "collection"}
    try:
        found = search(query, media_type, websearch)
    except SpotifyError:
        return None
    return confident(query, found, media_type)


def quick_play(query, media_type="track", websearch=None):
    """(is_error, text) after playing a confident match, or None to leave the request to the model."""
    pick = choose(query, media_type, websearch)
    if pick is None:
        return None
    try:
        _ready_speaker()                # the radio stops and the JBL connects now; a failure is still said
    except SpotifyError as err:
        return True, str(err)
    # Said at once from the search hit (Joel, 2026-10-28), and the music starts once the panel has said it: it
    # started at once and drowned "Spelar Kent". A failed start is only logged, the reply is already spoken.
    global _pending
    _pending = _after_reply(lambda: _start(pick))
    return False, f"Spelar {pick['name']}."


QUIET = {"idle", "unavailable", "unknown"}
AFTER_REPLY_MAX_S, AFTER_REPLY_POLL_S = 12.0, 0.15
_pending = None                         # the last deferred start's thread, for the tests
_shuffle_pending = None                 # the last deferred shuffle's thread, for the tests


def _satellite_state():
    """The panel's voice satellite now ("processing", "responding", "idle", ...), or None without one."""
    entity = os.environ.get("JARVIS_PANEL_SATELLITE")
    if not entity:
        return None
    try:
        import hass
        return (hass.get_json(f"/api/states/{entity}", timeout=1) or {}).get("state")
    except Exception:                   # noqa: BLE001 - no Home Assistant: start the music as before
        return None


def _after_reply(start):
    """start() now, or, while the panel is in a voice turn, once it is idle again (Jarvis has spoken), at most
    AFTER_REPLY_MAX_S later. The thread, or None when it ran at once."""
    import threading
    state = _satellite_state()
    if state is None or state in QUIET:     # typed, not spoken: nothing to wait for
        start()
        return None

    def wait():
        began = time.monotonic()
        last = state
        warm = _wake_link()
        while time.monotonic() - began < AFTER_REPLY_MAX_S:
            time.sleep(AFTER_REPLY_POLL_S)
            last = _satellite_state()
            if last is None or last in QUIET:
                break
        print(f"spotify: starting after the reply, {round((time.monotonic() - began) * 1000)} ms "
              f"(satellite {last})", flush=True)
        try:
            start()                     # returns once Spotify plays (or after _confirm's 3 s)
        finally:
            if warm is not None:
                warm.terminate()
    thread = threading.Thread(target=wait, daemon=True)
    thread.start()
    return thread


WAKE_ROLE = "Jarvis-link"              # _wake_link's own media.role, apart from Music (Soloist)


def _wake_link():
    """Silence to the JBL while Jarvis answers, so its Bluetooth link is up when the song starts: the link closes
    2 s after playback stops (session.suspend-timeout-seconds in 90-jbl.conf), and reopening it with the song
    seemed to stutter its first moment (Joel, 2026-10-28). Only for the wait, never kept open: the JBL turns
    itself off only without a stream. The process, or None without pw-play.

    Never `--volume 0` (2026-11-01): WirePlumber saved that volume for media.role Music, which Soloist's stream
    shares, and gave it to every Spotify stream created afterwards; Spotify then "played" at stream volume 0 after
    each voice start until a sound at full volume overwrote it. The zeros are silent anyway, and the stream has a
    role of its own (WAKE_ROLE) so it never touches Music's saved volume."""
    try:
        with open("/dev/zero", "rb") as zero:      # the child keeps its own copy of the descriptor
            return subprocess.Popen(["pw-play", "--raw", "--target", os.environ.get("JARVIS_JBL_SINK", "jbl"),
                                     "--media-role", WAKE_ROLE,
                                     "--format", "s16", "--rate", "44100", "--channels", "2", "-"],
                                    stdin=zero, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        return None


def _shuffle(on, fresh):
    """Shuffle on or off. fresh: a play of a new list just started, which begins at its first song and was not
    shuffled (2026-11-01: "shuffla mina gillade låtar" set shuffle during the reply, then the deferred play of
    Liked Songs began at its first song, in order); `next` then leaves that first song for a random one."""
    ctl("shuffle", "on" if on else "off")
    if on and fresh:
        ctl("next")
    print(f"spotify: shuffle {'on' if on else 'off'}{' and next, after a fresh start' if on and fresh else ''}",
          flush=True)


def _shuffle_after_reply(on):
    """By voice: shuffle once Jarvis has spoken and a play from the same turn has started, never before it. The
    model may call play and shuffle in parallel, so a play is waited for even if it registers a little later."""
    import threading
    earlier = _pending                  # a play already waiting from this turn, or an old finished one

    def wait():
        began = time.monotonic()
        while time.monotonic() - began < AFTER_REPLY_MAX_S and _satellite_state() not in (None, *QUIET):
            time.sleep(AFTER_REPLY_POLL_S)
        time.sleep(0.5)                 # the play's own wait ends at the same moment: let it start first
        pending = _pending
        fresh = pending is not None and (pending is not earlier or earlier.is_alive())
        if pending is not None and pending.is_alive():
            pending.join(SETTLE_S + 10)
        try:
            if _here():
                _shuffle(on, fresh)
            else:
                print("spotify: shuffle after the reply skipped: Spotify does not play on the JBL", flush=True)
        except SpotifyError as err:
            print(f"spotify: shuffle after the reply FAILED: {err}", flush=True)
    thread = threading.Thread(target=wait, daemon=True)
    thread.start()
    return thread


def _in_order():
    """A new play is in order unless Joel asks for shuffle (2026-11-01: "shuffle ska ej va default"). Spotify keeps
    shuffle on from an earlier "shuffla", and "Spela Coldplay" then began on a random song."""
    try:
        ctl("shuffle", "off")
    except SpotifyError as err:
        print(f"spotify: shuffle off FAILED: {err}", flush=True)


def _start(pick, shuffle=None):
    """Activate the JBL's Spotify and play the pick; how long it took to start, or why not, goes to the log.
    In order unless shuffle is True."""
    try:
        before = _now_uri()
        ctl("activate")
        if not shuffle:
            _in_order()
        ctl("play", pick["uri"])
    except SpotifyError as err:
        print(f"spotify: direct play FAILED after the reply: {pick['uri']} ({pick['name']}): {err}", flush=True)
        return
    _confirm(pick, before)
    if shuffle:
        try:
            _shuffle(True, fresh=True)
        except SpotifyError as err:
            print(f"spotify: shuffle after the start FAILED: {err}", flush=True)


def _confirm(pick, before):
    """Log how long the direct play took to start, or that it did not within SETTLE_S."""
    started = time.monotonic()
    text = _started(pick["uri"], before)
    took = round((time.monotonic() - started) * 1000)
    if text:
        print(f"spotify: direct play started after {took} ms: {pick['uri']}", flush=True)
    else:
        print(f"spotify: direct play NOT confirmed within {SETTLE_S:.0f} s: {pick['uri']} ({pick['name']})", flush=True)


def _ready_speaker():
    """Free the JBL for Spotify: stop MPD's radio there, and connect the paired speaker when it is off."""
    try:
        if speaker.configured() and speaker.mpd_state() == "play":
            speaker.mpd("stop")
            try:
                import radio
                radio._tell_speaker("idle")     # Spelas nu drops the radio at once, then shows Spotify
            except Exception:                   # noqa: BLE001 - the panel's row is a courtesy, playback is not
                pass
    except speaker.SpeakerError:
        pass
    try:
        if speaker.connected() is None:
            is_error, text = speaker.control("connect")
            if is_error:
                raise SpotifyError(f"JBL-högtalaren går inte att nå: {text}")
    except speaker.SpeakerError as err:
        raise SpotifyError(f"JBL-högtalaren går inte att nå: {err}") from None


def status():
    """What plays, short: Soloist's playback_state carries cover URLs and options the model does not need."""
    try:
        now = json.loads(ctl("now", "--json") or "{}")
    except ValueError:
        raise SpotifyError("Spotify-högtalaren gav ett ogiltigt svar.") from None
    item = now.get("item") or {}
    name = lambda node: ((node or {}).get("decorations") or {}).get("identity", {}).get("name")
    # Soloist 1.3.10 nests parent and creators in decorations; read the item itself too.
    field = lambda key: item.get(key) or (item.get("decorations") or {}).get(key)
    position = (now.get("position") or {}).get("position_ms")
    return {"status": now.get("status"), "track": name(item), "uri": item.get("uri"),
            "artists": [name(c.get("entity")) for c in field("creators") or [] if name(c.get("entity"))],
            "album": name((field("parent") or {}).get("entity")),
            "from": name(now.get("context")), "volume": now.get("volume"), "active": now.get("is_active"),
            "position_s": round(position / 1000) if isinstance(position, (int, float)) else None}


def _here(state=None):
    """True when this speaker is Spotify's active Connect device (not Joel's phone)."""
    return (state or status()).get("active") is True


def _now_uri():
    try:
        return status().get("uri")
    except SpotifyError:
        return None


def _started(selected, before):
    """'Spelar My Love med Westlife.' from what Soloist reports once the new item plays, or None.
    A search hit's page title is no song name ("Listen to Westlife songs", 2026-10-28)."""
    end = time.monotonic() + SETTLE_S
    while time.monotonic() < end:
        try:
            now = status()
        except SpotifyError:
            return None
        if now.get("status") == "playing" and now.get("track") and (now.get("uri") == selected or now.get("uri") != before):
            who = " och ".join(now.get("artists") or [])
            return f"Spelar {now['track']}{f' med {who}' if who else ''}."
        time.sleep(0.25)
    return None


def control(action=None, query=None, media_type="track", uri=None, level=None, websearch=None, enabled=None,
            shuffle=None):
    """Return (is_error, text), as the other local Jarvis tools. Only Spotify playback on the JBL changes."""
    try:
        if action not in (*COMMANDS, "play", "search", "status", "volume", "shuffle", "add_to_queue"):
            raise SpotifyError("Okänd Spotify-åtgärd.")
        if media_type not in TYPES:
            raise SpotifyError("Välj låt, artist, album eller spellista.")
        if query is not None and (not isinstance(query, str) or not query.strip() or len(query) > 300):
            raise SpotifyError("Ange en kort Spotify-sökning.")
        if (query or uri) and action not in ("play", "search", "add_to_queue"):
            raise SpotifyError("Den här Spotify-åtgärden tar ingen sökning eller länk.")
        if action == "search" and (not query or uri):
            raise SpotifyError("Ange vad som ska sökas.")
        if action == "add_to_queue" and not (query or uri):
            raise SpotifyError("Ange låten som ska läggas i kön.")
        if action == "shuffle" and not isinstance(enabled, bool):
            raise SpotifyError("Säg om blandningen ska vara på eller av.")
        if action == "status":
            return False, json.dumps(status(), ensure_ascii=False)[:1500]
        if action == "volume" and (not isinstance(level, int) or isinstance(level, bool) or not 0 <= level <= 100):
            raise SpotifyError("Ange volymen som 0 till 100.")
        if shuffle is not None and (action != "play" or not isinstance(shuffle, bool)):
            shuffle = None              # only play takes it; a stray value is ignored, not an error to speak
        if action == "shuffle" and _in_voice_turn():
            global _shuffle_pending           # after a play from the same turn has started (2026-11-01)
            _shuffle_pending = _shuffle_after_reply(enabled)
            return False, "Blandar Spotify." if enabled else "Spelar Spotify i ordning."
        if action in HERE_ONLY and not _here():
            return True, ELSEWHERE
        if action == "resume":
            return _resume()
        if action == "volume":
            ctl("volume", str(level))
            return False, f"Spotify på {level} procent."
        if action in COMMANDS:
            ctl(COMMANDS[action])
            return False, ACK[action]
        if action == "shuffle":
            ctl("shuffle", "on" if enabled else "off")
            return False, "Blandar Spotify." if enabled else "Spelar Spotify i ordning."
        if action == "add_to_queue":
            return _add_to_queue(query, uri, websearch)
        if action == "search":
            return False, json.dumps({"matches": search(query.strip(), media_type, websearch)[:5]}, ensure_ascii=False)
        if (collection := liked(query)) and not uri:
            selected, title = collection, "dina gillade låtar"
        elif uri:
            selected, title = spotify_uri(uri), "Spotify"
        elif query:
            found = search(query.strip(), media_type, websearch)
            if not found:
                return True, "Jag hittade det inte på Spotify. Be Joel om titel och artist, eller en länk."
            selected, title = found[0]["uri"], found[0]["name"]
        else:
            selected, title = None, None
        _ready_speaker()
        if selected and _in_voice_turn():
            # By voice the music waits for the reply, as on the direct path: "Play soccer with Kent" started
            # Socker while the model was still writing, under its answer (Joel, 2026-10-28).
            global _pending  # noqa: PLW0603
            _pending = _after_reply(lambda: _start({"uri": selected, "name": title}, shuffle))
            # Plain: in Swedish this text IS the spoken answer (agent_core.direct_reply). An instruction to the
            # model here was read aloud word for word, 9 s, on 2026-10-28.
            return False, f"Spelar {title}{' blandat' if shuffle else ''}."
        before = _now_uri()
        ctl("activate")
        if selected and not shuffle:
            _in_order()
        ctl("play", *([selected] if selected else []))
        said = _started(selected, before) or (f"Spelar {title}." if title else "Fortsätter Spotify.")
        if shuffle:
            _shuffle(True, fresh=bool(selected))
        return False, said
    except SpotifyError as err:
        return True, str(err)


def _resume():
    """Resume on the JBL. Paused here: play. Paused on another device: bring it here first (the panel speaks for the
    JBL). Playing on another device: leave it, or Joel's phone would lose the music."""
    state = status()
    if not _here(state):
        if state.get("status") == "playing":
            return True, "Spotify spelar redan på en annan enhet, så jag rör det inte."
        _ready_speaker()
        ctl("activate")
    ctl("play")
    return False, ACK["resume"]


def _add_to_queue(query, uri, websearch):
    """Soloist queues tracks only (`add-to-queue` rejects other URIs): a link must be a song, a query plays the
    first song hit."""
    if uri:
        selected, title = spotify_uri(uri), None
        if not selected.startswith("spotify:track:"):
            raise SpotifyError("Bara låtar kan läggas i kön.")
    else:
        found = [f for f in search(query.strip(), "track", websearch) if f.get("type") == "track"]
        if not found:
            return True, "Jag hittade ingen sådan låt på Spotify. Be Joel om titel och artist, eller en länk."
        selected, title = found[0]["uri"], found[0]["name"]
    ctl("add-to-queue", selected)
    return False, f"Lägger {title} i kön." if title else "Lägger låten i kön."


def _in_voice_turn():
    state = _satellite_state()
    return state is not None and state not in QUIET


if __name__ == "__main__":
    # The same control as the voice tool, for agents and the terminal on Jarvis, with server.env loaded
    # (docs/spotify.md, "From an agent"):
    #   spotify.py status | search [--album|--playlist|--artist] QUERY | play [--album|...] QUERY|LINK
    #   spotify.py pause | resume | next | previous | volume N | shuffle on|off | queue QUERY|LINK
    #   spotify.py try "SENTENCE"   (the direct path's choice, without playing)
    import sys
    args = sys.argv[1:] or ["status"]
    action, rest = args[0], args[1:]
    kind = "track"
    if rest and rest[0] in ("--album", "--playlist", "--artist", "--track"):
        kind, rest = rest[0][2:], rest[1:]
    words = " ".join(rest)
    if action == "try":
        # What a spoken request would do on the direct path, without playing (docs/spotify.md):
        #   spotify.py try "Spela Ghost Stories av Coldplay på Spotify"
        import time
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        import quick_command
        command = quick_command.match(words)
        if not command or command[0] != "spotify_play":
            print(json.dumps({"direct": False, "match": command}, ensure_ascii=False))
            sys.exit(0)
        started = time.monotonic()
        pick = choose(command[1]["query"], command[1]["media_type"])     # exactly what quick_play would play
        print(json.dumps({"direct": bool(pick), "args": command[1], "search_ms": round((time.monotonic() - started) * 1000),
                          "pick": pick, "to_model": pick is None}, ensure_ascii=False))
        sys.exit(0)
    if action == "volume":
        is_error, text = control("volume", level=int(words) if words.isdigit() else None)
    elif action == "shuffle" and words in ("on", "off"):
        is_error, text = control("shuffle", enabled=words == "on")
    elif action in ("status", "pause", "resume", "next", "previous"):
        is_error, text = control(action)
    elif action in ("search", "play", "queue"):
        link = words if words.startswith(("spotify:", "https://open.spotify.com/")) else None
        action = "add_to_queue" if action == "queue" else action
        is_error, text = control(action, query=None if link else (words or None), media_type=kind, uri=link)
    else:
        raise SystemExit("usage: spotify.py status | search|play [--album|--playlist|--artist] QUERY|LINK | "
                         "pause | resume | next | previous | volume N | shuffle on|off | queue QUERY|LINK")
    print(text)
    raise SystemExit(1 if is_error else 0)
