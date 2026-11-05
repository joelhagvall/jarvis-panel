#pragma once
// Two sparse angular atlases: the worker builds a new cover without touching the active one.
// Full speed copies sharp precomputed frames; start/stop interpolate neighbouring angles on the worker.
#define HALLPANEL_VINYL 1
#include <atomic>
#include <array>
#include <cstring>
#include <functional>
#include <mutex>
#include <string>
#include "esphome/core/log.h"
#include "esphome/core/hal.h"
#ifdef USE_ESP32
#include "esp_heap_caps.h"
#include "driver/ppa.h"
#include "src/display/lv_display_private.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#endif
#include "lvgl.h"
#include "vinyl_motion.h"
#include "vinyl_sampler.h"
#include "vinyl_line.h"
#include "vinyl_damage.h"
#include "vinyl_right.h"
#include "vinyl_snapshot.h"
#if !defined(USE_ESP32) && !defined(ESP_LOGW)
#define ESP_LOGW ESP_LOGI   // native tests log warnings as information
#endif
namespace panel_vinyl {
constexpr int COVER_MAX = 288;
constexpr uint16_t OUTSIDE = ((0x14 >> 3) << 11) | ((0x18 >> 2) << 5) | (0x26 >> 3);
constexpr float FRAME_S = 1.0f / 60;
constexpr float LABEL = 0.24f;
enum class Kind { PICTURE, LABEL, BLANK };
inline uint16_t *psram(size_t bytes) {
#ifdef USE_ESP32
  return static_cast<uint16_t *>(heap_caps_aligned_alloc(128, (bytes+127)&~size_t(127), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
#else
  return new uint16_t[bytes / 2];
#endif
}
// Free PSRAM now; a test may replace it (Disc::psram_left).
inline size_t psram_free_now() {
#ifdef USE_ESP32
  return heap_caps_get_free_size(MALLOC_CAP_SPIRAM);
#else
  return size_t(1) << 30;
#endif
}
inline void psram_free(uint16_t *pixels) {
#ifdef USE_ESP32
  heap_caps_free(pixels);
#else
  delete[] pixels;
#endif
}
struct FrameClock {
  const std::atomic<uint32_t> *count{nullptr}, *last_us{nullptr};
  const std::atomic<bool> *pending{nullptr};
  std::atomic<uint32_t> *render_source{nullptr};
  bool live() const {
    return count && count->load(std::memory_order_acquire) != 0 &&
           int32_t(esphome::micros() - last_us->load(std::memory_order_acquire)) < 100000;
  }
};
struct Disc {
  const char *name{""};
  int radius{72}, cover_radius{16};
  bool (*shown)(){nullptr};
  void (*on_visible)(bool){nullptr};   // the page's own workers that follow its visibility (the cover download)
  FrameClock clock;
  lv_obj_t *frame{nullptr}, *image{nullptr};
  const uint16_t *layer_rgb{nullptr};
  const uint8_t *layer_alpha{nullptr};
  struct Parts { lv_obj_t *base, *pivot, *rest, *weight, *arm, *head_rim, *head, *cart; } parts{};
  lv_point_precise_t points[6][2]{};
  std::array<LineMask,6> line_masks{};
  std::array<LineMask,6> work_masks{};
  Arm ready_arm{};      // the shown job's arm, for the main-task arm path
  bool arm_async{},ready_arm_ok{};
  std::atomic<uint32_t> arm_errors{0};
  std::array<lv_image_dsc_t,6> line_images{};
  RightArm right_arm;
  lv_obj_t *right_image{};
  std::array<lv_image_dsc_t,RightArm::POOL> right_images{};
  uint32_t arm_work_us{},render_areas{},render_pixels{},render_largest{};
  std::array<int,14> arm_pose{};bool have_arm_pose{};
  std::array<DamageRect,8> render_rects{};
  Motion motion;
  bool on{false}, playing{false}, open{false}, hidden{false}, test{false};
  uint32_t last_ms{0}, open_ms{0}, frames{0}, applied{0};
  uint32_t serial{0};
  float shown_lift{-1}, groove{0}, groove_target{0}, shown_groove{-1};
  const lv_image_dsc_t *cover{nullptr};
  uint16_t *copy{nullptr}, *inputs[2]{};
  int input{0}, pending_input{0};
  Layout layout;
  WorkerPriority priority;
  int copy_size{0};
  uint64_t picture{0};
  std::mutex source_mutex;
  uint32_t generation{0};                         // protected by source_mutex
  std::atomic<uint32_t> offered{0}, prepared{0};
  uint16_t *cache[2]{};      // one contiguous atlas (native tests)
  uint16_t *chunks[3][HALF]{};  // or, on the panel, one PSRAM block per image: a fragmented heap still has room
  // chunks[2] is `behind`: the first stage of the cover turned before the current one, kept so "föregående" turns
  // at once (2026-10-30). Only owned (panel) atlases keep it. Under source_mutex.
  uint64_t active_picture{0}, behind_id{0}, build_picture{0};
  // A cover change never leaves less than this PSRAM free: the release gates' floor is 4 MiB and the change peaked
  // at 4.77 MB with ahead and behind held (2026-10-30). Short of it, `behind` goes first (~1.2 MB).
  static constexpr size_t KEEP_FREE=4608u*1024u;
  size_t (*psram_left)()=psram_free_now;
  size_t missing_bytes(int slot,int parity) const {
    if(!owned_cache||slot<0||cache[slot])return 0;
    size_t n=0;for(int i=parity<0?0:parity;i<HALF;i+=parity<0?1:2)if(!chunks[slot][i])++n;
    return n*layout.count*2;
  }
  // Under source_mutex. The first stage kept for "föregående" goes when a change would otherwise eat into KEEP_FREE.
  void make_room(size_t bytes) {
    if(!owned_cache||!chunks[2][0]||psram_left()>=KEEP_FREE+bytes)return;
    for(auto *&image:chunks[2])if(image){psram_free(image);image=nullptr;}
    behind_id=0;
    ESP_LOGI("vinyl","%s: previous cover's first stage dropped to keep PSRAM free",name);
  }
  bool owned_cache{false};  // PSRAM atlases from setup: the one not turning exists only while a new cover builds
  // Three images: one LVGL shows, one ready for the next boundary, one the worker draws. The worker gets two frames
  // to draw one, so a slow start/stop image (PPA blend plus a moving arm, up to ~21 ms) no longer drops a frame.
  static constexpr int SLOTS=3;
  uint16_t *output[SLOTS]{};
  lv_image_dsc_t output_dsc[SLOTS]{};
  int active_step{1};  // worker: 6, then 2, while a new cover's atlas has only every sixth / every other image
  // A finished half or whole atlas, handed from the builder to the worker, which alone switches `active` (it is
  // the only reader of the atlas). Under source_mutex.
  struct Publish { int slot{-1}, size{0}, step{0}; uint32_t gen{0}; bool pending{false}; uint64_t picture{0}; } publish{};
  // The next Spotify track's cover, built ahead into the atlas not turning: its coarse first stage only, so the
  // record turns the new cover as soon as the track changes (Joel, 2026-10-30). Under source_mutex; the builder
  // advances `built`/`row`. `adopt` hands a finished part to the real build of the same picture.
  struct Ahead { uint64_t id{0}; int slot{-1}, input{-1}, size{0}, built{0}, row{0}; } ahead{};
  struct Adopt { uint32_t gen{0}; int slot{-1}, input{-1}, size{0}, frame{0}, row{0}; bool behind{false}; uint64_t want{0}; } adopt{};
  // A cover change fades instead of cutting (Joel, 2026-10-31: "mer smooth"). At the switch the old atlas is not
  // freed at once: for FADE_FRAMES images its sharp image at the same angle is blended over the new one, one PPA pass
  // on the packed pixels, then `keep_behind` runs as it did at the switch. No extra buffer: v1 held a 166 kB image and
  // the next cover's ahead build lost its room (docs/now-playing.md). Under source_mutex; `fade_on` mirrors `on` for
  // the worker's unlocked check. The builder ends a fade early (`end_fade`) before it needs the old slot or memory.
  static constexpr int FADE_FRAMES=18;   // 0.3 s at 60 fps
  bool fade_enabled{false};              // the panel's (setup); the native tests opt in
  struct Fade { bool on{false}, started{false}; int slot{-1}, step{1}; uint32_t from{0}; uint64_t picture{0}; } fade{};
  std::atomic<bool> fade_on{false};
  std::atomic<uint32_t> fades{0}, fade_images{0}, fade_cut{0};
  // The old cover's share of fade image k (0..FADE_FRAMES-1), 255..0 on an ease-in-out curve.
  static uint32_t fade_weight(int k) {
    if(k<0)return 255;
    if(k>=FADE_FRAMES)return 0;
    const float t=float(k+1)/(FADE_FRAMES+1),eased=t*t*(3-2*t);
    return uint32_t((1-eased)*255+.5f);
  }
  int active{-1}, active_size{0}, build_slot{-1}, build_row{0}, build_size{0}, build_frame{0}, build_input{-1};  // worker only
  uint32_t active_gen{0}, build_gen{0}, build_since{0};
  std::atomic<bool> foreground{false};
  // Slot ownership. The main task moves FREE->WORK, READY->SHOWN/FREE and SHOWN->FREE; the worker WORK->READY/FREE.
  enum : int { FREE=0, WORK=1, READY=2, SHOWN=3 };
  struct Job {
    uint32_t frame{};            // the boundary that takes it
    float angle{},lift{},groove{};
    bool interpolate{},arm_ok{},paint_strip{};
    int x{},y{},strip{-1};
    Arm arm{};
  };
  Job jobs[SLOTS]{};             // written by the main task before WORK, read by the worker until READY
  std::atomic<int> slot_state[SLOTS]{};
  uint32_t frame_now{0}, next_frame{0};  // main task: boundaries seen, the next frame to ask for
  uint32_t first_frame{0};                // main task: jobs before the page last opened are stale
  // Whether the queue runs ahead: images taken on their boundary tick vs. later in the frame, and how often
  // another finished image already waited behind the one taken.
  uint32_t taken_boundary{0},taken_late{0},taken_spare{0};
  RightArm::Pose strip_pose{};int strip_last{-1};
  float asked_angle{0};           // worker: the job being sampled
  bool asked_interpolate{false};
  int displayed{-1};
  uint32_t output_gen[SLOTS]{}, shown_gen{0};
  uint16_t *blend_flip{nullptr};
  std::atomic<uint32_t> gpu_blends{0}, gpu_errors{0};
  std::atomic<uint32_t> work_us{0}, work_total{0}, prepare_ms{0};
  std::atomic<uint32_t> sample_us{0},sample_max_us{0},arm_prepare_us{0},arm_prepare_max_us{0};
  std::atomic<uint32_t> sample_method{0},prepare_cpu_us{0},prepare_rows_done{0};
  uint32_t interval_total{0}, previous_ms{0};
  std::array<uint32_t,128> intervals{};
#ifdef USE_ESP32
  TaskHandle_t task{nullptr};
  // A new cover builds on its own task on core 0 (priority 1 while Spelas nu shows, else 0), beside the worker that
  // turns the record, which preempts it for every image (docs/now-playing.md, 2026-10-30: the worker alone built at about 30 % of its CPU
  // time, 4.8 s per atlas). Not core 1: there LVGL draws Spelas nu for ~15.5 of every 16.6 ms, and the half took 2.9 s.
  TaskHandle_t builder{nullptr};
  ppa_client_handle_t blender{};
#endif
  // The rare build over the turning atlas (no PSRAM for a second one) stays on the worker, which owns `active`.
  std::atomic<bool> build_in_worker{false};
  int size() const { return radius * 2; }
  size_t pixels() const { return size_t(size()) * size(); }
  static RightArm::Pose rounded_pose(const Arm &a) {
    return {int(a.pivot_x),int(a.pivot_y),int(a.end_x),int(a.end_y),int(a.head_x),int(a.head_y),
      int(a.weight_x),int(a.weight_y),int(a.cart_x0),int(a.cart_y0),int(a.cart_x1),int(a.cart_y1),int(a.rest_x),int(a.rest_y)};
  }
  bool enable_right(const lv_image_dsc_t *top,const lv_image_dsc_t *side) {
#ifdef USE_ESP32
    if(!arm_async||radius!=144||!top||!side||!top->data||!side->data||
       top->header.w!=407||top->header.h!=43||side->header.w!=76||side->header.h!=288||
       side->header.cf!=LV_COLOR_FORMAT_RGB565||
       (top->header.cf!=LV_COLOR_FORMAT_RGB565&&top->header.cf!=LV_COLOR_FORMAT_RGB565A8))return false;
    auto &r=right_arm;
    r.top_stride=(top->header.stride?top->header.stride:814)/2;
    r.right_stride=(side->header.stride?side->header.stride:152)/2;
    if(r.top_stride<407||r.right_stride<76||top->data_size<size_t(r.top_stride)*43*2||
       side->data_size<size_t(r.right_stride)*288*2)return false;
    if(top->header.cf==LV_COLOR_FORMAT_RGB565A8) {
      if(!solid_top_region(top->data,top->data_size,r.top_stride)) {
        ESP_LOGW("vinyl","Opaque arm: top crop is not solid or its RGB descriptor is invalid");return false;
      }
    }
    r.top_rgb=reinterpret_cast<const uint16_t *>(top->data);r.right_rgb=reinterpret_cast<const uint16_t *>(side->data);
    auto fail=[&](const char *why){ESP_LOGW("vinyl","Opaque arm refused: %s",why);
      for(auto *p:r.pixels)if(p)heap_caps_free(p);if(r.base)heap_caps_free(r.base);
      if(r.rest)heap_caps_free(r.rest);if(r.pivot)heap_caps_free(r.pivot);r=RightArm{};return false;};
    for(auto *&strip:r.pixels){strip=psram(RightArm::BYTES);if(!strip)return fail("output allocation");}
    struct Visibility {
      lv_obj_t *objects[3];bool hidden[3];
      ~Visibility(){for(int i=0;i<3;++i)if(hidden[i])lv_obj_add_flag(objects[i],LV_OBJ_FLAG_HIDDEN);}
    } visibility{{parts.base,parts.rest,parts.pivot},{}};
    for(int i=0;i<3;++i){visibility.hidden[i]=lv_obj_has_flag(visibility.objects[i],LV_OBJ_FLAG_HIDDEN);
      lv_obj_remove_flag(visibility.objects[i],LV_OBJ_FLAG_HIDDEN);}
    auto *parent=lv_obj_get_parent(frame);lv_obj_update_layout(parent);
    if(lv_obj_get_x(frame)!=72||lv_obj_get_y(frame)!=110)return fail("record origin");
    const auto a=panel_vinyl::arm(216,254,144,0,0);
    put(parts.base,a.pivot_x,a.pivot_y);put(parts.pivot,a.pivot_x,a.pivot_y);put(parts.rest,a.rest_x,a.rest_y);
    lv_obj_update_layout(parent);
    auto rect=[&](lv_obj_t *object,DamageRect &out){lv_area_t box;lv_obj_get_coords(object,&box);
      out={box.x1,box.y1,box.x2,box.y2};return out.x1>=RightArm::X&&out.y1>=RightArm::Y&&
        out.x2<RightArm::X+RightArm::W&&out.y2<RightArm::Y+RightArm::H;};
    if(!rect(parts.base,r.base_rect)||!rect(parts.rest,r.rest_rect)||!rect(parts.pivot,r.pivot_rect))return fail("static bounds");
    r.background(r.pixels[0]);lv_draw_buf_t buffer;
    if(lv_draw_buf_init(&buffer,RightArm::W,RightArm::H,LV_COLOR_FORMAT_RGB565,RightArm::W*2,
        r.pixels[0],RightArm::BYTES)!=LV_RESULT_OK)return fail("draw buffer");
    const lv_area_t area{RightArm::X,RightArm::Y,RightArm::X+RightArm::W-1,RightArm::Y+RightArm::H-1};
    if(!draw_static(parts.rest,buffer,area)||!draw_static(parts.base,buffer,area))return fail("static snapshot");
    auto save=[&](DamageRect box,uint16_t *&out){const int w=box.x2-box.x1+1,h=box.y2-box.y1+1;
      out=psram(w*h*2);if(!out)return false;
      for(int y=0;y<h;++y)std::memcpy(out+y*w,r.pixels[0]+(box.y1-RightArm::Y+y)*RightArm::W+box.x1-RightArm::X,w*2);
      return true;};
    if(!save(r.base_rect,r.base)||!save(r.rest_rect,r.rest))return fail("static allocation");
    const int pw=r.pivot_rect.x2-r.pivot_rect.x1+1,ph=r.pivot_rect.y2-r.pivot_rect.y1+1;
    if(size_t(pw)*ph*4>line_masks[2].capacity||lv_obj_get_style_border_width(parts.pivot,LV_PART_MAIN))return fail("pivot shape");
    auto *scratch=line_masks[2].pixels;std::memset(scratch,0,pw*ph*4);
    if(lv_draw_buf_init(&buffer,pw,ph,LV_COLOR_FORMAT_ARGB8888,pw*4,scratch,line_masks[2].capacity)!=LV_RESULT_OK)return fail("pivot buffer");
    const lv_area_t pivot_area{r.pivot_rect.x1,r.pivot_rect.y1,r.pivot_rect.x2,r.pivot_rect.y2};
    if(!draw_static(parts.pivot,buffer,pivot_area))return fail("pivot snapshot");
    r.pivot=reinterpret_cast<uint8_t *>(psram(pw*ph));if(!r.pivot)return fail("pivot allocation");
    alpha_from_argb(scratch,r.pivot,pw*ph);
    if(!valid_pivot(r.pivot,pw,ph))return fail("pivot mask has no opaque centre");
    r.pivot_color=lv_color_to_u16(lv_obj_get_style_bg_color(parts.pivot,LV_PART_MAIN));
    right_image=lv_image_create(parent);lv_obj_remove_flag(right_image,LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_flag(right_image,LV_OBJ_FLAG_HIDDEN);lv_obj_set_pos(right_image,RightArm::X,RightArm::Y);
    for(int i=0;i<RightArm::POOL;++i){auto &d=right_images[i];d.header.magic=LV_IMAGE_HEADER_MAGIC;
      d.header.cf=LV_COLOR_FORMAT_RGB565;d.header.w=RightArm::W;d.header.h=RightArm::H;d.header.stride=RightArm::W*2;
      d.data=reinterpret_cast<const uint8_t *>(r.pixels[i]);d.data_size=RightArm::BYTES;}
    for(auto &mask:line_masks)if(mask.pixels){heap_caps_free(mask.pixels);mask.pixels=nullptr;}
    r.enabled=true;ESP_LOGI("vinyl","Opaque arm strip ready: %u bytes per buffer",unsigned(RightArm::BYTES));return true;
#else
    (void)top;(void)side;return false;
#endif
  }
  void setup(const char *name_, int radius_, int cover_radius_, int foreground_priority, bool (*shown_)(),
             lv_obj_t *frame_, lv_obj_t *image_, const lv_image_dsc_t *layer, Parts parts_) {
    if (frame) return;
    priority.foreground=foreground_priority; name=name_; radius=radius_; cover_radius=cover_radius_; shown=shown_; frame=frame_; image=image_; parts=parts_;
#ifdef USE_ESP32
    auto cached_line=[&](int index,lv_obj_t *&object,uint32_t color) {
      const auto capacity=ARM_MASK_CAPACITY[index];
      auto &mask=line_masks[index];mask.capacity=capacity;
      mask.pixels=reinterpret_cast<uint8_t *>(psram(capacity));
      if(!mask.pixels){ESP_LOGW("vinyl","Arm mask allocation failed; native line retained");return;}
      auto *replacement=lv_image_create(lv_obj_get_parent(object));
      lv_obj_move_to_index(replacement,lv_obj_get_index(object));
      lv_obj_remove_flag(replacement,LV_OBJ_FLAG_CLICKABLE);lv_obj_add_flag(replacement,LV_OBJ_FLAG_HIDDEN);
      lv_obj_set_style_image_recolor(replacement,lv_color_hex(color),LV_PART_MAIN);
      lv_obj_set_style_image_recolor_opa(replacement,LV_OPA_COVER,LV_PART_MAIN);
      lv_obj_add_flag(object,LV_OBJ_FLAG_HIDDEN);object=replacement;
    };
    cached_line(0,parts.weight,0x18181C);cached_line(2,parts.arm,0x2C2F36);
    cached_line(3,parts.head_rim,0x4A4E58);cached_line(4,parts.head,0x18181C);
    cached_line(5,parts.cart,0xE6E8EC);
    arm_async=true;
    for(int i:{0,2,3,4,5}) {
      work_masks[i].capacity=line_masks[i].capacity;
      if(line_masks[i].pixels)work_masks[i].pixels=reinterpret_cast<uint8_t *>(psram(work_masks[i].capacity));
      if(!work_masks[i].pixels)arm_async=false;
    }
    if(!arm_async) {
      for(auto &mask:work_masks)if(mask.pixels){heap_caps_free(mask.pixels);mask.pixels=nullptr;}
      ESP_LOGW("vinyl","Arm worker cache allocation failed; main-task cache retained");
    }
    lv_display_add_event_cb(lv_display_get_default(),[](lv_event_t *event) {
      auto *self=static_cast<Disc *>(lv_event_get_user_data(event));
      const bool visible=!self->hidden&&self->shown()&&self->on;
      if(self->clock.render_source)self->clock.render_source->store(
        visible&&self->motion.speed>0 ? self->serial|0x80000000u:0,std::memory_order_release);
      if(!visible)return;
      auto *display=lv_display_get_default();self->render_areas=self->render_pixels=self->render_largest=0;
      for(int i=0;i<display->inv_p;++i)if(!display->inv_area_joined[i]) {
        const auto &a=display->inv_areas[i];const uint32_t pixels=lv_area_get_size(&a);
        if(self->render_areas<self->render_rects.size())self->render_rects[self->render_areas]={a.x1,a.y1,a.x2,a.y2};
        ++self->render_areas;self->render_pixels+=pixels;self->render_largest=std::max(self->render_largest,pixels);
      }
    },LV_EVENT_RENDER_START,this);
#endif
    if (layer && layer->data && layer->header.cf==LV_COLOR_FORMAT_RGB565A8 && int(layer->header.w)==size()) {
      layer_rgb=reinterpret_cast<const uint16_t *>(layer->data); layer_alpha=layer->data+pixels()*2;
    }
    layout.make(size(),layer_alpha);
    inputs[0]=psram(COVER_MAX*COVER_MAX*2); inputs[1]=psram(COVER_MAX*COVER_MAX*2); copy=inputs[0];
#ifdef USE_ESP32
    owned_cache=true;   // the second atlas comes with the next cover (claim_build_slot) and goes once it turns
    fade_enabled=true;
    allocate_atlas(0);
#else
    cache[0]=psram(atlas_bytes());
    cache[1]=psram(atlas_bytes());
#endif
    for(int i=0;i<SLOTS;++i) {
      output[i]=psram(pixels()*2);
      output_dsc[i].header.magic=LV_IMAGE_HEADER_MAGIC; output_dsc[i].header.cf=LV_COLOR_FORMAT_RGB565;
      output_dsc[i].header.w=size(); output_dsc[i].header.h=size(); output_dsc[i].header.stride=size()*2;
      if(output[i] && layer_rgb) std::memcpy(output[i],layer_rgb,pixels()*2);
      output_dsc[i].data=reinterpret_cast<const uint8_t *>(output[i]); output_dsc[i].data_size=pixels()*2;
    }
    bool outputs=true;for(auto *o:output)outputs=outputs&&o;
    if (!copy || !inputs[1] || !has_atlas(0) || !outputs) {
      ESP_LOGE("vinyl","%s: continuous renderer allocation failed",name); return;
    }
#ifdef USE_ESP32
    if(xTaskCreatePinnedToCore([](void *p){static_cast<Disc *>(p)->work();},"vinyl",4096,this,priority.current,&task,0)!=pdPASS) {
      ESP_LOGE("vinyl","%s: worker creation failed",name); return;
    }
    // The builder's stack is in PSRAM (docs/memory.md); the per-frame worker above keeps an internal one.
    if(xTaskCreatePinnedToCoreWithCaps([](void *p){static_cast<Disc *>(p)->build_work();},"vinyl-build",4096,this,0,&builder,0,
        MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT)!=pdPASS) {
      builder=nullptr;ESP_LOGW("vinyl","%s: no builder task; the worker builds new covers between images",name);
    }
#endif
#ifdef USE_ESP32
    blend_flip=static_cast<uint16_t *>(heap_caps_aligned_alloc(128,8192,MALLOC_CAP_INTERNAL|MALLOC_CAP_DMA|MALLOC_CAP_8BIT));
    ppa_client_config_t config{};config.oper_type=PPA_OPERATION_BLEND;config.max_pending_trans_num=1;
    config.data_burst_length=PPA_DATA_BURST_LENGTH_128;
    if(ppa_register_client(&config,&blender)!=ESP_OK) {
      blender=nullptr;ESP_LOGW("vinyl","PPA interpolation unavailable; staged CPU fallback retained");
    }
#endif
    ESP_LOGI("vinyl","%s: sparse atlas, %u pixels/frame, %u kB (+%u kB while a new cover builds)",name,
             unsigned(layout.count),unsigned((atlas_bytes() + SLOTS*pixels()*2 + 2*COVER_MAX*COVER_MAX*2)/1024),
             unsigned(atlas_bytes()/1024));
    lv_timer_create([](lv_timer_t *t){static_cast<Disc *>(lv_timer_get_user_data(t))->tick();},1,this);
  }
  size_t atlas_bytes() const { return layout.count*HALF*2; }
  bool has_atlas(int slot) const { return slot>=0&&(cache[slot]||chunks[slot][0]); }
  uint16_t *frame_ptr(int slot,int i) const {
    return chunks[slot][0]?chunks[slot][i]:cache[slot]+size_t(i)*layout.count;
  }
  // Each image is its own block: contiguous 7 MB failed after a day of other allocations (2026-10-29: 11 MB free,
  // the record froze 1.2 s at an album change). `parity` 0 allocates the even images, 1 the odd ones, -1 all.
  bool allocate_atlas(int slot,int parity=-1) {
    for(int i=0;i<HALF;++i) {
      if(parity>=0&&i%2!=parity)continue;
      auto *&image=chunks[slot][i];image=psram(layout.count*2);
      if(!image){
        for(int k=0;k<HALF;++k)if((parity<0||k%2==parity)&&chunks[slot][k]){psram_free(chunks[slot][k]);chunks[slot][k]=nullptr;}
        return false;
      }
    }
    return true;
  }
  bool has_image(int slot,int i) const { return slot>=0&&(cache[slot]||chunks[slot][i]); }
  // The images of one parity that are still missing (a cover built ahead has only its first nine). On failure only
  // the ones allocated here are freed.
  bool ensure_atlas(int slot,int parity) {
    if(!owned_cache||cache[slot])return true;
    bool fresh[HALF]{};
    for(int i=parity<0?0:parity;i<HALF;i+=parity<0?1:2) {
      if(chunks[slot][i])continue;
      chunks[slot][i]=psram(layout.count*2);fresh[i]=true;
      if(!chunks[slot][i]){
        for(int k=0;k<HALF;++k)if(fresh[k]&&chunks[slot][k]){psram_free(chunks[slot][k]);chunks[slot][k]=nullptr;}
        return false;
      }
    }
    return true;
  }
  // A new cover builds every sixth image first (9, 20 degrees apart) and turns from them at once, blending, then the
  // rest of the even ones and the odd ones (2026-10-30, Joel: as fast as it goes). Sixth, not fourth or third: 54 is
  // a multiple of 6, so the mirrored half-turn lands on built images too, and every sixth is even, inside the even
  // half allocated first. The old atlas goes at the first stage, so a change still needs 1.5 atlases at most.
  static constexpr int COARSE=6, FIRST=HALF/COARSE;
  static int build_image(int k) {
    if(k<FIRST)return COARSE*k;
    if(k<HALF/2){const int r=k-FIRST;return 2*(r+r/2+1);}   // the even images between: 2,4, 8,10, 14,16 ...
    return 2*(k-HALF/2)+1;
  }
  // Worker only. The atlas not turning is freed between covers (about 7 MB of PSRAM, 2026-10-29). Without room for
  // a second one the new cover builds over the turning one, which then holds still until the new cover turns.
  int claim_build_slot(bool from_builder=false) {
    const int slot=active==0?1:0,other=1-slot;
    make_room(missing_bytes(slot,0));
    if(owned_cache&&!ensure_atlas(slot,0)) {
      release_atlas(slot);   // a cover built ahead in it is lost too: never a slot with missing images
      ESP_LOGW("vinyl","%s: no room for a second atlas (%u kB); the record holds still while the "
               "new cover builds",name,unsigned(atlas_bytes()/1024));
    }
    if(has_atlas(slot))return slot;
    if(!has_atlas(other))return -1;
    if(from_builder&&active==other){build_in_worker.store(true);return -1;}   // only the worker may stop the record
    if(active==other)active=-1;
    return other;
  }
  void release_atlas(int slot) {
    if(owned_cache&&slot>=0)for(auto *&image:chunks[slot])if(image){psram_free(image);image=nullptr;}
  }
  // A new request swaps immutable input slots only between bounded preparation batches.
  // Worker: a cover to build, or the odd half of the one already turning.
  bool building() const { return offered.load()!=prepared.load()||build_slot>=0; }
  bool prepare_batch(int rows=4,bool from_builder=false) {
    if(!building()) return false;
    bool turn_now=false;
    {
      std::lock_guard<std::mutex> guard(source_mutex);
      if(build_gen!=generation) {
        end_fade(true);   // the next cover may need the old slot, or adopt the fading cover from `behind`
        build_gen=generation;build_input=pending_input;build_size=copy_size;build_row=0;build_frame=0;build_picture=picture;
        build_since=esphome::millis();prepare_cpu_us.store(0);prepare_rows_done.store(0);
        publish.pending=false;   // a superseded cover's half never turns
        build_in_worker.store(false);
        if(adopt.gen==generation&&adopt.behind) {
          // The cover turned before this one: its first stage moves from `behind` into the free slot.
          adopt.slot=active==0?1:0;adopt.frame=FIRST;adopt.row=0;adopt.input=build_input;adopt.size=build_size;
          for(int k=0;k<FIRST;++k){auto *&to=chunks[adopt.slot][COARSE*k];if(to&&owned_cache)psram_free(to);
            to=chunks[2][COARSE*k];chunks[2][COARSE*k]=nullptr;}
          behind_id=0;
        }
        if(adopt.gen==generation&&adopt.slot!=active) {
          // Built ahead: go on from its finished images, and turn them at once when the first stage is complete.
          build_slot=adopt.slot;build_input=adopt.input;build_size=adopt.size;build_frame=adopt.frame;build_row=adopt.row;
          make_room(missing_bytes(build_slot,0));
          if(!ensure_atlas(build_slot,0)){release_atlas(build_slot);build_slot=claim_build_slot(from_builder);build_frame=build_row=0;}
          if(build_slot>=0&&build_frame>=FIRST&&build_row==0) {
            publish={build_slot,build_size,COARSE,build_gen,true,build_picture};
            ESP_LOGI("vinyl","%s: new cover turns at once (built %s)",name,adopt.behind?"before":"ahead");
            turn_now=true;
          }
        }
        // A superseded cover keeps its slot; only a fresh build claims one.
        else if(build_slot<0||build_slot==active||!has_atlas(build_slot))build_slot=claim_build_slot(from_builder);
        if(from_builder&&build_in_worker.load()){
#ifdef USE_ESP32
          if(task)xTaskNotifyGive(task);
#endif
          return false;
        }
      }
      if(build_slot<0&&(publish.pending||build_in_worker.load()))return false;   // the worker still has a step to do
    }
    if(turn_now)handover(from_builder);
    if(build_slot<0){prepared.store(build_gen);return false;}
    const int end=std::min(build_row+std::max(rows,1),size());
    const uint32_t began=esphome::micros();
    const int image_index=build_image(build_frame);
    prepare_rows(inputs[build_input],build_size,frame_ptr(build_slot,image_index),layout,
                 build_row,end,image_index*(180.0f/HALF),layer_rgb,layer_alpha);
    prepare_cpu_us.fetch_add(esphome::micros()-began);
    prepare_rows_done.store(build_frame*size()+end);
    build_row=end;
    if(build_row==size()){build_row=0;++build_frame;}
    if(build_row==0&&(build_frame==FIRST||build_frame==HALF/2||build_frame==HALF)) {
      // Publish only the current generation, never a cancelled cover: first its even half, then the whole atlas.
      std::unique_lock<std::mutex> guard(source_mutex);
      if(build_gen==generation&&build_frame==FIRST) {
        publish={build_slot,build_size,COARSE,build_gen,true,build_picture};
        ESP_LOGI("vinyl","%s: new cover turns after %u ms (every %dth image)%s",name,
                 unsigned(esphome::millis()-build_since),COARSE,from_builder?", built beside the worker":"");
        guard.unlock();handover(from_builder);
      } else if(build_gen==generation&&build_frame==HALF/2) {
        publish={build_slot,build_size,2,build_gen,true,build_picture};
        ESP_LOGI("vinyl","%s: new cover sharper after %u ms (half its images)",name,unsigned(esphome::millis()-build_since));
        end_fade(true);   // the odd images need the old atlas's room
        make_room(missing_bytes(build_slot,1));
        if(owned_cache&&!ensure_atlas(build_slot,1)) {
          ESP_LOGW("vinyl","%s: no room for the odd images; the record keeps turning from half its atlas",name);
          build_slot=-1;prepare_ms.store(esphome::millis()-build_since);
          guard.unlock();handover(from_builder);return false;
        }
        guard.unlock();handover(from_builder);
      } else if(build_gen==generation) {
        publish={build_slot,build_size,1,build_gen,true,build_picture};build_slot=-1;
        prepare_ms.store(esphome::millis()-build_since);
        ESP_LOGI("vinyl","%s: new cover complete in %u ms",name,unsigned(prepare_ms.load()));
        guard.unlock();handover(from_builder);
        return false;
      }
    }
    return true;
  }
  // The worker switches to a published atlas between images; the builder only wakes it.
  void handover(bool from_builder) {
    if(from_builder) {
#ifdef USE_ESP32
      if(task)xTaskNotifyGive(task);
#endif
      return;
    }
    apply_publish();
  }
  // Worker: the builder found no room for a second atlas. Only the worker may stop the record (active=-1) so the
  // builder can draw over the atlas it turned; then the builder goes on.
  void claim_for_builder() {
    {
      std::lock_guard<std::mutex> guard(source_mutex);
      if(build_slot<0&&build_gen==generation)build_slot=claim_build_slot(false);
      build_in_worker.store(false);
    }
#ifdef USE_ESP32
    if(builder)xTaskNotifyGive(builder);
#endif
  }
  // Worker, under source_mutex: the atlas left keeps its first stage as `behind` (complete, it turned); the rest goes.
  void keep_behind(int old,uint64_t id) {
    if(old<0)return;
    if(!owned_cache||!chunks[old][0]){release_atlas(old);behind_id=0;return;}
    for(int k=0;k<FIRST;++k){const int i=COARSE*k;if(chunks[2][i])psram_free(chunks[2][i]);
      chunks[2][i]=chunks[old][i];chunks[old][i]=nullptr;}
    behind_id=id;
    release_atlas(old);
  }
  // Under source_mutex: the old atlas goes as at the switch, its first stage to `behind`, unless a pending
  // "föregående" adopts the cover already in `behind`.
  void end_fade(bool early) {
    if(!fade.on)return;
    fade.on=false;fade_on.store(false,std::memory_order_release);
    if(early)fade_cut.fetch_add(1);
    if(adopt.gen==generation&&adopt.behind&&adopt.want==behind_id&&adopt.want!=fade.picture)release_atlas(fade.slot);
    else keep_behind(fade.slot,fade.picture);
    fade.slot=-1;
  }
  // Worker only (or the single task of the native tests).
  void apply_publish() {
    std::lock_guard<std::mutex> guard(source_mutex);
    if(!publish.pending)return;
    publish.pending=false;
    if(publish.gen!=generation)return;
    const int old=active;
    if(publish.slot!=active) {
      end_fade(true);   // a change during a fade: that older cover goes now
      const uint64_t leaving=active_picture;const int leaving_step=active_step;
      active=publish.slot;active_size=publish.size;active_gen=publish.gen;
      if(fade_enabled&&old>=0&&leaving&&leaving!=publish.picture&&has_atlas(old)) {
        fade.on=true;fade.started=false;fade.slot=old;fade.step=leaving_step;fade.picture=leaving;
        fade_on.store(true,std::memory_order_release);fades.fetch_add(1);
        ESP_LOGI("vinyl","%s: old cover fades out over %d images",name,FADE_FRAMES);
      } else {
        keep_behind(old,leaving);
        if(old>=0)ESP_LOGI("vinyl","%s: old atlas freed, its first stage kept",name);
      }
      active_picture=publish.picture;
    }
    active_step=publish.step;
    prepared.store(publish.gen);
  }
  bool gpu_frame(uint16_t *destination) {
    return asked_interpolate&&gpu_sample(destination,active,active_step,asked_angle,true);
  }
  // PPA: atlas `slot` at `angle`, blended from its two images `step` apart into `destination`, packed, then
  // expanded unless `expand` is false (a fade blends the old cover over the packed pixels first).
  bool gpu_sample(uint16_t *destination,int slot,int step,float angle,bool expand) {
#ifdef USE_ESP32
    if(!blender || layout.count%64) return false;
    const auto chosen=pick(angle,true,step);
    const uint32_t w=chosen.w;
    if(!w)return false;
    const int lo=chosen.lo,hi=chosen.hi;
    const uint16_t *a=frame_ptr(slot,lo%HALF),*b=frame_ptr(slot,hi%HALF);
    const bool flip_a=lo>=HALF,flip_b=hi>=HALF;
    // Only a half-turn boundary needs reversed input. A bounded internal 4k-pixel tile replaces a
    // full 129kB PSRAM flip buffer, leaving room for the artwork worker's immutable double buffers.
    const bool mixed=flip_a!=flip_b;
    if(mixed&&!blend_flip)return false;
    for(size_t offset=0;offset<layout.count;) {
      const size_t amount=mixed?std::min<size_t>(4096,layout.count-offset):layout.count;
      const uint16_t *bg=a+offset,*fg=b+offset;
      if(mixed) {
        const uint16_t *in=flip_a?a:b;
        std::memcpy(blend_flip,in+layout.count-offset-amount,amount*2);
        std::reverse(blend_flip,blend_flip+amount);
        if(flip_a)bg=blend_flip;else fg=blend_flip;
      }
      ppa_blend_oper_config_t config{};config.in_bg.buffer=bg;config.in_fg.buffer=fg;
      for(auto *in:{&config.in_bg,&config.in_fg}) {
        in->pic_w=in->block_w=64;in->pic_h=in->block_h=amount/64;in->blend_cm=PPA_BLEND_COLOR_MODE_RGB565;
      }
      config.out.buffer=destination+offset;config.out.buffer_size=(pixels()-offset)*2;
      config.out.pic_w=64;config.out.pic_h=amount/64;config.out.blend_cm=PPA_BLEND_COLOR_MODE_RGB565;
      config.bg_alpha_update_mode=PPA_ALPHA_FIX_VALUE;config.bg_alpha_fix_val=255;
      config.fg_alpha_update_mode=PPA_ALPHA_FIX_VALUE;config.fg_alpha_fix_val=w*8;config.mode=PPA_TRANS_MODE_BLOCKING;
      const auto result=ppa_do_blend(blender,&config);
      if(result!=ESP_OK) {
        ++gpu_errors;ESP_LOGW("vinyl","PPA blend failed: %s",esp_err_to_name(result));
        if(layer_rgb)std::memcpy(destination,layer_rgb,pixels()*2);
        return false;
      }
      offset+=amount;
    }
    ++gpu_blends;
    if(flip_a&&flip_b)reverse_staged(destination,layout.count,blend_flip,4096);
    if(expand)expand_in_place(destination,layout,layer_rgb);
    return true;
#else
    (void)destination;(void)slot;(void)step;(void)angle;(void)expand;return false;
#endif
  }
  // Packed image `i` of `slot` (mirrored past HALF) into `destination`, no interpolation.
  void packed_image(uint16_t *destination,int slot,int i) const {
    const uint16_t *a=frame_ptr(slot,i%HALF);
    if(i<HALF)std::memcpy(destination,a,layout.count*2);
    else std::reverse_copy(a,a+layout.count,destination);
  }
  // Packed: `dst` = image `i` of `slot` (mirrored past HALF) at weight `keep` (0..255) over `dst`. PPA in place,
  // a mirrored image reversed through the internal tile as gpu_sample does; else the CPU.
  void blend_packed(uint16_t *dst,int slot,int i,uint32_t keep) {
    const uint16_t *over=frame_ptr(slot,i%HALF);const bool flip=i>=HALF;const size_t count=layout.count;
#ifdef USE_ESP32
    if(blender&&count%64==0&&(!flip||blend_flip)) {
      bool ok=true;
      for(size_t offset=0;offset<count&&ok;) {
        const size_t amount=flip?std::min<size_t>(4096,count-offset):count;
        const uint16_t *fg=over+offset;
        if(flip){std::memcpy(blend_flip,over+count-offset-amount,amount*2);std::reverse(blend_flip,blend_flip+amount);fg=blend_flip;}
        ppa_blend_oper_config_t config{};config.in_bg.buffer=dst+offset;config.in_fg.buffer=fg;
        for(auto *in:{&config.in_bg,&config.in_fg}) {
          in->pic_w=in->block_w=64;in->pic_h=in->block_h=amount/64;in->blend_cm=PPA_BLEND_COLOR_MODE_RGB565;
        }
        config.out.buffer=dst+offset;config.out.buffer_size=(pixels()-offset)*2;
        config.out.pic_w=64;config.out.pic_h=amount/64;config.out.blend_cm=PPA_BLEND_COLOR_MODE_RGB565;
        config.bg_alpha_update_mode=PPA_ALPHA_FIX_VALUE;config.bg_alpha_fix_val=255;
        config.fg_alpha_update_mode=PPA_ALPHA_FIX_VALUE;config.fg_alpha_fix_val=keep;config.mode=PPA_TRANS_MODE_BLOCKING;
        if(ppa_do_blend(blender,&config)!=ESP_OK){++gpu_errors;ok=false;break;}
        offset+=amount;
      }
      if(ok){++gpu_blends;return;}
      // A failed pass may have blended part of `dst`: the image stays usable, only that part is faded twice.
    }
#endif
    const uint32_t w=(keep*32+127)/255;
    for(size_t k=0;k<count;++k)dst[k]=pack565(mix565(spread565(dst[k]),spread565(over[flip?count-1-k:k]),w));
  }
  // Worker: a fade image, the new cover with the old one's sharp image at the same angle over it. False when no
  // fade runs (then the usual path draws the job). The old slot is read under source_mutex: the builder may end
  // the fade and free it.
  bool fade_frame(uint16_t *dst,const Job &j) {
    if(!fade_on.load(std::memory_order_acquire))return false;
    std::lock_guard<std::mutex> guard(source_mutex);
    if(!fade.on)return false;
    if(!fade.started){fade.started=true;fade.from=j.frame;}
    const int k=int(int32_t(j.frame-fade.from));
    if(k>=FADE_FRAMES){end_fade(false);return false;}
    if(!(asked_interpolate&&gpu_sample(dst,active,active_step,asked_angle,false)))
      packed_image(dst,active,pick(asked_angle,false,active_step).lo);
    blend_packed(dst,fade.slot,pick(j.angle,false,fade.step).lo,fade_weight(k));
    expand_in_place(dst,layout,layer_rgb);
    fade_images.fetch_add(1);
    return true;
  }
  // Worker: draw one queued job into its own slot. Strip painting does not need an atlas.
  void render(int s=0) {
    Job &j=jobs[s];
    const uint32_t start=esphome::micros();
    if(active>=0) {
      asked_angle=j.angle;asked_interpolate=j.interpolate||active_step>1;
      const bool faded=fade_frame(output[s],j);
      const bool gpu=faded||gpu_frame(output[s]);
      const bool staged=asked_interpolate&&blend_flip&&!gpu;
      if(!gpu) {
        if(arm_async)restore_static(output[s],layout,layer_rgb);
        atlas_frame_at([&](int i){return frame_ptr(active,i);},output[s],layout,asked_angle,asked_interpolate,blend_flip,4096,
                       active_step);
      }
      sample_method.store(!asked_interpolate?0:staged?1:gpu?2:3);
    }
    const uint32_t sampled=esphome::micros(),sampled_us=sampled-start;
    if(active>=0){sample_us.store(sampled_us);sample_max_us.store(std::max(sampled_us,sample_max_us.load()));}
    if(arm_async) {
      j.arm_ok=prepare_arm(j.arm,work_masks);
      if(!j.arm_ok){++arm_errors;ESP_LOGE("vinyl","Worker arm mask bounds refused");}
      else {
        if(active>=0)composite_arm(output[s],size(),j.x,j.y,work_masks);
        if(right_arm.enabled&&j.paint_strip&&j.strip>=0)right_arm.paint(right_arm.pixels[j.strip],work_masks);
      }
    }
    const uint32_t armed_us=esphome::micros()-sampled;
    arm_prepare_us.store(armed_us);arm_prepare_max_us.store(std::max(armed_us,arm_prepare_max_us.load()));
    if(active<0){slot_state[s].store(FREE,std::memory_order_release);return;}
    work_us.store(esphome::micros()-start); work_total.fetch_add(1);
    output_gen[s]=active_gen;
    slot_state[s].store(READY,std::memory_order_release);
  }
#ifdef USE_ESP32
  void work() {
    for(;;) {
      ulTaskNotifyTake(pdTRUE,portMAX_DELAY);
      do {
        apply_publish();
        for(int s;(s=next_work())>=0;)render(s);
        if(builder&&build_in_worker.load()){claim_for_builder();break;}
        if(!building()||builder)break;
        const bool visible=foreground.load(std::memory_order_acquire);
        const bool timed=clock.pending&&clock.live();
        const uint32_t stamp=timed?clock.last_us->load(std::memory_order_acquire):0;
        const bool pending=timed&&clock.pending->load(std::memory_order_acquire);
        const int rows=atlas_budget(visible,timed,pending,timed?esphome::micros()-stamp:0,active>=0,active_step==1&&queued_ahead());
        if(!rows) {
          vTaskDelay(1);continue; // Retry read-only budget; new display work still runs first on every loop.
        }
        if(!prepare_batch(rows)) break;
        // Remain responsive to frame requests while preparing the other cover.
        taskYIELD();
      } while(true);
    }
  }
  void build_work() {
    for(;;) {
      ulTaskNotifyTake(pdTRUE,portMAX_DELAY);
      while(!build_in_worker.load()&&(prepare_batch(16,true)||ahead_batch(16)))taskYIELD();
    }
  }
#endif
  // Main task. The worker can lag a queued job by up to `depth` boundaries before a frame repeats.
  int depth(bool timed) const { return timed&&right_arm.enabled?2:1; }
  // Worker: the oldest queued job. The main task may queue two at once (it ticks once per frame while LVGL renders).
  int next_work() const {
    int best=-1;
    for(int i=0;i<SLOTS;++i)if(slot_state[i].load(std::memory_order_acquire)==WORK&&
        (best<0||int32_t(jobs[i].frame-jobs[best].frame)<0))best=i;
    return best;
  }
  int free_slot() const {
    for(int i=0;i<SLOTS;++i)if(i!=displayed&&slot_state[i].load(std::memory_order_acquire)==FREE)return i;
    return -1;
  }
  // A strip no shown or queued image uses. The pool holds one more than can be in use.
  int free_strip() const {
    for(int k=0;k<RightArm::POOL;++k) {
      bool used=k==right_arm.shown;
      for(int i=0;i<SLOTS&&!used;++i){const int state=slot_state[i].load(std::memory_order_acquire);
        used=(state==WORK||state==READY||state==SHOWN)&&jobs[i].strip==k;}
      if(!used)return k;
    }
    return -1;
  }
  // Asks for the image the boundary `target` takes: motion predicted to that boundary, then one scanout on.
  bool ask(uint32_t target,bool timed) {
    if(!output[0] || prepared.load()==0) return false;
    const int s=free_slot();if(s<0)return false;
    Motion future=motion;
    const int steps=int(target-frame_now)+(timed?0:1);
    for(int i=0;i<steps;++i)future.step(FRAME_S,playing||test);
    Job &j=jobs[s];j.frame=target;j.angle=future.angle;j.interpolate=future.speed<0.9999f;
    j.x=lv_obj_get_x(frame);j.y=lv_obj_get_y(frame);
    j.arm=panel_vinyl::arm(j.x+radius,j.y+radius,radius,future.lift,groove);j.lift=future.lift;j.groove=groove;
    j.strip=-1;j.paint_strip=false;
    if(right_arm.enabled) {
      const auto pose=rounded_pose(j.arm);
      if(strip_last>=0&&pose==strip_pose)j.strip=strip_last;   // the same arm: share the strip already painted
      else {j.strip=free_strip();j.paint_strip=j.strip>=0;if(j.paint_strip){strip_pose=pose;strip_last=j.strip;}}
    }
    next_frame=target+1;
    slot_state[s].store(WORK,std::memory_order_release);
#ifdef USE_ESP32
    if(task) {xTaskNotifyGive(task); return true;}
#endif
    render(s);
    return true;
  }
  // The newest ready image due by now; older ready ones are dropped. -1 when none is due.
  int take_due() {
    int best=-1;
    for(int i=0;i<SLOTS;++i) {
      if(slot_state[i].load(std::memory_order_acquire)!=READY)continue;
      if(int32_t(jobs[i].frame-first_frame)<0){slot_state[i].store(FREE,std::memory_order_release);continue;}
      if(int32_t(jobs[i].frame-frame_now)<=0&&(best<0||int32_t(jobs[i].frame-jobs[best].frame)>0))best=i;
    }
    if(best<0)return -1;
    for(int i=0;i<SLOTS;++i)if(i!=best&&slot_state[i].load(std::memory_order_acquire)==READY&&
        int32_t(jobs[i].frame-jobs[best].frame)<0)slot_state[i].store(FREE,std::memory_order_release);
    return best;
  }
  // Worker: a finished image beyond the one taken next is queued, so the record has a frame of slack. Not while it
  // turns from half an atlas: those images are PPA blends, and building alongside them dropped frames (flow-6).
  bool queued_ahead() const {
    int ready=0;for(const auto &state:slot_state)ready+=state.load(std::memory_order_acquire)==READY;
    return ready>=2;
  }
  bool any_ready() const {
    for(const auto &state:slot_state)if(state.load(std::memory_order_acquire)==READY)return true;
    return false;
  }
  void drop_ready() {
    for(auto &state:slot_state){int expected=READY;state.compare_exchange_strong(expected,FREE);}
    strip_last=-1;
  }
  uint64_t identity(const lv_image_dsc_t *cover_, Kind kind) const {
    uint64_t h = 1469598103934665603ull ^ uint64_t(kind);
    if (kind != Kind::BLANK && cover_) {
      const uint8_t *bytes = cover_->data;
      const size_t n = size_t(cover_->header.w) * cover_->header.h * 2;
      for (size_t i = 0; i < n; i += 7) h = (h ^ bytes[i]) * 1099511628211ull;   // a sample is enough to tell covers apart
      h = (h ^ n) * 1099511628211ull;
    }
    return h;
  }

  void paint(const lv_image_dsc_t *cover_, Kind kind) {
    if (kind == Kind::PICTURE) {
      copy_size = cover_->header.w;
      std::memcpy(copy, cover_->data, size_t(copy_size) * copy_size * 2);
      return;
    }
    // A black record: the body, and inside LABEL of its radius the logo sampled down (or a plain label).
    copy_size = size();
    const int n = copy_size;
    const float c = n / 2.0f, label = LABEL * c;
    const uint16_t body = ((0x15 >> 3) << 11) | ((0x15 >> 2) << 5) | (0x18 >> 3);
    const uint16_t plain = ((0x3A >> 3) << 11) | ((0x40 >> 2) << 5) | (0x52 >> 3);
    const uint16_t *logo = kind == Kind::LABEL && cover_ ? reinterpret_cast<const uint16_t *>(cover_->data) : nullptr;
    const int logo_n = logo ? int(cover_->header.w) : 0;
    for (int y = 0; y < n; ++y)
      for (int x = 0; x < n; ++x) {
        const float dx = x + 0.5f - c, dy = y + 0.5f - c;
        uint16_t value = body;
        if (dx * dx + dy * dy < label * label) {
          if (logo) {    // the logo's square fills the label's square; the circle crops it
            int lx = int((dx + label) / (2 * label) * logo_n), ly = int((dy + label) / (2 * label) * logo_n);
            lx = lx < 0 ? 0 : lx >= logo_n ? logo_n - 1 : lx;
            ly = ly < 0 ? 0 : ly >= logo_n ? logo_n - 1 : ly;
            value = logo[ly * logo_n + lx];
          } else {
            value = plain;
          }
        }
        copy[y * n + x] = value;
      }
  }


  bool usable(const lv_image_dsc_t *d) const {
    return d && d->data && d->header.cf==LV_COLOR_FORMAT_RGB565 && d->header.w==d->header.h &&
      d->header.w>=16 && d->header.w<=COVER_MAX && (!d->header.stride || d->header.stride==d->header.w*2);
  }
  void source(const lv_image_dsc_t *src, Kind kind,uint64_t prepared_identity=0) {
    if(!copy || (!has_atlas(0) && !has_atlas(1))) return;   // the second atlas comes per cover (claim_build_slot)
    if(kind!=Kind::BLANK && !usable(src)) return;
    const uint64_t id=kind==Kind::PICTURE&&prepared_identity?prepared_identity:identity(src,kind);
    if(id==picture && offered.load()) return;
    {
      std::lock_guard<std::mutex> guard(source_mutex);
      if(kind==Kind::PICTURE&&id==ahead.id&&ahead.slot>=0&&ahead.slot!=active&&(ahead.built>0||ahead.row>0)) {
        // The picture built ahead: its input already holds it, and the build goes on from what is finished.
        pending_input=ahead.input;copy=inputs[pending_input];copy_size=ahead.size;picture=id;cover=src;
        offered.store(++generation);
        adopt={generation,ahead.slot,ahead.input,ahead.size,ahead.built,ahead.row};
      } else {
        pending_input=build_input==0?1:0;copy=inputs[pending_input];
        picture=id; cover=src; paint(src,kind); offered.store(++generation);
        // "Föregående": the picture turned before this one still has its first stage.
        if(kind==Kind::PICTURE&&id&&((id==behind_id&&chunks[2][0])||(fade.on&&id==fade.picture)))
          adopt={generation,-1,-1,0,0,0,true,id};
      }
      ahead=Ahead{};
    }
#ifdef USE_ESP32
    if(task) xTaskNotifyGive(task);
    if(builder) xTaskNotifyGive(builder);
#else
    while(prepare_batch()) {}
#endif
  }
  // Main task: the next track's cover (async_artwork's prefetch), built ahead while nothing else builds. A real
  // source with the same identity adopts it; any other drops it.
  void source_ahead(const uint16_t *pixels,int side,uint64_t id) {
    if(!pixels||!id||side<=0||side>COVER_MAX||id==picture||!copy)return;
    {
      std::lock_guard<std::mutex> guard(source_mutex);
      if(ahead.id==id||offered.load()!=prepared.load()||build_slot>=0||publish.pending||fade.on)return;
      const int input=build_input==0?1:0;
      ahead={id,active==0?1:0,input,side,0,0};
      std::memcpy(inputs[input],pixels,size_t(side)*side*2);
    }
#ifdef USE_ESP32
    if(builder)xTaskNotifyGive(builder);
#else
    while(ahead_batch(size())){}
#endif
  }
  // Builder: one row batch of the cover ahead, while no real cover builds. False when there is nothing to do.
  bool ahead_batch(int rows) {
    int slot,input,side,image,first;
    {
      std::lock_guard<std::mutex> guard(source_mutex);
      if(!ahead.id||ahead.built>=FIRST||offered.load()!=prepared.load()||build_slot>=0||publish.pending||fade.on)return false;
      if(ahead.slot==active){ahead=Ahead{};return false;}
      slot=ahead.slot;input=ahead.input;side=ahead.size;image=COARSE*ahead.built;first=ahead.row;
    }
    if(owned_cache&&!chunks[slot][image]) {
      // Building ahead must leave a whole change's room: the even half it still needs, above KEEP_FREE.
      if(psram_left()<KEEP_FREE+missing_bytes(slot,0)){std::lock_guard<std::mutex> guard(source_mutex);ahead=Ahead{};
        ESP_LOGI("vinyl","%s: not building the next cover ahead: PSRAM is short",name);return false;}
      chunks[slot][image]=psram(layout.count*2);
      if(!chunks[slot][image]){std::lock_guard<std::mutex> guard(source_mutex);ahead=Ahead{};
        ESP_LOGI("vinyl","%s: no room to build the next cover ahead",name);return false;}
    }
    if(!has_image(slot,image)){std::lock_guard<std::mutex> guard(source_mutex);ahead=Ahead{};return false;}
    const int end=std::min(first+std::max(rows,1),size());
    prepare_rows(inputs[input],side,frame_ptr(slot,image),layout,first,end,image*(180.0f/HALF),layer_rgb,layer_alpha);
    std::lock_guard<std::mutex> guard(source_mutex);
    if(ahead.slot!=slot||ahead.input!=input||ahead.built*COARSE!=image)return true;   // adopted or replaced meanwhile
    ahead.row=end==size()?0:end;
    if(end==size()&&++ahead.built==FIRST)ESP_LOGI("vinyl","%s: next cover's first %d images built ahead",name,FIRST);
    return ahead.built<FIRST;
  }
  // Main task: the panel's own next button. The cover built ahead turns now, before Home Assistant confirms the skip
  // (2026-10-30); the confirmed track's cover is the same picture and changes nothing, another replaces it as usual,
  // and a failed skip puts the previous one back from `behind` (now_playing.board).
  bool take_ahead() {
    {
      std::lock_guard<std::mutex> guard(source_mutex);
      if(!ahead.id||ahead.built<FIRST||ahead.row||ahead.slot==active||offered.load()!=prepared.load()||build_slot>=0||
         publish.pending)return false;
      pending_input=ahead.input;copy=inputs[pending_input];copy_size=ahead.size;picture=ahead.id;
      offered.store(++generation);adopt={generation,ahead.slot,ahead.input,ahead.size,ahead.built,ahead.row,false};
      ahead=Ahead{};
    }
    ESP_LOGI("vinyl","%s: next cover turns before the skip is confirmed",name);
#ifdef USE_ESP32
    if(builder)xTaskNotifyGive(builder);
    if(task)xTaskNotifyGive(task);
#else
    while(prepare_batch()) {}
#endif
    return true;
  }
  bool fresh() const { return shown_gen==offered.load(); }
  void show_parts(bool visible) {
    for(auto *p:all_parts()) {
      if(visible&&!right_arm.enabled) lv_obj_remove_flag(p,LV_OBJ_FLAG_HIDDEN); else lv_obj_add_flag(p,LV_OBJ_FLAG_HIDDEN);
    }
    if(right_arm.enabled){if(visible&&right_arm.shown>=0)lv_obj_remove_flag(right_image,LV_OBJ_FLAG_HIDDEN);
      else lv_obj_add_flag(right_image,LV_OBJ_FLAG_HIDDEN);}
    lv_obj_set_style_clip_corner(frame,false,LV_PART_MAIN); lv_obj_set_style_radius(frame,0,LV_PART_MAIN);
    if(visible) lv_obj_remove_flag(frame,LV_OBJ_FLAG_HIDDEN); else lv_obj_add_flag(frame,LV_OBJ_FLAG_HIDDEN);
    shown_lift=-1;have_arm_pose=false;
  }
  void set(bool value) {
    if(value!=playing) ESP_LOGI("vinyl","%s record %s",name,value?"starts":"stops, arm lifts");
    if(value!=playing)previous_ms=0;
    playing=value;
  }
  void set_groove(float fraction) { groove_target=std::max(0.0f,std::min(fraction,1.0f)); }
  void test_spin(bool value) {test=value; if(value) motion.settle(true);}
  void hide(bool value) {if(hidden!=value){hidden=value; if(on)show_parts(!hidden);}}
  std::array<lv_obj_t *, 8> all_parts() const {
    return {parts.base, parts.pivot, parts.rest, parts.weight, parts.arm, parts.head_rim, parts.head, parts.cart};
  }

  static void put(lv_obj_t *dot, float x, float y) {   // a round widget centred on (x, y)
    lv_obj_set_pos(dot, int(x) - lv_obj_get_width(dot) / 2, int(y) - lv_obj_get_height(dot) / 2);
  }

  bool prepare_arm(const Arm &a,std::array<LineMask,6> &masks) const {
    const float k=radius/144.0f;
    return masks[0].paint(int(a.pivot_x),int(a.pivot_y),int(a.weight_x),int(a.weight_y),10*k)&&
      masks[2].paint(int(a.pivot_x),int(a.pivot_y),int(a.end_x),int(a.end_y),8*k)&&
      masks[3].paint(int(a.end_x),int(a.end_y),int(a.head_x),int(a.head_y),18*k)&&
      masks[4].paint(int(a.end_x),int(a.end_y),int(a.head_x),int(a.head_y),14*k)&&
      masks[5].paint(int(a.cart_x0),int(a.cart_y0),int(a.cart_x1),int(a.cart_y1),8*k);
  }
  bool copy_arm() {
    for(int i:{0,2,3,4,5})if(!line_masks[i].pixels||!work_masks[i].pixels||
      size_t(work_masks[i].w)*work_masks[i].h>line_masks[i].capacity)return false;
    for(int i:{0,2,3,4,5}) {
      auto &to=line_masks[i];const auto &from=work_masks[i];
      auto *owned=to.pixels;const auto capacity=to.capacity;
      std::memcpy(owned,from.pixels,size_t(from.w)*from.h);
      to=from;to.pixels=owned;to.capacity=capacity; // Never publish a pointer the worker can overwrite.
    }
    return true;
  }

  void place_arm() {
#ifdef USE_ESP32
    const uint32_t arm_start=esphome::micros();
#endif
    float cx = lv_obj_get_x(frame) + radius, cy = lv_obj_get_y(frame) + radius;
    Arm a = arm_async?ready_arm:panel_vinyl::arm(cx, cy, radius, motion.lift, groove);
    const auto pose=rounded_pose(a);
    if(have_arm_pose&&pose==arm_pose) {
#ifdef USE_ESP32
      arm_work_us=esphome::micros()-arm_start;
#endif
      return;
    }
    arm_pose=pose;have_arm_pose=true;
#ifdef USE_ESP32
    auto *display=lv_display_get_default();
    const bool cached=line_masks[0].pixels&&line_masks[2].pixels&&line_masks[3].pixels&&line_masks[4].pixels&&line_masks[5].pixels;
    const bool invalidate=cached&&display&&lv_display_is_invalidation_enabled(display);
    // Flush unrelated pending layouts before suppressing the arm's overlapping automatic invalidations.
    if(invalidate)lv_obj_update_layout(lv_screen_active());
    lv_area_t record;lv_obj_get_coords(frame,&record);
    const DamageRect disc{record.x1,record.y1,record.x2,record.y2};ArmDamage damage;
    auto mark=[&](){for(const auto &mask:line_masks)if(mask.pixels&&mask.w&&mask.h)damage.merge(mask.coverage);};
#endif
    // These circles do not move with lift/groove. Keep normal invalidations for their initial placement.
    put(parts.base, a.pivot_x, a.pivot_y);put(parts.pivot, a.pivot_x, a.pivot_y);put(parts.rest, a.rest_x, a.rest_y);
#ifdef USE_ESP32
    if(invalidate){mark();lv_display_enable_invalidation(display,false);}
#endif
    if(arm_async&&!copy_arm()){++arm_errors;ESP_LOGE("vinyl","Arm publication bounds refused; main-task cache retained");}
    auto pt = [](float x, float y) {   // lv_value_precise_t is an int unless LVGL was built with floats
      return lv_point_precise_t{static_cast<lv_value_precise_t>(x), static_cast<lv_value_precise_t>(y)};
    };
    auto line = [&](int i, lv_obj_t *obj, float x0, float y0, float x1, float y1) {
      const float widths[6]={10,0,8,18,14,8};
      auto &mask=line_masks[i];
      if(mask.pixels && mask.paint(int(x0),int(y0),int(x1),int(y1),widths[i]*radius/144.0f)) {
        // The worker already drew the part inside the opaque record. Every outside pixel of the real arm
        // lies to its right (native full-motion guard), so these A8 widgets never cover the record's fast path.
        const int crop=arm_async?std::clamp(int(cx)+radius-mask.x,0,mask.w):0;
        if(crop==mask.w){lv_obj_add_flag(obj,LV_OBJ_FLAG_HIDDEN);return;}
        lv_obj_remove_flag(obj,LV_OBJ_FLAG_HIDDEN);
        auto &dsc=line_images[i];dsc.header.magic=LV_IMAGE_HEADER_MAGIC;dsc.header.cf=LV_COLOR_FORMAT_A8;
        dsc.header.w=mask.w-crop;dsc.header.h=mask.h;dsc.header.stride=mask.w;
        // LVGL validates full stride*height even though the last row only draws the cropped width.
        // Describe the actual remaining allocation, not the cropped rectangle's shorter payload.
        dsc.data=mask.pixels+crop;dsc.data_size=mask.capacity-crop;
        lv_obj_set_pos(obj,mask.x+crop,mask.y);lv_image_set_src(obj,&dsc);lv_obj_invalidate(obj);return;
      }
      if(mask.pixels){ESP_LOGE("vinyl","Arm mask bounds refused");return;}
      const float left=std::floor(std::min(x0,x1)), top=std::floor(std::min(y0,y1));
      lv_obj_set_pos(obj,int(left),int(top));
      points[i][0] = pt(x0-left, y0-top);
      points[i][1] = pt(x1-left, y1-top);
      lv_line_set_points(obj, points[i], 2);
    };
    line(0, parts.weight, a.pivot_x, a.pivot_y, a.weight_x, a.weight_y);
    line(2, parts.arm, a.pivot_x, a.pivot_y, a.end_x, a.end_y);
    line(3, parts.head_rim, a.end_x, a.end_y, a.head_x, a.head_y);
    line(4, parts.head, a.end_x, a.end_y, a.head_x, a.head_y);
    line(5, parts.cart, a.cart_x0, a.cart_y0, a.cart_x1, a.cart_y1);
#ifdef USE_ESP32
    if(invalidate) {
      lv_obj_update_layout(lv_screen_active());
      mark();
      lv_display_enable_invalidation(display,true);
      lv_obj_invalidate(image);
      damage.emit_compact(disc,[&](DamageRect r) {
        lv_area_t area{r.x1,r.y1,r.x2,r.y2};lv_obj_invalidate_area(lv_screen_active(),&area);
      });
    }
    arm_work_us=esphome::micros()-arm_start;
#endif
  }


  void tick() {
    float dt=0;bool boundary=false;
    const bool timed=clock.live();
    if(timed) {
      const uint32_t count=clock.count->load(std::memory_order_acquire);
      if(count!=applied){dt=(count-applied)*FRAME_S;frame_now+=count-applied;applied=count;boundary=true;}
    } else {
      const uint32_t now=lv_tick_get();
      if(now-last_ms>=16){dt=(now-last_ms)/1000.0f;last_ms=now;++frame_now;boundary=true;}
    }
    auto wanted=[&]{return motion.speed>0||motion.moving(playing||test)||shown_gen!=prepared.load()||
                    std::fabs(motion.lift-shown_lift)>.0001f;};
    const bool want=wanted();
    // Between boundaries (this runs every millisecond) only a late image or an idle worker with room matter.
    // Another widget may already have submitted this frame (a pressed button's transition): carry on regardless,
    // LVGL itself defers drawing until its framebuffer is free.
    if(!boundary&&!any_ready()&&!(want&&free_slot()>=0&&int32_t(next_frame-(frame_now+depth(timed)))<=0))return;
    const bool visible=!hidden && shown();
    priority.update(visible,[&](int level){
#ifdef USE_ESP32
      if(task)vTaskPrioritySet(task,level);
      // The builder above the idle task while Spelas nu shows (at 0 it shared core 0's idle slices: 9 images took
      // 1.6 s, 2026-10-30), below the worker; 0 elsewhere, so it never competes with the voice orb's prefetch.
      if(builder)vTaskPrioritySet(builder,level>0?1:0);
#endif
      if(on_visible)on_visible(level>0);
    });
    if(visible&&!open) {open=true; open_ms=lv_tick_get(); frames=0; motion.settle(playing||test); shown_lift=-1; dt=0; previous_ms=0;
      drop_ready();next_frame=first_frame=frame_now+1;}
    if(!visible&&open) {open=false; ESP_LOGI("vinyl","%s record left: %u images in %u ms",name,unsigned(frames),unsigned(lv_tick_get()-open_ms));}
    motion.step(dt,playing||test);
    // A paused page has no submitted frames to wait for. Its new cover must still finish without play.
    foreground.store(visible&&motion.moving(playing||test),std::memory_order_release);
    const float step=dt/1.5f;
    if(groove<groove_target)groove=std::min(groove_target,groove_target-groove>0.05f?groove+step:groove_target);
    else if(groove>groove_target)groove=std::max(groove_target,groove-step);
    // Consume only on the UI task: the worker never draws into the image LVGL shows or one still queued.
    const int result=take_due();
    if(result>=0) {
      if(visible) {
        if(displayed>=0&&displayed!=result)slot_state[displayed].store(FREE,std::memory_order_release);
        displayed=result;slot_state[result].store(SHOWN,std::memory_order_release);
        const Job &j=jobs[result];
        if(!on){on=true;show_parts(true);}
        lv_image_set_src(image,&output_dsc[result]); lv_obj_invalidate(image);
        if(right_arm.enabled) {
          if(j.strip>=0&&j.strip!=right_arm.shown) {
            lv_image_set_src(right_image,&right_images[j.strip]);lv_obj_remove_flag(right_image,LV_OBJ_FLAG_HIDDEN);
            lv_obj_invalidate(right_image);right_arm.shown=j.strip;
          }
          shown_lift=j.lift;shown_groove=j.groove;
        }
        ready_arm=j.arm;ready_arm_ok=j.arm_ok;
        ++(boundary?taken_boundary:taken_late);if(any_ready())++taken_spare;
        shown_gen=output_gen[result]; frames++;serial=(serial+1)&0x7fffffffu;if(!serial)serial=1;
        const uint32_t now=lv_tick_get();
        if(previous_ms) intervals[interval_total++%128]=now-previous_ms;
        previous_ms=now;
      }
      else slot_state[result].store(FREE,std::memory_order_release);
    }
    if(!visible) {drop_ready();return;}
    // Copy immutable worker arm data before asking for the next job, which may overwrite its scratch.
    if(!right_arm.enabled&&((arm_async&&result>=0&&ready_arm_ok)||(!arm_async&&
        (motion.lift!=shown_lift||std::fabs(groove-shown_groove)>0.002f)))) {
      place_arm();shown_lift=arm_async?jobs[result].lift:motion.lift;shown_groove=arm_async?jobs[result].groove:groove;
    }
    while(wanted()) {
      const uint32_t target=int32_t(next_frame-(frame_now+1))>0?next_frame:frame_now+1;
      if(int32_t(target-(frame_now+depth(timed)))>0||!ask(target,timed))break;
    }
  }
  std::function<std::string()> deferred_status() const {
    const auto values=intervals; const uint32_t total=interval_total, count=frames, us=work_us.load(), prep=prepare_ms.load();
    const uint32_t blends=gpu_blends.load(),errors=gpu_errors.load(),arm_failures=arm_errors.load();const bool async=arm_async,opaque=right_arm.enabled;
    const float speed=motion.speed; const uint32_t gen=shown_gen;
    const auto rects=render_rects;const uint32_t arm_us=arm_work_us,areas=render_areas,pixels=render_pixels,largest=render_largest;
    const uint32_t sampling=sample_us.load(),sample_max=sample_max_us.load(),arming=arm_prepare_us.load(),arm_max=arm_prepare_max_us.load();
    const uint32_t method=sample_method.load(),prep_cpu=prepare_cpu_us.load(),prep_rows=prepare_rows_done.load();
    const uint32_t on_boundary=taken_boundary,late=taken_late,spare=taken_spare;
    const uint32_t faded=fades.load(),fade_drawn=fade_images.load(),fade_early=fade_cut.load();const bool can_fade=fade_enabled;
    return [values,total,count,us,prep,speed,gen,blends,errors,rects,arm_us,areas,pixels,largest,arm_failures,async,opaque,sampling,sample_max,arming,arm_max,method,prep_cpu,prep_rows,on_boundary,late,spare,faded,fade_drawn,fade_early,can_fade]() {
      std::string s=",\"vinyl\":{\"frames\":"+std::to_string(count)+",\"speed\":"+std::to_string(speed)+
       ",\"generation\":"+std::to_string(gen)+",\"worker_us\":"+std::to_string(us)+",\"prepare_ms\":"+std::to_string(prep)+
       ",\"gpu_blends\":"+std::to_string(blends)+",\"gpu_errors\":"+std::to_string(errors)+
       ",\"sample_us\":"+std::to_string(sampling)+",\"sample_max_us\":"+std::to_string(sample_max)+
       ",\"arm_prepare_us\":"+std::to_string(arming)+",\"arm_prepare_max_us\":"+std::to_string(arm_max)+
       ",\"sample_method\":"+std::to_string(method)+",\"prepare_cpu_us\":"+std::to_string(prep_cpu)+
       ",\"prepare_rows\":"+std::to_string(prep_rows)+",\"taken_boundary\":"+std::to_string(on_boundary)+
       ",\"taken_late\":"+std::to_string(late)+",\"taken_spare\":"+std::to_string(spare)+
       ",\"fade\":"+(can_fade?"true":"false")+",\"fades\":"+std::to_string(faded)+
       ",\"fade_images\":"+std::to_string(fade_drawn)+",\"fade_cut\":"+std::to_string(fade_early)+
       ",\"arm_async\":"+(async?"true":"false")+",\"arm_opaque\":"+(opaque?"true":"false")+",\"arm_errors\":"+std::to_string(arm_failures)+
       ",\"arm_work_us\":"+std::to_string(arm_us)+",\"render_areas\":"+std::to_string(areas)+
       ",\"render_pixels\":"+std::to_string(pixels)+",\"render_largest\":"+std::to_string(largest)+",\"render_rects\":[";
      for(size_t i=0;i<std::min<size_t>(areas,rects.size());++i){const auto &r=rects[i];if(i)s+=',';
        s+="["+std::to_string(r.x1)+","+std::to_string(r.y1)+","+std::to_string(r.x2)+","+std::to_string(r.y2)+"]";}
      s+="],\"interval_total\":"+std::to_string(total)+",\"interval_ms\":[";
      for(uint32_t i=total-std::min<uint32_t>(total,128);i<total;++i){if(i!=total-std::min<uint32_t>(total,128))s+=',';s+=std::to_string(values[i%128]);}
      return s+"]}";
    };
  }
};
inline Disc page;
}  // namespace panel_vinyl
