#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10,<3.13"
# dependencies = ["pymicro-wakeword>=2.5,<3"]
# ///
"""Talk to Jarvis from the Mac the way the hallway panel will: say "Hey Jarvis", ask, hear the reply.

The Mac's microphone listens for "Hey Jarvis" with the model the panel runs (micro_wake_word, here
through pymicro-wakeword). After the wake word it plays a short sound and streams the microphone to
Home Assistant's Assist pipeline "Jarvis" while you talk, as the panel does; Home Assistant decides
when you are done (1.5 s of silence with Jarvis Cloud, at most 20 s here; the panel allows 60 s). It is exactly the path the panel uses: Home Assistant Cloud turns it into text, Home Assistant
handles its own commands, Luna on Jarvis answers the rest with the real calendar, and Mattias reads
the reply with trimmed pauses. The reply is streamed: the Mac starts playing it (with ffplay) as soon
as the first sentence is ready, as the panel's media player does. On the wake word it calls
jarvis_agent.warm, as the panel does. When the reply ends in a question, Home Assistant asks the panel
to keep listening (continue_conversation), so the loop listens again at once, without the wake word.

The Mac has no Home Assistant token, so it runs the pipeline on Jarvis over ssh (ha/ha_tool.py inside
the Home Assistant container). That adds about half a second the panel will not have; the timings
show it separately. --direct skips Home Assistant's pipeline and calls the server's /voice instead,
with the Mac recording until 0.8 s of silence first.

  uv run tools/voice_loop.py                3 questions, then stop
  uv run tools/voice_loop.py --turns 5
  uv run tools/voice_loop.py --enter        press Enter instead of saying "Hey Jarvis"
  uv run tools/voice_loop.py --file q.wav   send a recorded question instead of using the microphone
  uv run tools/voice_loop.py --mute         don't play the reply (only print it)
  uv run tools/voice_loop.py --stt groq     automatic Swedish/English, Groq Whisper on its free tier;
                                            Mattias for Swedish, Ryan for English (Cloud voices).
                                            Allows 1.5 s pauses and questions up to 60 s.
  uv run tools/voice_loop.py --stt groq --endpoint 2 --max-speech 90  longer pauses and questions
  uv run tools/voice_loop.py --direct       the server's /voice instead of Home Assistant's pipeline
  uv run tools/voice_loop.py --endpoint 0.5 the Mac decides you are done after 0.5 s of quiet, with Home
                                            Assistant's voice detection off (it always waits 0.7 s from here,
                                            plus the ssh hop). 0.5 is the chosen value: 0.3 cut sentences off
  uv run tools/voice_loop.py --mic-file x.wav   play a recording into the loop as if it were the
                                                microphone (tests wake word and silence detection)

It stops after --turns questions or --minutes minutes, whichever comes first.

Needs ffmpeg, and microphone access for the terminal app (macOS asks the first time).
"""
import argparse
import array
import base64
import collections
import io
import json
import math
import os
import queue
import shlex
import struct
import subprocess
import sys
import tempfile
import threading
import time
import wave
from datetime import datetime
from pathlib import Path

RATE, CHUNK = 16000, 320                 # 16 kHz mono 16-bit, 20 ms chunks
SSH = ["ssh", "-o", "ConnectTimeout=6", "-o", "BatchMode=yes", "-o", "ControlMaster=auto",
       "-o", f"ControlPath={Path.home()}/.ssh/cm-%C", "-o", "ControlPersist=10m"]
if sys.platform == "darwin":
    # Use the saved key passphrase even after ssh-agent has restarted/lost its identities.
    SSH += ["-o", "UseKeychain=yes"]
DING = str(Path(__file__).resolve().parent.parent / "assets" / "sounds" / "listen.wav")   # the panel's chime
with wave.open(DING) as _w:
    CHIME_S = _w.getnframes() / _w.getframerate()
SILENCE_END, NO_SPEECH, MAX_SPEECH = 0.8, 5.0, 20.0   # 20 s: long questions with several parts fit
GROQ_SILENCE_END, GROQ_MAX_SPEECH = 1.5, 60.0


