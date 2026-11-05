"""Speech in and out for the hallway panel, through Home Assistant on Jarvis. Standard library, plus numpy
(server/requirements.txt) for the AI tone; without numpy the tone runs ffmpeg as before.

  stt(pcm)      Swedish speech to text with Home Assistant Cloud (stt.home_assistant_cloud)
  tts(text)     text to speech with Home Assistant Cloud, voice MattiasNeural, as 16 kHz mono WAV
                (the authenticated Jarvis HA view requests native PCM16, so no format conversion is needed)
  trim(wav)     shorten Mattias's long pauses: silences over 0.25 s become 0.3 s, and leading and
                trailing silence goes. Joel preferred this over the untouched voice (2026-10-21).
  ai_tone(wav)  Jarvis's "AI tone", Joel's choice of 2026-10-21 (variant G2 of a listening test): a light
                flanger and two short echoes at Mattias's own pitch, raised 9.6 dB back to the untouched
                voice's loudness (-20 LUFS); the echo adds its own 70 ms tail. Since 2026-10-25 ffmpeg's
                filter chain (AI_TONE) is computed in numpy, matching ffmpeg within 8 PCM levels at isolated
                limiter breakpoints (tests/test_voice.py): 6 ms for a 1 s sentence and 17 ms for 3 s
                on Jarvis, where starting ffmpeg cost 100 ms a sentence. Without numpy it runs ffmpeg;
                without both, or with JARVIS_VOICE_EFFECT=none, the voice is left as it is.
                JARVIS_VOICE_TONE=ffmpeg forces ffmpeg, to compare the two by ear.
  speech(text)  the finished voice for /speak: tts, trim and ai_tone, with a small cache so a sentence
                Jarvis says often ("God morgon!", "Självklart.", a reminder) costs no cloud call the second
                time. Only the cloud voice is cached, never Piper's.

Without the internet (2026-10-22): when Home Assistant Cloud does not answer within 8 s, tts() reads the text
with Piper on Jarvis instead (voice sv_SE-nst-medium, the offline fallback chosen on 2026-10-21) and keeps using
Piper for a minute before it tries the cloud again. Piper runs in this process: the piper-tts package from
server/requirements.txt and the voice in ~/.local/share/hallpanel/piper (JARVIS_PIPER_VOICE), which
tools/deploy_jarvis.sh downloads. It loads on first use in about 3 s, then takes 0.2-0.6 s a sentence on Jarvis,
and its 22.05 kHz is resampled to Mattias's 16 kHz, so both can share one reply. So the panel can still speak
reminders, alarms and "the net is down" while speech to text and the agent, which need the internet, cannot work.

Settings: Home Assistant's address and token as in agents/hass.py (HA_URL, HA_TOKEN_FILE), TTS_VOICE (default MattiasNeural), VOICE_LANGUAGE (default sv-SE),
TTS_VOICE_EN and TTS_LANGUAGE_EN for English (default RyanNeural, en-GB), JARVIS_VOICE_EFFECT (ai or none),
JARVIS_VOICE_TONE (numpy or ffmpeg), JARVIS_PIPER_VOICE (default ~/.local/share/hallpanel/piper/sv_SE-nst-medium.onnx).
"""
import array
import collections
import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path

try:
    import numpy as np
except ImportError:                     # the tone then needs ffmpeg
    np = None

sys.path.insert(0, str(Path(__file__).resolve().parent / "agents"))
import hass  # noqa: E402

