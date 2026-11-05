"""Exact spoken commands that run one tool without a model round: radio off, the volume, and the music's controls.

Through the model "Stäng av radion" took 1.5 s and "Sänk volymen med 20%" 3.7 s, for tools that run in
8-26 ms (2026-10-27). Only whole sentences below match; anything with a place, a channel, a follow-up or a
second request goes to the agent. Numbers must be digits, as the transcription writes them.
"Kan du stänga av musiken?" waited 9.3 s on the model for a 50 ms Spotify pause (2026-10-28): "music off" stops
what plays (agent_core.ToolAgent.command picks Spotify or the radio), and next, previous, pause and resume go to
Spotify directly.
"Spela Ghost Stories av Coldplay på Spotify" took 4.4-7.7 s through the model, 2.5-5.7 s of it the search
(2026-10-28): a plain "spela X på Spotify" searches and plays without the model (agent_core.ToolAgent.command,
spotify.quick_play); anything vague, long or uncertain still goes to the model.
"Skulle du kunna spela Coldplay på Spotify?" took 2.0-2.6 s through the model four times on 2026-11-01, while
"Kan du spela Coldplay på Spotify?" played in 0.5-1.0 s: Joel's "skulle du kunna" is polite too, and "lite X"
("sätta på lite Coldplay", 2.6 s) means the artist.
"""
import re

POLITE = r"(?:(?:hej )?jarvis )?(?:(?:kan|skulle) du (?:snälla )?(?:kunna )?|snälla )?"
TAIL = r"(?: tack| är du snäll)?"
VOLUME = r"vol[yu]men"     # "Kan du höja volumen?" was transcribed so (2026-10-27)
PERCENT = r"(\d{1,3}) ?(?:%|procent(?:enheter)?)"
PLAY = r"sp[eä]la"      # Whisper wrote "Späla Coldplay" (2026-10-30), which then waited 2.8 s on the model

# Sveriges Radio's channels as Whisper writes them: "P3", "p 3", "Sätt på p fyra." (2026-10-23).
CHANNEL = r"p ?([1-4]|ett|två|tre|fyra)( stockholm)?"
CHANNEL_DIGITS = {"ett": "1", "två": "2", "tre": "3", "fyra": "4"}
RADIO_OFF = re.compile(POLITE + rf"(?:stäng(?:a)? av|stoppa) (?:radion|{CHANNEL})" + TAIL)
# "Spela P3." and "Sätt på p fyra." went through Sonnet in 2.2-3.6 s for the radio's one call (2026-10-23..28).
RADIO_PLAY = re.compile(POLITE + rf"(?:sp[eä]la|sätt(?:a)? på|starta) (?:radio |kanalen )?{CHANNEL}" + TAIL)
# "Spela något på Spotify." made Sonnet pick the liked songs every time (1.2-3.0 s, 2026-10-29/30).
SPOTIFY_ANY = re.compile(POLITE + r"(?:sp[eä]la|sätt(?:a)? på) (?:något|nåt|någonting|lite musik|musik) (?:på|från) spotify" + TAIL)
VOLUME_STEP = re.compile(POLITE + r"(höj|sänk)(?:a)? " + VOLUME + rf"(?: med {PERCENT})?" + TAIL)
VOLUME_SET = re.compile(POLITE + r"(?:sätt|ställ)(?:a)? " + VOLUME + rf" (?:på|till) {PERCENT}" + TAIL)
MUSIC_OFF = re.compile(POLITE + r"(?:stäng(?:a)? av|stoppa|pausa) (?:musiken|låten)" + TAIL)
SPOTIFY = {
    "pause": re.compile(POLITE + r"(?:pausa|stoppa|stäng(?:a)? av) spotify" + TAIL),
    "resume": re.compile(POLITE + r"(?:fortsätt(?:a)? (?:spela|med musiken|musiken|spotify)|spela vidare)" + TAIL),
    "next": re.compile(POLITE + r"(?:spela )?(?:nästa låt|byt(?:a)? låt|hoppa över (?:den här )?låten|skippa (?:den här )?låten)" + TAIL),
    "previous": re.compile(POLITE + r"(?:spela )?(?:föregående låt|förra låten|låten innan)" + TAIL),
}


