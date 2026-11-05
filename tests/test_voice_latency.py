"""tools/voice_latency.py joins the server journal, the history database, Home Assistant's pipeline debug
events and a panel log into one record per turn, without inventing missing pieces. No network here: the
fixtures use shifted timestamps and fictional speech; measured durations are retained."""
from datetime import datetime, timezone
import inspect
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import voice_latency as vl  # noqa: E402


def us(iso):
    """Journald's __REALTIME_TIMESTAMP (µs) for an ISO time."""
    return round(datetime.fromisoformat(iso).timestamp() * 1_000_000)


def sec(iso):
    return datetime.fromisoformat(iso).timestamp()


PREFIX = '127.0.0.1 - - [25/Oct/2054 13:45:52] '
JOURNAL = [
    {'us': us('2054-10-25T12:45:48.453620+00:00'), 'msg': PREFIX + '"GET /background HTTP/1.1" 200 -'},
    {'us': us('2054-10-25T12:45:50.468448+00:00'), 'msg': PREFIX + '"POST /transcribe HTTP/1.1" 200 -'},
    {'us': us('2054-10-25T12:45:50.580832+00:00'), 'msg': PREFIX + '"POST /converse HTTP/1.1" 200 -'},
    {'us': us('2054-10-25T12:45:52.553333+00:00'), 'msg': PREFIX + 'anthropic claude-sonnet-5-5: first word 1191 ms, total 1972 ms, '
     'tokens 14785/68 (cached 0, cache write 14449), 1 rounds, $0.037475, no tools'},
    {'us': us('2054-10-25T12:45:52.650044+00:00'), 'msg': PREFIX + '"POST /speak HTTP/1.1" 200 -'},
    {'us': us('2054-10-25T12:45:53.018424+00:00'), 'msg': PREFIX + '"POST /speak HTTP/1.1" 200 -'},
    {'us': us('2054-10-25T12:45:53.415940+00:00'), 'msg': PREFIX + '"POST /speak HTTP/1.1" 200 -'},
    # a turn with tools, after a /warm that reached the server
    {'us': us('2054-10-25T12:49:02.000000+00:00'), 'msg': PREFIX + '"POST /warm HTTP/1.1" 202 -'},
    {'us': us('2054-10-25T12:49:08.572000+00:00'), 'msg': PREFIX + '"POST /transcribe HTTP/1.1" 200 -'},
    {'us': us('2054-10-25T12:49:08.700000+00:00'), 'msg': PREFIX + '"POST /converse HTTP/1.1" 200 -'},
    {'us': us('2054-10-25T12:49:14.930000+00:00'), 'msg': PREFIX + 'anthropic claude-sonnet-5-5: first word 6315 ms, total 6325 ms, '
     'tokens 30500/400 (cached 28898, cache write 0), 2 rounds, $0.012984, add_event 1269 ms, add_event 2167 ms'},
    {'us': us('2054-10-25T12:49:15.400000+00:00'), 'msg': PREFIX + '"POST /speak HTTP/1.1" 200 -'},
    # speech recognition down
    {'us': us('2054-10-25T12:51:59.900000+00:00'), 'msg': "transcribe unavailable: ValueError: language 'norwegian' is neither Swedish nor English"},
    {'us': us('2054-10-25T12:52:00.000000+00:00'), 'msg': PREFIX + '"POST /transcribe HTTP/1.1" 503 -'},
    # a request without streaming logs its line after the reply (the older server also logged no rounds)
    {'us': us('2054-10-25T13:00:03.000000+00:00'), 'msg': PREFIX + 'anthropic claude-sonnet-5-5: first word 1375 ms, total 1752 ms, '
     'tokens 10128/65 (cached 8668, cache write 0), $0.005304, no tools'},
    {'us': us('2054-10-25T13:00:03.010000+00:00'), 'msg': PREFIX + '"POST /converse HTTP/1.1" 200 -'},
    {'us': us('2054-10-25T13:03:59.000000+00:00'), 'msg': 'Started hallpanel.service - Hallway panel server (Jarvis agent, voice, chat history) for Home Assistant.'},
    {'us': us('2054-10-25T13:04:02.000000+00:00'), 'msg': "anthropic warm-up: {'cache_read': 0, 'cache_write': 15907, 'input': 79, 's': 1.36, 'cost_usd': 0.039926}"},
]

