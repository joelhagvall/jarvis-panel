"""Split a reply that arrives in pieces into sentences, so the first one can be spoken while the
agent is still writing, and glue the spoken sentences into one WAV stream. No Home Assistant
imports, so it can be tested on its own (tests/test_speech.py)."""

from collections.abc import AsyncIterator
import asyncio
import contextlib
from contextvars import ContextVar
import io
import re
import wave

# Words that end with a period without ending the sentence ("kl. 10.20").
ABBREVIATIONS = {"kl", "t.ex", "bl.a", "ca", "nr", "st", "dvs", "osv", "o.s.v", "m.m", "s.k", "p.g.a", "jfr", "resp",
                 "mr", "mrs", "ms", "dr", "prof", "e.g", "i.e", "etc", "vs", "a.m", "p.m"}
END = re.compile(r"[.!?…]+[\"”»)]?(\s+)")
CLAUSE = re.compile(r"[,:;](\s+)")
PAUSE_S = 0.25   # silence between sentences; with what the trim keeps it is about 0.3 s, as Joel chose
CLAUSE_PAUSE_S = 0.1   # after a clause spoken on its own (FIRST_CLAUSE_WORDS), shorter than between sentences
# The panel's player (ESPHome's speaker media player, INITIAL_BUFFER_MS in audio_pipeline.cpp) starts only once
# this much of the stream has arrived. A first sentence shorter than that is followed by silence up to it, which
# counts towards the pause before the next sentence: "Klart!" is heard the moment it is ready, not once the next
# sentence is ready too. 0 turns it off.
PRIME_S = 1.0
# Nothing of a stream reaches the panel's speaker until two buffers fill up on the way. Home Assistant converts
# every TTS stream for the panel with ffmpeg (the satellite asks for a preferred format), and ffmpeg 8.1 writes
# nothing until more than 64 KiB arrived on its stdin; then it passes the rest within about 2 KiB (measured in the
# HA container 2026-10-30 with tools/ha_ffmpeg_hold.py, any WAV header; rerun it after an HA update). Behind it the panel's audio reader hands on only full
# 24 KiB reads and starts playing at 1 s. A 1 s first sentence therefore waited for the next sentences: "Hur mår
# du?" played 1.37-1.7 s after its first sentence was ready (the panel's "Reply first audio out" line). While
# more speech is coming, top_up() sends the rest as silence at once, so the first sentence plays as soon as it is
# ready and the pause after a short one grows to at most about 1.1 s (Joel's choice, 2026-10-30).
PANEL_START_BYTES = 64 * 1024 + 2048
# The same two holds cut a sentence's end when nothing follows it for a while: "Självklart, jag tittar på
# tvättstugan." stopped at "tvättstu" while the laundry site took 10-20 s (Joel, 2026-11-01), the rest waiting in
# ffmpeg and the panel's 24 KiB read. When the next sentence is not ready FLUSH_AFTER_S after one, flush() sends
# FLUSH_BYTES of silence (both holds, about 0.8 s), counted towards the pause before the next sentence.
FLUSH_AFTER_S = 0.3
FLUSH_BYTES = 24 * 1024 + 2048
# Speak the first sentence's opening clause on its own when it has at least this many words before a comma or a
# colon and the sentence goes on: the clause is read while the model writes the rest. 0 is off, Joel's call:
# Mattias reads the clause with a full stop's falling tone.
FIRST_CLAUSE_WORDS = 0
# Assist awaits STT in the run's task. Intent/TTS tasks inherit that run's context.
# Reset on every STT call; never store the last user's language on a shared entity.
TURN_LANGUAGE: ContextVar[str | None] = ContextVar("jarvis_turn_language", default=None)
# The turn's id, set with the language: the client sends it with /transcribe and /converse, and the STT and
# TTS entities report how their stage ended under it (the server's voice_turns table).
TURN_ID: ContextVar[str | None] = ContextVar("jarvis_turn_id", default=None)


def language_code(language: str | None) -> str | None:
    code = (language or '').lower().split('-')[0]
    return code if code in ('sv', 'en') else None


def _boundary(text: str) -> int | None:
    """Index just after the first sentence end whose next sentence has visibly started, or after the
    first line break: the agent ends a line where it stops to run its tools, and that sentence should
    be spoken while they run, not when the answer starts."""
    cut = None
    for m in END.finditer(text):
        after = m.end()
        if after >= len(text):
            break                             # wait: the next word has not arrived yet
        word = text[:m.start()].rsplit(None, 1)[-1].lower() if text[:m.start()].strip() else ""
        if word in ABBREVIATIONS or not text[after].isupper():
            continue
        cut = after
        break
    line = text.find("\n")
    return line + 1 if line != -1 and (cut is None or line < cut) else cut


def _clause_boundary(text: str, words: int) -> int | None:
    """Index just after the first comma or colon of a first sentence still being written, when at least `words`
    words come before it and the next word has visibly started; None when the text has a sentence end in it
    already (then the sentence logic decides), or when the next word starts with a digit ("kl. 10, 11 och 12")."""
    if not words:
        return None
    for m in CLAUSE.finditer(text):
        if END.search(text[:m.start()]):
            return None
        if m.end() >= len(text):
            return None                       # the next word has not arrived yet
        if len(text[:m.start()].split()) >= words and text[m.end()].isalpha():
            return m.end()
    return None


SENTENCE_IDLE_S = 0.12
TAIL_END = re.compile(r'[.!?…]+["”»)]?\s*$')


def _idle_boundary(text: str) -> bool:
    """A conservative sentence ending; numeric endings/abbreviations need lookahead."""
    match = TAIL_END.search(text)
    if not match or not text[:match.start()].strip():
        return False
    word = text[:match.start()].rsplit(None, 1)[-1].lower()
    return word not in ABBREVIATIONS and not word[-1:].isdigit()


