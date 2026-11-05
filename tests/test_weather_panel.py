#!/usr/bin/env python3
"""The weather card's days and the weather page (server/agents/weather.py panel(), styles/glass-v2/weather_view.h
and weather_page.py) without SMHI: a made-up forecast in SMHI's snow1g shape."""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server" / "agents"))
sys.path.insert(0, str(ROOT / "styles" / "glass-v2"))
import home_status  # noqa: E402
import weather  # noqa: E402

NOW = datetime(2026, 10, 29, 15, 0, tzinfo=weather.TZ)
SUN = {"next_rising": "2026-10-30T07:05:00+01:00", "next_setting": "2026-10-29T16:05:00+01:00"}


def point(start, hours, temp, rain=0.0, symbol=6, **more):
    end = start + timedelta(hours=hours)
    z = lambda t: t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"time": z(end), "intervalParametersStartTime": z(start),
            "data": {"air_temperature": temp, "precipitation_amount_mean": rain, "symbol_code": symbol,
                     "wind_speed": 4.2, "wind_speed_of_gust": 6.4, "relative_humidity": 89,
                     "probability_of_precipitation": 10, "air_pressure_at_mean_sea_level": 1021, **more}}


def forecast(rain_at=None):
    """48 hourly points from 14:00, then six-hour points for five more days."""
    series, t = [], NOW - timedelta(hours=1)
    for h in range(48):
        series.append(point(t, 1, 10 - abs(12 - h) / 3, rain=1.2 if rain_at == h else 0.0,
                            symbol=19 if rain_at == h else 1 if h % 24 < 3 else 6))
        t += timedelta(hours=1)
    for _ in range(20):
        series.append(point(t, 6, 5.0, rain=0.6, symbol=19))   # 0.6 mm in each whole 6-hour interval
        t += timedelta(hours=6)
    return {"timeSeries": series}


class WeatherServerTest(unittest.TestCase):
    def test_a_dry_afternoon(self):
        f = weather.panel(forecast(), NOW, home_status.SYMBOLS, SUN)
        days = [row.split("|") for row in f["w_days"].split(";")]
        self.assertEqual([d[0] for d in days], ["fre", "lör", "sön"])     # three days, from tomorrow
        week = [row.split("|") for row in f["wx_week"].split(";")]
        self.assertEqual(len(week), 7)
        self.assertEqual(week[0][0], "idag")
        self.assertEqual(week[0][4], "uppehåll")
        # a whole interval's amount counts once: four 6-hour intervals of 0.6 mm are 2.4 mm, not 14.4
        self.assertEqual(week[3][4], "2,4 mm")
        hours = [row.split("|") for row in f["wx_hours"].split(";")]
        self.assertEqual(len(hours), 24)
        self.assertEqual(hours[0][0], "nu")
        self.assertEqual([h[1] for h in hours[1:3]], ["sun", "moon"])   # clear at 16:00, and at 17:00 after 16:05
        self.assertEqual(week[0][1], hours[0][1])               # one sky for "nu", today and the header
        self.assertEqual((f["wx_sky"], f["wx_wet"]), ("Klart", "off"))     # the first hour is clear
        self.assertTrue(f["wx_sub"].startswith("Högst "))
        self.assertEqual(f["wx_facts"], "4 m/s, byar 6|89 %|10 %|1021 hPa")

    def test_rain_soon_and_night_icons(self):
        f = weather.panel(forecast(rain_at=2), NOW, home_status.SYMBOLS, SUN)
        self.assertEqual((f["wx_wet"], f["wx_sub"]), ("on", "Regn från 16:00 · ta paraply"))
        clear_night = weather.icon(1, night=True), weather.icon(3, night=True), weather.icon(1)
        self.assertEqual(clear_night, ("moon", "partly_night", "sun"))
        self.assertEqual(weather.panel({}, NOW, home_status.SYMBOLS)["w_days"], "")   # SMHI down: nothing shown

    def test_the_card_line_and_the_cached_field_fit(self):
        f = weather.panel(forecast(), NOW, home_status.SYMBOLS, SUN)
        limits = (ROOT / "bench/boot_cache.h").read_text()
        self.assertIn("512, 96};", limits)                            # w_days: the boot cache's ninth field
        self.assertLessEqual(len(f["w_days"].encode()), 96)


