#!/usr/bin/env python3
"""What Jarvis shows on the panel while he speaks: a one-line summary of everything he says and shows, and one
or two cards under the orb, one per subject (the plane in one, the outing in the other). Standard library only.

The tool skarm, which the model calls in the same step as its finished answer; agent_core ends the
question there, so a card costs no extra round. A card has an optional title and rows of an icon and one
short line, at most MAX_CARDS cards and MAX_LINES lines in all, titles included, which is what fits under
the orb. The tool is lenient: since nothing goes back to the model, too much is cut rather than refused.

The icons are Material Design Icons 7.4.47 (ICONS, name -> code point). The panel compiles exactly these
glyphs into a font (styles/glass-v2/jarvis_orb.py reads ICONS from here), and an unknown name becomes
FALLBACK. The model picks them by what each row is about, so a new kind of answer needs no new code.

A lookup can offer a card of its own (offered): the news tool offers its headlines, one card per source with a
row per headline, since a long news answer was hard to read as rolling text and the model almost never called
skarm for it (2 of 18 answers over 250 characters, 27/10-3/11). An offered card shows only when the model did not
call skarm itself in that question; its own cards always win.

encoded() is what the panel reads (sensor.jarvis_chat's last_card): a line "@<stamp>" so that every new
card differs from the one before, "><summary>", then per card "#<title>" and per row
"<glyph>|<RRGGBB or empty>|<text>".
"""
import time

FONT_URL = "https://cdn.jsdelivr.net/npm/@mdi/font@7.4.47/fonts/materialdesignicons-webfont.ttf"
ICONS = {
    # time
    "calendar": "F00ED", "calendar-check": "F00EF", "clock-outline": "F0150", "alarm": "F0020",
    "timer-outline": "F051B", "bell": "F009A",
    # weather
    "weather-sunny": "F0599", "weather-night": "F0594", "weather-partly-cloudy": "F0595", "weather-cloudy": "F0590",
    "weather-rainy": "F0597", "weather-pouring": "F0596", "weather-snowy": "F0598", "weather-windy": "F059D",
    "weather-fog": "F0591", "weather-lightning": "F0593", "weather-sunset-up": "F059C",
    "weather-sunset-down": "F059B", "umbrella": "F054A", "thermometer": "F050F", "water-percent": "F058E",
    # travel
    "subway-variant": "F04DF", "bus": "F00E7", "train": "F052C", "tram": "F052D", "ferry": "F0213", "walk": "F0583",
    "bike": "F00A3", "car": "F010B", "taxi": "F04FF", "airplane": "F001D", "airplane-takeoff": "F05D5",
    "airplane-landing": "F05D4", "map-marker": "F034E", "map-marker-distance": "F08F0", "navigation": "F0390",
    "ticket": "F0516",
    # home
    "home": "F02DC", "door-open": "F081C", "lightbulb": "F0335", "air-filter": "F0D43", "smoke-detector": "F0392",
    "water-alert": "F1502", "washing-machine": "F072A", "fan": "F0210", "lock": "F033E", "shield-check": "F0565",
    "power-plug": "F06A5", "wifi": "F05A9", "wifi-off": "F05AA", "robot-vacuum": "F070D",
    # messages and media
    "cellphone": "F011C", "message-text": "F0369", "email": "F01EE", "phone": "F03F2", "link-variant": "F0339",
    "television": "F0502", "radio": "F0439", "music": "F075A", "headphones": "F02CB",
    # status
    "check-circle": "F05E0", "alert": "F0026", "alert-circle": "F0028", "information": "F02FC",
    "close-circle": "F0159", "help-circle": "F02D7", "star": "F04CE", "heart": "F02D1", "flag": "F023B",
    # knowledge and people
    "book-open-variant": "F14F7", "newspaper-variant": "F1001", "wikipedia": "F05AC", "school": "F0474",
    "earth": "F01E7", "rocket-launch": "F14DE", "satellite-variant": "F0471", "police-badge": "F1167",
    "account": "F0004", "account-group": "F0849", "gift": "F0E44", "cake-variant": "F00EB",
    # food and shopping
    "silverware-fork-knife": "F0A70", "coffee": "F0176", "glass-wine": "F0876", "cart": "F0110",
    "shopping": "F049A", "cash": "F0114", "credit-card": "F0FEF", "tag": "F04F9",
    # sport and leisure
    "soccer": "F04B8", "hockey-puck": "F0879", "run": "F070E", "dumbbell": "F01E6", "swim": "F04E3", "ski": "F1304",
    "movie-open": "F0FCE", "palette": "F03D8", "bank": "F0070", "drama-masks": "F0D02", "party-popper": "F1056",
    "pine-tree": "F0405", "flower": "F024A", "paw": "F03E9",
    # health and work
    "pill": "F0402", "hospital-box": "F02E0", "heart-pulse": "F05F6", "briefcase": "F00D6", "laptop": "F0322",
    "chart-line": "F012A", "dots-horizontal": "F01D8",
}
# Icons with a colour of their own, as elsewhere on the panel; the rest are white.
COLORS = {"subway-variant": "0A84FF", "check-circle": "30D158", "calendar-check": "30D158", "alert": "FF9F0A",
          "alert-circle": "FF453A", "close-circle": "FF453A", "weather-sunny": "FFD60A", "weather-lightning": "FFD60A",
          "star": "FFD60A", "weather-rainy": "64D2FF", "weather-pouring": "64D2FF", "weather-snowy": "64D2FF",
          "heart": "FF453A"}
