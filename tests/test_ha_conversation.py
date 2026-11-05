"""The Jarvis conversation agent's deltas, against Home Assistant's real chat log.
Run inside the Home Assistant container; skipped when Home Assistant is not installed.
No server, speech service or language model is contacted.
"""
import asyncio
from contextlib import contextmanager, nullcontext
import io
import wave
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch, AsyncMock

ROOT = Path(__file__).resolve().parent.parent
HAS_HA = importlib.util.find_spec("homeassistant") is not None
if HAS_HA:
    # Load just the platform, without setting up the real integration or its services.
    package = types.ModuleType("jarvis_conversation_test")
    package.__path__ = [str(ROOT / "ha/custom_components/jarvis_agent")]
    sys.modules[package.__name__] = package
    import importlib
    agent = importlib.import_module("jarvis_conversation_test.conversation")
    sensor = importlib.import_module("jarvis_conversation_test.sensor")
    tts = importlib.import_module("jarvis_conversation_test.tts")
    cloud_speech = importlib.import_module("jarvis_conversation_test.cloud_speech")
    from homeassistant.components.conversation import ChatLog, ConversationInput, async_get_result_from_chat_log
    from homeassistant.components.conversation.chat_log import AssistantContent, ToolResultContent
    from homeassistant.core import Context
    from homeassistant.exceptions import HomeAssistantError
    from homeassistant.helpers.json import json_bytes
    from homeassistant.util import dt as dt_util


async def server(*events, fail=False):
    for event in events:
        yield event
    if fail:
        raise HomeAssistantError("lost Jarvis")


@unittest.skipUnless(HAS_HA, "run inside Home Assistant")
class ConversationTest(unittest.IsolatedAsyncioTestCase):
    async def run_log(self, *events, fail=False):
        seen, self.working = [], []
        log = ChatLog(NS(data={}), "test", delta_listener=agent.sentence_stream_listener(lambda _log, delta: seen.append(delta)))
        async for _ in log.async_add_delta_content_stream("conversation.jarvis", agent.jarvis_deltas(
                server(*events, fail=fail), self.working.append)):
            pass
        log.delta_listener.finish()
        user = ConversationInput(text="?", context=Context(), conversation_id="test", device_id=None,
                                 satellite_id=None, language="sv", agent_id="conversation.jarvis")
        return log, seen, async_get_result_from_chat_log(user, log).response.speech["plain"]["speech"]

    async def test_sentence_then_tools_then_answer(self):
        log, seen, speech = await self.run_log(
            ("delta", "Jag kollar flyget."), ("delta", "\n"), ("tools", ["flygplan", "vader"]),
            ("delta", "Planet är "), ("delta", "ett SAS-plan."), ("done", {"reply": "Planet är ett SAS-plan."}))
        kinds = [type(c).__name__ for c in log.content[1:]]
        self.assertEqual(kinds, ["AssistantContent", "ToolResultContent", "ToolResultContent", "AssistantContent"])
        first, last = log.content[1], log.content[-1]
        self.assertEqual(first.content, "Jag kollar flyget.\n")
        self.assertTrue(all(call.external for call in first.tool_calls))
        self.assertEqual([c.tool_name for c in log.content[2:4]], ["flygplan", "vader"])
        self.assertEqual(last.content, "Planet är ett SAS-plan.")
        self.assertEqual(speech, "Planet är ett SAS-plan.")
        self.assertEqual(self.working, ["Jag kollar flyget."])          # for the panel's screen
        # Home Assistant starts speaking on text followed by a tool call, and its pipeline events are JSON.
        calls = next(i for i, d in enumerate(seen) if d.get("tool_calls"))
        self.assertTrue(any(d.get("content") for d in seen[:calls]))
        for delta in seen:
            json_bytes({"chat_log_delta": delta})

    async def test_without_a_sentence_first_nothing_changes(self):
        log, _, speech = await self.run_log(("tools", ["flygplan"]), ("delta", "Planet är ett SAS-plan."),
                                            ("done", {"reply": "Planet är ett SAS-plan."}))
        self.assertEqual(len(log.content), 2)
        self.assertIsNone(log.content[1].tool_calls)
        self.assertEqual(self.working, [])
        self.assertEqual(speech, "Planet är ett SAS-plan.")

    async def test_log_ends_with_jarvis_when_nothing_follows_the_tools(self):
        log, _, _ = await self.run_log(("delta", "Jag kollar."), ("tools", ["flygplan"]), ("done", {"reply": ""}))
        self.assertIsInstance(log.content[-2], ToolResultContent)
        self.assertIsInstance(log.content[-1], AssistantContent)

    async def test_lost_server_after_the_tools(self):
        log, _, speech = await self.run_log(("delta", "Jag kollar."), ("tools", ["flygplan"]), fail=True)
        self.assertEqual(log.content[-1].content, " " + agent.UNREACHABLE)
        self.assertEqual(speech, " " + agent.UNREACHABLE)

    async def test_final_reply_when_nothing_streamed(self):
        _, _, speech = await self.run_log(("done", {"reply": "Hej."}))
        self.assertEqual(speech, "Hej.")

    async def test_short_sentence_starts_stream_without_changing_plain_reply_or_chat(self):
        log, seen, speech = await self.run_log(("delta", "Det är "), ("delta", "klart. "),
            ("delta", "Du kan gå nu."), ("done", {"reply": "Det är klart. Du kan gå nu."}))
        markers = [c for d in seen for c in d.get("tool_calls", []) if c.tool_name == "jarvis_speech_ready"]
        self.assertEqual(len(markers), 1)
        self.assertTrue(markers[0].external)
        self.assertEqual(len(log.content), 2)
        self.assertIsNone(log.content[-1].tool_calls)
        self.assertEqual(speech, "Det är klart. Du kan gå nu.")
        result = next(d for d in seen if d.get("tool_name") == "jarvis_speech_ready")
        self.assertEqual(result["tool_call_id"], markers[0].id)
        for delta in seen:json_bytes({"chat_log_delta": delta})

    async def test_question_keeps_follow_up_and_exact_text_once(self):
        log, _, speech = await self.run_log(("delta", "Vill du veta mer?"), ("done", {"reply": "Vill du veta mer?"}))
        self.assertEqual(speech, "Vill du veta mer?")
        self.assertTrue(log.continue_conversation)

    async def test_long_text_and_abbreviations_do_not_add_an_unnecessary_marker(self):
        for text in ("Vi ses kl. ", "Klockan är 10.", "x" * 60 + "."):
            _, seen, _ = await self.run_log(("delta", text), ("done", {"reply": text}))
            self.assertFalse(any(c.tool_name == "jarvis_speech_ready" for d in seen for c in d.get("tool_calls", [])), text)

    async def test_exactly_sixty_characters_still_need_the_marker(self):
        _, seen, speech = await self.run_log(("delta", "x" * 59 + "."), ("done", {"reply": "x" * 59 + "."}))
        self.assertEqual(len(speech),60)
        self.assertEqual(sum(c.tool_name == "jarvis_speech_ready" for d in seen for c in d.get("tool_calls", [])),1)



