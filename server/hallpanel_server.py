#!/usr/bin/env python3
"""Jarvis-side server for the hallway panel. Standard library only, so it runs on the Pi as is.

It listens on 127.0.0.1 only. The panel never talks to it: the panel talks to Home Assistant, and
Home Assistant's Jarvis integration (ha/custom_components/jarvis_agent) calls this server on the
same machine. That keeps Jarvis's firewall as it is (decided 2026-10-21).

  GET  /assets/<name>       the panel's images (backgrounds and orb frames); the panel itself
                            downloads them from Home Assistant's /local/hallpanel/ instead
  POST /converse            {"text": "...", "conversation_id": "..."} -> {"reply": "..."}
                            asks the Jarvis agent and stores both sides in the history.
                            With "stream": true the answer comes as JSON lines while the agent
                            writes it: {"delta": "..."} ... then {"done": true, "reply", "ok", "timing"};
                            {"tools": [names]} in between when a round of tools starts
  GET  /history?limit=20    the latest messages, oldest first, for the panel after a reboot
                            and for Home Assistant
  POST /voice               a 16 kHz mono WAV of Joel speaking -> speech to text (Home Assistant
                            Cloud), the agent, text to speech (Mattias) with shortened pauses ->
                            {"said", "reply", "ok", "timing", "audio_b64"}; both sides go to the history
  POST /speak               {"text": "..."} -> WAV of Mattias reading it, pauses shortened; this is
                            the voice of Home Assistant's "Jarvis Mattias" text-to-speech entity
  POST /warm                open the agent's connections ahead of the question; the panel calls it
                            on the wake word (through Home Assistant's jarvis_agent.warm action),
                            so the warm-up runs while Joel is still talking
  GET  /background          the panel's background, the choices and the last link: {"choice", "options", "link"}
  POST /background          {"choice": "Stjärnenatten"} or {"url": "https://..."} -> that picture on the panel
                            at once (server/agents/background.py; a new picture is baked first, about 4.5 s on Jarvis);
                            Home Assistant's select.jarvis_bakgrund and text.jarvis_bakgrund_lank call it
  GET  /speaker             where the radio plays: {"available", "choice", "options"}, "Panelen" or a paired
                            Bluetooth speaker on Jarvis (server/agents/speaker.py)
  POST /speaker             {"choice": "JBL Charge 4"} connects that speaker, {"choice": "Panelen"} disconnects;
                            Home Assistant's select.jarvis_ljud_ut (the panel's Ljud ut setting) calls it
  POST /speaker/reconnect   disconnects and connects the speaker again, then heals PipeWire and Soloist in the
                            background; button.jarvis_koppla_om_hogtalaren (the panel's Koppla om) calls it
  POST /radio               {"channel": "P3", "connect": true} -> Sveriges Radio where the sound plays; with connect
                            it first connects the paired Bluetooth speaker if none is (the radio after a wakeup,
                            ha/custom_components/jarvis_agent/wakeup.py). The panel when no speaker answers
  POST /clear               Rensa on the panel (Home Assistant's jarvis_agent.clear): Jarvis forgets the
                            conversation. Nothing is deleted; the context and /history start after it
  POST /reminders/cancel    {"id": 12}: Ta bort on Hemmet's reminder page (jarvis_agent.reminder_cancel); 404 when
                            that reminder no longer waits
  GET  /panel               the home for the panel's Hemmet page and status line: short strings from
                            Home Assistant and SMHI, the pending reminders, whether Jarvis has the internet
                            and the title and credit of the picture of the day (Tavla); for the dashboard
                            the weather (SMHI), the metro from Stadshagen (SL, with JARVIS_WALK_MIN, 3 by
                            default, to the platform) and SL's disruption line, today's next events (the
                            week's when today and tomorrow are empty), and Hemmet's house line (disruptions
                            from his own providers over home, Följdfel's database)
                            (Home Assistant's sensor.jarvis_hemmet reads it every 15 s)

The history is kept forever in SQLite, with no retention limit (Joel's choice, 2026-10-21).
It is never read aloud: /history is only for screens, not for the voice.

Jarvis also speaks unasked (2026-10-22): server/watch.py looks every 20 s and says reminders Joel asked for
(server/reminders.py, the tool paminnelse), smoke, leaks, a front door left open, bad air, the laundry slot, the
next calendar event and a lost internet, through server/notify.py: on the panel with Home Assistant's
assist_satellite.announce (JARVIS_PANEL_SATELLITE) or on his phone (JARVIS_PHONE, also the tool till_telefonen).

What Jarvis remembers about Joel (server/memory.py) follows the fixed instructions in every question:
a profile in ~/.config/hallpanel/profile.json and the notes he asks for by voice ("kom ihåg att ...",
the tools kom_ihag and glom), kept in the same database. See them with show_memory.py.

Every endpoint except /assets needs the header `X-Hallpanel-Token`, and the server refuses to start without
HALLPANEL_TOKEN (a throwaway local run may set HALLPANEL_ALLOW_NO_TOKEN=1 instead).
Set it on Jarvis, so that no other program on the machine can make the agent run skills.

With JARVIS_AGENT=inprocess the agent runs inside the server and stays up between questions, keeping its
HTTPS connection to the model and its MCP session open (server/agents/openai_agent.py, or
server/agents/anthropic_agent.py for the anthropic brain; settings in JARVIS_BRAIN (luna, groq or
anthropic), JARVIS_BRAIN_MODEL, JARVIS_BRAIN_TIER, JARVIS_BRAIN_EFFORT, JARVIS_CALENDAR_MCP,
JARVIS_CALENDAR_MCP_TOKEN_FILE, JARVIS_CALENDAR_MCP_TOOLS (default CALENDAR_TOOLS in agent_core.py),
JARVIS_MOCK_TOOLS, and JARVIS_HOME_TOKEN_FILE for the read-only home status in
server/agents/home_status.py). Switch the brain with tools/brain.sh. The anthropic brain needs the
anthropic package, which tools/deploy_jarvis.sh installs in a venv that the service runs in.
It is warmed once at start and again on every /warm (the anthropic brain also writes its prompt
cache then). The warm-up reads the coming week's agenda and the home status;
for two minutes after that the agent gets it in the prompt and answers questions about those days
without a calendar lookup (JARVIS_AGENDA_DAYS, JARVIS_AGENDA_MAX_AGE). Any calendar change drops it.

Otherwise the agent is the command in JARVIS_AGENT_CMD. It gets the prompt on stdin. The default runs
Claude Code with streaming JSON output, so the server can measure the time to the first
token and the total time; a command that prints plain text works too. Every reply carries
"timing": {"first_token_s", "total_s"}.

Usage: python3 server/hallpanel_server.py [--port 8765] [--bind 127.0.0.1]
                                          [--assets server/www] [--db ~/.local/share/hallpanel/history.db]
"""
import argparse
import base64
import json
import mimetypes
import os
import re
import shlex
import sys
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent / "agents"))
from memory import Memory
from notify import Notifier
from reminders import Reminders
from watch import Watch
import background  # noqa: E402  (server/agents)
import spoken_change  # noqa: E402  (server/agents): the question's words for the gated laundry/morning changes
import quick_command
import quick_reply
import quick_router
from plain_text import no_emoji