VOICE = os.environ.get("TTS_VOICE", "MattiasNeural")
VOICE_EN = os.environ.get("TTS_VOICE_EN", "RyanNeural")         # British; American Andrew for a few hours on 2026-10-23
LANGUAGE_EN = os.environ.get("TTS_LANGUAGE_EN", "en-GB")
LANGUAGE = os.environ.get("VOICE_LANGUAGE", "sv-SE")
EFFECT = os.environ.get("JARVIS_VOICE_EFFECT", "ai")
TONE = os.environ.get("JARVIS_VOICE_TONE", "numpy")
PIPER_VOICE = Path(os.environ.get("JARVIS_PIPER_VOICE", "~/.local/share/hallpanel/piper/sv_SE-nst-medium.onnx")).expanduser()
CLOUD_TIMEOUT, CLOUD_PAUSE = 8, 60      # seconds to wait for the cloud's voice, and to skip it after a failure
AI_TONE = ("flanger=delay=2:depth=1.5:regen=-20:width=50:speed=0.3,aecho=0.8:0.5:35|70:0.2|0.12,"
           "volume=9.6dB,alimiter=limit=0.89")
# The same chain as numbers for the numpy version; what AI_TONE leaves out is ffmpeg's default.
FLANGER = {"delay_ms": 2, "depth_ms": 1.5, "regen_pct": -20, "width_pct": 50, "speed_hz": 0.3}
ECHO = {"in_gain": 0.8, "out_gain": 0.5, "delays_ms": (35, 70), "decays": (0.2, 0.12)}
VOLUME_DB = 9.6
LIMITER = {"limit": 0.89, "attack_ms": 5, "release_ms": 50}
SPEECH_CACHE, SPEECH_CACHE_MAX_BYTES = 64, 400_000      # finished sentences kept, and the longest kept (12 s)
_warned = []
_cloud_down_until = [0.0]
_piper, _piper_lock = [], threading.Lock()
_speech_cache, _speech_lock = collections.OrderedDict(), threading.Lock()


def _request(path, data=None, headers=None, method=None, timeout=60):
    return hass.request(path, data, headers, method, timeout)


def pcm_of(wav_bytes):
    """Raw 16 kHz mono 16-bit samples from a WAV file (the panel and the Mac send that format)."""
    with wave.open(io.BytesIO(wav_bytes)) as w:
        if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (16000, 1, 2):
            raise ValueError("send 16 kHz mono 16-bit WAV")
        return w.readframes(w.getnframes())


def stt(pcm):
    """Swedish text from raw 16 kHz mono 16-bit audio."""
    body = _request("/api/stt/stt.home_assistant_cloud", data=pcm, headers={
        "X-Speech-Content": f"format=wav; codec=pcm; sample_rate=16000; bit_rate=16; channel=1; language={LANGUAGE}",
        "Content-Type": "application/octet-stream"})
    result = json.loads(body)
    return (result.get("text") or "").strip() if result.get("result") == "success" else ""


def speakable(text):
    """Text as Mattias should read it. Some models write times with a narrow no-break space
    ("9\u202f55", "15\u202f00"); make those "9.55" and every other odd space a plain one. A dash between
    words ("kroppen — rätt ambitiöst", "kroppen—rätt") becomes a comma, where Mattias pauses a little; he
    hardly pauses at the dash itself (Joel's wish, 2026-10-23). Ranges such as "10–12" keep theirs."""
    text = re.sub(r"(\d)[\u202f\u00a0](\d\d)(?!\d)", r"\1.\2", text)
    text = re.sub(r"[\u202f\u00a0\u2009]", " ", text)
    text = re.sub(r"(?<!\d)\s+[\u2014\u2013-]\s+|\s+[\u2014\u2013-]\s+(?!\d)|(?<=[^\W\d])\u2014(?=[^\W\d])", ", ", text)
    return re.sub(r",\s*,", ",", text)


# "JBL Charge 4" read by the Swedish voice sounded ugly (Joel, 2026-10-28), and since 2026-10-30 Swedish speech
# says just "högtalaren", never "JBL:en" or "JBL-högtalaren", wherever the name comes from (the speaker tool, the
# radio, the model). English speech and the screen keep the name.
SPEAKER_NAMES = re.compile(r"\bJBL(?:[\s-]*Charge(?:[\s-]*\d+)?)?(?:[\s-]*högtalare(n)?|(:en))?(s|:s)?(?![\w])"
                           r"|\bCharge[\s-]*\d+(:s)?(?![\w])", re.I)


