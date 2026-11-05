#!/usr/bin/env python3
"""Jarvis agent on Anthropic's Messages API: the brain "anthropic", default model claude-opus-5-5 at
effort low (the model always thinks; effort sets how much, and so the wait).

The tools, the guard, the warm-up and the question loop are shared with the other brains (agent_core.py);
this file only talks to Anthropic's Messages API. Unlike the rest
of the server it needs a package: the official SDK, `anthropic` (server/requirements.txt). On Jarvis
tools/deploy_jarvis.sh installs it in ~/hallpanel-live/venv; the other brains do not use it.

Prompt cache: the tools and the fixed instructions (the system prompt) are the cached prefix, with one
breakpoint on the system block and the 5-minute lifetime. The question's first request writes it (or reads
it within 5 minutes of the last), and the rounds after it read it. warm(), which the panel triggers on the
wake word, only opens the connection (a free model lookup): until 2026-11-03 it sent the prefix with
max_tokens 0 to write the cache while Joel was still talking, but wakes come more than 5 minutes apart and
many never reach this brain (Home Assistant answers, no speech), so nearly every wake paid a full write
(24 763 tokens, $0.062 on Sonnet 5.5) and the warm-ups cost 2-11 times the answers (journal 1-3 Nov).
JARVIS_ANTHROPIC_WARM_CACHE=1 in server.env brings the cache-writing warm-up back. Questions come hours
apart, so a longer lifetime would mostly be paid for (a 1-hour write costs 2x the input price, a 5-minute
one 1.25x) and not read. Everything that changes (time, agenda, today's talk, the question) comes after
the breakpoint.

Refusals: on the models that support it the request carries `fallbacks: "default"`, so if Anthropic's
safety classifiers decline, the API answers with its recommended fallback model in the same call.

Key ANTHROPIC_API_KEY, from the environment or ~/.config/hallpanel/anthropic.env. Settings in the server:
JARVIS_BRAIN=anthropic, JARVIS_BRAIN_MODEL (default claude-opus-5-5), JARVIS_BRAIN_EFFORT (default low).

Command usage (same stream-json output as openai_agent.py):
  anthropic_agent.py [--model claude-opus-5-5] [--effort low] [--mock-tools]
                     [--mcp URL --mcp-token-file F --mcp-tools a,b] < prompt
"""
import argparse
import json
import os
import sys
import time

import anthropic
import httpx2

from agent_core import CALENDAR_TOOLS, Call, Round, ToolAgent, emit, redact, tools_from_env
from env_files import load_key_file

MODEL, EFFORT, MAX_TOKENS = "claude-opus-5-5", "low", 16000      # thinking counts toward max_tokens
# The warm-up's own limit: a hung cache write (a question's limit is 60 s) must not hold the next wake word's
# warm-up off, since only one runs at a time. Measured 0.9-3.4 s on Jarvis.
WARM_TIMEOUT = 15.0
CACHE = {"type": "ephemeral"}                                    # 5 minutes; warm() refreshes it
CACHE_WRITE = 1.25                                               # a 5-minute write costs 1.25x the input price
# USD per million tokens: input, output, and a cache read as a share of the input price
# (Anthropic's prices as of 2026-10). Haiku 5.5's price holds for prompts up to 100K tokens.
PRICES = {
    "claude-opus-5-5": (4.00, 20.00, 0.05),
    "claude-sonnet-5-5": (2.00, 10.00, 0.10),
    "claude-haiku-5-5": (0.10, 0.50, 0.10),
    "claude-fable-5-1": (10.00, 50.00, 0.025),
    "claude-opus-5": (5.00, 25.00, 0.10),                        # possible fallback models
    "claude-opus-4-8": (5.00, 25.00, 0.10),
}
# Models that take the server-side refusal fallback. Haiku 5.5 has none.
FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5", "claude-fable-5-1"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Anthropic's own web search and page reading, run on their side inside the same request (no round of ours), in
# place of the Jina tool webbsok, with JARVIS_ANTHROPIC_WEB=1. Off by default since 2026-10-23: on a cinema question
# it cost 2.4 times as much, found no more than Jina and Sonnet left out the sentence before its tools (21 s of
# silence). $10 per 1 000 searches; the result pages count as input tokens.
WEB_TOOLS = [{"type": "web_search_20260209", "name": "web_search", "max_uses": 3,
              "user_location": {"type": "approximate", "city": "Stockholm", "region": "Stockholm", "country": "SE",
                                "timezone": "Europe/Stockholm"}},
             {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 2}]