from history import History
from prompts import (CALENDAR, GREETING, HOME, INSTRUCTIONS, MORNING, PHONE_INSTRUCTIONS, PROMPT,  # noqa: F401
                     TV_INSTRUCTIONS, WEATHER)

# Follow-ups and a yes need only the last minutes; a longer context made Sonnet repeat a morning's answer
# instead of looking again (2026-10-23).
CONTEXT_MINUTES, CONTEXT_MESSAGES, AGENT_TIMEOUT = 30, 12, 90
# How long a question waits for a warm-up's agenda and home reads that are still running, so they go into its
# prompt instead of costing a tool round (about 1.3 s on Sonnet 5.5). The reads take 0.15-0.6 s and the warm-up
# starts 1-3 s before the question, so this is normally 0 (2026-10-25). JARVIS_SNAPSHOT_WAIT=0 turns it off.
SNAPSHOT_WAIT = float(os.environ.get("JARVIS_SNAPSHOT_WAIT", "0.4"))
DEFAULT_AGENT = "claude -p --output-format stream-json --verbose --include-partial-messages --no-session-persistence"
DAYS = ["måndag", "tisdag", "onsdag", "torsdag", "fredag", "lördag", "söndag"]
MONTHS = ["januari", "februari", "mars", "april", "maj", "juni", "juli", "augusti", "september",
          "oktober", "november", "december"]


def now_text():
    n = datetime.now(ZoneInfo("Europe/Stockholm"))
    return f"{DAYS[n.weekday()]} {n.day} {MONTHS[n.month - 1]} {n.year} ({n:%Y-%m-%d}), klockan {n:%H:%M}"


AGENT, AGENT_LOCK = None, threading.Lock()
# The other company's brain, built the first time it is needed: when the brain's API fails before anything was
# done, the question goes to it instead of "Jag kunde inte svara just nu". JARVIS_FALLBACK_BRAIN picks it
# (luna, groq, anthropic or none; default the other of luna and anthropic), JARVIS_FALLBACK_MODEL its model.
FALLBACK = None                         # None: not built yet; False: none
FALLBACK_MODELS = {"anthropic": "claude-sonnet-5-5"}
MEMORY = None                           # memory.Memory, made in main
REMINDERS = None                        # reminders.Reminders, made in main
NOTIFIER = None                         # notify.Notifier, made in main
WATCH = None                            # watch.Watch, made in main
CALENDAR_WEEK = None                    # calendar_week.Week: the calendar page's seven days, made on first use


def instructions():
    """The fixed instructions and, after them, what Jarvis remembers. It changes only when a note or the
    profile does (or Joel turns a year older), so it stays in the prompt cache."""
    return INSTRUCTIONS + (MEMORY.block() if MEMORY is not None else "")


def start_agent():
    """Build the in-process agent if JARVIS_AGENT=inprocess, and warm it in the background."""
    global AGENT
    if (mode := os.environ.get("JARVIS_AGENT")) != "inprocess":
        if mode:                                # a typo or an old value must not fall back to the command unseen
            print(f"JARVIS_AGENT={mode} is unknown (only inprocess is): the agent is the command in "
                  "JARVIS_AGENT_CMD, or Claude Code without it", file=sys.stderr)
        return
    sys.path.insert(0, str(Path(__file__).resolve().parent / "agents"))
    if os.environ.get("JARVIS_BRAIN") == "anthropic":   # needs the anthropic package (server/requirements.txt)
        from anthropic_agent import agent_from_env
    else:
        from openai_agent import agent_from_env
    AGENT = agent_from_env()
    AGENT.extra_tools = [*(MEMORY.tools() if MEMORY is not None else []),
                         *(REMINDERS.tools() if REMINDERS is not None else []),
                         *(NOTIFIER.tools() if NOTIFIER is not None else [])]
    threading.Thread(target=warm_agent, daemon=True).start()


def fallback_agent():
    """The fallback brain, or None. Its tools are the main brain's, the memory's and the phone's included."""
    global FALLBACK
    if FALLBACK is None:
        brain = os.environ.get("JARVIS_BRAIN") or "luna"
        choice = os.environ.get("JARVIS_FALLBACK_BRAIN") or ("luna" if brain == "anthropic" else "anthropic")
        FALLBACK = False
        if choice not in ("none", brain):
            model = os.environ.get("JARVIS_FALLBACK_MODEL") or FALLBACK_MODELS.get(choice)
            try:
                if choice == "anthropic":
                    from anthropic_agent import ClaudeAgent, tools_from_env
                    FALLBACK = ClaudeAgent(model=model, **tools_from_env())
                else:
                    from openai_agent import LunaAgent, tools_from_env
                    FALLBACK = LunaAgent(brain=choice, model=model, **tools_from_env())
                FALLBACK.extra_tools = AGENT.extra_tools
            except Exception as e:              # no key, no SDK: then there is simply no fallback
                print(f"no fallback brain ({choice}): {e}", file=sys.stderr)
                FALLBACK = False
    return FALLBACK or None


def warm_agent():
    # A wake word during another warm-up or answer must not queue more work ahead of the next question.
    if AGENT is None or not AGENT_LOCK.acquire(blocking=False):
        return None
    try:
        try:
            return AGENT.start_warm(instructions(), AGENT_LOCK)
        except Exception as e:        # a failed warm-up must never stop a question
            print(f"warm-up failed: {e}", file=sys.stderr)
            return None
    finally:
        AGENT_LOCK.release()


