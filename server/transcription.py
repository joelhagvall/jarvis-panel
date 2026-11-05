"""One bounded Swedish/English recognizer for both panel and Mac. Audio stays in memory.

Uses the existing Jarvis Groq account, without a language hint or translation.
There are no automatic retries or Swedish-only fallback after an ambiguous failure.

The HTTPS connection to Groq is kept between recordings, and warm() opens one ahead of a question
(the server's /warm calls it as a turn starts), so no DNS lookup, TCP or TLS handshake runs after
the speech ends. A kept connection that Groq's edge closed meanwhile is replaced and the recording
sent once more; a connection opened for the request itself is not retried.
"""
import hashlib
from http.client import HTTPException, HTTPSConnection
import io
import json
import os
import re
from pathlib import Path
import secrets
import ssl
import threading
import time
import wave

MODEL = 'whisper-large-v3-turbo'
HOST, PATH = 'api.groq.com', '/openai/v1/audio/transcriptions'
MAX_PCM = 60 * 16000 * 2
MAX_REPLY = 262144
KEY_FILE = Path('~/.config/hallpanel/groq.env').expanduser()
KEEP_S = 60.0       # a connection idle for longer is closed rather than reused; edges drop idle ones
KEEP_MAX = 2
TIMEOUT_S = 30
# Names Whisper otherwise hears as ordinary words: "Spela Anna Book" came back as "annan bok" on 2026-10-28 and
# 2026-10-30 (Kent was played once instead), "Magnus Carlsson" with a K. Sent as Groq's `prompt`, which guides the
# spelling of unfamiliar words (at most 224 tokens). Names only, never a sentence, so the prompt read back on a
# quiet recording is no command; _echo drops such a reading. Add a name when real turns show it misheard.
VOCABULARY = ('Spotify, JBL, Jarvis, Anna Book, Kent, Coldplay, Westlife, Magnus Carlsson, The Weeknd, Pitbull, '
              'Opeth, Stadshagen, Kungsholmen')
# A turn's early request can still be in flight when its next one starts, and two turns may overlap.
_slots = threading.BoundedSemaphore(4)
_denied, _pause_until = None, 0.0
_idle = []          # (connection, time.monotonic() when it was last used), newest last
_idle_lock = threading.Lock()
_context = None
_rate_lock = threading.Lock()
_rate_limits, _last_http, _requests_started = {}, None, 0


class Unavailable(RuntimeError):
    """str() is a short reason for the server log: never the transcript, the key or a provider body."""


def key():
    value = os.environ.get('GROQ_API_KEY', '')
    if not value:
        try:
            for line in KEY_FILE.read_text().splitlines():
                if line.startswith('GROQ_API_KEY='):
                    value = line.split('=', 1)[1].strip().strip('"').strip("'")
        except OSError:
            pass
    return value if value and len(value) <= 512 and value.isascii() and not any(c.isspace() for c in value) else None


def status():
    token = key()
    refused = bool(token and hashlib.sha256(token.encode()).digest() == _denied)
    with _rate_lock:
        limits, last_http, started = dict(_rate_limits), _last_http, _requests_started
    return {'configured': bool(token), 'ready': bool(token) and not refused and time.monotonic() >= _pause_until,
            'credential_refused': refused, 'model': MODEL, 'languages': ['sv', 'en'], 'max_seconds': 60,
            'provider_request_attempts': started, 'last_http_status': last_http,
            'rate_limits': {'requests_per_minute': None, **limits}}


def _ssl_context():
    """One verified context: loading the system's certificates on every request costs time on the Pi."""
    global _context
    if _context is None:
        _context = ssl.create_default_context()
    return _context


def _open():
    connection = HTTPSConnection(HOST, 443, timeout=TIMEOUT_S, context=_ssl_context())
    connection.connect()
    return connection


def _prune_locked():
    """Close kept connections that have been idle too long, and all but the KEEP_MAX newest."""
    now = time.monotonic()
    fresh = [(c, used) for c, used in _idle if now - used <= KEEP_S][-KEEP_MAX:]
    for connection, _ in _idle:
        if not any(connection is kept for kept, _ in fresh):
            connection.close()
    _idle[:] = fresh


def _take():
    """The newest fresh kept connection and True, or a new connection and False."""
    with _idle_lock:
        _prune_locked()
        if _idle:
            return _idle.pop()[0], True
    return _open(), False


def _keep(connection):
    with _idle_lock:
        _idle.append((connection, time.monotonic()))
        _prune_locked()