def _speaker_word(m):
    indefinite = "högtalare" in m.group(0).lower() and not m.group(1)   # "en JBL-högtalare"
    word = ("högtalare" if indefinite else "högtalaren") + ("s" if m.group(3) or m.group(4) else "")
    before = m.string[:m.start()].rstrip()
    return word[0].upper() + word[1:] if not before or before[-1] in ".!?" else word


def spoken_names(text):
    """Swedish speech only: the speaker by what it is, "högtalaren", never its brand or model name."""
    text = SPEAKER_NAMES.sub(_speaker_word, text)
    return re.sub(r"\b([Hh]ögtalaren)(?:\s+högtalaren)+\b", r"\1", text)


# The laundry's passes are written as on the booking site, "06-09", and said "nollsex till nollnio" (Joel,
# 2026-11-01). Only two-digit hours 00-24 on both sides of the dash, never inside a date ("2026-11-01").
HOUR_RANGE = re.compile(r"(?<![\d\-\u2013])(?<!\d[.:,])([0-2]\d)\s*[-\u2013]\s*([0-2]\d)(?![\d\-\u2013]|[.:,]\d)")
DIGITS = ("noll", "ett", "två", "tre", "fyra", "fem", "sex", "sju", "åtta", "nio")


def _hour_word(h):
    return "noll" + DIGITS[int(h[1])] if h[0] == "0" else h


def spoken_hours(text):
    """Swedish speech only: "06-09" as "nollsex till nollnio" and "12-15" as "12 till 15"; the screen keeps "06-09"."""
    return HOUR_RANGE.sub(lambda m: f"{_hour_word(m[1])} till {_hour_word(m[2])}"
                          if int(m[1]) <= 24 and int(m[2]) <= 24 else m[0], text)


def tts(text, language=None):
    """WAV bytes (16 kHz mono 16-bit) of text read by Mattias, or by Piper when the cloud does not answer."""
    return _tts(text, language)[0]


def _tts(text, language=None):
    """(WAV, "cloud" or "piper")."""
    if language == "en":
        # The installed Piper voice is Swedish. Never read English with that fallback.
        return cloud_tts(text, language="en"), "cloud"
    if time.monotonic() >= _cloud_down_until[0]:
        try:
            return cloud_tts(text), "cloud"
        except (OSError, ValueError, KeyError) as e:          # URLError, HTTPError and timeouts are OSErrors
            _cloud_down_until[0] = time.monotonic() + CLOUD_PAUSE
            print(f"voice: Home Assistant Cloud did not speak ({e}); Piper for {CLOUD_PAUSE} s", file=sys.stderr)
    wav = piper_tts(text)
    if wav is None:
        raise OSError("no voice: Home Assistant Cloud failed and Piper is not installed")
    return wav, "piper"


def speech(text, language=None, timing=None):
    """The finished voice for one sentence: tts, trim and ai_tone. (WAV, source) with source "cloud", "piper"
    or "cache". A sentence Jarvis says often costs no cloud call the second time: the day's first "God morgon!",
    the butler's "Självklart." and "Ordnat.", a reminder's "Om 10 minuter: ...". Only the cloud voice is cached,
    so Piper's fallback gives way to Mattias again when the net is back; the key holds the voice and the effect,
    and the cache lives in the process, so a new voice or a deploy starts it empty."""
    started = time.monotonic()
    if timing is None:
        timing = {}
    timing.update(tts_ms=0, trim_ms=0, tone_ms=0, total_ms=0)
    key = ("en" if language == "en" else "sv", VOICE_EN if language == "en" else VOICE, EFFECT, TONE, text)
    with _speech_lock:
        wav = _speech_cache.get(key)
        if wav is not None:
            _speech_cache.move_to_end(key)
            timing["total_ms"] = round((time.monotonic() - started) * 1000)
            return wav, "cache"
    wav, source = _tts(text, language)
    synthesized = time.monotonic()
    wav = trim(wav)
    trimmed = time.monotonic()
    wav = ai_tone(wav)
    finished = time.monotonic()
    timing.update(tts_ms=round((synthesized - started) * 1000), trim_ms=round((trimmed - synthesized) * 1000),
                  tone_ms=round((finished - trimmed) * 1000), total_ms=round((finished - started) * 1000))
    if source == "cloud" and len(wav) <= SPEECH_CACHE_MAX_BYTES:
        with _speech_lock:
            _speech_cache[key] = wav
            while len(_speech_cache) > SPEECH_CACHE:
                _speech_cache.popitem(last=False)
    return wav, source


