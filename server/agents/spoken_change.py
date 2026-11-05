"""Offline utterance checks for the laundry booking and the morning changes (Morgon-TV, wakeups).

A change runs when Joel's own words in this question ask for it, in one of three ways:

  direct     a sentence with the routine's topic and an action ("boka tvätt på fredag 18",
             "ändra larmet till sju", "jag vill vakna halv sju", "wake me up at seven");
  answer     an affirmative answer ("ja", "javisst", "ja, kör", "ja, boka den") or one with a time, day or
             number ("21 till 24", "sju") to the question that ends Jarvis's last reply, when
             - Joel's previous turn asked for this change directly ("boka tvätt på onsdag" → "vilket pass?"), or
             - the question offers the change (has the routine's action) and is about the routine: it names
               it, refers back ("ska jag boka det?") to the sentence before it that does, or Joel's previous
               turn named it ("finns det lediga tvättider?" → "... vill du boka något av dem?").

Refused: a cancellation anywhere ("avboka", "glöm det", "never mind"), a leading "nej"/"no", a negation
within three words of the action ("boka inte", "jag vill inte ändra", but not the polite "kan du inte boka"
nor "... och säg att vi inte får glömma matsäcken"), questions and hypotheticals ("kan man boka ...?",
"om jag ber dig ..."), and a question in the answer ("ja, vad kostar det?"). A question in Jarvis's reply
that names only another routine authorizes nothing here.

These are lexical checks, not a parser; a refusal tells the model to ask "Ska jag ...?" so that Joel's yes
goes through. Refusals log the tool and reason to the server journal, never the utterance or the arguments.
The question's words travel per question in a Turn (agent_core.ToolAgent.spoken), never in module globals;
a command-mode agent gets them through the environment variable JARVIS_SPOKEN.
"""
import json
import os
import re
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class Turn:
    """What Joel said in this question, Jarvis's reply before it and Joel's turn before that reply."""
    said: str = ""
    last_reply: str = ""
    last_said: str = ""

    @classmethod
    def from_recent(cls, said, recent):
        """recent: the history's messages, oldest first, without this question."""
        last_reply = last_said = ""
        for i in range(len(recent) - 1, -1, -1):
            if recent[i]["role"] == "jarvis":
                last_reply = recent[i]["text"]
                last_said = next((m["text"] for m in reversed(recent[:i]) if m["role"] == "user"), "")
                break
        return cls(said or "", last_reply or "", last_said or "")

    def to_env(self):
        return json.dumps({"said": self.said, "last_reply": self.last_reply, "last_said": self.last_said},
                          ensure_ascii=False)

    @classmethod
    def from_env(cls):
        try:
            data = json.loads(os.environ.get("JARVIS_SPOKEN") or "{}")
            return cls(str(data.get("said", "")), str(data.get("last_reply", "")), str(data.get("last_said", "")))
        except (ValueError, AttributeError):
            return cls()


def _rx(*alternatives):
    return re.compile(r"\b(?:" + "|".join(alternatives) + r")\b", re.I)


# -- the routines -------------------------------------------------------------------------------
# Each routine has its own topic: a laundry request cannot authorize an alarm change, and so on.
LAUNDRY = _rx(r"tv[äa]tt\w*", r"laundry", r"washing")
BOOKING = _rx(r"boka(?:r|d|t)?", r"lägg(?:a|er)?\s+(?:in|till)", r"reservera(?:r)?", r"schemalägg(?:a|er)?",
              r"fixa(?:r)?", r"book(?:s|ed)?", r"reserve", r"schedule")
# Plain "tv" is playback, not the recurring morning schedule.
MORNING_TV = _rx(r"morgon[\s–-]*(?:tv|teve)\w*", r"morning[\s-]*tv")
WAKE = _rx(r"väck\w*", r"vakna\w*", r"larm\w*", r"wake\w*", r"alarm\w*")
_CHANGE = (r"ändra(?:r)?", r"ställ(?:a|er)?", r"sätt(?:a|er)?", r"flytta(?:r)?", r"lägg(?:a|er)?", r"ta(?:r)?\s+bort",
           r"byt(?:a|er)?", r"stäng(?:a|er)?(?:\s+av)?", r"slå(?:r)?\s+(?:på|av)", r"justera(?:r)?", r"rätta(?:r)?",
           r"tänd(?:a|er)?", r"(?:in)?aktivera(?:r)?", r"skippa(?:r)?", r"hoppa(?:r)?\s+över", r"stryk",
           r"change", r"set", r"move", r"turn\s+(?:on|off)", r"switch\s+(?:on|off)", r"enable", r"disable",
           r"cancel", r"remove", r"delete", r"skip", r"add", r"put", r"make")
