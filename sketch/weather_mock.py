"""Weather card + weather page mockup (2026-10-29), on real SMHI data with the panel's fonts and background.
The layout numbers here are the design to build from (gen.py's weather card, a page like Spelas nu).

Inputs, each fetched fresh (nothing is committed but this script):
  OUT/bg.png     the panel's baked background: tools/jarvis_ssh.sh user@jarvis.local 'cat ~/docker/homeassistant/config/www/hallpanel/bg.png'
  OUT/smhi.json  home_status.SMHI's point forecast (curl it)
  SHOT           a dashboard screenshot: tools/panel inspect
  MDI            the icon font gen.py's MDI URL names
  FONTS          styles/glass-v2/.esphome/font (Inter, cached by any ESPHome build)

  .venv/bin/python -I sketch/weather_mock.py OUT SHOT MDI FONTS   -> OUT/mock_card.png, OUT/mock_sheet.png

Translucent text is drawn on its own transparent layer and composited: drawn straight onto an RGBA picture its
alpha replaced the pixels and showed as full white.
"""
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont

S = Path(sys.argv[1])           # this folder
SHOT, MDI, FONTS = Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4])
TZ = ZoneInfo("Europe/Stockholm")
NOW = datetime(2026, 10, 29, 15, 0, tzinfo=TZ)
WHITE, SEC, BLUE, ORANGE = (255, 255, 255), (255, 255, 255, 153), (90, 170, 255), (255, 159, 67)
SHEET = (20, 24, 38)


def inter(w, s):
    return ImageFont.truetype(str(FONTS / f"Inter@{w}@False@v1.ttf"), s)


def icon_font(s):
    return ImageFont.truetype(str(MDI), s)


ICON = {"sun": "F0599", "moon": "F0594", "partly": "F0595", "cloud": "F0590", "rain": "F0597", "pour": "F0596",
        "snow": "F0598", "thunder": "F0593", "fog": "F0591", "sleet": "F067F"}


def glyph(kind):
    return chr(int(ICON[kind], 16))


def kind_of(sym, day=True):
    sym = int(sym)
    if sym in (1, 2):
        return "sun" if day else "moon"
    if sym in (3, 4):
        return "partly"
    if sym in (5, 6):
        return "cloud"
    if sym == 7:
        return "fog"
    if sym in (11, 21):
        return "thunder"
    if sym in (8, 9, 18, 19):
        return "rain"
    if sym in (10, 20):
        return "pour"
    if sym in (12, 13, 14, 22, 23, 24):
        return "sleet"
    return "snow"


SKY = {1: "Klart", 2: "Nästan klart", 3: "Växlande moln", 4: "Halvklart", 5: "Molnigt", 6: "Mulet", 7: "Dimma"}
DAYS = ["mån", "tis", "ons", "tors", "fre", "lör", "sön"]

data = json.loads((S / "smhi.json").read_text())["timeSeries"]
steps = []
for i, t in enumerate(data):
    when = datetime.fromisoformat(t["time"].replace("Z", "+00:00")).astimezone(TZ)
    nxt = datetime.fromisoformat(data[i + 1]["time"].replace("Z", "+00:00")) if i + 1 < len(data) else None
    hours = (nxt - datetime.fromisoformat(t["time"].replace("Z", "+00:00"))).total_seconds() / 3600 if nxt else 1
    steps.append((when, hours, t["data"]))
steps = [s for s in steps if s[0] >= NOW - timedelta(minutes=30)]
first = steps[0][2]

days = defaultdict(list)
for when, hours, d in steps:
    days[when.date()].append((when, hours, d))


def day_summary(rows):
    temps = [d["air_temperature"] for _, _, d in rows]
    # SMHI's amount is for the whole interval (server/agents/weather.py): summed as is, never times its hours
    rain = sum(d.get("precipitation_amount_mean", 0) for _, h, d in rows)
    gust = max(d.get("wind_speed_of_gust", 0) for _, _, d in rows)
    mid = min(rows, key=lambda r: abs(r[0].hour - 13))
    worst = max(rows, key=lambda r: r[2].get("precipitation_amount_mean", 0))
    sym = worst[2]["symbol_code"] if worst[2].get("precipitation_amount_mean", 0) >= 0.3 else mid[2]["symbol_code"]
    return max(temps), min(temps), rain, gust, sym


