#pragma once
#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include "virtual_frames.h"
#include "voice_wave.h"

namespace packed_orb {
// A voice for the speaking_live test mode (tools/panel wave): loudness like speech, as RMS of full scale,
// from the time in ms, so the director and the drawn wave run without the microphone, the speaker or a
// model. Syllables about 4 a second over a quiet floor, a short pause every 2.6 s, a longer one every 7 s.
inline double synthetic_speech(uint32_t ms) {
  const double t = ms / 1000.0;
  const double phrase = std::fmod(t, 7.0), breath = std::fmod(t, 2.6);
  if (phrase > 5.8 || breath > 2.3) return 0.0006;
  const double syllable = 0.5 + 0.5 * std::sin(2 * 3.14159265358979 * 4.1 * t);
  const double stress = 0.6 + 0.4 * std::sin(2 * 3.14159265358979 * 0.37 * t + 1.3);
  return 0.0006 + 0.06 * stress * syllable * syllable;
}
// The orb's motion, one image per display frame (atlas v7). The firmware writes each frame's recipe a few
// frames ahead, so the prefetch worker always prepares exactly the image that comes. The aim is one
// continuous motion that follows the voices, never a jump or a double image:
//   listen    the calm drawn loop, livelier while someone speaks (the microphone's loudness);
//   think     the same loop easing up to 1.8 s per loop over RAMP frames;
//   swing     entered where its first drawn frame equals the loop's (calm_00 = swing_00): the loop runs
//             on, at most 2.5x faster, until it gets there (about CATCH frames), then swings;
//   speak     the voice wave drawn on the panel (voice_wave.h on the empty glass), from the swing's last
//             frame on (swing_23 = talk_00 = the drawn wave at t 0, height 0.75). Each of its three lines
//             rises with Jarvis's loudness as the microphone next to the speaker hears it, the first one
//             at once, the others a little slower, and sinks back in his pauses;
//   hush      the drawn wave sinking to the flat line (rest_17), to settle or before anything else;
//   settle    the drawn rest frames, when settling does not start from the wave.
// Anything else (a new question while he speaks, a swing broken off) cross-fades over FADE frames.
struct DirectorCore {
  enum Mode : uint8_t { LISTEN, THINK, MORPH, SPEAK, SETTLE, HUSH };
  static constexpr size_t SETS = 5;
  static constexpr uint32_t FADE = 12, RAMP = 36, CATCH = 45, HUSH_FRAMES = 36, QUICK_HUSH = 8;
  // Drawn frames per display frame at the panel's 60.385 Hz, from the authored loop lengths.
  static constexpr double LISTEN_SPEED = 96.0 / 314, THINK_SPEED = 96.0 / 109;
  static constexpr double TALK_LOOP = 1.0 / 181;    // wave loops per display frame at 1x (3.0 s)
  static constexpr double SLEW = 0.012;             // largest speed change per frame, drawn frames
  static constexpr float START_HEIGHT = 0.75f;      // talk_00's height
  static constexpr Mode NONE = Mode(255);

  struct Motion {
    Mode mode{LISTEN};
    double pos{};       // drawn frames of the mode's set
    double speed{};     // drawn frames per display frame (HUSH: its length in frames)
    uint32_t age{};     // display frames in this mode
  };
  uint16_t first[SETS]{}, count[SETS]{};
  uint16_t hollow{NO_FRAME};
  Motion cur{}, from{};
  bool fading{};
  uint32_t fade_age{};
  double ramp_from{LISTEN_SPEED};
  Mode wanted{LISTEN};
  Mode pending{NONE};       // the swing waiting for the loop to reach calm_00
  uint32_t pending_age{};
  Mode after{NONE};         // where a hush goes once the wave is flat (NONE: stay flat)
  voice_wave::Params wave{};
  double wave_speed{TALK_LOOP};
  float hush_from[3]{};
  // Voice: raw loudness in, normalized between a slowly rising noise floor and a slowly falling peak.
  double peak{}, floor_level{1}, level{}, loud{}, slow_loud{};

