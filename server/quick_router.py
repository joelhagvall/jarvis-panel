"""A tiny hand-weighted network that says which fast path a spoken question belongs to, or 'agent'.

Shadow mode only (2026-11-04): hallpanel_server.ask_reply asks it beside quick_command/quick_reply and logs
the two side by side in the journal, never acting on it:

  quick-router: net=<class> <score> took=<command:TOOL|reply|model> <agree|disagree> text=<the question>
  python3 ~/hallpanel-live/server/show_router.py [--days N]   read it on Jarvis: counts, then each disagreement
  journalctl --user-unit hallpanel -g 'quick-router:'          the raw lines (`--user -u` prints nothing there)

Every weight below was written by Claude by hand from the voice turns in history.db, no training: features are
words, word bigrams and character trigrams per word (so "späla", "volumen", "tjaffla" still count), one hidden
layer of detectors clipped to 0..1, and a linear output per class; 'agent' scores 0 and a class wins only at
MARGIN or more. On the 246 distinct questions up to 2026-11-04 it chose right for 99.2 % against the regexes'
95.9 %, but both were tuned on those same questions: the shadow log on new turns is the real measure. It picks
the class only; the channel, percent or artist still come from quick_command.py. Standard library only.
tests/test_quick_router.py; docs/voice-latency.md.
"""

import re

MARGIN = 1.0
WEEKDAYS = ("måndag", "tisdag", "onsdag", "torsdag", "fredag", "lördag", "söndag")
CLASSES = ("agent", "clock", "metro", "weather", "calendar_day", "radio_play", "radio_off", "volume_up", "volume_down",
           "volume_set", "music_off", "spotify_play", "spotify_liked", "spotify_next", "spotify_prev", "spotify_resume")


def features(text):
    q = text.casefold()
    q = re.sub(r"^(?:hej,? )?jarvis[,.!]?\s*", "", q)
    q = re.sub(r"[%]", " procent ", q)
    n_sent = len([m for m in re.findall(r"[.?!]+", q.strip().rstrip(".?!"))])
    q = re.sub(r"[^\wåäöé ]+", " ", q)
    words = q.split()
    f = {}
    for w in words:
        f["w:" + w] = 1.0
        padded = f"#{w}#"
        for i in range(len(padded) - 2):
            f["t:" + padded[i:i + 3]] = 1.0
    for a, b in zip(words, words[1:]):
        f["b:" + a + "_" + b] = 1.0
    f["n_words"] = float(len(words))
    f["n_och"] = float(words.count("och"))
    f["n_sent"] = float(n_sent)
    return f


def tri(word, weight=1.0):
    padded = f"#{word}#"
    return {"t:" + padded[i:i + 3]: weight for i in range(len(padded) - 2)}


def W(words=(), bigrams=(), trigrams_of=(), weight=1.5, **extra):
    d = {}
    for w in words:
        d["w:" + w] = weight
    for b in bigrams:
        d["b:" + b] = weight
    for w in trigrams_of:
        for k, v in tri(w, weight * 0.25).items():
            d[k] = max(d.get(k, 0), v)
    d.update(extra)
    return d


