"""tools/panel wave without a device: it runs speaking_live, keeps to two captures a second, always restores,
and writes the frames, a GIF and a sheet of the line's box."""
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import panel_wave


class FakeClient:
    def __init__(self, fail_at=None):
        self.modes, self.shots, self.fail_at = [], 0, fail_at

    def test_mode(self, mode, duration_ms):
        self.modes.append((mode, duration_ms))

    def screenshot(self, path, lossless=False):
        from PIL import Image
        assert lossless
        self.shots += 1
        if self.shots == self.fail_at:
            raise OSError('lost')
        Image.new('RGB', (1024, 600), (self.shots * 9 % 255, 20, 40)).save(path)

    def status(self):
        return {'orb_prefetch_last_mix_us': 4000, 'orb_wave_held': 2, 'build_id': 'b'}


class WaveTest(unittest.TestCase):
    def test_captures_the_orb_box_twice_a_second_and_restores(self):
        now = [0.0]
        clock = lambda: now[0]
        def sleep(seconds):
            now[0] += max(seconds, 0.0)
        client = FakeClient()
        with tempfile.TemporaryDirectory() as tmp:
            shots, status = panel_wave.capture(client, Path(tmp), 3, clock, sleep)
            panel_wave.assemble(shots, Path(tmp))
            from PIL import Image
            with Image.open(shots[0]) as frame:
                self.assertEqual(frame.size, (280, 180))
            self.assertTrue((Path(tmp) / 'wave.gif').is_file() and (Path(tmp) / 'sheet.png').is_file())
        self.assertEqual(client.modes[0], ('speaking_live', 6000))
        self.assertEqual(client.modes[-1], ('restore', 100))
        self.assertEqual(len(shots), 6)          # 3 s at one capture per 0.52 s
        self.assertEqual(status['orb_wave_held'], 2)

    def test_a_lost_capture_still_restores_the_page(self):
        client = FakeClient(fail_at=2)
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(OSError):
            panel_wave.capture(client, Path(tmp), 3, sleep=lambda s: None)
        self.assertEqual(client.modes[-1], ('restore', 100))


class MeasureTest(unittest.TestCase):
    def test_summary_reports_render_time_rate_and_held_share(self):
        samples = [{'t': i * 0.5, 'mix_us': 30000 + 1000 * i, 'decoded': 10 * i, 'held': 20 * i,
                    'presented': 30 * i, 'page': 'page_orb', 'build_id': 'b'} for i in range(11)]
        summary = panel_wave.summarize(samples)
        self.assertEqual((summary['wave_images_per_s'], summary['presented_per_s'], summary['held_share']),
                         (20.0, 60.0, 0.667))
        self.assertEqual((summary['render_ms_p50'], summary['render_ms_max']), (35.0, 40.0))

    def test_a_measurement_off_the_voice_page_is_refused(self):
        samples = [{'t': i, 'mix_us': 1, 'decoded': i, 'held': 0, 'presented': i, 'page': 'page_orb',
                    'build_id': 'b'} for i in range(4)]
        samples[2]['page'] = 'page_glass_v2'   # real voice or a finger took over
        with self.assertRaises(ValueError):
            panel_wave.summarize(samples)


if __name__ == '__main__':
    unittest.main()
