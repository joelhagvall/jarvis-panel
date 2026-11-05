#include "esphome/core/defines.h"

#ifdef USE_ESP32_VARIANT_ESP32P4
#include "sdkconfig.h"
#include "esp_attr.h"
#include "esp_cpu.h"
#include "esp_idf_version.h"
#include "esp_private/rtc_clk.h"
#include "hal/wdt_hal.h"
#include "riscv/rv_utils.h"
#include "soc/cache_reg.h"
#include "soc/soc.h"
#include "soc/rtc.h"

// Re-review against upstream before changing the SDK. Do not silently keep a
// workaround after the SDK's shutdown sequence or cache registers have changed.
#if ESP_IDF_VERSION != ESP_IDF_VERSION_VAL(5, 5, 5)
#error "Review panel_restart against this ESP-IDF version (espressif/esp-idf#19171)"
#endif

extern "C" void __real_esp_restart_noos(void) __attribute__((noreturn));

// IDFGH-18386: resetting the other core during cache maintenance leaves the
// shared sync engine busy. IDF's subsequent Cache_WriteBack_All then faults on
// CACHE_SYNC_MAP/ADDR_REG. Stall first and let that transaction finish BEFORE
// entering IDF's reset-before-stall sequence. Keep the rest of IDF's restart.
// Confirmed by Espressif: https://github.com/espressif/esp-idf/issues/19171#issuecomment-6075256928
// No logging, allocation, RTOS calls or writes to cache-sync registers here.
extern "C" void IRAM_ATTR __attribute__((noreturn)) __wrap_esp_restart_noos(void) {
  rv_utils_intr_global_disable();

  // Arm the same hardware fallback as IDF BEFORE waiting. A stuck sync engine
  // must end in a watchdog reset, never proceed into another cache writeback.
  wdt_hal_context_t watchdog;
  wdt_hal_init(&watchdog, WDT_RWDT, 0, false);
  const uint32_t ticks = rtc_clk_slow_freq_get_hz();  // one second
  wdt_hal_write_protect_disable(&watchdog);
  wdt_hal_config_stage(&watchdog, WDT_STAGE0, ticks, WDT_STAGE_ACTION_RESET_SYSTEM);
  wdt_hal_config_stage(&watchdog, WDT_STAGE1, ticks, WDT_STAGE_ACTION_RESET_RTC);
  wdt_hal_set_flashboot_en(&watchdog, true);
  wdt_hal_enable(&watchdog);
  wdt_hal_write_protect_enable(&watchdog);

#if !CONFIG_ESP_SYSTEM_SINGLE_CORE_MODE
  esp_cpu_stall(esp_cpu_get_core_id() == 0 ? 1 : 0);
#endif
  // Read-only polling. A successful sync sets DONE; the other core cannot start
  // another one while stalled. Bound CPU polling as well as hardware lifetime.
  for (uint32_t spin = 0; spin < 10000000; ++spin) {
    if (REG_GET_BIT(CACHE_SYNC_CTRL_REG, CACHE_SYNC_DONE)) {
      __real_esp_restart_noos();
    }
  }
  while (true) {
    esp_cpu_wait_for_intr();  // RTC watchdog resets the chip if sync never ends.
  }
}
#else
#error "panel_restart is only supported on ESP32-P4"
#endif
