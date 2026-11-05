"""Calendar topics Jarvis never mentions, set on Jarvis in server.env rather than in the code (docs/jarvis.md):

  JARVIS_SILENT_TOPICS        a regex, matched case-insensitively as whole words against event titles
  JARVIS_SILENT_TOPICS_NAME   how the prompt names them, e.g. "dammsugningen (vacuuming)"

Unset, nothing is hidden. Their events stay in the calendar and still count as busy; they are only left out of
what Jarvis says, announces, briefs in the morning and sends to the phone. Standard library only.
"""
import os
import re


def pattern():
    raw = os.environ.get("JARVIS_SILENT_TOPICS", "").strip()
    return re.compile(rf"\b(?:{raw})\b", re.IGNORECASE) if raw else None


def silent(title):
    """True when an event title is about a silent topic."""
    rx = pattern()
    return bool(rx and rx.search(title or ""))


def instructions():
    """The prompt's rule for them, or "" when none are set. Read once by prompts.py, so the prompt cache stays fixed."""
    if pattern() is None:
        return ""
    name = os.environ.get("JARVIS_SILENT_TOPICS_NAME", "").strip() or "de dolda ämnena"
    return (f"Nämn aldrig {name}, inte heller deras kalenderhändelser eller påminnelser, i tal eller\n"
            "meddelanden till telefonen. Säg inte heller att du utelämnar dem. Deras bokade tider räknas fortfarande\n"
            "som upptagna när du letar efter en ledig lucka.\n")
