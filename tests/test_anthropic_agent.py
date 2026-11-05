#!/usr/bin/env python3
"""The anthropic brain's loop without the network: a fake client stands in for the SDK's.

  uv run --with-requirements server/requirements.txt python -m unittest tests/test_anthropic_agent.py

Checks that a tool call runs the mock tool and sends its result back, that the warm-up and the
question send the same cached prefix (tools, system with its breakpoint, effort), that the cost and
token counts add up over the steps, and that refusals, cut-off tool calls and bad tool inputs never
run a tool. Skipped when the anthropic package is not installed.
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server" / "agents"))
try:
    import anthropic_agent
except ImportError:
    anthropic_agent = None


def usage(inp=10, out=5, read=0, write=0, iterations=None):
    return NS(input_tokens=inp, output_tokens=out, cache_read_input_tokens=read, cache_creation_input_tokens=write,
              iterations=iterations)


def message(content, stop="end_turn", model="claude-opus-5-5", **u):
    return NS(content=content, stop_reason=stop, model=model, usage=usage(**u), stop_details=None)


class FakeStream:
    def __init__(self, msg):
        self.msg = msg

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        """Text as it streams, and each tool call's block when it is complete, as the SDK does."""
        return iter([NS(type="text", text=b.text) if b.type == "text" else NS(type="content_block_stop", content_block=b)
                     for b in self.msg.content if b.type in ("text", "tool_use")])

    def get_final_message(self):
        return self.msg


class FakeMessages:
    def __init__(self, replies):
        self.replies, self.calls, self.created = list(replies), [], []

    def stream(self, **params):
        self.calls.append(json.loads(json.dumps(params, default=lambda o: o.__dict__)))
        return FakeStream(self.replies.pop(0))

    def create(self, **params):
        self.created.append(json.loads(json.dumps(params, default=lambda o: o.__dict__)))
        return message([], stop="max_tokens", out=0, write=1500)


