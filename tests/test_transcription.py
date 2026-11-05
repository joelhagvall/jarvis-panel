"""Multilingual recognition protocol, limits and the kept Groq connection, with fake HTTP only."""
from http.client import RemoteDisconnected
import io
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'server'))
import transcription as speech

SWEDISH = {'text':'Hej där.','language':'swedish'}


def wav(pcm=b'\x01\x00'*1600, rate=16000):
    out=io.BytesIO()
    with wave.open(out,'wb') as w:
        w.setparams((1,2,rate,0,'NONE','not compressed'));w.writeframes(pcm)
    return out.getvalue()


class Reply:
    """What http.client hands back: status, body, and whether the edge wants the connection closed."""
    def __init__(self, status=200, payload=None, body=None, will_close=False, headers=None):
        self.status=status;self.will_close=will_close
        self.body=body if body is not None else json.dumps(payload).encode()
        self.headers=headers or {}

    def getheader(self,name):return self.headers.get(name)

    def read(self, amount):
        data,self.body=self.body[:amount],self.body[amount:]
        return data

    def isclosed(self):
        return not self.body            # the whole body has been read


class Groq:
    """Fake api.groq.com: scripted replies (or errors) in order, one connection object per handshake."""
    def __init__(self, *replies, connect_error=None):
        self.replies=list(replies);self.connections=[];self.requests=[];fake=self

        class Connection:
            def __init__(self, host, port, timeout=None, context=None):
                self.host,self.port,self.timeout,self.context=host,port,timeout,context
                self.closed=False;fake.connections.append(self)

            def connect(self):
                if connect_error is not None:raise connect_error

            def request(self, method, path, body, headers):
                fake.requests.append((self,method,path,body,headers))

            def getresponse(self):
                reply=fake.replies.pop(0)
                if isinstance(reply,BaseException):raise reply
                return reply

            def close(self):
                self.closed=True
        self.Connection=Connection

    def patched(self):
        return patch.object(speech,'HTTPSConnection',self.Connection)


class RecognitionTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.patches=[patch.object(speech,'KEY_FILE',Path(self.tmp.name)/'groq.env'),
                      patch.dict('os.environ',{'GROQ_API_KEY':'unit-test-private-key'}),
                      patch.object(speech,'_denied',None),patch.object(speech,'_pause_until',0),
                      patch.object(speech,'_idle',[]),patch.object(speech,'_context',object())]
        self.patches += [patch.object(speech,'_rate_limits',{}),patch.object(speech,'_last_http',None),
                         patch.object(speech,'_requests_started',0)]
        for p in self.patches:p.start();self.addCleanup(p.stop)

    def test_each_question_detects_its_own_language_without_translation(self):
        groq=Groq(Reply(payload=SWEDISH),Reply(payload={'text':'Can we speak English?','language':'english'}),
                  Reply(payload={'text':'Can we speak English?','language':'swedish'}),   # same words as Swedish
                  Reply(payload={'text':'Nu tar vi svenska igen.','language':'swedish'}))
        with groq.patched():
            self.assertEqual([speech.transcribe(wav())['language'] for _ in range(3)],['sv','en','sv'])
        self.assertEqual(len(groq.requests),4)                 # the English one is read again as Swedish
        self.assertIn(b'name="language"\r\n\r\nsv\r\n',groq.requests[2][3])
        for connection,method,path,body,headers in groq.requests[:2]+groq.requests[3:]:
            self.assertEqual((connection.host,connection.port,method,path),
                             ('api.groq.com',443,'POST','/openai/v1/audio/transcriptions'))
            self.assertIsNotNone(connection.context)          # verified TLS, one context for all
            self.assertEqual(headers['User-Agent'],'Hallpanel/1.0')
            self.assertTrue(headers['Authorization'].startswith('Bearer '))
            self.assertNotIn(b'name="language"',body)
            self.assertNotIn(b'translation',body)
            self.assertIn(b'RIFF',body)
        self.assertEqual(len(groq.connections),1,'three questions, one handshake')

    def test_invalid_recordings_never_reach_provider(self):
        groq=Groq()
        with groq.patched():
            for data in (b'not audio',wav(b''),wav(rate=8000),wav()[:-1],wav(b'\0'*(speech.MAX_PCM+2))):
                with self.subTest(length=len(data)),self.assertRaises(ValueError):speech.transcribe(data)
        self.assertEqual(groq.connections,[])

    def test_provider_refusal_is_not_retried_until_credentials_change(self):
        groq=Groq(Reply(401,body=b'private'),Reply(401,body=b'private'))
        with groq.patched():
            for _ in range(2):
                with self.assertRaises(speech.Unavailable) as caught:speech.transcribe(wav())
                self.assertNotIn('private',str(caught.exception))
            self.assertEqual(len(groq.requests),1)
            self.assertTrue(speech.status()['credential_refused'])
            with patch.dict('os.environ',{'GROQ_API_KEY':'changed-test-key'}):
                with self.assertRaises(speech.Unavailable):speech.transcribe(wav())
                self.assertEqual(len(groq.requests),2)

    def test_rate_limit_is_reported_and_bounded_without_retry(self):
        groq=Groq(Reply(429,body=b'limited'))
        with groq.patched():
            for _ in range(2):
                with self.assertRaises(speech.Unavailable):speech.transcribe(wav())
        self.assertEqual(len(groq.requests),1)
        self.assertFalse(speech.status()['ready'])

    def test_daily_request_headers_are_never_mislabeled_as_rpm(self):
        groq=Groq(Reply(payload=SWEDISH,headers={'x-ratelimit-limit-requests':'2000',
            'x-ratelimit-remaining-requests':'1999','authorization':'private-secret'}))
        with groq.patched():speech.transcribe(wav())
        state=speech.status()
        self.assertEqual(state['rate_limits'],{'requests_per_minute':None,'requests_per_day':2000,'remaining_requests_today':1999})
        self.assertEqual(state['provider_request_attempts'],1)
        self.assertNotIn('private-secret',json.dumps(state))

    def test_retry_after_is_honored_and_bad_headers_are_not_exposed(self):
        groq=Groq(Reply(429,body=b'limited',headers={'retry-after':'120','x-ratelimit-limit-requests':'private-secret'}))
        with groq.patched():
            with self.assertRaises(speech.Unavailable):speech.transcribe(wav())
        self.assertGreater(speech._pause_until-time.monotonic(),119)
        self.assertEqual(speech.status()['rate_limits'],{'requests_per_minute':None,'retry_after_seconds':120.0})

    def test_edge_filter_does_not_falsely_label_valid_credentials_as_invalid(self):
        groq=Groq(Reply(403,body=b'error code: 1010'))
        with groq.patched():
            with self.assertRaises(speech.Unavailable):speech.transcribe(wav())
        state=speech.status()
        self.assertFalse(state['credential_refused']);self.assertFalse(state['ready'])

    def test_swedish_heard_as_english_is_read_again_and_the_surer_reading_wins(self):
        # 2026-10-28: "Spela Socker med Kent" came back as English, "Play soccer with Kent."
        def heard(text, language, *sure):
            return Reply(payload={'text': text, 'language': language,
                                  'segments': [{'avg_logprob': v} for v in sure]})
        groq = Groq(heard('Play soccer with Kent.', 'english', -0.9, -0.7),
                    heard('Spela Socker med Kent.', 'swedish', -0.2))
        with groq.patched(), patch('builtins.print'):
            self.assertEqual(speech.transcribe(wav()), {'text': 'Spela Socker med Kent.', 'language': 'sv'})
        self.assertIn(b'name="language"\r\n\r\nsv\r\n', groq.requests[1][3])
        # English Joel meant: the Swedish reading is less sure, or the same words
        for second in (heard('Vatt sapp mai man', 'swedish', -1.4), heard("What's up, my man?", 'swedish', -0.1)):
            groq = Groq(heard("What's up my man", 'english', -0.3), second)
            speech._idle.clear()                  # the kept connection belongs to the previous fake
            with self.subTest(second=second.body[:30]), groq.patched(), patch('builtins.print'):
                self.assertEqual(speech.transcribe(wav())['language'], 'en')
        # 2026-11-01: "Spela Coldplay" read the same both ways is Swedish, though the words match
        for same in ("Spela Coldplay.", "Pausa musiken", "Höj volymen"):
            groq = Groq(heard(same, 'english', -0.33), heard(same, 'swedish', -0.25))
            speech._idle.clear()
            with self.subTest(same=same), groq.patched(), patch('builtins.print'):
                self.assertEqual(speech.transcribe(wav())['language'], 'sv')
                groq = Groq(heard('Good morning.', 'english', -0.2), Reply(500, body=b'busy'))
        speech._idle.clear()
        with groq.patched():
            self.assertEqual(speech.transcribe(wav()), {'text': 'Good morning.', 'language': 'en'})

    def test_names_go_as_prompt_and_an_echo_of_it_is_no_speech(self):
        # 2026-10-28/30: "Spela Anna Book" came back as "annan bok"; the names steer Whisper's spelling.
        groq = Groq(Reply(payload={'text': 'Spela Anna Book.', 'language': 'swedish'}),
                    Reply(payload={'text': 'Anna Book, Kent, Coldplay, Westlife.', 'language': 'swedish'}),
                    Reply(payload={'text': 'Anna Book.', 'language': 'swedish'}))
        with groq.patched(), patch('builtins.print'):
            self.assertEqual(speech.transcribe(wav())['text'], 'Spela Anna Book.')
            self.assertEqual(speech.transcribe(wav()), {'text': '', 'language': None})   # the prompt read back
            self.assertEqual(speech.transcribe(wav())['text'], 'Anna Book.')             # a short answer stays
        self.assertIn(f'name="prompt"\r\n\r\n{speech.VOCABULARY}\r\n'.encode(), groq.requests[0][3])
        self.assertLess(len(speech.VOCABULARY.split()), 100)          # well inside Groq's 224 prompt tokens

    def test_swedish_heard_as_another_language_is_asked_again_as_swedish(self):
        # 2026-10-27: four questions failed as "language 'Polish' is neither Swedish nor English".
        groq=Groq(Reply(payload={'text':'Czy masz kalendarz','language':'polish'}),
                  Reply(payload={'text':'Vad har jag i kalendern?','language':'swedish'}))
        with groq.patched():
            self.assertEqual(speech.transcribe(wav()),{'text':'Vad har jag i kalendern?','language':'sv'})
        self.assertNotIn(b'name="language"',groq.requests[0][3])
        self.assertIn(b'name="language"\r\n\r\nsv\r\n',groq.requests[1][3])
        self.assertEqual(len(groq.connections),1,'the second request reuses the connection')

    def test_a_failed_second_try_reports_the_language_first_heard(self):
        for second in (Reply(500, body=b'busy'), Reply(payload={'text':'','language':'swedish'})):
            speech._idle.clear()
            with self.subTest(second=second.status), Groq(Reply(payload={'text':'x','language':'polish'}),second).patched():
                with self.assertRaises(speech.Unavailable) as caught: speech.transcribe(wav())
            self.assertIn("language 'polish' is neither Swedish nor English", str(caught.exception))

    def test_empty_unknown_and_malformed_responses(self):
        for response in ({'text':'','language':None},
                         {'text':[],'language':'english'},[],{'text':'x'*8001,'language':'english'}):
            speech._idle.clear()                  # the kept connection belongs to the previous fake
            with self.subTest(response_type=type(response).__name__),Groq(Reply(payload=response)).patched():
                if response=={'text':'','language':None}:
                    self.assertEqual(speech.transcribe(wav()),{'text':'','language':None})
                else:
                    with self.assertRaises(speech.Unavailable):speech.transcribe(wav())

    def test_each_unavailable_names_a_safe_reason_for_the_log(self):
        # A bare 503 in the journal was undiagnosable (three on 2026-10-26).
        cases = ((Groq(Reply(500, body=b'private provider body')), 'Groq answered HTTP 500'),
                 (Groq(Reply(payload={'text': 'private words', 'language': 'norwegian'}), Reply(503, body=b'private')),
                  "language 'norwegian'"),
                 (Groq(Reply(payload={'text': 'private words', 'language': 'x y'}), Reply(503, body=b'private')),
                  "language 'unreadable'"),
                 (Groq(connect_error=OSError('network down')), 'OSError: network down'))
        for groq, expected in cases:
            speech._idle.clear()
            with self.subTest(expected=expected), groq.patched(), self.assertRaises(speech.Unavailable) as caught:
                speech.transcribe(wav())
            reason = str(caught.exception)
            self.assertIn(expected, reason)
            for secret in ('private', 'unit-test-private-key'):
                self.assertNotIn(secret, reason)
        with Groq(Reply(429, body=b'limited')).patched():
            with self.assertRaises(speech.Unavailable):speech.transcribe(wav())
            with self.assertRaises(speech.Unavailable) as caught:speech.transcribe(wav())
        self.assertIn('paused after Groq HTTP 429', str(caught.exception))
        with patch.dict('os.environ', {'GROQ_API_KEY': ''}), self.assertRaises(speech.Unavailable) as caught:
            speech.transcribe(wav())
        self.assertEqual(str(caught.exception), 'no Groq key')

    def test_oversized_reply_is_rejected_and_its_connection_dropped(self):
        groq=Groq(Reply(body=b'{"text": "'+b'x'*speech.MAX_REPLY+b'"}'))
        with groq.patched():
            with self.assertRaises(speech.Unavailable):speech.transcribe(wav())
        self.assertTrue(groq.connections[0].closed);self.assertEqual(speech._idle,[])

    def test_status_is_passive_and_contains_no_key(self):
        groq=Groq()
        with groq.patched():
            state=speech.status();self.assertTrue(state['ready'])
        self.assertEqual(groq.connections,[])
        self.assertNotIn('unit-test-private-key',json.dumps(state))

    def test_warm_opens_the_connection_ahead_and_the_recording_reuses_it(self):
        groq=Groq(Reply(payload=SWEDISH))
        with groq.patched():
            self.assertTrue(speech.warm())
            self.assertEqual((len(groq.connections),len(groq.requests)),(1,0))
            self.assertTrue(speech.warm())                    # a fresh connection waits: no second handshake
            self.assertEqual(len(groq.connections),1)
            self.assertEqual(speech.transcribe(wav())['text'],'Hej där.')
        self.assertEqual((len(groq.connections),len(groq.requests)),(1,1))
        self.assertIs(groq.requests[0][0],groq.connections[0])
        self.assertEqual(len(speech._idle),1,'kept for the next recording')

    def test_warm_without_key_or_network_is_harmless(self):
        groq=Groq()
        with groq.patched(),patch.dict('os.environ',{'GROQ_API_KEY':''}):
            self.assertFalse(speech.warm())
        self.assertEqual(groq.connections,[])
        groq=Groq(connect_error=OSError('no route'))
        with groq.patched():
            self.assertFalse(speech.warm())
        self.assertEqual(speech._idle,[])

    def test_kept_connection_closed_by_the_edge_is_replaced_once(self):
        groq=Groq(Reply(payload=SWEDISH),RemoteDisconnected('closed by the edge'),Reply(payload=SWEDISH))
        with groq.patched():
            speech.transcribe(wav())
            self.assertEqual(speech.transcribe(wav())['text'],'Hej där.')
        self.assertEqual((len(groq.connections),len(groq.requests)),(2,3))
        self.assertTrue(groq.connections[0].closed);self.assertFalse(groq.connections[1].closed)
        self.assertIs(speech._idle[0][0],groq.connections[1])

    def test_fresh_connection_or_timeout_is_not_retried(self):
        groq=Groq(RemoteDisconnected('refused'))
        with groq.patched():
            with self.assertRaises(speech.Unavailable):speech.transcribe(wav())
        self.assertEqual((len(groq.connections),len(groq.requests)),(1,1))
        self.assertTrue(groq.connections[0].closed)
        groq=Groq(Reply(payload=SWEDISH),TimeoutError('30 s'))
        with groq.patched():
            speech.transcribe(wav())
            with self.assertRaises(speech.Unavailable):speech.transcribe(wav())
        self.assertEqual((len(groq.connections),len(groq.requests)),(1,2))
        self.assertEqual(speech._idle,[])

    def test_connection_idle_for_a_minute_is_not_reused(self):
        groq=Groq(Reply(payload=SWEDISH),Reply(payload=SWEDISH))
        with groq.patched():
            speech.transcribe(wav())
            speech._idle[0]=(speech._idle[0][0],time.monotonic()-speech.KEEP_S-1)
            speech.transcribe(wav())
        self.assertEqual(len(groq.connections),2)
        self.assertTrue(groq.connections[0].closed);self.assertIs(speech._idle[0][0],groq.connections[1])

    def test_edge_asking_to_close_is_honoured(self):
        groq=Groq(Reply(payload=SWEDISH,will_close=True))
        with groq.patched():
            speech.transcribe(wav())
        self.assertTrue(groq.connections[0].closed);self.assertEqual(speech._idle,[])

    def test_parallel_recordings_are_bounded_without_a_handshake(self):
        groq=Groq()
        held=[speech._slots.acquire(blocking=False) for _ in range(4)]
        try:
            self.assertTrue(all(held))
            with groq.patched(),self.assertRaises(speech.Unavailable):speech.transcribe(wav())
        finally:
            for _ in held:speech._slots.release()
        self.assertEqual(groq.connections,[])

    def test_local_endpoint_requires_auth_and_forwards_only_bounded_recordings(self):
        from hallpanel_server import make_handler
        handler=make_handler(self.tmp.name,None,'private-unit-token')
        request=object.__new__(handler)
        request.path='/transcribe';request.send_json=Mock()
        request.headers={'Content-Length':str(len(wav()))}
        request.rfile=io.BytesIO(wav())
        with patch.object(speech,'transcribe',return_value={'text':'Hello.','language':'en'}) as recognize:
            request.do_POST();self.assertEqual(request.send_json.call_args.args[0],401)
            recognize.assert_not_called()
            request.headers['X-Hallpanel-Token']='private-unit-token'
            request.do_POST();self.assertEqual(request.send_json.call_args.args,(200,{'text':'Hello.','language':'en'}))
            recognize.assert_called_once_with(wav())
            request.headers['Content-Length']=str(speech.MAX_PCM+1025)
            request.do_POST();self.assertEqual(request.send_json.call_args.args[0],400)
            self.assertEqual(recognize.call_count,1)
        # The reason goes to the journal only; the panel gets the same generic 503.
        request.headers['Content-Length']=str(len(wav()));request.rfile=io.BytesIO(wav())
        with patch.object(speech,'transcribe',side_effect=speech.Unavailable('Groq answered HTTP 500')), \
                patch('sys.stderr',new_callable=io.StringIO) as journal:
            request.do_POST()
        self.assertEqual(request.send_json.call_args.args,(503,{'error':'Speech recognition is unavailable'}))
        self.assertIn('transcribe unavailable: Groq answered HTTP 500',journal.getvalue())


if __name__=='__main__':unittest.main()
