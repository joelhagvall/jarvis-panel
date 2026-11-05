#!/usr/bin/env python3
"""Sveriges Radio on the panel's speaker, or on a Bluetooth speaker Jarvis has connected. Standard library only.

  channels()            SR's channels (P1, P2, P3, P4 Stockholm ...) with their MP3 streams, kept a day
  now(channel)          what is on: the programme and the song, from SR's open API
  play(channel)         Home Assistant's media_player.play_media with the channel's stream on the panel, or on
                        the Bluetooth speaker while one is connected (speaker.py, JARVIS_SPEAKER_PLAYER)
  stop()                media_player.media_stop on both

The panel is the media player in JARVIS_RADIO_PLAYER (its entity id once Home Assistant has adopted it,
for example media_player.hallpanel_hallpanel). Home Assistant is called on 127.0.0.1 through hass.py, and
only for these two actions on that one player. Home Assistant converts the
MP3 stream to the format the panel asks for (WAV, 16 kHz mono).

  python3 server/agents/radio.py now P3
"""
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import hass
import speaker

TZ = ZoneInfo("Europe/Stockholm")
SR = "https://api.sr.se/api/v2"
AGENT = "hallpanel/1 (Jarvis, a home voice assistant; https://github.com/joelhagvall/jarvis-panel)"
_cache = {}
_playing = {}                   # {"channel", "player"} of the last play this process started, until a stop
_sleep = {}                     # {"timer", "at"}: the sleep timer that stops the radio, while one is set