HISTORY = {
    'columns': ['id', 'ts', 'conversation_id', 'role', 'text', 'ok', 'brain', 'first_token_ms', 'total_ms', 'tokens_in',
                'tokens_out', 'cost_usd', 'tools', 'error', 'tokens_cached', 'tokens_cache_write', 'steps', 'agent_queue_ms',
                'request_to_first_text_ms'],
    'rows': [
        [212, '2054-10-25T12:45:50+00:00', 'C1', 'user', 'Demofråga ett.', 1] + [None] * 13,
        [213, '2054-10-25T12:45:52+00:00', 'C1', 'jarvis', 'Demosvar ett! Detta är en demonstration.', 1, 'anthropic claude-sonnet-5-5',
         1191, 1972, 14785, 68, 0.037475, '[]', None, 0, 14449, 1, 0, 1192],
        [216, '2054-10-25T12:49:08+00:00', 'C1', 'user', 'Kan du lägga in Demohändelse?', 1] + [None] * 13,
        [217, '2054-10-25T12:49:14+00:00', 'C1', 'jarvis', 'Ordnat.', 1, 'anthropic claude-sonnet-5-5', 6315, 6325, 30500, 400,
         0.012984, '[{"name": "add_event", "ms": 1269, "error": false, "round": 1}, {"name": "add_event", "ms": 2167, "error": false, "round": 1}]',
         None, 28898, 0, 2, 0, 6316],
        [218, '2054-10-25T12:55:00+00:00', 'notice', 'jarvis', 'Om 15 minuter: Demohändelse.', 1, 'notice:notice'] + [None] * 12,
        [220, '2054-10-25T13:00:01+00:00', 'C2', 'user', 'Hur mår du?', 1] + [None] * 13,
        [221, '2054-10-25T13:00:03+00:00', 'C2', 'jarvis', 'Bra.', 1, 'anthropic claude-sonnet-5-5', 1375, 1752, 10128, 65, 0.005304,
         '[]', None, 8668, 0, None, None, None],
        # a turn that only the database remembers (journal rotated): the agent's own numbers remain
        [230, '2054-10-24T19:14:50+00:00', 'C3', 'user', 'Detta är demofråga tre.', 1] + [None] * 13,
        [231, '2054-10-24T19:14:52+00:00', 'C3', 'jarvis', 'Detta är demosvar tre.', 1, 'anthropic claude-sonnet-5-5', 1427, 1864, 14716, 46,
         0.037099, '[]', None, 0, 14414, 1, 0, 1428],
    ],
}


def event(kind, iso, data=None):
    return {'type': kind, 'timestamp': iso, 'data': data or {}}


def delta(iso, **chat_log_delta):
    return event('intent-progress', iso, {'chat_log_delta': chat_log_delta})


