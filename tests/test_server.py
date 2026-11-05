#!/usr/bin/env python3
"""Regression tests for server/hallpanel_server.py with a stub agent and a fake Home Assistant
(no Claude, no network beyond localhost). Run: python3 -m unittest tests/test_server.py"""
import array
import io
import json
import math
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import wave
from types import SimpleNamespace
from unittest.mock import Mock, patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOKEN = "unit-test-token"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def speech_wav(pause_s):
    """0.5 s tone, pause_s of silence, 0.5 s tone, 16 kHz mono, like a sentence with a long pause."""
    tone = [int(8000 * math.sin(2 * math.pi * 220 * i / 16000)) for i in range(8000)]
    samples = array.array("h", tone + [0] * int(16000 * pause_s) + tone)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(samples.tobytes())
    return buf.getvalue()


class FakeHomeAssistant(BaseHTTPRequestHandler):
    """Answers the native HA speech view with a WAV containing a 1.2 s pause."""
    def do_POST(self):
        self.server.last_tts = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        self.server.tts_requests.append(self.server.last_tts)
        body = speech_wav(1.2)
        self.send_response(200 if self.headers.get("Authorization") == "Bearer ha-token" else 401)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        wav = speech_wav(1.2)
        self.send_response(200)
        self.send_header("Content-Length", str(len(wav)))
        self.end_headers()
        self.wfile.write(wav)

    def log_message(self, *args):
        pass


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="hallpanel-ut-"))
        (cls.tmp / "www").mkdir()
        (cls.tmp / "www" / "bg.png").write_bytes(b"\x89PNG fake")
        stub = cls.tmp / "agent.py"
        stub.write_text("import sys\nt = sys.stdin.read()\n"
                        "if 'FAIL' in t.splitlines()[-1]: sys.exit(3)\n"
                        "if 'LANG' in t.splitlines()[-1]: print('en' if 'svarsspråk = engelska' in t else 'sv'); sys.exit()\n"
                        "if 'MINNS' in t.splitlines()[-1]: print('minns' if 'före rensa' in t else 'glömt'); sys.exit()\n"
                        "print('Svar: ' + t.splitlines()[-1].removeprefix('Joel säger nu: '))\n")
        cls.ha = ThreadingHTTPServer(("127.0.0.1", 0), FakeHomeAssistant)
        cls.ha.tts_requests = []
        threading.Thread(target=cls.ha.serve_forever, daemon=True).start()
        (cls.tmp / "ha-token").write_text("ha-token\n")
        cls.port = free_port()
        env = {**os.environ, "HALLPANEL_TOKEN": TOKEN, "JARVIS_AGENT_CMD": f"{sys.executable} {stub}",
               "HA_URL": f"http://127.0.0.1:{cls.ha.server_port}", "HA_TOKEN_FILE": str(cls.tmp / "ha-token"),
               "JARVIS_VOICE_EFFECT": "none",        # the AI tone has its own test below
               "JARVIS_BACKGROUND_FILE": str(cls.tmp / "background.json"),
               "JARVIS_PANEL_WWW": str(cls.tmp / "panel-www")}
        cls.proc = subprocess.Popen([sys.executable, str(ROOT / "server" / "hallpanel_server.py"), "--port", str(cls.port),
                                     "--bind", "127.0.0.1", "--assets", str(cls.tmp / "www"), "--db", str(cls.tmp / "h.db")],
                                    env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.8)

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait()
        cls.ha.shutdown()

    def req(self, path, data=None, token=TOKEN, headers=None):
        h = {"X-Hallpanel-Token": token} if token else {}
        h.update(headers or {})
        r = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data, headers=h,
                                   method="POST" if data is not None else "GET")
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                return resp.status, resp.read(), dict(resp.headers)
        except urllib.error.HTTPError as e:
            with e:
                return e.code, e.read(), dict(e.headers)

    def converse(self, text):
        code, body, _ = self.req("/converse", json.dumps({"text": text, "conversation_id": "c"}).encode())
        return code, json.loads(body)

    def test_asset_served_with_etag_and_304(self):
        code, body, headers = self.req("/assets/bg.png", token=None)
        self.assertEqual((code, body), (200, b"\x89PNG fake"))
        code, _, _ = self.req("/assets/bg.png", token=None, headers={"If-None-Match": headers["ETag"]})
        self.assertEqual(code, 304)

    def test_asset_traversal_and_missing(self):
        self.assertEqual(self.req("/assets/%2e%2e/h.db", token=None)[0], 404)
        self.assertEqual(self.req("/assets/nope.png", token=None)[0], 404)

    def test_token_required(self):
        self.assertEqual(self.req("/history", token=None)[0], 401)
        self.assertEqual(self.req("/converse", b'{"text":"hej"}', token="wrong")[0], 401)

    def test_bad_body(self):
        self.assertEqual(self.req("/converse", b"not json")[0], 400)

    def test_negative_content_length_is_refused_at_once(self):
        # read(-1) read until the client closed and skipped the body caps (2026-11-02 review). The socket
        # stays open here, so a hang shows as a timeout instead of an answer.
        for path in ("/converse", "/turn", "/voice"):
            with socket.create_connection(("127.0.0.1", self.port), timeout=5) as s:
                s.sendall((f"POST {path} HTTP/1.1\r\nHost: x\r\nX-Hallpanel-Token: {TOKEN}\r\n"
                           "Content-Length: -1\r\n\r\n{}").encode())
                self.assertTrue(s.recv(64).startswith(b"HTTP/1.0 400"), path)

    def test_converse_stores_history_and_timing(self):
        code, res = self.converse("hej Jarvis")
        self.assertEqual(code, 200)
        self.assertEqual(res["reply"], "Svar: hej Jarvis")
        self.assertTrue(res["ok"])
        self.assertIsNotNone(res["timing"]["total_s"])
        msgs = json.loads(self.req("/history?limit=2")[1])["messages"]
        self.assertEqual([(m["role"], m["text"]) for m in msgs], [("user", "hej Jarvis"), ("jarvis", "Svar: hej Jarvis")])

    def test_speak_returns_trimmed_wav(self):
        code, body, headers = self.req("/speak", json.dumps({"text": "Hej Joel"}).encode())
        self.assertEqual((code, headers["Content-Type"]), (200, "audio/wav"))
        with wave.open(io.BytesIO(body)) as w:
            seconds = w.getnframes() / w.getframerate()
        self.assertAlmostEqual(seconds, 0.5 + 0.3 + 0.5, delta=0.05)    # the 1.2 s pause became 0.3 s

    def test_real_speak_endpoint_reuses_finished_audio_without_another_cloud_request(self):
        before = len(self.ha.tts_requests)
        payload = {"text": "Återanvänd den här meningen.", "language": "sv"}
        code, first, _ = self.req("/speak", json.dumps(payload).encode())
        self.assertEqual(code, 200)
        self.assertEqual(len(self.ha.tts_requests), before + 1)
        payload["language"] = None
        code, second, _ = self.req("/speak", json.dumps(payload).encode())
        self.assertEqual((code, second), (200, first))
        self.assertEqual(len(self.ha.tts_requests), before + 1)

    @unittest.skipUnless(shutil.which("ffmpeg"), "no ffmpeg")
    def test_ai_tone_keeps_format_and_loudness_limit(self):
        sys.path.insert(0, str(ROOT / "server"))
        import voice
        tone = array.array("h", (int(8000 * math.sin(2 * math.pi * 220 * n / 16000)) for n in range(16000)))
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(tone.tobytes())
        with wave.open(io.BytesIO(voice.ai_tone(buf.getvalue()))) as w:
            self.assertEqual((w.getframerate(), w.getnchannels(), w.getsampwidth()), (16000, 1, 2))
            seconds = w.getnframes() / w.getframerate()
            out = array.array("h", w.readframes(w.getnframes()))
        self.assertAlmostEqual(seconds, 1.07, delta=0.01)           # the echo's 70 ms tail
        self.assertLess(max(abs(x) for x in out), 32767)            # the limiter keeps it from clipping
        self.assertNotEqual(out[:16000].tobytes(), tone.tobytes())

    def test_speak_needs_token_and_text(self):
        self.assertEqual(self.req("/speak", b'{"text":"hej"}', token=None)[0], 401)
        self.assertEqual(self.req("/speak", b'{"text":"  "}')[0], 400)
        self.assertEqual(self.req("/warm", b"", token=None)[0], 401)

    def test_calendar_status_requires_auth_and_exposes_no_event_text(self):
        self.assertEqual(self.req('/calendar/status',token=None)[0],401)
        code,body,_=self.req('/calendar/status')
        self.assertEqual(code,200)
        self.assertIsInstance(json.loads(body),dict)
        self.assertNotIn(b'Authorization',body)

    def test_sl_status_requires_auth_and_exposes_no_departure_text(self):
        self.assertEqual(self.req('/sl/status',token=None)[0],401)
        code,body,_=self.req('/sl/status')
        self.assertEqual(code,200)
        self.assertIsInstance(json.loads(body),dict)
        self.assertNotIn(b'Hjulsta',body)

    def test_language_is_per_request_and_reaches_the_voice(self):
        for language in ("en", "sv", None):
            payload = {"text": "LANG", "language": language}
            code, body, _ = self.req("/converse", json.dumps(payload).encode())
            self.assertEqual(code, 200)
            self.assertEqual(json.loads(body)["reply"], language or "sv")
        code, _, _ = self.req("/speak", b'{"text":"Hello.","language":"en"}')
        self.assertEqual(code, 200)
        self.assertEqual((self.ha.last_tts["language"], self.ha.last_tts["options"]["voice"]),
                         ("en-GB", "RyanNeural"))
        for path in ("/converse", "/speak"):
            self.assertEqual(self.req(path, b'{"text":"Hello.","language":"de"}')[0], 400)

    def test_converse_streams_json_lines(self):
        code, body, headers = self.req("/converse", json.dumps({"text": "hej strömning", "stream": True}).encode())
        self.assertEqual((code, headers["Content-Type"].split(";")[0]), (200, "application/x-ndjson"))
        lines = [json.loads(x) for x in body.decode().splitlines()]
        self.assertEqual("".join(x.get("delta", "") for x in lines).strip(), "Svar: hej strömning")
        self.assertEqual((lines[-1]["done"], lines[-1]["reply"], lines[-1]["ok"]), (True, "Svar: hej strömning", True))

    def test_first_lookup_is_said_before_the_rest_is_looked_up(self):
        # Joel, 2026-11-01: "vilket plan ... en restaurang dit" should open with the plane and where it goes,
        # not 18 s of silence; and the panel stands in the bookshelf, not the hallway.
        sys.path.insert(0, str(ROOT / "server"))
        import prompts
        text = " ".join(prompts.INSTRUCTIONS.split())
        self.assertIn("säger du det du fick först, kort, i den meningen", text)
        self.assertNotIn("i hallen", text)

    def test_times_are_made_speakable(self):
        sys.path.insert(0, str(ROOT / "server"))
        import voice
        self.assertEqual(voice.speakable("På fredag den\u202f23 oktober, klockan\u202f9\u202f55–10\u202f10."),
                         "På fredag den 23 oktober, klockan 9.55–10.10.")
        self.assertEqual(voice.speakable("Kl. 10.20 och 2026"), "Kl. 10.20 och 2026")
        self.assertEqual(voice.speakable("till resten av kroppen — rätt ambitiöst"), "till resten av kroppen, rätt ambitiöst")
        self.assertEqual(voice.speakable("kroppen—rätt och kungen – han"), "kroppen, rätt och kungen, han")
        self.assertEqual(voice.speakable("Besök 10–12, kl. 10 - 12 och 18:28–19:00"),
                         "Besök 10–12, kl. 10 - 12 och 18:28–19:00")             # ranges stay ranges
        self.assertEqual(voice.speakable("Klart, — sen"), "Klart, sen")

    def test_laundry_hours_are_said_with_noll(self):
        # Joel, 2026-11-01: the screen shows "06-09", Mattias says "nollsex till nollnio".
        sys.path.insert(0, str(ROOT / "server"))
        import voice
        self.assertEqual(voice.spoken_hours("Ledigt 00-03, 06–09, 09-12 och 21-24."),
                         "Ledigt nollnoll till nolltre, nollsex till nollnio, nollnio till 12 och 21 till 24.")
        self.assertEqual(voice.spoken_hours("Den 2026-11-01, 10.20-11.30, 18:00-21:00, 30-40 och 7-9"),
                         "Den 2026-11-01, 10.20-11.30, 18:00-21:00, 30-40 och 7-9")    # not hours of a pass

    def test_god_morgon_only_on_the_first_morning_question(self):
        sys.path.insert(0, str(ROOT / "server"))
        import hallpanel_server as hs
        from datetime import datetime
        from zoneinfo import ZoneInfo
        h = hs.History(self.tmp / "morning.db")

        def say(local_time):                      # a question stored at this Stockholm time
            ts = datetime.fromisoformat(f"2026-10-22T{local_time}").replace(tzinfo=ZoneInfo("Europe/Stockholm"))
            h.db.execute("INSERT INTO messages (ts, conversation_id, role, text, ok) VALUES (?, 'c', 'user', 'x', 1)",
                         (ts.astimezone(hs.timezone.utc).isoformat(timespec="seconds"),))
        at = lambda t: datetime.fromisoformat(f"2026-10-22T{t}").replace(tzinfo=ZoneInfo("Europe/Stockholm"))
        say("02:10:00")                           # in the night: does not count
        say("07:30:00")
        self.assertTrue(hs.first_question_this_morning(h, at("07:30:05")))
        self.assertFalse(hs.first_question_this_morning(h, at("11:00:00")))      # not morning
        say("07:45:00")
        self.assertFalse(hs.first_question_this_morning(h, at("07:45:05")))      # second question

    def test_wake_during_an_answer_does_not_queue_a_warm_up(self):
        sys.path.insert(0, str(ROOT / "server"))
        import hallpanel_server as hs
        agent = SimpleNamespace(start_warm=Mock())
        with patch.object(hs, "AGENT", agent):
            hs.AGENT_LOCK.acquire()
            worker = threading.Thread(target=hs.warm_agent)
            try:
                worker.start()
                worker.join(timeout=0.5)
                self.assertFalse(worker.is_alive(), "warm-up queued behind an active answer")
                agent.start_warm.assert_not_called()
            finally:
                hs.AGENT_LOCK.release()
                worker.join(timeout=1)

    def test_the_question_reaches_the_gated_tools_only_while_it_is_answered(self):
        # 2026-11-02 review: the words reached tvatt_boka and the morning tools through module globals, set
        # only in-process and never cleared, so a command-mode agent refused every change and a stale question
        # stayed readable. Now the in-process agent holds the Turn during ask(), and a command gets JARVIS_SPOKEN.
        sys.path.insert(0, str(ROOT / "server"))
        import hallpanel_server as hs
        import spoken_change
        recent = [{"role": "user", "text": "Boka tvätt på onsdag"}, {"role": "jarvis", "text": "Vilket pass?"}]
        expected = spoken_change.Turn("21 till 24", "Vilket pass?", "Boka tvätt på onsdag")
        seen = []
        agent = SimpleNamespace(fresh_agenda=lambda: None, fresh_home=lambda: None, home=None, label="fake",
                                early=set(), last_tools=[], last_rounds=[], screen=SimpleNamespace(encoded=lambda: ""),
                                spoken=spoken_change.Turn())
        agent.ask = lambda prompt, **kw: (seen.append(agent.spoken), ("Klart.", 0.0, {}))[1]
        with patch.object(hs, "AGENT", agent), patch.object(hs, "FALLBACK", None):
            hs._ask_agent("21 till 24", recent, "", time.monotonic(), 0, None, "sv", None)
        self.assertEqual(seen, [expected])
        self.assertEqual(agent.spoken, spoken_change.Turn())
        stub = self.tmp / "spoken_agent.py"
        stub.write_text("import os, sys\nsys.stdin.read()\nprint('spoken ' + os.environ.get('JARVIS_SPOKEN', 'missing'))\n")
        with patch.object(hs, "AGENT", None), patch.dict(os.environ, {"JARVIS_AGENT_CMD": f"{sys.executable} {stub}"}):
            reply, _ = hs._ask_agent("21 till 24", recent, "", time.monotonic(), 0, None, "sv", None)
        with patch.dict(os.environ, {"JARVIS_SPOKEN": reply.removeprefix("spoken ")}):
            self.assertEqual(spoken_change.Turn.from_env(), expected)

    def test_failed_warm_up_releases_the_agent_lock(self):
        sys.path.insert(0, str(ROOT / "server"))
        import hallpanel_server as hs
        with patch.object(hs, "AGENT", SimpleNamespace(start_warm=Mock(side_effect=RuntimeError("offline")))):
            self.assertIsNone(hs.warm_agent())
        self.assertTrue(hs.AGENT_LOCK.acquire(blocking=False))
        hs.AGENT_LOCK.release()

    def test_reply_keeps_model_and_times(self):
        import sqlite3
        self.converse("logga mig")
        row = sqlite3.connect(self.tmp / "h.db").execute(
            "SELECT brain, total_ms, error FROM messages WHERE role = 'jarvis' ORDER BY id DESC LIMIT 1").fetchone()
        self.assertTrue(row[0])                            # the stub agent's command name
        self.assertGreaterEqual(row[1], 0)
        self.assertIsNone(row[2])

    def test_old_history_gets_the_new_columns(self):
        import sqlite3
        sys.path.insert(0, str(ROOT / "server"))
        import hallpanel_server as hs
        old = self.tmp / "old.db"
        con = sqlite3.connect(old)
        con.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, ts TEXT NOT NULL, conversation_id TEXT, "
                    "role TEXT NOT NULL, text TEXT NOT NULL, ok INTEGER NOT NULL DEFAULT 1)")
        con.execute("INSERT INTO messages (ts, role, text) VALUES ('2026-10-21T10:00:00+00:00', 'user', 'gammal')")
        con.commit()
        con.close()
        h = hs.History(old)
        h.add("c", "jarvis", "ny", meta={"brain": "luna gpt-6-luna", "total_ms": 1460, "tools": [{"name": "agenda"}]})
        rows = h.db.execute("SELECT text, brain, total_ms, tools FROM messages ORDER BY id").fetchall()
        self.assertEqual(rows[0], ("gammal", None, None, None))
        self.assertEqual(rows[1], ("ny", "luna gpt-6-luna", 1460, '[{"name": "agenda"}]'))

    def test_clear_forgets_the_conversation_but_keeps_the_history(self):
        self.converse("före rensa")
        self.assertEqual(self.converse("MINNS du")[1]["reply"], "minns")
        code, body, _ = self.req("/clear", b"{}")
        self.assertEqual((code, json.loads(body)), (200, {"cleared": True}))
        self.assertEqual(json.loads(self.req("/history?limit=50")[1])["messages"], [])
        self.assertEqual(self.converse("MINNS du nu")[1]["reply"], "glömt")
        msgs = json.loads(self.req("/history?limit=50")[1])["messages"]
        self.assertEqual([m["text"] for m in msgs], ["MINNS du nu", "glömt"])
        everything = [m["text"] for m in json.loads(self.req("/history?limit=500&all=1")[1])["messages"]]
        self.assertIn("före rensa", everything)
        self.assertEqual(self.req("/clear", b"{}", token=None)[0], 401)

    def test_panel_tap_cancels_a_reminder_by_id(self):
        """Ta bort on Hemmet's reminder page: POST /reminders/cancel, shown in rem_list until then."""
        import sqlite3
        db = sqlite3.connect(self.tmp / "h.db")
        n = db.execute("INSERT INTO reminders (ts, due, text) VALUES ('2026-11-03T10:00:00+00:00', "
                       "'2099-01-01T10:00:00+00:00', 'Panelens test')").lastrowid
        db.commit()
        db.close()
        self.assertIn(f"r|{n}|", json.loads(self.req("/panel")[1])["rem_list"])
        self.assertEqual(self.req("/reminders/cancel", json.dumps({"id": n}).encode(), token=None)[0], 401)
        self.assertEqual(self.req("/reminders/cancel", b'{"id": "x"}')[0], 400)
        code, body, _ = self.req("/reminders/cancel", json.dumps({"id": n}).encode())
        self.assertEqual((code, json.loads(body)), (200, {"cancelled": True, "id": n}))
        self.assertNotIn(f"r|{n}|", json.loads(self.req("/panel")[1])["rem_list"])
        self.assertEqual(self.req("/reminders/cancel", json.dumps({"id": n}).encode())[0], 404)

    def test_panel_without_home_assistant(self):
        code, body, _ = self.req("/panel")
        self.assertEqual(code, 200)
        panel = json.loads(body)
        self.assertEqual((panel["alert"], panel["online"], panel["reminders"]), ("off", "on", ""))
        self.assertIn("art_title", panel)
        self.assertEqual(self.req("/panel", token=None)[0], 401)

    def test_background_choice(self):
        code, body, _ = self.req("/background")
        self.assertEqual(code, 200)
        self.assertIn("Stjärnenatten", json.loads(body)["options"])
        code, body, _ = self.req("/background", json.dumps({"choice": "Mona Lisa"}).encode())
        self.assertEqual(code, 400)
        code, body, _ = self.req("/background", json.dumps({"choice": "Mandelblom"}).encode())
        self.assertEqual((code, json.loads(body)["choice"]), (200, "Mandelblom"))
        self.assertEqual(json.loads(self.req("/panel")[1])["art_title"], "Mandelblom")
        self.assertEqual(self.req("/background", b"{}", token=None)[0], 401)

    def test_speaker_choice(self):
        """The panel's Ljud ut setting (select.jarvis_ljud_ut): without a speaker set up, only Panelen."""
        code, body, _ = self.req("/speaker")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)["options"][0], "Panelen")
        code, body, _ = self.req("/speaker", b"{}")
        self.assertEqual((code, json.loads(body)["choice"]), (400, "Panelen"))
        self.assertEqual(self.req("/speaker", token=None)[0], 401)
        self.assertEqual(self.req("/speaker", b'{"choice": "Panelen"}', token=None)[0], 401)
        self.assertEqual(self.req("/speaker/reconnect", b"{}", token=None)[0], 401)   # Koppla om needs the token

    def test_agent_failure_is_honest(self):
        code, res = self.converse("FAIL now")
        self.assertEqual(code, 200)
        self.assertFalse(res["ok"])
        self.assertEqual(res["reply"], "Jag kunde inte svara just nu.")

    def turn_row(self, turn_id):
        import sqlite3
        con = sqlite3.connect(self.tmp / "h.db")
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT * FROM voice_turns WHERE id = ?", (turn_id,)).fetchone()
        con.close()
        return dict(row) if row else None

    def report(self, turn_id, stage, report):
        return self.req("/turn", json.dumps({"turn": turn_id, "stage": stage, "report": report}).encode())[0]

    def test_voice_turn_keeps_every_stage_in_any_order(self):
        # 2026-10-27: failed recognitions and silent wakes left no lasting trace (a one-day journal, 10 HA runs).
        turn = "0123456789abcdef"
        self.assertEqual(self.report(turn, "tts", {"outcome": "complete", "sentences": 2, "failed": 0,
                                                    "first_audio_ms": 640, "audio_ms": 3100}), 200)
        code, _, _ = self.req("/transcribe", b"not a wav at all, but long enough to be read" * 2,
                              headers={"X-Hallpanel-Turn": turn})
        self.assertEqual(code, 400)                   # never reaches Groq: the WAV check comes first
        code, body, _ = self.req("/converse", json.dumps({"text": "hej tur", "conversation_id": "c",
                                                           "turn": turn}).encode())
        self.assertEqual(code, 200)
        self.assertEqual(self.report(turn, "stt", {"outcome": "text", "listen_ms": 4200, "speech_ms": 2100,
                                                    "early": True, "language": "sv", "transcript": "secret"}), 200)
        row = self.turn_row(turn)
        self.assertEqual((row["stt"], row["listen_ms"], row["speech_ms"], row["early"], row["language"]),
                         ("text", 4200, 2100, 1, "sv"))
        self.assertEqual((row["recognitions"], row["stt_error"]), (1, "invalid recording"))
        self.assertEqual((row["tts"], row["sentences"], row["sentences_failed"], row["audio_ms"]),
                         ("complete", 2, 0, 3100))
        self.assertEqual(row["reply_ok"], 1)
        msgs = json.loads(self.req("/history?limit=1")[1])["messages"]
        self.assertEqual(msgs[-1]["text"], "Svar: hej tur")
        self.assertNotIn("secret", json.dumps(row))
        # The turn began listen_ms before the STT report, not when the TTS report happened to arrive.
        self.assertLess(row["started"], row["updated"])

    def test_voice_turn_reports_are_checked(self):
        self.assertEqual(self.report("not-a-turn", "stt", {"outcome": "text"}), 400)
        self.assertEqual(self.report("fedcba9876543210", "brain", {}), 400)
        self.assertEqual(self.req("/turn", b'{"turn": "fedcba9876543210"}', token=None)[0], 401)
        self.assertEqual(self.report("fedcba9876543210", "stt", {"outcome": "rm -rf", "listen_ms": -5,
                                                                  "early": "yes"}), 200)
        self.assertEqual(self.turn_row("fedcba9876543210")["stt"], None)
        # A turn id that is not ours on /converse is ignored, and the answer still comes.
        code, _, _ = self.req("/converse", json.dumps({"text": "x", "turn": "'; DROP TABLE"}).encode())
        self.assertEqual(code, 200)

    def test_polls_stay_out_of_the_journal_but_failures_do_not(self):
        sys.path.insert(0, str(ROOT / "server"))
        import hallpanel_server as hs
        for path in ("/panel", "/background", "/speaker", "/wakeup/status", "/history?limit=5", "/assets/bg.png"):
            self.assertTrue(hs.quiet_request("GET", path, 200), path)
        self.assertFalse(hs.quiet_request("GET", "/panel", 401))
        self.assertFalse(hs.quiet_request("GET", "/panel", 500))
        self.assertFalse(hs.quiet_request("POST", "/converse", 200))
        self.assertFalse(hs.quiet_request("POST", "/transcribe", 503))
        self.assertTrue(hs.quiet_request("POST", "/turn", 200))
        from http import HTTPStatus
        self.assertTrue(hs.quiet_request("GET", "/panel", HTTPStatus.OK))