def warm():
    """Open a connection ahead of a question unless a fresh one is waiting. Blocks for the handshake, so
    call it on a worker thread. True when a connection waits afterwards."""
    if not key():
        return False
    with _idle_lock:
        _prune_locked()
        if _idle:
            return True
    try:
        _keep(_open())
    except OSError:
        return False
    return True


def _post(connection, body, headers):
    global _requests_started
    with _rate_lock:
        _requests_started += 1
    connection.request('POST', PATH, body, headers)
    return connection.getresponse()


def _remember_limits(response):
    """Whitelisted numeric headers only. Groq's request headers are RPD, never RPM."""
    global _rate_limits, _last_http
    values = {}
    header = getattr(response, 'getheader', lambda name: None)
    for name, field in (('x-ratelimit-limit-requests', 'requests_per_day'),
                        ('x-ratelimit-remaining-requests', 'remaining_requests_today')):
        raw = header(name)
        if isinstance(raw, str) and raw.isascii() and raw.isdecimal() and len(raw) <= 12:
            values[field] = int(raw)
    try:
        retry = float(header('retry-after') or 0)
        if 0 < retry <= 86400:
            values['retry_after_seconds'] = retry
    except (ValueError, TypeError):
        pass
    with _rate_lock:
        _rate_limits, _last_http = values, response.status
    return values


def _exchange(body, headers):
    """(status, reply, numeric limits) from Groq; only a stale connection is replaced once."""
    connection, reused = _take()
    try:
        response = _post(connection, body, headers)
    except (HTTPException, ConnectionError, ssl.SSLError):
        connection.close()
        if not reused:
            raise
        connection = _open()
        try:
            response = _post(connection, body, headers)
        except (HTTPException, OSError):
            connection.close()
            raise
    except OSError:
        connection.close()
        raise
    limits = _remember_limits(response)
    try:
        raw = response.read(MAX_REPLY + 1)
    except (HTTPException, OSError):
        connection.close()
        raise
    if response.status == 200 and response.isclosed() and not response.will_close and len(raw) <= MAX_REPLY:
        _keep(connection)
    else:
        connection.close()
    return response.status, raw, limits


def transcribe(wav):
    global _denied, _pause_until
    if len(wav) > MAX_PCM + 1024:
        raise ValueError('Recording exceeds 60 seconds')
    try:
        with wave.open(io.BytesIO(wav)) as audio:
            if (audio.getframerate(), audio.getnchannels(), audio.getsampwidth(), audio.getcomptype()) != (16000, 1, 2, 'NONE'):
                raise ValueError('Send 16 kHz mono 16-bit WAV')
            frames = audio.readframes(audio.getnframes())
            if not frames or len(frames) != audio.getnframes()*2 or len(frames) > MAX_PCM:
                raise ValueError('Empty, truncated or oversized recording')
    except (wave.Error, EOFError) as error:
        raise ValueError('Invalid WAV') from error
    token = key()
    if not token:
        raise Unavailable('no Groq key')
    state = status()
    if state['credential_refused']:
        raise Unavailable('Groq refused this key earlier (401)')
    if not state['ready']:
        raise Unavailable(f'paused after Groq HTTP {state["last_http_status"]}, '
                          f'{max(0, round(_pause_until - time.monotonic()))} s left')
    if not _slots.acquire(blocking=False):
        raise Unavailable('busy: 4 recognitions already in flight')
    try:
        text, language, heard, sure = _recognize(wav, token)
        if text.strip() and language == 'en':
            text, language = _english_or_swedish(wav, token, text, sure)
        if text.strip() and language is None:
            # Whisper heard Swedish as another language (Polish on four questions on 2026-10-27): its text is
            # then that language's words for the sound. Ask once more with the language set to Swedish, the
            # household's language, instead of failing the turn; English is recognised reliably on its own.
            original = ValueError(f'language {heard!r} is neither Swedish nor English')
            try:
                text, _, _, _ = _recognize(wav, token, language='sv')
            except (HTTPException, OSError, ValueError, AttributeError, Unavailable):
                raise original from None
            if not text.strip():
                raise original
            language = 'sv'
        if _echo(text):
            print('transcription: the reading was the vocabulary prompt itself, taken as no speech', flush=True)
            return {'text': '', 'language': None}
        return {'text': text.strip(), 'language': language}
    except (HTTPException, OSError, ValueError, AttributeError) as error:
        raise Unavailable(f'{type(error).__name__}: {error}'[:160]) from None
    finally:
        _slots.release()


def _words(text):
    return re.findall(r"[0-9a-zåäöéü]+", text.casefold())


