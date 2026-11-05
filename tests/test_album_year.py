"""The album year for the Musik page: only validated Spotify album ids, the year from the page's head, a bounded
cache that never loops on a failure. No network."""
import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace as NS
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("album_year", ROOT / "ha/custom_components/jarvis_agent/album_year.py")
album_year = importlib.util.module_from_spec(spec)
spec.loader.exec_module(album_year)


class AlbumYearTest(unittest.TestCase):
    def test_only_album_uris_and_the_year_from_the_meta_tag(self):
        self.assertEqual(album_year.album_id("spotify:album:2G4AUqfwxcV1UdQjm2ouYr"), "2G4AUqfwxcV1UdQjm2ouYr")
        for bad in ("spotify:track:2G4AUqfwxcV1UdQjm2ouYr", "spotify:album:../../x", "https://evil.test", "", None):
            self.assertEqual(album_year.album_id(bad), "")
        page = b'<head><meta name="music:release_date" content="2014-05-16"/></head>'
        self.assertEqual(album_year.release_year(page), "2014")
        self.assertEqual(album_year.release_year(b'<meta name="music:release_date" content="1999"/>'), "1999")
        self.assertEqual(album_year.release_year(b"<html>no date</html>"), "")

    def test_one_lookup_per_album_and_a_failure_is_not_retried_at_once(self):
        tasks = []
        hass = NS(async_create_task=lambda coro, name: tasks.append(coro))
        years = album_year.AlbumYear(hass, lambda: None)
        uri = "spotify:album:2G4AUqfwxcV1UdQjm2ouYr"
        self.assertEqual(years.year(uri), "")
        self.assertEqual(years.year(uri), "")
        self.assertEqual(len(tasks), 1)                       # one fetch while it runs
        tasks[0].close()
        years.busy.clear()
        years.years["2G4AUqfwxcV1UdQjm2ouYr"] = ("", album_year.time.monotonic(), 1)
        self.assertEqual(years.year(uri), "")
        self.assertEqual(len(tasks), 1)                       # a failure waits RETRY_S (a minute) ...
        years.years["2G4AUqfwxcV1UdQjm2ouYr"] = ("", album_year.time.monotonic() - 61, 1)
        self.assertEqual(years.year(uri), ""); self.assertEqual(len(tasks), 2)   # ... then tries again
        tasks[1].close(); years.busy.clear()
        years.years["2G4AUqfwxcV1UdQjm2ouYr"] = ("", 0, album_year.TRIES)
        self.assertEqual(years.year(uri), ""); self.assertEqual(len(tasks), 2)   # three tries, then it rests
        years.years["2G4AUqfwxcV1UdQjm2ouYr"] = ("2014", 0, 1)
        self.assertEqual(years.year(uri), "2014")
        self.assertEqual(years.year("spotify:track:x"), "")
        self.assertEqual(len(tasks), 2)


if __name__ == "__main__":
    unittest.main()