def first_question_this_morning(history, now=None, stored=True):
    """True between 05:00 and 10:00 if the question being answered is the first since 05:00 today; stored says
    whether it is in the history already. Questions Home Assistant answers itself never reach the history."""
    now = now or datetime.now(ZoneInfo("Europe/Stockholm"))
    if not MORNING[0] <= now.hour < MORNING[1]:
        return False
    since = now.replace(hour=MORNING[0], minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    return history.questions_since(since.isoformat(timespec="seconds")) <= (1 if stored else 0)


def store_question(history, conversation_id, text):
    """history.add for the question, in its own thread: a failure is logged, never raised into the answer."""
    try:
        history.add(conversation_id, "user", text)
    except Exception as e:                  # sqlite3 errors, a full disk: the answer still comes
        print(f"could not store the question: {e}", file=sys.stderr)


def direct_timing(started, waited, tools):
    """The timing record of an answer the server gave itself (quick_reply, quick_command)."""
    elapsed = time.monotonic() - started
    return {"brain": "direct", "first_token_s": round(elapsed, 3), "total_s": round(elapsed, 3),
            "first_token_ms": round(elapsed * 1000), "total_ms": round(elapsed * 1000),
            "snapshot_wait_ms": round(waited * 1000),
            "cost_usd": 0, "tokens_in": 0, "tokens_out": 0, "tools": tools, "steps": 0, "card": ""}


def shadow_route(text, language, took):
    """One journal line per question with quick_router's class beside what ask_reply did. It never raises:
    a bug in the shadow must not cost an answer."""
    try:
        print(quick_router.shadow_line(text, language, took), file=sys.stderr, flush=True)
    except Exception as e:                  # a feature error on odd input: say so, answer anyway
        print(f"quick-router: failed: {e}", file=sys.stderr, flush=True)


def ask_agent(history, text, on_delta=None, language=None, on_tools=None, store=None):
    """ask_reply without emoji, in both the streamed text and the reply (plain_text.py: the panel drew a box)."""
    reply, timing = ask_reply(history, text, (lambda d: on_delta(no_emoji(d))) if on_delta else None, language,
                              on_tools, store)
    return no_emoji(reply).strip(), timing


def ask_reply(history, text, on_delta=None, language=None, on_tools=None, store=None):
    """Ask the agent with the last half hour as context. on_delta(text) gets the reply as it is written, and
    on_tools(names) the in-process agent's tools as each round of them starts. With store, the conversation id,
    the question is stored under it here: off the request path, once the context has been read (so it is not
    its own context) and before this returns (so the reply's row follows it). The caller stores it otherwise.
    Each question also gets one `quick-router:` journal line: what quick_router.py would have chosen beside
    what happened here (shadow mode, it decides nothing; quick_router.py says how to read it)."""
    started = time.monotonic()
    stored = None
    try:
        waited = 0.0
        if AGENT is not None and (command := quick_command.match(text, language)):
            # "Stäng av radion", "Höj volymen": the one tool runs at once and its result is the answer.
            with AGENT_LOCK:
                direct = AGENT.command(*command, language=language)
                tools = list(AGENT.last_tools)
            if direct is not None:          # None: not sure enough (a Spotify search), the model takes it
                shadow_route(text, language, "command:" + command[0])
                if on_delta:
                    on_delta(direct)
                return direct, direct_timing(started, 0.0, tools)
        if AGENT is not None:
            # Clock and already-fresh calendar answers need no home/network read or pending warm-up.
            direct = quick_reply.answer(text, agenda=AGENT.fresh_agenda(), language=language)
            if direct is None:
                # Otherwise retain the bounded prefetch wait to avoid an unnecessary model/tool round.
                waited = AGENT.wait_for_snapshot(SNAPSHOT_WAIT)
                direct = quick_reply.answer(text, home=getattr(AGENT, "home", None),
                                            agenda=AGENT.fresh_agenda(), language=language)
            if direct:
                shadow_route(text, language, "reply")
                if on_delta:
                    on_delta(direct)
                return direct, direct_timing(started, waited, [])
        shadow_route(text, language, "model")
        since = (datetime.now(timezone.utc) - timedelta(minutes=CONTEXT_MINUTES)).isoformat(timespec="seconds")
        recent = history.latest(CONTEXT_MESSAGES, since)
        greeting = GREETING if first_question_this_morning(history, stored=store is None) else ""
        if store is not None:               # the SQLite commit (an SD card on Jarvis) runs beside the model call
            stored = threading.Thread(target=store_question, args=(history, store, text), daemon=True)
            stored.start()
        return _ask_agent(text, recent, greeting, started, waited, on_delta, language, on_tools)
    finally:
        if stored is not None:
            stored.join()
        elif store is not None:
            store_question(history, store, text)


def _ask_agent(text, recent, greeting, started, waited, on_delta, language, on_tools):
    """The rest of ask_agent: the prompt, the question lock, the in-process agent or the command."""
    context = ""
    if recent:
        lines = [("Joel: " if m["role"] == "user" else "Jarvis: ") + m["text"] for m in recent]
        context = "\nDet här har sagts och gjorts den senaste halvtimmen:\n" + "\n".join(lines) + "\n"
    cmd = shlex.split(os.environ.get("JARVIS_AGENT_CMD", DEFAULT_AGENT))
    if language == "en":
        greeting = greeting.replace("God morgon!", "Good morning!")
    if language in ("sv", "en"):
        greeting += "\nIntern språkstyrning (ska inte nämnas i svaret): svarsspråk = " + ("engelska" if language == "en" else "svenska") + ".\n"
    prompt = PROMPT.format(now=now_text(), calendar="", home="", weather="", context=context, greeting=greeting, text=text)
    if AGENT is not None:
        waiting_for_agent = time.monotonic()
        with AGENT_LOCK:                # serialize answers; snapshots warm independently
            queue_ms = round((time.monotonic() - waiting_for_agent) * 1000)
            calendar = ""
            if fresh := AGENT.fresh_agenda():
                agenda, age, days = fresh
                calendar = CALENDAR.format(days=days - 1, age=age, agenda=agenda or "(inget inlagt)")
            home = ""
            if fresh := AGENT.fresh_home():
                home = HOME.format(home=fresh[0], age=fresh[1])
            weather = ""
            cached_forecast = getattr(getattr(AGENT, "home", None), "prompt_forecast", lambda _text: None)(text)
            if cached_forecast:
                weather = WEATHER.format(forecast=cached_forecast[0], age=cached_forecast[1])
            prompt = PROMPT.format(now=now_text(), calendar=calendar, home=home, weather=weather, context=context, greeting=greeting,
                                   text=text)
            # kom_ihag, glom, paminnelse and till_telefonen run only when this asks for them
            for tool in (MEMORY, REMINDERS, NOTIFIER):
                if tool is not None:
                    tool.said = text
            turn = spoken_change.Turn.from_recent(text, recent)
            if NOTIFIER is not None:   # a yes to "ska jag skicka det till telefonen?"
                NOTIFIER.last_reply = turn.last_reply
            start, first, request_first = time.monotonic(), [], []
            try:
                def on_text(d):
                    if not first:
                        now = time.monotonic()
                        first.append(now - start)
                        request_first.append(now - started)
                    if on_delta:
                        on_delta(d)
                agent = AGENT
                # tvatt_boka books a real laundry pass and the morning tools move real alarms: their spoken
                # gate (agents/spoken_change.py) reads this question's words, and only during this question.
                agent.spoken = turn
                try:
                    reply, cost, usage = agent.ask(prompt, on_text=on_text, instructions=instructions(),
                                                   on_tools=on_tools, language=language)
                except Exception as e:
                    # Another brain only when nothing was done yet: a lookup may be repeated, a booking or a
                    # push must not be.
                    if any(t["name"] not in agent.early for t in agent.last_tools) or not (agent := fallback_agent()):
                        raise
                    print(f"{AGENT.label} failed ({str(e)[:200]}); asking {agent.label}", file=sys.stderr)
                    if first:
                        on_text("\n")
                    agent.spoken = turn
                    reply, cost, usage = agent.ask(prompt, on_text=on_text, instructions=instructions(),
                                                   on_tools=on_tools, language=language)
            except Exception as e:
                raise RuntimeError(f"agent failed: {e}") from e
            finally:
                for answered in (AGENT, FALLBACK or None):
                    if answered is not None:
                        answered.spoken = spoken_change.Turn()
        if not reply:
            raise RuntimeError("agent gave no reply")
        total = time.monotonic() - start
        return reply, {"first_token_s": round(first[0], 2) if first else None, "total_s": round(total, 2),
                       "first_token_ms": round(first[0] * 1000) if first else None, "total_ms": round(total * 1000),
                       "cost_usd": cost, "brain": agent.label, "agent_queue_ms": queue_ms,
                       "request_to_first_text_ms": round(request_first[0] * 1000) if request_first else None,
                       "snapshot_wait_ms": round(waited * 1000), "rounds": getattr(agent, "last_rounds", None),
                       "tokens_in": usage.get("input_tokens"),
                       "tokens_out": usage.get("output_tokens"), "tokens_cached": usage.get("cached_tokens"),
                       "tokens_cache_write": usage.get("cache_write_tokens"), "tools": agent.last_tools,
                       "steps": usage.get("steps"), "card": agent.screen.encoded()}
    start = time.monotonic()
    first = None
    # A command-mode agent runs its tools in its own process: the gated changes get the question's words here.
    env = {**os.environ, "JARVIS_SPOKEN": spoken_change.Turn.from_recent(text, recent).to_env()}
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            env=env)
    timer = threading.Timer(AGENT_TIMEOUT, proc.kill)
    timer.start()
    try:
        proc.stdin.write(instructions() + "\n" + prompt)
        proc.stdin.close()
        plain, reply, failed, cost = [], None, False, None
        for line in proc.stdout:
            try:
                event = json.loads(line)
            except ValueError:                       # plain-text agent
                plain.append(line)
                first = first or time.monotonic() - start
                if on_delta:
                    on_delta(line)
                continue
            inner = event.get("event", {}) if event.get("type") == "stream_event" else {}
            if inner.get("type") == "content_block_delta" and inner.get("delta", {}).get("type") == "text_delta":
                first = first or time.monotonic() - start
                if on_delta:
                    on_delta(inner["delta"].get("text", ""))
            elif event.get("type") == "result":
                reply, failed = str(event.get("result", "")).strip(), bool(event.get("is_error"))
                cost = event.get("total_cost_usd")
        err = proc.stderr.read()
        code = proc.wait()
    finally:
        timer.cancel()
    total = time.monotonic() - start
    reply = reply if reply is not None else "".join(plain).strip()
    if code != 0 or failed or not reply:
        raise RuntimeError(f"agent exited {code}: {err.strip()[:200]}")
    return reply, {"first_token_s": round(first, 2) if first else None, "total_s": round(total, 2),
                   "first_token_ms": round(first * 1000) if first else None, "total_ms": round(total * 1000),
                   "cost_usd": cost, "brain": Path(cmd[0]).name}


