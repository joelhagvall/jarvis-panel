"""Jarvis panel recording limits, against real HA classes and a fake Cloud provider.
Run inside the Home Assistant container; skipped when Home Assistant is not installed.
No speech service, microphone or language model is contacted.
"""
import importlib.util
import asyncio
import io
import sys
import tempfile
import types
import unittest
import wave
from pathlib import Path
from unittest.mock import patch, AsyncMock

ROOT = Path(__file__).resolve().parent.parent
HAS_HA = importlib.util.find_spec("homeassistant") is not None
if HAS_HA:
    # Load just the platform, without setting up the real integration or its services.
    package = types.ModuleType("jarvis_panel_test")
    package.__path__ = [str(ROOT / "ha/custom_components/jarvis_agent")]
    sys.modules[package.__name__] = package
    import importlib
    stt = importlib.import_module("jarvis_panel_test.stt")
    number = importlib.import_module("jarvis_panel_test.number")
    voice = importlib.import_module("jarvis_panel_test.tts")
    conversation = importlib.import_module("jarvis_panel_test.conversation")
    speech = importlib.import_module("jarvis_panel_test.speech")
    api = importlib.import_module("jarvis_panel_test.client")
    from homeassistant.core import HomeAssistant, Context
    from homeassistant.components.conversation import ChatLog, ConversationInput
    from homeassistant.exceptions import HomeAssistantError
    METADATA = stt.SpeechMetadata("sv-SE", stt.AudioFormats.WAV, stt.AudioCodecs.PCM, stt.AudioBitRates.BITRATE_16,
                                  stt.AudioSampleRates.SAMPLERATE_16000, stt.AudioChannels.CHANNEL_MONO)


class Vad:
    def Process10ms(self, chunk):
        return 1.0 if chunk[0] else 0.0


async def audio(sections, split=320, progress=None):
    """PCM for (seconds, speech) sections, handed over like the pipeline does and yielding to the loop between
    chunks, so a request started while listening runs alongside. progress, if given, counts the chunks sent."""
    data = b"".join((bytes([1 if speech else 0, 0]) * 160) * round(seconds * 100)
                    for seconds, speech in sections)
    for i in range(0, len(data), split):
        if progress is not None:
            progress.append(i)
        yield data[i:i + split]
        await asyncio.sleep(0)


def runtime(**attrs):
    """runtime_data as the client provides it: warm() records its calls, the test adds transcribe and so on."""
    warmed = []

    async def warm():
        warmed.append(True)
    return types.SimpleNamespace(warm=warm, warmed=warmed, **attrs)


def provider_for(runtime_data):
    provider = stt.JarvisCloudSpeech(types.SimpleNamespace(entry_id="fake", runtime_data=runtime_data))
    provider.hass = types.SimpleNamespace(
        async_create_background_task=lambda coro, name, eager_start=True: asyncio.ensure_future(coro))
    return provider


async def settle():
    for _ in range(5):
        await asyncio.sleep(0)


