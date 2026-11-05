// Actual firmware microphone converter and ESPHome's actual ART implementation.
#include "components/panel_audio/mic_resampler_core.h"
#include <cassert>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <vector>

namespace esp_audio_libs::internal {
void *alloc_psram_fallback(size_t n) { return std::malloc(n); }
void free_psram_fallback(void *p) { std::free(p); }
}

// Match generated main.cpp's unqualified ESPHome component namespace lookup.
namespace esphome::panel_audio { struct PanelMicrophone {}; }
using namespace esphome;
static_assert(sizeof(panel_audio::PanelMicrophone) > 0);

int main(int argc, char **argv) {
  assert(argc == 3);
  const double frequency = std::atof(argv[1]);
  const size_t chunk = std::strtoul(argv[2], nullptr, 10);
  assert(chunk > 0);
  panel_audio_dsp::MicResampler converter;
  assert(!converter.process(nullptr, 0, [](auto *, size_t) {}));
  assert(converter.begin());
  assert(!converter.process(reinterpret_cast<const uint8_t *>("x"), 1, [](auto *, size_t) {}));
  std::vector<int16_t> input(48000 * 2), output;
  for (size_t i = 0; i < input.size(); ++i)
    input[i] = int16_t(std::lround(10000 * std::sin(2 * 3.141592653589793 * frequency * i / 48000)));
  for (size_t i = 0; i < input.size(); i += chunk) {
    const size_t count = std::min(chunk, input.size() - i);
    assert(converter.process(reinterpret_cast<const uint8_t *>(input.data() + i), count * 2,
        [&](const uint8_t *data, size_t bytes) {
          assert(bytes <= panel_audio_dsp::MicResampler::OUTPUT_FRAMES * 2);
          auto *samples = reinterpret_cast<const int16_t *>(data);
          output.insert(output.end(), samples, samples + bytes / 2);
        }));
  }
  assert(converter.input_frames() == input.size());
  assert(output.size() == converter.output_frames());
  assert(output.size() >= 32000 - 128 && output.size() <= 32000);
  assert(converter.clipped() == 0);
  assert(std::fwrite(output.data(), sizeof(int16_t), output.size(), stdout) == output.size());
}
