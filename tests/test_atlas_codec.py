"""Compile and execute the actual firmware decoder against valid and hostile fragmented streams."""
from array import array
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "orbs"))
import atlas


@unittest.skipUnless(shutil.which("clang++") or shutil.which("g++"), "C++ compiler unavailable")
class AtlasCodecTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="hallpanel-codec-")
        cls.binary = Path(cls.tmp.name) / "codec"
        source = Path(cls.tmp.name) / "codec.cpp"
        source.write_text(r'''
#include <iostream>
#include <iterator>
#include <vector>
#include "atlas_codec.h"
int main(int argc, char **argv) {
  size_t words = std::stoul(argv[1]), chunk = std::stoul(argv[2]);
  std::vector<uint8_t> input((std::istreambuf_iterator<char>(std::cin)), {});
  std::vector<uint16_t> output(words + 2, 0x5a5a);
  orb_atlas_ns::RleDecoder decoder(output.data() + 1, words);
  size_t at = 0, guard = 0;
  while (at < input.size() || !decoder.done()) {
    const size_t size = std::min(chunk, input.size() - at);
    int used = decoder.feed(input.data() + at, size, 17);
    if (used < 0) return 3;
    at += used;
    if (++guard > input.size() + words + 100) return 2;
  }
  if (output.front() != 0x5a5a || output.back() != 0x5a5a) return 4;
  std::cout.write(reinterpret_cast<char *>(output.data() + 1), words * 2);
}
''')
        subprocess.run([shutil.which("clang++") or shutil.which("g++"), "-std=c++17", "-O2",
                        "-I", str(ROOT / "styles/glass-v2"), str(source), "-o", str(cls.binary)], check=True,
                       capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def decode(self, data, words, chunk):
        return subprocess.run([str(self.binary), str(words), str(chunk)], input=data,
                              capture_output=True, timeout=5)

    def test_run_literal_and_token_boundaries_survive_arbitrary_chunks(self):
        words = array("H", [0] * 32768 + [12, 65535] * 20000 + [3] * 65540)
        encoded = atlas.rle_encode(words)
        for chunk in (1, 3, 257, 4096):
            result = self.decode(encoded, len(words), chunk)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, words.tobytes())

    def test_zero_oversized_trailing_and_truncated_streams_are_rejected(self):
        for data, count in ((b"\0\0", 1), (struct.pack("<HH", 0x8002, 12), 1),
                            (struct.pack("<HHH", 1, 12, 0), 1), (b"\x01", 1),
                            (struct.pack("<H", 1), 1), (struct.pack("<H", 0x8001), 1)):
            self.assertNotEqual(self.decode(data, count, 1).returncode, 0)

    def test_transparent_atlas_preserves_rgb_alpha_planes_and_state_endpoints(self):
        from PIL import Image
        counts = {key: 3 for key in atlas.COUNTS}
        with mock.patch.dict(atlas.COUNTS, counts, clear=True):
            path = atlas.build(Path(self.tmp.name) / 'alpha.rle', alpha=True)
        data = path.read_bytes()
        magic, width, count, size = struct.unpack('<4sHHI', data[:12])
        self.assertEqual((magic, width, count, size), (b'HPA2', 220, 12, 12 * 220 * 220 * 3))
        decoded = self.decode(data[12:], size // 2, 4096)
        self.assertEqual(decoded.returncode, 0, decoded.stderr)
        words = array('H')
        words.frombytes(decoded.stdout)
        stride = 220 * 220 * 3 // 2
        for i in range(stride, len(words)):
            words[i] ^= words[i-stride]
        raw = words.tobytes()
        row = 0
        for state, n in counts.items():
            indices = [(row, 0)]
            if state in ('morph', 'settle'):
                indices.append((row+n-1, atlas.SOURCE_COUNTS[state]-1))
            for frame_index, source_index in indices:
                with Image.open(ROOT / 'orbs/glass-jarvis' / f'{state}_{source_index:02d}.png') as image:
                    rgba = image.convert('RGBA')
                frame = raw[frame_index * stride * 2:(frame_index+1) * stride * 2]
                rgb = rgba.convert('RGB').tobytes()
                expected = array('H', (((rgb[i] >> 3) << 11) | ((rgb[i+1] >> 2) << 5) | (rgb[i+2] >> 3)
                                       for i in range(0,len(rgb),3)))
                self.assertEqual(frame[:220*220*2], expected.tobytes())
                self.assertEqual(frame[220*220*2:], rgba.getchannel('A').tobytes())
                # Transparent edges reveal whichever painting is selected, with no baked rectangle.
                self.assertEqual(frame[220*220*2], 0)
            row += n


if __name__ == "__main__":
    unittest.main()
