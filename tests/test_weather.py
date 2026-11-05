"""Full SMHI horizon and date-aware outing weather, with offline fixtures only."""
import json
import sys
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server" / "agents"))
import agent_core
import home_status
import weather

NOW = datetime(2026, 10, 23, 7, 30, tzinfo=timezone.utc)


def point(at, temp, rain=0, probability=0, hours=1, symbol=1):
    end = datetime.fromisoformat(at)
    return {"time": end.isoformat(), "intervalParametersStartTime": (end - timedelta(hours=hours)).isoformat(),
            "data": {"air_temperature": temp, "wind_speed": 3, "wind_speed_of_gust": 6,
                     "precipitation_amount_mean": rain, "probability_of_precipitation": probability,
                     "symbol_code": symbol}}


class WeatherTest(unittest.TestCase):
    def setUp(self):
        self.raw = {"createdTime": "2026-10-23T07:01:00Z", "referenceTime": "2026-10-23T07:00:00Z",
                    "timeSeries": [point("2026-10-23T08:00:00+00:00", 10, 1, 90, symbol=19),
                                   point("2026-10-24T08:00:00+00:00", 7),
                                   point("2026-10-24T11:00:00+00:00", 12),
                                   point("2026-10-24T17:00:00+00:00", 9, 2, 80, symbol=19)] +
                    [point(f"2026-10-{day}T12:00:00+00:00", 8, hours=12, symbol=4) for day in range(25, 32)]}

    def summary(self, **args):
        return weather.summarize(self.raw, NOW, home_status.SYMBOLS, **args)

    def test_default_exposes_whole_available_horizon_not_just_twelve_hours(self):
        error, text = self.summary()
        self.assertFalse(error, text)
        result = json.loads(text)
        self.assertEqual(len(result["days"]), 9)
        self.assertEqual(result["days"][-1]["date"], "2026-10-31")
        self.assertTrue(result["days"][-1]["partial_coverage"])
        self.assertNotIn("time_points", result["days"][0])
        self.assertEqual(result["created_at"], self.raw["createdTime"])

    def test_tomorrow_is_not_todays_rain_and_supports_daytime_window(self):
        error, text = self.summary(from_date="2026-10-24", from_time="10:00", to_time="16:00")
        self.assertFalse(error, text)
        day = json.loads(text)["days"][0]
        self.assertEqual(day["sampled_temperature_min_c"], 7)
        self.assertEqual(day["sampled_temperature_max_c"], 12)
        self.assertEqual(day["conditions"], ["klart"])
        self.assertEqual(day["precipitation_intervals"], [])
        self.assertEqual(len(day["time_points"]), 2)

    def test_evening_rain_has_real_interval_and_probability(self):
        error, text = self.summary(from_date="2026-10-24", from_time="18:00", to_time="22:00")
        self.assertFalse(error, text)
        rain = json.loads(text)["days"][0]["precipitation_intervals"][0]
        self.assertEqual((rain["from"], rain["to"]), ("2026-10-24T18:00:00+02:00", "2026-10-24T19:00:00+02:00"))
        self.assertEqual((rain["mean_mm_in_full_interval"], rain["probability_percent"]), (2, 80))

    def test_cross_midnight_amount_is_not_invented_as_hourly_or_daily_total(self):
        self.raw["timeSeries"] = [point("2026-10-24T04:00:00+00:00", 8, 6, 70, hours=6)]
        error, text = self.summary(from_date="2026-10-24")
        self.assertFalse(error, text)
        rain = json.loads(text)["days"][0]["precipitation_intervals"][0]
        self.assertEqual(rain["from"], "2026-10-24T00:00:00+02:00")
        self.assertEqual(rain["mean_mm_in_full_interval"], 6)
        self.assertNotIn("mm_per_hour", text)

    def test_valid_temperature_timestamp_is_not_shifted_to_interval_start(self):
        self.raw["timeSeries"] = [point("2026-10-23T23:00:00+00:00", 7, hours=6)]
        error, text = self.summary(from_date="2026-10-24")
        self.assertFalse(error, text)
        self.assertEqual(json.loads(text)["days"][0]["time_points"][0]["time"], "2026-10-24T01:00:00+02:00")

    def test_sparse_forecast_points_stay_sparse_and_use_winter_time(self):
        error, text = self.summary(from_date="2026-10-26")
        self.assertFalse(error, text)
        points = json.loads(text)["days"][0]["time_points"]
        self.assertEqual(len(points), 1)
        self.assertEqual(points[0]["time"], "2026-10-26T13:00:00+01:00")

    def test_short_window_between_sparse_points_gets_the_nearest_one(self):
        # Joel, 2026-11-01: "lördag kväll" six days ahead fell between SMHI's 6-12 h points and got no weather.
        error, text = self.summary(from_date="2026-10-26", from_time="17:00", to_time="19:00")
        self.assertFalse(error, text)
        day = json.loads(text)["days"][0]
        self.assertTrue(day["nearest_point_outside_window"])
        self.assertEqual(day["time_points"][0]["time"], "2026-10-26T13:00:00+01:00")
        self.assertEqual(day["sampled_temperature_max_c"], 8)
        # but never further than NEAREST_H from the window
        self.assertTrue(self.summary(from_date="2026-10-26", from_time="21:00", to_time="23:00")[0])

    def test_missing_values_are_unknown_not_clear_dry_or_huge_temperatures(self):
        self.raw["timeSeries"][-1]["data"].update({"wind_speed": 9999, "symbol_code": 9999,
                                                   "probability_of_precipitation": 9999})
        error, text = self.summary(from_date="2026-10-31")
        self.assertFalse(error, text)
        day = json.loads(text)["days"][0]
        self.assertIsNone(day["wind_max_ms"])
        self.assertIsNone(day["precipitation_probability_max_percent"])
        self.assertEqual(day["conditions"], [])
        self.raw["timeSeries"][-1]["data"]["air_temperature"] = 9999
        self.assertTrue(self.summary(from_date="2026-10-31")[0])

    def test_beyond_horizon_or_invalid_window_never_substitutes_today(self):
        for args in ({"from_date": "2026-11-15"}, {"from_date": "2026-10-22"},
                     {"from_date": "tomorrow"}, {"from_time": "20:00", "to_time": "10:00"},
                     {"from_date": "2026-10-25", "to_date": "2026-10-24"},
                     {"from_date": "2026-10-23", "to_date": "2026-12-23"}):
            with self.subTest(args=args):
                self.assertTrue(self.summary(**args)[0])

    def test_partial_range_marks_unavailable_days(self):
        error, text = self.summary(from_date="2026-10-30", to_date="2026-11-02")
        self.assertFalse(error, text)
        days = json.loads(text)["days"]
        self.assertEqual([d["available"] for d in days], [True, True, False, False])

    def test_forecast_reuses_warm_and_panel_cache_without_network(self):
        home = home_status.Home()
        home.cache[home_status.SMHI] = (time.monotonic(), self.raw)
        with patch.object(home_status, "get_json", side_effect=AssertionError("cache was not reused")), \
             patch.object(home_status, "datetime", wraps=datetime) as clock:
            clock.now.return_value = NOW
            error, text = home.forecast(from_date="2026-10-24")
        self.assertFalse(error, text)

    def test_compound_weather_question_gets_the_same_verified_tomorrow_data_without_fetching(self):
        home = home_status.Home()
        home.cache[home_status.SMHI] = (time.monotonic(), self.raw)
        with patch.object(home_status, "get_json", side_effect=AssertionError("network on question path")):
            forecast, age = home.prompt_forecast("Hur blir vädret imorgon och behöver jag paraply?", NOW)
        data = json.loads(forecast)
        self.assertEqual([d["date"] for d in data["days"]], ["2026-10-24"])
        self.assertEqual(data["days"][0]["precipitation_probability_max_percent"], 80)
        self.assertEqual(data["days"][0]["sampled_temperature_max_c"], 12)
        self.assertEqual(data["source"], "SMHI SNOW1g")
        self.assertIn("time_points", data["days"][0])
        self.assertEqual(age, 0)

    def test_prompt_weather_never_waits_for_missing_stale_or_unrelated_data(self):
        home = home_status.Home()
        with patch.object(home_status, "get_json", side_effect=AssertionError("network")):
            self.assertIsNone(home.prompt_forecast("Behöver jag paraply imorgon?", NOW))
            home.cache[home_status.SMHI] = (time.monotonic() - 901, self.raw)
            self.assertIsNone(home.prompt_forecast("Behöver jag paraply imorgon?", NOW))
            home.cache[home_status.SMHI] = (time.monotonic(), self.raw)
            for text in ("Vad har jag i kalendern?", "Hur blir vädret på lördag?", "Väder den 31 oktober?"):
                self.assertIsNone(home.prompt_forecast(text, NOW))
            home.cache[home_status.SMHI] = (time.monotonic(), {})
            self.assertIsNone(home.prompt_forecast("Weather tomorrow?", NOW))

    def test_network_failure_does_not_fall_back_to_today(self):
        with patch.object(home_status, "get_json", side_effect=TimeoutError):
            error, text = home_status.Home().forecast(from_date="2026-10-24")
        self.assertTrue(error)
        self.assertIn("Använd inte dagens väder", text)

    def test_tool_is_available_without_home_assistant_and_read_only(self):
        with patch.dict(home_status.os.environ, {"JARVIS_HOME_TOKEN_FILE": "", "JARVIS_TV_MCP": ""}), \
             patch.object(agent_core.lookup, "jina_key", return_value=None):
            agent = agent_core.ToolAgent()
            agent._init_tools(False, None, None, None)
            agent._ensure_tools()
        self.assertIn("vader", agent.read_only)
        self.assertNotIn("hemmet", [t["name"] for t in agent.tools])


if __name__ == "__main__":
    unittest.main()