class ServerStartTest(unittest.TestCase):
    """2026-11-02 review: without the token the whole API ran open, so the startup now refuses."""

    def spawn_without_token(self, allow, timeout):
        """The server on port 0 with no HALLPANEL_TOKEN; its own scratch db and assets dir."""
        tmp = tempfile.mkdtemp(prefix="hallpanel-start-")
        assets = Path(tmp) / "www"
        assets.mkdir()
        env = os.environ.copy()
        env.pop("HALLPANEL_TOKEN", None)
        if allow:
            env["HALLPANEL_ALLOW_NO_TOKEN"] = "1"
        else:
            env.pop("HALLPANEL_ALLOW_NO_TOKEN", None)
        try:
            return subprocess.run([sys.executable, str(ROOT / "server" / "hallpanel_server.py"), "--port", "0",
                                   "--assets", str(assets), "--db", str(Path(tmp) / "h.db")],
                                  env=env, capture_output=True, text=True, timeout=timeout)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_no_token_refuses_to_start(self):
        proc = self.spawn_without_token(allow=False, timeout=120)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("no HALLPANEL_TOKEN", proc.stderr)

    def test_the_escape_hatch_is_explicit(self):
        # The pass is the server's own "serving" line; any early exit (an import error too) fails, where the
        # old "not exit 2" check passed it (2026-11-02 review).
        tmp = Path(tempfile.mkdtemp(prefix="hallpanel-start-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "www").mkdir()
        env = {k: v for k, v in os.environ.items() if k != "HALLPANEL_TOKEN"}
        env["HALLPANEL_ALLOW_NO_TOKEN"] = "1"
        proc = subprocess.Popen([sys.executable, str(ROOT / "server" / "hallpanel_server.py"), "--port", "0",
                                 "--assets", str(tmp / "www"), "--db", str(tmp / "h.db")],
                                env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(proc.wait)
        self.addCleanup(proc.kill)
        line = []
        reader = threading.Thread(target=lambda: line.append(proc.stdout.readline()), daemon=True)
        reader.start()
        reader.join(60)
        self.assertTrue(line and line[0].startswith("hallpanel server on"),
                        f"exit {proc.poll()}: {line} {proc.stderr.read() if proc.poll() is not None else ''}")


if __name__ == "__main__":
    unittest.main()
