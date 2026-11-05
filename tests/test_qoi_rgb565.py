#!/usr/bin/env python3
"""components/runtime_image: the QOI decoder's direct RGB565 path paints exactly what ImageDecoder::draw painted.

The real qoi_decoder.cpp, image_decoder.cpp and ESPHome's colour headers compile on the host (ASan/UBSan)
against a RuntimeImage stub whose rgb565_pixels() is the one in components/runtime_image/runtime_image.h.
Streams arrive in random chunk sizes, as OnlineImage hands them over, and cover every QOI op.
"""
from pathlib import Path
import random
import re
import shutil
import struct
import subprocess
import tempfile
import unittest

import esphome

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / 'components/runtime_image'
ESPHOME = Path(esphome.__file__).resolve().parent

STUBS = {
    'esphome/core/defines.h': '#pragma once\n#define USE_RUNTIME_IMAGE_QOI\n',
    'esphome/core/component.h': '#pragma once\n',
    'esphome/core/log.h': '#pragma once\n#define ESP_LOGE(...) do {} while (0)\n#define ESP_LOGD(...) do {} while (0)\n'
                          '#define ESP_LOGW(...) do {} while (0)\n#define ESP_LOGV(...) do {} while (0)\n',
    'esphome/core/helpers.h': '#pragma once\n#include <algorithm>\n#include <cstdint>\n#define HOT\n#define ESPHOME_ALWAYS_INLINE\n'
                              'namespace esphome { inline uint32_t random_uint32() { return 4; } constexpr uint32_t encode_uint32(uint8_t a, uint8_t b, uint8_t c, '
                              'uint8_t d) { return uint32_t(a) << 24 | uint32_t(b) << 16 | uint32_t(c) << 8 | d; } }\n',
    'esphome/components/display/display.h': '#pragma once\n#include "display_color_utils.h"\n',
    'esphome/components/image/image.h': r'''#pragma once
namespace esphome::image {
enum ImageType { IMAGE_TYPE_BINARY, IMAGE_TYPE_GRAYSCALE, IMAGE_TYPE_RGB, IMAGE_TYPE_RGB565 };
enum Transparency { TRANSPARENCY_OPAQUE, TRANSPARENCY_CHROMA_KEY, TRANSPARENCY_ALPHA_CHANNEL };
}
''',
}

# RuntimeImage as ImageDecoder sees it. draw_pixel is upstream's RGB565 branch; rgb565_pixels() is
# pasted from the real header below.
RUNTIME_IMAGE = r'''#pragma once
#include <cstdint>
#include <vector>
#include "esphome/components/display/display_color_utils.h"
#include "esphome/components/image/image.h"
namespace esphome::runtime_image {
class RuntimeImage {
 public:
  RuntimeImage(image::ImageType type, bool big_endian) : type_(type), is_big_endian_(big_endian) {}
  int resize(int width, int height) {
    this->storage_.assign(size_t(width) * height * 2 + 2, 0xA5);
    this->buffer_ = this->storage_.data() + this->offset_;
    this->buffer_width_ = width;
    this->buffer_height_ = height;
    return width * height * 2;
  }
  void draw_pixel(int x, int y, const Color &color) {
    this->draw_pixel_calls++;
    const size_t pos = (x + y * this->buffer_width_) * 2;
    uint16_t rgb565 = display::ColorUtil::color_to_565(color);
    if (this->is_big_endian_) {
      this->buffer_[pos + 0] = static_cast<uint8_t>((rgb565 >> 8) & 0xFF);
      this->buffer_[pos + 1] = static_cast<uint8_t>(rgb565 & 0xFF);
    } else {
      this->buffer_[pos + 0] = static_cast<uint8_t>(rgb565 & 0xFF);
      this->buffer_[pos + 1] = static_cast<uint8_t>((rgb565 >> 8) & 0xFF);
    }
  }
  int get_buffer_width() const { return this->buffer_width_; }
  int get_buffer_height() const { return this->buffer_height_; }
  RGB565_PIXELS
  uint8_t *buffer_{nullptr};
  std::vector<uint8_t> storage_;
  size_t offset_{0};
  int buffer_width_{0}, buffer_height_{0};
  image::ImageType type_;
  image::Transparency transparency_{image::TRANSPARENCY_OPAQUE};
  bool is_big_endian_;
  long draw_pixel_calls{0};
};
}
'''