ACTION = _rx(*_CHANGE, r"(?:ingen|inget|inga|no)\s+morgon[\s–-]*(?:tv|teve)\w*")
WAKE_ACTION = _rx(*_CHANGE, r"väck(?:a|er)?\s+(?:mig|oss)", r"wake\s+(?:me|us)", r"(?:ingen|inget|inga|no)\s+"
                  r"(?:väckning\w*|larm\w*|alarm\w*|wake\w*)",
                  r"(?:vill|ska|behöver|måste|tänker)\s+(?:gärna\s+)?vakna", r"(?:want|need|have)\s+to\s+wake")
ROUTINES = {"laundry": (LAUNDRY, BOOKING), "morning_tv": (MORNING_TV, ACTION), "wake": (WAKE, WAKE_ACTION)}

# -- the words ------------------------------------------------------------------------------------
CANCEL = _rx(r"avboka\w*", r"boka\s+av", r"avbryt\w*", r"glöm\s+det", r"strunta\s+i\s+det", r"låt\s+bli",
             r"skit\s+i\s+det", r"never\s*mind", r"forget\s+it", r"cancel\s+that", r"kanske", r"maybe",
             r"vet\s+inte", r"not\s+sure", r"vänta", r"wait")
LEADING_NO = re.compile(r"^\W*(?:nej|nä|nää|nix|näe|no|nope|nah)\b", re.I)
NEGATION = _rx(r"inte", r"inget", r"ingenting", r"inga", r"ingen", r"aldrig", r"ej", r"not", r"never", r"no",
               r"don['’]?t", r"do\s+not", r"won['’]?t")
POLITE = re.compile(r"\b(?:kan|kunde|skulle|vill)\s+du\s+$", re.I)    # "kan du inte boka ..." asks for it
AFFIRM = re.compile(r"^\W*(?:ja+|javisst|jadå|jo|jepp|japp|jajamän|visst|absolut|gärna|okej|ok|okay|kör|gör\s+(?:det|så)|"
                    r"precis|perfekt|bra|toppen|boka|ändra|yes|yeah|yep|yup|sure|please|go\s+ahead|do\s+it)\b", re.I)
DETAIL = _rx(r"\d+", r"måndag\w*", r"tisdag\w*", r"onsdag\w*", r"torsdag\w*", r"fredag\w*", r"lördag\w*",
             r"söndag\w*", r"vardag\w*", r"helg\w*", r"i\s*morgon", r"imorgon", r"ikväll", r"halv", r"kvart",
             r"två", r"tre", r"fyra", r"fem", r"sex", r"sju", r"åtta", r"nio", r"tio", r"elva",
             r"tolv", r"noll\w+", r"morgon\w*", r"förmiddag\w*", r"eftermiddag\w*", r"kväll\w*", r"natt\w*",
             r"monday", r"tuesday", r"wednesday", r"thursday", r"friday", r"saturday", r"sunday", r"tomorrow",
             r"tonight", r"weekend", r"two", r"three", r"four", r"five", r"six", r"seven", r"eight",
             r"nine", r"ten", r"eleven", r"twelve", r"half", r"quarter", r"morning", r"evening", r"night")
BACK_REFERENCE = _rx(r"det", r"den", r"dem", r"dom", r"detta", r"denna", r"det\s+passet", r"it", r"that",
                     r"them", r"this", r"one")
READ_WORDS = (r"vad|vilk\w*|hur|när|varför|var|finns|har|är|läs|visa|kolla|berätta|undrar|kan\s+man|kan\s+jag|"
              r"går\s+det|om|ifall|what|which|how|when|why|where|is|are|do\s+i|does|show|tell|check|read|list|"
              r"can\s+i|can\s+one|if")
