"""Direct pytest runs (`.venv/bin/python -m pytest tests/test_x.py`) get the same private state files as
tools/check.py's local_tests, before any test imports agent_core: a booking test once wrote the Mac's real
~/.local/share/hallpanel/recent-creates.json, the guard that stops a repeated tvatt_boka (2026-11-03)."""
import os
import tempfile
from pathlib import Path

_tmp = Path(tempfile.mkdtemp(prefix="hallpanel-pytest-"))
for name, file in (("JARVIS_WRITE_GUARD_FILE", "recent-creates.json"), ("HALLPANEL_PROFILE", "profile.json"),
                   ("HALLPANEL_AUTH_PIN", "panel-auth.json")):
    os.environ.setdefault(name, str(_tmp / file))