PROGRAM = r'''
#include "qoi_decoder.h"
#include <cstdio>
#include <cstdlib>
#include <vector>
using namespace esphome;
using namespace esphome::runtime_image;
int main(int argc, char **argv) {
  // argv: file, big_endian(0/1), odd_offset(0/1), seed
  FILE *f = fopen(argv[1], "rb");
  std::vector<uint8_t> data;
  for (int c; (c = fgetc(f)) != EOF;) data.push_back(c);
  fclose(f);
  RuntimeImage image(image::IMAGE_TYPE_RGB565, atoi(argv[2]));
  image.offset_ = atoi(argv[3]);
  srand(atoi(argv[4]));
  QoiDecoder decoder(&image);
  decoder.prepare(data.size());
  std::vector<uint8_t> pending;
  size_t next = 0;
  while (!decoder.is_finished()) {
    if (next >= data.size()) { fprintf(stderr, "stream ended early\n"); return 2; }
    size_t take = std::min(data.size() - next, size_t(1 + rand() % 23));
    pending.insert(pending.end(), data.begin() + next, data.begin() + next + take);
    next += take;
    int used = decoder.decode(pending.data(), pending.size());
    if (used < 0) { fprintf(stderr, "decode error %d\n", used); return 3; }
    pending.erase(pending.begin(), pending.begin() + used);
  }
  printf("%ld\n", image.draw_pixel_calls);
  fwrite(image.buffer_, 1, size_t(image.buffer_width_) * image.buffer_height_ * 2, stdout);
  return 0;
}
'''


def qoi_encode(pixels, width, height):
    """Reference QOI encoder (qoiformat.org spec) producing every op."""
    out = bytearray(b'qoif' + struct.pack('>II', width, height) + bytes([4, 0]))
    index = [(0, 0, 0, 0)] * 64
    prev, run = (0, 0, 0, 255), 0
    signed = lambda v: (v + 128) % 256 - 128
    for n, p in enumerate(pixels):
        if p == prev:
            run += 1
            if run == 62 or n == len(pixels) - 1:
                out.append(0xC0 | (run - 1))
                run = 0
            continue
        if run:
            out.append(0xC0 | (run - 1))
            run = 0
        h = (p[0] * 3 + p[1] * 5 + p[2] * 7 + p[3] * 11) % 64
        if index[h] == p:
            out.append(h)
        else:
            index[h] = p
            if p[3] == prev[3]:
                vr, vg, vb = (signed(p[i] - prev[i]) for i in range(3))
                if -3 < vr < 2 and -3 < vg < 2 and -3 < vb < 2:
                    out.append(0x40 | (vr + 2) << 4 | (vg + 2) << 2 | (vb + 2))
                elif -33 < vg < 32 and -9 < vr - vg < 8 and -9 < vb - vg < 8:
                    out += bytes([0x80 | (vg + 32), (vr - vg + 8) << 4 | (vb - vg + 8)])
                else:
                    out += bytes([0xFE, *p[:3]])
            else:
                out += bytes([0xFF, *p])
        prev = p
    return bytes(out + bytes([0] * 7 + [1]))


def make_image(width=37, height=23):
    rng = random.Random(5)
    palette = [tuple(rng.randrange(256) for _ in range(3)) + (255,) for _ in range(5)]
    pixels = []
    for y in range(height):
        for x in range(width):
            if y < 4:          # long runs across row ends
                pixels.append((10, 20, 30, 255))
            elif y < 9:        # small and medium steps: DIFF and LUMA
                pixels.append(((x * 3 + y) % 256, (x * 5) % 256, (y * 7 + x) % 256, 255))
            elif y < 13:       # a few colours again and again: INDEX
                pixels.append(palette[(x * y) % 5])
            elif y < 16:       # alpha changes: RGBA (alpha is dropped for an opaque RGB565 image)
                pixels.append((x * 7 % 256, y * 11 % 256, 99, 255 - x))
            else:              # noise: RGB
                pixels.append(tuple(rng.randrange(256) for _ in range(3)) + (255,))
    return pixels