  void setup(uint16_t listen, uint16_t morph, uint16_t speak, uint16_t settle, uint16_t empty_glass) {
    const uint16_t starts[SETS]{0, 0, listen, uint16_t(listen + morph), uint16_t(listen + morph + speak)};
    const uint16_t counts[SETS]{listen, listen, morph, speak, settle};
    for (size_t i = 0; i < SETS; ++i) { first[i] = starts[i]; count[i] = counts[i]; }
    hollow = empty_glass;
    cur = {LISTEN, 0, LISTEN_SPEED, 0};
    wanted = LISTEN; pending = NONE; after = NONE; fading = false;
  }
  static bool loops(Mode m) { return m == LISTEN || m == THINK; }
  static double smooth(double x) { x = std::clamp(x, 0.0, 1.0); return x * x * (3 - 2 * x); }
  uint16_t flat() const { return uint16_t(first[SETTLE] + count[SETTLE] - 1); }   // rest_17
  double base_speed(Mode m) const {
    switch (m) {
      case LISTEN: return LISTEN_SPEED * (1 + 0.8 * slow_loud);
      case THINK: return THINK_SPEED;
      case MORPH: return double(count[MORPH] - 1) / 38;    // 0.65 s
      case SETTLE: return double(count[SETTLE] - 1) / 35;  // 0.6 s
      default: return 0;
    }
  }
  bool drawn_wave() const { return cur.mode == SPEAK || cur.mode == HUSH; }

  void fade_to(Motion next) {
    from = cur; fading = true; fade_age = 0; pending = NONE;
    cur = next;
  }
  void start_hush(uint32_t frames, Mode then) {
    for (int i = 0; i < 3; ++i) hush_from[i] = wave.amp[i];
    cur = {HUSH, 0, double(frames), 0};
    after = then;
  }
  // The state the scripts show.
  void change(Mode to) {
    if (to == wanted) return;
    wanted = to;
    if (drawn_wave()) {                       // leave the wave by letting it sink flat first
      if (to == SPEAK) { if (cur.mode == HUSH && after == NONE) cur = {SPEAK, 0, 0, 0}; return; }
      start_hush(to == SETTLE ? HUSH_FRAMES : QUICK_HUSH, to == SETTLE ? NONE : to);
      return;
    }
    const bool calm_loop = cur.mode == LISTEN || cur.mode == THINK;
    if (to == LISTEN || to == THINK) {
      if (calm_loop) {                        // one loop, eased to the other speed
        ramp_from = cur.speed;
        cur = {to, cur.pos, cur.speed, 0};
        if (pending == MORPH) pending = NONE;
        return;
      }
      ramp_from = base_speed(to);
      fade_to({to, 0, base_speed(to), 0});
      return;
    }
    if (to == MORPH) {
      if (calm_loop) { pending = MORPH; pending_age = 0; return; }   // run on to calm_00 = swing_00
      fade_to({MORPH, 0, base_speed(MORPH), 0});
      return;
    }
    if (to == SPEAK) {
      if (cur.mode == MORPH || pending == MORPH) return;              // the swing hands over by itself
      fade_to({MORPH, double(count[MORPH] - 1), 0, 1});               // to the swing's end, then speak
      return;
    }
    fade_to({SETTLE, 0, base_speed(SETTLE), 0});                      // settling from the calm loop
  }
  // The microphone's loudness (RMS of full scale) for the next frame.
  void voice(double raw) {
    peak = std::max(raw, peak * 0.9985);                              // halves in about 7.7 s
    floor_level = raw < floor_level ? raw : floor_level * 1.002;
    const double low = 2 * floor_level, span = peak - low;
    level = peak < 0.002 || span <= 1e-6 ? 0 : std::clamp((raw - low) / span, 0.0, 1.0);
    loud += (level - loud) * 0.25;
    slow_loud += (level - slow_loud) * 0.05;
  }
  double loop_speed(double target) {
    if (pending == MORPH) {
      const double remaining = count[cur.mode] - cur.pos;
      target = std::max(target, std::min(remaining / std::max(8.0, double(CATCH) - pending_age), 2.5 * target));
      ++pending_age;
    }
    return cur.speed + std::clamp(target - cur.speed, -SLEW * 4, SLEW * 4);
  }
  // The wave's three lines: the first follows the voice at once, the others a little later.
  void move_wave() {
    const double target_speed = TALK_LOOP * (0.85 + 0.35 * slow_loud);
    wave_speed += std::clamp(target_speed - wave_speed, -TALK_LOOP * 0.02, TALK_LOOP * 0.02);
    wave.t = float(std::fmod(double(wave.t) + wave_speed, 1.0));
    const double voices[3] = {level, loud, slow_loud};
    for (int i = 0; i < 3; ++i) {
      const float target = float(0.18 + 0.92 * std::pow(voices[i], 0.8));
      wave.amp[i] += std::clamp(target - wave.amp[i], -0.05f, 0.14f);   // rises fast, sinks softly
    }
  }
  Recipe wave_recipe() const { return {hollow, WAVE, 0, NO_FRAME}; }

