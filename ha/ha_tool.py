#!/usr/bin/env python3
"""Set up and test the Jarvis voice path inside Home Assistant. Runs inside the Home Assistant
container on Jarvis (it uses Home Assistant's own aiohttp), talking to Home Assistant on 127.0.0.1.

The first line on stdin is JSON with the tokens, so they never appear on a command line:

  setup   {"ha_token": "...", "jarvis_token": "..."}
          Adds the Jarvis integration (if it is not there yet) and creates or updates the Assist
          pipeline "Jarvis": Home Assistant Cloud speech to text, Home Assistant's own commands
          first, then the Jarvis agent, and Mattias with shortened pauses. The Jarvis Cloud STT
          wrapper allows 1.5 s pauses and 60 s per recording. Prints what it did.
          With "speaker_port": N it also adds MPD on 127.0.0.1:N (server/hallpanel-mpd.service) as the
          media player SPEAKER_ENTITY, which plays the radio on Jarvis's Bluetooth speaker.

  ask     {"ha_token": "...", "conversation_id": "...", "frames": false} followed by a 16 kHz mono WAV
          Runs the pipeline "Jarvis" from speech to text to spoken reply, the way the panel will:
          when Home Assistant starts streaming the reply (intent-progress with tts_start_streaming),
          it fetches the audio at once, as the panel's media player does. Prints
          {"said", "reply", "ok", "audio_b64", "timing", "conversation_id"}; timing.first_audio_s is
          when the first audio arrived, counted from the end of the recording.
          With "warm_s": N it first calls jarvis_agent.warm and waits N seconds, as the panel warms
          the agent on the wake word while Joel is still talking. With "fresh_tts": true it first
          empties Home Assistant's speech cache, so a repeated reply is not read faster from cache.
          With "frames": true it writes the audio to stdout as it arrives instead, as frames of
          b"A" + 4-byte big-endian length + bytes, and ends with b"J" + length + the JSON (no audio_b64).
          With "live": true (needs "frames") the rest of stdin is raw 16 kHz mono 16-bit audio sent
          while Joel speaks, as the panel streams its microphone. Home Assistant's own voice detection
          decides when he is done (1.5 s of silence with Jarvis Cloud). With "frames", b"E" frames report events as they
          happen: stt-vad-start, stt-vad-end, stt-end (with "text", what Joel said) and delta (with
          "text", the reply as the agent writes it).

tools/deploy_jarvis.sh runs setup; tools/voice_loop.py runs ask through ssh.
"""
import asyncio
import base64
import io
import importlib.util
import json
import struct
import sys
import time
import wave
from pathlib import Path

import aiohttp

HA = "http://127.0.0.1:8123"
SPEAKER_ENTITY = "media_player.bluetooth_hogtalare"     # JARVIS_SPEAKER_PLAYER in server.env; docs/bluetooth-speaker.md
PIPELINE = {
    "name": "Jarvis",
    "language": "sv",
    "conversation_engine": None,          # filled in: the Jarvis conversation entity
    "conversation_language": "sv",
    "prefer_local_intents": True,         # "tänd lampan" is handled by Home Assistant itself
    "stt_engine": None,                   # Jarvis Cloud: 1.5 s pauses, max 60 s (filled in from registry)
    "stt_language": "sv-SE",
    "tts_engine": None,                   # filled in: the Jarvis Mattias entity
    "tts_language": "sv-SE",
    "tts_voice": None,
    "wake_word_entity": None,             # the wake word runs on the panel
    "wake_word_id": None,
}


class WS:
    """Home Assistant's websocket API: one command at a time, plus the event stream of a run."""

    def __init__(self, session, token):
        self.session, self.token, self.n = session, token, 0

    async def __aenter__(self):
        self.ws = await self.session.ws_connect(f"{HA}/api/websocket", max_msg_size=0)
        await self.ws.receive_json()
        await self.ws.send_json({"type": "auth", "access_token": self.token})
        if (await self.ws.receive_json()).get("type") != "auth_ok":
            raise SystemExit("Home Assistant refused the token")
        return self

    async def __aexit__(self, *exc):
        await self.ws.close()

    async def send(self, msg):
        self.n += 1
        await self.ws.send_json({"id": self.n, **msg})
        return self.n

    async def call(self, msg):
        n = await self.send(msg)
        while True:
            r = await self.ws.receive_json()
            if r.get("id") == n and r.get("type") == "result":
                if not r.get("success"):
                    raise SystemExit(f"{msg['type']} failed: {r.get('error')}")
                return r.get("result")


