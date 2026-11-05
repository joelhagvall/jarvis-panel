"""Mattias/Ryan with shortened pauses and language isolated to each Assist turn."""

import asyncio
import logging
import time
from typing import Any

from homeassistant.components.tts import TextToSpeechEntity, TTSAudioRequest, TTSAudioResponse, TtsAudioType
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import jarvis_device
from .speech import FLUSH_AFTER_S, TURN_ID, TURN_LANGUAGE, WavStream, language_code, sentences, split_wav

_LOGGER = logging.getLogger(__name__)
NOTHING = object()  # no queue item taken ahead
KEEPALIVE_S = 5     # longest wait for the next sentence before a little silence keeps the stream alive
# Sentences read by the cloud at the same time; the next waits for a slot. A sentence's audio outlasts the next
# one's synthesis (0.3-1.1 s), so two keep the stream fed, and the first sentence never waits for a slot.
IN_FLIGHT = 2


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    """Add the voice."""
    async_add_entities([JarvisVoice(entry)])


class JarvisVoice(TextToSpeechEntity):
    """The Jarvis server reads the text with Home Assistant Cloud's Mattias and trims the pauses."""

    _attr_name = "Jarvis Mattias"
    _attr_supported_languages = ["sv-SE", "sv", "en-GB", "en-US", "en"]
    _attr_default_language = "sv-SE"

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}-tts"
        self._attr_device_info = jarvis_device(entry)

    async def async_get_tts_audio(self, message: str, language: str, options: dict[str, Any]) -> TtsAudioType:
        try:
            return "wav", await self._entry.runtime_data.speak(message, language=TURN_LANGUAGE.get() or language_code(language))
        except HomeAssistantError as err:
            _LOGGER.warning("Jarvis could not speak: %s", err)
            return None, None

    async def async_stream_tts_audio(self, request: TTSAudioRequest) -> TTSAudioResponse:
        """Speak the reply sentence by sentence while the agent is still writing it.

        Each sentence is sent to the server as soon as it is complete, all of them in parallel,
        and the audio comes back in order as one WAV stream with a short pause between sentences.
        """
        client = self._entry.runtime_data
        language = TURN_LANGUAGE.get() or language_code(request.language)
        turn = TURN_ID.get()                # None for announcements: they are no voice turn
        report = {"outcome": "error", "sentences": 0, "failed": 0}
        queue: asyncio.Queue = asyncio.Queue()
        jobs: set[asyncio.Future] = set()
        slots = asyncio.Semaphore(IN_FLIGHT)

        async def speak(sentence):
            async with slots:
                return await client.speak(sentence, language=language)

        async def read_text() -> None:
            try:
                async for sentence in sentences(request.message_gen):
                    job = asyncio.ensure_future(speak(sentence))
                    jobs.add(job)
                    queue.put_nowait((sentence, job))
            finally:
                queue.put_nowait(None)

        def send_report() -> None:
            """How the speech of a voice turn ended, in the background (the server's voice_turns)."""
            if turn is None or self.hass is None or not hasattr(client, "report_turn"):
                return

            async def send() -> None:
                try:
                    await client.report_turn(turn, "tts", report)
                except HomeAssistantError as err:
                    _LOGGER.debug("Jarvis turn report skipped: %s", err)
            self.hass.async_create_background_task(send(), "jarvis_agent.turn_report")

        async def data_gen():
            # An abandoned response may never be iterated/closed. Start work only with its consumer.
            reader = asyncio.ensure_future(read_text())
            stream = WavStream()
            started = time.monotonic()
            audio_s = 0.0
            ahead = NOTHING                 # the next queue item, taken early to see whether speech follows
            try:
                while True:
                    try:
                        if ahead is not NOTHING:
                            item, ahead = ahead, NOTHING
                        else:
                            item = await asyncio.wait_for(
                                queue.get(), KEEPALIVE_S if stream.flushed or stream.fmt is None else FLUSH_AFTER_S)
                    except TimeoutError:
                        # Nothing follows the last sentence yet: push its end out to the speaker first
                        # (speech.py's FLUSH_BYTES), then keep the stream alive.
                        if (pad := stream.flush()) is not None:
                            _LOGGER.debug("Flushed %d bytes after a sentence while waiting", len(pad))
                            yield pad
                            continue
                        # Jarvis's tools are running after the sentence it said first. The panel's
                        # player gives up after 30 s without data, so it gets a moment of silence.
                        if (quiet := stream.keepalive()) is not None:
                            yield quiet
                        continue
                    if item is None:
                        await reader  # upstream errors must not look like a successful truncated reply
                        break
                    sentence, job = item
                    try:
                        sentence_fmt, frames = split_wav(await job)
                    except (HomeAssistantError, ValueError, EOFError) as err:
                        _LOGGER.warning("Jarvis could not speak a sentence: %s", err)
                        report["failed"] += 1
                        continue
                    finally:
                        jobs.discard(job)
                    report["sentences"] += 1
                    audio_s += len(frames) / (sentence_fmt[0] * sentence_fmt[1] * sentence_fmt[2])
                    if stream.fmt is None:
                        report["first_audio_ms"] = round((time.monotonic() - started) * 1000)
                        rate, channels, width = sentence_fmt
                        # Shown with logger: custom_components.jarvis_agent: info in configuration.yaml
                        _LOGGER.info("First TTS audio ready in %.0f ms: %.1f s of speech for %d characters",
                                     (time.monotonic() - started) * 1000, len(frames) / (rate * channels * width), len(sentence))
                    for chunk in stream.sentence(sentence_fmt, frames, sentence):
                        yield chunk
                    # More speech is coming but is not ready: the panel's player still needs PANEL_START_BYTES
                    # to start, so it gets silence now instead of waiting for the next sentence. Not after the
                    # last sentence: the stream's end starts the player by itself.
                    if not queue.empty():
                        ahead = queue.get_nowait()
                    queued = ahead is not NOTHING and ahead is not None
                    coming = queued or (ahead is NOTHING and not reader.done())
                    if coming and not (queued and ahead[1].done()) and (pad := stream.top_up()) is not None:
                        yield pad
                if (header := stream.end()) is not None:
                    yield header
                report["outcome"] = "complete"
            except (GeneratorExit, asyncio.CancelledError):
                report["outcome"] = "stopped"       # the panel stopped listening: a new wake word, or Stopp
                raise
            finally:
                report["audio_ms"] = round(audio_s * 1000)
                send_report()
                reader.cancel()
                for job in jobs:
                    job.cancel()
                await asyncio.gather(reader, *jobs, return_exceptions=True)

        return TTSAudioResponse("wav", data_gen())
