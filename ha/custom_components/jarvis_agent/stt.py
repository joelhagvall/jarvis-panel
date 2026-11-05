"""Automatic Swedish/English recognition through Jarvis, with our recording boundaries.

Home Assistant's standard segmenter stops at 15 s. Its satellite sensitivity select offers 0.25,
0.7 or 1.25 s of silence. This provider owns segmentation so Jarvis can use an adjustable pause
(0.8 s by default, number.jarvis_talpaus) and 60 s without patching Home Assistant or changing
other assistants. Groq needs the whole recording, so nothing can be recognized while Joel talks.
Instead the recording is sent once the silence has lasted VOICE_EARLY_S less than the pause, while
the segmenter still listens: when the pause then runs out, the text is ready up to 0.4 s sooner;
when speech resumes, that text is dropped and the whole recording is sent at the next pause. The
boundary itself never moves. The agent is warmed the moment a turn's audio starts, since Home
Assistant rejects the panel's own jarvis_agent.warm action without device permissions.
Conversation and response audio still stream.
"""
import asyncio
from collections.abc import AsyncIterable, AsyncIterator
from dataclasses import replace
import logging
import secrets
import time

from pymicro_vad import MicroVad

from homeassistant.components.assist_pipeline.vad import VoiceCommandSegmenter
from homeassistant.components.stt import (
    DEFAULT_AUDIO_PROCESSING, AudioBitRates, AudioChannels, AudioCodecs, AudioFormats,
    AudioSampleRates, SpeechAudioProcessing, SpeechMetadata, SpeechResult, SpeechResultState,
    SpeechToTextEntity, async_get_speech_to_text_entity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    VOICE_EARLY_S, VOICE_MAX_RECORDING_S, VOICE_SILENCE_S, VOICE_SPEECH_RESET_S, VOICE_START_TIMEOUT_S,
)
from .entity import jarvis_device
from .speech import TURN_ID, TURN_LANGUAGE

CLOUD_ENTITY = "stt.home_assistant_cloud"
CHUNK_BYTES, CHUNK_SECONDS = 320, 0.01  # microVAD: 160 samples, 16 kHz, mono, 16-bit PCM
# A cancelled turn is the panel's own abort (a tap on the voice page or the mic button, Mikrofon off) when the panel
# is still connected a moment later; ESPHome marks its satellite unavailable when the connection goes.
CANCEL_CHECK_S = 2.0
_LOGGER = logging.getLogger(__name__)


async def command_audio(stream: AsyncIterable[bytes], silence_seconds: float = VOICE_SILENCE_S,
                        on_speech=None, on_silence=None) -> AsyncIterator[bytes]:
    """Stream audio until speech ends or the recording limit is reached; detector state is per turn.

    on_speech() is called once, when the command starts. on_silence(seconds) is called for every
    10 ms inside the command with the length of the silence running up to it, 0.0 while speaking.
    Home Assistant's segmenter restores the pause only after 1 s of speech, so a short word after a
    pause ("på [0.5 s] fredag") was followed by what was left of the pause, not the whole pause; here
    VOICE_SPEECH_RESET_S of speech is enough.
    """
    vad = MicroVad()
    segmenter = VoiceCommandSegmenter(silence_seconds=silence_seconds, timeout_seconds=VOICE_MAX_RECORDING_S,
                                      reset_seconds=VOICE_SPEECH_RESET_S)
    pending = bytearray()
    elapsed = 0.0
    silent_chunks = 0
    async for audio in stream:
        pending.extend(audio)
        while len(pending) >= CHUNK_BYTES:
            chunk = bytes(pending[:CHUNK_BYTES])
            del pending[:CHUNK_BYTES]
            probability = vad.Process10ms(chunk)
            if not segmenter.process(CHUNK_SECONDS, probability):
                return
            if segmenter.in_command:
                if on_speech is not None:
                    on_speech()
                    on_speech = None
                silent_chunks = 0 if probability > segmenter.in_command_speech_threshold else silent_chunks + 1
                if on_silence is not None:
                    on_silence(silent_chunks * CHUNK_SECONDS)
            elapsed += CHUNK_SECONDS
            if not segmenter.in_command and elapsed >= VOICE_START_TIMEOUT_S:
                return                         # a false wake word must not record silence for a minute
            yield chunk
    if pending:
        yield bytes(pending)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    async_add_entities([JarvisCloudSpeech(entry)])