def deg(x):
    return f"{round(x)}°".replace("-", "−")


def mm(x):
    return f"{x:.1f}".replace(".", ",") + " mm" if x >= 0.1 else ""


wet = next(((w, d) for w, _, d in steps[:12] if d.get("precipitation_amount_mean", 0) >= 0.1), None)
line = SKY.get(int(first["symbol_code"]), "")
if wet:
    line = ("Regn" if kind_of(wet[1]["symbol_code"]) != "snow" else "Snö") + (
        " nu" if wet[0] <= NOW else f" från {wet[0]:%H:%M}")


# ---------------------------------------------------------------- the card, on the real dashboard
shot = Image.open(SHOT).convert("RGBA")
bg = Image.open(S / "bg.png").convert("RGBA")
card = (520, 24, 750, 254)
shot.paste(bg.crop(card), card[:2])
layer = Image.new("RGBA", shot.size, (0, 0, 0, 0))
d = ImageDraw.Draw(layer)
x0, y0, W = 536, 40, 198
d.text((x0, y0), "Kungsholmen", font=inter(600, 15), fill=WHITE)
d.text((x0 - 3, y0 + 14), deg(first["air_temperature"]), font=inter(400, 56), fill=WHITE)
d.text((x0 + W, y0 + 22), glyph(kind_of(first["symbol_code"])), font=icon_font(44), fill=WHITE, anchor="ra")
d.text((x0, y0 + 84), line, font=inter(600, 14), fill=WHITE)
if wet:
    d.text((x0, y0 + 104), "Ta paraply", font=inter(600, 14), fill=ORANGE)
d.line((x0, y0 + 128, x0 + W, y0 + 128), fill=(255, 255, 255, 46))
upcoming = [k for k in sorted(days) if k > NOW.date()][:3]
col = W / 3
for i, day in enumerate(upcoming):
    hi, lo, rain, gust, sym = day_summary(days[day])
    cx = x0 + col * i + col / 2
    d.text((cx, y0 + 138), DAYS[day.weekday()], font=inter(500, 13), fill=SEC, anchor="ma")
    d.text((cx, y0 + 154), glyph(kind_of(sym)), font=icon_font(24), fill=WHITE, anchor="ma")
    hi_t, lo_t = deg(hi), deg(lo)
    f = inter(600, 14)
    wh, wl = f.getlength(hi_t), f.getlength(" " + lo_t)
    d.text((cx - (wh + wl) / 2, y0 + 182), hi_t, font=f, fill=WHITE)
    d.text((cx - (wh + wl) / 2 + wh, y0 + 182), " " + lo_t, font=f, fill=SEC)
shot = Image.alpha_composite(shot, layer)
shot.convert("RGB").save(S / "mock_card.png")
shot.crop((500, 0, 1024, 280)).resize((1048, 560), Image.LANCZOS).convert("RGB").save(S / "mock_card_zoom.png")

# ---------------------------------------------------------------- the sheet, a full page like Spelas nu
page = Image.new("RGBA", (1024, 600), (*SHEET, 255))
layer = Image.new("RGBA", page.size, (0, 0, 0, 0))
d = ImageDraw.Draw(layer)
M = 40
d.text((M, 32), "Kungsholmen", font=inter(600, 16), fill=SEC)
d.text((M - 3, 52), deg(first["air_temperature"]), font=inter(400, 56), fill=WHITE)
d.text((M + 104, 60), glyph(kind_of(first["symbol_code"])), font=icon_font(48), fill=WHITE)
d.text((M + 166, 62), SKY.get(int(first["symbol_code"]), "Mulet"), font=inter(600, 22), fill=WHITE)
today_hi, today_lo = day_summary(days[NOW.date()])[:2]
second = line + " · ta paraply" if wet else f"Högst {deg(today_hi)}, i natt ner mot {deg(today_lo)}"
d.text((M + 166, 92), second, font=inter(500, 16), fill=ORANGE if wet else SEC)
facts = [("Vind", f"{round(first['wind_speed'])} m/s, byar {round(first['wind_speed_of_gust'])}"),
         ("Luftfuktighet", f"{round(first['relative_humidity'])} %"),
         ("Regnrisk", f"{round(first.get('probability_of_precipitation', 0))} %"),
         ("Lufttryck", f"{round(first['air_pressure_at_mean_sea_level'])} hPa")]