@unittest.skipUnless(HAS_HA, "run inside Home Assistant")
class PanelVoiceTest(unittest.IsolatedAsyncioTestCase):
    async def collect(self, sections, split=320, pause=stt.VOICE_SILENCE_S if HAS_HA else 0.8):
        with patch.object(stt, "MicroVad", Vad):
            return b"".join([chunk async for chunk in stt.command_audio(audio(sections, split), pause)])

    async def test_pause_inside_a_long_question_is_kept(self):
        result = await self.collect([(12, True), (0.5, False), (12, True), (2, False)])
        self.assertGreater(len(result) / 32000, 25)
        self.assertLess(len(result) / 32000, 26)

    async def test_long_thinking_pause_can_still_be_selected(self):
        result = await self.collect([(12, True), (1, False), (12, True), (2, False)], pause=1.5)
        self.assertGreater(len(result) / 32000, 26)

    async def test_fast_mode_ends_after_eight_tenths_of_silence(self):
        result = await self.collect([(2, True), (2, False)])
        self.assertAlmostEqual(len(result) / 32000, 2.8, delta=0.15)

    async def test_short_word_after_a_pause_gets_the_whole_pause_again(self):
        # "på [0.5 s] fredag [0.4 s] eller lördag": Home Assistant's segmenter restores the pause only after 1 s
        # of speech, so "fredag" would have been followed by the 0.3 s that were left and "eller lördag" cut off.
        result = await self.collect([(2, True), (0.5, False), (0.6, True), (0.4, False), (1, True), (2, False)])
        self.assertAlmostEqual(len(result) / 32000, 5.3, delta=0.05)

    async def test_sixty_second_recording_limit(self):
        result = await self.collect([(65, True)])
        self.assertAlmostEqual(len(result) / 32000, 60, delta=0.02)

    async def test_false_wake_stops_after_five_seconds(self):
        result = await self.collect([(10, False)])
        self.assertAlmostEqual(len(result) / 32000, 5, delta=0.02)

    async def test_arbitrary_transport_chunks_preserve_the_same_audio(self):
        sections = [(2, True), (2, False)]
        expected = await self.collect(sections)
        for split in (137, 640, 3200):
            self.assertEqual(await self.collect(sections, split), expected)

    async def test_multilingual_recognizer_gets_bounded_original_audio_without_outer_vad(self):
        consumed=[]
        async def transcribe(pcm):
            consumed.append(pcm)
            return {'text':'Can we switch to English?', 'language':'en'}
        provider = provider_for(runtime(transcribe=transcribe))

        class Cloud:
            available = True
            audio_processing = stt.DEFAULT_AUDIO_PROCESSING

            def check_metadata(self, metadata):
                return True

        cloud = Cloud()
        with patch.object(stt, "async_get_speech_to_text_entity", return_value=cloud), patch.object(stt, "MicroVad", Vad):
            self.assertFalse(provider.audio_processing.requires_external_vad)
            self.assertTrue(provider.check_metadata(METADATA))
            result = await provider.async_process_audio_stream(METADATA, audio([(20, True), (2, False)]))
        self.assertEqual(result.text, "Can we switch to English?")
        self.assertEqual(speech.TURN_LANGUAGE.get(),'en')
        self.assertEqual(len(consumed), 1)
        # the whole question, sent 0.4 s before the 0.8 s pause ran out
        self.assertAlmostEqual(len(consumed[0]) / 32000, 20.4, delta=0.05)
        self.assertTrue(consumed[0].startswith(b"\x01\x00" * 160))

    async def test_missing_cloud_returns_a_normal_error(self):
        provider = stt.JarvisCloudSpeech(types.SimpleNamespace(entry_id="fake"))
        provider.hass = types.SimpleNamespace()
        with patch.object(stt, "async_get_speech_to_text_entity", return_value=None):
            result = await provider.async_process_audio_stream(None, audio([(1, True)]))
        self.assertEqual(result.result, stt.SpeechResultState.ERROR)

    async def test_false_wake_never_calls_external_recognizer(self):
        async def forbidden(_pcm):raise AssertionError('Silent false wake must not be sent')
        data=runtime(transcribe=forbidden)
        provider=provider_for(data)
        with patch.object(stt,'MicroVad',Vad):
            result=await provider.async_process_audio_stream(METADATA,audio([(10,False)]))
        self.assertEqual(result.text,'')
        self.assertIsNone(speech.TURN_LANGUAGE.get())
        self.assertEqual(data.warmed,[True],'the agent is warmed as soon as the turn starts, before any speech')

    async def test_agent_is_warmed_once_when_the_turns_audio_starts(self):
        events=[]
        async def warm():events.append('warm')
        async def transcribe(pcm):
            events.append('transcribe')
            return {'text':'Vad är klockan?','language':'sv'}
        provider=provider_for(types.SimpleNamespace(warm=warm,transcribe=transcribe))
        with patch.object(stt,'MicroVad',Vad):
            result=await provider.async_process_audio_stream(METADATA,audio([(1,True),(2,False)]))
        self.assertEqual(result.text,'Vad är klockan?')
        self.assertEqual(events,['warm','transcribe'])

    async def test_warm_up_is_not_repeated_while_one_is_running(self):
        gate=asyncio.Event();calls=[]
        async def warm():
            calls.append(True);await gate.wait()
        provider=provider_for(types.SimpleNamespace(warm=warm))
        provider._warm_agent();await settle();provider._warm_agent();await settle()
        self.assertEqual(len(calls),1)
        gate.set();await settle()
        provider._warm_agent();await settle()
        self.assertEqual(len(calls),2)
        gate.set();await settle()

    async def test_unreachable_warm_up_does_not_affect_the_turn(self):
        async def warm():raise HomeAssistantError('Cannot reach Jarvis')
        async def transcribe(pcm):return {'text':'Hej.','language':'sv'}
        provider=provider_for(types.SimpleNamespace(warm=warm,transcribe=transcribe))
        with patch.object(stt,'MicroVad',Vad):
            result=await provider.async_process_audio_stream(METADATA,audio([(1,True),(2,False)]))
        self.assertEqual(result.text,'Hej.')

    async def test_recording_is_sent_before_the_pause_runs_out(self):
        progress,sent=[],[]
        async def transcribe(pcm):
            sent.append((len(pcm)/32000,len(progress)))
            return {'text':'Vad är klockan?','language':'sv'}
        provider=provider_for(runtime(transcribe=transcribe))
        with patch.object(stt,'MicroVad',Vad):
            result=await provider.async_process_audio_stream(METADATA,audio([(2,True),(2,False)],progress=progress))
        self.assertEqual(result.text,'Vad är klockan?')
        self.assertEqual(len(sent),1)
        seconds,chunks_so_far=sent[0]
        self.assertAlmostEqual(seconds,2.4,delta=0.05)                     # 2 s of speech and 0.4 s of the pause
        self.assertLess(chunks_so_far,270,'sent while the panel still listened, not after the 2.8 s boundary')

    async def test_resumed_speech_drops_the_early_text_and_sends_the_whole_recording(self):
        sent=[]
        async def transcribe(pcm):
            sent.append(len(pcm)/32000)
            return {'text':f'text {len(sent)}','language':'sv'}
        provider=provider_for(runtime(transcribe=transcribe))
        with patch.object(stt,'MicroVad',Vad):
            result=await provider.async_process_audio_stream(
                METADATA,audio([(2,True),(0.6,False),(1.5,True),(2,False)]))
        self.assertEqual(result.text,'text 2')
        self.assertEqual(len(sent),2)
        self.assertAlmostEqual(sent[0],2.4,delta=0.05)                    # 0.4 s into the 0.6 s thinking pause
        self.assertAlmostEqual(sent[1],4.9,delta=0.05)                    # one early attempt used: final audio includes the full pause

    async def test_failed_early_recognition_is_the_turns_error_without_a_second_request(self):
        sent=[]
        async def transcribe(pcm):
            sent.append(len(pcm));raise HomeAssistantError('offline')
        provider=provider_for(runtime(transcribe=transcribe))
        with patch.object(stt,'MicroVad',Vad):
            result=await provider.async_process_audio_stream(METADATA,audio([(1,True),(2,False)]))
        self.assertEqual(result.result,stt.SpeechResultState.ERROR)
        self.assertEqual(len(sent),1)

    async def test_long_pause_setting_sends_the_recording_late_enough(self):
        sent=[]
        async def transcribe(pcm):
            sent.append(len(pcm)/32000)
            return {'text':'Boka in ett möte.','language':'sv'}
        provider=provider_for(runtime(transcribe=transcribe,silence_seconds=2.0))
        with patch.object(stt,'MicroVad',Vad):
            result=await provider.async_process_audio_stream(METADATA,audio([(2,True),(1,False),(1,True),(3,False)]))
        self.assertEqual(result.text,'Boka in ett möte.')
        self.assertEqual(len(sent),1,'a 1 s pause is below 1.6 s: nothing is sent there')
        self.assertAlmostEqual(sent[0],5.6,delta=0.05)                    # 2 + 1 + 1 + 1.6 of the 2 s pause

    async def test_failed_recognition_clears_previous_turn_language(self):
        async def failed(_pcm):raise HomeAssistantError('offline')
        provider=provider_for(runtime(transcribe=failed))
        speech.TURN_LANGUAGE.set('en')
        with patch.object(stt,'MicroVad',Vad):
            result=await provider.async_process_audio_stream(METADATA,audio([(1,True),(2,False)]))
        self.assertEqual(result.result,stt.SpeechResultState.ERROR)
        self.assertIsNone(speech.TURN_LANGUAGE.get())

    async def test_many_thinking_pauses_send_at_most_one_early_request_and_one_final(self):
        sent=[]
        async def transcribe(pcm):
            sent.append(len(pcm)/32000)
            return {'text':str(len(sent)), 'language':'sv'}
        provider=provider_for(runtime(transcribe=transcribe))
        sections=[(2,True)]+[(0.5,False),(0.6,True)]*8+[(2,False)]
        with patch.object(stt,'MicroVad',Vad):
            result=await provider.async_process_audio_stream(METADATA,audio(sections))
        self.assertEqual(len(sent),2)
        self.assertEqual(result.text,'2')
        self.assertGreater(sent[-1],10)

    async def test_interrupted_stream_cancels_its_speculative_recognition(self):
        started,cancelled=asyncio.Event(),asyncio.Event()
        async def transcribe(pcm):
            started.set()
            try:await asyncio.Event().wait()
            finally:cancelled.set()
        async def recording():
            async for chunk in audio([(2,True),(0.5,False)]):yield chunk
            await started.wait()
            raise asyncio.CancelledError()
        provider=provider_for(runtime(transcribe=transcribe))
        with patch.object(stt,'MicroVad',Vad):
            with self.assertRaises(asyncio.CancelledError):
                await provider.async_process_audio_stream(METADATA,recording())
        self.assertTrue(cancelled.is_set())

    async def test_same_conversation_switches_languages_and_concurrent_runs_remain_isolated(self):
        with tempfile.TemporaryDirectory() as folder:
            hass=HomeAssistant(folder)
            async def turn(language):
                calls=[]
                reply='Of course. We can continue in English.' if language=='en' else 'Klart. Vi pratar svenska igen.'
                async def warm():
                    calls.append(('warm',))
                async def transcribe(pcm):
                    await asyncio.sleep(0)
                    return {'text':'English please.' if language=='en' else 'Svenska igen.', 'language':language}
                async def converse(text,cid,language=None):
                    calls.append(('conversation',cid,language))
                    yield 'delta',reply
                    yield 'done',{'reply':reply,'ok':True}
                async def speak(text,language=None):
                    calls.append(('voice',text,language))
                    out=io.BytesIO()
                    with wave.open(out,'wb') as w:
                        w.setparams((1,2,16000,0,'NONE','not compressed'));w.writeframes(b'\x01\x00'*160)
                    return out.getvalue()
                entry=types.SimpleNamespace(entry_id='fake',runtime_data=types.SimpleNamespace(
                    warm=warm,transcribe=transcribe,converse_stream=converse,speak=speak))
                provider=stt.JarvisCloudSpeech(entry);provider.hass=hass
                with patch.object(stt,'MicroVad',Vad):
                    result=await provider.async_process_audio_stream(METADATA,audio([(1,True),(2,False)]))
                agent=conversation.JarvisConversation(entry);agent.hass=hass
                log=ChatLog(hass,'same-conversation',delta_listener=lambda *_:None)
                user=ConversationInput(text=result.text,context=Context(),conversation_id='same-conversation',
                    device_id=None,satellite_id=None,language='sv',agent_id='conversation.jarvis')
                await agent._async_handle_message(user,log)
                async def chunks():yield reply
                async def synthesis():
                    # HA prepares TTS early but starts its background worker when intent text arrives.
                    output=await voice.JarvisVoice(entry).async_stream_tts_audio(types.SimpleNamespace(
                        language='sv-SE',message_gen=chunks()))
                    return b''.join([chunk async for chunk in output.data_gen])
                data=await hass.async_create_background_task(synthesis(),'test-language-tts')
                self.assertTrue(data.startswith(b'RIFF'))
                self.assertEqual(calls[0],('warm',))
                self.assertEqual(calls[1],('conversation','same-conversation',language))
                self.assertTrue(all(c[-1]==language for c in calls[1:]))
            for code in ('sv','en','sv'):await turn(code)
            await asyncio.gather(turn('en'),turn('sv'))


