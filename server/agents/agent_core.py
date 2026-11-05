#!/usr/bin/env python3
"""What every brain of Jarvis shares, whichever company's model answers. Standard library only.

  ToolAgent      the tools (mock tools, the calendar's MCP server, the TV connector, the free lookups,
                 the home status, the radio), the duplicate-create guard, the warm-up with the prefetched
                 agenda and home, and the question loop itself: ask the model, run the tools it asks for
                 at the same time, count the rounds, add up tokens and cost. A brain implements only three methods:
                   _begin(prompt, instructions)   the provider's conversation for one question
                   _round(convo, on_text)         one request: streams text to on_text, returns a Round;
                                                  with convo["no_tools"] it may not call tools (the last round)
                   _add_results(convo, round, results)   hands the tools' results back for the next round
                 and, if it can, _warm_api(instructions) to open its connection ahead of the question.
  MCPClient      a minimal MCP client for the streamable HTTP transport

openai_agent.py (OpenAI Responses, OpenAI-compatible Chat Completions such as Groq) and anthropic_agent.py
(Anthropic Messages) are the brains.
"""
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import lookup
import journey
import radio
import spotify
import screen
import panel
import speaker
import moon_phase
import volume
import ai_usage
import laundry
import spoken_change
import bookers
import mornings
from home_status import Home

ROOT = Path(__file__).resolve().parent.parent.parent
GUARD_FILE = Path(os.environ.get("JARVIS_WRITE_GUARD_FILE", "~/.local/share/hallpanel/recent-creates.json")).expanduser()
GUARD_MINUTES = 15
# Every timed event Jarvis adds gets an alert this many minutes before its start, unless he gives his own
# (Joel's wish, 2026-10-23). For an outing the start is when to leave home, so it is time to get ready.
ALARM_MINUTES = 15


def default_alarm(args):
    """Minutes before the start for an event added without its own alert. An event no longer than
    ALARM_MINUTES is itself the nudge ("Gå till tvättstugan" 08:45-09:00 for a 09:00 slot), so it rings
    at its start; 15 minutes more made it ring at 08:30 (2026-11-01)."""
    try:
        start, end = (datetime.strptime(str(args.get(k))[:16].replace("T", " "), "%Y-%m-%d %H:%M")
                      for k in ("start", "end"))
    except (TypeError, ValueError):
        return ALARM_MINUTES
    return 0 if timedelta(0) < end - start <= timedelta(minutes=ALARM_MINUTES) else ALARM_MINUTES
# An event that would overlap a timed event already in the calendar is not added: the model is told what it
# collides with and picks another time, or sets overlap_ok when Joel asks for exactly that (2026-10-23).
OVERLAP_ARG = {"type": "boolean", "description": "Sätt bara när Joel uttryckligen vill lägga det ovanpå något som redan "
                                                 "finns i kalendern; annars stoppas en krock"}
AGENDA_LINE = re.compile(r"^(\d{4}-\d\d-\d\d) (\d\d:\d\d)(?:-| \.\. (\d{4}-\d\d-\d\d) )(\d\d:\d\d) \| ([^|]+?) \| ([^|]+?) \|")
TZ = ZoneInfo("Europe/Stockholm")
# warm() reads this many days of agenda (today and the next 7). For AGENDA_MAX_AGE seconds the server
# hands it to the model, so a question about those days needs no calendar lookup. A write drops it.
AGENDA_DAYS = int(os.environ.get("JARVIS_AGENDA_DAYS", "8"))
AGENDA_MAX_AGE = int(os.environ.get("JARVIS_AGENDA_MAX_AGE", "120"))
MAX_STEPS = 8          # the last of them is asked without tools, so an answer always comes
LAST_ROUND = ("\n\n(Det här var sista steget med verktyg. Svara nu med det du har. Säg bara att något är gjort om "
              "ett verktyg bekräftade det, och säg kort vad som inte hann göras.)")
FINAL_TOOLS = {screen.TOOL["name"]}     # shown, not answered: a round with only these ends the question
# Lookups that only read, started the moment the model has written their call, while it still writes the rest
# of its answer (with the calendar's tools marked read-only). Anything that writes, sends or plays waits for the
# whole answer, so nothing is done from an answer that is cut off or declined.
# Tools so slow that silence until the answer feels broken: when the model writes no sentence before them, this
# one is said as they start (Joel, 2026-11-01: 17 s of silence while the laundry site booked; the description's
# rule to say a sentence first was not followed).
SLOW_NOTICES = {"tvatt": ("Självklart, jag tittar på tvättstugan.", "Of course, I'm checking the laundry room."),
                "tvatt_boka": ("Självklart, jag tittar på tvättstugan.", "Of course, I'm checking the laundry room.")}
# Since 2026-11-04 every round that goes out to tools says one such sentence too (Joel: "jag tittar på det" or
# the like while it searches or does things), since a tool round means another model round, 4-7 s before the
# answer. Once a question, only when the model wrote nothing first, and never a claim that something is done:
# a calendar write still says its result only after it. Not for the tools whose result is said at once
# (DIRECT_TOOLS), nor the card alone; calls with different sentences say NOTICE.
NOTICE = ("Jag tittar på det.", "Let me check.")
TOOL_NOTICES = {
    "resa": ("Jag kollar resan.", "Let me check the trip."),
    "vader": ("Jag kollar prognosen.", "Let me check the forecast."),
    "hemmet": ("Jag kollar läget hemma.", "Let me check the house."),
    "nyheter": ("Jag kollar nyheterna.", "Let me check the news."),
    **dict.fromkeys(("wikipedia", "webbsok", "web_search", "web_fetch"), ("Jag slår upp det.", "Let me look it up.")),
    **dict.fromkeys(("flygplan", "iss"), ("Jag kollar himlen.", "Let me check the sky.")),
    "polisen": ("Jag kollar polisens händelser.", "Let me check the police reports."),
    **dict.fromkeys(("calendars", "agenda", "search", "show_event"), ("Jag kollar kalendern.", "Let me check the calendar.")),
    "add_event": ("Jag lägger in det.", "I'll add it."),
    "edit_event": ("Jag ändrar det.", "I'll change it."),
    "delete_event": ("Jag tar bort det.", "I'll remove it."),
    "till_telefonen": ("Jag skickar det till mobilen.", "I'll send it to your phone."),
    **dict.fromkeys(("find_streaming", "find_film", "tv_status", "last_result"), ("Jag kollar tv:n.", "Let me check the TV.")),
    **dict.fromkeys(("play_streaming", "continue_tv4", "search_and_play_youtube", "play_latest_youtube", "play_film",
                     "playback", "volume", "power"), ("Jag fixar det på tv:n.", "I'll do it on the TV.")),
}
DIRECT_TOOLS = {"radio", "volym", "spotify"}
RESULT_KEPT = 200     # characters of each tool result kept in the history
EARLY_TOOLS = {"hemmet", "vader", "wikipedia", "nyheter", "flygplan", "iss", "polisen", "webbsok", "resa", "tvatt",
               "automatiska_bokningar", "morgon_tv", "vackningar"}
