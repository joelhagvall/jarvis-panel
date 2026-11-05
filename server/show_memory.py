#!/usr/bin/env python3
"""What Jarvis remembers about Joel (server/memory.py): the profile, then the notes as a table.

  python3 ~/hallpanel-live/server/show_memory.py              the profile and the notes that hold today
  python3 ~/hallpanel-live/server/show_memory.py --all        also the forgotten and expired notes
  python3 ~/hallpanel-live/server/show_memory.py --forget 3   forget note 3 (its row stays)
  python3 ~/hallpanel-live/server/show_memory.py --db ~/.local/share/hallpanel/history-sim.db
"""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from memory import STOCKHOLM, Memory

args = sys.argv[1:]
db = "~/.local/share/hallpanel/history.db"
if "--db" in args:
    i = args.index("--db")
    db, args = args[i + 1], args[:i] + args[i + 2:]
memory = Memory(db)
if "--forget" in args:
    print(memory.forget_note(int(args[args.index("--forget") + 1]))[1])
    sys.exit(0)
print(f"Profil ({memory.profile_path}):")
for line in memory.profile() or ["(ingen profil)"]:
    print(f"  {line}")
saved = dict(memory.db.execute("SELECT id, ts FROM facts"))
when = lambda t: datetime.fromisoformat(t).astimezone(STOCKHOLM).strftime("%Y-%m-%d %H:%M") if t else ""
print(f"\n{'nr':>3}  {'sparad':16} {'till':10} {'glömd':16} text")
for n, text, until, forgotten in memory.notes(everything="--all" in args):
    print(f"{n:>3}  {when(saved[n]):16} {until or '':10} {when(forgotten):16} {text}")