def agent_label():
    return AGENT.label if AGENT is not None else Path(shlex.split(os.environ.get("JARVIS_AGENT_CMD", DEFAULT_AGENT))[0]).name


def public_timing(timing):
    """The reply's timing without the tools' result text, which stays in history.db on Jarvis."""
    if not timing or not timing.get("tools"):
        return timing
    return {**timing, "tools": [{k: v for k, v in t.items() if k not in ("result", "args")} for t in timing["tools"]]}


def log_line(timing):
    tools = ", ".join(f"{t['name']} {t['ms']} ms" for t in timing.get("tools") or []) or "no tools"
    # Each request to the model: its time, when its first text came (= its time means the text arrived in one
    # piece at the end) and its output tokens, thinking included.
    rounds = " + ".join(f"{r['ms']} ms (first {r['first_ms']}, out {r['out']})" for r in timing.get("rounds") or [])
    return (f"{timing.get('brain')}: first word {timing.get('first_token_ms')} ms, total {timing.get('total_ms')} ms, "
            f"tokens {timing.get('tokens_in')}/{timing.get('tokens_out')} (cached {timing.get('tokens_cached')}, "
            f"cache write {timing.get('tokens_cache_write')}), {timing.get('steps')} rounds, ${timing.get('cost_usd')}, {tools}"
            + (f"; model {rounds}" if rounds else "")
            + (f"; waited {timing['snapshot_wait_ms']} ms for the warm-up" if timing.get("snapshot_wait_ms") else ""))