# A tool whose result is the whole spoken answer ends the question without another model round. On Sonnet 5.5 a
# round costs about 1.3 s even for a six-word answer ("Stäng av radion": 2.7 s, two rounds, 58 output tokens,
# 2026-10-23), and its text arrives only when the round ends. The radio's rule in the prompt is to answer with
# "Spelar P3." or "Radion är av.", nothing more, so the server says that itself when the model wrote no text,
# asked for that one tool and it went through; a failure still goes back to the model, which explains it.
# JARVIS_DIRECT_REPLIES=0 turns it off.
DIRECT_REPLIES = os.environ.get("JARVIS_DIRECT_REPLIES", "1") != "0"
RADIO_STARTED = re.compile(r"(?:Home Assistant|Jarvis) har startat (.+?) på [^.]+\.")
# volym's result ("Panelen: 40 %.", "JBL Charge 4: 25 %."). Volume took two rounds, 2.2-3.7 s (2026-10-27).
VOLUME_LEVEL = re.compile(r"(.+): (\d{1,3}) %\.")

# Read-only home status (home_status.py), on when JARVIS_HOME_TOKEN_FILE is set. warm() reads it as well.
HOME_TOOL = {"type": "function", "name": "hemmet", "strict": False,
             "description": ("Läget hemma just nu: ytterdörren, temperaturen inne, luften och luftrenaren, brand- och "
                             "vattenlarmet, solen, nästa tvättid, vädret ute med regn eller snö de närmaste timmarna "
                             "och nästa tunnelbana från Stadshagen åt båda hållen. Läser bara, ändrar inget."),
             "parameters": {"type": "object", "properties": {}}}
# Free lookups (lookup.py), always offered: Wikipedia and news headlines.
WEATHER_TOOL = {"type": "function", "name": "vader", "strict": False,
                "description": ("SMHI-prognos för Stockholm/Kungsholmen: imorgon, helgen, veckan eller hela "
                                "tillgängliga prognosen (normalt cirka 10 dygn). Temperatur, väder, vind/byar, "
                                "nederbörd och sannolikhet med faktiska datum/tider. Använd vid framtida utflykter; "
                                "hemmet visar bara närmaste 12 timmarna. Utan argument visas hela prognosen. "
                                "Med from_date visas den dagen, med to_date ett intervall. Läser bara."),
                "parameters": {"type": "object", "properties": {
                    "from_date": {"type": "string", "description": "Första dagen YYYY-MM-DD, svensk tid"},
                    "to_date": {"type": "string", "description": "Sista dagen YYYY-MM-DD, inklusive"},
                    "from_time": {"type": "string", "description": "Tidigaste klockslag varje dag HH:MM, t.ex. 10:00"},
                    "to_time": {"type": "string", "description": "Sluttid varje dag HH:MM, t.ex. 16:00"}}}}
WIKI_TOOL = {"type": "function", "name": "wikipedia", "strict": False,
             "description": ("Slår upp ett ämne på Wikipedia, på svenska och annars engelska, och ger artikelns "
                             "inledning: personer med födelsedatum, platser, händelser, historia och förklaringar. "
                             "Läser bara."),
             "parameters": {"type": "object", "required": ["query"], "properties": {
                 "query": {"type": "string", "description": "Ett namn eller ämne, till exempel \"Anna Book\""}}}}
NEWS_TOOL = {"type": "function", "name": "nyheter", "strict": False,
             "description": ("De senaste rubrikerna från en nyhetskälla, med tid och en kort sammanfattning. "
                             "Läser bara."),
             "parameters": {"type": "object", "properties": {
                 "source": {"type": "string", "enum": list(lookup.FEEDS),
                            "description": ("svt: Sverige och världen (standard), stockholm: SVT Stockholm, "
                                            "sport: SVT Sport, dn, aftonbladet, expressen, bbc: världen på engelska")},
                 "count": {"type": "integer", "description": "Hur många rubriker, 1 till 15 (standard 8)"}}}}
PLANES_TOOL = {"type": "function", "name": "flygplan", "strict": False,
               "description": ("Flygplanen närmast hemmet just nu, från frivilligas mottagare (adsb.lol): flygbolag, "
                               "typ, avstånd och väderstreck från er, höjd, fart och riktning, och rutten när den "
                               "stämmer med planets läge och kurs. Läser bara."),
               "parameters": {"type": "object", "properties": {
                   "radius_km": {"type": "integer", "description": "Hur långt bort, 2 till 100 km (standard 25)"}}}}
ISS_TOOL = {"type": "function", "name": "iss", "strict": False,
            "description": ("Var rymdstationen ISS är just nu: över vilket land eller hav, höjd, fart, avstånd från er "
                            "och om den är ovanför er horisont, och när den syns härifrån med blotta ögat det närmaste "
                            "dygnet: tider, riktning och hur högt. Läser bara."),
            "parameters": {"type": "object", "properties": {}}}
POLICE_TOOL = {"type": "function", "name": "polisen", "strict": False,
               "description": ("Polisens senaste händelser från polisen.se, med polisens egna korta sammanfattningar. "
                               "Läser bara."),
               "parameters": {"type": "object", "properties": {
                   "place": {"type": "string", "description": "Kommun eller län (standard Stockholm)"},
                   "query": {"type": "string", "description": "Bara händelser som nämner det, till exempel Kungsholmen"},
                   "hours": {"type": "integer", "description": "Hur långt bakåt, 1 till 168 timmar (standard 24)"}}}}
SEARCH_TOOL = {"type": "function", "name": "webbsok", "strict": False,
               "description": ("Söker på webben och ger träffarnas rubrik, korta utdrag och adress; med url läser "
                               "den i stället texten på en sida. Använd det bara när wikipedia och nyheter inte "
                               "räcker: matchresultat, öppettider, priser och färska händelser. Tar ett par sekunder, "
                               "en sida några till. Läser bara."),
               "parameters": {"type": "object", "properties": {
                   "query": {"type": "string", "description": "Vad som ska sökas, kort och specifikt"},
                   "url": {"type": "string", "description": "En sida att läsa, från en tidigare sökning"}}}}
