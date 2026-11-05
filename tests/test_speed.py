"""server/speed.py: the turntable's 33/45, by Soloist's streams' links to the pitch filter. No PipeWire."""
import json
import sys
from pathlib import Path
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
import speed  # noqa: E402


def node(i, name):
    return {"id": i, "type": "PipeWire:Interface:Node", "info": {"props": {"node.name": name}}}


def link(out, into):
    return {"id": 900 + out, "type": "PipeWire:Interface:Link", "info": {"output-node-id": out, "input-node-id": into}}


class SpeedTest(unittest.TestCase):
    def test_state_reads_the_links(self):
        base = [node(32, "jbl"), node(54, "jbl45"), node(46, "spotify"), node(47, "spotify")]
        self.assertEqual(speed.state(base + [link(47, 32)]), {"rpm": 33, "available": True, "streams": 2})
        self.assertEqual(speed.state(base + [link(47, 54)])["rpm"], 45)
        self.assertFalse(speed.state([node(32, "jbl")])["available"])

    def test_45_needs_the_filter_and_moves_every_spotify_stream(self):
        calls = []
        def run(args, **kw):
            calls.append(args)
            dump = [node(32, "jbl"), node(46, "spotify"), node(47, "spotify")] + ([node(54, "jbl45")] if filt else [])
            return mock.Mock(stdout=json.dumps(dump))
        filt = False
        with mock.patch.object(speed.subprocess, "run", side_effect=run):
            self.assertEqual(speed.set_speed(45)["error"], "Pitchfiltret körs inte")
            self.assertFalse(any(c[0] == "pw-metadata" for c in calls))
            filt = True
            with mock.patch.object(speed, "state", return_value={"rpm": 45}), mock.patch("time.sleep"):
                speed.set_speed(45)
        moved = [c for c in calls if c[0] == "pw-metadata"]
        self.assertEqual(moved, [["pw-metadata", "46", "target.object", "jbl45"], ["pw-metadata", "47", "target.object", "jbl45"]])
        with self.assertRaises(ValueError):
            speed.set_speed(78)


if __name__ == "__main__":
    unittest.main()
