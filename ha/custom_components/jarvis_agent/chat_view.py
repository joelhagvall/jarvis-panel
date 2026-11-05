"""The dashboard chat's earlier bubbles (Joel, 2026-10-30: he could not scroll back to his first question).

last_said and last_reply are the newest question and reply. `earlier` is what came before that question in the
same fresh window (sensor.CHAT_FRESH, and since the last Rensa), oldest first, at most EARLIER messages, as one
text attribute: messages separated by US (0x1f), each "u" (Joel) or "j" (Jarvis) and then the text. The panel's
chat_scroll.h panel_chat::earlier fills that many bubbles above the newest two.

No Home Assistant imports: the free tests load this file on its own.
"""
EARLIER = 6
SEP = "\x1f"
MAX_CHARS = 600         # a bubble's text; the full message stays in Jarvis's history


def earlier(fresh):
    """fresh: the window's messages, oldest first, as dicts with "role" and "text"."""
    users = [i for i, m in enumerate(fresh) if m["role"] == "user"]
    before = fresh[:users[-1]] if users else fresh[:-1]
    return SEP.join(("u" if m["role"] == "user" else "j") + " ".join(str(m["text"]).replace(SEP, " ").split())[:MAX_CHARS]
                    for m in before[-EARLIER:] if str(m["text"]).strip())
