#!/usr/bin/env python3
"""Jarvis agent on the OpenAI Responses API (default model gpt-6-luna, fast tier), or on any
OpenAI-compatible Chat Completions API. The "brain" picks one (JARVIS_BRAIN in the server):

  luna   OpenAI, gpt-6-luna, fast tier, no reasoning. Key OPENAI_API_KEY (~/.config/hallpanel/openai.env)
  groq   Groq, openai/gpt-oss-20b, low reasoning, Chat Completions (Groq's Responses API cannot chain
         tool calls with previous_response_id). Key GROQ_API_KEY (~/.config/hallpanel/groq.env)

Two ways to run it:
  * In-process: server/hallpanel_server.py builds one LunaAgent at start (JARVIS_AGENT=inprocess) and
    keeps it. The HTTPS connection to OpenAI and the MCP session stay open between questions,
    and warm() opens them ahead of time (the panel calls /warm on the wake word).
  * As a command (JARVIS_AGENT_CMD): reads the prompt on stdin and prints the same stream-json
    events as `claude -p --output-format stream-json --include-partial-messages`:
      {"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", ...}}}
      {"type": "result", "result": <final text>, "is_error": false, "total_cost_usd": <estimate>}

Tools:
  mock tools        tests/mock_tools_mcp.py in-process, the same mock calendar and TV as the Claude Code test
  an MCP server     streamable HTTP, e.g. calendar-mcp on Jarvis (http://jarvis.local:3020/mcp), with a bearer
                    token file; an allow-list offers only some of its tools (least privilege)
Every tool call is appended to the JSONL file in AGENT_TOOL_LOG, if set: tool, args, error, ms.
The tools, the guard, the warm-up and the question loop are shared with the other brains (agent_core.py);
this file only talks to the two OpenAI-style APIs.

Needs OPENAI_API_KEY, from the environment or from ~/.config/hallpanel/openai.env. Standard library only.

Command usage: openai_agent.py [--model gpt-6-luna] [--tier fast] [--effort none] [--mock-tools]
                               [--mcp URL --mcp-token-file F --mcp-tools a,b] [--price-in 0.20 --price-out 1.00]
               openai_agent.py --check   test the key, the model, the tier and the effort one by one
Error messages never contain the key: anything that looks like one is redacted.
"""
import argparse
import http.client
import json
import os
import sys
import urllib.error
import urllib.request

from agent_core import (AGENDA_DAYS, AGENDA_MAX_AGE, CALENDAR_TOOLS, GUARD_FILE, HOME_TOOL, MAX_STEPS, ROOT,  # noqa: F401
                        TV_TOOLS, Call, MCPClient, Round, ToolAgent, emit, redact, tools_from_env)
from env_files import load_key_file

API_HOST, API_PATH = "api.openai.com", "/v1/responses"
API = f"https://{API_HOST}{API_PATH}"
BRAINS = {
    "luna": {"api": "responses", "host": "api.openai.com", "path": "/v1/responses", "models": "/v1/models",
             "model": "gpt-6-luna", "tier": "fast", "effort": "none", "key": "OPENAI_API_KEY",
             # USD per million tokens, Fast mode, context up to 272K (OpenAI's pricing page, 2026-10-21):
             # input 0.20, cached input 0.02, 12-hour cache write 0.50, output 1.00. Standard is half.
             "key_file": "openai.env", "price_in": 0.20, "price_out": 1.00,
             # Prompt cache (GPT-5.6 and later): reads cost 0.1x the input price, 12-hour writes 2.5x.
             # 12 hours, because questions come a few times a day and a 30-minute cache would mostly
             # be written and never read.
             "cache": {"ttl": "12h", "read": 0.1, "write": 2.5}},
    "groq": {"api": "chat", "host": "api.groq.com", "path": "/openai/v1/chat/completions",
             "models": "/openai/v1/models", "model": "openai/gpt-oss-20b", "tier": None, "effort": "low",
             "key": "GROQ_API_KEY", "key_file": "groq.env", "price_in": 0.075, "price_out": 0.30,
             "prices": {"openai/gpt-oss-120b": (0.15, 0.60)},       # USD per million tokens, other models
             "cache": {"read": 0.5, "write": 1.0}},                 # automatic; gpt-oss-20b cached input $0.037
}



def request(method, url, key, body=None):
    """Plain JSON request; returns (status, parsed body or text). Used by --check."""
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body else None,
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, raw