class JarvisCloudSpeech(SpeechToTextEntity):
    """Keep the existing stt.jarvis_cloud identity while detecting language afresh each turn."""

    _attr_name = "Jarvis Cloud"
    _attr_extra_state_attributes = {"silence_seconds": VOICE_SILENCE_S, "early_seconds": VOICE_EARLY_S,
                                    "max_recording_seconds": VOICE_MAX_RECORDING_S,
                                    "provider": "groq/whisper-large-v3-turbo", "auto_languages": ["sv", "en"]}

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}-stt"
        self._attr_device_info = jarvis_device(entry)
        self._warming: asyncio.Future | None = None

    @property
    def supported_languages(self) -> list[str]:
        return ["sv-SE", "sv", "en-GB", "en-US", "en"]

    @property
    def supported_formats(self) -> list[AudioFormats]:
        return [AudioFormats.WAV]

    @property
    def supported_codecs(self) -> list[AudioCodecs]:
        return [AudioCodecs.PCM]

    @property
    def supported_bit_rates(self) -> list[AudioBitRates]:
        return [AudioBitRates.BITRATE_16]

    @property
    def supported_sample_rates(self) -> list[AudioSampleRates]:
        return [AudioSampleRates.SAMPLERATE_16000]

    @property
    def supported_channels(self) -> list[AudioChannels]:
        return [AudioChannels.CHANNEL_MONO]

    @property
    def audio_processing(self) -> SpeechAudioProcessing:
        cloud = async_get_speech_to_text_entity(self.hass, CLOUD_ENTITY)
        processing = cloud.audio_processing if cloud else DEFAULT_AUDIO_PROCESSING
        # Prevent the outer pipeline from applying its fixed 15 s segmenter first.
        return replace(processing, requires_external_vad=False)

    def _warm_agent(self) -> None:
        """Warm the agent for this turn, once; a warm-up still running from the last turn is enough."""
        if self._warming is not None and not self._warming.done():
            return
        self._warming = self.hass.async_create_background_task(self._warm(), "jarvis_agent.warm")

    async def _warm(self) -> None:
        try:
            await self._entry.runtime_data.warm()
        except HomeAssistantError as err:        # the question itself reports an unreachable Jarvis
            _LOGGER.debug("Jarvis warm-up skipped: %s", err)

    async def _recognize(self, pcm: bytes) -> dict | None:
        """Jarvis's transcript of the recording, or None when recognition failed."""
        try:
            return await self._entry.runtime_data.transcribe(pcm)
        except HomeAssistantError:
            _LOGGER.warning("Jarvis speech recognition failed")
            return None

    def _report(self, turn: str, report: dict) -> None:
        """Send how recognition ended to the server, in the background: the turn never waits for it."""
        client = getattr(self._entry, "runtime_data", None)
        if self.hass is None or not hasattr(client, "report_turn"):
            return

        async def send() -> None:
            if report["outcome"] == "cancelled":
                if not getattr(self.hass, "is_stopping", False):
                    await asyncio.sleep(CANCEL_CHECK_S)
                report["outcome"] = self._cancel_cause()
            try:
                await client.report_turn(turn, "stt", report)
            except HomeAssistantError as err:
                _LOGGER.debug("Jarvis turn report skipped: %s", err)
        self.hass.async_create_background_task(send(), "jarvis_agent.turn_report")

    def _cancel_cause(self) -> str:
        """stopped: the panel aborted the turn itself; lost: the panel or Home Assistant went away."""
        if getattr(self.hass, "is_stopping", False):
            return "lost"
        satellites = self.hass.states.async_all("assist_satellite")
        if not satellites:
            return "cancelled"                  # nothing to tell them apart by
        return "lost" if any(s.state == "unavailable" for s in satellites) else "stopped"

    async def async_process_audio_stream(self, metadata: SpeechMetadata,
                                         stream: AsyncIterable[bytes]) -> SpeechResult:
        """One turn: a new id for the server's voice_turns, then recognition, then the report of its outcome."""
        TURN_LANGUAGE.set(None)
        turn = secrets.token_hex(8)
        TURN_ID.set(turn)
        started = time.monotonic()
        report = {"outcome": "cancelled"}
        try:
            result = await self._process(metadata, stream, report)
        except asyncio.CancelledError:
            raise
        except Exception:
            report["outcome"] = "error"
            raise
        finally:
            report["listen_ms"] = round((time.monotonic() - started) * 1000)
            self._report(turn, report)
        return result

    async def _process(self, metadata: SpeechMetadata, stream: AsyncIterable[bytes], report: dict) -> SpeechResult:
        if metadata is None or not self.check_metadata(metadata):
            report["outcome"] = "invalid"
            return SpeechResult(None, SpeechResultState.ERROR)
        self._warm_agent()
        # Read once per turn: moving the setting never changes the boundary halfway through a question.
        pause = getattr(self._entry.runtime_data, "silence_seconds", VOICE_SILENCE_S)
        early_at = max(pause - VOICE_EARLY_S, pause / 2)
        recording = bytearray()
        heard = []
        early: asyncio.Future | None = None
        early_started = False
        early_jobs = set()

        def on_silence(seconds: float) -> None:
            nonlocal early, early_started
            if seconds == 0.0:
                if early is not None and not early.done():
                    early.cancel()
                early = None                 # resumed speech invalidates the speculative transcript
            elif not early_started and seconds >= early_at:
                early_started = True  # at most one speculative request per turn, even after many pauses
                early = self.hass.async_create_background_task(self._recognize(bytes(recording)),
                                                               "jarvis_agent.recognize")
                early_jobs.add(early)

        try:
            async for chunk in command_audio(stream, pause, lambda: heard.append(True), on_silence):
                recording.extend(chunk)
            report["speech_ms"] = len(recording) // 32         # 16 kHz mono 16-bit: 32 bytes a millisecond
            if not heard:
                report["outcome"] = "no_speech"                # a false wake word, or nothing said after a reply
                return SpeechResult('', SpeechResultState.SUCCESS)
            report["early"] = early is not None
            result = await (early if early is not None else self._recognize(bytes(recording)))
        finally:
            for job in early_jobs:
                if not job.done():
                    job.cancel()
            if early_jobs:
                await asyncio.gather(*early_jobs, return_exceptions=True)
        if result is None or not isinstance(result.get('text'), str):
            report["outcome"] = "failed"
            return SpeechResult(None, SpeechResultState.ERROR)
        if not result['text'].strip():
            report["outcome"] = "empty"
            return SpeechResult('', SpeechResultState.SUCCESS)
        if result.get('language') not in ('sv', 'en'):
            report["outcome"] = "failed"
            return SpeechResult(None, SpeechResultState.ERROR)
        TURN_LANGUAGE.set(result['language'])
        report.update(outcome="text", language=result['language'])
        return SpeechResult(result['text'], SpeechResultState.SUCCESS)