class WeatherViewTest(unittest.TestCase):
    def test_parsing_icons_and_the_chart_natively(self):
        source = r'''
#include "styles/glass-v2/weather_view.h"
#include <cassert>
#include <cstring>
int main() {
  using namespace weather_view;
  auto rows = table("fre|sun|10°|5°;lör|cloud|8°|4°");
  assert(rows.size() == 2 && rows[1].size() == 4 && rows[1][0] == "lör" && rows[0][3] == "5°");
  assert(table("").empty());
  assert(table("a|")[0].size() == 2 && table("a|")[0][1].empty());
  assert(std::strcmp(glyph("moon"), "\U000F0594") == 0 && std::strcmp(glyph("unknown"), glyph("cloud")) == 0);
  assert(x_at(0) == CHART.left && x_at(HOURS - 1) == CHART.right);
  float lo, hi; range({5.f, 9.f}, lo, hi);
  assert(lo == 4.f && hi == 10.f && y_at(10.f, lo, hi) == CHART.top && y_at(4.f, lo, hi) == CHART.bottom);
  assert(bar_h(0.f) == 0 && bar_h(0.04f) == 0 && bar_h(0.1f) == 3 && bar_h(9.f) == BAR_MAX);
  assert(deg(-0.4f) == "0°" && deg(9.6f) == "10°");
}
'''
        compiler = shutil.which("clang++") or shutil.which("g++")
        with tempfile.TemporaryDirectory() as d:
            cpp, binary = Path(d) / "weather.cpp", Path(d) / "weather"
            cpp.write_text(source)
            built = subprocess.run([compiler, "-std=c++17", "-fsanitize=address,undefined", f"-I{ROOT}", str(cpp),
                                    "-o", str(binary)], capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stderr)
            ran = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(ran.returncode, 0, ran.stderr)


class WeatherPageTest(unittest.TestCase):
    def test_the_card_opens_the_page_and_the_page_goes_back(self):
        import gen
        import weather_page
        card = next(w for w in gen.page["widgets"] if "w_temp" in json.dumps(w))
        self.assertEqual(card["obj"]["on_click"], weather_page.open_page())
        page = weather_page.page(gen._ui())
        self.assertIn("lvgl.page.show", json.dumps(page["widgets"][0]["obj"]["on_click"]))   # the back tap, first
        self.assertNotIn(weather_page.PAGE, gen.SWIPE_ORDER)       # not a tab: no swipe lands there
        cfg = gen.config("assets/bg_starry.png")
        fonts = {f["id"] for f in cfg["font"]}
        self.assertTrue({"mdi_w24", "mdi_w32", "mdi_w44"} <= fonts)
        self.assertFalse({"sun_icon", "rain_icon"} & {i["id"] for i in cfg["image"]})   # glyphs replaced the PNGs


if __name__ == "__main__":
    unittest.main()


class LineWidgetsTest(unittest.TestCase):
    def test_lambdas_reach_a_line_widget_through_its_obj(self):
        """ESPHome wraps a line (LvLineType); passed as lv_obj_t* without ->obj it only failed at the firmware
        compile, after every free check had passed (wx_curve, 2026-10-29)."""
        import re
        generated = subprocess.run([sys.executable, str(ROOT / "board.py"), "--voice", "jarvis_orb.py", "--remote",
                                    "http://example.invalid/assets"], cwd=ROOT / "styles/glass-v2",
                                   capture_output=True, text=True, check=True)
        text = generated.stdout
        lines = set(re.findall(r'"line": \{\s*"id": "(\w+)"', text))
        self.assertIn("wx_curve", lines)
        bare = sorted({m for m in re.findall(r"id\((\w+)\)(?!->obj)", text) if m in lines})
        self.assertEqual(bare, [])