SEARCH_PRICE = 0.01                                              # USD per web search
MAX_CONTINUATIONS = 3                                            # resumes of a turn a long search paused
JSON_TYPES = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "object": dict, "array": list}


def input_problem(schema, args):
    """Why a tool input does not fit its JSON schema (required keys, top-level types), or None.
    Tool inputs stream as they are written (eager_input_streaming), so the API no longer checks them."""
    if not isinstance(args, dict):
        return "the input is not a JSON object"
    for key in (schema or {}).get("required", []):
        if key not in args:
            return f"{key} is missing"
    for key, value in args.items():
        kind = ((schema or {}).get("properties") or {}).get(key, {}).get("type")
        kinds = kind if isinstance(kind, list) else [kind] if kind else []
        if not kinds or (value is None and "null" in kinds):
            continue
        if not any(k in JSON_TYPES and isinstance(value, JSON_TYPES[k])
                   and not (k in ("integer", "number") and isinstance(value, bool)) for k in kinds):
            return f"{key} should be {' or '.join(kinds)}"
    return None


def echo(content):
    """The assistant turn to send back. After a mid-answer fallback, the blocks before the last
    fallback marker other than text (thinking, tool calls) belong to the model that declined and must
    not go back; the marker itself is dropped."""
    last = max((i for i, b in enumerate(content) if b.type == "fallback"), default=-1)
    return [b for i, b in enumerate(content) if b.type != "fallback" and (i > last or b.type == "text")]


