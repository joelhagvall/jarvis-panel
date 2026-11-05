#include "diagnostics.h"
#include <algorithm>
#include <atomic>
#include <cstring>
#include <new>
#include <string_view>
#include <sys/time.h>
#include "driver/gptimer.h"
#include "esp_attr.h"
#include "esp_heap_caps.h"
#include "esp_memory_utils.h"
#include "esp_private/cache_utils.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "esphome/core/defines.h"
#include "esphome/core/hal.h"
#include "esphome/core/log.h"
#include "esphome/components/logger/logger.h"
#ifdef USE_ESP32_CRASH_HANDLER
#include "esphome/components/esp32/crash_handler.h"
#endif
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "log_ring.h"
#include "protocol.h"
#include "redraw_grid.h"
#include "src/display/lv_display_private.h"
#include "touch_log.h"

#ifndef ESPHOME_LOGGER_TX_BUFFER_SIZE
#define ESPHOME_LOGGER_TX_BUFFER_SIZE 512
#endif

namespace {
constexpr size_t RING_BYTES = 16384;
using LogRing = panel_debug_logs::Ring<RING_BYTES>;
// LP RAM: survives a software reset and a panic, not a power cut. 16 of its 32 kB; nothing else used it.
// Adding, removing or resizing any RTC_NOINIT object here moves the others: the first boot of such a build
// (and a rollback to the build before it) cannot read the previous ring. Seen 2026-10-29 when older_boot
// was added; change them only together with a reason worth one lost generation of logs.
RTC_NOINIT_ATTR LogRing log_ring;
RTC_NOINIT_ATTR panel_debug_logs::AbortRecord abort_record;
// The boot before the previous one: its last 8 kB and abort text. A crash chain (a candidate crashes, the
// rolled-back firmware crashes too) otherwise overwrote the first, interesting crash (2026-10-29).
constexpr size_t OLDER_BYTES = 8192;
struct OlderBoot {
  uint32_t magic, inverse, boot_id, started_by, ended_by, size;
  bool has_abort;
  panel_debug_logs::AbortRecord abort;
  char text[OLDER_BYTES];
  bool valid() const {
    return magic == panel_debug_logs::RING_MAGIC && inverse == ~panel_debug_logs::RING_MAGIC && size <= OLDER_BYTES;
  }
};
RTC_NOINIT_ATTR OlderBoot older_boot;
OlderBoot *older_copy{};  // PSRAM copy of the boot before the previous one, read at start
portMUX_TYPE ring_lock = portMUX_INITIALIZER_UNLOCKED;
std::atomic<bool> formatting{false};
std::atomic<uint32_t> dropped_lines{0};
// PSRAM, allocated in start_logs(): as internal .bss these two buffers took 4.6 kB from the boot's tightest
// internal RAM, and a boot then aborted in operator new (2026-10-29, __wrap___cxa_allocate_exception).
constexpr size_t SCRATCH_BYTES = 3 * ESPHOME_LOGGER_TX_BUFFER_SIZE + 48;
char *scratch{};  // used only while `formatting` is held
DRAM_ATTR uint32_t current_boot;                       // read by the abort wrapper
char *previous_text{};
size_t previous_size{};
uint32_t previous_boot{}, previous_started_by{};
bool previous_valid{false}, abort_valid{false};
panel_debug_logs::AbortRecord previous_abort{};
panel_debug_memory::String *crash_lines{};
bool capturing_crash{false};

size_t put_decimal(char *out, uint64_t value) {
  char digits[20];
  size_t n = 0;
  do { digits[n++] = char('0' + value % 10); value /= 10; } while (value);
  for (size_t i = 0; i < n; ++i) out[i] = digits[n - 1 - i];
  return n;
}

// Called by ESPHome's logger for every line, normally on the main task. Never logs itself.
void on_log(void *, uint8_t, const char *tag, const char *message, size_t size) {
  if (!message || !scratch) return;
  struct timeval now {};
  gettimeofday(&now, nullptr);
  if (formatting.exchange(true, std::memory_order_acquire)) {
    dropped_lines.fetch_add(1, std::memory_order_relaxed);
    return;
  }
  size_t n = put_decimal(scratch, esphome::millis());
  scratch[n++] = ' ';
  if (now.tv_sec >= 1767225600) {  // SNTP has set the clock (2026 or later)
    n += put_decimal(scratch + n, uint64_t(now.tv_sec));
    scratch[n++] = '.';
    const uint32_t ms = uint32_t(now.tv_usec / 1000);
    scratch[n++] = char('0' + ms / 100);
    scratch[n++] = char('0' + ms / 10 % 10);
    scratch[n++] = char('0' + ms % 10);
  } else {
    scratch[n++] = '-';
  }
  scratch[n++] = ' ';
  n += panel_debug_logs::clean_line(std::string_view(message, std::min<size_t>(size, ESPHOME_LOGGER_TX_BUFFER_SIZE)),
                                    scratch + n);
  scratch[n++] = '\n';
  portENTER_CRITICAL(&ring_lock);
  log_ring.append(scratch, n);
  portEXIT_CRITICAL(&ring_lock);
  if (capturing_crash && crash_lines && tag && std::strcmp(tag, "esp32.crash") == 0 && crash_lines->size() < 8192)
    crash_lines->append(scratch, n);
  formatting.store(false, std::memory_order_release);
}

void *psram(size_t size) { return heap_caps_calloc(1, size, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT); }

// JSON array of cleaned lines; at most `limit` of the newest when limit is non-zero.
void append_lines(panel_debug_memory::String &out, const char *text, size_t size, size_t limit = 0) {
  size_t start = 0;
  if (limit) {
    size_t seen = 0, at = size;
    while (at > 0) {
      const size_t newline = std::string_view(text, at - 1).rfind('\n');
      if (++seen > limit) { start = at; break; }
      if (newline == std::string_view::npos) break;
      at = newline + 1;
    }
  }
  static char *clean = static_cast<char *>(heap_caps_malloc(3 * 1024 + 1, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
  out += '[';
  if (!clean) {
    out += ']';
    return;
  }
  bool first = true;
  while (start < size) {
    const char *end = static_cast<const char *>(std::memchr(text + start, '\n', size - start));
    const size_t length = (end ? size_t(end - text) : size) - start;
    if (length) {
      const size_t n = panel_debug_logs::clean_line(std::string_view(text + start, std::min<size_t>(length, 1024)),
                                                    clean);
      if (!first) out += ',';
      first = false;
      const std::string line = esphome::panel_debug::quoted(std::string_view(clean, n));
      out.append(line.data(), line.size());
    }
    start += length + 1;
  }
  out += ']';
}

void append_number(panel_debug_memory::String &out, const char *key, uint64_t value, bool comma = true) {
  if (comma) out += ',';
  out += '"';
  out += key;
  out += "\":";
  char digits[24];
  const size_t n = put_decimal(digits, value);
  out.append(digits, n);
}
}  // namespace

extern "C" {
void __real_panic_abort(const char *details) __attribute__((noreturn));
// Wraps IDF's panic_abort (linker --wrap, see __init__.py): keeps "assert failed: ..." for the next boot.
// IRAM, no locks, no flash reads while the cache is off; the text is copied before the panic handler runs.
void IRAM_ATTR __attribute__((noreturn)) __wrap_panic_abort(const char *details) {
  abort_record.magic = 0;
  size_t n = 0;
  if (details && (esp_ptr_internal(details) || spi_flash_cache_enabled()))
    while (n + 1 < sizeof(abort_record.text) && details[n]) {
      abort_record.text[n] = details[n];
      ++n;
    }
  abort_record.text[n] = 0;
  abort_record.boot_id = current_boot;
  abort_record.uptime_ms = uint32_t(esp_timer_get_time() / 1000);
  abort_record.inverse = ~panel_debug_logs::RING_MAGIC;
  abort_record.magic = panel_debug_logs::RING_MAGIC;
  __real_panic_abort(details);
}
}

namespace esphome::panel_debug::diag {
static const char *const TAG = "panel_debug";

void start_logs(uint32_t boot_id, const std::string &build_id) {
  current_boot = boot_id;
  if (older_boot.valid()) {
    older_copy = static_cast<OlderBoot *>(psram(sizeof(OlderBoot)));
    if (older_copy) std::memcpy(static_cast<void *>(older_copy), &older_boot, sizeof(OlderBoot));
  }
  older_boot.magic = 0;
  if (log_ring.valid()) {
    previous_text = static_cast<char *>(psram(RING_BYTES));
    if (previous_text) {
      previous_size = log_ring.copy_out(previous_text);
      previous_boot = log_ring.boot_id;
      previous_started_by = log_ring.reset_reason;
      previous_valid = true;
    }
  }
  if (abort_record.valid()) {
    previous_abort = abort_record;
    previous_abort.text[sizeof(previous_abort.text) - 1] = 0;
    abort_valid = true;
  }
  abort_record.magic = 0;
  if (previous_valid) {  // the previous boot becomes the older one for the next boot: its newest 8 kB
    const size_t keep = std::min(previous_size, OLDER_BYTES);
    std::memcpy(older_boot.text, previous_text + previous_size - keep, keep);
    older_boot.size = uint32_t(keep);
    older_boot.boot_id = previous_boot;
    older_boot.started_by = previous_started_by;
    older_boot.ended_by = uint32_t(esp_reset_reason());
    older_boot.has_abort = abort_valid && previous_abort.boot_id == previous_boot;
    if (older_boot.has_abort) older_boot.abort = previous_abort;
    older_boot.inverse = ~panel_debug_logs::RING_MAGIC;
    older_boot.magic = panel_debug_logs::RING_MAGIC;
  }
  scratch = static_cast<char *>(heap_caps_malloc(SCRATCH_BYTES, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
  log_ring.reset(boot_id, uint32_t(esp_reset_reason()));
  if (logger::global_logger) logger::global_logger->add_log_callback(nullptr, on_log);
  ESP_LOGI(TAG, "Log ring started: boot %u, build %s, reset reason %d, previous boot %s", unsigned(boot_id),
           build_id.c_str(), int(esp_reset_reason()), previous_valid ? "kept" : "not kept");
}

void capture_crash_record() {
#ifdef USE_ESP32_CRASH_HANDLER
  if (!esp32::crash_handler_has_data()) return;
  void *memory = heap_caps_malloc(sizeof(panel_debug_memory::String), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
  if (!memory) return;
  crash_lines = new (memory) panel_debug_memory::String();
  capturing_crash = true;
  esp32::crash_handler_log();  // logs on this (main) task, so every line passes on_log before this returns
  capturing_crash = false;
#endif
}

panel_debug_memory::String logs_json() {
  panel_debug_memory::String out;
  char *raw = static_cast<char *>(psram(RING_BYTES)), *text = static_cast<char *>(psram(RING_BYTES));
  if (!raw || !text) {
    heap_caps_free(raw);
    heap_caps_free(text);
    return "{\"error\":\"no memory for the log copy\"}";
  }
  portENTER_CRITICAL(&ring_lock);
  const uint32_t written = log_ring.copy_raw(raw);
  portEXIT_CRITICAL(&ring_lock);
  const size_t size = LogRing::order(raw, written, text);
  heap_caps_free(raw);
  out.reserve(64 * 1024);
  out += '{';
  append_number(out, "boot_id", current_boot, false);
  append_number(out, "reset_reason", uint32_t(esp_reset_reason()));
  append_number(out, "uptime_ms", millis());
  append_number(out, "ring_bytes", RING_BYTES);
  append_number(out, "written_bytes", written);
  append_number(out, "dropped_lines", dropped_lines.load(std::memory_order_relaxed));
  out += ",\"lines\":";
  append_lines(out, text, size);
  heap_caps_free(text);
  out += ",\"previous\":";
  if (previous_valid) {
    out += '{';
  append_number(out, "boot_id", previous_boot, false);
    append_number(out, "started_by_reset_reason", previous_started_by);
    append_number(out, "ended_by_reset_reason", uint32_t(esp_reset_reason()));
    out += ",\"lines\":";
    append_lines(out, previous_text, previous_size);
    out += '}';
  } else {
    out += "null";
  }
  out += ",\"older\":";
  if (older_copy) {
    out += '{';
    append_number(out, "boot_id", older_copy->boot_id, false);
    append_number(out, "started_by_reset_reason", older_copy->started_by);
    append_number(out, "ended_by_reset_reason", older_copy->ended_by);
    out += ",\"abort\":";
    if (older_copy->has_abort) {
      older_copy->abort.text[sizeof(older_copy->abort.text) - 1] = 0;
      const std::string text = quoted(older_copy->abort.text);
      out.append(text.data(), text.size());
    } else {
      out += "null";
    }
    out += ",\"lines\":";
    append_lines(out, older_copy->text, older_copy->size);
    out += '}';
  } else {
    out += "null";
  }
  out += '}';
  return out;
}

panel_debug_memory::String crash_json() {
  panel_debug_memory::String out;
  out.reserve(16 * 1024);
  out += '{';
  append_number(out, "boot_id", current_boot, false);
  append_number(out, "reset_reason", uint32_t(esp_reset_reason()));
  out += ",\"previous_boot_id\":";
  if (previous_valid) {
    char digits[24];
    out.append(digits, put_decimal(digits, previous_boot));
  } else {
    out += "null";
  }
  out += ",\"esphome_record\":";
  if (crash_lines) append_lines(out, crash_lines->data(), crash_lines->size());
  else out += "[]";
  out += ",\"abort\":";
  if (abort_valid) {
    out += '{';
  append_number(out, "boot_id", previous_abort.boot_id, false);
    append_number(out, "uptime_ms", previous_abort.uptime_ms);
    out += ",\"text\":";
    const std::string text = quoted(previous_abort.text);
    out.append(text.data(), text.size());
    out += '}';
  } else {
    out += "null";
  }
  out += ",\"previous_tail\":";
  if (previous_valid) append_lines(out, previous_text, previous_size, 60);
  else out += "[]";
  out += '}';
  return out;
}

// ---- The boot's internal RAM low point: when it happens and what kind of memory it is. ----
namespace {
constexpr uint32_t BOOT_WATCH_MS = 40000, BOOT_STEP_MS = 5, BOOT_TIMELINE_STEP_MS = 50;
struct BootHeap {
  std::atomic<bool> done{false};
  uint32_t internal_min{UINT32_MAX}, internal_min_at{}, dma_min{UINT32_MAX}, dma_min_at{}, largest_at_min{};
  uint32_t samples{};
  uint16_t internal_kb[BOOT_WATCH_MS / BOOT_TIMELINE_STEP_MS];  // per 50 ms: the lowest internal free (kB)
  uint16_t dma_kb[BOOT_WATCH_MS / BOOT_TIMELINE_STEP_MS];
};
BootHeap *boot_heap{};

void boot_heap_task(void *) {
  auto *b = boot_heap;
  for (auto &v : b->internal_kb) v = UINT16_MAX;
  for (auto &v : b->dma_kb) v = UINT16_MAX;
  while (millis() < BOOT_WATCH_MS) {
    const uint32_t now = millis();
    const uint32_t internal = heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    const uint32_t dma = heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_DMA);
    if (internal < b->internal_min) {
      b->internal_min = internal;
      b->internal_min_at = now;
      b->largest_at_min = heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    }
    if (dma < b->dma_min) {
      b->dma_min = dma;
      b->dma_min_at = now;
    }
    const uint32_t slot = now / BOOT_TIMELINE_STEP_MS;
    if (slot < BOOT_WATCH_MS / BOOT_TIMELINE_STEP_MS) {
      b->internal_kb[slot] = std::min<uint32_t>(b->internal_kb[slot], internal / 1024);
      b->dma_kb[slot] = std::min<uint32_t>(b->dma_kb[slot], dma / 1024);
    }
    ++b->samples;
    vTaskDelay(pdMS_TO_TICKS(BOOT_STEP_MS));
  }
  b->done.store(true);
  ESP_LOGI(TAG, "Boot internal RAM low point: %u free at %u ms (largest block %u); DMA-capable low %u at %u ms",
           unsigned(b->internal_min), unsigned(b->internal_min_at), unsigned(b->largest_at_min), unsigned(b->dma_min),
           unsigned(b->dma_min_at));
  vTaskDelete(nullptr);
}
}  // namespace

void start_boot_heap_watch() {
  if (boot_heap) return;
  void *memory = psram(sizeof(BootHeap));
  if (!memory) return;
  boot_heap = new (memory) BootHeap();
  // Priority above the image download and LVGL work so the samples keep coming during the dip; 2 kB stack,
  // deleted after 40 s.
  if (xTaskCreatePinnedToCore(boot_heap_task, "boot-heap", 2048, nullptr, 15, nullptr, 0) != pdPASS)
    ESP_LOGW(TAG, "Boot heap watch unavailable");
}

void append_boot_heap(panel_debug_memory::String &out) {
  out += ",\"boot_heap\":";
  if (!boot_heap || !boot_heap->done.load()) {
    out += "null";
    return;
  }
  const auto *b = boot_heap;
  out += '{';
  append_number(out, "internal_min", b->internal_min, false);
  append_number(out, "internal_min_at_ms", b->internal_min_at);
  append_number(out, "largest_block_at_min", b->largest_at_min);
  append_number(out, "dma_min", b->dma_min);
  append_number(out, "dma_min_at_ms", b->dma_min_at);
  append_number(out, "samples", b->samples);
  append_number(out, "timeline_step_ms", BOOT_TIMELINE_STEP_MS);
  for (int which = 0; which < 2; ++which) {
    out += which ? ",\"dma_kb\":[" : ",\"internal_kb\":[";
    const uint16_t *values = which ? b->dma_kb : b->internal_kb;
    for (uint32_t i = 0; i < BOOT_WATCH_MS / BOOT_TIMELINE_STEP_MS; ++i) {
      if (i) out += ',';
      if (values[i] == UINT16_MAX) out += "null";
      else out += std::to_string(values[i]).c_str();
    }
    out += ']';
  }
  out += '}';
}

// ---- Which task each core runs: a 1003 Hz timer samples both cores' current task. ----
namespace {
struct Samples {
  TaskHandle_t *core[2];
  volatile uint32_t count;
  uint32_t capacity;
};
bool IRAM_ATTR on_sample(gptimer_handle_t, const gptimer_alarm_event_data_t *, void *context) {
  auto *s = static_cast<Samples *>(context);
  const uint32_t i = s->count;
  if (i < s->capacity) {
    s->core[0][i] = xTaskGetCurrentTaskHandleForCore(0);
    s->core[1][i] = xTaskGetCurrentTaskHandleForCore(1);
    s->count = i + 1;
  }
  return false;
}
void append_heap(panel_debug_memory::String &out, const char *name, uint32_t caps, bool comma) {
  multi_heap_info_t info{};
  heap_caps_get_info(&info, caps);
  if (comma) out += ',';
  out += '"';
  out += name;
  out += "\":";
  out += '{';
  append_number(out, "free", info.total_free_bytes, false);
  append_number(out, "allocated", info.total_allocated_bytes);
  append_number(out, "largest_free_block", info.largest_free_block);
  append_number(out, "minimum_free", info.minimum_free_bytes);
  append_number(out, "free_blocks", info.free_blocks);
  out += '}';
}
}  // namespace

panel_debug_memory::String tasks_json(uint32_t window_ms) {
  static std::atomic<bool> running{false};
  if (running.exchange(true)) return "{\"error\":\"a task measurement is already running\"}";
  Samples samples{};
  samples.capacity = window_ms + window_ms / 50 + 16;
  samples.core[0] = static_cast<TaskHandle_t *>(psram(samples.capacity * sizeof(TaskHandle_t)));
  samples.core[1] = static_cast<TaskHandle_t *>(psram(samples.capacity * sizeof(TaskHandle_t)));
  gptimer_handle_t timer{};
  bool sampled = false;
  if (samples.core[0] && samples.core[1]) {
    gptimer_config_t config{};
    config.clk_src = GPTIMER_CLK_SRC_DEFAULT;
    config.direction = GPTIMER_COUNT_UP;
    config.resolution_hz = 1000000;
    gptimer_event_callbacks_t callbacks{};
    callbacks.on_alarm = on_sample;
    gptimer_alarm_config_t alarm{};
    alarm.alarm_count = 997;  // ~1003 Hz: not a multiple of the 1 kHz tick, so tick-aligned work is not over- or under-counted
    alarm.flags.auto_reload_on_alarm = true;
    if (gptimer_new_timer(&config, &timer) == ESP_OK && gptimer_register_event_callbacks(timer, &callbacks, &samples) == ESP_OK &&
        gptimer_set_alarm_action(timer, &alarm) == ESP_OK && gptimer_enable(timer) == ESP_OK) {
      if (gptimer_start(timer) == ESP_OK) {
        vTaskDelay(pdMS_TO_TICKS(window_ms));
        gptimer_stop(timer);
        sampled = true;
      }
      gptimer_disable(timer);
    }
    if (timer) gptimer_del_timer(timer);
  }
  const uint32_t count = samples.count;
  UBaseType_t capacity = uxTaskGetNumberOfTasks() + 8;
  auto *tasks = static_cast<TaskStatus_t *>(psram(capacity * sizeof(TaskStatus_t)));
  const UBaseType_t task_count = tasks ? uxTaskGetSystemState(tasks, capacity, nullptr) : 0;
  auto *hits = static_cast<uint32_t *>(psram((task_count + 1) * 2 * sizeof(uint32_t)));
  panel_debug_memory::String out;
  out.reserve(16 * 1024);
  if (!sampled || !tasks || !hits) {
    out = "{\"error\":\"task sampling unavailable\"}";
  } else {
    for (uint32_t i = 0; i < count; ++i)
      for (int core = 0; core < 2; ++core) {
        UBaseType_t k = 0;
        while (k < task_count && tasks[k].xHandle != samples.core[core][i]) ++k;
        ++hits[k * 2 + core];  // k == task_count: a task that ended before the list was taken
      }
    UBaseType_t *order = static_cast<UBaseType_t *>(psram(task_count * sizeof(UBaseType_t)));
    for (UBaseType_t k = 0; order && k < task_count; ++k) order[k] = k;
    if (order)
      std::sort(order, order + task_count, [&](UBaseType_t a, UBaseType_t b) {
        return hits[a * 2] + hits[a * 2 + 1] > hits[b * 2] + hits[b * 2 + 1];
      });
    out += '{';
  append_number(out, "window_ms", window_ms, false);
    append_number(out, "samples", count);
    append_number(out, "sample_hz", 1003);
    append_number(out, "ended_task_samples_core0", hits[task_count * 2]);
    append_number(out, "ended_task_samples_core1", hits[task_count * 2 + 1]);
    out += ",\"tasks\":[";
    static const char *const STATES[] = {"running", "ready", "blocked", "suspended", "deleted", "invalid"};
    for (UBaseType_t j = 0; j < task_count; ++j) {
      const UBaseType_t k = order ? order[j] : j;
      const TaskStatus_t &t = tasks[k];
      if (j) out += ',';
      out += "{\"name\":";
      const std::string name = quoted(t.pcTaskName ? t.pcTaskName : "");
      out.append(name.data(), name.size());
      append_number(out, "priority", t.uxCurrentPriority);
      out += ",\"state\":\"";
      out += STATES[std::min<int>(int(t.eCurrentState), 5)];
      out += '"';
#if configTASKLIST_INCLUDE_COREID
      out += ",\"pinned_core\":";
      if (t.xCoreID == tskNO_AFFINITY) out += "null";
      else out += char('0' + int(t.xCoreID));
#endif
      // ESP-IDF's StackType_t is one byte: the high-water mark is in bytes.
      append_number(out, "stack_min_free_bytes", t.usStackHighWaterMark);
      append_number(out, "core0_samples", hits[k * 2]);
      append_number(out, "core1_samples", hits[k * 2 + 1]);
      out += '}';
    }
    out += ']';
    append_boot_heap(out);
    out += ",\"heap\":{";
    append_heap(out, "internal", MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT, false);
    append_heap(out, "dma", MALLOC_CAP_INTERNAL | MALLOC_CAP_DMA, true);
    append_heap(out, "psram", MALLOC_CAP_SPIRAM, true);
    out += "}}";
    heap_caps_free(order);
  }
  heap_caps_free(hits);
  heap_caps_free(tasks);
  heap_caps_free(samples.core[0]);
  heap_caps_free(samples.core[1]);
  running.store(false);
  return out;
}

// ---- The physical touchscreen's reads, as LVGL got them. ----
namespace {
using TouchRing = panel_debug_touch::Ring<2048>;
TouchRing *touch_ring{};
portMUX_TYPE touch_lock = portMUX_INITIALIZER_UNLOCKED;
struct Wrapped {
  lv_indev_t *indev;
  lv_indev_read_cb_t read;
};
Wrapped wrapped[4]{};
size_t wrapped_count{};

void recording_read(lv_indev_t *indev, lv_indev_data_t *data) {
  for (size_t i = 0; i < wrapped_count; ++i) {
    if (wrapped[i].indev != indev) continue;
    wrapped[i].read(indev, data);
    portENTER_CRITICAL(&touch_lock);
    touch_ring->observe(millis(), data->point.x, data->point.y, data->state == LV_INDEV_STATE_PRESSED);
    portEXIT_CRITICAL(&touch_lock);
    return;
  }
}
}  // namespace

void install_touch_recorder(lv_indev_t *virtual_pointer) {
  if (touch_ring) return;
  void *memory = psram(sizeof(TouchRing));
  if (!memory) return;
  touch_ring = new (memory) TouchRing();
  for (auto *indev = lv_indev_get_next(nullptr); indev && wrapped_count < 4; indev = lv_indev_get_next(indev)) {
    const auto read = lv_indev_get_read_cb(indev);
    if (indev == virtual_pointer || lv_indev_get_type(indev) != LV_INDEV_TYPE_POINTER || !read || read == recording_read)
      continue;
    wrapped[wrapped_count++] = Wrapped{indev, read};
    lv_indev_set_read_cb(indev, recording_read);
  }
  ESP_LOGI(TAG, "Recording %u physical pointer device(s)", unsigned(wrapped_count));
}

panel_debug_memory::String touches_json() {
  if (!touch_ring) return "{\"error\":\"touch recorder unavailable\"}";
  auto *copy = static_cast<TouchRing *>(psram(sizeof(TouchRing)));
  if (!copy) return "{\"error\":\"no memory for the touch copy\"}";
  portENTER_CRITICAL(&touch_lock);
  std::memcpy(static_cast<void *>(copy), touch_ring, sizeof(TouchRing));
  portEXIT_CRITICAL(&touch_lock);
  panel_debug_memory::String out;
  out.reserve(copy->size() * 22 + 128);
  out += '{';
  append_number(out, "now_ms", millis(), false);
  append_number(out, "recorded", copy->count);
  append_number(out, "kept", copy->size());
  out += ",\"devices\":";
  out += std::to_string(wrapped_count).c_str();
  out += ",\"samples\":[";
  for (size_t i = 0; i < copy->size(); ++i) {
    const auto &s = copy->at(i);
    if (i) out += ',';
    out += '[';
    out += std::to_string(s.t_ms).c_str();
    out += ',';
    out += std::to_string(s.x).c_str();
    out += ',';
    out += std::to_string(s.y).c_str();
    out += ',';
    out += s.pressed ? '1' : '0';
    out += ']';
  }
  out += "]}";
  heap_caps_free(copy);
  return out;
}

// ---- Where LVGL draws, counted at render start from its joined invalid areas. ----
namespace {
panel_debug_redraw::Grid *grid{};
std::atomic<bool> redraw_on{false}, redraw_busy{false}, redraw_claimed{false};

void render_start(lv_event_t *event) {
  redraw_busy.store(true);  // seq_cst with redraw_on: the HTTP task never reads the grid while this writes it
  if (redraw_on.load()) {
    auto *display = static_cast<lv_display_t *>(lv_event_get_user_data(event));
    panel_debug_redraw::Area list[LV_INV_BUF_SIZE];
    size_t n = 0;
    for (uint32_t i = 0; i < display->inv_p && i < LV_INV_BUF_SIZE; ++i)
      if (!display->inv_area_joined[i]) {
        const auto &a = display->inv_areas[i];
        list[n++] = panel_debug_redraw::Area{a.x1, a.y1, a.x2, a.y2};
      }
    grid->add_frame(list, n);
  }
  redraw_busy.store(false);
}
}  // namespace

bool install_redraw_map(lv_display_t *display) {
  if (grid || !display) return grid != nullptr;
  grid = static_cast<panel_debug_redraw::Grid *>(psram(sizeof(panel_debug_redraw::Grid)));
  if (!grid) return false;
  lv_display_add_event_cb(display, render_start, LV_EVENT_RENDER_START, display);
  return true;
}

panel_debug_memory::String redraw_json(uint32_t window_ms) {
  if (!grid) return "{\"error\":\"redraw map unavailable\"}";
  if (redraw_claimed.exchange(true)) return "{\"error\":\"a redraw measurement is already running\"}";
  grid->clear();
  const uint32_t started = millis();
  redraw_on.store(true);
  vTaskDelay(pdMS_TO_TICKS(window_ms));
  redraw_on.store(false);
  while (redraw_busy.load()) vTaskDelay(1);
  const uint32_t measured = millis() - started;
  panel_debug_memory::String out;
  out.reserve(32 * 1024);
  using namespace panel_debug_redraw;
  out += '{';
  append_number(out, "window_ms", measured, false);
  append_number(out, "cell", CELL);
  append_number(out, "cols", COLS);
  append_number(out, "rows", ROWS);
  append_number(out, "frames", grid->frames);
  append_number(out, "areas", grid->areas);
  append_number(out, "drawn_px", grid->pixels);
  append_number(out, "screen_px", uint64_t(WIDTH) * HEIGHT);
  out += ",\"cells\":[";
  for (int i = 0; i < ROWS * COLS; ++i) {
    if (i) out += ',';
    out += std::to_string(grid->cells[i]).c_str();
  }
  out += "],\"frame_px\":[";
  for (uint32_t i = 0; i < std::min<uint32_t>(grid->frames, MAX_FRAMES); ++i) {
    if (i) out += ',';
    out += std::to_string(grid->frame_px[i]).c_str();
  }
  out += "]}";
  redraw_claimed.store(false);
  return out;
}
}  // namespace esphome::panel_debug::diag