def rms(chunk):
    a = array.array("h", chunk)
    return math.sqrt(sum(s * s for s in a) / len(a)) if a else 0.0


def as_wav(pcm):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)
    return buf.getvalue()


class Finished(Exception):
    pass


class Mic:
    """ffmpeg reads the microphone (or plays a file in real time); a thread puts (time, 10 ms chunk)
    on a queue and keeps the loudness of the last 3 s for the noise floor."""

    def __init__(self, device, file=None, deadline=None):
        source = ["-re", "-i", file] if file else ["-f", "avfoundation", "-i", f":{device}"]
        self.proc = subprocess.Popen(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", *source, "-ac", "1", "-ar", str(RATE),
             "-f", "s16le", "-"], stdout=subprocess.PIPE, stdin=subprocess.DEVNULL)
        self.file, self.deadline, self.started = file, deadline, time.monotonic()
        self.q, self.levels, self.heard = queue.Queue(), collections.deque(maxlen=300), False
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        while True:
            data = self.proc.stdout.read(CHUNK * 2)
            if not data:
                self.q.put((time.monotonic(), None))
                return
            level = rms(data)
            self.levels.append(level)
            self.heard = self.heard or level > 0
            self.q.put((time.monotonic(), data))

    def floor(self):
        """The quiet level of the room: the 20th percentile of the last 3 s."""
        lv = sorted(self.levels)
        return lv[len(lv) // 5] if lv else 0.0

    def get(self):
        t, data = self.q.get()
        if data is None:
            raise Finished("the recording ended" if self.file else "the microphone stopped")
        if self.deadline and t > self.deadline:
            raise Finished("time is up")
        if not self.heard and not self.file and t - self.started > 3:
            raise Finished("the microphone gives only silence. Give the terminal app microphone access: "
                           "System Settings > Privacy & Security > Microphone, then run again")
        return t, data

    def drain(self):
        while not self.q.empty():
            self.q.get_nowait()

    def close(self):
        self.proc.terminate()


def wait_for_wake(mic):
    """Block until "Hey Jarvis"."""
    from pymicro_wakeword import MicroWakeWord, MicroWakeWordFeatures, Model
    mww, feats = MicroWakeWord.from_builtin(Model.HEY_JARVIS), MicroWakeWordFeatures()
    while True:
        t, chunk = mic.get()
        for f in feats.process_streaming(chunk):
            if mww.process_streaming(f):
                return t


def record(mic, endpoint=SILENCE_END, ignore_s=0, max_speech=MAX_SPEECH):
    """Record until `endpoint` seconds of quiet or the time limit. Returns (pcm, last speech time)."""
    threshold = max(mic.floor() * 3.5, 300.0)   # 300 is about -40 dBFS
    pcm, started, loud_run, last_loud, t_start = [], False, 0, None, None
    while True:
        t, chunk = mic.get()
        t_start = t_start or t
        pcm.append(chunk)
        if t - t_start < ignore_s:
            continue                            # don't count the listening chime as a spoken question
        if rms(chunk) > threshold:
            loud_run += 1
            if loud_run >= 3:                    # 60 ms of sound in a row counts as speech
                started, last_loud = True, t
        else:
            loud_run = 0
        if not started and t - t_start > NO_SPEECH:
            return None, None
        if started and t - last_loud > endpoint:
            return b"".join(pcm), last_loud
        if t - t_start > max_speech:
            print(f"  Maxlängden {max_speech:g} s är nådd; skickar frågan nu.", flush=True)
            return b"".join(pcm), last_loud or t


HA_TOKEN = '"$(cat ~/.config/homeassistant/token)"'
SERVER = ". ~/.config/hallpanel/server.env && curl -s -m 90 -X POST -H \"X-Hallpanel-Token: $HALLPANEL_TOKEN\" "


def remote(host, command, body=b""):
    """Run a command on Jarvis through the shared ssh connection. Returns (stdout, seconds)."""
    t0 = time.monotonic()
    out = subprocess.run(SSH + [host, command], input=body, capture_output=True, timeout=120)
    if out.returncode != 0:
        raise RuntimeError(f"ssh exited {out.returncode}: {out.stderr.decode(errors='replace').strip()[:200]}")
    return out.stdout, time.monotonic() - t0


def prepare(args):
    """Open the ssh connection and put ha_tool.py in the Home Assistant container."""
    if args.direct:
        return remote(args.host, "true")[1]
    if args.stt == "groq":
        remote(args.host, "test -s ~/.config/hallpanel/groq.env || "
               "{ echo 'Groq-nyckeln saknas i ~/.config/hallpanel/groq.env på Jarvis' >&2; exit 1; }")
    return remote(args.host, "docker cp ~/hallpanel-live/ha/ha_tool.py homeassistant:/tmp/ha_tool.py")[1]


def warm(args):
    """What the panel does on the wake word; runs in the background."""
    if args.direct:
        cmd = SERVER + "-o /dev/null http://127.0.0.1:8765/warm"
    else:
        cmd = (f"curl -s -o /dev/null -X POST -H \"Authorization: Bearer {HA_TOKEN[1:-1]}\" "
               "http://127.0.0.1:8123/api/services/jarvis_agent/warm")
    subprocess.Popen(SSH + [args.host, cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def ask(args, wav, cid):
    if args.direct:
        cmd = (SERVER + f"--data-binary @- -H 'Content-Type: audio/wav' -H 'X-Conversation-Id: {cid or 'mac'}' "
               "http://127.0.0.1:8765/voice")
    else:
        cmd = ("{ printf '{\"ha_token\": \"%s\", \"conversation_id\": \"%s\"}\\n' " + HA_TOKEN + " "
               + shlex.quote(cid or "") + "; cat; } | docker exec -i homeassistant python /tmp/ha_tool.py ask")
    return remote(args.host, cmd, wav)


def read_exact(stream, n):
    data = b""
    while len(data) < n:
        part = stream.read(n - len(data))
        if not part:
            return None
        data += part
    return data


class Player:
    """ffplay reading from stdin, told the format so it starts at once instead of probing.

    The reply pauses while Jarvis's tools run, after the sentence it says first. A starved ffplay on
    macOS blocks its audio thread and loses the start of what comes after the pause, so a WAV stream
    is topped up with silence as it plays, never more than AHEAD_S ahead of the clock."""
    AHEAD_S = 0.1

    def __init__(self, first):
        fmt = "wav" if first[:4] == b"RIFF" else "mp3"
        self.proc = subprocess.Popen(["ffplay", "-nodisp", "-autoexit", "-loglevel", "error", "-fflags", "nobuffer",
                                      "-probesize", "32", "-analyzeduration", "0", "-f", fmt, "-i", "-"],
                                     stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.lock, self.closed = threading.Lock(), threading.Event()
        self.rate = self.frame = 0
        if fmt == "wav" and len(first) >= 44:          # the header of a stream of unknown length
            channels, self.rate = struct.unpack("<HI", first[22:28])
            self.frame = channels * struct.unpack("<H", first[34:36])[0] // 8
        self.start, self.queued = time.monotonic(), 0.0
        self._send(first, len(first) - 44)
        if self.rate and self.frame:
            threading.Thread(target=self._fill, daemon=True).start()

    def _send(self, data, audio_bytes):
        try:
            self.proc.stdin.write(data)
            self.proc.stdin.flush()
        except (BrokenPipeError, ValueError):
            return
        if self.rate and self.frame:
            self.queued += audio_bytes / (self.rate * self.frame)

    def write(self, data):
        with self.lock:
            self._send(data, len(data))

    def _fill(self):
        while not self.closed.wait(0.05):
            with self.lock:
                short = time.monotonic() - self.start + self.AHEAD_S - self.queued
                if short > 0:
                    silence = bytes(int(short * self.rate) * self.frame)
                    self._send(silence, len(silence))

    def close(self):
        """Let it play what it has, then wait for it to finish."""
        self.closed.set()
        with self.lock:
            try:
                self.proc.stdin.close()
            except BrokenPipeError:
                pass
        self.proc.wait()


class Transcript:
    """Prints what you said as soon as Home Assistant has it, and the reply as the agent writes it."""

    def __init__(self):
        self.said = self.replied = False

    def event(self, ev):
        if ev["type"] == "stt-end" and ev.get("text"):
            label = {"sv": "svenska", "en": "engelska"}.get(ev.get("language"))
            print(f"  Du:     {ev['text']}" + (f" ({label})" if label else ""), flush=True)
            self.said = True
        elif ev["type"] == "delta":
            text = ev["text"].replace("\n", "\n" + " " * 10)    # the sentence before the tools ends a line
            print(text if self.replied else f"  Jarvis: {text.lstrip()}", end="", flush=True)
            self.replied = True

    def finish(self, r):
        """Print whatever did not stream (a reply Home Assistant gave itself comes whole)."""
        if r.get("said") and not self.said:
            print(f"  Du:     {r['said']}")
        if self.replied:
            print()
        elif r.get("reply"):
            print(f"  Jarvis: {r['reply']}")


def groq_command(cid):
    """Read keys on Jarvis and pass them over an internal stdin pipe, never as process arguments."""
    bootstrap = (
        "import json,shutil,sys; from pathlib import Path; "
        "sys.path.insert(0,str(Path.home()/'hallpanel-live/server/agents')); "
        "from env_files import env_file; "
        "print(json.dumps({'jarvis_token':env_file('server.env').get('HALLPANEL_TOKEN'),"
        "'conversation_id':sys.argv[1]}),flush=True); "
        "shutil.copyfileobj(sys.stdin.buffer,sys.stdout.buffer)"
    )
    return ("python3 -c " + shlex.quote(bootstrap) + " " + shlex.quote(cid or "")
            + " | docker exec -i homeassistant python /tmp/ha_tool.py ask-groq")


def ask_streaming(args, wav, cid):
    """Run Home Assistant's pipeline and play the reply while it arrives.
    Returns (result, when the first audio arrived, the player still playing or None)."""
    cmd = ("{ printf '{\"ha_token\": \"%s\", \"conversation_id\": \"%s\", \"frames\": true}\\n' "
           + HA_TOKEN + " " + shlex.quote(cid or "") + "; cat; } | docker exec -i homeassistant python /tmp/ha_tool.py ask")
    if args.stt == "groq":
        cmd = groq_command(cid)
    proc = subprocess.Popen(SSH + [args.host, cmd], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE)
    proc.stdin.write(wav)
    proc.stdin.close()
    player, first_audio, result, transcript = None, None, None, Transcript()
    while (head := read_exact(proc.stdout, 5)) is not None:
        data = read_exact(proc.stdout, struct.unpack(">I", head[1:])[0]) or b""
        if head[:1] == b"A":
            first_audio = first_audio or time.monotonic()
            if not args.mute:
                if player:
                    player.write(data)
                else:
                    player = Player(data)
        elif head[:1] == b"E":
            transcript.event(json.loads(data))
        elif head[:1] == b"J":
            result = json.loads(data)
    if proc.wait() != 0 or result is None:
        raise RuntimeError(f"ssh exited {proc.returncode}: {proc.stderr.read().decode(errors='replace').strip()[:200]}")
    transcript.finish(result)
    return result, first_audio, player


def play(audio):
    suffix = ".wav" if audio[:4] == b"RIFF" else ".mp3"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        f.write(audio)
    try:
        subprocess.run(["afplay", f.name], check=False)
    finally:
        os.unlink(f.name)


def turn(args, cid, wav, ended):
    """Send one question, print what was heard and answered, play the reply, print the timings.
    Returns the conversation id to use for the next question."""
    sent = time.monotonic()
    if not args.direct:
        return turn_streaming(args, cid, wav, ended, sent)
    raw, trip = ask(args, wav, cid)
    r = json.loads(raw)
    if r.get("error"):
        print(f"  Home Assistant svarade med ett fel: {r['error']}\n")
        return cid
    if not r.get("said"):
        print("  Jarvis hörde inget tal i inspelningen.\n")
        return cid
    audio = base64.b64decode(r.get("audio_b64", ""))
    t = r.get("timing", {})
    reply_at = time.monotonic()
    print(f"  Du:     {r['said']}")
    print(f"  Jarvis: {r['reply']}")
    if r.get("handled_by") == "Home Assistant":
        print("          (Home Assistant svarade själv, utan agenten)")
    wait = (reply_at - ended) if ended else None
    hop = "ssh" if args.direct else "ssh och docker, finns inte i panelen"
    parts = [f"tal till text {t.get('stt_s')} s", f"{'agenten' if r.get('handled_by') != 'Home Assistant' else 'svar'} "
             f"{t.get('agent_s')} s", f"röst {t.get('tts_s')} s", f"{hop} {trip - t.get('server_total_s', 0):.2f} s"]
    if wait is not None:
        print(f"  Tid:    {wait:.1f} s från att du slutade prata till svaret ({SILENCE_END} s tystnad för att "
              f"veta att du var klar, sedan {reply_at - sent:.1f} s: " + ", ".join(parts) + ")")
    else:
        print(f"  Tid:    {reply_at - sent:.1f} s till svaret (" + ", ".join(parts) + ")")
    if t.get("speech_s"):
        print(f"          svaret är {t['speech_s']} s långt")
    print()
    if not args.mute and audio:
        play(audio)
    return r.get("conversation_id") or cid


def turn_live(args, cid, mic):
    """Stream the microphone to Home Assistant until it hears you are done, then play the reply as
    it streams back. Returns the conversation id for the next question, and whether Home Assistant
    wants the conversation to go on (the reply ended in a question)."""
    cmd = ("{ printf '{\"ha_token\": \"%s\", \"conversation_id\": \"%s\", \"frames\": true, \"live\": true"
           + (', \"no_vad\": true' if args.endpoint else "") + "}\\n' "
           + HA_TOKEN + " " + shlex.quote(cid or "") + "; cat; } | docker exec -i homeassistant python /tmp/ha_tool.py ask")
    proc = subprocess.Popen(SSH + [args.host, cmd], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, bufsize=0)
    seen, state, done, transcript = {}, {"player": None, "result": None}, threading.Event(), Transcript()

    def read_frames():
        while (head := read_exact(proc.stdout, 5)) is not None:
            data = read_exact(proc.stdout, struct.unpack(">I", head[1:])[0]) or b""
            if head[:1] == b"A":
                seen.setdefault("first_audio", time.monotonic())
                if not args.mute:
                    if state["player"]:
                        state["player"].write(data)
                    else:
                        state["player"] = Player(data)
            elif head[:1] == b"E":
                ev = json.loads(data)
                seen.setdefault(ev["type"], time.monotonic())
                transcript.event(ev)
                if "stt-vad-end" in seen or "stt-end" in seen:
                    done.set()
            elif head[:1] == b"J":
                state["result"] = json.loads(data)
        done.set()

    reader = threading.Thread(target=read_frames, daemon=True)
    reader.start()
    threshold, loud_run, ended, start = max(mic.floor() * 3.5, 300.0), 0, None, time.monotonic()
    # The chime plays as listening starts, and the microphone hears it: it is not speech. --endpoint
    # counts quiet only after 0.1 s of real speech, so a pause before you start never ends the turn.
    quiet_from, spoke = start + (0 if args.mute else CHIME_S), False
    try:
        while not done.is_set() and time.monotonic() - start < args.max_speech:
            t, chunk = mic.get()
            proc.stdin.write(chunk)
            if t < quiet_from:
                continue
            loud_run = loud_run + 1 if rms(chunk) > threshold else 0
            if loud_run >= 3:
                ended = t                       # the Mac's own guess at when you stopped talking
            spoke = spoke or loud_run >= 10
            if args.endpoint and spoke and t - ended >= args.endpoint:
                break                           # --endpoint: the Mac, not Home Assistant, says you are done
            if args.endpoint and not spoke and t - quiet_from > NO_SPEECH:
                break                           # nothing said: Home Assistant answers that it heard no text
    except BrokenPipeError:
        pass
    finally:
        try:
            proc.stdin.close()
        except BrokenPipeError:
            pass
    reader.join()
    if proc.wait() != 0 or state["result"] is None:
        raise RuntimeError(f"ssh exited {proc.returncode}: {proc.stderr.read().decode(errors='replace').strip()[:200]}")
    r, player = state["result"], state["player"]
    if (r.get("error") or {}).get("code") == "stt-no-text-recognized":
        print("  Jarvis hörde inget. Säg \"Hey Jarvis\" igen.\n")
    elif r.get("error"):
        print(f"  Home Assistant svarade med ett fel: {r['error']}\n")
    elif not r.get("said"):
        print("  Jarvis hörde inget tal.\n")
    else:
        transcript.finish(r)
        if r.get("handled_by") == "Home Assistant":
            print("          (Home Assistant svarade själv, utan agenten)")
        vad, stt, first = seen.get("stt-vad-end"), seen.get("stt-end"), seen.get("first_audio")
        if ended and stt and first:
            parts = ([f"Home Assistant hörde att du var klar efter {vad - ended:.1f} s"] if vad else
                     [f"Macen bestämde att du var klar efter {args.endpoint} s"] if args.endpoint else []) + [
                f"tal till text {stt - (vad or (ended + args.endpoint if args.endpoint else stt)):.2f} s",
                f"{'svar' if r.get('handled_by') == 'Home Assistant' else 'agenten'} till första ljudet {first - stt:.2f} s"
                + (" (strömmat)" if r.get("timing", {}).get("streamed") else "")]
            print(f"  Tid:    {first - ended:.1f} s från att du slutade prata till första ljudet (" + ", ".join(parts) + ")")
        print()
    if player:
        player.close()
    return r.get("conversation_id") or cid, bool(r.get("continue")) and bool(r.get("said"))


def turn_streaming(args, cid, wav, ended, sent):
    r, first_audio, player = ask_streaming(args, wav, cid)
    groq = args.stt == "groq"
    if r.get("error"):
        if player:
            player.close()
        print(f"  Röstkedjan svarade med ett fel: {r['error']}\n")
        return (cid, False) if groq else cid
    if not r.get("said"):
        print("  Jarvis hörde inget tal i inspelningen.\n")
        return (cid, False) if groq else cid
    t = r.get("timing", {})
    local = r.get("handled_by") == "Home Assistant"
    if local:
        print("          (Home Assistant svarade själv, utan agenten)")
    if first_audio:
        hop = (first_audio - sent) - t.get("first_audio_s", 0)
        parts = [f"{'Groq tal till text' if groq else 'tal till text'} {t.get('stt_s')} s",
                 f"{'svar' if local else 'agenten'} till första meningen {t.get('first_audio_s', 0) - t.get('stt_s', 0):.2f} s"
                 + (" (strömmat)" if t.get("streamed") else ""),
                 f"ssh och docker, finns inte i panelen {hop:.2f} s"]
        if ended:
            print(f"  Tid:    {first_audio - ended:.1f} s från att du slutade prata till första ljudet "
                  f"({args.endpoint if groq else SILENCE_END} s tystnad för att veta att du var klar, sedan " + ", ".join(parts) + ")")
        else:
            print(f"  Tid:    {first_audio - sent:.1f} s till första ljudet (" + ", ".join(parts) + ")")
    print()
    if player:
        player.close()
    next_cid = r.get("conversation_id") or cid
    return (next_cid, bool(r.get("continue"))) if groq else next_cid


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--turns", type=int, default=3)
    ap.add_argument("--enter", action="store_true", help="press Enter instead of saying Hey Jarvis")
    ap.add_argument("--file", help="send this recording instead of using the microphone")
    ap.add_argument("--mute", action="store_true", help="print the reply but don't play it")
    ap.add_argument("--minutes", type=float, default=5, help="stop after this many minutes")
    ap.add_argument("--mic-file", help="play this recording into the loop as if it were the microphone")
    ap.add_argument("--direct", action="store_true", help="the server's /voice instead of Home Assistant's pipeline")
    ap.add_argument("--stt", choices=("cloud", "groq"), default="cloud",
                    help="cloud: panel's HA pipeline with automatic Swedish/English; groq: same recognizer, bypass HA intents")
    ap.add_argument("--host", default=os.environ.get("JARVIS_SSH", "user@jarvis.local"))
    ap.add_argument("--endpoint", type=float, help="seconds of quiet after which the Mac ends your turn "
                    "(default 1.5 with --stt groq; Home Assistant's own voice detection off)")
    ap.add_argument("--max-speech", type=float, help="maximum seconds per question (60 with --stt groq, otherwise 20)")
    ap.add_argument("--device", default="0", help="avfoundation audio device (ffmpeg -f avfoundation -list_devices true -i '')")
    args = ap.parse_args()
    if args.stt == "groq" and args.direct:
        ap.error("--stt groq cannot be combined with --direct")
    if args.endpoint is not None and (not math.isfinite(args.endpoint) or args.endpoint <= 0):
        ap.error("--endpoint must be finite and greater than zero")
    if args.max_speech is not None and (not math.isfinite(args.max_speech) or args.max_speech <= 0):
        ap.error("--max-speech must be finite and greater than zero")
    if args.stt == "groq" and args.endpoint is None:
        args.endpoint = GROQ_SILENCE_END
    if args.max_speech is None:
        args.max_speech = GROQ_MAX_SPEECH if args.stt == "groq" else MAX_SPEECH
    sys.stdout.reconfigure(line_buffering=True)
    cid = f"mac-{datetime.now():%Y%m%d-%H%M%S}" if args.direct or args.stt == "groq" else None

    print("Ansluter till Jarvis …", end=" ", flush=True)
    took = prepare(args)
    print(f"klar ({took:.1f} s).")
    if args.stt == "groq":
        print("Groq-test: svenska/engelska automatiskt, Mattias/Ryan. Samma hjärna och verktyg på Jarvis; "
              "Home Assistants lokala kommandotolk används inte i detta läge.")
        print(f"Lyssnar tills du varit tyst i {args.endpoint:g} s, högst {args.max_speech:g} s per fråga.")

    if args.file:
        wav = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", args.file, "-ac", "1",
                              "-ar", str(RATE), "-sample_fmt", "s16", "-f", "wav", "-"],
                             check=True, capture_output=True).stdout
        warm(args)
        turn(args, cid, wav, None)
        return

    mic = Mic(args.device, args.mic_file, time.monotonic() + args.minutes * 60)
    follow = False                          # Jarvis asked something: listen again without the wake word
    try:
        for n in range(1, args.turns + 1):
            if not args.mic_file:
                mic.drain()
            if follow:
                print(f"[{n}/{args.turns}] (Jarvis väntar på ditt svar)", flush=True)
            elif args.enter:
                input(f"[{n}/{args.turns}] Tryck Enter och prata. ")
                mic.drain()
            else:
                print(f"[{n}/{args.turns}] Säg \"Hey Jarvis\" …", flush=True)
                wait_for_wake(mic)
            if not follow:
                warm(args)                  # the panel warms on the wake word only
            if not args.mute:
                subprocess.Popen(["afplay", DING])
            print("  (lyssnar)", flush=True)
            if args.stt == "groq":
                pcm, ended = record(mic, endpoint=args.endpoint, ignore_s=0 if args.mute else CHIME_S,
                                    max_speech=args.max_speech)
                if pcm is None:
                    print("  Hörde ingen fråga. Säg \"Hey Jarvis\" igen.\n")
                    follow = False
                    continue
                cid, follow = turn_streaming(args, cid, as_wav(pcm), ended, time.monotonic())
                continue
            if not args.direct:
                cid, follow = turn_live(args, cid, mic)
                continue
            pcm, ended = record(mic, max_speech=args.max_speech)
            if pcm is None:
                print("  Hörde ingen fråga. Säg \"Hey Jarvis\" igen.\n")
                continue
            cid = turn(args, cid, as_wav(pcm), ended)
    except Finished as e:
        print(f"Slut: {e}.")
    except KeyboardInterrupt:
        print()
    finally:
        mic.close()


if __name__ == "__main__":
    main()
