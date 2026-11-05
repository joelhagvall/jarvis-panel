#!/usr/bin/env python3
"""Paid model evals through the real server with isolated history and synthetic tools.

Free preview:
  python tests/integration_test.py --list
Explicitly bounded paid run (14 scenarios, 15 question turns):
  python tests/integration_test.py --live --max-turns 15 --agent openai
  python tests/integration_test.py --live --max-turns 15 --agent anthropic --model <model>
  python tests/integration_test.py --live --max-turns 2 --cases calendar-followup
Your own questions, same isolation (preview with --list first; see docs/evals.md):
  python tests/integration_test.py --live --max-turns 4 --agent anthropic --model claude-sonnet-5-5 \
      --prompt "Vad blir vädret i helgen?" --prompts my-prompts.txt

For the scenarios, API brains always run in-process with only mock calendar/TV tools and the screen.
Claude Code uses the strict mock MCP configuration with built-in write/network tools disabled.
--max-turns bounds questions, not dollars or API requests: a question can use several model rounds.
Optional --max-first-token-s/--max-wall-s fail latency regressions. Reports, including partial
infrastructure failures, are kept per run in tests/results/. No real home/calendar/TV writes.
Prompt mode keeps the public read-only lookups (eval_cases.PROMPT_READ_TOOLS) and replaces every other tool with a
recording stub (tests/eval_server.py); it never contacts Jarvis, the panel or Home Assistant.
"""
import argparse
import json
import math
import os
import re
import shlex
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from check import clean_env
from eval_cases import PROMPT_READ_TOOLS, prompt_scenarios, read_prompts, scenarios, select, summarize


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def read_jsonl(path):
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()] if path.exists() else []


