"""The diagnostics' /status reply: boot milestones, render and touch counters, assembled off the LVGL task."""
import sys

from .common import ROOT


def apply(cfg):
    sys.path.insert(0, str(ROOT / 'tools'))
    from panel_test_codegen import apply as apply_panel_diagnostics
    apply_panel_diagnostics(cfg, ROOT)
    cfg['globals'].append({'id': 'panel_ready_ms', 'type': 'uint32_t', 'initial_value': '0'})
    # Split the boot's Wi-Fi time into setup -> associated -> IP (bench/wifi_boot.h). Priority 249 runs right
    # after ESPHome's Wi-Fi setup (250), which creates the event loop the handlers need.
    cfg['esphome'].setdefault('includes', []).append(str(ROOT / 'bench' / 'wifi_boot.h'))
    cfg['esphome'].setdefault('on_boot', []).append({'priority': 249, 'then': [{'lambda': 'wifi_boot::install();'}]})
    cfg.setdefault('interval', []).append({'interval': '100ms', 'then': [{'lambda':
        'if (!id(panel_ready_ms) && id(assets_loaded) && wifi::global_wifi_component->is_connected()) '
        'id(panel_ready_ms) = millis();'}]})
    cfg['esphome'].setdefault('on_boot', []).append({'priority': -102, 'then': [{'lambda': r'''
id(physical_ui).set_status_callback([]() -> std::function<std::string()> {
  const uint32_t ready_ms = id(panel_ready_ms);
  const bool cache_used = id(boot_cache_used), cache_active = id(boot_cache_active);
  const uint32_t cache_epoch = boot_cache::panel_cache.epoch(), cache_saves = boot_cache::panel_cache.saves,
                 cache_failures = boot_cache::panel_cache.failures, boot_live = id(boot_live_ms), boot_ntp = id(boot_ntp_ms);
  const uint32_t boot_ui = id(boot_ui_ms), boot_wifi = id(boot_wifi_ms), boot_bg = id(boot_bg_ms),
                 boot_backgrounds = id(boot_backgrounds_ms), boot_orb = id(boot_orb_ms),
                 boot_voice_client = id(boot_voice_client_ms), boot_light = id(boot_light_ms),
                 boot_shown = id(boot_shown_ms), boot_sound = id(boot_sound_ms), boot_data = id(boot_data_ms),
                 boot_fade_start = id(boot_fade_start_ms), boot_fade_end = id(boot_fade_end_ms),
                 loop_interval = esphome::App.get_loop_interval();
  const auto sample = bench_snapshot();
  const auto timing = responsiveness::recorder.deferred_json();
  const auto orb_extra = []() -> std::function<std::string()> {
#ifdef HALLPANEL_ORB_PREFETCH
    return packed_orb::prefetch.deferred_status();
#else
    return []() { return std::string(); };
#endif
  }();
  const auto swap_extra = []() -> std::function<std::string()> {
#ifdef HALLPANEL_SCREEN_SWAP
    const auto s = screen_swap::state.stats;
    return [s]() {
      std::string out = ",\"screen_swap_applied\":";
      out += std::to_string(s.applied);
      out += ",\"screen_swap_fallbacks\":";
      out += std::to_string(s.fallbacks);
      out += ",\"screen_swap_last_fallback\":";
      out += std::to_string(s.last_fallback);
      out += ",\"screen_swap_last_areas\":";
      out += std::to_string(s.last_areas);
      out += ",\"screen_swap_last_pixels\":";
      out += std::to_string(s.last_pixels);
      out += ",\"screen_swap_last_us\":";
      out += std::to_string(s.last_us);
      out += ",\"screen_swap_last_old_us\":";
      out += std::to_string(s.last_old_us);
      out += ",\"screen_swap_last_collected\":";
      out += std::to_string(s.last_collected);
      return out;
    };
#else
    return []() { return std::string(); };
#endif
  }();
  const auto presentation = []() -> std::function<std::string()> {
#ifdef HALLPANEL_DIRECT_DISPLAY
    return direct_display::deferred_presentation();
#else
    return []() { return std::string(); };
#endif
  }();
  const auto vinyl_extra = []() -> std::function<std::string()> {
#ifdef HALLPANEL_VINYL
    return panel_vinyl::page.deferred_status();
#else
    return [](){return std::string();};
#endif
  }();
  const auto artwork_extra = []() -> std::function<std::string()> {
#ifdef HALLPANEL_ASYNC_ARTWORK
    return esphome::async_artwork::instance->deferred_status();
#else
    return [](){return std::string();};
#endif
  }();
  const auto frames = bench::lifetime_frames, busy_us = bench::lifetime_busy_us;
  const bool healthy = ready_ms && millis() >= 65000;
  const bool alarm = id(h_smoke_alarm) || id(h_water_alarm) || id(timer_ringing);
  const bool announcement = id(panel_media)->state == media_player::MEDIA_PLAYER_STATE_ANNOUNCING;
  const bool voice = id(assist).is_running();
  const bool mic_enabled=id(mic_on).state,wake_running=id(wake).is_running();
  const bool music_load_active = panel_music_load::fixture.active;
  const auto music_load_steps = panel_music_load::fixture.step, music_load_covers = panel_music_load::fixture.covers;
  const bool detail_active=panel_detail_load::fixture.active;
  const auto detail_steps=panel_detail_load::fixture.steps;
  const auto priority_stats=ui_priority::queue.stats;
  return [cache_used, cache_active, cache_epoch, cache_saves, cache_failures, boot_live, boot_ntp, ready_ms, boot_ui, boot_wifi, boot_bg, boot_backgrounds, boot_orb, boot_voice_client, boot_light, boot_shown, boot_sound, boot_data, boot_fade_start, boot_fade_end, loop_interval,
          sample, timing, orb_extra, swap_extra, presentation, vinyl_extra, artwork_extra, frames, busy_us, healthy, alarm, announcement, voice, mic_enabled, wake_running,
          music_load_active, music_load_steps, music_load_covers, detail_active, detail_steps, priority_stats]() {
  // End each conversion's temporary lifetime before formatting the next field.
  // One long operator+ expression retained all strings on the 8 KiB HTTP stack.
  std::string result = timing();
  result.reserve(result.size() + 2048);
  result += orb_extra();
  result += swap_extra();
  result += presentation();
  result += vinyl_extra();
  result += artwork_extra();
  result += ",\"mic_enabled\":";result += mic_enabled?"true":"false";
  result += ",\"wake_running\":";result += wake_running?"true":"false";
  result += ",\"music_load_active\":";
  result += music_load_active ? "true" : "false";
  result += ",\"music_load_steps\":";
  result += std::to_string(music_load_steps);
  result += ",\"music_load_covers\":";
  result += std::to_string(music_load_covers);
  result += ",\"detail_load_active\":";
  result += detail_active ? "true" : "false";
  result += ",\"detail_load_steps\":";
  result += std::to_string(detail_steps);
  result += ",\"ui_update_pending\":"+std::to_string(priority_stats.pending);
  result += ",\"ui_update_applied\":"+std::to_string(priority_stats.applied);
  result += ",\"ui_update_replaced\":"+std::to_string(priority_stats.replaced);
  result += ",\"ui_update_deferred\":"+std::to_string(priority_stats.deferred);
  result += ",\"ui_update_max_wait_ms\":"+std::to_string(priority_stats.max_wait_ms);
  result += ",\"redraws_per_s\":";
  result += std::to_string(sample.redraws_per_s);
  result += ",\"render_avg_ms\":";
  result += std::to_string(sample.average_ms);
  result += ",\"render_max_ms\":";
  result += std::to_string(sample.maximum_ms);
  result += ",\"render_frames_total\":";
  result += std::to_string(frames);
  result += ",\"render_busy_total_us\":";
  result += std::to_string(busy_us);
  result += ",\"touch_refresh_last_ms\":";
  result += std::to_string(sample.touch_last_ms);
  result += ",\"touch_refresh_p95_ms\":";
  result += std::to_string(sample.touch_p95_ms);
  result += ",\"boot_cache_used\":";
  result += cache_used ? "true" : "false";
  result += ",\"boot_cache_active\":";
  result += cache_active ? "true" : "false";
  result += ",\"boot_cache_epoch\":";
  result += std::to_string(cache_epoch);
  result += ",\"boot_cache_saves\":";
  result += std::to_string(cache_saves);
  result += ",\"boot_cache_failures\":";
  result += std::to_string(cache_failures);
  result += ",\"boot_ntp_ms\":";
  result += std::to_string(boot_ntp);
  result += ",\"boot_live_ms\":";
  result += std::to_string(boot_live);
  result += ",\"boot_ready_ms\":";
  result += std::to_string(ready_ms);
  result += ",\"boot_ui_ms\":";
  result += std::to_string(boot_ui);
  result += ",\"boot_light_ms\":";
  result += std::to_string(boot_light);
  result += ",\"boot_shown_ms\":";
  result += std::to_string(boot_shown);
  result += ",\"boot_sound_ms\":";
  result += std::to_string(boot_sound);
  result += ",\"boot_data_ms\":";
  result += std::to_string(boot_data);
  result += ",\"boot_fade_start_ms\":";
  result += std::to_string(boot_fade_start);
  result += ",\"boot_fade_end_ms\":";
  result += std::to_string(boot_fade_end);
  result += ",\"loop_interval_ms\":";
  result += std::to_string(loop_interval);
  result += ",\"boot_wifi_ms\":";
  result += std::to_string(boot_wifi);
  result += wifi_boot::json();
  result += ",\"boot_bg_ms\":";
  result += std::to_string(boot_bg);
  result += ",\"boot_backgrounds_ms\":";
  result += std::to_string(boot_backgrounds);
  result += ",\"boot_orb_ms\":";
  result += std::to_string(boot_orb);
  result += ",\"boot_voice_client_ms\":";
  result += std::to_string(boot_voice_client);
  result += ",\"boot_healthy\":";
  result += (healthy ? "true" : "false");
  result += ",\"alarm_active\":";
  result += (alarm ? "true" : "false");
  result += ",\"announcement_active\":";
  result += (announcement ? "true" : "false");
  result += ",\"voice_active\":";
  result += (voice ? "true" : "false");
  result += ",\"audio_bus_hz\":48000,\"voice_sample_rate\":16000";
  return result;
  };
});
'''}]})