async def setup(session, args):
    headers = {"Authorization": f"Bearer {args['ha_token']}"}
    done = []
    async with session.get(f"{HA}/api/config/config_entries/entry?domain=jarvis_agent", headers=headers) as r:
        entries = await r.json()
    if not entries:
        async with session.post(f"{HA}/api/config/config_entries/flow", headers=headers,
                                json={"handler": "jarvis_agent", "show_advanced_options": False}) as r:
            flow = await r.json()
        async with session.post(f"{HA}/api/config/config_entries/flow/{flow['flow_id']}", headers=headers,
                                json={"url": "http://127.0.0.1:8765", "token": args["jarvis_token"]}) as r:
            result = await r.json()
        if result.get("type") != "create_entry":
            raise SystemExit(f"adding the integration failed: {result.get('errors') or result}")
        done.append("added the Jarvis integration")
        await asyncio.sleep(3)
    async with WS(session, args["ha_token"]) as ws:
        ents = [e for e in await ws.call({"type": "config/entity_registry/list"}) if e["platform"] == "jarvis_agent"]
        by_domain = {e["entity_id"].split(".")[0]: e["entity_id"] for e in ents}
        fields = {**PIPELINE, "conversation_engine": by_domain["conversation"], "tts_engine": by_domain["tts"],
                  "stt_engine": by_domain["stt"]}
        pipes = await ws.call({"type": "assist_pipeline/pipeline/list"})
        old = next((p for p in pipes["pipelines"] if p["name"] == "Jarvis"), None)
        if old is None:
            new = await ws.call({"type": "assist_pipeline/pipeline/create", **fields})
            done.append(f"created the Assist pipeline Jarvis ({new['id']})")
        elif any(old.get(k) != v for k, v in fields.items()):
            await ws.call({"type": "assist_pipeline/pipeline/update", "pipeline_id": old["id"], **fields})
            done.append("updated the Assist pipeline Jarvis")
        else:
            done.append("the Assist pipeline Jarvis was already right")
        if args.get("speaker_port"):
            done.append(await speaker_player(session, headers, ws, int(args["speaker_port"])))
    print(json.dumps({"done": done, "entities": sorted(by_domain.values()), "pipeline": fields}, ensure_ascii=False,
                     indent=1))


async def speaker_player(session, headers, ws, port):
    """MPD on Jarvis as SPEAKER_ENTITY. Home Assistant's MPD flow refuses the same host and port twice, so a
    second run only checks the name."""
    async def mpd_entities():
        return [e for e in await ws.call({"type": "config/entity_registry/list"}) if e["platform"] == "mpd"]
    if any(e["entity_id"] == SPEAKER_ENTITY for e in await mpd_entities()):
        return f"{SPEAKER_ENTITY} was already there"
    async with session.post(f"{HA}/api/config/config_entries/flow", headers=headers,
                            json={"handler": "mpd", "show_advanced_options": False}) as r:
        flow = await r.json()
    async with session.post(f"{HA}/api/config/config_entries/flow/{flow['flow_id']}", headers=headers,
                            json={"host": "127.0.0.1", "port": port}) as r:
        result = await r.json()
    if result.get("type") == "create_entry":
        entry_id = result["result"]["entry_id"]
    elif result.get("reason") == "already_configured":
        entry_id = None
    else:
        raise SystemExit(f"adding MPD for the Bluetooth speaker failed: {result.get('errors') or result}")
    for _ in range(20):                 # the entity appears once the new entry has set up
        mine = [e for e in await mpd_entities() if entry_id is None or e["config_entry_id"] == entry_id]
        if len(mine) == 1:
            await ws.call({"type": "config/entity_registry/update", "entity_id": mine[0]["entity_id"],
                           "new_entity_id": SPEAKER_ENTITY, "name": "Bluetooth-högtalare"})
            return f"added {SPEAKER_ENTITY} (MPD on port {port})"
        await asyncio.sleep(0.5)
    raise SystemExit("MPD was added, but its media player did not appear as one entity")


