"""The eval server only: offer mock tools and the screen; never real home/calendar/TV tools.

With JARVIS_EVAL_PROMPTS=1 (integration_test.py --prompt/--prompts) the agent also keeps its public read-only
lookups (eval_cases.PROMPT_READ_TOOLS), so weather, SL, flight and news questions get real data. Every other tool
it offers still appears to the model but is a stub: the call is recorded in EVAL_BLOCKED_LOG and answered with an
error, and nothing is written, sent, played or booked.
"""
import json
import os
from pathlib import Path
import runpy
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'server'), str(ROOT / 'server/agents'), str(ROOT / 'tests')]
import agent_core
from eval_cases import PROMPT_READ_TOOLS
from watch import Watch

original = agent_core.ToolAgent._ensure_tools
BLOCKED = ("Testläge: verktyget {name} är avstängt och kördes inte. Inget har ändrats, sparats eller skickats. "
           "Säg kort vad du skulle ha gjort.")


def blocked(name):
    """A recording stub for a tool that writes, sends, plays or books."""
    def stub(args):
        if log := os.environ.get('EVAL_BLOCKED_LOG'):
            with open(log, 'a') as f:
                f.write(json.dumps({'tool': name, 'args': args}, ensure_ascii=False) + '\n')
        return True, BLOCKED.format(name=name)
    stub.blocked = True
    return stub


def stub_writes(agent):
    """Keep PROMPT_READ_TOOLS; every other offered tool runs the stub (local tools are dispatched first)."""
    for tool in agent.tools:
        if tool['name'] not in PROMPT_READ_TOOLS:
            agent.local_tools[tool['name']] = blocked(tool['name'])
    agent.read_only &= PROMPT_READ_TOOLS


def mock_tools_only(self):
    if not self.mock or self.mcp or self.tv or self.home:
        raise RuntimeError('eval server requires only mock tools')
    original(self)
    if os.environ.get('JARVIS_EVAL_PROMPTS') == '1':
        return stub_writes(self)
    allowed = {t['name'] for t in self.mock.TOOLS} | {'skarm'}
    self.tools = [t for t in self.tools if t['name'] in allowed]
    self.local_tools = {k: v for k, v in self.local_tools.items() if k == 'skarm'}
    self.read_only &= allowed


def prompt_tool_policy(db):
    """(real, blocked) tool names the prompt mode offers, built offline like the server does (db: a temporary
    SQLite path for the memory/reminder tools; a placeholder phone adds till_telefonen)."""
    from memory import Memory
    from notify import Notifier
    from reminders import Reminders
    agent = agent_core.ToolAgent()
    agent._init_tools(True, None, None, None)
    phone = Notifier()
    phone.phone = 'eval-stub'
    memory, reminders = Memory(db), Reminders(db)
    try:
        agent.extra_tools = [*memory.tools(), *reminders.tools(), *phone.tools()]
        if agent.mcp or agent.tv or agent.home:
            raise RuntimeError('eval server requires only mock tools')
        original(agent)
        stub_writes(agent)
    finally:
        memory.db.close()
        reminders.db.close()
    names = [t['name'] for t in agent.tools]
    return ([n for n in names if not getattr(agent.local_tools.get(n), 'blocked', False)],
            [n for n in names if getattr(agent.local_tools.get(n), 'blocked', False)])


agent_core.ToolAgent._ensure_tools = mock_tools_only
# Eval requests do not need production background probes or reminder delivery.
Watch.start = lambda self: None
if __name__ == '__main__':
    # The server refuses to start without HALLPANEL_TOKEN now (2026-11-02 review); the eval harness is
    # local, so it gets a plain one here.
    os.environ.setdefault('HALLPANEL_TOKEN', 'eval-local')
    runpy.run_path(str(ROOT / 'server/hallpanel_server.py'), run_name='__main__')