def cloud_tts(text, language=None):
    """Same HA Cloud voice, native PCM16 wrapped in WAV; no ffmpeg or proxy download."""
    body = json.dumps({"message": speakable(text if language == "en" else spoken_hours(spoken_names(text))),
                       "language": LANGUAGE_EN if language == "en" else LANGUAGE,
                       "options": {"voice": VOICE_EN if language == "en" else VOICE}}).encode()
    return _request("/api/jarvis_agent/tts", data=body, headers={"Content-Type": "application/json"}, timeout=CLOUD_TIMEOUT)


def load_piper():
    """The Piper voice, loaded once (about 3 s on Jarvis); None without piper-tts or the voice file."""
    with _piper_lock:
        if not _piper:
            try:
                from piper import PiperVoice
            except ImportError:
                return None
            if not PIPER_VOICE.is_file():
                return None
            _piper.append(PiperVoice.load(str(PIPER_VOICE)))
        return _piper[0]


def piper_tts(text):
    """WAV bytes (16 kHz mono 16-bit) of text read by Piper, or None when Piper is not there."""
    voice = load_piper()
    if voice is None:
        return None
    buf = io.BytesIO()
    with _piper_lock, wave.open(buf, "wb") as w:            # one sentence at a time through the model
        voice.synthesize_wav(speakable(spoken_hours(spoken_names(text))), w)     # Piper is the Swedish voice
    with wave.open(io.BytesIO(buf.getvalue())) as w:
        params, frames = w.getparams(), w.readframes(w.getnframes())
    return _wav(resample(frames, params.framerate, 16000), params._replace(framerate=16000))


def resample(frames, rate, to):
    """16-bit mono samples from rate to `to` Hz by linear interpolation; plenty for a fallback voice."""
    if rate == to:
        return frames
    src = array.array("h", frames)
    n = int(len(src) * to / rate)
    out = array.array("h", bytes(2 * n))
    step = rate / to
    for i in range(n):
        x = i * step
        j = int(x)
        a = src[j]
        b = src[j + 1] if j + 1 < len(src) else a
        out[i] = int(a + (b - a) * (x - j))
    return out.tobytes()