fx = 1024 - M - 2 * 170
for i, (k, v) in enumerate(facts):
    x, y = fx + (i % 2) * 170, 34 + (i // 2) * 44
    d.text((x, y), k, font=inter(500, 13), fill=SEC)
    d.text((x, y + 17), v, font=inter(600, 16), fill=WHITE)

# next 24 hours: temperature curve, hourly ticks, rain bars
hours24 = [s for s in steps if s[0] < NOW + timedelta(hours=24)][:24]
top, bottom = 196, 300
left, right = M + 8, 1024 - M - 8
d.text((M, 150), "Närmaste dygnet", font=inter(600, 16), fill=WHITE)
temps = [h[2]["air_temperature"] for h in hours24]
lo_t, hi_t = min(temps) - 1, max(temps) + 1
step = (right - left) / (len(hours24) - 1)
pts = [(left + i * step, bottom - (t - lo_t) / (hi_t - lo_t) * (bottom - top)) for i, t in enumerate(temps)]
for i, (when, h, dd) in enumerate(hours24):
    r = dd.get("precipitation_amount_mean", 0)
    if r > 0:
        bh = min(60, r * 30)
        x = left + i * step
        d.rounded_rectangle((x - 9, 380 - bh, x + 9, 380), radius=3, fill=(*BLUE, 150))
d.line((left, 380, right, 380), fill=(255, 255, 255, 30))
d.line(pts, fill=WHITE, width=3, joint="curve")
for i, ((x, y), (when, h, dd)) in enumerate(zip(pts, hours24)):
    if i % 3 == 0:
        d.ellipse((x - 4, y - 4, x + 4, y + 4), fill=WHITE)
        d.text((x, y - 10), deg(dd["air_temperature"]), font=inter(600, 14), fill=WHITE, anchor="mb")
        d.text((x, 318), glyph(kind_of(dd["symbol_code"], 7 <= when.hour <= 16)), font=icon_font(22), fill=WHITE,
               anchor="ma")
        d.text((x, 386), "nu" if i == 0 else f"{when:%H}", font=inter(500, 13), fill=SEC, anchor="ma")
d.text((right, 150), "regn mm/h", font=inter(500, 13), fill=(*BLUE, 255), anchor="ra")

# 7 days
d.line((M, 418, 1024 - M, 418), fill=(255, 255, 255, 30))
week = [k for k in sorted(days) if k >= NOW.date()][:7]
colw = (1024 - 2 * M) / len(week)
for i, day in enumerate(week):
    hi, lo, rain, gust, sym = day_summary(days[day])
    cx = M + colw * i + colw / 2
    name = "idag" if day == NOW.date() else DAYS[day.weekday()]
    d.text((cx, 432), name, font=inter(600, 15), fill=WHITE if i == 0 else SEC, anchor="ma")
    d.text((cx, 456), glyph(kind_of(sym)), font=icon_font(32), fill=WHITE, anchor="ma")
    f = inter(600, 16)
    a, b = deg(hi), " " + deg(lo)
    wa, wb = f.getlength(a), f.getlength(b)
    d.text((cx - (wa + wb) / 2, 500), a, font=f, fill=WHITE)
    d.text((cx - (wa + wb) / 2 + wa, 500), b, font=f, fill=SEC)
    d.text((cx, 526), mm(rain) or "uppehåll", font=inter(500, 13), fill=(*BLUE, 255) if rain >= 0.1 else SEC, anchor="ma")
    d.text((cx, 546), f"byar {round(gust)}", font=inter(500, 13), fill=SEC, anchor="ma")
page = Image.alpha_composite(page, layer)
page.convert("RGB").save(S / "mock_sheet.png")
print("ok")