def print_prompt_policy(agent, model, repeat):
    """The free preview of prompt mode: which tools run for real and which are blocked stubs. Builds the agent's
    tool list offline like the eval server does, with no model, network or real paths."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("eval_server_policy", ROOT / "tests" / "eval_server.py")
    bootstrap = importlib.util.module_from_spec(spec)
    saved = dict(os.environ)
    try:
        os.environ.clear()
        os.environ.update(clean_env())
        with tempfile.TemporaryDirectory(prefix="hallpanel-it-list-") as tmp:
            os.environ.update(JARVIS_WRITE_GUARD_FILE=str(Path(tmp) / "recent-creates.json"),
                              HALLPANEL_PROFILE=str(Path(tmp) / "profile.json"))
            spec.loader.exec_module(bootstrap)
            real, blocked = bootstrap.prompt_tool_policy(str(Path(tmp) / "history.db"))
    finally:
        os.environ.clear()
        os.environ.update(saved)
    print(f"brain: --agent {agent}" + (f" --model {model}" if model else " (default model)")
          + f"; repeat {repeat}; each prompt is one paid question turn when run with --live --max-turns N")
    print("real tools (read-only): " + ", ".join(real))
    print("blocked and recorded (never run): " + ", ".join(blocked))
    print("history, profile, notes, reminders and the write guard are temporary; nothing contacts Jarvis")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", choices=["claude", "openai", "anthropic"], default="openai")
    ap.add_argument("--model", help="default gpt-6-luna for openai, claude-opus-5-5 for anthropic")
    ap.add_argument("--tier", default="fast")
    ap.add_argument("--effort", help="default none for openai, low for anthropic")
    ap.add_argument("--inprocess", action="store_true", help="with --agent openai: run Luna inside the server")
    ap.add_argument("--list", action="store_true", help="list scenarios without any API calls")
    ap.add_argument("--live", action="store_true", help="explicitly enable paid model evaluation")
    ap.add_argument("--max-turns", type=int, help="required paid question-turn budget, including repeats/followups")
    ap.add_argument("--cases", nargs="+", help="scenario names from --list")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--max-first-token-s", type=float, help="fail a turn exceeding this model text latency")
    ap.add_argument("--max-wall-s", type=float, help="fail a turn exceeding this HTTP roundtrip latency")
    ap.add_argument("--require-cache", action="store_true", help="for an Anthropic profile known to meet the cache minimum")
    ap.add_argument("--prompt", action="append", default=[], metavar="TEXT",
                    help="ask your own question instead of the scenarios (repeatable); writes are blocked")
    ap.add_argument("--prompts", action="append", default=[], metavar="FILE",
                    help="questions from a file, one per line; blank lines and # comments are skipped")
    ap.add_argument("--chain", action="store_true", help="with prompts: one conversation instead of a fresh history each")
    opts = ap.parse_args()
    now = datetime.now(ZoneInfo("Europe/Stockholm"))
    try:
        prompts = read_prompts(opts.prompt, opts.prompts)
    except (OSError, ValueError) as exc:
        ap.error(str(exc))
    prompt_mode = bool(opts.prompt or opts.prompts)
    if prompt_mode and not prompts:
        ap.error("--prompts files hold no prompts")
    if prompt_mode and opts.cases:
        ap.error("--cases selects scenarios; it cannot be combined with --prompt/--prompts")
    if prompt_mode and opts.agent == "claude":
        ap.error("prompt mode needs an in-process API brain: --agent openai or --agent anthropic")
    if opts.chain and not prompt_mode:
        ap.error("--chain applies to --prompt/--prompts only")
    cases = prompt_scenarios(prompts, opts.chain) if prompt_mode else scenarios(now)
    if opts.list:
        for case in cases:
            if prompt_mode:
                print(f"{case.name}: " + " | ".join(turn.text for turn in case.turns))
            else:
                print(f"{case.name}: {len(case.turns)} question turn(s)")
        if prompt_mode:
            print_prompt_policy(opts.agent, opts.model, opts.repeat)
        return 0
    if not opts.live or opts.max_turns is None:
        ap.error("paid evals require --live and --max-turns; use --list for a free preview")
    if any(v is not None and (not math.isfinite(v) or v <= 0)
           for v in (opts.max_first_token_s, opts.max_wall_s)):
        ap.error("latency limits must be finite and positive")
    try:
        selected = select(cases, opts.cases, opts.repeat, opts.max_turns)
    except ValueError as exc:
        ap.error(str(exc))
    anthropic = opts.agent == "anthropic"
    opts.model = opts.model or ("claude-opus-5-5" if anthropic else "gpt-6-luna" if opts.agent == "openai" else None)
    opts.effort = opts.effort or ("low" if anthropic else "none")
    # The eval bootstrap restricts the in-process agents to mock tools. Always use it for API brains.
    opts.inprocess = opts.agent in ("openai", "anthropic")
    if prompt_mode:
        print(f"prompt mode: {sum(len(c.turns) for c in selected)} question turn(s); real tools only "
              f"{', '.join(sorted(PROMPT_READ_TOOLS))}; everything else is blocked and recorded")
    if anthropic:
        sys.path.insert(0, str(ROOT / "server" / "agents"))
        from env_files import load_key_file
        load_key_file("ANTHROPIC_API_KEY", "anthropic.env")
        if not os.environ.get("ANTHROPIC_API_KEY"):
            sys.exit("ANTHROPIC_API_KEY is not set (put it in ~/.config/hallpanel/anthropic.env)")
    if opts.agent == "openai":
        sys.path.insert(0, str(ROOT / "server" / "agents"))
        from env_files import load_key_file
        load_key_file()
        if not os.environ.get("OPENAI_API_KEY"):
            sys.exit("OPENAI_API_KEY is not set (put it in ~/.config/hallpanel/openai.env)")
    tmp = Path(tempfile.mkdtemp(prefix="hallpanel-it-"))
    log, mode_file, token, port = tmp / "calls.jsonl", tmp / "mode", "it-" + os.urandom(6).hex(), free_port()
    tool_log, blocked_log = tmp / "tools.jsonl", tmp / "blocked.jsonl"
    (tmp / "mcp.json").write_text(json.dumps({"mcpServers": {"mock": {
        "command": sys.executable, "args": [str(ROOT / "tests" / "mock_tools_mcp.py")],
        "env": {"MOCK_LOG": str(log), "MOCK_MODE_FILE": str(mode_file), "MOCK_STATE": str(tmp / "state.json")}}}}))
    agent = ["claude", "-p", "--output-format", "stream-json", "--verbose", "--include-partial-messages",
             "--no-session-persistence", "--disable-slash-commands", "--strict-mcp-config",
             "--mcp-config", str(tmp / "mcp.json"), "--permission-mode", "default",
             "--allowedTools", "mcp__mock__calendar_add_event,mcp__mock__calendar_list_events,mcp__mock__tv_power",
             "--disallowedTools", "Bash,Edit,Write,NotebookEdit,WebFetch,WebSearch,Agent,Task"]
    if opts.agent == "claude" and opts.model:
        agent += ["--model", opts.model]
    if opts.agent == "openai":
        agent = [sys.executable, str(ROOT / "server" / "agents" / "openai_agent.py"), "--mock-tools",
                 "--model", opts.model, "--tier", opts.tier, "--effort", opts.effort]
    extra = {"JARVIS_AGENT": "inprocess", "JARVIS_MOCK_TOOLS": "1", "JARVIS_BRAIN_MODEL": opts.model,
             "JARVIS_BRAIN_TIER": opts.tier, "JARVIS_BRAIN_EFFORT": opts.effort} \
        if opts.agent == "openai" and opts.inprocess else {}
    if anthropic:
        extra = {"JARVIS_AGENT": "inprocess", "JARVIS_BRAIN": "anthropic", "JARVIS_MOCK_TOOLS": "1",
                 "JARVIS_BRAIN_MODEL": opts.model, "JARVIS_BRAIN_EFFORT": opts.effort}
    env = {**clean_env(), **{k: v for k, v in os.environ.items() if k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY")}, **extra, "HALLPANEL_TOKEN": token, "JARVIS_AGENT_CMD": shlex.join(agent),
           "JARVIS_WRITE_GUARD_FILE": str(tmp / "recent-creates.json"),
           "HALLPANEL_PROFILE": str(tmp / "profile.json"), "JARVIS_FALLBACK_BRAIN": "none",
           "MOCK_LOG": str(log), "MOCK_MODE_FILE": str(mode_file), "MOCK_STATE": str(tmp / "state.json"),
           "AGENT_TOOL_LOG": str(tool_log)}
    if prompt_mode:
        # A placeholder phone offers till_telefonen and its instructions as in production; the stub blocks it.
        env.update(JARVIS_EVAL_PROMPTS="1", EVAL_BLOCKED_LOG=str(blocked_log), JARVIS_PHONE="eval-stub")
    server = subprocess.Popen([sys.executable, str(ROOT / "tests" / "eval_server.py"), "--port", str(port),
                               "--bind", "127.0.0.1", "--db", str(tmp / "history.db"), "--assets", str(tmp)],
                              env=env, stdout=subprocess.DEVNULL, stderr=open(tmp / "server.log", "w"))
    time.sleep(1)
    def converse(text):
        req = urllib.request.Request(f"http://127.0.0.1:{port}/converse", method="POST",
                                     data=json.dumps({"text": text, "conversation_id": "it"}).encode(),
                                     headers={"X-Hallpanel-Token": token, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=150) as r:
            return json.load(r)

    def history():
        req = urllib.request.Request(f"http://127.0.0.1:{port}/history?limit=500", headers={"X-Hallpanel-Token": token})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.load(r)["messages"]

    results, failed = [], 0
    try:
        # Reset history and mock state between scenarios, preserve them within followups.
        n = 0
        for case in selected:
            reset = urllib.request.Request(f"http://127.0.0.1:{port}/clear", method="POST", data=b"{}",
                                           headers={"X-Hallpanel-Token": token})
            with urllib.request.urlopen(reset, timeout=10):
                pass
            (tmp / "state.json").write_text("[]")
            (tmp / "recent-creates.json").write_text("{}")
            for turn in case.turns:
                n += 1
                text, mode = turn.text, turn.mode
                mode_file.write_text(mode)
                for f in (log, tool_log, blocked_log):
                    f.write_text("")
                before = len(history())
                started = time.monotonic()
                error = None
                try:
                    res = converse(text)
                except (OSError, ValueError) as exc:
                    error = type(exc).__name__
                    res = {"ok": False, "reply": "", "timing": {}}
                wall = time.monotonic() - started
                calls = [json.loads(l) for l in log.read_text().splitlines() if l.strip()]
                tried = [{"tool": c["tool"], "args": c["args"], "error": c.get("error"), "ms": c.get("ms"),
                          "blocked": prompt_mode and c["tool"] not in PROMPT_READ_TOOLS} for c in read_jsonl(tool_log)]
                blocked = [c["tool"] for c in read_jsonl(blocked_log)]
                t = res.get("timing") or {}
                outcome = [(name, False) for name in turn.grade(calls, res["reply"])] + [
                    ("server ok", res.get("ok") is True),
                    ("both turns in history", len(history()) == before + 2)]
                if anthropic and opts.require_cache:
                    outcome.append(("reads the prompt cache", (t.get("tokens_cached") or 0) > 0))
                if opts.max_first_token_s is not None:
                    value = t.get("first_token_s")
                    outcome.append(("first token latency limit", isinstance(value, (int, float)) and
                                    math.isfinite(value) and value <= opts.max_first_token_s))
                if opts.max_wall_s is not None:
                    outcome.append(("HTTP latency limit", wall <= opts.max_wall_s))
                bad = [name for name, passed in outcome if not passed]
                failed += bool(bad)
                results.append({"n": n, "case": case.name, "wall_s": round(wall, 3), "error": error,
                                "text": text, "mode": mode, "reply": res["reply"], "calls": calls,
                                "first_token_s": t.get("first_token_s"), "total_s": t.get("total_s"),
                                "cost_usd": t.get("cost_usd"), "tokens_in": t.get("tokens_in"),
                                "tokens_cached": t.get("tokens_cached"), "tokens_cache_write": t.get("tokens_cache_write"),
                                "steps": t.get("steps"), "brain": t.get("brain"), "tools_tried": tried,
                                "blocked": blocked, "failed_checks": bad})
                print(f"{'PASS' if not bad else 'FAIL'} {n}. [{mode}] {text}")
                if prompt_mode:
                    print(f"     tools run: {[(c['tool'], c['args']) + (('error',) if c['error'] else ()) for c in tried if not c['blocked']]}")
                    print(f"     BLOCKED writes: {[(c['tool'], c['args']) for c in tried if c['blocked']]}")
                else:
                    print(f"     tools: {[(c['tool'], c['args']) for c in calls]}")
                print(f"     reply: {res['reply']}")
                print(f"     {t.get('brain')}, {t.get('steps')} round(s), HTTP {wall:.2f} s, "
                      f"first token {t.get('first_token_s')} s, total {t.get('total_s')} s, cost {t.get('cost_usd')} USD"
                      + (f", tokens in {t.get('tokens_in')} (cached {t.get('tokens_cached')}, written "
                         f"{t.get('tokens_cache_write')})" if t.get("tokens_in") is not None else "")
                      + (f"  FAILED: {bad}" if bad else ""))
                if not res.get("ok"):
                    errors = [l for l in (tmp / "server.log").read_text().splitlines() if "agent failed" in l]
                    if errors:
                        print("     agent error: " + re.sub(r"sk-[A-Za-z0-9_*-]{4,}", "sk-…", errors[-1].split("agent failed: ", 1)[-1])[:300])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        # Infrastructure failure is a failed eval too. Preserve partial results instead of losing the run.
        results.append({"case": "infrastructure", "error": type(exc).__name__,
                        "failed_checks": ["eval infrastructure failed"]})
        failed += 1
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait()
    label = ("claude" if opts.agent == "claude" else f"anthropic-{opts.model}-{opts.effort}" if anthropic
             else f"openai-{opts.model}-{opts.tier or 'default'}" + ("-inprocess" if opts.inprocess else ""))
    suffix = ("" if anthropic or not opts.inprocess else "-inprocess") + ("-prompts" if prompt_mode else "")
    output = ROOT / "tests" / "results"
    output.mkdir(exist_ok=True)
    report = output / f"{now:%Y%m%d-%H%M%S}-{time.time_ns()}-{opts.agent}{suffix}.json"
    report.write_text(json.dumps(
        {"run_at": now.isoformat(timespec="seconds"), "agent": label, "question_turn_budget": opts.max_turns,
         "mode": "prompts" if prompt_mode else "scenarios",
         "real_tools": sorted(PROMPT_READ_TOOLS) if prompt_mode else None,
         "summary": summarize(results), "results": results}, ensure_ascii=False, indent=1))
    print(f"\n{len(results) - failed}/{len(results)} question turns passed; report: {report}")
    print(json.dumps(summarize(results), ensure_ascii=False))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