HA = {
    'ha_version': '2026.10.4',
    'pipeline': {'id': 'p1', 'name': 'Jarvis'},
    'runs': [
        {'pipeline_run_id': 'R1', 'timestamp': '2054-10-25T12:45:47.189448+00:00', 'events': [
            event('run-start', '2054-10-25T12:45:47.189910+00:00', {'conversation_id': 'C1', 'satellite_id': 'assist_satellite.hallpanel'}),
            event('stt-start', '2054-10-25T12:45:47.190219+00:00', {'engine': 'stt.jarvis_cloud'}),
            event('stt-end', '2054-10-25T12:45:50.468963+00:00', {'stt_output': {'text': 'Demofråga ett.'}}),
            event('intent-start', '2054-10-25T12:45:50.469140+00:00', {'engine': 'conversation.jarvis', 'intent_input': 'Demofråga ett.'}),
            delta('2054-10-25T12:45:50.571067+00:00', role='assistant'),
            delta('2054-10-25T12:45:51.773219+00:00', content='Demosvar ett!'),
            delta('2054-10-25T12:45:52.118541+00:00', content=' Demo'),
            event('intent-progress', '2054-10-25T12:45:52.119328+00:00', {'tts_start_streaming': True}),
            delta('2054-10-25T12:45:52.552094+00:00', content='.'),
            event('intent-end', '2054-10-25T12:45:52.570424+00:00', {'processed_locally': False, 'intent_output': {
                'response': {'speech': {'plain': {'speech': 'Demosvar ett! Detta är en demonstration.'}}}}}),
            event('tts-start', '2054-10-25T12:45:52.570687+00:00', {'engine': 'tts.jarvis_mattias'}),
            event('tts-end', '2054-10-25T12:45:52.571181+00:00', {'tts_output': {'url': '<redacted>'}}),
            event('run-end', '2054-10-25T12:45:52.571457+00:00'),
        ]},
        {'pipeline_run_id': 'R2', 'timestamp': '2054-10-25T12:49:01.309432+00:00', 'events': [
            event('run-start', '2054-10-25T12:49:01.309958+00:00', {'conversation_id': 'C1'}),
            event('stt-start', '2054-10-25T12:49:01.310325+00:00'),
            event('stt-end', '2054-10-25T12:49:08.572725+00:00', {'stt_output': {'text': 'Kan du lägga in Demohändelse?'}}),
            event('intent-start', '2054-10-25T12:49:08.572932+00:00'),
            delta('2054-10-25T12:49:08.576902+00:00', role='assistant'),
            delta('2054-10-25T12:49:14.906212+00:00', content='Ordnat'),
            event('intent-progress', '2054-10-25T12:49:14.914255+00:00', {'tts_start_streaming': True}),
            event('intent-end', '2054-10-25T12:49:14.934756+00:00', {'processed_locally': False}),
            event('tts-start', '2054-10-25T12:49:14.935077+00:00'),
            event('run-end', '2054-10-25T12:49:14.936058+00:00'),
        ]},
        {'pipeline_run_id': 'R3', 'timestamp': '2054-10-25T12:50:01.967692+00:00', 'events': [
            event('run-start', '2054-10-25T12:50:01.968208+00:00', {'conversation_id': 'C1'}),
            event('stt-start', '2054-10-25T12:50:01.968513+00:00'),
            event('error', '2054-10-25T12:50:06.955322+00:00', {'code': 'stt-no-text-recognized', 'message': 'No text recognized'}),
            event('run-end', '2054-10-25T12:50:06.955545+00:00'),
        ]},
        # a command Home Assistant answered itself never reaches the server
        {'pipeline_run_id': 'R4', 'timestamp': '2054-10-25T12:51:00.000000+00:00', 'events': [
            event('run-start', '2054-10-25T12:51:00.000000+00:00', {'conversation_id': 'C1'}),
            event('stt-start', '2054-10-25T12:51:00.001000+00:00'),
            event('stt-end', '2054-10-25T12:51:03.000000+00:00', {'stt_output': {'text': 'Sätt en timer på en minut.'}}),
            event('intent-start', '2054-10-25T12:51:03.001000+00:00'),
            event('intent-end', '2054-10-25T12:51:03.900000+00:00', {'processed_locally': True, 'intent_output': {
                'response': {'speech': {'plain': {'speech': 'Timern är satt.'}}}}}),
            event('tts-start', '2054-10-25T12:51:03.901000+00:00'),
            event('tts-end', '2054-10-25T12:51:04.200000+00:00'),
            event('run-end', '2054-10-25T12:51:04.201000+00:00'),
        ]},
    ],
}

HA_LOG = [
    '\x1b[31m2054-10-25 13:45:47.013 ERROR (MainThread) [homeassistant.components.esphome.manager] Hallpanel: Service call '
    'jarvis_agent.warm: with data {} rejected; If you trust this device and want to allow access for it to make Home Assistant '
    'service calls, you can enable this functionality in the options flow\x1b[0m',
    '2054-10-25 13:45:52.650 INFO (MainThread) [custom_components.jarvis_agent.tts] First TTS audio ready in 531 ms',
    'not a log line',
]

PANEL_LOG = """INFO Starting log output from hallpanel.local using esphome API
[13:45:46.800][D][micro_wake_word:299]: Detected 'Hey Jarvis' with sliding average probability is 0.99
[13:45:47.011][D][voice_assistant:340]: Requesting start
[13:45:47.240][D][voice_assistant:637]: Client started, streaming microphone
[13:45:47.339][D][speaker_media_player:456]: State changed to ANNOUNCING
[13:45:48.632][D][speaker_media_player:456]: State changed to IDLE
[13:45:50.520][D][voice_assistant:812]: Speech recognised as: "Demofråga ett."
[13:45:50.525][D][voice_assistant:817]: Intent started
[13:45:52.150][D][voice_assistant:821]: Intent progress
[13:45:52.700][D][speaker_media_player:456]: State changed to ANNOUNCING
[13:45:52.701][I][latency:1234]: STT text -> player announcing 2180 ms
[13:45:52.780][D][speaker_media_player.pipeline:128]: Decoded audio has 1 channels, 16000 Hz sample rate, and 16 bits per sample
[13:45:52.800][D][voice_assistant:870]: Response: "Demosvar ett! Detta är en demonstration."
[13:45:59.090][D][voice_assistant:481]: Announcement finished playing
[13:45:59.122][D][voice_assistant:615]: State changed from RESPONSE_FINISHED to IDLE
"""
PANEL_LOG_END = sec('2054-10-25T12:46:30+00:00')