JOURNEY_TOOL = {"type": "function", "name": "resa", "strict": False,
                "description": ("SL-resplan för vald framtida tid, med gångdelar, linjer, byten och klockslag. "
                                "Använd inför utflykt/bokning, inte hemmet-verktygets avgångar just nu. "
                                "Ange arrival_at ELLER departure_at. Med return_at kontrolleras även hemresan "
                                "parallellt och besökets SMHI-prognos bifogas som visit_weather. "
                                "visit anger besökstid, calendar_interval hela bokningen inklusive resor; "
                                "first_vehicle är första tågets/bussens avgång, inte tiden att gå hemifrån. "
                                "home_by är nästa bokning hemma (10 min marginal räknas in). "
                                "Hem/Stadshagen inkluderar 3 min gång till/från stationen, konfigurerbart. "
                                "Tider YYYY-MM-DDTHH:MM i Stockholm, gärna med UTC-offset. Läser bara."),
                "parameters": {"type": "object", "required": ["destination"], "properties": {
                    "destination": {"type": "string", "description": "Besöksplats eller adress, t.ex. Moderna museet"},
                    "origin": {"type": "string", "description": "Startplats, standard Stadshagen (hemma)"},
                    "arrival_at": {"type": "string", "description": "Framme vid besöksplatsen senast denna tid"},
                    "departure_at": {"type": "string", "description": "Lämna startplatsen tidigast denna tid"},
                    "return_at": {"type": "string", "description": "Lämna besöksplatsen tidigast vid besökets slut"},
                    "home_by": {"type": "string", "description": "Tillbaka vid startplatsen före nästa bokning"}}}}
RADIO_TOOL = {"type": "function", "name": "radio", "strict": False,
              "description": ("Sveriges Radio på panelens högtalare, eller på Bluetooth-högtalaren medan en är kopplad: spela en kanal, stoppa, "
                              "pausa (pause) och fortsätta (resume), stäng av om en stund (sleep med minutes; 0 tar bort "
                              "timern), eller läs vad som går just nu (programmet och låten). Efter play eller stop svarar "
                              "du med bara \"Spelar\" och kanalens namn (\"Spelar P3.\") eller \"Radion är av.\", inget mer: radion ligger tyst under ditt "
                              "svar. Vad som går säger du bara när Joel frågar (action now)."),
              "parameters": {"type": "object", "properties": {
                  "action": {"type": "string", "enum": ["play", "stop", "pause", "resume", "sleep", "now"],
                             "description": "Standard play"},
                  "minutes": {"type": "integer", "minimum": 0, "maximum": 240, "description": "Vid sleep: om hur många minuter"},
                  "channel": {"type": "string",
                              "description": "P1, P2, P3, P4 (P4 Stockholm), P3 Din gata, Ekot ... (standard P3)"}}}}
# The TV connector on Jarvis (the tv skill's MCP server), on when JARVIS_TV_MCP is set. Only these tools
# are offered by voice: no key presses, typing, app launches, screenshots or morning TV.
TV_TOOLS = ("tv_status,find_streaming,play_streaming,continue_tv4,search_and_play_youtube,play_latest_youtube,find_film,play_film,"
            "playback,volume,power,last_result")
# The calendar's MCP server on Jarvis (calendar-mcp), on when JARVIS_CALENDAR_MCP is set. By voice Jarvis
# reads, adds, edits and deletes events; JARVIS_CALENDAR_MCP_TOOLS (or --mcp-tools) narrows it.
# Calendar administration and undo are still excluded from the voice tool set.
CALENDAR_READ_TOOLS = "calendars,agenda,search,show_event"
CALENDAR_WRITE_TOOLS = ("add_event", "edit_event", "delete_event")
CALENDAR_TOOLS = ",".join((CALENDAR_READ_TOOLS, *CALENDAR_WRITE_TOOLS))


def redact(text):
    return re.sub(r"(sk-|gsk_)[A-Za-z0-9_*-]{4,}", r"\1…", text)


def direct_reply(name, args, is_error, text, language=None):
    """The spoken answer one tool's result stands for (DIRECT_REPLIES), or None when the model phrases it.
    Spotify's and the radio's results are read aloud as they are: keep them a plain answer, never a note to the
    model (one was read aloud word for word, 2026-10-28; tests/test_spotify.py checks the play text)."""
    if not is_error and name == "spotify" and (args or {}).get("action") in ("play", "pause", "resume", "next", "previous", "stop",
                                                                              "shuffle", "add_to_queue") and language != "en":
        return text
    if not is_error and name == "volym" and (m := VOLUME_LEVEL.fullmatch((text or "").strip())):
        where, level = m.groups()
        if language == "en":
            return f"{'The panel' if where == 'Panelen' else where} is at {level} percent."
        return f"{where} står på {level} procent."
    if is_error or name != "radio":
        return None
    action = str((args or {}).get("action") or "play").lower()
    if action == "stop":
        return "The radio is off." if language == "en" else "Radion är av."
    if action == "play" and (m := RADIO_STARTED.fullmatch((text or "").strip())):
        return ("Playing " if language == "en" else "Spelar ") + m.group(1) + "."
    return None


