#!/usr/bin/env python3
"""Bluetooth speakers on Jarvis, for the radio. Standard library only.

The panel cannot send music to a Bluetooth speaker: its ESP32-C6 has Bluetooth LE only, and speakers such as
Joel's JBL Charge 4 take music over classic Bluetooth (A2DP). Jarvis (the Pi 5) can: BlueZ pairs and connects
the speaker, BlueALSA carries the sound and MPD (hallpanel-mpd.service) plays it. Home Assistant sees MPD as the
media player in JARVIS_SPEAKER_PLAYER. Connecting a speaker chooses it: while one is connected the radio plays
there, otherwise on the panel. Jarvis's own answers still come from the panel. See docs/bluetooth-speaker.md.

  python3 server/agents/speaker.py status
  python3 server/agents/speaker.py pair [name]     # hold the speaker's Bluetooth button first
  python3 server/agents/speaker.py connect [name]
  python3 server/agents/speaker.py disconnect
"""
import os
import re
import socket
import subprocess
import sys
import threading
import time

PANEL = "Panelen"                  # the choice that means the panel's own speaker (select.jarvis_ljud_ut)
AUDIO_SINK = "0000110b-"           # the A2DP Audio Sink service: a device that plays music sent to it
DEVICE = re.compile(r"^Device ((?:[0-9A-F]{2}:){5}[0-9A-F]{2}) (.*)$")
# 2026-11-01 bluetoothctl on Jarvis colours its lines even without a terminal ("\x1b[1;30mDevice ..."), and the
# regex above then found no paired speaker at all: Ljud ut showed only Panelen and Koppla om failed.
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x01|\x02")
TOOL = {"type": "function", "name": "hogtalare", "strict": False,
        "description": ("Var ljudet spelar: panelen eller en Bluetooth-högtalare, t.ex. Joels JBL Charge 4. connect med "
                        "en högtalares namn kopplar den och flyttar radion dit om den spelar; så länge högtalaren är "
                        "kopplad spelas radion där. connect med name \"Panelen\" (\"vanliga högtalaren\", \"spela på "
                        "panelen\") och disconnect kopplar från högtalaren, och en radio som spelar där flyttar till "
                        "panelen. status säger vilka som är parade och kopplade. Dina egna svar hörs från panelen."),
        "parameters": {"type": "object", "properties": {
            "action": {"type": "string", "enum": ["status", "connect", "disconnect"], "description": "Standard status"},
            "name": {"type": "string", "description": ("Högtalarens namn, t.ex. JBL, eller Panelen. Utelämna om det "
                                                        "bara finns en högtalare.")}}}}


class SpeakerError(Exception):
    pass


SWITCH = threading.Lock()          # Jarvis's own connect/disconnect and the Watcher never move the radio at once
_settled = {}                      # {"mac"}: the speaker this process connected last, None after its disconnect


def configured():
    return bool(os.environ.get("JARVIS_SPEAKER_PLAYER"))


def player():
    return os.environ.get("JARVIS_SPEAKER_PLAYER") or None


def mpd(*commands, timeout=5):
    """Send commands straight to the speaker's MPD (JARVIS_SPEAKER_MPD, default 127.0.0.1:6601). Home Assistant's
    MPD player took 12.3 s to answer one play_media, MPD itself under 10 ms (2026-10-27), so the radio skips HA."""
    host, _, port = (os.environ.get("JARVIS_SPEAKER_MPD") or "127.0.0.1:6601").rpartition(":")
    out = []
    try:
        with socket.create_connection((host, int(port)), timeout=timeout) as s, s.makefile("rwb") as f:
            if not f.readline().startswith(b"OK MPD"):
                raise SpeakerError("Högtalarens MPD svarade konstigt.")
            for c in commands:
                f.write(c.encode() + b"\n")
                f.flush()
                fields = {}
                while (line := f.readline()) != b"OK\n":
                    if not line or line.startswith(b"ACK"):
                        raise SpeakerError(f"Högtalarens MPD vägrade {c.split()[0]}: {line.decode().strip()}")
                    key, _, value = line.decode(errors="replace").rstrip("\n").partition(": ")
                    fields[key] = value
                out.append(fields)
        return out
    except OSError as e:
        raise SpeakerError(f"Högtalarens MPD svarade inte ({type(e).__name__}).") from None


