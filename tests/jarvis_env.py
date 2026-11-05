#!/usr/bin/env python3
"""What the scripts in tests/ that run on Jarvis take from the server's own ~/.config/hallpanel/server.env."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server" / "agents"))
from env_files import env_file  # noqa: E402


def calendar():
    """(url, token file) of the calendar Jarvis's server uses. Exits when either is missing: not on Jarvis."""
    env = env_file("server.env")
    url, token_file = env.get("JARVIS_CALENDAR_MCP"), env.get("JARVIS_CALENDAR_MCP_TOKEN_FILE")
    if not (url and token_file):
        sys.exit("No JARVIS_CALENDAR_MCP and JARVIS_CALENDAR_MCP_TOKEN_FILE in ~/.config/hallpanel/server.env: "
                 "run this on Jarvis.")
    return url, token_file