@unittest.skipUnless(anthropic_agent, "the anthropic package is not installed")
class AnthropicAgentTest(unittest.TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp(prefix="hallpanel-anthropic-"))
        os.environ.update({"MOCK_LOG": str(tmp / "calls.jsonl"), "MOCK_MODE_FILE": str(tmp / "mode"),
                           "MOCK_STATE": str(tmp / "state.json")})
        self.agent = anthropic_agent.ClaudeAgent(key="test", mock_tools=True)

    def use(self, *replies):
        self.agent.client = NS(beta=NS(messages=FakeMessages(replies)))
        return self.agent.client.beta.messages

    def test_tool_call_then_answer(self):
        api = self.use(
            message([NS(type="thinking", thinking=""), NS(type="tool_use", id="t1", name="tv_power", input={"on": False})],
                    stop="tool_use", inp=100, write=1400, out=30),
            message([NS(type="text", text="Klart, tv:n är av.")], inp=60, read=1400, out=12))
        said, ready = [], []
        self.agent._ready = ready.append
        reply, cost, used = self.agent.ask("Stäng av tv:n", on_text=said.append, instructions="FAST")
        self.assertEqual([c.name for c in ready], ["tv_power"])        # handed on as soon as its block was done
        self.assertEqual(reply, "Klart, tv:n är av.")
        self.assertEqual(said, ["Jag tittar på det.", "\n", "Klart, tv:n är av."])   # TOOL_NOTICES, then the answer
        self.assertEqual([t["name"] for t in self.agent.last_tools], ["tv_power"])
        second = api.calls[1]["messages"]
        self.assertEqual([m["role"] for m in second], ["user", "assistant", "user"])
        self.assertEqual(second[2]["content"][0]["tool_use_id"], "t1")
        self.assertNotIn("is_error", second[2]["content"][0])
        self.assertEqual(used, {"input_tokens": 100 + 1400 + 60 + 1400, "output_tokens": 42,
                                "cached_tokens": 1400, "cache_write_tokens": 1400, "steps": 2})
        expected = ((100 + 1400 * 1.25 + 60 + 1400 * 0.05) * 4.00 + 42 * 20.00) / 1e6
        self.assertAlmostEqual(cost, round(expected, 6))

    def test_last_round_asks_without_tools(self):
        api = self.use(message([NS(type="text", text="Svar.")]))
        convo = self.agent._begin("Hej", "FAST")
        convo["no_tools"] = True
        self.agent._round(convo, None)
        self.assertEqual(api.calls[-1]["tool_choice"], {"type": "none"})
        self.assertNotIn("tool_choice", convo["params"])                 # only that request

    def test_the_default_warm_up_only_opens_the_connection(self):
        # 2026-11-03: the cache-writing warm-up paid a full write on nearly every wake (wakes >5 min apart, many
        # answered by HA), $0.062 each on Sonnet 5.5 and 2-11x the answers' cost. Now the question writes it.
        api = self.use(message([NS(type="text", text="Hej.")], write=1500))
        retrieve = mock.Mock()
        self.agent.client.models = NS(retrieve=retrieve)
        self.agent._warm_api("FAST")
        retrieve.assert_called_once_with(self.agent.model)
        self.assertEqual(api.created, [])                                        # no messages request, no write
        self.assertEqual((self.agent.last_warm["connection_only"], self.agent.last_warm["cost_usd"]), (True, 0.0))
        self.agent.ask("Hej", instructions="FAST")
        self.assertEqual(api.calls[0]["system"][0]["cache_control"], {"type": "ephemeral"})  # the question writes it

    def test_warm_up_and_question_share_the_cached_prefix(self):
        api = self.use(message([NS(type="text", text="Hej.")]))
        self.agent.warm_cache = True
        self.agent._warm_api("FAST")
        self.agent.ask("Hej", instructions="FAST")
        warm, ask = api.created[0], api.calls[0]
        self.assertEqual(warm["max_tokens"], 0)
        for key in ("model", "tools", "system", "output_config", "betas", "fallbacks"):
            self.assertEqual(warm[key], ask[key], key)
        self.assertEqual(ask["system"], [{"type": "text", "text": "FAST", "cache_control": {"type": "ephemeral"}}])
        self.assertEqual(ask["output_config"], {"effort": "low"})
        self.assertEqual(ask["fallbacks"], "default")
        self.assertTrue(all(t["eager_input_streaming"] for t in ask["tools"]))
        self.assertFalse([t for t in ask["tools"] if "type" in t])               # Jina's search by default
        self.assertEqual(self.agent.last_warm["cache_write"], 1500)

    def test_anthropics_web_search_when_turned_on(self):
        with mock.patch.dict(os.environ, {"JARVIS_ANTHROPIC_WEB": "1"}):
            agent = anthropic_agent.ClaudeAgent(key="test", mock_tools=True)
        tools = agent._params("FAST")["tools"]
        self.assertEqual([t["type"] for t in tools if "type" in t], ["web_search_20260209", "web_fetch_20260209"])
        self.assertNotIn("webbsok", [t["name"] for t in tools])
        self.assertTrue(all(t["eager_input_streaming"] for t in tools if "input_schema" in t))

    def test_a_paused_search_resumes_and_is_billed(self):
        search = NS(type="server_tool_use", id="s1", name="web_search", input={"query": "Rigoletto"})
        first = message([NS(type="text", text="Jag "), search], stop="pause_turn", inp=100, out=10)
        first.usage.server_tool_use = NS(web_search_requests=2)
        api = self.use(first, message([NS(type="text", text="hittade det.")], inp=50, out=5))
        reply, cost, used = self.agent.ask("Vad går på Rigoletto?", instructions="FAST")
        self.assertEqual(reply, "Jag hittade det.")
        self.assertEqual(api.calls[1]["messages"][-1]["role"], "assistant")      # the paused turn, handed back
        self.assertEqual(used["steps"], 1)
        self.assertEqual([(t["name"], t.get("server")) for t in self.agent.last_tools], [("web_search", True)])
        expected = ((100 + 50) * 4.00 + 15 * 20.00) / 1e6 + 2 * anthropic_agent.SEARCH_PRICE
        self.assertAlmostEqual(cost, round(expected, 6))

    def test_the_background_warm_up_shares_the_questions_client_with_a_short_timeout(self):
        import threading
        messages = FakeMessages([])
        warm = NS(beta=NS(messages=messages), models=NS(retrieve=mock.Mock()), close=mock.Mock())
        options = []
        self.agent.client = NS(with_options=lambda **kw: options.append(kw) or warm, close=mock.Mock(),
                               beta=NS(messages=FakeMessages([])))
        question = self.agent.client
        self.agent.warm_cache = True
        lock = threading.Lock()
        with lock:                                   # a question holds the lock: the warm-up still runs beside it
            self.agent._warm_api_independent("FAST", lock)
        self.assertIs(self.agent.client, question)   # nothing swapped
        question.close.assert_not_called()
        warm.close.assert_not_called()               # and nothing closed: the pool keeps the connection
        self.assertEqual(options[0]["timeout"].read, anthropic_agent.WARM_TIMEOUT)
        self.assertEqual(options[0]["timeout"].connect, 5.0)
        self.assertEqual((messages.created[0]["max_tokens"], messages.created[0]["system"][0]["cache_control"]),
                         (0, {"type": "ephemeral"}))
        self.assertEqual(self.agent.last_warm["cache_write"], 1500)

    def test_haiku_has_no_fallback(self):
        self.agent.model = "claude-haiku-5-5"
        api = self.use(message([NS(type="text", text="Hej.")], model="claude-haiku-5-5"))
        self.agent.ask("Hej", instructions="FAST")
        self.assertNotIn("fallbacks", api.calls[0])
        self.assertNotIn("betas", api.calls[0])

    def test_refusal_and_cut_off_tool_call_run_nothing(self):
        call = NS(type="tool_use", id="t1", name="tv_power", input={"on": False})
        self.use(message([call], stop="refusal"))
        with self.assertRaises(RuntimeError):
            self.agent.ask("Stäng av tv:n", instructions="FAST")
        self.use(message([call], stop="max_tokens"))
        with self.assertRaises(RuntimeError):
            self.agent.ask("Stäng av tv:n", instructions="FAST")
        self.assertEqual(self.agent.last_tools, [])

    def test_bad_tool_input_goes_back_as_an_error(self):
        api = self.use(
            message([NS(type="tool_use", id="t1", name="tv_power", input={"on": "nej"})], stop="tool_use"),
            message([NS(type="text", text="Det gick inte.")]))
        self.agent.ask("Stäng av tv:n", instructions="FAST")
        result = api.calls[1]["messages"][2]["content"][0]
        self.assertTrue(result["is_error"])
        self.assertIn("INVALID_INPUT", result["content"])
        self.assertEqual(self.agent.last_tools, [])

    def test_fallback_cost_and_echo(self):
        parts = [NS(type="message", model="claude-opus-5-5", input_tokens=50, output_tokens=0,
                    cache_read_input_tokens=0, cache_creation_input_tokens=0),
                 NS(type="fallback_message", model="claude-opus-4-8", input_tokens=50, output_tokens=10,
                    cache_read_input_tokens=0, cache_creation_input_tokens=0)]
        msg = message([NS(type="text", text="Del"), NS(type="thinking", thinking=""),
                       NS(type="fallback"), NS(type="text", text="resten")], model="claude-opus-4-8")
        msg.usage = usage(iterations=parts)
        self.assertAlmostEqual(self.agent._cost(msg), (50 * 4.00 + 50 * 5.00 + 10 * 25.00) / 1e6)
        self.assertEqual([b.type for b in anthropic_agent.echo(msg.content)], ["text", "text"])

    def test_input_problem(self):
        schema = {"type": "object", "properties": {"title": {"type": "string"}, "n": {"type": "integer"}},
                  "required": ["title"]}
        self.assertIsNone(anthropic_agent.input_problem(schema, {"title": "x", "n": 2}))
        self.assertIn("title", anthropic_agent.input_problem(schema, {"n": 2}))
        self.assertIn("n", anthropic_agent.input_problem(schema, {"title": "x", "n": True}))
        self.assertIsNotNone(anthropic_agent.input_problem(schema, ["x"]))


if __name__ == "__main__":
    unittest.main()