# "spela [albumet|skivan|spellistan|låten] X på Spotify", "spela något med X på Spotify"
SPOTIFY_PLAY = re.compile(POLITE + rf"(?:{PLAY}|sätt på|sätta på|starta) (?:(albumet|skivan|spellistan|låten|något med|nåt med|musik med|låtar med|lite) )?"
                          r"(.+?) (?:på|i|från) spotify" + TAIL)
KINDS = {"albumet": "album", "skivan": "album", "spellistan": "playlist", "låten": "track", "något med": "artist",
         "nåt med": "artist", "musik med": "artist", "låtar med": "artist", "lite": "artist"}
# Words that make a request vague or a correction: those stay with the model, which can ask or reason.
VAGUE = re.compile(r"\b(?:istället|eller|något|nåt|lite|typ|kanske|lugn|glad|sen|också|bokstav|stavas|med [a-zåäö] )\b")
# "spela Coldplay" without "på Spotify": Sonnet took 2.7-3.8 s to play the same thing (2026-10-30, "Spela Coldplay.",
# "Spela Anna Book."). Radio, a place or speaker ("på JBL:en", "i köket") and anything that is not a name stay with
# the model; spotify.confident() still leaves an unsure search to it.
SPOTIFY_BARE = re.compile(POLITE + rf"(?:{PLAY}|sätt på|sätta på) (?:(albumet|skivan|spellistan|låten|något med|nåt med|musik med|låtar med) )?"
                          r"(.+?)" + TAIL)
NOT_MUSIC = re.compile(r"\b(?:p ?\d|radio(?:n)?|kanal(?:en)?|ekot|nyheter(?:na)?|podd(?:en)?|podcast|sveriges|din gata|"
                       r"på|i|från|igen|musik(?:en)?|låt(?:en|ar)?|album(?:et)?|nästa|förra|föregående|vidare|upp|om)\b")


def match(text, language=None):
    """(tool name, args) for an exact command, or None."""
    if language not in (None, "sv"):
        return None
    query = re.sub(r"\s+", " ", text.casefold().replace(",", " ")).strip().rstrip("?.!").strip()
    if RADIO_OFF.fullmatch(query):
        return "radio", {"action": "stop"}
    if m := RADIO_PLAY.fullmatch(query):
        return "radio", {"action": "play", "channel": "P" + CHANNEL_DIGITS.get(m[1], m[1]) + (" Stockholm" if m[2] else "")}
    if SPOTIFY_ANY.fullmatch(query):
        return "spotify_play", {"query": "mina gillade låtar", "media_type": "track"}
    if m := VOLUME_STEP.fullmatch(query):
        step = int(m[2]) if m[2] else None
        if step is not None and not 1 <= step <= 100:
            return None
        args = {"action": "up" if m[1] == "höj" else "down"}
        return "volym", {**args, "step": step} if step else args
    if (m := VOLUME_SET.fullmatch(query)) and int(m[1]) <= 100:
        return "volym", {"action": "set", "percent": int(m[1])}
    if MUSIC_OFF.fullmatch(query):
        return "music", {"action": "off"}
    for action, pattern in SPOTIFY.items():
        if pattern.fullmatch(query):
            return "spotify", {"action": action}
    if m := SPOTIFY_PLAY.fullmatch(query):
        what = m[2].strip()
        if VAGUE.search(what) or len(what) > 60 or len(what.split()) > 8 or not what:
            return None
        return "spotify_play", {"query": what, "media_type": KINDS.get(m[1] or "", "track")}
    if m := SPOTIFY_BARE.fullmatch(query):
        what = m[2].strip()
        liked = re.fullmatch(r"(?:mina |dina )?(?:gillade|favorit) ?låtar(?:na)?", what)
        if not liked and (VAGUE.search(what) or NOT_MUSIC.search(what)) or len(what.split()) > 6 or not what:
            return None
        return "spotify_play", {"query": what, "media_type": KINDS.get(m[1] or "", "track")}
    return None