def frame(kind, data):
    sys.stdout.buffer.write(kind + struct.pack(">I", len(data)) + data)
    sys.stdout.buffer.flush()


async def ask(session, args, stdin):
    live = bool(args.get("live"))
    pcm = b""
    if not live:
        with wave.open(io.BytesIO(await stdin.read())) as w:
            if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (16000, 1, 2):
                raise SystemExit("send 16 kHz mono 16-bit WAV")
            pcm = w.readframes(w.getnframes())
    framed = bool(args.get("frames"))
    heard = asyncio.Event()                     # Home Assistant has decided Joel is done talking

    async def stream_microphone(ws, handler):
        """Pass the audio on as it arrives, until Home Assistant has heard enough or it ends."""
        while not heard.is_set():
            try:
                chunk = await stdin.readexactly(3200)               # 100 ms, whole samples
            except asyncio.IncompleteReadError as end:
                chunk = end.partial[:len(end.partial) // 2 * 2]
                if chunk and not heard.is_set():
                    await ws.ws.send_bytes(bytes([handler]) + chunk)
                if not heard.is_set():
                    await ws.ws.send_bytes(bytes([handler]))        # the sender stopped: end of audio
                return
            if not heard.is_set():
                await ws.ws.send_bytes(bytes([handler]) + chunk)

    if args.get("fresh_tts"):
        async with session.post(f"{HA}/api/services/tts/clear_cache",
                                headers={"Authorization": f"Bearer {args['ha_token']}"}) as r:
            await r.read()
    if args.get("warm_s"):
        async with session.post(f"{HA}/api/services/jarvis_agent/warm",
                                headers={"Authorization": f"Bearer {args['ha_token']}"}) as r:
            await r.read()
        await asyncio.sleep(float(args["warm_s"]))
    t0 = time.monotonic()
    out = {"said": "", "reply": "", "ok": False, "timing": {}}
    marks, chunks = {}, []

    async def fetch(url):
        """Read the reply audio as it is made, like the panel's media player."""
        async with session.get(url if url.startswith("http") else HA + url) as r:
            async for chunk in r.content.iter_any():
                marks.setdefault("first_audio", time.monotonic())
                if framed:
                    frame(b"A", chunk)
                else:
                    chunks.append(chunk)
        marks["audio_done"] = time.monotonic()

    fetcher, url, mic_task = None, None, None
    async with WS(session, args["ha_token"]) as ws:
        t_conn = time.monotonic()
        pipes = await ws.call({"type": "assist_pipeline/pipeline/list"})
        pid = next(p["id"] for p in pipes["pipelines"] if p["name"] == "Jarvis")
        run = {"type": "assist_pipeline/run", "start_stage": "stt", "end_stage": "tts", "pipeline": pid,
               "input": {"sample_rate": 16000, **({"no_vad": True} if args.get("no_vad") else {})}}
        if args.get("conversation_id"):
            run["conversation_id"] = args["conversation_id"]
        n = await ws.send(run)
        while True:
            msg = await ws.ws.receive_json()
            if msg.get("id") != n:
                continue
            if msg.get("type") == "result" and not msg.get("success"):
                raise SystemExit(f"pipeline failed: {msg.get('error')}")
            ev = msg.get("event") or {}
            kind, data = ev.get("type"), ev.get("data") or {}
            if framed and kind in ("stt-vad-start", "stt-vad-end"):
                frame(b"E", json.dumps({"type": kind}).encode())
            elif framed and kind == "stt-end":
                frame(b"E", json.dumps({"type": kind, "text": data["stt_output"]["text"]}, ensure_ascii=False).encode())
            elif framed and kind == "intent-progress" and (data.get("chat_log_delta") or {}).get("content"):
                frame(b"E", json.dumps({"type": "delta", "text": data["chat_log_delta"]["content"]},
                                       ensure_ascii=False).encode())
            if kind == "run-start":
                handler = data["runner_data"]["stt_binary_handler_id"]
                url = data.get("tts_output", {}).get("url")          # known from the start when streaming
                if live:
                    mic_task = asyncio.create_task(stream_microphone(ws, handler))
                else:
                    for i in range(0, len(pcm), 3200):                 # 100 ms per message
                        await ws.ws.send_bytes(bytes([handler]) + pcm[i:i + 3200])
                    await ws.ws.send_bytes(bytes([handler]))           # end of audio
                    marks["audio_sent"] = time.monotonic()
            elif kind == "stt-vad-end":
                heard.set()
                marks["audio_sent"] = time.monotonic()                 # live: Joel is done talking
            elif kind == "stt-end":
                heard.set()
                marks["stt"] = time.monotonic()
                marks.setdefault("audio_sent", marks["stt"])
                out["said"] = data["stt_output"]["text"]
            elif kind == "intent-progress" and data.get("tts_start_streaming") and url and fetcher is None:
                marks["streaming"] = time.monotonic()
                fetcher = asyncio.create_task(fetch(url))
            elif kind == "intent-end":
                marks["intent"] = time.monotonic()
                io_ = data["intent_output"]
                out["reply"] = io_["response"]["speech"]["plain"]["speech"]
                out["conversation_id"] = io_.get("conversation_id")
                out["handled_by"] = "Home Assistant" if data.get("processed_locally") else "Jarvis"
                # True when the reply ends in a question: the panel then listens again without the wake word
                out["continue"] = bool(io_.get("continue_conversation"))
            elif kind == "tts-end":
                url = data["tts_output"]["url"]
                if fetcher is None:
                    fetcher = asyncio.create_task(fetch(url))
            elif kind == "error":
                out["error"] = data
                break
            elif kind == "run-end":
                break
    if mic_task:
        heard.set()
        mic_task.cancel()
    if fetcher:
        await fetcher
        audio = b"".join(chunks)
        out["ok"] = bool(out["reply"])
        if not framed:
            out["audio_b64"] = base64.b64encode(audio).decode()
        a, s, i = marks.get("audio_sent", t_conn), marks.get("stt", t_conn), marks.get("intent", t_conn)
        out["timing"] = {"stt_s": round(s - a, 2), "agent_s": round(i - s, 2), "tts_s": round(marks["audio_done"] - i, 2),
                         "first_audio_s": round(marks["first_audio"] - a, 2), "streamed": "streaming" in marks,
                         "server_total_s": round(marks["audio_done"] - t0, 2)}
    body = json.dumps(out, ensure_ascii=False)
    if framed:
        frame(b"J", body.encode())
    else:
        print(body)


def speech_helpers():
    """Use the integration's existing sentence splitter without importing Home Assistant."""
    path = Path("/config/custom_components/jarvis_agent/speech.py")
    spec = importlib.util.spec_from_file_location("jarvis_speech", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def groq_transcribe(session, token, wav):
    """Use the same private multilingual transcription endpoint as the panel."""
    with wave.open(io.BytesIO(wav)) as w:
        if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (16000, 1, 2):
            raise ValueError("send 16 kHz mono 16-bit WAV")
    async with session.post("http://127.0.0.1:8765/transcribe", data=wav,
                            headers={"X-Hallpanel-Token": token, "Content-Type":"audio/wav"},
                            timeout=aiohttp.ClientTimeout(total=40)) as resp:
        if resp.status != 200:
            # Never echo provider bodies or headers: they may contain credentials.
            raise ValueError(f"Jarvis speech to text returned HTTP {resp.status}")
        result = await resp.json()
    text = str(result.get("text") or "").strip()
    language = {"english": "en", "en": "en", "swedish": "sv", "sv": "sv"}.get(
        str(result.get("language") or "").lower())
    if text and language is None:
        raise ValueError("Groq did not identify Swedish or English; try a longer sentence")
    return text, language


async def ask_groq(session, args, stdin):
    """Mac experiment: Groq STT, then the existing agent and Cloud voice, streamed sentence by sentence.
    Tokens arrive on stdin over ssh, as for ask. It changes no Assist pipeline or brain setting.
    The agent still runs on Jarvis and has its normal tools and memory; HA's local intents are skipped.
    """
    out = {"said": "", "reply": "", "ok": False, "timing": {}, "conversation_id": args.get("conversation_id")}
    marks, jobs = {}, set()
    producer = None
    t0 = time.monotonic()
    try:
        if not args.get("jarvis_token"):
            raise ValueError("Jarvis token is missing on Jarvis")
        speech = speech_helpers()
        wav = await stdin.read()
        # Start STT timing after the Mac's recording has arrived, excluding ssh/upload.
        t0 = time.monotonic()
        text, language = await groq_transcribe(session, args["jarvis_token"], wav)
        marks["stt"] = time.monotonic()
        out.update(said=text, language=language)
        if not text:
            return
        frame(b"E", json.dumps({"type": "stt-end", "text": text, "language": language}, ensure_ascii=False).encode())
        headers = {"X-Hallpanel-Token": args["jarvis_token"]}
        base = "http://127.0.0.1:8765"
        queue = asyncio.Queue()

        async def deltas():
            payload = {"text": text, "language": language, "stream": True,
                       "conversation_id": args.get("conversation_id")}
            async with session.post(base + "/converse", json=payload, headers=headers,
                                    timeout=aiohttp.ClientTimeout(total=90)) as resp:
                if resp.status != 200:
                    raise ValueError(f"Jarvis conversation returned HTTP {resp.status}")
                finished = False
                async for line in resp.content:
                    if not line.strip():
                        continue
                    msg = json.loads(line)
                    if msg.get("done"):
                        finished = True
                        out.update(reply=msg.get("reply", ""), ok=bool(msg.get("ok")))
                        out["continue"] = out["ok"] and out["reply"].rstrip().endswith("?")
                        marks["intent"] = time.monotonic()
                    elif delta := msg.get("delta"):
                        frame(b"E", json.dumps({"type": "delta", "text": delta}, ensure_ascii=False).encode())
                        yield delta
                if not finished:
                    raise ValueError("Jarvis conversation ended before the answer was complete")

        async def speak(sentence):
            async with session.post(base + "/speak", json={"text": sentence, "language": language}, headers=headers,
                                    timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status != 200:
                    raise ValueError(f"Jarvis voice returned HTTP {resp.status}")
                return await resp.read()

        async def produce():
            try:
                async for sentence in speech.sentences(deltas()):
                    job = asyncio.create_task(speak(sentence))
                    jobs.add(job)
                    await queue.put(job)
            finally:
                await queue.put(None)

        producer = asyncio.create_task(produce())
        fmt = None
        while (job := await queue.get()) is not None:
            audio = await job
            sentence_fmt, frames = speech.split_wav(audio)
            if fmt is None:
                fmt = sentence_fmt
                prefix = speech.stream_header(fmt)
            elif fmt != sentence_fmt:
                raise ValueError("Jarvis changed audio format between sentences")
            else:
                prefix = speech.silence(fmt)
            marks.setdefault("first_audio", time.monotonic())
            frame(b"A", prefix + frames)
        await producer
        if not fmt:
            raise ValueError("Jarvis returned no spoken answer")
        out["handled_by"] = "Jarvis"
    except (aiohttp.ClientError, TimeoutError, ValueError, EOFError, wave.Error) as err:
        out["ok"] = False
        out["error"] = {"code": "groq-voice-failed", "message": str(err)}
    finally:
        if producer and not producer.done():
            producer.cancel()
        for job in jobs:
            if not job.done():
                job.cancel()
        await asyncio.gather(*jobs, *([producer] if producer else []), return_exceptions=True)
        ended = time.monotonic()
        out["timing"] = {"stt_s": round(marks.get("stt", ended) - t0, 2),
                         "first_audio_s": round(marks["first_audio"] - t0, 2) if "first_audio" in marks else None,
                         "server_total_s": round(ended - t0, 2), "streamed": True}
        frame(b"J", json.dumps(out, ensure_ascii=False).encode())


async def main():
    cmd = sys.argv[1]
    stdin = asyncio.StreamReader(limit=2 ** 24)
    await asyncio.get_running_loop().connect_read_pipe(lambda: asyncio.StreamReaderProtocol(stdin), sys.stdin.buffer)
    args = json.loads(await stdin.readline())
    async with aiohttp.ClientSession() as session:
        if cmd == "setup":
            await setup(session, args)
        elif cmd == "ask":
            await ask(session, args, stdin)
        elif cmd == "ask-groq":
            await ask_groq(session, args, stdin)
        else:
            raise SystemExit(__doc__)


if __name__ == "__main__":
    asyncio.run(main())
