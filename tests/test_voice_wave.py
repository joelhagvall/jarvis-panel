"""The panel-drawn voice wave (styles/glass-v2/voice_wave.h) matches the Jarvis line's drawn talk_*.png frames."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'orbs'))

PROGRAM = r'''
#include <chrono>
#include <cstdio>
#include <vector>
#include "voice_wave.h"
int main(int argc, char **argv) {
  std::FILE *in = std::fopen(argv[1], "rb");
  std::vector<uint8_t> hollow(220 * 220 * 3);
  if (std::fread(hollow.data(), 1, hollow.size(), in) != hollow.size()) return 2;
  std::fclose(in);
  std::vector<float> scratch(voice_wave::SCRATCH_FLOATS);   // exactly the size the panel allocates
  std::FILE *out = std::fopen(argv[2], "wb");
  voice_wave::tables();   // built once at boot on the panel, not per frame
  double slowest = 0;
  for (int k = 0; k < 72; k += 9) {
    std::vector<uint8_t> frame = hollow;
    voice_wave::Params p;
    p.t = k / 72.0f;
    const float speak = 0.75f + 0.25f * std::sin(4 * voice_wave::PI * p.t);
    p.amp[0] = p.amp[1] = p.amp[2] = speak;
    voice_wave::render(frame.data(), p, scratch.data());
    std::vector<uint8_t> again = hollow;
    const auto began = std::chrono::steady_clock::now();
    for (int r = 0; r < 20; ++r) { again = hollow; voice_wave::render(again.data(), p, scratch.data()); }
    slowest = std::max(slowest, std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - began).count() / 20);
    std::fwrite(frame.data(), 1, frame.size(), out);
  }
  std::fclose(out);
  std::printf("%.3f\n", slowest);
}
'''


def planes(raw):
    import numpy as np
    colors = np.frombuffer(raw[:220 * 220 * 2], '<u2').astype(int)
    rgb = np.stack([(colors >> 11) * 255 / 31, ((colors >> 5) & 63) * 255 / 63, (colors & 31) * 255 / 31], -1)
    return rgb, np.frombuffer(raw[220 * 220 * 2:], 'u1').astype(int)


@unittest.skipUnless(shutil.which("clang++") or shutil.which("g++"), "C++ compiler unavailable")
class VoiceWaveTest(unittest.TestCase):
    def test_drawn_wave_matches_the_drawn_frames(self):
        import atlas
        import numpy as np
        from PIL import Image
        with tempfile.TemporaryDirectory(prefix="hallpanel-wave-") as tmp:
            tmp = Path(tmp)
            (tmp / "wave.cpp").write_text(PROGRAM)
            # AddressSanitizer: a too-small scratch once overflowed on the panel's heap (2026-10-27).
            subprocess.run([shutil.which("clang++") or shutil.which("g++"), "-std=c++17", "-O1", "-Wall", "-Werror",
                            "-fsanitize=address,undefined", "-I", str(ROOT / "styles/glass-v2"), str(tmp / "wave.cpp"),
                            "-o", str(tmp / "wave")], check=True, capture_output=True)
            frames = atlas.folder(atlas.PANEL_VERSION)
            with Image.open(frames / 'hollow_00.png') as hollow:
                (tmp / 'hollow.raw').write_bytes(atlas.rgb565a8(hollow.convert('RGBA')))
            result = subprocess.run([str(tmp / "wave"), str(tmp / 'hollow.raw'), str(tmp / 'out.raw')],
                                    check=True, capture_output=True, text=True)
            drawn = (tmp / 'out.raw').read_bytes()
        frame = 220 * 220 * 3
        for i, k in enumerate(range(0, 72, 9)):
            with Image.open(frames / f'talk_{k:02d}.png') as talk:
                ref_rgb, ref_a = planes(atlas.rgb565a8(talk.convert('RGBA')))
            rgb, a = planes(drawn[i * frame:(i + 1) * frame])
            # The line lies on the painting, so compare what shows: colour times alpha, everywhere.
            error = np.abs(rgb * a[:, None] - ref_rgb * ref_a[:, None]).max(1) / 255
            lit = ref_a >= 24
            with self.subTest(frame=k):
                # The worst channel per pixel, in 8-bit levels; one RGB565 red/blue step is 8 levels.
                # The slope-corrected distance is 17x cheaper than measuring to sampled points, which took
                # 20 ms per frame on the panel; it costs a few levels at the steepest peaks, where the free
                # line's halo comes out slightly dim. 2026-10-28: 6.8 at worst searching 7 columns, 4.2 with
                # the three columns the tangent aims at (voice_wave.h), which is also faster.
                self.assertLess(error[lit].mean(), 6)
                self.assertLess(np.percentile(error, 99.9), 75)
                self.assertLess(np.abs(a - ref_a).mean(), 1.5)
                self.assertLess(np.abs(a - ref_a)[lit].mean(), 4)     # 2.8 at worst
        print(f"slowest render on this Mac: {result.stdout.strip()} ms", file=sys.stderr)


if __name__ == "__main__":
    unittest.main()