def trim(wav_bytes, threshold_db=-35.0, long_pause=0.25, keep_pause=0.3, keep_start=0.03, keep_end=0.05):
    """Shorten pauses in a 16-bit WAV. Works on 10 ms windows; a window is silent when its RMS is
    below threshold_db (dBFS). Interior silences longer than long_pause keep keep_pause seconds."""
    with wave.open(io.BytesIO(wav_bytes)) as w:
        params, frames = w.getparams(), w.readframes(w.getnframes())
    if params.sampwidth != 2:
        return wav_bytes
    step = max(1, params.framerate // 100) * params.nchannels          # 10 ms
    limit = 32768 * 10 ** (threshold_db / 20)
    if np is not None and frames:
        samples = np.frombuffer(frames, dtype="<i2").astype(np.int64)
        starts = np.arange(0, len(samples), step)
        counts = np.minimum(step, len(samples) - starts)
        silent = (np.sqrt(np.add.reduceat(samples * samples, starts) / counts) < limit).tolist()
    else:
        samples = array.array("h", frames)
        silent = []
        for i in range(0, len(samples), step):
            win = samples[i:i + step]
            silent.append(math.sqrt(sum(s * s for s in win) / len(win)) < limit if win else True)
    win_s = step / params.nchannels / params.framerate
    keep = [True] * len(silent)
    i = 0
    while i < len(silent):
        if not silent[i]:
            i += 1
            continue
        j = i
        while j < len(silent) and silent[j]:
            j += 1
        run = j - i
        if i == 0:
            budget = int(keep_start / win_s)
        elif j == len(silent):
            budget = int(keep_end / win_s)
        elif run * win_s > long_pause:
            budget = int(keep_pause / win_s)
        else:
            budget = run
        lead = budget // 2
        for k in range(i, j):
            if i == 0:
                keep[k] = k >= j - budget                              # keep the end of leading silence
            elif j == len(silent):
                keep[k] = k < i + budget                               # keep the start of trailing silence
            else:
                keep[k] = k < i + lead or k >= j - (budget - lead)     # keep both edges of a pause
        i = j
    out = b"".join(frames[n * step * 2:(n + 1) * step * 2] for n, k in enumerate(keep) if k)
    return _wav(out, params)


def _wav(frames, params):
    """A WAV with a correct length (Home Assistant streams WAV with an unknown length in the header)."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(params.nchannels)
        w.setsampwidth(params.sampwidth)
        w.setframerate(params.framerate)
        w.writeframes(frames)
    return buf.getvalue()


def duration(wav_bytes):
    with wave.open(io.BytesIO(wav_bytes)) as w:
        frames = w.readframes(w.getnframes())
        return len(frames) / (w.getsampwidth() * w.getnchannels() * w.getframerate())


def ai_tone(wav_bytes):
    """The AI tone on a 16-bit WAV (see the top of the file): in numpy when it is installed and the sound is
    mono, as the voice always is, else through ffmpeg."""
    if EFFECT != "ai":
        return wav_bytes
    with wave.open(io.BytesIO(wav_bytes)) as w:
        params, frames = w.getparams(), w.readframes(w.getnframes())
    if params.sampwidth != 2:
        return wav_bytes
    if np is not None and params.nchannels == 1 and TONE != "ffmpeg":
        return _wav(_tone_numpy(frames, params.framerate), params)
    out = _tone_ffmpeg(frames, params.framerate, params.nchannels)
    return wav_bytes if out is None else _wav(out, params)


def _tone_ffmpeg(frames, rate, channels):
    """AI_TONE through ffmpeg: raw 16-bit samples both ways, since a WAV written to a pipe has no length in
    its header. None when ffmpeg is missing or fails."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        if not _warned:
            _warned.append(True)
            print("voice: neither numpy nor ffmpeg on this machine, so no AI tone (pip install numpy)", file=sys.stderr)
        return None
    raw = ["-f", "s16le", "-ar", str(rate), "-ac", str(channels)]
    try:
        return subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", *raw, "-i", "pipe:0", "-af", AI_TONE,
                               *raw, "pipe:1"], input=frames, capture_output=True, timeout=10, check=True).stdout
    except (OSError, subprocess.SubprocessError) as e:
        print(f"voice: the AI tone failed, plain voice instead: {e}", file=sys.stderr)
        return None


# AI_TONE in numpy. Each step follows the ffmpeg 7.1 source it names (libavfilter/af_*.c) in its order of
# operations and its float32 details. tests/test_voice.py checks lengths and a <=8-level PCM tolerance at
# isolated limiter breakpoints. Samples are doubles in -1..1, as inside ffmpeg's filter graph.