def _get(url, keep_s, timeout=4):
    hit = _cache.get(url)
    if hit and time.monotonic() - hit[0] < keep_s:
        return hit[1]
    req = urllib.request.Request(url, headers={"User-Agent": AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.load(r)
    _cache[url] = (time.monotonic(), data)
    return data


def channels():
    data = _get(f"{SR}/channels?format=json&pagination=false", 86400)
    return [c for c in data.get("channels") or [] if (c.get("liveaudio") or {}).get("url")]


def find(name):
    """The channel that best fits a spoken name: "P3", "p4", "P4 Stockholm", "din gata"."""
    want = " ".join(str(name or "P3").lower().replace("kanal", "").split())
    all_ = channels()
    for test in (lambda c: c["name"].lower() == want,
                 lambda c: want == "p4" and c["name"] == "P4 Stockholm",   # his local P4
                 lambda c: c["name"].lower().startswith(want),
                 lambda c: want in c["name"].lower()):
        hits = [c for c in all_ if test(c)]
        if hits:
            return min(hits, key=lambda c: len(c["name"]))
    return None


def _when(sr_date):
    """SR's "/Date(1792667625000)/" as Swedish local HH:MM."""
    ms = re.search(r"\d+", sr_date or "")
    return datetime.fromtimestamp(int(ms.group()) / 1000, timezone.utc).astimezone(TZ).strftime("%H:%M") if ms else ""


def now(channel="P3"):
    try:
        c = find(channel)
        if c is None:
            return True, f"Sveriges Radio har ingen kanal som heter {channel}."
        lines = [f"{c['name']}:"]
        try:
            ep = (_get(f"{SR}/scheduledepisodes/rightnow?channelid={c['id']}&format=json", 60)
                  .get("channel") or {}).get("currentscheduledepisode") or {}
            if ep.get("title"):
                lines.append(f"Programmet nu: {ep['title']}" + (f", {ep['subtitle']}" if ep.get("subtitle") else "")
                             + f" ({_when(ep.get('starttimeutc'))}–{_when(ep.get('endtimeutc'))}).")
        except (OSError, ValueError, urllib.error.URLError):
            pass
        try:
            pl = _get(f"{SR}/playlists/rightnow?channelid={c['id']}&format=json", 30).get("playlist") or {}
            if (song := pl.get("song")) and song.get("title"):
                lines.append(f"Låten nu: {song['title']} med {song.get('artist', 'okänd artist')}.")
            elif (prev := pl.get("previoussong")) and prev.get("title"):
                lines.append(f"Senaste låten: {prev['title']} med {prev.get('artist', 'okänd artist')}"
                             f" (slutade {_when(prev.get('stoptimeutc'))}).")
        except (OSError, ValueError, urllib.error.URLError):
            pass
        return False, " ".join(lines) if len(lines) > 1 else f"{c['name']}: SR säger inget om vad som spelas."
    except (OSError, ValueError, urllib.error.URLError) as e:
        return True, f"Sveriges Radio svarade inte ({type(e).__name__})."


SPEAKER_EVENT = "jarvis_agent_speaker"     # ha/custom_components/jarvis_agent/media.py shows it in Spelas nu


def _tell_speaker(state, c=None, place=None):
    """Tell Home Assistant what the Bluetooth speaker plays, for the panel's Spelas nu row. Its MPD player in HA
    knows no channel name or logo and polls slowly, so the radio says it. Best effort: playback never waits.
    "paused" and "playing" without a channel keep what HA already shows."""
    data = {"state": state}
    if c is not None:
        data.update(title=c["name"], artist="Sveriges Radio", source="Radio", image=c.get("image") or "",
                    channel_id=c["id"], place=place or "")
    try:
        hass.request(f"/api/events/{SPEAKER_EVENT}", data, timeout=3)
    except (OSError, hass.Refused, urllib.error.URLError):
        pass


def _ha(service, data, player=None):
    player = player or os.environ.get("JARVIS_RADIO_PLAYER")
    if not player:
        return True, "Panelens högtalare är inte ansluten till Home Assistant än, så radion kan inte spelas där."
    try:
        hass.call_service("media_player", service, {"entity_id": player, **data})
        return False, ""
    except hass.Refused:
        return True, "Jarvis har ingen giltig token till Home Assistant."
    except urllib.error.HTTPError as e:
        return True, f"Home Assistant svarade {e.code} på {service}."
    except (OSError, urllib.error.URLError) as e:
        return True, f"Home Assistant svarade inte ({type(e).__name__})."


def play(channel="P3"):
    try:
        c = find(channel)
    except (OSError, ValueError, urllib.error.URLError) as e:
        return True, f"Sveriges Radio svarade inte ({type(e).__name__})."
    if c is None:
        return True, f"Sveriges Radio har ingen kanal som heter {channel}."
    player, place = speaker.output()
    if player and player == speaker.player():
        try:
            speaker.play_url(c["liveaudio"]["url"])
        except speaker.SpeakerError as e:
            return True, str(e)
        _playing.update(channel=c["name"], player=player)
        _tell_speaker("playing", c, place)
        return False, f"Jarvis har startat {c['name']} på {place}."
    error, text = _ha("play_media", {"media_content_id": c["liveaudio"]["url"], "media_content_type": "music",
                                  "extra": {"metadata": {"title": c["name"], "artist": "Sveriges Radio", "source": "Radio",
                                                         "image": c.get("image") or "", "channel_id": c["id"]}}},
                      player)
    if error:
        return True, text
    _playing.update(channel=c["name"], player=player)
    # No "what is on" here: Sveriges Radio's lookup took about 1 s before Jarvis could answer, while the radio
    # waited 20 dB down under his reply (2026-10-23). He hears "Spelar P3."; action "now" tells the rest.
    return False, f"Home Assistant har startat {c['name']} på {place}."


def stop():
    cancel_sleep()
    error, text = _ha("media_stop", {})
    if speaker.player():                    # it may play on the Bluetooth speaker instead: stop both
        try:
            speaker.mpd("stop")
            error = False
            _tell_speaker("idle")
        except speaker.SpeakerError:
            pass
    if not error:
        _playing.clear()
    return (True, text) if error else (False, "Radion är stoppad.")


def stop_speaker():
    """Before a Bluetooth speaker is disconnected: stop the radio there instead of leaving MPD failing."""
    if speaker.player():
        try:
            speaker.mpd("stop")
        except speaker.SpeakerError:
            pass
        _tell_speaker("idle")
        if _playing.get("player") == speaker.player():
            _playing.clear()


def playing_on_speaker():
    """True when the radio plays on the Bluetooth speaker: this process started it there, or MPD plays."""
    if not speaker.player():
        return False
    if _playing.get("player") == speaker.player():
        return True
    try:
        return speaker.mpd_state() == "play"
    except speaker.SpeakerError:
        return False


def _playing_on(player):
    """True when the radio this process started still plays on player (MPD for the speaker, HA for the panel)."""
    try:
        if player == speaker.player():
            return speaker.mpd_state() == "play"
        return (hass.get_json(f"/api/states/{player}") or {}).get("state") == "playing"
    except (OSError, ValueError, hass.Refused, urllib.error.URLError, speaker.SpeakerError):
        return False


def on_speaker():
    """The channel playing on the Bluetooth speaker, or None: what a disconnect moves to the panel. MPD's stream URL
    holds SR's channel id, so this also works in another process or after the server restarted, when _playing is
    empty (the switch was tested by hand with an injected _playing four times, 2026-10-27)."""
    if not speaker.player():
        return None
    try:
        status, song = speaker.mpd("status", "currentsong")
    except speaker.SpeakerError:
        return None
    if status.get("state") != "play":
        return None
    if _playing.get("player") == speaker.player() and _playing.get("channel"):
        return _playing["channel"]
    match = re.search(r"sverigesradio\.se/.*/(\d+)(?:-[a-z]+)?\.mp3$", song.get("file", ""))
    try:
        c = next((c for c in channels() if match and c["id"] == int(match.group(1))), None)
    except (OSError, ValueError, urllib.error.URLError):
        c = None
    return c["name"] if c else None


def follow_output():
    """After a speaker was connected: move a radio that is playing elsewhere to it. Says what happened, or ""."""
    old, channel = _playing.get("player"), _playing.get("channel")
    if not old:
        # Another process or a restart: Home Assistant's Spelas nu sensor still knows a station on the panel.
        try:
            shown = hass.get_json("/api/states/sensor.jarvis_uppspelning", timeout=5) or {}
        except (OSError, ValueError, hass.Refused, urllib.error.URLError):
            shown = {}
        attrs = shown.get("attributes") or {}
        if attrs.get("control_entity") == os.environ.get("JARVIS_RADIO_PLAYER") and attrs.get("radio_channel"):
            old, channel = attrs["control_entity"], attrs["radio_channel"]
    new, place = speaker.output()
    if not old or not channel or old == new or not _playing_on(old):
        return ""
    error, _ = play(channel)
    if error:
        return f"{channel} gick inte att flytta dit."
    _ha("media_stop", {}, old)
    return f"{channel} spelas nu på {place}."


def pause(resume=False):
    """Pause or resume the radio where it plays: MPD on the speaker, HA's player on the panel."""
    player = _playing.get("player")
    if not player:
        return True, "Jarvis vet inte om någon radio som spelar."
    if player == speaker.player():
        try:
            speaker.mpd("pause 0" if resume else "pause 1")
        except speaker.SpeakerError as e:
            return True, str(e)
        _tell_speaker("playing" if resume else "paused")
    else:
        error, text = _ha("media_play" if resume else "media_pause", {}, player)
        if error:
            return True, text
    return False, f"{_playing.get('channel', 'Radion')} fortsätter." if resume else "Radion är pausad."


def cancel_sleep():
    if timer := _sleep.pop("timer", None):
        timer.cancel()
    _sleep.pop("at", None)


def sleep(minutes):
    """Stop the radio after minutes (the sleep timer); 0 cancels it. In the server's memory: a restart forgets it."""
    try:
        minutes = int(minutes)
    except (TypeError, ValueError):
        return True, "Hur många minuter?"
    cancel_sleep()
    if minutes <= 0:
        return False, "Insomningstimern är av."
    if not _playing.get("player"):
        return True, "Radion spelar inte."
    at = datetime.now(TZ) + timedelta(minutes=minutes)
    timer = threading.Timer(minutes * 60, stop)
    timer.daemon = True
    timer.start()
    _sleep.update(timer=timer, at=at)
    return False, f"Radion stängs av klockan {at:%H:%M}."


def radio(action="play", channel=None, minutes=None):
    action = str(action or "play").lower()
    if action == "stop":
        return stop()
    if action == "now":
        return now(channel or _playing.get("channel") or "P3")
    if action in ("pause", "resume"):
        return pause(resume=action == "resume")
    if action == "sleep":
        return sleep(minutes)
    return play(channel or "P3")


if __name__ == "__main__":
    action, *rest = sys.argv[1:] or ["now"]
    error, text = radio(action, " ".join(rest) or None)
    print(("FEL: " if error else "") + text)
