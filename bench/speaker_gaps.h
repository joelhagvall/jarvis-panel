#pragma once
#include <atomic>
#include <cstdint>

// Holes in the speaker's real audio. ESPHome's I2S task pads a DMA buffer with silence whenever the
// stream upstream (announcement decoder, resampler, mixer) has nothing ready, so a starved reply is
// heard as a stutter but logs nothing. The speaker's audio output callback reports each buffer's real
// frames and when they finished playing; a hole between two such runs is a gap.
//
// played() runs on the speaker task; report() reads the counters from the main loop.
class SpeakerGapMeter {
 public:
  static constexpr int64_t MIN_GAP_US = 5000;        // below this: DMA completion jitter
  static constexpr int64_t NEW_SOUND_US = 1500000;   // longer: a new sound, not a gap in this one
  static constexpr int64_t INTERRUPT_US = 1000000;   // after new_sound(): a hole is the switch, not starvation

  explicit SpeakerGapMeter(uint32_t rate) : rate_(rate) {}

  void played(uint32_t frames, int64_t end_us) {
    const int64_t start_us = end_us - static_cast<int64_t>(frames) * 1000000 / rate_;
    const int64_t hole = start_us - last_end_us_;
    if (last_end_us_ == 0 || hole > NEW_SOUND_US || start_us <= interrupt_until_us_.load(std::memory_order_acquire)) {
      sound_start_us_ = start_us;
      sound_start_seen_.store(start_us, std::memory_order_release);
    } else if (hole > MIN_GAP_US) {
      const uint32_t ms = static_cast<uint32_t>((hole + 500) / 1000);
      last_gap_ms_.store(ms, std::memory_order_relaxed);
      last_gap_into_ms_.store(static_cast<uint32_t>((last_end_us_ - sound_start_us_) / 1000),
                              std::memory_order_relaxed);
      total_ms_.fetch_add(ms, std::memory_order_relaxed);
      if (ms > worst_ms_.load(std::memory_order_relaxed)) worst_ms_.store(ms, std::memory_order_relaxed);
      gaps_.fetch_add(1, std::memory_order_release);
    }
    last_end_us_ = end_us;
  }

  // Another sound is about to replace what plays (the listening chime cutting a reply short, 2026-10-30:
  // counted as a 200 ms gap). Holes in the next INTERRUPT_US start a new sound instead. Main loop, esp_timer clock.
  void new_sound(int64_t now_us) { interrupt_until_us_.store(now_us + INTERRUPT_US, std::memory_order_release); }

  // When the latest sound began playing (real audio after NEW_SOUND_US of quiet), on the esp_timer clock.
  int64_t sound_start_us() const { return sound_start_seen_.load(std::memory_order_acquire); }

  // True once per batch of new gaps, with the numbers to log.
  bool report(uint32_t &gaps, uint32_t &last_ms, uint32_t &last_into_ms, uint32_t &worst_ms, uint32_t &total_ms) {
    gaps = gaps_.load(std::memory_order_acquire);
    if (gaps == reported_) return false;
    reported_ = gaps;
    last_ms = last_gap_ms_.load(std::memory_order_relaxed);
    last_into_ms = last_gap_into_ms_.load(std::memory_order_relaxed);
    worst_ms = worst_ms_.load(std::memory_order_relaxed);
    total_ms = total_ms_.load(std::memory_order_relaxed);
    return true;
  }

 private:
  uint32_t rate_;
  int64_t last_end_us_{0};
  int64_t sound_start_us_{0};
  uint32_t reported_{0};
  std::atomic<uint32_t> gaps_{0}, last_gap_ms_{0}, last_gap_into_ms_{0}, worst_ms_{0}, total_ms_{0};
  std::atomic<int64_t> sound_start_seen_{0}, interrupt_until_us_{0};
};

// A voice reply's first audio out of the speaker, against the moments the panel had the recognised text and
// its player started announcing. Nothing timed the reply's sound before (docs/voice-latency.md): the player says
// PLAYING before any audio exists. All times are the esp_timer clock; armed and read on the main loop.
struct FirstReplyAudio {
  static constexpr int64_t GIVE_UP_US = 15000000;    // no new sound: music on the panel's speaker hid it
  int64_t text_us{0}, announce_us{0};

  // Once the first sound that began at or after announcing is known: its delays, then disarmed.
  bool take(int64_t sound_start_us, int64_t now_us, uint32_t &after_announce_ms, uint32_t &after_text_ms) {
    if (announce_us == 0) return false;
    if (sound_start_us < announce_us) {
      if (now_us - announce_us > GIVE_UP_US) text_us = announce_us = 0;
      return false;
    }
    after_announce_ms = static_cast<uint32_t>((sound_start_us - announce_us) / 1000);
    after_text_ms = text_us && text_us <= sound_start_us ? static_cast<uint32_t>((sound_start_us - text_us) / 1000) : 0;
    text_us = announce_us = 0;
    return true;
  }
};

// speaker_out runs at a fixed 48 kHz (board.py).
inline SpeakerGapMeter panel_speaker_gaps{48000};
inline FirstReplyAudio panel_first_reply;