def panel_status(now=None):
    """GET /panel: the home as short strings for the panel (see the top of the file)."""
    sys.path.insert(0, str(Path(__file__).resolve().parent / "agents"))
    import home_status
    now = now or datetime.now(ZoneInfo("Europe/Stockholm"))
    house = []
    out = {"status": "", "alert": "off", "outside": "", "house": "", "online": "on" if WATCH is None or WATCH.net_ok else "off",
           "reminders": "\n".join(REMINDERS.lines(now)) if REMINDERS is not None else "",
           # Hemmet's reminder page (styles/glass-v2/reminders_page.py): every pending one, with its id
           "rem_list": REMINDERS.panel_rows(now) if REMINDERS is not None else ""}
    if WATCH is not None and WATCH.home is not None:
        states = WATCH.latest_states()
        if states is None:
            try:
                states = WATCH.home.states()
            except Exception as e:      # Home Assistant restarting: the panel shows what it has
                print(f"panel: no home status: {e}", file=sys.stderr)
        if states is not None:
            out.update(home_status.panel_fields(states, now))
        else:
            out.update(status="Hemstatus kunde inte läsas.", alert="on")
        out["outside"] = WATCH.home.outside(now)
        sun = next((s for s in states or [] if s["entity_id"] == home_status.ENTITIES["sun"]), None)
        out.update(WATCH.home.weather(now, sun_up=not sun or sun.get("state") != "below_horizon",
                                      sun=(sun or {}).get("attributes")))
        walk = int(os.environ.get("JARVIS_WALK_MIN", "3"))
        out.update(WATCH.home.metro(now, walk))
        # Hemmet's house line: his providers' disruptions over home (Följdfel)
        house = WATCH.home.disruptions(states, now)
        out["house"] = home_status.house_line(house)
    if WATCH is not None and WATCH.calendar is not None:
        from calendar_palette import PALETTE
        global CALENDAR_WEEK
        if CALENDAR_WEEK is None:
            import calendar_week
            CALENDAR_WEEK = calendar_week.Week(calendar_reader)
        feed = getattr(WATCH, "calendar_feed", None)
        # The revision moves with the agenda; the change tokens also with an edited alarm or note (the tap's details).
        status = feed.status() if feed else {}
        out.update(CALENDAR_WEEK.fields(now, PALETTE.get(), (status.get("revision"), status.get("token_changes"))
                                        if feed else None))
        # An empty today and tomorrow show the week's next events ("KOMMANDE") once the week has been read.
        out.update(home_status.calendar_panel(WATCH.events, now, PALETTE.get(), later=CALENDAR_WEEK.events))
        if not WATCH.calendar_fresh(now):
            out.update(cal0="Kalendern kan inte uppdateras||", cal1="", cal2="")
    if out["online"] == "off":
        out["status"] = "Nätet är nere. Timers, påminnelser och larm fungerar."
    # The Jarvis card's Härnäst while its chat is empty (home_status.next_up)
    by_id = {s["entity_id"]: s for s in (states if WATCH is not None and WATCH.home is not None else None) or []}
    out["next_up"] = home_status.next_up(
        now, out["status"] if out["alert"] == "on" else "",
        REMINDERS.upcoming(now) if REMINDERS is not None else (),
        (by_id.get(home_status.ENTITIES["laundry"]) or {}).get("state") or "",
        str(((by_id.get(home_status.WAKEUP) or {}).get("attributes") or {}).get("panel") or ""), house)
    # Tavla's plaque: NASA's picture of the day (server/apod.py) or the painting chosen in Home Assistant
    out["art_title"], out["art_credit"] = map(background.plaque_text, background.art(home_status.APOD_JSON.parent))
    out["art_text"] = background.plaque_text(background.art_text(home_status.APOD_JSON.parent))
    out.update(background.earth(home_status.APOD_JSON.parent))    # Tavla's Earth view (server/earth.py)
    out["bg_version"] = background.version()    # the panel fetches the background again when this changes
    return out


def background_status():
    return {"choice": background.current(), "options": background.choices(),
            "link": background._state().get("link_url", "")}


def home_status_reader():
    """home_status.Home for the watch, when JARVIS_HOME_TOKEN_FILE is set."""
    from home_status import Home
    return Home.from_env()


def calendar_reader():
    """The watch's own session with the calendar's MCP server (JARVIS_CALENDAR_MCP), or None."""
    from agent_core import mcp_client, tools_from_env
    env = tools_from_env()
    return mcp_client(env["mcp_url"], env["mcp_token_file"])