@unittest.skipUnless(HAS_HA, "run inside Home Assistant")
class VoiceTurnReportTest(unittest.IsolatedAsyncioTestCase):
    """2026-10-27: failed recognitions and silent wakes left no lasting trace; each stage now reports its end."""

    def reporter(self):
        reports = []

        async def report_turn(turn, stage, report):
            reports.append((turn, stage, dict(report)))
        return reports, report_turn

    async def test_each_turn_gets_its_own_id_and_reports_how_recognition_ended(self):
        reports, report_turn = self.reporter()
        answers = [{'text': 'Hej Jarvis', 'language': 'sv'}, HomeAssistantError('503')]

        async def transcribe(pcm):
            answer = answers.pop(0)
            if isinstance(answer, Exception):
                return None                            # _recognize turns a failed request into None
            return answer
        provider = provider_for(runtime(transcribe=transcribe, report_turn=report_turn))
        ids = []
        with patch.object(stt, "MicroVad", Vad):
            for sections in ([(1, True), (2, False)], [(10, False)], [(1, True), (2, False)]):
                await provider.async_process_audio_stream(METADATA, audio(sections))
                ids.append(speech.TURN_ID.get())
            await provider.async_process_audio_stream(None, audio([(1, True)]))
        await settle()
        self.assertEqual(len(set(ids)), 3)
        self.assertEqual([r[0] for r in reports[:3]], ids)
        self.assertEqual([(r[1], r[2]['outcome']) for r in reports],
                         [('stt', 'text'), ('stt', 'no_speech'), ('stt', 'failed'), ('stt', 'invalid')])
        self.assertEqual(reports[0][2]['language'], 'sv')
        self.assertAlmostEqual(reports[1][2]['speech_ms'], 5000, delta=20)   # a false wake records 5 s
        self.assertTrue(all(r[2]['listen_ms'] >= 0 for r in reports))
        self.assertNotIn('Hej Jarvis', repr(reports))  # the transcript stays in the messages table

    async def test_a_cancelled_turn_says_whether_the_panel_stopped_it_or_went_away(self):
        async def endless():
            yield b'\x01\x00' * 160
            await asyncio.Event().wait()           # the panel keeps the stream open until it aborts
        outcomes = []
        for state, stopping in (('idle', False), ('unavailable', False), ('idle', True)):
            reports, report_turn = self.reporter()
            provider = provider_for(runtime(report_turn=report_turn))
            provider.hass.states = types.SimpleNamespace(
                async_all=lambda domain, state=state: [types.SimpleNamespace(state=state)])
            provider.hass.is_stopping = stopping
            with patch.object(stt, "MicroVad", Vad), patch.object(stt, "CANCEL_CHECK_S", 0):
                task = asyncio.ensure_future(provider.async_process_audio_stream(METADATA, endless()))
                await settle()
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                await settle()
            outcomes.append(reports[0][2]['outcome'])
        self.assertEqual(outcomes, ['stopped', 'lost', 'lost'])

    async def test_client_sends_the_turn_with_recognition_and_the_question(self):
        client = object.__new__(api.JarvisClient)
        client._call = AsyncMock(return_value=b'{"text":"Hej","language":"sv"}')
        token = speech.TURN_ID.set('ab' * 8)
        try:
            await client.transcribe(b'\x01\x00' * 160)
            await client.converse('Hej', 'c')
        finally:
            speech.TURN_ID.reset(token)
        self.assertEqual(client._call.call_args_list[0].kwargs['headers'], {'X-Hallpanel-Turn': 'ab' * 8})
        self.assertEqual(client._call.call_args_list[1].args[2]['turn'], 'ab' * 8)
        await client.transcribe(b'\x01\x00' * 160)   # no turn (the Mac, a test): no header
        self.assertIsNone(client._call.call_args_list[2].kwargs['headers'])

    def voice_for(self, report_turn):
        async def speak(text, language=None):
            out = io.BytesIO()
            with wave.open(out, 'wb') as w:
                w.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
                w.writeframes(b'\x01\x00' * 16000)   # one second a sentence
            return out.getvalue()
        entry = types.SimpleNamespace(entry_id='fake', runtime_data=types.SimpleNamespace(
            speak=speak, report_turn=report_turn))
        entity = voice.JarvisVoice(entry)
        entity.hass = types.SimpleNamespace(
            async_create_background_task=lambda coro, name, eager_start=True: asyncio.ensure_future(coro))
        return entity

    async def speech_of(self, entity, turn, text):
        async def chunks():
            yield text
        token = speech.TURN_ID.set(turn)
        try:
            return await entity.async_stream_tts_audio(types.SimpleNamespace(language='sv-SE', message_gen=chunks()))
        finally:
            speech.TURN_ID.reset(token)

    async def test_speech_reports_sentences_audio_and_a_stop(self):
        reports, report_turn = self.reporter()
        entity = self.voice_for(report_turn)
        response = await self.speech_of(entity, 'cd' * 8, 'Första meningen. Andra meningen.')
        self.assertTrue(b''.join([c async for c in response.data_gen]).startswith(b'RIFF'))
        stopped = await self.speech_of(entity, 'ef' * 8, 'En lång mening. En till.')
        await stopped.data_gen.__anext__()
        await stopped.data_gen.aclose()             # the panel stopped listening
        announced = await self.speech_of(entity, None, 'Om fem minuter: tvätten.')
        b''.join([c async for c in announced.data_gen])
        await settle()
        done = {r[0]: r[2] for r in reports}
        self.assertEqual(set(done), {'cd' * 8, 'ef' * 8})   # an announcement is no voice turn
        self.assertEqual(done['cd' * 8]['outcome'], 'complete')
        self.assertEqual(done['cd' * 8]['failed'], 0)
        self.assertEqual(done['cd' * 8]['audio_ms'], 1000 * done['cd' * 8]['sentences'])
        self.assertGreaterEqual(done['cd' * 8]['sentences'], 1)
        self.assertEqual(done['ef' * 8]['outcome'], 'stopped')

    async def test_a_reply_that_goes_on_is_topped_up_so_the_panel_starts_with_its_first_sentence(self):
        # 2026-10-30: the panel's reader waits for full 24 KiB reads and starts at 1 s, so after a 1 s first
        # sentence it waited 1.37 s for the next one. speech.top_up() sends the missing bytes as silence at once.
        _, report_turn = self.reporter()
        entity = self.voice_for(report_turn)
        plain, second_ready = entity._entry.runtime_data.speak, asyncio.Event()

        async def speak(text, language=None):
            if 'Andra' in text:
                await second_ready.wait()
            return await plain(text, language)
        entity._entry.runtime_data.speak = speak

        async def text():
            yield 'Första meningen. '
            yield 'Andra meningen.'
        response = await entity.async_stream_tts_audio(types.SimpleNamespace(language='sv-SE', message_gen=text()))
        sent = 0
        async for chunk in response.data_gen:
            sent += len(chunk)
            if sent >= speech.PANEL_START_BYTES:
                break                                   # all before the second sentence's speech exists
        self.assertEqual(sent, speech.PANEL_START_BYTES)
        second_ready.set()
        rest = b''.join([c async for c in response.data_gen])
        self.assertEqual(len(rest), 32000)               # the top-up was the pause: the second second follows

        single = await self.speech_of(entity, None, 'Bara en mening.')
        self.assertEqual(len(b''.join([c async for c in single.data_gen])), 44 + 32000)   # no silence after the end


    async def test_a_slow_next_sentence_gets_the_last_ones_end_flushed_out(self):
        # Joel, 2026-11-01: the sentence before the laundry tools stopped at "tvättstu" until more bytes came.
        _, report_turn = self.reporter()
        entity = self.voice_for(report_turn)

        async def text():
            yield 'Självklart, jag tittar på tvättstugan.\n'
            await asyncio.sleep(speech.FLUSH_AFTER_S + 0.3)        # the tools are still running: no text yet
            yield 'Klart, onsdag 21-24.'
        response = await entity.async_stream_tts_audio(types.SimpleNamespace(language='sv-SE', message_gen=text()))
        sizes = [len(c) async for c in response.data_gen]
        flush = -(-speech.FLUSH_BYTES // 2) * 2
        self.assertIn(flush, sizes)
        self.assertEqual(sizes[-1], 32000)                  # the answer follows the flush without another pause

@unittest.skipUnless(HAS_HA, "run inside Home Assistant")
class SpeechPauseTest(unittest.IsolatedAsyncioTestCase):
    async def test_client_preserves_wav_and_per_request_language_in_real_payloads(self):
        client=object.__new__(api.JarvisClient)
        client._call=AsyncMock(return_value=b'{"text":"Hello","language":"en"}')
        result=await client.transcribe(b'\x01\x00'*160)
        self.assertEqual(result['language'],'en')
        call=client._call.call_args
        self.assertEqual(call.args[:2],('POST','/transcribe'))
        with wave.open(io.BytesIO(call.args[2])) as audio:
            self.assertEqual((audio.getframerate(),audio.getnchannels(),audio.getsampwidth()),(16000,1,2))
            self.assertEqual(audio.readframes(160),b'\x01\x00'*160)
        await client.speak('Hello.','en')
        self.assertEqual(client._call.call_args.args,('POST','/speak',{'text':'Hello.','language':'en'}))
        client._call.return_value=b'{"reply":"Hello."}'
        await client.converse('English now.','same','en')
        self.assertEqual(client._call.call_args.args[2]['language'],'en')
        client._call.return_value=b'{"warming": true}'
        await client.warm()
        self.assertEqual(client._call.call_args.args[:2],('POST','/warm'))

    async def test_changing_pause_updates_the_next_question(self):
        entry = types.SimpleNamespace(entry_id="fake", runtime_data=types.SimpleNamespace())
        setting = number.JarvisSpeechPause(entry)
        with patch.object(setting, "async_write_ha_state"):
            await setting.async_set_native_value(1.5)
        self.assertEqual(entry.runtime_data.silence_seconds, 1.5)
        self.assertEqual(setting.native_value, 1.5)

    async def test_invalid_pause_does_not_change_the_setting(self):
        entry = types.SimpleNamespace(entry_id="fake", runtime_data=types.SimpleNamespace(silence_seconds=0.8))
        setting = number.JarvisSpeechPause(entry)
        for value in (0, 3, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                await setting.async_set_native_value(value)
        self.assertEqual(entry.runtime_data.silence_seconds, 0.8)


if __name__ == "__main__":
    unittest.main()