def raw(**overrides):
    doc = {'journal': {'entries': JOURNAL}, 'history': HISTORY, 'ha': HA, 'ha_log': {'lines': HA_LOG}}
    doc.update(overrides)
    return doc


class ParsingTest(unittest.TestCase):
    def test_early_lookups_saving_per_round(self):
        # Round 1: resa 1 300 ms, 900 of them while the model wrote, beside a 400 ms agenda read; round 2 none.
        tools = [{"name": "resa", "ms": 1300, "round": 1, "at_ms": 200, "early_ms": 900},
                 {"name": "agenda", "ms": 400, "round": 1, "at_ms": 1100},
                 {"name": "add_event", "ms": 600, "round": 2, "at_ms": 3000}]
        self.assertEqual(vl.early_saved_ms(tools), 1300 - 400)
        self.assertIsNone(vl.early_saved_ms([{"name": "resa", "ms": 1300, "round": 1}]))    # older rows
        record = {"error": None, "local": False, "tools": tools}
        self.assertTrue(vl.tools_cell(record).startswith("early -900: resa 1300"))

    def test_agent_line_formats(self):
        new = vl.parse_agent_line(PREFIX + 'anthropic claude-sonnet-5-5: first word 1191 ms, total 1972 ms, tokens 14785/68 '
                                  '(cached 0, cache write 14449), 1 rounds, $0.037475, no tools')
        self.assertEqual(new['brain'], 'anthropic claude-sonnet-5-5')
        self.assertEqual((new['first_token_ms'], new['total_ms'], new['steps'], new['cost_usd']), (1191, 1972, 1, 0.037475))
        self.assertEqual((new['tokens_in'], new['tokens_out'], new['tokens_cached'], new['tokens_cache_write']), (14785, 68, 0, 14449))
        self.assertEqual(new['tools'], [])
        tools = vl.parse_agent_line('luna gpt-6-luna: first word 5918 ms, total 6412 ms, tokens 11044/145 (cached 3171, cache write 3171), '
                                    '2 rounds, $0.002734, nyheter 33 ms, add_event 1269 ms')
        self.assertEqual(tools['tools'], [{'name': 'nyheter', 'ms': 33}, {'name': 'add_event', 'ms': 1269}])
        old = vl.parse_agent_line(PREFIX + 'anthropic claude-sonnet-5-5: first word 1375 ms, total 1752 ms, tokens 10128/65 '
                                  '(cached 8668, cache write 0), $0.005304, no tools')
        self.assertIsNone(old['steps'])
        self.assertEqual(old['total_ms'], 1752)
        direct = vl.parse_agent_line(PREFIX + 'direct: first word 0 ms, total 0 ms, tokens 0/0 (cached None, cache write None), 0 rounds, $0, no tools')
        self.assertEqual((direct['brain'], direct['tokens_cached'], direct['cost_usd']), ('direct', None, 0))
        self.assertIsNone(vl.parse_agent_line(PREFIX + '"POST /speak HTTP/1.1" 200 -'))
        self.assertIsNone(vl.parse_agent_line('home status: SL failed: HTTP Error 429: Too Many Requests'))

    def test_journal_events_skip_reads_and_keep_microseconds(self):
        events = vl.parse_journal(JOURNAL)
        kinds = [e['kind'] for e in events]
        self.assertFalse(any(e.get('path', '').startswith('/background') for e in events))
        self.assertEqual(kinds.count('reply'), 3)
        self.assertEqual(kinds.count('restart'), 1)
        self.assertEqual(kinds.count('warmup'), 1)
        first = next(e for e in events if e['kind'] == 'request' and e['path'] == '/transcribe')
        self.assertAlmostEqual(first['t'], sec('2054-10-25T12:45:50.468448+00:00'), places=5)

    def test_server_turns_streamed_non_streamed_failures_and_warm(self):
        server = vl.server_turns(vl.parse_journal(JOURNAL))
        turns = server['turns']
        self.assertEqual(len(turns), 3)
        first, second, third = turns
        self.assertTrue(first['streamed'])
        self.assertAlmostEqual(first['transcribe'], sec('2054-10-25T12:45:50.468448+00:00'), places=5)
        self.assertAlmostEqual(first['converse'], sec('2054-10-25T12:45:50.580832+00:00'), places=5)
        self.assertAlmostEqual(first['reply_done'], sec('2054-10-25T12:45:52.553333+00:00'), places=5)
        self.assertEqual(len(first['speaks']), 3)
        self.assertEqual(first['reply']['tokens_cache_write'], 14449)
        self.assertEqual([t['name'] for t in second['reply']['tools']], ['add_event', 'add_event'])
        self.assertFalse(third['streamed'])
        self.assertIsNone(third['converse'])
        self.assertEqual(third['reply']['total_ms'], 1752)
        self.assertEqual([f['code'] for f in server['stt_failures']], [503])
        # The server's logged reason travels with the failure, so a 503 turn says why.
        self.assertEqual(server['stt_failures'][0]['reason'],
                         "ValueError: language 'norwegian' is neither Swedish nor English")
        self.assertEqual(len(server['warms']), 1)
        self.assertEqual(len(server['restarts']), 1)

    def test_history_pairs_rows_and_skips_notices(self):
        turns = vl.history_turns(HISTORY)
        self.assertEqual([t['text'] for t in turns], ['Demofråga ett.', 'Kan du lägga in Demohändelse?', 'Hur mår du?', 'Detta är demofråga tre.'])
        Gympass = turns[1]
        self.assertEqual(Gympass['meta']['tools'][1]['ms'], 2167)
        self.assertEqual(Gympass['meta']['request_to_first_text_ms'], 6316)
        self.assertEqual(Gympass['conversation_id'], 'C1')

    def test_ha_runs_extract_the_stamps_that_matter(self):
        runs = vl.ha_runs(HA)
        r1, r2, r3, r4 = runs
        self.assertEqual(r1['text'], 'Demofråga ett.')
        self.assertAlmostEqual(r1['first_content'], sec('2054-10-25T12:45:51.773219+00:00'), places=5)
        self.assertAlmostEqual(r1['agent_called'], sec('2054-10-25T12:45:50.571067+00:00'), places=5)
        self.assertAlmostEqual(r1['tts_start_streaming'], sec('2054-10-25T12:45:52.119328+00:00'), places=5)
        self.assertIs(r1['processed_locally'], False)
        self.assertEqual(r1['reply_text'], 'Demosvar ett! Detta är en demonstration.')
        self.assertEqual(r1['deltas'], 4)
        self.assertEqual(r2['tool_calls'], [])
        self.assertIsNone(r3['stt_end'])
        self.assertEqual(r3['error'], 'stt-no-text-recognized: No text recognized')
        self.assertIs(r4['processed_locally'], True)

    def test_ha_log_lines(self):
        events = vl.parse_ha_log(HA_LOG)
        self.assertEqual([e['kind'] for e in events], ['warm_rejected', 'first_tts'])
        self.assertEqual(events[1]['ms'], 531)
        self.assertAlmostEqual(events[0]['t'], sec('2054-10-25T12:45:47.013+00:00'), places=3)   # UTC normalization is checked explicitly.

    def test_panel_log_runs(self):
        runs = vl.parse_panel_log(PANEL_LOG, PANEL_LOG_END)
        self.assertEqual(len(runs), 1)
        run = runs[0]
        self.assertEqual(run['text'], 'Demofråga ett.')
        self.assertAlmostEqual(run['start'], sec('2054-10-25T12:45:46.800+00:00'), places=3)       # the wake word, Stockholm time
        self.assertAlmostEqual(run['announcing'], sec('2054-10-25T12:45:52.700+00:00'), places=3)  # not the chime at 47.339
        self.assertEqual(run['latency_ms'], 2180)
        self.assertAlmostEqual(run['finished'] - run['announcing'], 6.39, places=2)

    def test_panel_log_across_midnight(self):
        text = '[23:59:59.000][D][voice_assistant:340]: Requesting start\n[00:00:02.000][D][voice_assistant:812]: Speech recognised as: "Hej."\n'
        end = sec('2026-10-26T00:00:30+01:00')
        run = vl.parse_panel_log(text, end)[0]
        self.assertAlmostEqual(run['stt_end'] - run['start'], 3.0, places=3)
        self.assertLess(run['start'], end)

    def test_since(self):
        now = datetime(2054, 10, 25, 18, 0, tzinfo=timezone.utc)
        self.assertEqual(vl.parse_since('2h', now), datetime(2054, 10, 25, 16, 0, tzinfo=timezone.utc))
        self.assertEqual(vl.parse_since('3d', now), datetime(2054, 10, 22, 18, 0, tzinfo=timezone.utc))
        # UTC normalization is checked explicitly.
        self.assertEqual(vl.parse_since('2054-10-25T13:00', now), datetime(2054, 10, 25, 12, 0, tzinfo=timezone.utc))
        with self.assertRaises(SystemExit):
            vl.parse_since('yesterday', now)