def check(args, key):
    def show(label, status, body):
        err = body.get("error") if isinstance(body, dict) else body
        msg = (err.get("message") if isinstance(err, dict) else str(err)) if err else "ok"
        print(f"{label:<44} {status}  {redact(str(msg))[:200]}")
    show(f"key and model ({args.model})", *request("GET", f"https://{API_HOST}/v1/models/{args.model}", key))
    base = {"model": args.model, "input": "Svara bara: hej", "max_output_tokens": 16}
    show("plain request", *request("POST", API, key, base))
    if args.tier:
        show(f"service_tier={args.tier}", *request("POST", API, key, {**base, "service_tier": args.tier}))
    if args.effort:
        show(f"reasoning.effort={args.effort}", *request("POST", API, key, {**base, "reasoning": {"effort": args.effort}}))


class LunaAgent(ToolAgent):
    """The OpenAI brains: the Responses API (luna) and OpenAI-compatible Chat Completions (groq)."""

    def __init__(self, model=None, tier=None, effort=None, mock_tools=False, mcp_url=None,
                 mcp_token_file=None, mcp_tools=None, price_in=None, price_out=None, key=None, brain="luna"):
        """None takes the brain's value; an empty string leaves tier or effort out."""
        preset = BRAINS[brain]
        self.brain, self.api, self.host, self.path = brain, preset["api"], preset["host"], preset["path"]
        self.models_path = preset["models"]
        load_key_file(preset["key"], preset["key_file"])
        self.key = key or os.environ.get(preset["key"])
        if not self.key:
            raise RuntimeError(f"{preset['key']} is not set")
        self.model = model or preset["model"]
        self.tier = preset["tier"] if tier is None else (tier or None)
        self.effort = preset["effort"] if effort is None else (effort or None)
        default_in, default_out = preset.get("prices", {}).get(self.model, (preset["price_in"], preset["price_out"]))
        self.price_in = default_in if price_in is None else price_in
        self.price_out = default_out if price_out is None else price_out
        self.label = f"{brain} {self.model}"
        self.cache = preset.get("cache")
        self.conn = None
        self._init_tools(mock_tools, mcp_url, mcp_token_file, mcp_tools)

    # -- OpenAI connection ------------------------------------------------------------------
    def _open(self):
        if self.conn is None:
            self.conn = http.client.HTTPSConnection(self.host, timeout=90)
        return self.conn

    def _send(self, method, path, body=None, stream=False):
        """Send over the kept connection; reconnect once if the server had closed it."""
        headers = {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"}
        if stream:
            headers["Accept"] = "text/event-stream"
        data = json.dumps(body).encode() if body is not None else None
        for attempt in (1, 2):
            try:
                conn = self._open()
                conn.request(method, path, body=data, headers=headers)
                return conn.getresponse()
            except (http.client.HTTPException, ConnectionError, OSError):
                if self.conn:
                    self.conn.close()
                self.conn = None
                if attempt == 2:
                    raise

    def _stream(self, body):
        resp = self._send("POST", self.path, body, stream=True)
        if resp.status != 200:
            raise RuntimeError(redact(f"{self.brain} API {resp.status}: {resp.read().decode()[:400]}"))
        event = None
        for raw in resp:
            line = raw.decode("utf-8").rstrip("\n").rstrip("\r")
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: ") and line[6:] != "[DONE]":
                data = json.loads(line[6:])
                yield event or data.get("type"), data

    def _warm_api(self, instructions):
        """Open the HTTPS connection to the API with a cheap request."""
        resp = self._send("GET", f"{self.models_path}/{self.model}")
        resp.read()

    def _warm_api_independent(self, instructions, question_lock):
        import copy
        warmer = copy.copy(self)
        warmer.conn = None
        try:
            warmer._warm_api(instructions)
            # Never wait for an active answer or touch its transport.
            if question_lock.acquire(blocking=False):
                try:
                    old, self.conn = self.conn, warmer.conn
                    warmer.conn = old
                finally:
                    question_lock.release()
        finally:
            if warmer.conn is not None:
                warmer.conn.close()

    def ask(self, prompt, on_text=None, instructions=None, on_tools=None):
        """The shared loop; the Responses API is asked once more without the 12-hour prompt cache if it
        refuses the cache options."""
        try:
            return super().ask(prompt, on_text, instructions, on_tools)
        except RuntimeError as e:
            if self.api == "responses" and self.cache and self.cache.get("ttl") and "prompt_cache" in str(e):
                print(f"prompt cache refused, asking without it: {e}", file=sys.stderr)
                self.cache = {**self.cache, "ttl": None}
                return super().ask(prompt, on_text, instructions, on_tools)
            raise

    def _cost(self, tokens_in, cached, written, tokens_out):
        read, write = (self.cache or {}).get("read", 1.0), (self.cache or {}).get("write", 1.0)
        plain = max(tokens_in - cached - written, 0)
        return (plain + cached * read + written * write) / 1e6 * self.price_in + tokens_out / 1e6 * self.price_out

    def _begin(self, prompt, instructions):
        return self._begin_chat(prompt, instructions) if self.api == "chat" else self._begin_responses(prompt, instructions)

    def _round(self, convo, on_text):
        return self._round_chat(convo, on_text) if self.api == "chat" else self._round_responses(convo, on_text)

    def _add_results(self, convo, r, results):
        if self.api == "chat":
            convo["messages"].append({"role": "assistant", "content": r.text or None, "tool_calls": [
                {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": r.raw[c.id] or "{}"}}
                for c in r.calls]})
            convo["messages"] += [{"role": "tool", "tool_call_id": c.id, "content": out[1]}
                                  for c, out in zip(r.calls, results)]
        else:
            convo["body"]["input"] = [{"type": "function_call_output", "call_id": c.id, "output": out[1]}
                                      for c, out in zip(r.calls, results)]

    # -- the Responses API (luna) ------------------------------------------------------------------
    def _begin_responses(self, prompt, instructions):
        body = {"model": self.model, "input": prompt, "stream": True}
        if instructions:
            fixed = {"type": "input_text", "text": instructions}
            if self.cache and self.cache.get("ttl"):
                fixed["prompt_cache_breakpoint"] = {"mode": "explicit"}
                body["prompt_cache_options"] = {"mode": "explicit", "ttl": self.cache["ttl"]}
            body["input"] = [{"role": "developer", "content": [fixed]}, {"role": "user", "content": prompt}]
        if self.tools:
            body["tools"] = self.tools
        if self.tier:
            body["service_tier"] = self.tier
        if self.effort:
            body["reasoning"] = {"effort": self.effort}
        return {"body": body}

    def _round_responses(self, convo, on_text):
        text, calls, usage = [], [], {}
        body = {**convo["body"], "tool_choice": "none"} if convo.get("no_tools") and convo["body"].get("tools") \
            else convo["body"]
        for event, data in self._stream(body):
            if event == "response.output_item.added" and data.get("item", {}).get("type") == "message" and text:
                text.append(" ")                    # a second message in one answer: not glued to the first
                if on_text:
                    on_text(" ")
            elif event == "response.output_text.delta":
                text.append(data["delta"])
                if on_text:
                    on_text(data["delta"])
            elif event == "response.output_item.done" and data.get("item", {}).get("type") == "function_call":
                item = data["item"]
                try:
                    args, problem = json.loads(item.get("arguments") or "{}"), None
                except ValueError:
                    args, problem = None, f"Ogiltiga argument till {item['name']}: {item.get('arguments', '')[:200]}"
                calls.append(Call(item["call_id"], item["name"], args, problem))
                if problem is None:
                    self._ready(calls[-1])          # a lookup starts while the model writes the rest
            elif event == "response.completed":
                u = data["response"].get("usage") or {}
                details = u.get("input_tokens_details") or {}
                usage = {"input_tokens": u.get("input_tokens", 0), "output_tokens": u.get("output_tokens", 0),
                         "cached_tokens": details.get("cached_tokens") or 0,
                         "cache_write_tokens": details.get("cache_write_tokens") or 0}
                convo["body"] = {**convo["body"], "previous_response_id": data["response"]["id"]}
            elif event in ("response.failed", "error"):
                raise RuntimeError(redact(json.dumps(data)[:300]))
        cost = self._cost(usage.get("input_tokens", 0), usage.get("cached_tokens", 0),
                          usage.get("cache_write_tokens", 0), usage.get("output_tokens", 0))
        return Round("".join(text), calls, cost, usage)

    # -- Chat Completions (groq) -------------------------------------------------------------------
    def _begin_chat(self, prompt, instructions):
        """Stateless: the whole conversation, tool calls and results included, is sent again each round."""
        messages = ([{"role": "system", "content": instructions}] if instructions else []) + [
            {"role": "user", "content": prompt}]
        body = {"model": self.model, "messages": messages, "stream": True}
        if self.tools:
            body["tools"] = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                               "parameters": t["parameters"]}} for t in self.tools]
        if self.tier:
            body["service_tier"] = self.tier
        if self.effort:
            body["reasoning_effort"] = self.effort
            body["include_reasoning"] = False          # only the answer comes back, not the reasoning
        return {"body": body, "messages": messages}

    def _round_chat(self, convo, on_text):
        text, calls, usage = [], {}, {}
        body = {**convo["body"], "tool_choice": "none"} if convo.get("no_tools") and convo["body"].get("tools") \
            else convo["body"]
        for _event, data in self._stream(body):
            u = data.get("usage") or (data.get("x_groq") or {}).get("usage")
            if u:
                usage = {"input_tokens": u.get("prompt_tokens", 0), "output_tokens": u.get("completion_tokens", 0),
                         "cached_tokens": (u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0}
            for choice in data.get("choices") or []:
                delta = choice.get("delta") or {}
                if delta.get("content"):
                    text.append(delta["content"])
                    if on_text:
                        on_text(delta["content"])
                for tc in delta.get("tool_calls") or []:
                    call = calls.setdefault(tc.get("index", 0), {"id": "", "name": "", "arguments": ""})
                    fn = tc.get("function") or {}
                    call["id"] = tc.get("id") or call["id"]
                    call["name"] += fn.get("name") or ""
                    call["arguments"] += fn.get("arguments") or ""
        parsed = []
        for c in calls.values():
            try:
                args = json.loads(c["arguments"] or "{}")
            except ValueError:
                args = None
            problem = None if isinstance(args, dict) else f"Ogiltiga argument till {c['name']}: {c['arguments'][:200]}"
            parsed.append(Call(c["id"], c["name"], args if problem is None else None, problem))
        cost = self._cost(usage.get("input_tokens", 0), usage.get("cached_tokens", 0), 0, usage.get("output_tokens", 0))
        return Round("".join(text), parsed, cost, usage, raw={c["id"]: c["arguments"] for c in calls.values()})


def agent_from_env():
    """The in-process agent the server builds when JARVIS_AGENT=inprocess. JARVIS_BRAIN picks the brain
    (luna, groq); JARVIS_BRAIN_MODEL, JARVIS_BRAIN_TIER and JARVIS_BRAIN_EFFORT override its model, tier and effort."""
    return LunaAgent(brain=os.environ.get("JARVIS_BRAIN", "luna"), model=os.environ.get("JARVIS_BRAIN_MODEL") or None,
                     tier=os.environ.get("JARVIS_BRAIN_TIER"), effort=os.environ.get("JARVIS_BRAIN_EFFORT") or None,
                     **tools_from_env())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--brain", default="luna", choices=sorted(BRAINS))
    ap.add_argument("--model", help="default: the brain's model")
    ap.add_argument("--tier", help="service_tier; empty for the default tier (default: the brain's)")
    ap.add_argument("--effort", help="reasoning effort; empty to leave it out (default: the brain's)")
    ap.add_argument("--mock-tools", action="store_true")
    ap.add_argument("--mcp", help="MCP server URL (streamable HTTP)")
    ap.add_argument("--mcp-token-file", help="file holding the MCP server's bearer token")
    ap.add_argument("--mcp-tools", help=f"comma-separated MCP tools to offer (default {CALENDAR_TOOLS})")
    ap.add_argument("--price-in", type=float)
    ap.add_argument("--price-out", type=float)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    preset = BRAINS[args.brain]
    load_key_file(preset["key"], preset["key_file"])
    if not os.environ.get(preset["key"]):
        sys.exit(f"{preset['key']} is not set")
    if args.check:
        if args.brain != "luna":
            sys.exit("--check tests the OpenAI key only")
        args.model, args.tier = args.model or preset["model"], preset["tier"] if args.tier is None else args.tier
        args.effort = preset["effort"] if args.effort is None else args.effort
        return check(args, os.environ["OPENAI_API_KEY"])
    try:
        agent = LunaAgent(args.model, args.tier, args.effort, args.mock_tools, args.mcp, args.mcp_token_file,
                          args.mcp_tools, args.price_in, args.price_out, brain=args.brain)
        reply, cost, usage = agent.ask(sys.stdin.read(), on_text=lambda d: emit(
            {"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": d}}}))
    except (RuntimeError, OSError, http.client.HTTPException, urllib.error.URLError) as e:
        sys.exit(redact(f"{args.brain} agent failed: {e}"))
    emit({"type": "result", "result": reply, "is_error": not reply, "total_cost_usd": cost, "usage": usage})


if __name__ == "__main__":
    main()
