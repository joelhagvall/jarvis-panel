#pragma once
#define HALLPANEL_ORB_VIRTUAL 1
#include <array>
#include "packed_atlas.h"
#include "packed_prefetch.h"
#include "orb_director.h"
#include "vsync_clock.h"
#include "esphome/core/hal.h"
#include "esphome/core/log.h"
#include "lvgl.h"
#include "src/misc/cache/instance/lv_image_cache.h"

#if !LV_USE_LZ4 || !LV_USE_LZ4_INTERNAL || !LV_BIN_DECODER_RAM_LOAD
#error "Packed orb requires native LZ4 and LV_BIN_DECODER_RAM_LOAD"
#endif

namespace packed_orb {
// Production images per state. At the panel's 60.385 Hz scan one image per frame
// takes 5199.9 / 1805.0 / 645.9 / 2997.4 / 596.2 ms for listen / think / morph / speak /
// settle. Listening plays its 96 drawn frames at half the old 2.6 s pace, speaking its 72 drawn
// frames in 3.0 s instead of 2.0 s; thinking plays the listening loop in 1.8 s.
constexpr std::array<size_t, 5> VIRTUAL_COUNTS{314, 109, 39, 181, 36};
constexpr std::array<bool, 5> ONE_SHOT{false, false, true, false, true};
constexpr size_t VIRTUAL_TOTAL = 314 + 109 + 39 + 181 + 36;
// Directed images (orb_director.h): a ring of recipes rewritten AHEAD frames before they are shown.
constexpr size_t RING = 32, AHEAD = 3;
// The loudness the speaking wave follows (the board points it at the microphone's level()).
inline float (*voice_source)() = nullptr;
// The speaking_live test mode's voice instead of the microphone (synthetic_speech in orb_director.h).
inline bool synthetic_voice = false;

struct Timelines {
  std::array<lv_obj_t *, 5> objects{};
  std::array<const void **, 5> native{}, smooth{};
  std::array<size_t, 5> native_counts{}, smooth_counts{};
  std::array<lv_anim_exec_xcb_t, 5> native_callbacks{};
  bool production{};
  bool installed{};
  // Production frames follow the display clock: exactly one new image per frame
  // boundary, chosen from the boundary count, so no beat against the animation
  // timer can repeat or skip an image. Without a live clock (simulator, stalled
  // display) the LVGL animation value selects the image as before.
  const vsync::Clock *clock{};
  std::array<uint32_t, 5> start{};
  uint32_t applied{UINT32_MAX};
  lv_timer_t *step_timer{};
  // Once direct() is called: every production frame comes from the director, whatever state is shown.
  DirectorCore director{};
  bool directed{}, primed{};
  Recipe *ring_recipes{};
  voice_wave::Params *ring_waves{};
  lv_image_dsc_t *ring_frames{};
  size_t ring_base{};
  uint32_t written{};
  bool live() const { return clock && clock->live(esphome::micros()); }
  uint32_t next_boundary() const { return clock ? clock->count.load(std::memory_order_acquire) + 1 : 0; }
  void set_clock(const vsync::Clock *source) { clock = source; start.fill(next_boundary()); }
  size_t find(const void *object) const {
    for (size_t i = 0; i < objects.size(); ++i) if (objects[i] == object) return i;
    return objects.size();
  }
  // Image for a boundary count relative to the state's start: a loop wraps, a one-shot holds its last image.
  static size_t image_at(uint32_t elapsed, size_t count, bool one_shot) {
    return one_shot ? std::min<size_t>(elapsed, count - 1) : elapsed % count;
  }
  void step() {
    if (!installed || !production || !live()) return;
    const uint32_t boundary = clock->count.load(std::memory_order_acquire);
    if (boundary == applied) return;
    applied = boundary;
    // A buffer submitted before this boundary is still waiting for it to be presented
    // at the next one: a refresh now would block until then and start every later
    // frame late. Skip this boundary's image; the next boundary renders into a free
    // buffer right away, so the image chosen at a boundary is presented at the next.
    if (clock->pending.load(std::memory_order_acquire)) return;
    if (directed) { direct(boundary); return; }
    for (size_t i = 0; i < objects.size(); ++i) {
      // Frozen test scenes delete the animation; a finished one-shot keeps its last image.
      if (!lv_anim_get(objects[i], nullptr)) continue;
      const uint32_t elapsed = boundary - start[i];
      if (int32_t(elapsed) < 0) continue;  // restarted in this frame: its image 0 is already set
      const auto *source = smooth[i][image_at(elapsed, smooth_counts[i], ONE_SHOT[i])];
      if (lv_image_get_src(objects[i]) != source) lv_image_set_src(objects[i], source);
    }
  }
  void write_ring(uint32_t boundary, Recipe recipe) {
    const size_t at = ring_base + boundary % RING;
    recipe.next = uint16_t(ring_base + (boundary + 1) % RING);
    prefetch.forget(uint32_t(at));   // a cached image of this slot's previous recipe must not come back
    if (recipe.second == WAVE) ring_waves[at] = director.wave;   // before the recipe that points at it
    ring_recipes[at] = recipe;
    ring_frames[at].data = ring_frames[recipe.first].data;
    ring_frames[at].data_size = ring_frames[recipe.first].data_size;
  }
  void direct(uint32_t boundary) {
    size_t shown = objects.size();
    for (size_t i = 0; i < objects.size(); ++i)
      if (!lv_obj_has_flag(objects[i], LV_OBJ_FLAG_HIDDEN)) { shown = i; break; }
    // Frozen test scenes delete the shown state's animation; leave their image alone.
    if (shown < objects.size() && !lv_anim_get(objects[shown], nullptr)) return;
    const int32_t lead = int32_t(written - boundary);
    if (!primed || lead < -1 || lead > int32_t(AHEAD)) { written = boundary - 1; primed = true; }
    while (int32_t(written - boundary) < int32_t(AHEAD)) {
      // A change of state takes effect AHEAD frames later; the images before it are already prepared.
      if (shown < objects.size()) director.change(DirectorCore::Mode(shown));
      director.voice(synthetic_voice ? synthetic_speech(uint32_t(esphome::micros() / 1000))
                                     : voice_source ? voice_source() : 0.0f);
      ++written;
      write_ring(written, director.advance());
    }
    const auto *source = &ring_frames[ring_base + boundary % RING];
    for (auto *object : objects) if (lv_image_get_src(object) != source) lv_image_set_src(object, source);
  }
  static void index(void *object, int32_t value);
  void select(bool use_production) {
    if (!installed) return;
    production = use_production;
    for (size_t i = 0; i < objects.size(); ++i) {
      const auto count = production ? smooth_counts[i] : native_counts[i];
      auto *animation = lv_animimg_get_anim(objects[i]);
      // LVGL 9.4 stores pic_count as int8_t even though its public getter is uint8_t.
      // Keep that field in range; a public animation callback addresses the full timeline.
      lv_animimg_set_src(objects[i], production ? smooth[i] : native[i], std::min(count, size_t(127)));
      lv_anim_set_exec_cb(animation, production || count > 127 ? index : native_callbacks[i]);
      lv_anim_set_values(animation, 0, int32_t(count));
    }
  }
};
inline Timelines timelines;
// The two looping sphere states share the same source artwork. Align the new
// state's actual image before showing it, with at most half a target-frame of
// phase quantization. Use the visible image, not a clock that may be ahead of a
// delayed render. One-shot morph/settle and frozen diagnostics are unaffected.
inline void sync_loop_phase(lv_obj_t *object) {
  if (!timelines.installed || !timelines.production || !timelines.live() || timelines.directed) return;
  const auto to = timelines.find(object);
  if (to > 1) return;
  const size_t from = 1 - to;
  if (lv_obj_has_flag(timelines.objects[from], LV_OBJ_FLAG_HIDDEN)) return;
  const auto *shown = lv_image_get_src(timelines.objects[from]);
  const auto *begin = timelines.smooth[from];
  const size_t count = timelines.smooth_counts[from], target_count = timelines.smooth_counts[to];
  const auto *found = std::find(begin, begin + count, shown);
  if (found == begin + count) return;
  const size_t index = (size_t(found - begin) * target_count + count / 2) / count % target_count;
  timelines.start[to] = timelines.clock->count.load(std::memory_order_acquire) - uint32_t(index);
  if (lv_image_get_src(object) != timelines.smooth[to][index]) lv_image_set_src(object, timelines.smooth[to][index]);
}
inline void Timelines::index(void *object, int32_t value) {
  // With a live display clock, production images are chosen per frame boundary in step().
  if (timelines.production && timelines.live()) return;
  for (size_t i = 0; i < timelines.objects.size(); ++i) if (timelines.objects[i] == object) {
    const auto count = timelines.production ? timelines.smooth_counts[i] : timelines.native_counts[i];
    const auto index = size_t(std::max(int32_t(0), std::min(value, int32_t(count - 1))));
    auto *image = static_cast<lv_obj_t *>(object);
    const auto *source = (timelines.production ? timelines.smooth[i] : timelines.native[i])[index];
    // LVGL also executes the terminal value `count`, clamped to the last image.
    // Re-invalidating that same image can miss a cache slot already reused for
    // the next cycle. Keep its existing pixels until the source actually changes.
    if (lv_image_get_src(image) != source) lv_image_set_src(image, source);
    return;
  }
}
inline void select_timeline(bool production) { timelines.select(production); }
// Production frames from the director (orb_director.h) instead of each state's fixed timeline.
inline bool direct() {
  // The director draws the speaking wave on the empty glass; without it the fixed timelines stay.
  if (!timelines.installed || !timelines.ring_recipes || timelines.director.hollow == NO_FRAME) return false;
  timelines.directed = true;
  timelines.primed = false;
  return true;
}
// Start a state's animation from its first image. Replaces lvgl.animimg.start for the
// orb states, so a one-shot morph/settle is paced from the next frame boundary.
inline void restart(lv_obj_t *object) {
  const auto i = timelines.find(object);
  if (timelines.installed && i < timelines.objects.size()) {
    timelines.start[i] = timelines.next_boundary();
    if (timelines.production && timelines.live() && !timelines.directed && lv_image_get_src(object) != timelines.smooth[i][0])
      lv_image_set_src(object, timelines.smooth[i][0]);
  }
  lv_animimg_start(object);
}
inline size_t source_count(lv_obj_t *object) {
  for (size_t i = 0; i < timelines.objects.size(); ++i) if (timelines.objects[i] == object)
    return timelines.production ? timelines.smooth_counts[i] : timelines.native_counts[i];
  return lv_animimg_get_src_count(object);
}

template<size_t Listen, size_t Morph, size_t Speak, size_t Settle, size_t Hollow = 0>
bool install(const Atlas &atlas, lv_obj_t *listen, lv_obj_t *think, lv_obj_t *morph, lv_obj_t *speak, lv_obj_t *settle) {
#ifdef USE_ESP32
  if (timelines.installed) return false;
  constexpr size_t raw_count = Listen + Morph + Speak + Settle + Hollow;
  constexpr size_t v_listen = raw_count, v_think = v_listen + VIRTUAL_COUNTS[0], v_morph = v_think + VIRTUAL_COUNTS[1],
                   v_speak = v_morph + VIRTUAL_COUNTS[2], v_settle = v_speak + VIRTUAL_COUNTS[3],
                   v_ring = v_settle + VIRTUAL_COUNTS[4], count = v_ring + RING;
  static_assert(count == raw_count + VIRTUAL_TOTAL + RING);
  if (atlas.count() != raw_count) return false;
  // Native LZ4 decoding uses one transient frame. Do not accumulate a decoded atlas in LVGL's cache.
  lv_image_cache_resize(0, true);
  auto *frames = static_cast<lv_image_dsc_t *>(heap_caps_calloc(count, sizeof(lv_image_dsc_t), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
  auto *sources = static_cast<const void **>(heap_caps_calloc(count, sizeof(void *), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
  auto *recipes = static_cast<Recipe *>(heap_caps_calloc(count, sizeof(Recipe), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
  auto *waves = static_cast<voice_wave::Params *>(heap_caps_calloc(count, sizeof(voice_wave::Params), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
  if (!frames || !sources || !recipes || !waves) {
    heap_caps_free(frames); heap_caps_free(sources); heap_caps_free(recipes); heap_caps_free(waves); return false;
  }
  static std::array<const void *, (Listen + 2) / 3> thinking{};
  virtual_sequence(recipes, v_listen, VIRTUAL_COUNTS[0], 0, Listen, true);
  virtual_sequence(recipes, v_think, VIRTUAL_COUNTS[1], 0, Listen, true);
  virtual_sequence(recipes, v_morph, VIRTUAL_COUNTS[2], Listen, Morph, false, v_speak);
  virtual_sequence(recipes, v_speak, VIRTUAL_COUNTS[3], Listen + Morph, Speak, true);
  virtual_sequence(recipes, v_settle, VIRTUAL_COUNTS[4], Listen + Morph + Speak, Settle, false);
  for (size_t i = 0; i < RING; ++i) recipes[v_ring + i] = {0, 0, 0, uint16_t(v_ring + (i + 1) % RING)};
  for (size_t i = 0; i < count; ++i) {
    auto &frame = frames[i];
    frame.header.magic = LV_IMAGE_HEADER_MAGIC;
    frame.header.cf = LV_COLOR_FORMAT_RGB565A8;
    frame.header.flags = LV_IMAGE_FLAGS_COMPRESSED;
    frame.header.w = frame.header.h = SIDE;
    frame.header.stride = SIDE * 2;
    const size_t raw = i < raw_count ? i : recipes[i].first;
    frame.data_size = atlas.frame_size(raw);
    frame.data = atlas.frame(raw);
    sources[i] = &frame;
  }
  prefetch.set_waves(waves);
  if (!prefetch.begin(atlas, frames, count, Listen, Morph, Speak, recipes)) {
    heap_caps_free(frames); heap_caps_free(sources); heap_caps_free(recipes); heap_caps_free(waves);
    return false;
  }
  for (size_t i = 0; i < thinking.size(); ++i) thinking[i] = sources[i * 3];
  const auto *clock = timelines.clock;
  timelines = {{listen, think, morph, speak, settle},
               {sources, thinking.data(), sources + Listen, sources + Listen + Morph, sources + Listen + Morph + Speak},
               {sources + v_listen, sources + v_think, sources + v_morph, sources + v_speak, sources + v_settle},
               {Listen, thinking.size(), Morph, Speak, Settle}, VIRTUAL_COUNTS, {}, false, true};
  timelines.set_clock(clock);
  timelines.ring_recipes = recipes;
  timelines.ring_waves = waves;
  timelines.ring_frames = frames;
  timelines.ring_base = v_ring;
  timelines.director.setup(Listen, Morph, Speak, Settle, Hollow ? uint16_t(Listen + Morph + Speak + Settle) : NO_FRAME);
  for (size_t i = 0; i < timelines.objects.size(); ++i)
    timelines.native_callbacks[i] = lv_animimg_get_anim(timelines.objects[i])->exec_cb;
  select_timeline(true);
  // Newest timer first: a boundary's image is set before the refresh timer runs in the same pass.
  timelines.step_timer = lv_timer_create([](lv_timer_t *) { timelines.step(); }, 1, nullptr);
  lv_animimg_start(listen);
  lv_animimg_start(think);
  lv_animimg_start(speak);
  ESP_LOGI("packed_orb", "Installed %u original and %u virtual frames, one per display frame when the frame clock is live; image cache disabled",
           unsigned(raw_count), unsigned(count - raw_count));
  return true;
#else
  (void)atlas; (void)listen; (void)think; (void)morph; (void)speak; (void)settle;
  return false;
#endif
}
}  // namespace packed_orb