def _tone_numpy(frames, rate):
    """Raw 16-bit mono samples in, the same out, 70 ms longer for the echo's tail."""
    x = np.frombuffer(frames, dtype="<i2").astype(np.float64) * (1.0 / 32768)
    y = _limiter(_volume(_echo(_flanger(x, rate), rate)), rate)
    return np.clip(np.rint(y * 32768), -32768, 32767).astype("<i2").tobytes()


def _flanger(x, rate):
    """af_flanger: the signal mixed with a copy 2-3.5 ms behind, the delay swept by a 0.3 Hz sine, and 20 % of
    the delayed copy fed back into the delay line. The feedback ties every sample to the one before it, so rather
    than a loop over the samples the whole signal is iterated to its fixed point: each round is a few numpy
    operations, and 20 % feedback settles below 1e-13 in about 19 rounds."""
    feedback = FLANGER["regen_pct"] / 100.
    width = FLANGER["width_pct"] / 100.
    in_gain = 1 / (1 + width)
    delay_gain = width / (1 + width) * (1 - abs(feedback))
    delay_min, delay_depth = FLANGER["delay_ms"] / 1000., FLANGER["depth_ms"] / 1000.
    max_samples = int((delay_min + delay_depth) * rate + 2.5)
    length = int(rate / FLANGER["speed_hz"])
    # the sweep, a float32 table as ffmpeg keeps it, from the shortest delay (phase 3π/2) up to max_samples - 2
    lo, hi = float(round(delay_min * rate)), max_samples - 2.
    point = (np.arange(length) + int(0.75 * length + 0.5)) % length
    table = (((np.sin(point / length * 2 * math.pi) + 1) / 2) * (hi - lo) + lo).astype(np.float32).astype(np.float64)
    n = len(x)
    idx = np.arange(n)
    lfo = table[idx % length]
    whole = lfo.astype(np.int64)
    frac = lfo - whole
    i0 = idx - whole + max_samples              # into the signal with max_samples of silence in front of it
    i1 = i0 - 1
    front = np.zeros(max_samples)

    def delayed(w):
        """The delay line read between two samples, interpolated as ffmpeg does (linear)."""
        wp = np.concatenate((front, w))
        w0, w1 = wp[i0], wp[i1]
        return w0 + (w1 - w0) * frac

    w = x
    for _ in range(40):
        d = delayed(w)
        w_next = x + np.concatenate(((0.0,), d[:-1])) * feedback
        settled = n == 0 or np.max(np.abs(w_next - w)) < 1e-13
        w = w_next
        if settled:
            break
    return x * in_gain + delayed(w) * delay_gain


def _echo(y, rate):
    """af_aecho: 80 % of the signal plus copies 35 and 70 ms back at 20 % and 12 %, all at half gain, and 70 ms
    of tail so the last echo is heard. ffmpeg holds these gains as float32, so they are float32 here."""
    f32 = lambda v: float(np.float32(v))                         # noqa: E731
    taps = [(int(f32(ms) * rate / 1000.0), f32(decay)) for ms, decay in zip(ECHO["delays_ms"], ECHO["decays"])]
    src = np.concatenate((y, np.zeros(max(samples for samples, _ in taps))))
    out = src * f32(ECHO["in_gain"])
    for samples, decay in taps:
        out = out + np.concatenate((np.zeros(samples), src[:-samples])) * decay
    return out * f32(ECHO["out_gain"])


def _volume(y):
    """af_volume works in float32 unless told otherwise, so the gain and the samples are float32 here."""
    gain = np.float32(10 ** (VOLUME_DB / 20))
    return (y.astype(np.float32) * gain).astype(np.float64)