class FakeServer:
    def __init__(self, messages):
        self.messages = messages

    async def history(self, limit):
        return self.messages


@unittest.skipUnless(HAS_HA, "run inside Home Assistant")
class ChatSensorTest(unittest.IsolatedAsyncioTestCase):
    async def test_card_and_working_for_the_panel(self):
        now = dt_util.utcnow().isoformat()
        chat = sensor.JarvisChat(NS(entry_id="test", runtime_data=FakeServer([
            {"ts": now, "role": "user", "text": "Vilket plan?", "card": ""},
            {"ts": now, "role": "jarvis", "text": "SAS58E.", "card": "@1\n#\nX||SAS58E"}])))
        chat.async_write_ha_state = lambda: None
        chat._working("Genast. Jag kollar flyget.")                   # before any update: no attributes yet
        self.assertEqual(chat.extra_state_attributes["working"], "Genast. Jag kollar flyget.")
        await chat.async_update()
        attrs = chat.extra_state_attributes
        self.assertEqual((attrs["last_reply"], attrs["last_card"], attrs["working"]), ("SAS58E.", "@1\n#\nX||SAS58E", ""))
        chat._working("Ska bli. Jag tittar på vädret.")
        self.assertEqual(chat.extra_state_attributes["last_card"], "@1\n#\nX||SAS58E")   # the rest is kept