def voice_module():
    """server/voice.py, imported on first use so the server starts without Home Assistant."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import voice
    return voice


# Home Assistant polls these every 5-15 s. Logged, they were 98 % of the server's journal lines, and the Pi's
# 50 MB journal held only a day (2026-10-27). A failed read (not 2xx/304) is still logged.
QUIET_POLLS = ("/history", "/assets/", "/panel", "/background", "/speaker", "/wakeup/status", "/sl/status", "/speech/status",
               "/calendar/status")


def quiet_request(command, path, code):
    """True when the access log should skip this request: a successful poll or turn report."""
    if str(getattr(code, "value", code)) not in ("200", "202", "204", "304"):
        return False
    return (command == "GET" and path.startswith(QUIET_POLLS)) or (command == "POST" and path == "/turn")


TURN_ID = re.compile(r"[0-9a-f]{16}")
# What Home Assistant may report per stage (POST /turn); anything else is dropped. Columns of voice_turns.
TURN_STAGES = {
    "stt": {"outcome": ("stt", ("text", "no_speech", "empty", "failed", "invalid", "error", "stopped", "lost",
                                "cancelled")),
            "listen_ms": ("listen_ms", int), "speech_ms": ("speech_ms", int), "early": ("early", bool),
            "language": ("language", ("sv", "en"))},
    "tts": {"outcome": ("tts", ("complete", "stopped", "error")), "sentences": ("sentences", int),
            "failed": ("sentences_failed", int), "first_audio_ms": ("first_audio_ms", int),
            "audio_ms": ("audio_ms", int)},
}


def turn_fields(stage, report):
    """voice_turns columns from a POST /turn report; ValueError for an unknown stage."""
    if stage not in TURN_STAGES or not isinstance(report, dict):
        raise ValueError("unknown stage")
    out = {}
    for key, (column, kind) in TURN_STAGES[stage].items():
        value = report.get(key)
        if isinstance(kind, tuple):
            ok = value in kind
        elif kind is bool:
            ok, value = isinstance(value, bool), int(bool(value))
        else:
            ok = isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 86_400_000
        if ok:
            out[column] = value
    return out


def record_turn(history, turn_id, **kw):
    """history.turn that never fails the request it belongs to; turn ids that are not ours are ignored."""
    if not isinstance(turn_id, str) or not TURN_ID.fullmatch(turn_id):
        return
    try:
        history.turn(turn_id, **kw)
    except Exception as e:                  # sqlite3 errors, a full disk: the voice turn itself goes on
        print(f"could not record voice turn: {e}", file=sys.stderr)


# Request bodies are loopback-only; the caps are for a runaway client, not an attacker (2026-11-02 review).
# Nothing sent comes close: the panel's voice uploads stay within transcription.MAX_PCM's 60 s of WAV and
# every JSON body is a sentence, a choice or a turn record.
JSON_BODY_MAX = 256 * 1024
AUDIO_BODY_MAX = 8 * 1024 * 1024


def make_handler(assets, history, token):
    assets = Path(assets).resolve()

    class Handler(BaseHTTPRequestHandler):
        server_version = "hallpanel/1"

        def log_request(self, code="-", size="-"):
            if not quiet_request(self.command, self.path, code):
                super().log_request(code, size)

        def send_json(self, code, payload):
            body = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def body_size(self):
            """Content-Length as a byte count. A negative or non-numeric one raises ValueError: read(-1)
            reads until the client closes, which skipped every cap below (2026-11-02 review)."""
            size = int(self.headers.get("Content-Length") or 0)
            if size < 0:
                raise ValueError("negative Content-Length")
            return size

        def body_json(self):
            """The request's JSON body. Over JSON_BODY_MAX the rest of the body is simply dropped, so the
            parse fails and the endpoint's own bad-request answer replies; the extra bytes never locate."""
            return json.loads(self.rfile.read(min(self.body_size(), JSON_BODY_MAX)) or b"{}")

        def authorised(self):
            if token and self.headers.get("X-Hallpanel-Token") != token:
                self.send_json(401, {"error": "missing or wrong X-Hallpanel-Token"})
                return False
            return True

        def do_GET(self):
            url = urlparse(self.path)
            if url.path.startswith("/assets/"):
                f = (assets / url.path[len("/assets/"):]).resolve()
                if assets not in f.parents or not f.is_file():
                    return self.send_json(404, {"error": "no such asset"})
                st = f.stat()
                etag = f'"{st.st_mtime_ns:x}-{st.st_size:x}"'
                if self.headers.get("If-None-Match") == etag:
                    self.send_response(304)
                    self.send_header("ETag", etag)
                    return self.end_headers()
                data = f.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", mimetypes.guess_type(f.name)[0] or "application/octet-stream")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("ETag", etag)
                self.send_header("Last-Modified", self.date_time_string(int(st.st_mtime)))
                self.end_headers()
                return self.wfile.write(data)
            if url.path == "/history":
                if not self.authorised():
                    return
                query = parse_qs(url.query)
                limit = min(max(int(query.get("limit", ["20"])[0]), 1), 500)
                # after Rensa the screens start over; ?all=1 also gives what came before
                return self.send_json(200, {"messages": history.latest(limit, after_clear=query.get("all") != ["1"])})
            if url.path == "/panel":
                if not self.authorised():
                    return
                return self.send_json(200, panel_status())
            if url.path == '/speech/status':
                if not self.authorised():
                    return
                import transcription
                return self.send_json(200, transcription.status())
            if url.path == '/wakeup/status':
                if not self.authorised():
                    return
                import wakeup
                return self.send_json(200, wakeup.instance().status())
            if url.path == "/calendar/status":
                if not self.authorised():
                    return
                feed = getattr(WATCH, 'calendar_feed', None)
                return self.send_json(200, feed.status() if feed else {'available': False})
            if url.path == "/sl/status":
                if not self.authorised():
                    return
                home = getattr(WATCH, 'home', None)
                return self.send_json(200, home.sl_status() if home else {'available': False})
            if url.path == "/background":
                if not self.authorised():
                    return
                return self.send_json(200, background_status())
            if url.path == "/speaker":
                if not self.authorised():
                    return
                import speaker
                return self.send_json(200, speaker.choices())
            if url.path == "/speed":
                if not self.authorised():
                    return
                import speed
                try:
                    return self.send_json(200, speed.state())
                except (OSError, subprocess.SubprocessError, ValueError) as e:
                    return self.send_json(503, {"error": f"speed: {e}", "rpm": 33})
            self.send_json(404, {"error": "unknown path"})

        def do_POST(self):
            path = urlparse(self.path).path
            if path.startswith('/wakeup/'):
                if not self.authorised():
                    return
                import wakeup
                try:
                    size = self.body_size()
                    if size > 4096:
                        raise ValueError("Invalid wakeup request")
                    data = json.loads(self.rfile.read(size) or b'{}')
                    action = path.removeprefix('/wakeup/')
                    manager = wakeup.instance()
                    if action == 'reserves':
                        result = manager.reserves()
                    elif action in ('prepare', 'claim'):
                        due = wakeup.due_time(data['due'])
                        kind = data['kind']
                        if kind not in wakeup.KINDS or not isinstance(data.get('preview', False), bool):
                            raise ValueError("Invalid wakeup options")
                        wish = data.get('wish', '')
                        result = (manager.prepare(due, kind, wish=wish) if action == 'prepare'
                                  else manager.claim(due, kind, data.get('preview', False), wish=wish))
                    else:
                        return self.send_json(404, {'error': 'unknown wakeup action'})
                    return self.send_json(200, result)
                except (ValueError, KeyError, TypeError):
                    return self.send_json(400, {'error': 'invalid wakeup request or unavailable prepared audio'})
                except OSError:
                    return self.send_json(503, {'error': 'wakeup preparation unavailable'})
            if path == '/turn':
                if not self.authorised():
                    return
                try:
                    body = json.loads(self.rfile.read(min(self.body_size(), 4096)) or b'{}')
                    turn_id, fields = body['turn'], turn_fields(body['stage'], body.get('report'))
                    if not isinstance(turn_id, str) or not TURN_ID.fullmatch(turn_id):
                        raise ValueError('turn id')
                except (ValueError, KeyError, TypeError, AttributeError):
                    return self.send_json(400, {'error': 'send JSON like {"turn": "<16 hex>", "stage": "stt", "report": {...}}'})
                # The STT report comes when recognition ends: the turn began listen_ms before that.
                record_turn(history, turn_id, started_ms_ago=fields.get('listen_ms', 0), **fields)
                return self.send_json(200, {'recorded': True})
            if path == '/transcribe':
                if not self.authorised():
                    return
                import transcription
                turn_id, started = self.headers.get('X-Hallpanel-Turn'), time.monotonic()
                try:
                    size = self.body_size()
                    if not 44 < size <= transcription.MAX_PCM+1024:
                        record_turn(history, turn_id, increment=('recognitions',), stt_error='recording size')
                        return self.send_json(400, {'error':'Send a WAV of at most 60 seconds'})
                    result = transcription.transcribe(self.rfile.read(size))
                    record_turn(history, turn_id, increment=('recognitions',),
                                recognition_ms=round((time.monotonic() - started) * 1000))
                    return self.send_json(200, result)
                except ValueError:
                    record_turn(history, turn_id, increment=('recognitions',), stt_error='invalid recording')
                    return self.send_json(400, {'error':'Invalid recording'})
                except transcription.Unavailable as error:
                    # The reason goes only to the journal and voice_turns (never the transcript or the key).
                    print(f'transcribe unavailable: {error}', file=sys.stderr)
                    record_turn(history, turn_id, increment=('recognitions',), stt_error=str(error)[:160])
                    return self.send_json(503, {'error':'Speech recognition is unavailable'})
            if path in ("/voice", "/speak"):
                if not self.authorised():
                    return
                return self.voice() if path == "/voice" else self.speak()
            if path == "/warm":
                if not self.authorised():
                    return
                if AGENT is not None:
                    threading.Thread(target=warm_agent, daemon=True).start()
                # Groq's connection too, so no handshake runs once the speech has ended (transcription.warm).
                import transcription
                threading.Thread(target=transcription.warm, daemon=True).start()
                return self.send_json(202, {"warming": AGENT is not None, "brain": AGENT.label if AGENT else None})
            if path == "/background":
                if not self.authorised():
                    return
                try:
                    body = self.body_json()
                    choice = background.from_link(body["url"]) if "url" in body else background.choose(str(body["choice"]))
                except (ValueError, KeyError) as e:
                    return self.send_json(400, {"error": str(e).strip("'"), **background_status()})
                self.log_message("background: %s", choice)
                return self.send_json(200, background_status())
            if path == "/radio":
                if not self.authorised():
                    return
                import radio
                import speaker
                try:
                    body = self.body_json()
                except ValueError as e:
                    return self.send_json(400, {"error": str(e)})
                said = []
                if body.get("connect") and speaker.configured():
                    try:
                        paired = speaker.speakers()
                        if paired and not any(s["connected"] for s in paired):
                            said.append(speaker.connect(paired[0]["name"])[1])
                    except speaker.SpeakerError as e:
                        said.append(str(e))
                error, text = radio.play(str(body.get("channel") or "P3"))
                said.append(text)
                self.log_message("radio: %s", " ".join(said))
                return self.send_json(400 if error else 200, {**({"error": text} if error else {}), "said": said})
            if path == "/speaker":
                if not self.authorised():
                    return
                import speaker
                try:
                    body = self.body_json()
                    choice = str(body["choice"])
                except (ValueError, KeyError) as e:
                    return self.send_json(400, {"error": f"choice: {e}", **speaker.choices()})
                error, text = speaker.choose(choice)
                self.log_message("speaker: %s -> %s", choice, text)
                return self.send_json(400 if error else 200, {**({"error": text} if error else {"said": text}),
                                                              **speaker.choices()})
            if path == "/speaker/reconnect":
                if not self.authorised():
                    return
                import speaker
                error, text = speaker.reconnect(log=lambda t: self.log_message("speaker: %s", t))
                self.log_message("speaker: koppla om -> %s", text)
                return self.send_json(400 if error else 200, {**({"error": text} if error else {"said": text}),
                                                              **speaker.choices()})
            if path == "/speed":
                if not self.authorised():
                    return
                import speed
                try:
                    body = self.body_json()
                    result = speed.set_speed(int(body["rpm"]))
                except (ValueError, KeyError, TypeError) as e:
                    return self.send_json(400, {"error": f"rpm: {e}"})
                except (OSError, subprocess.SubprocessError) as e:
                    return self.send_json(503, {"error": f"speed: {e}", "rpm": 33})
                self.log_message("speed: %s -> %s", body.get("rpm"), result)
                return self.send_json(400 if "error" in result else 200, result)
            if path == "/reminders/cancel":
                # Ta bort on Hemmet's reminder page (2026-11-03): Joel's own tap, through jarvis_agent.reminder_cancel,
                # so unlike the agent's tool it needs no spoken request. The row stays in the database, cancelled.
                if not self.authorised():
                    return
                try:
                    n = int(self.body_json()["id"])
                except (ValueError, KeyError, TypeError):
                    return self.send_json(400, {"error": "id: a reminder number"})
                cancelled = REMINDERS is not None and REMINDERS.cancel(n)
                self.log_message("Reminder %s cancelled from the panel: %s", n, "yes" if cancelled else "none pending")
                return self.send_json(200 if cancelled else 404, {"cancelled": cancelled, "id": n})
            if path == "/clear":
                if not self.authorised():
                    return
                history.clear()
                self.log_message("Rensa: Jarvis forgot the conversation")
                return self.send_json(200, {"cleared": True})
            if path != "/converse":
                return self.send_json(404, {"error": "unknown path"})
            if not self.authorised():
                return
            try:
                body = self.body_json()
                text = str(body["text"]).strip()
            except (ValueError, KeyError):
                return self.send_json(400, {"error": 'send JSON like {"text": "..."}'})
            cid, stream = body.get("conversation_id"), bool(body.get("stream"))
            language = body.get("language")
            if language not in (None, "sv", "en"):
                return self.send_json(400, {"error": "language must be sv or en"})
            emit = None
            if stream:
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
                self.end_headers()

                def emit(obj):
                    try:
                        self.wfile.write((json.dumps(obj, ensure_ascii=False) + "\n").encode())
                    except OSError:              # Home Assistant hung up; the agent still finishes
                        pass
            try:
                (reply, timing), ok = ask_agent(history, text, (lambda d: emit({"delta": d})) if emit else None,
                                               language=language, store=cid,
                                               on_tools=(lambda names: emit({"tools": names})) if emit else None), True
                self.log_message("%s", log_line(timing))
                meta = timing
            except (RuntimeError, OSError) as e:
                self.log_message("agent failed: %s", e)
                reply, ok, timing = ("I couldn't answer just now." if language == "en" else "Jag kunde inte svara just nu."), False, None
                meta = {"brain": agent_label(), "error": str(e)[:500]}
            reply_id = history.add(cid, "jarvis", reply, ok, meta)
            record_turn(history, body.get("turn"), reply_id=reply_id, reply_ok=int(ok))
            timing = public_timing(timing)
            if emit:
                return emit({"done": True, "reply": reply, "ok": ok, "timing": timing})
            self.send_json(200, {"reply": reply, "ok": ok, "timing": timing})

        def speak(self):
            v = voice_module()
            try:
                body = self.body_json()
                text = str(body["text"]).strip()
            except (ValueError, KeyError):
                text = ""
            if not text:
                return self.send_json(400, {"error": 'send JSON like {"text": "..."}'})
            language = body.get("language")
            if language not in (None, "sv", "en"):
                return self.send_json(400, {"error": "language must be sv or en"})
            try:
                timing = {}
                wav, source = v.speech(text, language=language, timing=timing)
            except (OSError, ValueError, KeyError) as e:
                self.log_message("text to speech failed: %s", e)
                return self.send_json(502, {"error": "text to speech failed"})
            self.log_message("speech: source %s, tts %d ms, trim %d ms, tone %d ms, total %d ms", source,
                timing["tts_ms"], timing["trim_ms"], timing["tone_ms"], timing["total_ms"])
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(wav)))
            self.end_headers()
            self.wfile.write(wav)

        def voice(self):
            v = voice_module()
            t0 = time.monotonic()
            cid = self.headers.get("X-Conversation-Id")
            try:
                size = self.body_size()
            except ValueError as e:
                return self.send_json(400, {"error": f"Content-Length: {e}"})
            if size > AUDIO_BODY_MAX:          # the panel's own upload is at most ~60 s of WAV; anything
                return self.send_json(413, {"error": "body larger than 8 MB, not a voice upload"})
            try:
                pcm = v.pcm_of(self.rfile.read(size))
            except (ValueError, EOFError) as e:
                return self.send_json(400, {"error": str(e)})
            said = v.stt(pcm)
            t_stt = time.monotonic()
            if not said:
                return self.send_json(200, {"said": "", "reply": "", "ok": False,
                                            "timing": {"stt_s": round(t_stt - t0, 2)}})
            history.add(cid, "user", said)
            try:
                reply, agent_timing = ask_agent(history, said)
                ok, meta = True, agent_timing
                self.log_message("%s", log_line(agent_timing))
            except (RuntimeError, OSError) as e:
                self.log_message("agent failed: %s", e)
                reply, ok, agent_timing = "Jag kunde inte svara just nu.", False, {}
                meta = {"brain": agent_label(), "error": str(e)[:500]}
            history.add(cid, "jarvis", reply, ok, meta)
            t_agent = time.monotonic()
            wav = v.tts(reply)
            t_tts = time.monotonic()
            wav = v.ai_tone(v.trim(wav))
            t_trim = time.monotonic()
            self.send_json(200, {"said": said, "reply": reply, "ok": ok, "audio_b64": base64.b64encode(wav).decode(),
                                 "timing": {"stt_s": round(t_stt - t0, 2), "agent_s": round(t_agent - t_stt, 2),
                                            "agent_first_token_s": agent_timing.get("first_token_s"),
                                            "tts_s": round(t_tts - t_agent, 2), "trim_s": round(t_trim - t_tts, 3),   # with the AI tone
                                            "server_total_s": round(t_trim - t0, 2),
                                            "speech_s": round(v.duration(wav), 2)}})

    return Handler


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--bind", default="127.0.0.1")
    ap.add_argument("--assets", default=str(Path(__file__).resolve().parent / "www"))
    ap.add_argument("--db", default="~/.local/share/hallpanel/history.db")
    args = ap.parse_args()
    token = os.environ.get("HALLPANEL_TOKEN")
    if not token and os.environ.get("HALLPANEL_ALLOW_NO_TOKEN") != "1":
        # 2026-11-02 review: without the token the whole API runs open, and loopback alone is no fence to
        # answer on. Fail instead, so a lost server.env shows as a stopped service.
        print("hallpanel server: no HALLPANEL_TOKEN; refusing to start. Deploy writes it in "
              "~/.config/hallpanel/server.env (mode 600). For a throwaway local run set "
              "HALLPANEL_ALLOW_NO_TOKEN=1.", file=sys.stderr)
        raise SystemExit(2)
    history = History(args.db)
    MEMORY = Memory(args.db)
    REMINDERS = Reminders(args.db)
    NOTIFIER = Notifier(history)
    start_agent()
    WATCH = Watch(NOTIFIER, REMINDERS, home=home_status_reader(), calendar=calendar_reader(),
                  on_net_down=lambda: threading.Thread(target=voice_module().load_piper, daemon=True).start())
    WATCH.start()
    if WATCH.home is not None:
        WATCH.home.prefetch_sl()            # the first wake word after a restart already knows the metro
    import speaker
    if speaker.configured():
        speaker.watch(threading.Event())    # a speaker switched on by hand takes over the radio
        import audio_health
        audio_health.watch(threading.Event())   # a 5-minute audio health sample (docs/spotify.md, Logs)
    handler = make_handler(args.assets, history, token)
    # flush: unflushed, journald stamped this line minutes later, mid-turn, and it read as a restart (2026-10-30).
    print(f"hallpanel server on {args.bind}:{args.port}, assets {args.assets}, history {args.db}", flush=True)
    ThreadingHTTPServer((args.bind, args.port), handler).serve_forever()