def emit(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


@dataclass
class Call:
    """One tool call the model asked for. problem is set when its arguments cannot be used; the model
    then gets that text back as an error instead of the tool running."""
    id: str
    name: str
    args: dict | None
    problem: str | None = None


@dataclass
class Round:
    """What one request to the model gave: the text it streamed, the tools it asked for, its cost
    and tokens (input_tokens counts cached and written input too), and the provider's own message."""
    text: str = ""
    calls: list = field(default_factory=list)
    cost: float = 0.0
    usage: dict = field(default_factory=dict)
    raw: object = None


class MCPClient:
    """Minimal MCP client for the streamable HTTP transport (JSON or SSE responses).
    If the server forgets the session (it restarted), the next call starts a new one."""

    def __init__(self, url, token=None):
        self.url, self.token, self.session, self.next_id, self.tools = url, token, None, 1, None

    def _post(self, payload, timeout=60):
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                   "MCP-Protocol-Version": "2025-06-18"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        req = urllib.request.Request(self.url, data=json.dumps(payload).encode(), method="POST", headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            self.session = r.headers.get("Mcp-Session-Id") or self.session
            body = r.read().decode()
            if "id" not in payload:
                return None
            if r.headers.get("Content-Type", "").startswith("text/event-stream"):
                for line in body.splitlines():
                    if line.startswith("data: "):
                        msg = json.loads(line[6:])
                        if msg.get("id") == payload["id"]:
                            return msg
                raise RuntimeError("no reply in the event stream")
            return json.loads(body)

    def call(self, method, params=None, retry=True, timeout=60):
        msg = {"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params or {}}
        self.next_id += 1
        try:
            reply = self._post(msg, timeout)
        except urllib.error.HTTPError as e:
            if retry and e.code in (400, 404) and self.session and method != "initialize":
                self.session = None
                self.start()
                return self.call(method, params, retry=False, timeout=timeout)
            raise
        if "error" in reply:
            raise RuntimeError(f"MCP {method}: {reply['error']}")
        return reply["result"]

    def start(self):
        self.session = None
        self.call("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                 "clientInfo": {"name": "hallpanel-agent", "version": "1"}}, retry=False)
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.tools = self.call("tools/list", retry=False)["tools"]
        return self.tools

    def run(self, name, args, timeout=60):
        result = self.call("tools/call", {"name": name, "arguments": args}, timeout=timeout)
        text = "\n".join(c.get("text", "") for c in result.get("content", []) if c.get("type") == "text")
        return bool(result.get("isError")), text


def mcp_client(url, token_file=None):
    """An MCPClient for url with the bearer token in token_file, or None without a url."""
    if not url:
        return None
    return MCPClient(url, Path(token_file).expanduser().read_text().strip() if token_file else None)


def tools_from_env():
    """The tool settings every brain's agent_from_env() passes on: the mock tools and the calendar's MCP server."""
    return {"mock_tools": os.environ.get("JARVIS_MOCK_TOOLS") == "1",
            "mcp_url": os.environ.get("JARVIS_CALENDAR_MCP") or None,
            "mcp_token_file": os.environ.get("JARVIS_CALENDAR_MCP_TOKEN_FILE"),
            "mcp_tools": os.environ.get("JARVIS_CALENDAR_MCP_TOOLS") or None}


class ToolAgent:
    """The shared half of every brain; see the top of the file."""
    label = "agent"

    def _init_tools(self, mock_tools, mcp_url, mcp_token_file, mcp_tools):
        """The tool setup every brain shares: mock tools, MCP, the TV, the guard, the agenda, the home."""
        self.mock = None
        if mock_tools:
            sys.path.insert(0, str(ROOT / "tests"))
            import mock_tools_mcp as mock
            self.mock = mock
        self.mcp = mcp_client(mcp_url, mcp_token_file)
        self.allowed = set((mcp_tools or CALENDAR_TOOLS).split(","))
        self.tv = mcp_client(os.environ.get("JARVIS_TV_MCP"), os.environ.get("JARVIS_TV_MCP_TOKEN_FILE"))
        self.tv_names = set()
        self.tv_allowed = set((os.environ.get("JARVIS_TV_MCP_TOOLS") or TV_TOOLS).split(","))
        self.tools = None
        self.read_only = set()
        self._snapshot_lock = threading.Lock()
        self._agenda_generation = 0
        self._warm_inflight = threading.Lock()
        self._prefetch_client = None
        self.agenda = None              # (time.monotonic() when read, agenda text) from the last warm()
        self.home = Home.from_env()
        self.local_tools = {}           # name -> function(args) -> (is_error, text), filled by _ensure_tools
        self.screen = screen.Screen()   # the cards the question's answer shows on the panel (the tool skarm)
        # What Joel said in this question (spoken_change.Turn), for the gated changes (tvatt_boka, morgon_tv_andra,
        # vackning_andra). The in-process server sets it around each ask; a command-mode agent answers one
        # question per process and gets it from JARVIS_SPOKEN.
        self.spoken = spoken_change.Turn.from_env()
        self.extra_tools = []           # (tool, run) pairs the server adds before the warm-up: the memory's tools
        self._mcp_lock = threading.Lock()   # one call at a time per MCP session; the other tools run in parallel
        self._guard_lock = threading.Lock()
        self.home_seen = None           # (time.monotonic() when read, home status text)
        self.last_tools = []            # the tools the last question used: name, ms, error, round
        self.last_rounds = []           # the last question's requests to the model: ms, first text, output tokens
        self._step = 0                  # the round whose tools are running
        self._round_t0 = None           # time.monotonic() when the running round's model request began
        self._language = None           # the answer's language this question, for direct_reply
        self._warm_jobs = ()            # a running warm-up's agenda and home reads, for wait_for_snapshot
        self._event = None              # the question's last add_event that went through, for the phone
        self.log_path = os.environ.get("AGENT_TOOL_LOG")
        self.early = set(EARLY_TOOLS)   # and the calendar's read-only tools, added by _ensure_tools
        self._early = None              # while a round streams: start(call) for a call that is fully written

    # -- tools --------------------------------------------------------------------------------
    def _ensure_tools(self):
        if self.tools is not None:
            return
        tools = []
        if self.mock:
            tools += [{"type": "function", "name": t["name"], "description": t["description"],
                       "parameters": t["inputSchema"], "strict": False} for t in self.mock.TOOLS]
        if self.mcp:
            for t in self.mcp.start():
                if (t.get("annotations") or {}).get("readOnlyHint"):
                    self.read_only.add(t["name"])
                    self.early.add(t["name"])
                if t["name"] in self.allowed:
                    params = t.get("inputSchema") or {"type": "object", "properties": {}}
                    if t["name"] == "add_event":        # ours, taken off again before the call goes to the calendar
                        params = {**params, "properties": {**(params.get("properties") or {}), "overlap_ok": OVERLAP_ARG}}
                    tools.append({"type": "function", "name": t["name"], "description": t.get("description", ""),
                                  "parameters": params, "strict": False})
        if self.tv:
            try:                                # a TV connector that is down must not take the agent with it
                for t in self.tv.start():
                    if t["name"] in self.tv_allowed:
                        if (t.get("annotations") or {}).get("readOnlyHint"):
                            self.read_only.add(t["name"])
                        self.tv_names.add(t["name"])
                        tools.append({"type": "function", "name": t["name"], "description": t.get("description", ""),
                                      "parameters": t.get("inputSchema") or {"type": "object", "properties": {}},
                                      "strict": False})
            except (OSError, RuntimeError, ValueError, urllib.error.URLError) as e:
                print(f"tv connector unavailable, no TV tools until the server restarts: {e}", file=sys.stderr)
        local = [(HOME_TOOL, lambda args: (False, self._read_home()))] if self.home else []
        if self.home:                           # the AI sensors and the page request go through Home Assistant
            local.append((ai_usage.TOOL, lambda args: ai_usage.usage()))
            # Morgon-TV and the wakeups: their HA helpers, read (early) and changed (after the whole answer)
            local += [(mornings.TV_TOOL, lambda args: mornings.tv_read()),
                      (mornings.TV_CHANGE_TOOL, lambda args: mornings.tv_change(**{**args, "spoken": self.spoken})),
                      (mornings.WAKE_TOOL, lambda args: mornings.wake_read()),
                      (mornings.WAKE_CHANGE_TOOL,
                       lambda args: mornings.wake_change(**{**args, "spoken": self.spoken}))]
        forecast_home = self.home or Home()    # weather works without a Home Assistant token too
        local.append((WEATHER_TOOL, lambda args: forecast_home.forecast(**args)))
        local += [(WIKI_TOOL, lambda args: lookup.wikipedia(args.get("query"))),
                  (NEWS_TOOL, lambda args: lookup.news(args.get("source"), args.get("count"), shown=self.screen.offer_news)),
                  (PLANES_TOOL, lambda args: lookup.planes(args.get("radius_km"))),
                  (ISS_TOOL, lambda args: lookup.iss()),
                  (POLICE_TOOL, lambda args: lookup.police(args.get("place"), args.get("query"), args.get("hours"))),
                  (JOURNEY_TOOL, lambda args: self._plan_journey(args, forecast_home)),
                  (RADIO_TOOL, lambda args: radio.radio(args.get("action"), args.get("channel"), args.get("minutes"))),
                  (moon_phase.TOOL, lambda args: moon_phase.moon()),
                  (volume.TOOL, lambda args: volume.control(args.get("action"), args.get("percent"), args.get("step"),
                                                            args.get("where"))),
                  (screen.TOOL, self.screen.show),
                  (panel.TOOL, lambda args: panel.control(**args))]    # the panel's page, background and light
        if lookup.jina_key():
            local.append((SEARCH_TOOL, lambda args: lookup.websearch(args.get("query"), args.get("url"))))
        local += self.extra_tools
        if speaker.configured():
            local.append((speaker.TOOL, lambda args: speaker.control(args.get("action"), args.get("name"))))
        if laundry.configured():               # tvatt reads (early); tvatt_boka books, after the whole answer
            local += [(laundry.TOOL, lambda args: laundry.read(**args)),
                      (laundry.BOOK_TOOL, lambda args: laundry.book(**{**args, "spoken": self.spoken}))]
        local.append((bookers.TOOL, lambda args: bookers.read()))   # the booking jobs' status, read only
        if spotify.configured():
            local.append((spotify.TOOL, lambda args: spotify.control(action=args.get("action"), query=args.get("query"),
                          media_type=args.get("media_type", "track"), uri=args.get("uri"), level=args.get("level"),
                          enabled=args.get("enabled"), shuffle=args.get("shuffle"))))
        for tool, run in local:
            tools.append(tool)
            self.read_only.add(tool["name"])
            self.local_tools[tool["name"]] = run
        self.tools = tools

    def _plan_journey(self, args, forecast_home):
        """Keep an outing's weather attached to its actual visit, not the current home snapshot."""
        error, text = journey.plan(**args)
        if error:
            return error, text
        plan = json.loads(text)
        if visit := plan.get("visit"):
            first, last = visit["from"], visit["to"]
            selection = {"from_date": first[:10], "to_date": last[:10]}
            if first[:10] == last[:10]:
                selection.update(from_time=first[11:16], to_time=last[11:16])
            failed, forecast = forecast_home.forecast(**selection)
            plan["visit_weather"] = {"verified": not failed, "visit_from": first, "visit_to": last,
                                     "scope": "visit window" if first[:10] == last[:10] else "full visit days",
                                     "forecast": forecast if failed else json.loads(forecast)}
            if failed:
                plan["problem"] = "Besökstidens väder kunde inte verifieras. Påstå inte att vädret passar utflykten."
            return failed, json.dumps(plan, ensure_ascii=False)
        return False, text

    def _collisions(self, args):
        """The timed events an add_event would overlap ("Möte 12:00–13:00 (Jobb)"), or [] when there are none or
        the calendar cannot tell. All-day events never collide. Called with the MCP lock held."""
        def parse(value):
            try:
                return datetime.strptime(str(value or "").strip()[:16], "%Y-%m-%d %H:%M")
            except ValueError:
                return None
        start = parse(args.get("start"))
        if start is None:
            return []                       # all-day, or a format the calendar will refuse anyway
        end = parse(args.get("end")) or start + timedelta(hours=1)
        days = (start.date() - datetime.now(TZ).date()).days + 1
        if not 1 <= days <= 60:
            return []
        try:
            # CalDAV returns occurrences overlapping this window, including those beginning earlier.
            # A booking needs its own days, not every day between now and its start.
            is_error, text = self.mcp.run("agenda", {"start": start.date().isoformat(),
                "end": (end - timedelta(microseconds=1)).date().isoformat()}, timeout=8)
        except (OSError, RuntimeError, ValueError, urllib.error.URLError):
            return []
        hits = []
        for line in [] if is_error else text.splitlines():
            if m := AGENDA_LINE.match(line.strip()):
                day, first, last_day, last, calendar, title = m.groups()
                begins, ends = parse(f"{day} {first}"), parse(f"{last_day or day} {last}")
                ends += timedelta(days=1) if ends <= begins else timedelta()
                if begins < end and start < ends:
                    hits.append(f"{title.strip()} {first}–{last} ({calendar.strip()})")
        return hits

    def duplicate_create(self, name, args):
        """Refuse a create (add_*, create_*, book_*, *_boka) identical to one done in the last GUARD_MINUTES.
        A model can re-run an old request it sees in the history; this keeps that from making
        duplicates. Kept in a file, so it holds across questions in both run modes."""
        if not re.search(r"(^|_)(add|create|book|boka)", name):
            return None
        key = name + json.dumps(args, sort_keys=True, ensure_ascii=False)
        now = time.time()
        try:
            seen = json.loads(GUARD_FILE.read_text())
        except (OSError, ValueError):
            seen = {}
        seen = {k: t for k, t in seen.items() if now - t < GUARD_MINUTES * 60}
        if key in seen:
            return round((now - seen[key]) / 60)
        seen[key] = now
        GUARD_FILE.parent.mkdir(parents=True, exist_ok=True)
        GUARD_FILE.write_text(json.dumps(seen))
        return None

    def run_tools(self, calls):
        """Run [(name, args), ...] at the same time; results in the same order. The tools of one answer
        rarely depend on each other, and the slowest one sets the wait instead of the sum."""
        if len(calls) <= 1:
            return [self.run_tool(name, args) for name, args in calls]
        with ThreadPoolExecutor(len(calls)) as pool:
            return list(pool.map(lambda call: self.run_tool(*call), calls))

    def run_tool(self, name, args):
        t0 = time.monotonic()
        phases = {}
        with self._guard_lock:
            dup = self.duplicate_create(name, args) if name in {t["name"] for t in self.tools} else None
        if name not in {t["name"] for t in self.tools}:
            is_error, text = True, f"Verktyget {name} är inte tillgängligt."
        elif dup is not None:
            is_error, text = True, (f"Stoppad: exakt samma sak lades till för {dup} minuter sedan, så den läggs inte "
                                    "till igen. Säg att den redan finns.")
        elif name in self.local_tools:
            is_error, text = self.local_tools[name](args)
        elif self.mock and name in {t["name"] for t in self.mock.TOOLS}:
            is_error, text = self.mock.call(name, args)
        elif name in self.tv_names:
            try:                            # the connector answers STILL WORKING itself after 45 s
                with self._mcp_lock:
                    is_error, text = self.tv.run(name, args, timeout=50)
            except (OSError, RuntimeError, ValueError, urllib.error.URLError) as e:
                is_error, text = True, f"Tv-styrningen svarade inte ({type(e).__name__})."
        else:
            overlap_ok = name == "add_event" and bool(args.get("overlap_ok"))
            if name == "add_event":
                args = {k: v for k, v in args.items() if k != "overlap_ok"}
                if not args.get("alarms") and re.search(r"\d:\d\d", str(args.get("start"))):
                    args = {**args, "alarms": [default_alarm(args)]}
            waiting = time.monotonic()
            with self._mcp_lock:
                acquired = time.monotonic()
                hits = self._collisions(args) if name == "add_event" and not overlap_ok else []
                checked = time.monotonic()
                if hits:
                    is_error, text = True, ("Inte inlagt: krockar med " + ", ".join(hits) + ". Välj en annan tid, eller "
                                            "lägg det med overlap_ok bara om Joel uttryckligen vill det.")
                else:
                    is_error, text = self.mcp.run(name, args)
                phases = {"queue_ms": round((acquired - waiting) * 1000),
                          "collision_ms": round((checked - acquired) * 1000),
                          "execute_ms": round((time.monotonic() - checked) * 1000)}
        if name not in self.read_only and name not in self.tv_names:
            with self._snapshot_lock:
                self._agenda_generation += 1
                self.agenda = None          # discard snapshots begun before this change too
        # at_ms: started this long after its round's model request began; early_ms (added by _ask_round): of its
        # time, how much ran while the model still wrote, which a lookup started at the round's end would add.
        at = {"at_ms": round((t0 - self._round_t0) * 1000)} if self._round_t0 is not None else {}
        # result: the start of what the model was told, kept in history.db only (the server strips it before
        # anything leaves): on 2026-10-28 Jarvis read out an instruction no code contains, and with the journal
        # rotated nothing showed what the tool had answered.
        self.last_tools.append({"name": name, "ms": round((time.monotonic() - t0) * 1000), "error": is_error,
                                "round": self._step, **at, **phases, "result": str(text)[:RESULT_KEPT],
                                # args too (2026-11-01: a weather window that held no forecast point could not be
                                # confirmed afterwards, since only the result was kept); history.db only, like result
                                "args": json.dumps(args, ensure_ascii=False, default=str)[:RESULT_KEPT]})
        if self.log_path:
            with open(self.log_path, "a") as f:
                f.write(json.dumps({"tool": name, "args": args, "error": is_error, "result": text[:500],
                                    "ms": round((time.monotonic() - t0) * 1000)}, ensure_ascii=False) + "\n")
        return is_error, text

    # -- the question loop ----------------------------------------------------------------------
    def ask(self, prompt, on_text=None, instructions=None, on_tools=None, language=None):
        """Answer one prompt, calling tools as needed. Returns (reply, cost in USD, usage), usage with
        the tokens and "steps", the number of requests to the model. instructions is the fixed part of the
        prompt, which each brain puts first and caches; only the changing prompt after it is billed in full.
        language ("sv", "en" or None) is the answer's, for the replies the server speaks itself (direct_reply).
        A sentence the model writes before its first tools ("Genast. Jag kollar flyget och vädret.") streams
        on_text followed by a line break, so the voice speaks it while the tools run; on_tools(names) is
        called as each round's tools start. After such a sentence, each later round is held until it ends:
        text written before more tools is a note ("Besök 10:30–12:30, innan regnet."), not something to
        say, and is spoken only if nothing comes after the last tools. A round whose only tool is the
        screen (skarm, with the written answer) ends the question: nothing goes back to the model. Without an
        answer written, the screen goes back like any tool and the model answers next. Written with the last
        tools instead (a booking, a push), its card shows only if they all went through.
        A lookup (EARLY_TOOLS) starts as soon as the brain reports its call written (_ready), while the model
        writes the rest. Model text waits until the round ends, and drafts in calendar write rounds are discarded;
        but when nothing was said yet and the question cannot write the calendar (spoken_change.may_write_calendar),
        the text streams to on_text as it is written.
        A round with no text and one tool whose result is the answer itself (direct_reply, the radio) ends the
        question with that answer, without another round. last_rounds keeps each request's time, the time to
        its first text and its output tokens, so the history shows where a slow answer spent its time."""
        self._ensure_tools()
        if "tvatt" in self.local_tools:     # the laundry site's 2.5 s read starts now, beside the model
            laundry.prefetch(self.spoken.said)
        self.last_tools, self.last_rounds, self._language = [], [], language
        self._event = None
        self.screen.clear()
        convo = self._begin(prompt, instructions)
        final, held, spoke, cost, steps = "", "", False, 0.0, 0
        used = {"input_tokens": 0, "output_tokens": 0, "cached_tokens": 0, "cache_write_tokens": 0}
        pool = ThreadPoolExecutor(max_workers=8)
        try:
            for steps in range(1, MAX_STEPS + 1):
                # The last round may not call tools: after seven rounds of them the model must answer with what
                # it has (2026-10-23: six searches for an unusual outing used up the rounds before the answer).
                convo["no_tools"] = steps == MAX_STEPS
                done, final, held, spoke, r_cost, r_used = self._ask_round(
                    convo, steps, pool, on_text, on_tools, final, held, spoke)
                cost += r_cost
                for key in used:
                    used[key] += r_used.get(key) or 0
                if done:
                    break
            else:
                final = held or final
        finally:
            self._early = self._round_t0 = None
            pool.shutdown(wait=False)       # a lookup the answer dropped may still run; nobody waits for it
        return final, round(cost, 6), {**used, "steps": steps}

    def command(self, name, args, language=None):
        """Run one tool for an exact spoken command (quick_command.py) and return what to say: the
        direct_reply, or the tool's own text. No model round; last_tools times it for the history. None: the
        command was not sure (a Spotify search without a confident match), ask the model instead."""
        self._ensure_tools()
        self.last_tools, self.last_rounds, self._language, self._step = [], [], language, 0
        self._round_t0 = None
        if name == "music":                 # "stäng av musiken": whatever plays
            name, args = self._music_off()
        if name == "spotify_play":          # "spela X på Spotify": None leaves it to the model
            if "spotify" not in self.local_tools:
                return None
            started = time.monotonic()
            result = spotify.quick_play(args["query"], args.get("media_type", "track"))
            if result is None:
                return None
            self.last_tools = [{"name": "spotify", "ms": round((time.monotonic() - started) * 1000), "error": result[0],
                                "round": 0, "direct": True}]
            return result[1]
        is_error, text = self.run_tool(name, args)
        return direct_reply(name, args, is_error, text, language) or text

    def _music_off(self):
        """Spotify's pause when Spotify plays, else the radio's stop (also when nothing plays: a no-op there)."""
        if "spotify" in self.local_tools:
            try:
                state = spotify.status()
                if state.get("status") == "playing" and state.get("active") is True:   # not Joel's phone
                    return "spotify", {"action": "pause"}
            except spotify.SpotifyError:
                pass
        return "radio", {"action": "stop"}

    def _ask_round(self, convo, steps, pool, on_text, on_tools, final, held, spoke):
        """One round of ask(): ask the model, start the lookups it writes, run the rest, hand the results back.
        Returns (done, final, held, spoke, cost, usage)."""
        self._step = steps
        hold, parts, started = bool(on_text) and spoke, [], {}
        t0, first_text = time.monotonic(), []
        # Nothing said yet and no calendar write in sight: speak the text as it is written instead of after the
        # round (2026-11-04: a 118-token answer waited 1.5 s for its last word). Text before tools here would be
        # said anyway; only a calendar write's draft must not be, so those questions still wait.
        live = bool(on_text) and not hold and not spoken_change.may_write_calendar(self.spoken)

        def stream(d):
            if not first_text and d.strip():
                first_text.append(time.monotonic() - t0)
            parts.append(d)
            if live:
                on_text(d)

        def start(call):
            if call.problem is not None or call.name not in self.early or call.id in started:
                return
            started[call.id] = pool.submit(self.run_tool, call.name, call.args)

        self._early, self._round_t0 = start, t0
        try:
            r = self._round(convo, stream if on_text else None)
        finally:
            self._early = None
        self.last_rounds.append({"ms": round((time.monotonic() - t0) * 1000),
                                 "first_ms": round(first_text[0] * 1000) if first_text else None,
                                 "out": r.usage.get("output_tokens"), "tools": len(r.calls)})
        text = r.text.strip()
        calendar_write = any(call.name in CALENDAR_WRITE_TOOLS for call in r.calls)
        streamed = live and any(d.strip() for d in parts)
        if calendar_write:
            if streamed:        # may_write_calendar missed this question: the draft was already said
                print("agent: draft spoken live before calendar write", file=sys.stderr)
            elif text:
                print("agent: suppressed text before calendar write", file=sys.stderr)
            text, held = "", ""
            parts.clear()
        # No tools, or only the screen with the answer written: done. The screen without an answer (the model
        # meant to write it once the screen was confirmed) goes back to the model like any tool, so the answer
        # still comes (2026-10-23: a reply that was only "Genast. Jag tittar …").
        if not r.calls or (text and all(c.name in FINAL_TOOLS for c in r.calls)):
            self.run_tools([(c.name, c.args) for c in r.calls if c.problem is None])
            if on_text and not streamed and (say := ("".join(parts) or text) if text else held):
                on_text(say)
            return True, text or held or final, held, spoke, r.cost, r.usage
        if hold:
            held = text or held
        elif text:
            final, spoke = text, True
            if on_text:
                if not streamed:
                    on_text("".join(parts) or text)
                on_text("\n")
        usable = [c for c in r.calls if c.problem is None]
        # The phone gets the event added in the same step, or in an earlier one of this question, instead of
        # the plan written a second time (2026-10-23: Luna booked in one round and sent in the next).
        event = next((c.args for c in usable if c.name == "add_event"), None) or self._event
        for c in usable:
            if c.name == "till_telefonen" and c.args.get("from_calendar") and event:
                c.args = {**c.args, "event": event}
        if on_text and not (text or hold):
            notice = self._notice(usable)
            if notice:
                say = notice[self._language == "en"]
                print(f"agent: said for tools: {say}", file=sys.stderr)
                on_text(say)
                on_text("\n")
                spoke = True
        if on_tools and usable:
            on_tools([c.name for c in usable])
        # the lookups already running, and the rest of the calls started now, all at the same time
        running = {c.id: started.get(c.id) or pool.submit(self.run_tool, c.name, c.args) for c in usable}
        results = [(True, c.problem) if c.problem is not None else running[c.id].result() for c in r.calls]
        model_ms = self.last_rounds[-1]["ms"]
        for t in self.last_tools:
            if t.get("round") == steps and t.get("at_ms", model_ms) < model_ms:
                t["early_ms"] = min(t["ms"], model_ms - t["at_ms"])
        # The tool's result is the answer (the radio): say it now instead of asking the model to repeat it.
        if DIRECT_REPLIES and not text and (say := self._direct_say(r.calls, results, spoke)):
            print(f"agent: direct reply for {', '.join(c.name for c in r.calls)}", file=sys.stderr)
            if on_text:
                on_text(say)
            return True, say, held, spoke, r.cost, r.usage
        self._event = next((c.args for c, (is_error, _) in zip(r.calls, results)
                            if c.name == "add_event" and not is_error), self._event)
        if steps == MAX_STEPS - 1 and results:          # the next round may not call tools: say so, and how to answer
            is_error, out = results[-1]
            results[-1] = (is_error, out + LAST_ROUND)
        # The screen written with the last tools (a booking, a push) shows only if they all went through, so a
        # card never says "bokat" for a booking that failed; the model is told and can show what did go.
        failed = [c.name for c, (is_error, _) in zip(r.calls, results) if is_error and c.name not in FINAL_TOOLS]
        if failed and any(c.name in FINAL_TOOLS for c in r.calls):
            self.screen.clear()
            note = (True, f"Kortet visas inte, eftersom {', '.join(failed)} misslyckades i samma steg. Anropa "
                          "skarm igen med det som verkligen gick.")
            results = [note if c.name in FINAL_TOOLS else out for c, out in zip(r.calls, results)]
        self._add_results(convo, r, results)
        return False, final, held, spoke, r.cost, r.usage

    @staticmethod
    def _notice(calls):
        """The sentence said as this round's tools start (SLOW_NOTICES, TOOL_NOTICES), or None."""
        names = [c.name for c in calls if c.name not in FINAL_TOOLS]
        if not names or any(n in DIRECT_TOOLS for n in names):
            return None
        if slow := next((SLOW_NOTICES[n] for n in names if n in SLOW_NOTICES), None):
            return slow
        if change := next((TOOL_NOTICES[n] for n in names if n in CALENDAR_WRITE_TOOLS), None):
            return change          # what is done, not the lookups beside it
        found = {TOOL_NOTICES.get(n, NOTICE) for n in names}
        return found.pop() if len(found) == 1 else NOTICE

    def _direct_say(self, calls, results, spoke):
        """What to say for this round's results without asking the model again, or None. A laundry booking's
        answer is said even after the slow tool's sentence ("Självklart, jag tittar på tvättstugan."), since the
        booking is news; one radio, volume or Spotify call only when nothing was said yet."""
        if calls and all(c.name == "tvatt_boka" for c in calls):
            return laundry.done_reply(results, getattr(self.spoken, "said", ""), language=self._language)
        if not spoke and len(calls) == 1:
            return direct_reply(calls[0].name, calls[0].args, *results[0], self._language)
        return None

    def _ready(self, call):
        """Brains call this as soon as a tool call is fully written in the stream; a lookup starts at once."""
        if self._early is not None:
            self._early(call)

    def _begin(self, prompt, instructions):
        raise NotImplementedError

    def _round(self, convo, on_text):
        raise NotImplementedError

    def _add_results(self, convo, r, results):
        raise NotImplementedError

    def _warm_api(self, instructions):
        """Open the connection to the model's API ahead of the question; nothing by default."""

    # -- warm-up, agenda and home -----------------------------------------------------------------
    def start_warm(self, instructions, question_lock):
        """Start one independent warm-up. A calendar read must never hold the question lock."""
        self._ensure_tools()  # caller holds question_lock while establishing the immutable tool list
        if not self._warm_inflight.acquire(blocking=False):
            return False

        def background():
            try:
                with ThreadPoolExecutor(max_workers=3) as pool:
                    jobs = [pool.submit(self._warm_snapshot),
                            pool.submit(self._warm_api_independent, instructions, question_lock)]
                    if self.home:
                        jobs.append(pool.submit(self._read_home))
                    self._warm_jobs = (jobs[0], *jobs[2:])     # the reads a question may wait for, not the model
                    for job in jobs:
                        try:
                            job.result()
                        except Exception as exc:
                            print(f"background warm-up failed: {type(exc).__name__}", file=sys.stderr)
            finally:
                self._warm_jobs = ()
                self._warm_inflight.release()

        threading.Thread(target=background, daemon=True, name="jarvis-warm").start()
        return True

    def _warm_api_independent(self, instructions, question_lock):
        """Backends override this to use a separate transport, handed over only while idle."""
        return None

    def wait_for_snapshot(self, timeout):
        """Wait at most timeout seconds for a running warm-up's agenda and home reads, so a question that comes
        right after the wake word gets them in its prompt instead of spending a tool round on them (a round is
        about 1.3 s on Sonnet 5.5; the reads take 0.15-0.6 s and start 1-3 s before the question, so this is
        usually 0). Returns the seconds waited. A read that fails or is still running is left to the question."""
        jobs = self._warm_jobs
        if not jobs or timeout <= 0:
            return 0.0
        t0 = time.monotonic()
        for job in jobs:
            try:
                job.result(timeout=max(0.0, t0 + timeout - time.monotonic()))
            except Exception:           # a timeout or a failed read: the question goes on without it
                pass
        return time.monotonic() - t0

    def _warm_snapshot(self):
        if not self.mcp or not any(t["name"] == "agenda" for t in self.mcp.tools or []):
            return
        # Separate MCP session: calendar tools in an actual answer do not queue behind prefetch.
        if self._prefetch_client is None:
            self._prefetch_client = MCPClient(self.mcp.url, self.mcp.token)
        client = self._prefetch_client
        with self._snapshot_lock:
            generation = self._agenda_generation
        try:
            if client.tools is None:
                client.start()
            is_error, text = client.run("agenda", {"days": AGENDA_DAYS}, timeout=4)
            with self._snapshot_lock:
                if generation == self._agenda_generation:
                    self.agenda = None if is_error else (time.monotonic(), text.strip())
        except (OSError, ValueError, RuntimeError, urllib.error.URLError):
            pass  # keep a still-fresh previous snapshot when a background refresh fails

    def warm(self, instructions=None):
        """Open everything a question needs: the MCP session and tool list, the calendar's
        connection to iCloud and the HTTPS connection to the model. The agenda it reads to wake the
        calendar (today and the next days) is kept for fresh_agenda(). instructions is the fixed
        prompt the questions will use; the Anthropic brain writes it to its prompt cache here."""
        t0 = time.monotonic()
        self._ensure_tools()
        home = threading.Thread(target=self._read_home, daemon=True) if self.home else None
        home_started = time.monotonic()
        if home:                                # Home Assistant, SMHI and SL, alongside the calendar
            home.start()
        # Calendar prefetch and model/cache warm-up are independent. Keep them alongside the home
        # read, so a short question doesn't wait for their durations added together.
        with ThreadPoolExecutor(max_workers=2) as pool:
            calendar = pool.submit(self._warm_agenda)
            api = pool.submit(self._warm_api, instructions)
            calendar.result()
            api.result()
        if home:
            home.join(timeout=max(0, 4 - (time.monotonic() - home_started)))
        return round(time.monotonic() - t0, 2)

    def _warm_agenda(self):
        """Refresh the calendar snapshot; failure must not stop the model connection warming."""
        if self.mcp and any(t["name"] == "agenda" for t in self.mcp.tools or []):
            # A question waits for the warm-up, so a slow iCloud may hold it up 4 s at most; the
            # question then looks the calendar up itself. (Once it hung for 22 s, 2026-10-21.)
            self.agenda = None
            try:
                is_error, text = self.mcp.run("agenda", {"days": AGENDA_DAYS}, timeout=4)
                self.agenda = None if is_error else (time.monotonic(), text.strip())
            except (OSError, urllib.error.URLError, RuntimeError):
                pass

    def _read_home(self):
        text = self.home.read()
        self.home_seen = (time.monotonic(), text)
        return text

    def fresh_home(self):
        """(home status text, age in seconds) if read less than AGENDA_MAX_AGE ago, else None."""
        if self.home_seen is None or time.monotonic() - self.home_seen[0] > AGENDA_MAX_AGE:
            return None
        return self.home_seen[1], round(time.monotonic() - self.home_seen[0])

    def fresh_agenda(self):
        """(agenda text, age in seconds, days) if warm() read it less than AGENDA_MAX_AGE ago, else None."""
        if self.agenda is None:
            return None
        age = time.monotonic() - self.agenda[0]
        return (self.agenda[1], round(age), AGENDA_DAYS) if age <= AGENDA_MAX_AGE else None
