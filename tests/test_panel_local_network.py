"""panel_remote's hint when macOS blocks Python from the LAN (2026-11-01: "No route to host" while curl worked)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import panel_remote  # noqa: E402


class LocalNetworkHintTest(unittest.TestCase):
    def test_only_ehostunreach_on_macos(self):
        error = OSError(65, "No route to host")
        self.assertIn("Local Network", panel_remote.local_network_hint(error, "darwin"))
        self.assertIsNone(panel_remote.local_network_hint(error, "linux"))
        self.assertIsNone(panel_remote.local_network_hint(OSError(61, "Connection refused"), "darwin"))
        self.assertIsNone(panel_remote.local_network_hint(ValueError("x"), "darwin"))


if __name__ == "__main__":
    unittest.main()