  Recipe advance() {
    switch (cur.mode) {
      case LISTEN: case THINK: {
        const double target = base_speed(cur.mode);
        double speed = pending == NONE ? ramp_from + (target - ramp_from) * smooth(double(cur.age + 1) / RAMP)
                                       : loop_speed(target);
        speed = std::max(speed, 0.05);
        const double n = count[cur.mode];
        if (pending == MORPH && cur.pos + speed >= n) {          // calm_00 = swing_00
          cur = {MORPH, 0, base_speed(MORPH), 1};
          pending = NONE;
          break;
        }
        cur.speed = speed;
        cur.pos = std::fmod(cur.pos + speed, n);
        ++cur.age;
        break;
      }
      case MORPH:
        if (cur.age == 0) { ++cur.age; break; }
        cur.pos = std::min(cur.pos + base_speed(MORPH), double(count[MORPH] - 1));
        ++cur.age;
        if (cur.pos >= count[MORPH] - 1 && wanted == SPEAK) {   // swing_23 = talk_00 = the wave at t 0
          cur = {SPEAK, 0, 0, 1};
          wave.t = 0;
          wave_speed = TALK_LOOP;
          for (auto &a : wave.amp) a = START_HEIGHT;
        }
        break;
      case SPEAK:
        if (cur.age > 0) move_wave();          // its first frame is talk_00 itself
        ++cur.age;
        break;
      case HUSH: {
        ++cur.age;
        const float left = float(1 - smooth(double(cur.age) / cur.speed));
        wave.t = float(std::fmod(double(wave.t) + wave_speed, 1.0));
        for (int i = 0; i < 3; ++i) wave.amp[i] = hush_from[i] * left;
        if (left <= 0) {
          // Flat: the drawn wave equals rest_17. Hold it, or go on where the scripts are.
          const Mode next = after;
          cur = {SETTLE, double(count[SETTLE] - 1), 0, 1};
          after = NONE;
          if (next != NONE) {
            if (next == LISTEN || next == THINK) ramp_from = base_speed(next);
            fade_to({next, 0, base_speed(next), 0});
          }
        }
        break;
      }
      case SETTLE:
        if (cur.age == 0) { ++cur.age; break; }
        cur.pos = std::min(cur.pos + base_speed(SETTLE), double(count[SETTLE] - 1));
        ++cur.age;
        break;
    }
    if (fading) {
      if (loops(from.mode)) from.pos = std::fmod(from.pos + from.speed, count[from.mode]);
      else if (from.mode != SPEAK && from.mode != HUSH)
        from.pos = std::clamp(from.pos + from.speed, 0.0, double(count[from.mode] - 1));
      ++fade_age;
      if (fade_age >= FADE) fading = false;
      else return {nearest(from), nearest(cur), uint16_t(std::lround(smooth(double(fade_age) / FADE) * 256)), NO_FRAME};
    }
    if (drawn_wave()) return wave_recipe();
    return blend(cur);
  }
  uint16_t nearest(const Motion &m) const {
    if (m.mode == HUSH) return flat();
    if (m.mode == SPEAK) return uint16_t(first[SPEAK] + uint16_t(std::lround(wave.t * count[SPEAK])) % count[SPEAK]);
    const uint16_t n = count[m.mode];
    uint16_t i = uint16_t(std::lround(std::max(0.0, m.pos)));
    if (i >= n) i = loops(m.mode) ? 0 : n - 1;
    return uint16_t(first[m.mode] + i);
  }
  Recipe blend(const Motion &m) const {
    const uint16_t n = count[m.mode];
    const double pos = std::max(0.0, m.pos), floor_pos = std::floor(pos);
    uint16_t a = uint16_t(floor_pos), b = uint16_t(a + 1);
    if (a >= n) a = loops(m.mode) ? 0 : n - 1;
    if (b >= n) b = loops(m.mode) ? 0 : a;
    uint16_t weight = uint16_t(std::lround((pos - floor_pos) * 256));
    if (weight >= 256) { a = b; weight = 0; }
    return {uint16_t(first[m.mode] + a), uint16_t(first[m.mode] + b), weight, NO_FRAME};
  }
};
}  // namespace packed_orb