class ClaudeAgent(ToolAgent):
    def __init__(self, model=None, effort=None, mock_tools=False, mcp_url=None, mcp_token_file=None,
                 mcp_tools=None, key=None):
        load_key_file("ANTHROPIC_API_KEY", "anthropic.env")
        self.brain, self.model, self.effort = "anthropic", model or MODEL, effort or EFFORT
        self.label = f"anthropic {self.model}"
        # Keep the connection open for two minutes (the SDK's default is 5 s), so the question after
        # the wake word's warm-up reuses it. One retry: a voice answer should not wait for three.
        self.client = anthropic.Anthropic(
            api_key=key or None, max_retries=1, timeout=anthropic.Timeout(60.0, connect=5.0),
            http_client=anthropic.DefaultHttpxClient(
                limits=httpx2.Limits(max_connections=10, max_keepalive_connections=2, keepalive_expiry=120)))
        self.claude_tools = None
        self.last_warm = None
        self.web = os.environ.get("JARVIS_ANTHROPIC_WEB") == "1"
        self.warm_cache = os.environ.get("JARVIS_ANTHROPIC_WARM_CACHE") == "1"
        self._init_tools(mock_tools, mcp_url, mcp_token_file, mcp_tools)
        self.early |= {t["name"] for t in WEB_TOOLS}    # lookups too: a fallback brain may follow them

    def _params(self, instructions):
        """Everything but the messages and max_tokens; the same bytes for warm() and every question,
        since any difference in tools, system, thinking or effort would miss the cache."""
        self._ensure_tools()
        if self.claude_tools is None:
            self.claude_tools = [{"name": t["name"], "input_schema": t["parameters"], "eager_input_streaming": True,
                                  **({"description": t["description"]} if t.get("description") else {})}
                                 for t in self.tools if not (self.web and t["name"] == "webbsok")]
            if self.web:
                self.claude_tools += WEB_TOOLS
        params = {"model": self.model, "output_config": {"effort": self.effort}}
        if self.claude_tools:
            params["tools"] = self.claude_tools
        if instructions:
            params["system"] = [{"type": "text", "text": instructions, "cache_control": CACHE}]
        if self.model in FALLBACK_MODELS:
            params.update(betas=[FALLBACK_BETA], fallbacks="default")
        return params

    def _warm_api(self, instructions, timeout=None):
        """Open the connection with a free model lookup, so the question's first request skips the TLS setup.
        With warm_cache (JARVIS_ANTHROPIC_WARM_CACHE=1) and instructions it instead writes or refreshes the
        prompt cache: the real prefix with max_tokens 0 (no answer, no output billed, the input is).
        timeout, in seconds, replaces the client's for this one request."""
        t0 = time.monotonic()
        client = self.client.with_options(timeout=anthropic.Timeout(timeout, connect=5.0)) if timeout else self.client
        try:
            if not (instructions and self.warm_cache):
                client.models.retrieve(self.model)
                self.last_warm = {"connection_only": True, "s": round(time.monotonic() - t0, 2), "cost_usd": 0.0}
                print(f"anthropic warm-up: {self.last_warm}", file=sys.stderr, flush=True)
                return
            msg = client.beta.messages.create(**self._params(instructions), max_tokens=0,
                                              messages=[{"role": "user", "content": "warmup"}])
        except anthropic.APIError as e:
            raise RuntimeError(redact(f"anthropic warm-up: {e}")) from e
        u = msg.usage
        self.last_warm = {"cache_read": u.cache_read_input_tokens or 0, "cache_write": u.cache_creation_input_tokens or 0,
                          "input": u.input_tokens, "s": round(time.monotonic() - t0, 2), "cost_usd": round(self._cost(msg), 6)}
        print(f"anthropic warm-up: {self.last_warm}", file=sys.stderr, flush=True)

    def _warm_api_independent(self, instructions, question_lock):
        """The background warm-up runs on the question's own client: httpx's pool is thread-safe, so a question
        that arrives mid-warm takes a second connection, nothing is swapped or closed under an active answer, and
        the connection the warm-up opened serves the question's next request (until 2026-10-25 the warm-up used
        a client of its own and handed it over only if no question held the lock, so a question that came
        mid-warm opened its own connection and the warm one was closed)."""
        self._warm_api(instructions, timeout=WARM_TIMEOUT)

    def _cost(self, msg):
        """Estimated USD for one response. usage.iterations lists every attempt when a fallback ran,
        each billed at its own model's price; otherwise the top-level usage is the whole request."""
        total = 0.0
        for part in msg.usage.iterations or [msg.usage]:
            model = getattr(part, "model", None) or msg.model or self.model
            p_in, p_out, read = PRICES.get(model, PRICES.get(self.model, (0.0, 0.0, 0.0)))
            total += (part.input_tokens * p_in + (part.cache_creation_input_tokens or 0) * p_in * CACHE_WRITE
                      + (part.cache_read_input_tokens or 0) * p_in * read + part.output_tokens * p_out) / 1e6
        searches = getattr(getattr(msg.usage, "server_tool_use", None), "web_search_requests", 0) or 0
        return total + searches * SEARCH_PRICE

    def _stream(self, params, messages, on_text, schemas=None):
        """One request, streamed. Returns the final message. Each tool call is handed to _ready the moment
        its block is complete, so a lookup can start while the model writes the rest. JSON the SDK cannot
        parse at all (from a streamed tool input) comes up as ValueError; that turn is asked once more."""
        for attempt in (1, 2):
            try:
                with self.client.beta.messages.stream(**params, max_tokens=MAX_TOKENS, messages=messages) as stream:
                    for event in stream:
                        if event.type == "text" and on_text:
                            on_text(event.text)
                        elif event.type == "content_block_stop" and getattr(event.content_block, "type", "") == "tool_use":
                            b = event.content_block
                            if input_problem((schemas or {}).get(b.name), b.input) is None:
                                self._ready(Call(b.id, b.name, b.input))
                    return stream.get_final_message()
            except ValueError:
                if attempt == 2:
                    raise RuntimeError("anthropic: a tool input was not valid JSON twice")
            except anthropic.AuthenticationError as e:
                raise RuntimeError("anthropic: the API key was refused (ANTHROPIC_API_KEY)") from e
            except anthropic.RateLimitError as e:
                raise RuntimeError(redact(f"anthropic: rate limited: {e}")) from e
            except anthropic.APIStatusError as e:
                raise RuntimeError(redact(f"anthropic API {e.status_code}: {e}")[:400]) from e
            except anthropic.APIConnectionError as e:
                raise RuntimeError(f"anthropic: no connection: {e}") from e

    def _begin(self, prompt, instructions):
        return {"params": self._params(instructions), "messages": [{"role": "user", "content": prompt}],
                "schemas": {t["name"]: t["input_schema"] for t in self.claude_tools if "input_schema" in t}}

    def _round(self, convo, on_text):
        params = {**convo["params"], "tool_choice": {"type": "none"}} if convo.get("no_tools") else convo["params"]
        msgs = [self._stream(params, convo["messages"], on_text, convo["schemas"])]
        while msgs[-1].stop_reason == "pause_turn" and len(msgs) <= MAX_CONTINUATIONS:
            # A long web search paused the turn on Anthropic's side: hand it back as it is and it resumes there.
            convo["messages"].append({"role": "assistant", "content": echo(msgs[-1].content)})
            msgs.append(self._stream(params, convo["messages"], on_text, convo["schemas"]))
        msg = msgs[-1]
        usage = {"input_tokens": 0, "output_tokens": 0, "cached_tokens": 0, "cache_write_tokens": 0}
        for m in msgs:
            u = m.usage
            read, written = u.cache_read_input_tokens or 0, u.cache_creation_input_tokens or 0
            usage["input_tokens"] += u.input_tokens + read + written
            usage["output_tokens"] += u.output_tokens
            usage["cached_tokens"] += read
            usage["cache_write_tokens"] += written
            # Anthropic's searches never reach run_tool: note them for the history all the same
            self.last_tools += [{"name": b.name, "ms": 0, "error": False, "round": self._step, "server": True}
                                for b in m.content if b.type == "server_tool_use"]
        if msg.model != self.model:
            print(f"anthropic: {self.model} declined, {msg.model} answered", file=sys.stderr, flush=True)
        if msg.stop_reason == "refusal":
            raise RuntimeError(f"anthropic: refused ({getattr(msg.stop_details, 'category', None)})")
        uses = [b for b in msg.content if b.type == "tool_use"]
        if uses and msg.stop_reason == "max_tokens":    # a cut-off tool input still parses; never run it
            raise RuntimeError("anthropic: a tool call was cut off at max_tokens")
        calls = []
        for b in uses:
            problem = input_problem(convo["schemas"].get(b.name), b.input)
            calls.append(Call(b.id, b.name, b.input if problem is None else None, problem and json.dumps(
                {"INVALID_INPUT": problem, "input": b.input}, ensure_ascii=False, default=str)))
        text = "".join(b.text for m in msgs for b in m.content if b.type == "text")
        return Round(text, calls, sum(self._cost(m) for m in msgs), usage, raw=msg)

    def _add_results(self, convo, r, results):
        convo["messages"] += [
            {"role": "assistant", "content": echo(r.raw.content)},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": c.id, "content": out or "(tomt)",
                                          **({"is_error": True} if is_error else {})}
                                         for c, (is_error, out) in zip(r.calls, results)]}]