def mpd_state():
    """MPD's player state: "play", "pause" or "stop"."""
    return mpd("status")[0].get("state", "stop")


def play_url(url):
    # The newline and carriage return too: anything that ends up on the MPD command line can no longer
    # start a new MPD command (2026-11-02 review). Only Sveriges Radio's URLS arrive here today.
    mpd("clear", 'add "' + url.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
        .replace("\r", " ") + '"', "play")


def _ctl(*args, timeout=10):
    try:
        r = subprocess.run(["bluetoothctl", *args], capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise SpeakerError("Bluetooth är inte installerat på Jarvis.") from None
    except subprocess.TimeoutExpired:
        raise SpeakerError("Bluetooth på Jarvis svarade inte i tid.") from None
    return ANSI.sub("", r.stdout)


def _devices(*filters):
    out = []
    for line in _ctl("devices", *filters).splitlines():
        if m := DEVICE.match(line.strip()):
            out.append((m.group(1), m.group(2)))
    return out


def _info(mac):
    fields, uuids = {}, []
    for line in _ctl("info", mac).splitlines():
        key, sep, value = line.strip().partition(": ")
        if not sep:
            continue
        if key == "UUID":
            uuids.append(value.lower())
        else:
            fields[key] = value
    fields["audio"] = fields.get("Icon", "").startswith("audio") or any(AUDIO_SINK in u for u in uuids)
    return fields


def speakers():
    """The paired speakers, [{"mac", "name", "connected"}], connected ones first."""
    out = []
    for mac, name in _devices("Paired"):
        info = _info(mac)
        if info["audio"]:
            out.append({"mac": mac, "name": info.get("Alias") or name, "connected": info.get("Connected") == "yes"})
    return sorted(out, key=lambda s: not s["connected"])


def connected():
    """The connected speaker, or None. Cheap enough for every radio play: only connected devices are asked."""
    for mac, name in _devices("Connected"):
        info = _info(mac)
        if info["audio"] and info.get("Paired") == "yes":
            return {"mac": mac, "name": info.get("Alias") or name, "connected": True}
    return None


def output():
    """(entity_id, spoken place) for the radio: the connected speaker, else the panel's own speaker."""
    if player():
        try:
            if s := connected():
                return player(), s["name"]
        except SpeakerError:
            pass
    return os.environ.get("JARVIS_RADIO_PLAYER"), "panelens högtalare"


def _pick(name, candidates):
    # "JBL-högtalaren", "JBL:en" and "the JBL speaker" all mean "jbl".
    filler = {"högtalaren", "högtalare", "en", "den", "the", "speaker", "bluetooth"}
    want = " ".join(w for w in re.sub(r"[-:.,!?]", " ", str(name or "").lower()).split() if w not in filler)
    if not want:
        return candidates[0] if len(candidates) == 1 else None
    for test in (lambda s: s["name"].lower() == want, lambda s: want in s["name"].lower(),
                 lambda s: all(w in s["name"].lower() for w in want.split())):
        if hits := [s for s in candidates if test(s)]:
            return hits[0]
    return None


def _wait_connected(mac, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if _info(mac).get("Connected") == "yes":
            return True
        time.sleep(0.5)
    return False


def connect(name=None):
    with SWITCH:
        return _connect(name)


def _connect(name=None):
    import radio
    paired = speakers()
    if not paired:
        return True, "Jarvis har inga parade Bluetooth-högtalare. Para den först (docs/bluetooth-speaker.md)."
    s = _pick(name, paired)
    if s is None:
        return True, "Vilken högtalare? Parade: " + ", ".join(p["name"] for p in paired) + "."
    if not s["connected"]:
        _ctl("power", "on")
        _ctl("connect", s["mac"], timeout=20)
        if not _wait_connected(s["mac"], 5):
            return True, f"{s['name']} svarar inte. Är den på och inom räckhåll?"
    for other in paired:            # one speaker at a time: the radio follows the one just chosen
        if other["connected"] and other["mac"] != s["mac"]:
            _ctl("disconnect", other["mac"])
    _settled["mac"] = s["mac"]
    moved = radio.follow_output()
    return False, f"{s['name']} är kopplad." + (f" {moved}" if moved else " Radion spelas där tills den kopplas från.")


def disconnect(name=None):
    """Disconnect, and a radio playing on the speaker goes on on the panel: the radio follows the output."""
    with SWITCH:
        return _disconnect(name)


def _disconnect(name=None):
    import radio
    on = [s for s in speakers() if s["connected"]]
    if name:
        on = [s for s in on if s is _pick(name, on)]
    if not on:
        return False, "Ingen Bluetooth-högtalare är kopplad. Ljudet kommer från panelen."
    moving = radio.on_speaker()
    radio.stop_speaker()
    for s in on:
        _ctl("disconnect", s["mac"])
    _settled["mac"] = None
    text = " och ".join(s["name"] for s in on) + " är frånkopplad."
    if moving:
        error, _ = radio.play(moving)
        return False, text + (f" {moving} gick inte att starta på panelen." if error else f" {moving} spelas nu på panelen.")
    return False, text + " Radion spelas på panelen nästa gång."


def _soloist_watch():
    """server/soloist/watch.py, which owns the PipeWire/Soloist heal (docs/spotify.md)."""
    import importlib.util
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "soloist", "watch.py")
    spec = importlib.util.spec_from_file_location("soloist_watch", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _heal_audio(watch, spotify_here, log, active=False):
    """After a reconnect: fresh PipeWire, WirePlumber, jbl45 and Soloist, so Spotify is heard again (the dead-PCM
    case in docs/spotify.md). Runs only with the speaker connected: PipeWire cannot start without its BlueALSA PCM
    (2026-11-01). Takes about 25 s, so it runs beside the HTTP answer."""
    if watch is None:
        log("omkopplad, men Soloist-vakten (server/soloist/watch.py) gick inte att läsa: ljudvägen är orörd")
        return
    try:
        watch.heal(spotify_here, activate=active)
        watch.hold_off(0)                   # from now on the watch looks for drops again
        found = watch.nodes()
        log(f"omkopplad: spotify={found.get('spotify')} jbl={found.get('jbl')}"
            f"{' (spelar vidare)' if spotify_here else ''}")
    except Exception as e:                  # a background thread: say why instead of dying silently
        log(f"omkopplingen kunde inte laga ljudet: {type(e).__name__}: {e}")


def _reconnect_link(s, log):
    """Drop the Bluetooth link and make it again; True once the speaker is connected. If the first connect fails,
    Jarvis's adapter is switched off and on before the second: 2026-11-01 13:46-13:49 every connect to the JBL
    failed with bluetoothd's "Operation already in progress" (also after Joel restarted the JBL), and only
    `bluetoothctl power off`/`power on` cleared it. Worst case about 55 s, inside Home Assistant's 75 s."""
    if s["connected"]:
        _ctl("disconnect", s["mac"])
        end = time.monotonic() + 5
        while time.monotonic() < end and _info(s["mac"]).get("Connected") == "yes":
            time.sleep(0.5)
        time.sleep(2)                       # let the speaker settle before it is asked again
    _ctl("power", "on")
    for attempt in (1, 2):
        if attempt == 2:
            log("omkoppling: startar om Jarvis Bluetooth-adapter")
            _ctl("power", "off")
            time.sleep(2)
            _ctl("power", "on")
            time.sleep(3)
        try:
            _ctl("connect", s["mac"], timeout=15)
        except SpeakerError as e:           # bluetoothctl timed out; the link may still come up below
            log(f"omkoppling: försök {attempt}: {e}")
        if _wait_connected(s["mac"], 5):
            return True
    return False


def reconnect(name=None, log=None, background=True):
    """POST /speaker/reconnect, the panel's Koppla om: disconnect the speaker and connect it again from Jarvis, then
    heal the sound path. Fixes the JBL that is connected but silent, and one that dropped off and did not come back
    (2026-11-01: Joel's phone held it). A radio playing there starts again; Spotify plays on only if it played
    here. (error, text)"""
    import radio
    log = log or (lambda text: print(f"speaker: {text}", file=sys.stderr, flush=True))
    try:
        with SWITCH:
            paired = speakers()
            if not paired:
                return True, "Jarvis har inga parade Bluetooth-högtalare. Para den först (docs/bluetooth-speaker.md)."
            s = _pick(name, paired) or (paired[0] if not name else None)
            if s is None:
                return True, "Vilken högtalare? Parade: " + ", ".join(p["name"] for p in paired) + "."
            try:
                watch = _soloist_watch()
                spotify_here = watch.playing()
                active = watch.active_here()
                watch.hold_off()            # before the disconnect: the watch must not heal this drop as well
            except Exception:
                watch, spotify_here, active = None, False, False
            channel = radio.on_speaker() if s["connected"] else None
            try:
                back = _reconnect_link(s, log)
            except SpeakerError:
                back = False
            if not back:
                log(f"omkoppling: {s['name']} svarade inte")
                if watch is not None:       # it may come back by itself: then the watch heals as after any drop
                    watch.hold_off(0, heal=spotify_here)
                return True, (f"{s['name']} svarar inte. Är den på, inom räckhåll och inte kopplad till en "
                              "telefon?")
            _settled["mac"] = s["mac"]
            log(f"omkoppling: {s['name']} kopplad igen (spotify här: {spotify_here}, radio: {channel or 'nej'})")
            if channel:
                radio.play(channel)
            else:
                radio.follow_output()
    except SpeakerError as e:
        return True, str(e)
    if background:
        threading.Thread(target=_heal_audio, args=(watch, spotify_here, log, active), daemon=True, name="speaker-heal").start()
    else:
        _heal_audio(watch, spotify_here, log, active)
    return False, f"{s['name']} är omkopplad. Ljudet kommer tillbaka om en halv minut."


class Watcher:
    """Follow a speaker switched on or off by hand (hallpanel.service runs it). The JBL reconnects to Jarvis by
    itself when switched on (seen 2026-10-27): a radio playing on the panel then moves to it. Switched off or out
    of range, the radio there stops instead of MPD playing into nothing; switching a speaker off means quiet.
    A change must hold for two checks, and one Jarvis made itself (connect/disconnect above) is left alone."""
    def __init__(self, log=None):
        self.log = log or (lambda text: print(f"speaker: {text}", file=sys.stderr, flush=True))
        self.seen = self.pending = self._read()

    @staticmethod
    def _read():
        s = connected()
        return (s["mac"], s["name"]) if s else None

    def check(self):
        try:
            now = self._read()
        except SpeakerError:
            return
        if now == self.seen or now != self.pending:
            self.pending = now                  # unchanged, or new: look once more before acting
            return
        import radio
        with SWITCH:
            before, self.seen = self.seen, now
            if _settled.pop("mac", "unset") == (now[0] if now else None):
                return                          # Jarvis switched it itself and already moved the radio
            if now:
                moved = radio.follow_output()
                self.log(f"{now[1]} kopplade upp sig. {moved or 'Ingen radio att flytta.'}")
            elif radio.playing_on_speaker():
                radio.stop_speaker()
                self.log(f"{before[1]} försvann. Radion där är stoppad.")
            else:
                self.log(f"{before[1]} försvann.")

    def run(self, stop, interval=2.0):
        while not stop.wait(interval):
            try:
                self.check()
            except Exception as e:              # a thread that dies silently would end the following for good
                self.log(f"bevakningen fick fel: {type(e).__name__}: {e}")


def watch(stop):
    """Start the Watcher in a daemon thread; for hallpanel.service."""
    try:
        watcher = Watcher()
    except SpeakerError:
        watcher = None
    if watcher is not None:
        threading.Thread(target=watcher.run, args=(stop,), daemon=True, name="speaker-watch").start()
    return watcher


def status():
    paired = speakers()
    if not paired:
        return False, "Jarvis har inga parade Bluetooth-högtalare."
    return False, "; ".join(f"{s['name']}: {'kopplad' if s['connected'] else 'inte kopplad'}" for s in paired) + "."


def _panel(name):
    words = " ".join(re.sub(r"[-:.,!?]", " ", str(name or "").lower()).split())
    return any(w in words for w in ("panel", "vanlig", "inbyggd", "hallpanel"))


def control(action="status", name=None):
    action = str(action or "status").lower()
    try:
        if action == "connect" and _panel(name):
            return disconnect()
        if action == "connect":
            return connect(name)
        if action == "disconnect":
            return disconnect(name)
        return status()
    except SpeakerError as e:
        return True, str(e)


def choices():
    """For the panel's Ljud ut setting (GET /speaker): {"available", "choice", "options"}, Panelen first."""
    if not configured():
        return {"available": False, "choice": PANEL, "options": [PANEL]}
    try:
        paired = speakers()
    except SpeakerError:
        return {"available": False, "choice": PANEL, "options": [PANEL]}
    on = next((s["name"] for s in paired if s["connected"]), PANEL)
    return {"available": True, "choice": on, "options": [PANEL, *(s["name"] for s in paired)]}


def choose(choice):
    """POST /speaker: Panelen disconnects the speakers, a speaker's name connects it. (error, text)"""
    return control("connect", choice)


def pair(name=None, seconds=20):
    """Find a speaker in pairing mode, then pair, trust and connect it. For setup from a terminal, not by voice:
    the scan alone takes `seconds`."""
    print(f"Söker i {seconds} s. Håll högtalarens Bluetooth-knapp tills den blinkar ...", flush=True)
    _ctl("power", "on")
    _ctl("--timeout", str(seconds), "scan", "on", timeout=seconds + 10)
    found = [{"mac": mac, "name": n} for mac, n in _devices() if _info(mac).get("Paired") != "yes"]
    found = [f for f in found if _info(f["mac"])["audio"]]
    s = _pick(name, found)
    if s is None:
        names = ", ".join(f["name"] for f in found) or "inga ljudenheter"
        return True, f"Hittade ingen entydig högtalare ({names}). Ange namnet: speaker.py pair jbl"
    # Jarvis's adapter starts with Pairable: no. Pairing then still says "Pairing successful", but stores no
    # keys (Bonded: no), and the speaker drops off after the first connection. Bond only for this pairing.
    _ctl("pairable", "on")
    try:
        out = _ctl("pair", s["mac"], timeout=30)
    finally:
        _ctl("pairable", "off")
    if _info(s["mac"]).get("Bonded") != "yes":
        return True, f"Parningen med {s['name']} sparade ingen nyckel (Bonded: no): {out.strip()[-300:]}"
    _ctl("trust", s["mac"])
    return control("connect", s["name"])


if __name__ == "__main__":
    action, *rest = sys.argv[1:] or ["status"]
    try:
        error, text = pair(" ".join(rest) or None) if action == "pair" else control(action, " ".join(rest) or None)
    except SpeakerError as e:
        error, text = True, str(e)
    print(("FEL: " if error else "") + text)
    sys.exit(1 if error else 0)