# ---- hidden layer: (weights, bias), clipped to 0..1 after the sum.
HIDDEN = {
    # the play verb, tolerant of Whisper ("späla", "tjaffla"), not the past tense ("spelade")
    "play": ({**W(words=["spela", "späla", "starta", "tjaffla"], bigrams=["sätt_på", "sätta_på"]),
              **W(trigrams_of=["spela", "späla", "spila"], weight=0.8),
              "t:lad": -1.5, "t:ade": -1.5, "w:spelade": -3, "w:spelar": -2}, -0.5),
    "spotify": (W(words=["spotify"], trigrams_of=["spotify"], weight=1.5), -0.5),
    "radio": ({**W(words=["p3", "p4", "p1", "p2", "radio", "radion", "kanalen", "ekot"]),
               **W(bigrams=["p_3", "p_4", "p_1", "p_2", "p_ett", "p_två", "p_tre", "p_fyra"])}, -0.5),
    "off": (W(words=["stoppa", "pausa", "stop"], bigrams=["stäng_av", "stänga_av"]), -0.5),
    "music": (W(words=["musiken", "låten", "musik"], trigrams_of=["musiken"], weight=1.5), -0.5),
    "volume": (W(words=["volymen", "volumen", "volym", "ljudet"], trigrams_of=["volymen", "volumen"], weight=1.5), -0.5),
    "up": (W(words=["höj", "höja", "högre"], weight=1.5), -0.5),
    "down": (W(words=["sänk", "sänka", "lägre", "tystare"], weight=1.5), -0.5),
    "setverb": (W(words=["sätt", "ställ", "ställa"], bigrams=["volymen_på", "volymen_till", "volumen_på", "volumen_till"], weight=0.8), -0.5),
    "percent": (W(words=["procent"]), -0.5),
    "next": (W(words=["skippa", "nästa"], bigrams=["nästa_låt", "byt_låt", "byta_låt", "hoppa_över"], weight=1.5), -0.5),
    "prev": (W(words=["föregående"], bigrams=["förra_låten", "låten_innan"], weight=1.5), -0.5),
    "resume": (W(words=["fortsätt", "fortsätta"], bigrams=["spela_vidare"], weight=1.5), -0.5),
    "liked": (W(words=["gillade", "favoritlåtar", "gillar"], bigrams=["gillade_låtar"], weight=1.5), -0.5),
    # "spela något/musik på spotify" without an artist: the liked songs. All three parts are needed.
    "anyspotify": ({**W(words=["något", "nåt", "någonting", "musik"], weight=1.5), **W(words=["spotify"], weight=1.0),
                    **W(trigrams_of=["spela"], weight=0.75), "b:något_med": -3, "b:nåt_med": -3, "b:musik_med": -3,
                    "w:som": -2, "w:tror": -2, "w:tycker": -2}, -2.4),
    "clock": ({**W(words=["klockan"], bigrams=["vad_är", "hur_mycket", "är_klockan"], weight=1.5),
               "w:lägg": -3, "w:påminn": -3, "w:in": -2, "w:efter": -2, "w:kvart": -3, "w:halv": -3}, -1.5),
    "metro": ({**W(words=["tunnelbana", "tunnelbanan", "tåget"], weight=1.5), **W(trigrams_of=["tunnelbana"], weight=0.8),
               "b:när_går": 0.7, "w:nästa": 0.5, "w:går": 0.3, "w:buss": -2, "w:bussen": -2}, -1.2),
    "weather": ({**W(words=["vädret", "väder"], weight=1.5), **W(trigrams_of=["vädret"], weight=0.6),
                 "w:blir": 0.3, "b:vad_blir": 0.3, "b:hur_blir": 0.3, "w:blev": -2, "w:efter": -1.5, "w:klockan": -1.5,
                 "w:paraply": -3, "w:regna": -3, "w:regnar": -3}, -0.8),
    "calendar": ({**W(words=["kalendern", "inbokat"], weight=1.5), "w:kalender": 0.8, "b:vad_har": 0.6, "b:har_jag": 0.6,
                  "w:du": -1.5, "w:ligger": -2}, -1.2),
    "day": (W(words=["idag", "imorgon"], bigrams=["i_dag", "i_morgon"] + ["på_" + d for d in WEEKDAYS], weight=1.5), -0.5),
    "weekday": (W(bigrams=["på_" + d for d in WEEKDAYS]), -0.5),
    # inhibitors
    "vague": (W(words=["istället", "eller", "typ", "kanske", "senaste", "spellista", "väldigt", "tror", "tycker", "menar",
                       "menade", "stavas", "bokstav"], bigrams=["som_du", "om_på", "spotify_på"]), -0.5),
    "followup": (W(words=["nej", "ja", "jo", "också", "igen", "samma"], bigrams=["jag_sa", "jag_menar", "nej_tack"], weight=1.5), -0.5),
    "compound": ({"n_och": 1.0, "n_sent": 1.5, "w:sen": 1.0, "w:samt": 1.0, "w:också": 1.0, "w:dessutom": 1.0}, -0.5),
    "place": (W(words=["jbl", "köket", "sovrummet", "vardagsrummet", "hallen", "högtalaren", "högtalen", "högtalarna"]), -0.5),
    "english": (W(words=["the", "you", "play", "what", "is", "my", "with", "me", "how", "and", "next", "from", "please"], weight=0.8), -0.5),
    "add": (W(words=["lägg", "lägga", "boka", "ändra", "påminn", "påminna", "byta", "byt", "flytta", "ta", "lägger", "lade"],
              bigrams=["ta_bort"], weight=1.5), -0.5),
    "short": ({"n_words": -1.0}, 2.0),      # one word -> 1, two words -> 0
    "long7": ({"n_words": 1.0}, -7.0),      # eight words -> 1
    "long9": ({"n_words": 1.0}, -9.0),
}