class CorrelationTest(unittest.TestCase):
    def setUp(self):
        self.records, self.sources = vl.analyse(raw(), PANEL_LOG, PANEL_LOG_END, 'net.log')
        self.by_question = {r['question']: r for r in self.records if r['question']}

    def test_one_row_per_turn_in_time_order(self):
        questions = [r['question'] for r in self.records]
        self.assertEqual(questions, ['Detta är demofråga tre.', 'Demofråga ett.', 'Kan du lägga in Demohändelse?', None,
                                     'Sätt en timer på en minut.', None, 'Hur mår du?'])
        self.assertEqual([r['sources'] for r in self.records], ['--D-', 'HJDP', 'HJD-', 'H---', 'H---', '-J--', '-JD-'])

    def test_full_waterfall_from_all_sources(self):
        r = self.by_question['Demofråga ett.']
        d = r['deltas_ms']
        self.assertEqual(d['listen'], 3279)
        self.assertEqual(d['text_to_first_token'], 1304)
        self.assertEqual(r['first_token_source'], 'ha')
        self.assertEqual(d['first_token_to_tts'], 346)
        self.assertEqual(r['tts_mode'], 'streamed')
        self.assertEqual(d['tts_to_first_audio'], 531)
        self.assertEqual(d['text_to_first_audio'], 2181)
        self.assertEqual(d['text_to_last_audio'], 2947)
        self.assertEqual(d['reply_total'], 2101)
        self.assertEqual(r['reply_total_source'], 'ha')
        self.assertEqual(d['ha_overhead_text_to_intent'], 0)
        self.assertEqual(r['sentences'], 3)
        self.assertEqual(r['model'], 'anthropic claude-sonnet-5-5')
        self.assertEqual(r['cache'], 'cold')
        self.assertEqual(r['warm'], 'rejected')
        self.assertEqual(r['agent']['cost_usd'], 0.037475)
        self.assertEqual(r['time_means'], 'HA stt-end (text ready)')
        self.assertAlmostEqual(r['time'], sec('2054-10-25T12:45:50.468963+00:00'), places=5)
        self.assertIsNone(r['error'])

    def test_panel_log_adds_the_panel_leg_without_mixing_clocks(self):
        p = self.by_question['Demofråga ett.']['panel']
        self.assertEqual(p['stt_to_announcing_ms'], 2180)
        self.assertEqual(p['latency_line_ms'], 2180)
        self.assertEqual(p['url_to_announcing_ms'], 550)
        self.assertEqual(p['played_ms'], 6390)
        self.assertEqual(p['first_audio_to_announcing_ms'], 2180 - 2181)
        self.assertIsNone(self.by_question['Kan du lägga in Demohändelse?']['panel'])

    def test_tools_cache_hit_and_warm_ok(self):
        r = self.by_question['Kan du lägga in Demohändelse?']
        self.assertEqual([t['name'] for t in r['tools']], ['add_event', 'add_event'])
        self.assertEqual(r['cache'], 'hit')
        self.assertEqual(r['warm'], 'ok')
        self.assertEqual(r['agent']['steps'], 2)
        self.assertEqual(r['deltas_ms']['text_to_first_token'], 6333)
        self.assertEqual(r['deltas_ms']['first_token_to_tts'], 8)

    def test_missing_pieces_stay_unavailable(self):
        db_only = self.by_question['Detta är demofråga tre.']
        self.assertEqual(db_only['time_means'], 'history row (whole seconds)')
        self.assertIsNone(db_only['deltas_ms']['listen'])
        self.assertIsNone(db_only['deltas_ms']['text_to_first_audio'])
        self.assertEqual(db_only['deltas_ms']['text_to_first_token'], 1428)
        self.assertEqual(db_only['first_token_source'], 'server')
        self.assertEqual(db_only['deltas_ms']['reply_total'], 1864)
        self.assertEqual(db_only['reply_total_source'], 'agent')
        self.assertIsNone(db_only['warm'])
        non_streamed = self.by_question['Hur mår du?']
        self.assertIs(non_streamed['streamed_request'], False)
        self.assertEqual(non_streamed['first_token_source'], 'model')
        self.assertEqual(non_streamed['deltas_ms']['text_to_first_token'], 1375)
        self.assertEqual(non_streamed['time_means'], 'history row (whole seconds)')
        self.assertIsNone(non_streamed['deltas_ms']['text_to_first_audio'])

    def test_local_commands_errors_and_failed_transcriptions_are_rows(self):
        local = self.by_question['Sätt en timer på en minut.']
        self.assertTrue(local['local'])
        self.assertEqual(local['model'], 'HA local')
        self.assertEqual(local['deltas_ms']['reply_total'], 900)
        self.assertIsNone(local['agent'])
        errors = [r for r in self.records if r['error']]
        self.assertEqual([r['error'] for r in errors], ['stt-no-text-recognized: No text recognized',
                          "transcribe answered HTTP 503: ValueError: language 'norwegian' is neither Swedish nor English"])
        self.assertEqual(errors[0]['sources'], 'H---')

    def test_table_and_json_render(self):
        window = 'since 2054-10-24T21:00:00+02:00 (3d), last all turns'
        table = vl.render_table(self.records, self.sources, window)
        self.assertIn('13:45:50.4  10-25  HJDP    3279   1304   346     531        2181   2101   2180', table)
        self.assertIn('median', table)
        self.assertIn('$0.0', table)
        self.assertIn('text ready (HA stt-end)', table)
        self.assertIn('panel log: 1 runs in net.log', table)
        doc = vl.to_json(self.records, self.sources, window)
        self.assertEqual(set(doc['clocks']), {'jarvis', 'mac', 'panel'})
        turn = next(t for t in doc['turns'] if t['question'] == 'Demofråga ett.')
        self.assertEqual(turn['time'], '2054-10-25T13:45:50.468+01:00')
        self.assertEqual(turn['stamps_jarvis']['stt_start'], '2054-10-25T13:45:47.190+01:00')
        self.assertEqual(len(turn['stamps_jarvis']['speaks']), 3)
        self.assertEqual(turn['panel']['announcing_mac'], '2054-10-25T13:45:52.700+01:00')
        json.dumps(doc)

    def test_empty_sources_render_without_inventing(self):
        records, sources = vl.analyse({'journal': {'error': 'journalctl missing'}, 'history': {'error': 'no db'},
                                       'ha': {'error': 'Home Assistant refused the token'}, 'ha_log': {'error': 'docker'}})
        self.assertEqual(records, [])
        table = vl.render_table(records, sources, 'since x')
        self.assertIn('No Jarvis turns found', table)
        self.assertIn('journal: journalctl missing', table)
        self.assertIn('HA debug: Home Assistant refused the token', table)


class RemoteScriptTest(unittest.TestCase):
    def test_collector_compiles_and_keeps_the_token_on_jarvis(self):
        compile(vl.REMOTE_SCRIPT, 'remote', 'exec')
        self.assertEqual(vl.REMOTE_SCRIPT.count('access_token'), 2)                 # the login, and the redaction list
        self.assertEqual(vl.REMOTE_SCRIPT.count('print('), 1)
        self.assertIn('print(json.dumps(out', vl.REMOTE_SCRIPT)
        self.assertNotIn('token =', vl.REMOTE_SCRIPT)                              # never kept in a variable that could leak
        self.assertIn('mode=ro', vl.REMOTE_SCRIPT)
        for forbidden in ('docker exec', 'docker restart', 'systemctl', 'INSERT', 'UPDATE', 'DELETE', 'call_service'):
            self.assertNotIn(forbidden, vl.REMOTE_SCRIPT)

    def test_ssh_options_are_non_interactive(self):
        self.assertIn('BatchMode=yes', vl.SSH)
        self.assertIn('python3 -I -', inspect.getsource(vl.fetch))                  # isolated mode, script on stdin


if __name__ == '__main__':
    unittest.main()
