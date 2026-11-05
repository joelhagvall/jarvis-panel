"""tools/panel throughput-history: earlier throughput reports side by side. No device."""
import json
import sys
import tempfile
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import throughput_history  # noqa: E402


class ThroughputHistoryTest(unittest.TestCase):
    def test_newest_first_and_a_broken_report_is_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            for run, top in (("run-20261028-102112", 21.04), ("run-20261028-203252", 46.12)):
                (Path(d) / run).mkdir()
                (Path(d) / run / "throughput.json").write_text(json.dumps({"summary": {
                    "animation.refresh_interval_ms": {"median": 16.5, "p95": 18.4, "max": top},
                    "status_roundtrip_ms": {"p95": 39.29}}}))
            (Path(d) / "run-x").mkdir()
            (Path(d) / "run-x" / "throughput.json").write_text("{")
            rows = throughput_history.history(Path(d), 14)
        self.assertEqual([r["run"] for r in rows], ["run-20261028-203252", "run-20261028-102112"])
        self.assertEqual(rows[0]["animation.refresh_interval_ms.max"], 46.1)
        self.assertEqual(rows[0]["status_roundtrip_ms.p95"], 39.3)

    def test_a_missing_metric_is_none(self):
        self.assertIsNone(throughput_history.row({"summary": {}})["animation.refresh_interval_ms.max"])


if __name__ == "__main__":
    unittest.main()