def rgb565(p):
    scale = lambda v, m: v * (1 + m) // 256
    return scale(p[0], 31) << 11 | scale(p[1], 63) << 5 | scale(p[2], 31)


class QoiRgb565Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix='qoi-rgb565-'))
        for name, text in STUBS.items():
            (cls.tmp / name).parent.mkdir(parents=True, exist_ok=True)
            (cls.tmp / name).write_text(text)
        shutil.copy(ESPHOME / 'core/color.h', cls.tmp / 'esphome/core/color.h')
        shutil.copy(ESPHOME / 'components/display/display_color_utils.h',
                    cls.tmp / 'esphome/components/display/display_color_utils.h')
        for name in ('qoi_decoder.h', 'qoi_decoder.cpp', 'image_decoder.h', 'image_decoder.cpp', 'image_format.h'):
            shutil.copy(COMPONENT / name, cls.tmp / name)
        header = (COMPONENT / 'runtime_image.h').read_text()
        method = re.search(r'  uint16_t \*rgb565_pixels\(\) \{.*?\n  \}\n', header, re.S)
        assert method, 'rgb565_pixels() not found in runtime_image.h'
        (cls.tmp / 'runtime_image.h').write_text(RUNTIME_IMAGE.replace('RGB565_PIXELS', method.group(0)))
        (cls.tmp / 'main.cpp').write_text(PROGRAM)
        cls.binary = cls.tmp / 'qoi'
        subprocess.run(['c++', '-std=c++20', '-O1', '-g', '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
                        '-I', str(cls.tmp), str(cls.tmp / 'main.cpp'), str(cls.tmp / 'qoi_decoder.cpp'),
                        str(cls.tmp / 'image_decoder.cpp'), '-o', str(cls.binary)], check=True)
        cls.width, cls.height = 37, 23
        cls.pixels = make_image(cls.width, cls.height)
        (cls.tmp / 'image.qoi').write_bytes(qoi_encode(cls.pixels, cls.width, cls.height))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def decode(self, path, big_endian=0, odd=0, seed=1):
        result = subprocess.run([str(self.binary), str(path), str(big_endian), str(odd), str(seed)],
                                capture_output=True, check=True)
        calls, _, raw = result.stdout.partition(b'\n')
        return int(calls), raw

    def test_direct_path_paints_every_pixel_without_draw_pixel(self):
        expected = b''.join(struct.pack('<H', rgb565(p)) for p in self.pixels)
        for seed in range(1, 6):
            calls, raw = self.decode(self.tmp / 'image.qoi', seed=seed)
            self.assertEqual(calls, 0)
            self.assertEqual(raw, expected)

    def test_draw_path_unchanged_where_direct_writes_do_not_apply(self):
        # Big endian and an odd buffer address keep upstream's per-pixel draw.
        expected = b''.join(struct.pack('>H', rgb565(p)) for p in self.pixels)
        calls, raw = self.decode(self.tmp / 'image.qoi', big_endian=1)
        self.assertEqual(calls, self.width * self.height)
        self.assertEqual(raw, expected)
        calls, raw = self.decode(self.tmp / 'image.qoi', odd=1)
        self.assertEqual(calls, self.width * self.height)
        self.assertEqual(raw, b''.join(struct.pack('<H', rgb565(p)) for p in self.pixels))

    def test_run_past_the_last_pixel_stays_inside_the_buffer(self):
        # 3x1 image whose only op is a run of 5: ASan fails on any write past the 3 pixels.
        stream = b'qoif' + struct.pack('>II', 3, 1) + bytes([4, 0, 0xC0 | 4]) + bytes([0] * 7 + [1])
        path = self.tmp / 'run.qoi'
        path.write_bytes(stream)
        calls, raw = self.decode(path)
        self.assertEqual(calls, 0)
        self.assertEqual(raw, struct.pack('<H', rgb565((0, 0, 0, 255))) * 3)


if __name__ == '__main__':
    unittest.main()