@unittest.skipUnless(HAS_HA, "run inside Home Assistant")
class StreamingVoiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_installed_ha_pipeline_keeps_every_word_after_short_sentence_marker(self):
        """Replay the cutoff through HA's real role-filtering callback, not a copy of it."""
        from homeassistant.components.assist_pipeline import default_pipeline

        chunks = ["Inlagt. L", "ördag den 31 oktober ligger besöket i kalendern. ",
                  "Jag satte det till en timme."]
        events, logs, streams = [], [], []
        hass = NS(data={})
        host = NS(hass=hass, context=Context(), device_id=None, satellite_id=None,
                  audio_settings=NS(needs_processor=False), process_event=events.append,
                  pipeline=NS(conversation_language="sv", prefer_local_intents=False))
        processor = default_pipeline._DefaultPipelineProcessor(host)
        processor.intent_agent = NS(id="conversation.jarvis", supports_streaming=True)
        processor._conversation_data = NS(continue_conversation_agent=None)
        processor._intent_agent_only = True
        processor.tts_stream = NS(supports_streaming_input=True, async_set_message_stream=streams.append)

        @contextmanager
        def get_log(_hass, _session, user_input, *, chat_log_delta_listener):
            log = ChatLog(hass, "test", delta_listener=chat_log_delta_listener)
            logs.append(log)
            yield log

        async def model(*_args, **_kwargs):
            for text in chunks:
                yield "delta", text
            yield "done", {"reply": "".join(chunks)}

        async def converse(**kwargs):
            entity = agent.JarvisConversation(NS(entry_id="test", runtime_data=NS(converse_stream=model)))
            entity.hass = hass
            user = ConversationInput(text=kwargs["text"], context=kwargs["context"],
                conversation_id="test", device_id=None, satellite_id=None,
                language="sv", agent_id="conversation.jarvis")
            return await entity._async_handle_message(user, logs[-1])

        with patch.object(default_pipeline.chat_session, "async_get_chat_session", return_value=nullcontext(NS(conversation_id="test"))), \
             patch.object(default_pipeline.conversation, "async_get_chat_log", get_log), \
             patch.object(default_pipeline.conversation, "async_converse", converse), \
             patch.object(agent, "async_dispatcher_send"):
            plain, _ = await processor.recognize_intent("Lägg in ett besök", "test", None)

        self.assertEqual(plain, "".join(chunks))
        self.assertEqual(len(streams), 1)
        streamed = "".join([part async for part in streams[0]])
        self.assertEqual(streamed, plain)
        self.assertEqual(logs[0].content[-1].content, plain)
        self.assertIsNone(logs[0].content[-1].tool_calls)

    async def test_conversation_starts_tts_before_next_model_chunk_below_ha_threshold(self):
        ready, spoken = asyncio.Event(), asyncio.Event()
        queue, calls = asyncio.Queue(), []
        count = 0
        def pipeline(_log, delta):
            nonlocal count
            text = delta.get("content") or ""
            if text:
                queue.put_nowait(text)
            # Same threshold/trigger as installed HA default_pipeline.py.
            trigger = count > 0 and delta.get("tool_calls")
            count += len(text)
            if trigger or count > 60:ready.set()
        async def model():
            yield "delta", "Första meningen. "
            await asyncio.wait_for(spoken.wait(), 1)
            yield "delta", "Andra meningen."
            yield "done", {"reply": "Första meningen. Andra meningen."}
        async def speak(text, language=None):
            calls.append(text);spoken.set()
            out=io.BytesIO()
            with wave.open(out,"wb") as wav:
                wav.setparams((1,2,16000,0,"NONE","not compressed"));wav.writeframes(b"\x01\x00"*160)
            return out.getvalue()
        log=ChatLog(NS(data={}),"test",delta_listener=agent.sentence_stream_listener(pipeline))
        async def converse():
            async for _ in log.async_add_delta_content_stream("conversation.jarvis",agent.jarvis_deltas(model())):pass
            queue.put_nowait(None)
        async def text():
            while (chunk:=await queue.get()) is not None:yield chunk
        async def audio():
            await asyncio.wait_for(ready.wait(),1)
            response=await tts.JarvisVoice(NS(entry_id="test",runtime_data=NS(speak=speak))).async_stream_tts_audio(
                NS(language="sv-SE",message_gen=text()))
            return b"".join([chunk async for chunk in response.data_gen])
        _,wav=await asyncio.gather(converse(),audio())
        self.assertTrue(wav.startswith(b"RIFF"))
        self.assertEqual(calls,["Första meningen.","Andra meningen."])
        self.assertEqual(log.content[-1].content,"Första meningen. Andra meningen.")

    async def test_first_sentence_speaks_before_the_next_model_chunk(self):
        spoken = asyncio.Event()
        calls = []
        async def speak(text, language=None):
            calls.append(text)
            spoken.set()
            buf = io.BytesIO()
            with wave.open(buf, "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(16000)
                wav.writeframes(b"\x01\x00" * 160)
            return buf.getvalue()
        async def chunks():
            yield "Första meningen. "
            await asyncio.wait_for(spoken.wait(), 1)
            yield "Andra meningen."
        entity = tts.JarvisVoice(NS(entry_id="test", runtime_data=NS(speak=speak)))
        response = await entity.async_stream_tts_audio(NS(language='sv-SE',message_gen=chunks()))
        data = b"".join([chunk async for chunk in response.data_gen])
        self.assertEqual(calls, ["Första meningen.", "Andra meningen."])
        self.assertTrue(data.startswith(b"RIFF"))

    async def test_stopping_audio_cancels_pending_synthesis_and_text(self):
        cancelled, started = asyncio.Event(), asyncio.Event()
        async def speak(text, language=None):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        async def chunks():
            yield "En mening."
        entity = tts.JarvisVoice(NS(entry_id="test", runtime_data=NS(speak=speak)))
        response = await entity.async_stream_tts_audio(NS(language='sv-SE',message_gen=chunks()))
        pending = asyncio.create_task(anext(response.data_gen))
        await asyncio.wait_for(started.wait(), 1)
        pending.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await pending
        self.assertTrue(cancelled.is_set())

    async def test_unused_audio_response_does_not_start_background_text_or_speech(self):
        touched=[]
        async def chunks():
            touched.append("text")
            yield "En mening."
        speak=AsyncMock()
        entity=tts.JarvisVoice(NS(entry_id="test",runtime_data=NS(speak=speak)))
        response=await entity.async_stream_tts_audio(NS(language='sv-SE',message_gen=chunks()))
        await response.data_gen.aclose()
        for _ in range(4):await asyncio.sleep(0)
        self.assertEqual(touched,[])
        speak.assert_not_awaited()

    async def test_broken_text_stream_reports_error_and_allows_the_next_turn(self):
        async def broken():
            raise HomeAssistantError("text stream disconnected")
            yield ""
        entity=tts.JarvisVoice(NS(entry_id="test",runtime_data=NS(speak=AsyncMock())))
        response=await entity.async_stream_tts_audio(NS(language='sv-SE',message_gen=broken()))
        with self.assertRaisesRegex(HomeAssistantError,"disconnected"):
            _=[chunk async for chunk in response.data_gen]
        buf=io.BytesIO()
        with wave.open(buf,'wb') as wav:
            wav.setparams((1,2,16000,0,'NONE','not compressed'))
            wav.writeframes(b'\0\0'*160)
        entity._entry.runtime_data.speak=AsyncMock(return_value=buf.getvalue())
        async def next_turn():yield 'Nästa svar.'
        response=await entity.async_stream_tts_audio(NS(language='sv-SE',message_gen=next_turn()))
        self.assertTrue(b''.join([chunk async for chunk in response.data_gen]).startswith(b'RIFF'))


@unittest.skipUnless(HAS_HA, "run inside Home Assistant")
class NativeCloudSpeechTest(unittest.IsolatedAsyncioTestCase):
    async def test_authenticated_view_keeps_voice_and_every_native_pcm_sample(self):
        pcm = b"\x01\x02\xff\x7f\x00\x80" * 200
        synth = AsyncMock(return_value=pcm)
        view = cloud_speech.JarvisCloudSpeechView(NS(data={cloud_speech.DATA_CLOUD: NS(voice=NS(process_tts=synth))}))
        self.assertTrue(view.requires_auth)
        for language, voice in (("sv-SE", "MattiasNeural"), ("en-GB", "RyanNeural")):
            result = await view.post(NS(json=AsyncMock(return_value={"message": "En hel mening.", "language": language,
                                                                    "options": {"voice": voice}})))
            self.assertEqual(result.status, 200)
            synth.assert_awaited_with(text="En hel mening.", language=language, voice=voice, output=cloud_speech.AudioOutput.RAW)
            with wave.open(io.BytesIO(result.body)) as wav:
                self.assertEqual((wav.getframerate(), wav.getnchannels(), wav.getsampwidth()), (16000, 1, 2))
                self.assertEqual(wav.readframes(wav.getnframes()), pcm)

    async def test_invalid_or_missing_cloud_does_not_synthesize(self):
        synth = AsyncMock()
        view = cloud_speech.JarvisCloudSpeechView(NS(data={cloud_speech.DATA_CLOUD: NS(voice=NS(process_tts=synth))}))
        for body in ({}, [], {"message": "", "language": "sv-SE", "options": {"voice": "MattiasNeural"}},
                     {"message": "Hej.", "language": "fr-FR", "options": {"voice": "MattiasNeural"}}):
            self.assertEqual((await view.post(NS(json=AsyncMock(return_value=body)))).status, 400)
        synth.assert_not_awaited()
        view = cloud_speech.JarvisCloudSpeechView(NS(data={}))
        request = NS(json=AsyncMock(return_value={"message": "Hej.", "language": "sv-SE", "options": {"voice": "MattiasNeural"}}))
        self.assertEqual((await view.post(request)).status, 503)

    async def test_cloud_failures_are_errors_without_leaking_provider_details(self):
        synth = AsyncMock(side_effect=cloud_speech.VoiceError("private provider detail"))
        view = cloud_speech.JarvisCloudSpeechView(NS(data={cloud_speech.DATA_CLOUD: NS(voice=NS(process_tts=synth))}))
        request = NS(json=AsyncMock(return_value={"message": "Hej.", "language": "sv-SE", "options": {"voice": "MattiasNeural"}}))
        result = await view.post(request)
        self.assertEqual(result.status, 502)
        self.assertNotIn(b"private provider detail", result.body)


if __name__ == "__main__":
    unittest.main()