def _limiter(x, rate):
    """af_alimiter (Calf's lookahead limiter) with its defaults: 5 ms of lookahead, a linear attack over it, a
    50 ms release, no ASC, and "level" on, which scales the limited signal by 1/limit. The output runs 79 samples
    behind the input and the last 79 never come out, as ffmpeg leaves them with latency=0. The gain is 1 and
    nothing changes until a sample is over the limit, so the per-sample loop ported from C runs only where a
    sample is over the limit, where a planned gain is reached, or where a release ends; the stretches between,
    where the gain changes by a constant each sample, are filled in with numpy."""
    limit = LIMITER["limit"]
    n = len(x)
    lookahead = int(rate * (LIMITER["attack_ms"] / 1000.) * 1)
    release = rate * (LIMITER["release_ms"] / 1000.)
    level = 1 / limit
    lag = lookahead - 1
    late = np.concatenate((np.zeros(lag), x[:n - lag])) if n > lag else np.zeros(n)
    over = np.flatnonzero(np.abs(x) > limit)
    gain = np.empty(n)
    att, delta = 1.0, 0.0
    nextpos, nextabs, nextdelta = [-1] * lookahead, [0] * lookahead, [0.0] * lookahead
    nextiter = nextlen = 0
    inf = float("inf")
    o = i = 0
    while i < n:
        # the next sample where something happens; up to it the gain only slides by delta
        e_next = over[o] if o < len(over) else n
        a_next = nextabs[nextiter] + lag if nextpos[nextiter] >= 0 else n
        r_next = n
        if delta > 0:
            r_next = i + max(int((1.0 - 1e-13 - att) / delta) + 1, 1) - 1
        elif delta < 0 and att + delta <= 0:
            r_next = i
        nxt = min(e_next, a_next, r_next, n)
        if nxt > i:
            m = nxt - i
            if delta == 0.0:
                gain[i:nxt] = att
            else:
                gain[i:nxt] = att + delta * np.arange(1, m + 1)
                att += delta * m
            i = nxt
            continue
        # one turn of ffmpeg's loop, as written in C
        pos = i % lookahead
        peak = abs(x[i])
        if peak > limit:
            patt = min(limit / peak, 1.0)
            rdelta = (1.0 - patt) / release
            d = (limit / peak - att) / lookahead * 1
            if d < delta:
                delta = d
                nextpos[0], nextabs[0], nextpos[1] = pos, i, -1
                nextdelta[0] = rdelta
                nextlen, nextiter = 1, 0
            else:
                found = False
                k = nextiter
                for k in range(nextiter, nextiter + nextlen):
                    j = k % lookahead
                    ppeak = abs(x[nextabs[j]]) if nextpos[j] >= 0 else 0.0
                    gap = (lookahead - nextpos[j] + pos) % lookahead
                    target = limit / ppeak if ppeak else inf
                    pdelta = (limit / peak - target) / gap if gap else (-inf if limit / peak < target else inf)
                    if pdelta < nextdelta[j]:
                        nextdelta[j] = pdelta
                        found = True
                        break
                if found:
                    nextlen = k - nextiter + 1
                    slot = (nextiter + nextlen) % lookahead
                    nextpos[slot], nextabs[slot], nextdelta[slot] = pos, i, rdelta
                    nextpos[(nextiter + nextlen + 1) % lookahead] = -1
                    nextlen += 1
        if o < len(over) and over[o] == i:
            o += 1
        peak = abs(late[i])
        att += delta
        gain[i] = att
        if (pos + 1) % lookahead == nextpos[nextiter]:
            delta = nextdelta[nextiter]
            att = limit / peak if peak else inf
            nextlen -= 1
            nextpos[nextiter] = -1
            nextiter = (nextiter + 1) % lookahead
        if att > 1.0:
            att, delta, nextiter, nextlen = 1.0, 0.0, 0, 0
            nextpos[0] = -1
        if att <= 0.0:
            att = 0.0000000000001
            delta = (1.0 - att) / release
        if att != 1.0 and (1.0 - att) < 0.0000000000001:
            att = 1.0
        if delta != 0.0 and abs(delta) < 0.00000000000001:
            delta = 0.0
        i += 1
    return np.clip(late * gain, -limit, limit) * level * 1.0