FALLBACK = "dots-horizontal"
MAX_CARDS, MAX_LINES, MAX_TITLE, MAX_TEXT, MAX_SUMMARY = 2, 6, 40, 56, 80

TOOL = {"type": "function", "name": "skarm", "strict": False,
        "description": ("Visar en superkort sammanfattning av hela svaret och ett eller två kort på panelens "
                        "skärm medan svaret läses upp. Sammanfattningen är en rad på högst 70 tecken som tar med "
                        "allt du säger och visar, till exempel \"SAS58E över dig nu · Moderna museet söndag 10:30 "
                        "inlagt\". Korten är ett per ämne: "
                        "till exempel planet i ett kort och utflykten i ett annat. Ett kort har en kort rubrik "
                        "(den kan utelämnas på ett kort med en enda rad) och rader med en ikon och en rad text på "
                        "högst 50 tecken. Högst sex rader totalt, rubrikerna medräknade. Tider, siffror och namn "
                        "exakt som verktygen gav dem. För en resa: en rad med när han ska gå och när tåget eller "
                        "bussen avgår, till exempel \"Gå 09:58 · linje 10 avgår 10:03\". Anropa det i samma steg som dina "
                        "sista verktyg, till exempel bokningen och meddelandet till telefonen, eller tillsammans "
                        "med ditt färdiga svar om inga verktyg återstår. Kortet visas bara om de andra verktygen i "
                        "samma steg lyckas."),
        "parameters": {"type": "object", "required": ["summary"], "properties": {
            "summary": {"type": "string", "description": "Hela svaret i en rad, högst 70 tecken"},
            "cards": {"type": "array", "maxItems": MAX_CARDS, "items": {
                "type": "object", "required": ["rows"], "properties": {
                    "title": {"type": "string", "description": "Ämnet, till exempel Moderna museet"},
                    "rows": {"type": "array", "items": {
                        "type": "object", "required": ["icon", "text"], "properties": {
                            "icon": {"type": "string", "enum": list(ICONS)},
                            "text": {"type": "string", "description": "En rad, högst 50 tecken"}}}}}}}}}}


def _clean(value, limit):
    """One line without the separators the panel splits on, cut to limit characters."""
    text = " ".join(str(value or "").replace("|", "/").split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _fit(wanted):
    """(cards, lines) that fit under the orb: at most MAX_CARDS cards and MAX_LINES lines, titles included. Every
    subject gets its title and first row before one card takes the rest (four planes must not push the outing
    off the screen)."""
    cards, lines = [], 0
    wanted = wanted[:MAX_CARDS]
    for n, (title, rows) in enumerate(wanted):
        later = sum((1 if t else 0) + 1 for t, _ in wanted[n + 1:])
        room = MAX_LINES - lines - later - (1 if title else 0)
        if room <= 0:
            continue
        cards.append((title, rows[:room]))
        lines += (1 if title else 0) + len(rows[:room])
    return cards, lines


class Screen:
    """The cards of the question being answered; ToolAgent clears them as each question starts."""

    def __init__(self):
        self.summary, self.cards, self.offered = "", None, []

    def clear(self):
        self.summary, self.cards, self.offered = "", None, []

    def offer_news(self, source, titles):
        """The news tool's headlines, shown as a card when the model shows none (see the module's doc)."""
        rows = [("newspaper-variant", _clean(t, MAX_TEXT)) for t in titles if _clean(t, MAX_TEXT)]
        if rows:
            self.offered = [c for c in self.offered if c[0] != _clean(source, MAX_TITLE)]
            self.offered.append((_clean(source, MAX_TITLE), rows))

    def show(self, args):
        """The tool: keep what fits, and say what is shown."""
        summary = _clean((args or {}).get("summary"), MAX_SUMMARY)
        wanted = []
        for card in ((args or {}).get("cards") or [])[:MAX_CARDS]:
            if not isinstance(card, dict):
                continue
            rows = [(row.get("icon") if row.get("icon") in ICONS else FALLBACK, _clean(row.get("text"), MAX_TEXT))
                    for row in card.get("rows") or [] if isinstance(row, dict) and _clean(row.get("text"), MAX_TEXT)]
            if rows:
                wanted.append((_clean(card.get("title"), MAX_TITLE), rows))
        cards, lines = _fit(wanted)
        if not cards and not summary:
            return True, "Inget att visa: ange en sammanfattning, och kort med minst en rad ikon och text."
        self.summary, self.cards = summary, cards
        return False, (f"Visas på panelen: sammanfattningen, {len(cards)} kort, {lines} rader. Har du inte "
                       "skrivit ditt talade svar än, skriv det nu.")

    def encoded(self):
        """The summary and cards as the panel reads them, or "" without either. Without skarm, the offered cards."""
        cards = self.cards if self.cards is not None else _fit(self.offered)[0]
        if not cards and not self.summary:
            return ""
        out = [f"@{time.time():.3f}"] + ([">" + self.summary] if self.summary else [])
        for title, rows in cards:
            out.append("#" + title)
            out += [f"{chr(int(ICONS[icon], 16))}|{COLORS.get(icon, '')}|{text}" for icon, text in rows]
        return "\n".join(out)