# ---- output layer: class -> (weights over the hidden detectors, bias). 'agent' is 0.
INHIBIT = {"place": -3, "followup": -3, "compound": -2, "english": -3, "long9": -1.5}
OUTPUT = {
    "spotify_play": ({"play": 3, "spotify": 1, "radio": -4, "off": -4, "vague": -2, "liked": -4, "next": -4, "prev": -4,
                      "resume": -4, "music": -2, "volume": -4, "anyspotify": -4, "short": -4, "weather": -3, "calendar": -3,
                      **INHIBIT}, -1.5),
    "spotify_liked": ({"liked": 4, "anyspotify": 4, "vague": -3, **INHIBIT}, -2.5),
    "spotify_next": ({"next": 4, "play": 0.5, "metro": -4, **INHIBIT}, -2.0),
    "spotify_prev": ({"prev": 4, "play": 0.5, **INHIBIT}, -2.0),
    "spotify_resume": ({"resume": 4, **INHIBIT}, -2.0),
    "radio_play": ({"play": 3, "radio": 3, "off": -4, "volume": -3, **INHIBIT}, -3.5),
    "radio_off": ({"off": 3, "radio": 3, "music": -3, "volume": -3, **INHIBIT}, -3.5),
    "music_off": ({"off": 3, "music": 2, "spotify": 2, "radio": -4, "volume": -4, **INHIBIT}, -3.5),
    "volume_up": ({"up": 3, "volume": 2, "music": 2, "setverb": -2, "down": -4, **INHIBIT}, -3.5),
    "volume_down": ({"down": 3, "volume": 2, "music": 2, "setverb": -2, "up": -4, **INHIBIT}, -3.5),
    "volume_set": ({"setverb": 3, "volume": 2, "percent": 2, "up": -4, "down": -4, "radio": -4, **INHIBIT}, -5.0),
    "clock": ({"clock": 4, "long7": -3, "calendar": -3, "weather": -3, **INHIBIT}, -2.5),
    "metro": ({"metro": 4, "long7": -3, **INHIBIT}, -2.5),
    "weather": ({"weather": 4, "day": 1, "weekday": -4, "long7": -3, "add": -3, "calendar": -3, **INHIBIT}, -2.5),
    "calendar_day": ({"calendar": 3, "day": 3, "add": -4, "long9": -3, "weather": -3, **INHIBIT}, -4.5),
}


def clip(x):
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def forward(text):
    f = features(text)
    h = {name: clip(sum(w * f.get(k, 0.0) for k, w in weights.items()) + bias) for name, (weights, bias) in HIDDEN.items()}
    scores = {cls: sum(w * h[name] for name, w in weights.items()) + bias for cls, (weights, bias) in OUTPUT.items()}
    scores["agent"] = 0.0
    return scores, h


def classify(text, language=None):
    """(class, score): the fast path the question belongs to, or ("agent", 0.0)."""
    if language not in (None, "sv"):
        return "agent", 0.0
    scores, _ = forward(text)
    best = max(scores, key=scores.get)
    if best != "agent" and scores[best] < MARGIN:
        return "agent", scores[best]
    return best, scores[best]


def parameter_count():
    return sum(len(w) + 1 for w, _ in HIDDEN.values()) + sum(len(w) + 1 for w, _ in OUTPUT.values())


def shadow_line(text, language, took):
    """The journal line for one question: the net's class beside what the server actually did (took:
    'command:<tool>', 'reply' or 'model'). They agree when both chose a fast path, or both the model."""
    cls, score = classify(text, language)
    verdict = "agree" if (cls != "agent") == (took != "model") else "disagree"
    return f"quick-router: net={cls} {score:.2f} took={took} {verdict} text={text.strip()!r}"


if __name__ == "__main__":
    import sys
    for line in sys.argv[1:] or sys.stdin:
        cls, score = classify(line.strip())
        print(f"{cls:14s} {score:5.2f}  {line.strip()}")
