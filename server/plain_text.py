"""Jarvis's replies without emoji: the panel's fonts have none (it drew a box) and the voice cannot say one.

On 2026-11-01 Spotify's playlist name "fall songs 2026 🍂 chill autumn vibes" was read straight into the reply
and the panel showed "fall songs 2026 □ chill autumn vibes". Only pictographs go: °, →, € and letters stay.
"""
import re

EMOJI = re.compile("[\U0001F000-\U0001FAFF\U0001FC00-\U0001FFFF"   # emoji, flags, skin tones
                   "\u2300-\u23FF\u2600-\u27BF\u2B00-\u2BFF"          # ⌚ ⏩, ☀ ✅ ❤, ⭐ ⬆
                   "\uFE00-\uFE0F\u200D\u20E3\U000E0000-\U000E007F]")  # variation selectors, joiners, keycaps, tags


def no_emoji(text):
    """text with emoji removed; the spaces around one become one (a streamed piece keeps its edge space)."""
    if not text:
        return text
    out = EMOJI.sub("", text)
    return re.sub(r" {2,}", " ", out) if out != text else out
