"""Lossless pixels and native bounds checks for the atlas retained in compressed form."""
import hashlib
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

import lz4.block

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'orbs'))
import atlas


class PackedAtlasTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='hallpanel-packed-')
        folder = Path(cls.tmp.name)
        cls.data = atlas.build_packed(folder / 'orb.lz4').read_bytes()
        source = folder / 'validate.cpp'
        source.write_text(r'''
#include <iostream>
#include <iterator>
#include <vector>
#include "packed_atlas.h"
int main() {
  std::vector<uint8_t> data((std::istreambuf_iterator<char>(std::cin)), {});
  packed_orb::Atlas atlas;
  if (!atlas.open(data.data(), data.size(), 143)) return 1;
  for (size_t i = 0; i < atlas.count(); ++i) {
    if (!atlas.frame(i) || atlas.frame_size(i) < 13) return 2;
  }
  if (atlas.frame(143) || atlas.frame_size(143)) return 3;
  return 0;
}
''')
        cls.binary = folder / 'validate'
        compiler = shutil.which('clang++') or shutil.which('g++')
        if not compiler:
            raise RuntimeError('Native atlas regression requires a C++ compiler')
        subprocess.run([compiler, '-std=c++17', '-O1', '-g', '-fsanitize=address,undefined',
                        '-I', str(ROOT / 'styles/glass-v2'), str(source), '-o', str(cls.binary)],
                       check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def valid(self, data):
        result = subprocess.run([str(self.binary)], input=data, capture_output=True, timeout=5)
        self.assertNotIn(b'ERROR: AddressSanitizer', result.stderr)
        self.assertNotIn(b'runtime error:', result.stderr)
        self.assertIn(result.returncode, (0, 1), result.stderr.decode(errors='replace'))
        return result.returncode == 0

    def test_all_143_frames_match_the_previous_pixels_and_alpha_exactly(self):
        data = self.data
        magic, side, count, total = struct.unpack('<4sHHI', data[:12])
        self.assertEqual((magic, side, count, total), (b'HPA3', 220, 143, len(data)))
        self.assertTrue(self.valid(data))
        offsets = struct.unpack_from(f'<{count+1}I', data, 12)
        for i, frame in enumerate(atlas.frames(alpha=True)):
            method, compressed, raw_size = struct.unpack_from('<III', data, offsets[i])
            self.assertEqual((method, raw_size), (2, 220*220*3))
            decoded = lz4.block.decompress(data[offsets[i]+12:offsets[i]+12+compressed], uncompressed_size=raw_size)
            self.assertEqual(hashlib.sha256(decoded).digest(), hashlib.sha256(atlas.rgb565a8(frame)).digest())
        # Large enough saving to leave room for audio, image changes and both framebuffers.
        self.assertLess(len(data), 6*1024*1024)

    def test_malformed_headers_indices_and_frame_sizes_are_rejected_without_out_of_bounds_reads(self):
        data = self.data
        for length in list(range(32)) + [100, len(data)-1]:
            self.assertFalse(self.valid(data[:length]))
        for at, value in ((8, len(data)+4), (12, 0), (12, 0xffffffff), (16, 4),
                          (12+143*4, len(data)-4)):
            changed = bytearray(data)
            struct.pack_into('<I', changed, at, value)
            self.assertFalse(self.valid(changed))
        first = struct.unpack_from('<I', data, 12)[0]
        for at, value in ((0, 1), (4, 0), (4, 0xffffffff), (8, 220*220*4)):
            changed = bytearray(data)
            struct.pack_into('<I', changed, first+at, value)
            self.assertFalse(self.valid(changed))
        changed = bytearray(data)
        changed[:4] = b'BAD!'
        self.assertFalse(self.valid(changed))

    def test_panel_atlas_loops_are_drawn_frames_without_blends_and_fit_the_budget(self):
        from PIL import Image
        version = atlas.PANEL_VERSION
        sources, counts = atlas.LAYOUTS[version]
        with tempfile.TemporaryDirectory() as tmp:
            data = atlas.build_packed(Path(tmp) / 'orb.lz4', version=version).read_bytes()
        magic, side, count, total = struct.unpack('<4sHHI', data[:12])
        self.assertEqual((magic, side, count, total), (b'HPA3', 220, sum(counts.values()), len(data)))
        self.assertEqual(count, 211)
        offsets = struct.unpack_from(f'<{count+1}I', data, 12)
        def decoded(i):
            method, compressed, raw_size = struct.unpack_from('<III', data, offsets[i])
            return lz4.block.decompress(data[offsets[i]+12:offsets[i]+12+compressed], uncompressed_size=raw_size)
        start = 0
        for state in counts:
            name, drawn = sources[state]
            self.assertEqual(counts[state], drawn)   # every state is drawn frames, one per atlas frame
            for i in range(0, drawn, 5):
                with Image.open(atlas.folder(version) / f'{name}_{i:02d}.png') as frame:
                    self.assertEqual(decoded(start + i), atlas.rgb565a8(frame.convert('RGBA')))
            start += counts[state]
        # The swing ends where the speaking loop starts; the rest ends on v5's flat line.
        frames = atlas.folder(version)
        with Image.open(frames / 'swing_23.png') as swing_end, Image.open(frames / 'talk_00.png') as speak_start:
            self.assertEqual(swing_end.tobytes(), speak_start.tobytes())
        with Image.open(frames / 'rest_17.png') as rest_end, Image.open(frames / 'settle_05.png') as flat:
            self.assertEqual(rest_end.tobytes(), flat.tobytes())
        # The director swings from the loop where both are the same drawn frame (orb_director.h).
        with Image.open(frames / 'calm_00.png') as loop_start, Image.open(frames / 'swing_00.png') as swing_start:
            self.assertEqual(loop_start.tobytes(), swing_start.tobytes())
        # What the panel draws the voice wave on is the last frame; the line's is fully transparent.
        with Image.open(frames / 'hollow_00.png') as hollow:
            self.assertEqual(decoded(count - 1), atlas.rgb565a8(hollow.convert('RGBA')))
            if version in atlas.LINE_VERSIONS:
                self.assertEqual(hollow.convert('RGBA').getchannel('A').getextrema(), (0, 0))
        self.assertLess(len(data), 6*1024*1024)