READ_START = re.compile(r"^\W*(?:(?:ja|japp|okej|ok|yes|and|och|men)[\s,]+)?(?:" + READ_WORDS + r")\b", re.I)
READ_CLAUSE = re.compile(r"\b(?:och|men|and|but)\s+(?=(?:" + READ_WORDS + r")\b)", re.I)
SENTENCE_END = re.compile(r"([!?;]|\.(?!\d))")  # keep times such as 07.30 in their sentence


CALENDAR = _rx(r"kalender\w*", r"möte\w*", r"händelse\w*", r"bokning\w*", r"inbokad\w*", r"calendar\w*",
               r"meeting\w*", r"events?", r"appointment\w*")


def may_write_calendar(turn):
    """Whether this question might end in a calendar write (add_event, edit_event, delete_event). Such a
    round's draft is discarded, so its speech waits for the round's end; any other question is spoken
    sentence by sentence as the model writes it (agent_core._ask_round). Errs towards True: no words known,
    a change or booking word, a calendar word, or a yes to a question in Jarvis's last reply."""
    said = (turn.said or "").strip()
    if not said:
        return True
    if any(rx.search(said) for rx in (ACTION, BOOKING, CANCEL, CALENDAR)):
        return True
    return bool(AFFIRM.search(said)) and "?" in (turn.last_reply or "")


def _sentences(text):
    """(clause, end) pairs: sentences, with a read clause after och/men split off as its own."""
    parts = SENTENCE_END.split(" ".join((text or "").split()))
    for i in range(0, len(parts), 2):
        if not parts[i].strip():
            continue
        for clause in READ_CLAUSE.split(parts[i]):
            yield clause.strip(), parts[i + 1] if i + 1 < len(parts) else ""


def _negated(sentence, action):
    """A negation within three words before or after one of the action's matches, outside the match
    itself; the polite "kan du inte boka" does not count."""
    actions = [m.span() for m in action.finditer(sentence)]
    for neg in NEGATION.finditer(sentence):
        if any(a <= neg.start() < b for a, b in actions):
            continue                       # "ingen väckning", "inaktivera": the negation is the action
        if POLITE.search(sentence[:neg.start()]):
            continue
        for a, b in actions:
            gap = sentence[b:neg.start()] if neg.start() >= b else sentence[neg.end():a]
            if len(gap.split()) <= 3:
                return True
    return False


def _read(sentence):
    return bool(READ_START.search(sentence))


def _direct(text, topic, action):
    return any(topic.search(s) and action.search(s) and not _read(s) and not _negated(s, action)
               for s, _ in _sentences(text))


def _questions_other_routine(question, topic):
    return not topic.search(question) and any(t.search(question) for t, _ in ROUTINES.values() if t is not topic)


def allowed(turn, routine, tool):
    """True when this question's words ask for the routine's change; see the module docstring."""
    turn = turn or Turn()
    topic, action = ROUTINES[routine]
    said = " ".join(turn.said.split())
    reason = "no request for this routine"
    clauses = list(_sentences(said))
    if not said:
        reason = "no words for this question"
    elif CANCEL.search(said) or LEADING_NO.search(said):
        reason = "cancelled or declined"
    elif _direct(said, topic, action):
        return True
    elif any(_read(s) or end == "?" for s, end in clauses) or any(_negated(s, action) for s, _ in clauses) \
            or NEGATION.search(said) and len(said.split()) <= 6:
        reason = "a question or negation, not an answer"
    else:
        reply = list(_sentences(turn.last_reply))
        if not reply or reply[-1][1] != "?":
            reason = "the last reply asked nothing"
        else:
            question = reply[-1][0]
            before = reply[-2][0] if len(reply) > 1 else ""
            if _questions_other_routine(question, topic) or NEGATION.search(question):
                reason = "the last question was about something else"
            else:
                # The routine is what the question is about: named in it, in the sentence it refers back to,
                # or in Joel's previous turn. A question that offers no change ("vill du att jag kollar
                # tvättstugan?") authorizes none, unless Joel's previous turn asked for the change itself.
                about = bool(topic.search(question) or topic.search(turn.last_said) or topic.search(before) and (
                    BACK_REFERENCE.search(question) or DETAIL.search(question)))
                asked = _direct(turn.last_said, topic, action)
                if (asked or about and action.search(question)) and (AFFIRM.search(said) or DETAIL.search(said)):
                    return True
                reason = "an answer to a question about something else"
    print(f"spoken_change: {tool} refused: {reason}", file=sys.stderr)
    return False