def first_sentence_ready(text: str) -> bool:
    """A sentence has ended; do not flush a trailing abbreviation or partial number."""
    return _boundary(text) is not None or _idle_boundary(text)


async def sentences(chunks: AsyncIterator[str]) -> AsyncIterator[str]:
    """Keep lookahead for normal chunks, but release a complete sentence during a model pause.

    Waiting does not cancel the upstream iterator. Cancellation closes it and its pending read.
    """
    buf = ""
    iterator = aiter(chunks)
    pending = None
    first = True                              # nothing spoken yet: the opening clause may go on its own
    try:
        while True:
            if pending is None:
                pending = asyncio.ensure_future(anext(iterator))
            ready, _ = await asyncio.wait({pending}, timeout=SENTENCE_IDLE_S if _idle_boundary(buf) else None)
            if not ready:
                yield buf.strip()
                buf = ""
                first = False
                continue
            try:
                chunk = pending.result()
            except StopAsyncIteration:
                break
            finally:
                pending = None
            buf += chunk
            while True:
                cut = _boundary(buf)
                clause = _clause_boundary(buf, FIRST_CLAUSE_WORDS) if first else None
                if clause is not None and (cut is None or clause < cut):
                    yield buf[:clause].strip()
                    first = False
                    buf = buf[clause:]
                    continue
                if cut is None:
                    break
                if sentence := buf[:cut].strip():
                    yield sentence
                    first = False
                buf = buf[cut:]
        if buf.strip():
            yield buf.strip()
    finally:
        if pending is not None:
            pending.cancel()
            with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration):
                await pending
        if hasattr(iterator, "aclose"):
            await iterator.aclose()


def split_wav(data: bytes) -> tuple[tuple[int, int, int], bytes]:
    """((rate, channels, sample width), raw frames) of a WAV."""
    with wave.open(io.BytesIO(data)) as w:
        return (w.getframerate(), w.getnchannels(), w.getsampwidth()), w.readframes(w.getnframes())


def stream_header(fmt: tuple[int, int, int]) -> bytes:
    """A WAV header with 0 frames: the stream's length is not known yet (as Wyoming does it)."""
    rate, channels, width = fmt
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setframerate(rate)
        w.setnchannels(channels)
        w.setsampwidth(width)
    return buf.getvalue()


def silence(fmt: tuple[int, int, int], seconds: float = PAUSE_S) -> bytes:
    rate, channels, width = fmt
    return bytes(int(rate * seconds) * channels * width)


class WavStream:
    """Glues the spoken sentences into one WAV stream, in order: the header with the first sentence, a pause
    before each later one, and after a short first sentence the silence the panel's player needs before it
    starts (PRIME_S), counted towards the pause that follows."""

    def __init__(self, prime_s: float | None = None) -> None:
        self.fmt: tuple[int, int, int] | None = None
        self.prime_s = PRIME_S if prime_s is None else prime_s
        self.spoken = 0                       # sentences so far
        self._pause = PAUSE_S                 # before the next sentence, by how the last one ended
        self._padded = 0.0                    # seconds of silence already sent after the last sentence
        self.sent = 0                         # bytes of stream so far, header included
        self.flushed = False                  # flush() already sent since the last sentence

    def sentence(self, fmt: tuple[int, int, int], frames: bytes, text: str = "") -> list[bytes]:
        """The bytes to send for one spoken sentence; its text decides the pause after it."""
        chunks = []
        if self.fmt is None:
            self.fmt = fmt
            chunks.append(stream_header(fmt))
        elif (pause := self._pause - self._padded) > 0:
            chunks.append(silence(self.fmt, pause))
        chunks.append(frames)
        self._padded = 0.0
        rate, channels, width = fmt
        if self.spoken == 0 and (short := int(rate * self.prime_s) * channels * width - len(frames)) > 0:
            chunks.append(bytes(short))
            self._padded = short / (rate * channels * width)
        self.flushed = False
        self.spoken += 1
        self._pause = CLAUSE_PAUSE_S if text.rstrip().endswith((",", ":", ";")) else PAUSE_S
        self.sent += sum(len(c) for c in chunks)
        return chunks

    def top_up(self) -> bytes | None:
        """Silence up to PANEL_START_BYTES when the stream is shorter and more speech is coming, counted towards
        the pause before the next sentence; None once the panel has enough."""
        if self.fmt is None or self.sent >= PANEL_START_BYTES:
            return None
        rate, channels, width = self.fmt
        frame = channels * width
        short = -(-(PANEL_START_BYTES - self.sent) // frame) * frame
        self.sent += short
        self._padded += short / (rate * frame)
        return bytes(short)

    def flush(self) -> bytes | None:
        """Silence that pushes the last sentence's end through ffmpeg and the panel's read while nothing follows;
        once per sentence, counted towards the pause before the next one. None before the first sentence."""
        if self.fmt is None or self.flushed:
            return None
        rate, channels, width = self.fmt
        frame = channels * width
        quiet = bytes(-(-FLUSH_BYTES // frame) * frame)
        self.flushed = True
        self.sent += len(quiet)
        self._padded += len(quiet) / (rate * frame)
        return quiet

    def keepalive(self, seconds: float = 0.05) -> bytes | None:
        """A little silence to keep the panel's player waiting; nothing before the first sentence."""
        if self.fmt is None:
            return None
        quiet = silence(self.fmt, seconds)
        self.sent += len(quiet)
        return quiet

    def end(self) -> bytes | None:
        """What a stream with no sentence at all still needs: a header."""
        return stream_header((16000, 1, 2)) if self.fmt is None else None