# Words that are Swedish and not English, for a reading that is the same both ways. 2026-11-01 "Spela Coldplay"
# came back identical as English and as Swedish (Swedish surer, -0.251 vs -0.33) and was answered in English.
# Kept unambiguous: no "man", "i", "is", "for", "under" or names.
SWEDISH_WORDS = frozenset("""spela pausa fortsätt nästa föregående stoppa stäng sätt starta visa höj sänk blanda
    shuffla volym låt låten låtar gillade mina min mitt mig jag du vad hur när vem vilken vilket varför och eller
    inte är kan vill ska har på med till från av om det den ett en nu idag imorgon igår klockan väder vädret hej tack
    snälla radio radion högtalaren panelen""".split())


def _swedish(words):
    """True when a reading's words are Swedish: a Swedish-only word, or å, ä or ö."""
    return any(w in SWEDISH_WORDS or any(c in w for c in 'åäö') for w in words)


def _echo(text):
    """True when the reading is a run of four or more of VOCABULARY's words in its order: Whisper repeating its
    prompt over silence. A short answer that is one of the names ("Anna Book") is kept."""
    words, vocabulary = _words(text), _words(VOCABULARY)
    return len(words) >= 4 and any(vocabulary[i:i + len(words)] == words for i in range(len(vocabulary)))


def _english_or_swedish(wav, token, text, sure):
    """(text, language) for a question Whisper heard as English: Swedish too often comes back as English words for
    its sound ("Spela Socker med Kent" -> "Play soccer with Kent.", "Spellock heter jag", 2026-10-28), while Joel
    also speaks English ("what's up my man"). So it is asked once more as Swedish. The same words both ways are
    Swedish when they hold a Swedish word ("Spela Coldplay"), else English; otherwise the reading Whisper is surer
    of (its mean log probability per segment) wins. Both are
    logged, so the rule can be checked against real turns. A failed second request keeps the English."""
    try:
        swedish, _, _, sure_sv = _recognize(wav, token, language='sv')
    except (HTTPException, OSError, ValueError, AttributeError, Unavailable):
        return text, 'en'
    if not swedish.strip():
        choice = 'en'
    elif _words(swedish) == _words(text):
        # the same words both ways: forced Swedish reads English speech as surely ("What's up, my man?", -0.1 vs
        # -0.3), so the words decide, not the sureness
        choice = 'sv' if _swedish(_words(text)) else 'en'
    else:
        choice = 'sv' if sure_sv is not None and (sure is None or sure_sv >= sure) else 'en'
    print(f'transcription: heard as English (sure {sure}), as Swedish (sure {sure_sv}), chose {choice}', flush=True)
    return (swedish, 'sv') if choice == 'sv' else (text, 'en')


def _sureness(result):
    """Whisper's mean avg_logprob over the segments (closer to 0 is surer), or None."""
    values = [s.get('avg_logprob') for s in result.get('segments') or [] if isinstance(s, dict)]
    values = [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
    return round(sum(values) / len(values), 3) if values else None


def _recognize(wav, token, language=None):
    """(text, 'sv'/'en' or None, the language Groq named made safe for the log, its sureness) for one request."""
    global _denied, _pause_until
    boundary = 'hallpanel-' + secrets.token_hex(16)
    fields = [('model', MODEL), ('response_format', 'verbose_json'), ('temperature', '0'), ('prompt', VOCABULARY)]
    if language:
        fields.append(('language', language))
    parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
             for name, value in fields]
    parts += [f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="question.wav"\r\nContent-Type: audio/wav\r\n\r\n'.encode(),
              wav, f'\r\n--{boundary}--\r\n'.encode()]
    code, raw, limits = _exchange(b''.join(parts), {
        'Authorization': 'Bearer '+token, 'Content-Type': 'multipart/form-data; boundary='+boundary,
        'User-Agent': 'Hallpanel/1.0'})
    if code != 200:
        if code == 401:
            _denied = hashlib.sha256(token.encode()).digest()
        elif code in (403, 429):
            with _rate_lock:
                retry = limits.get('retry_after_seconds', 0)
                _pause_until = max(_pause_until, time.monotonic()+max(60, retry))
        raise Unavailable(f'Groq answered HTTP {code}')
    if len(raw) > MAX_REPLY:
        raise ValueError('Speech response exceeds limit')
    result = json.loads(raw)
    text = result.get('text', '')
    if not isinstance(text, str) or len(text) > 8000:
        raise ValueError('Invalid transcript')
    named = str(result.get('language', ''))
    found = {'english': 'en', 'en': 'en', 'swedish': 'sv', 'sv': 'sv'}.get(named.lower())
    heard = named if named.isascii() and named.isalpha() and len(named) <= 20 else 'unreadable'
    return text, found, heard, _sureness(result)