def agent_from_env():
    """The in-process agent the server builds when JARVIS_AGENT=inprocess and JARVIS_BRAIN=anthropic."""
    return ClaudeAgent(model=os.environ.get("JARVIS_BRAIN_MODEL") or None,
                       effort=os.environ.get("JARVIS_BRAIN_EFFORT") or None, **tools_from_env())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", help=f"default {MODEL}")
    ap.add_argument("--effort", help=f"low, medium, high, xhigh or max (default {EFFORT})")
    ap.add_argument("--mock-tools", action="store_true")
    ap.add_argument("--mcp", help="MCP server URL (streamable HTTP)")
    ap.add_argument("--mcp-token-file", help="file holding the MCP server's bearer token")
    ap.add_argument("--mcp-tools", help=f"comma-separated MCP tools to offer (default {CALENDAR_TOOLS})")
    args = ap.parse_args()
    try:
        agent = ClaudeAgent(args.model, args.effort, args.mock_tools, args.mcp, args.mcp_token_file, args.mcp_tools)
        reply, cost, usage = agent.ask(sys.stdin.read(), on_text=lambda d: emit(
            {"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": d}}}))
    except (RuntimeError, OSError, anthropic.AnthropicError) as e:
        sys.exit(redact(f"anthropic agent failed: {e}"))
    emit({"type": "result", "result": reply, "is_error": not reply, "total_cost_usd": cost, "usage": usage})


if __name__ == "__main__":
    main()
