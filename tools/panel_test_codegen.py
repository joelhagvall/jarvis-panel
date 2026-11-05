"""Attach bounded, visual-only diagnostics to the generated board config.

The component owns authentication, the device lease and a hard expiry timer. This
callback only changes LVGL objects. It never changes Wi-Fi, HA, alarms, audio,
microphone configuration, saved settings, or the actual voice-assistant state.
"""

TEST_CALLBACK = r'''
id(physical_ui).set_test_callback([](const std::string &mode) -> bool {
  static lv_obj_t *original = nullptr, *expected = nullptr;
  static bool saved_hidden[13]{};
  static std::string saved_text[5], injected_text[5];
  static bool text_changed[5]{};
  static const lv_font_t *saved_reply_font{};
  const lv_font_t *fixture_reply_font = id(i600_30)->get_lv_font();
  static uint32_t saved_listen_duration{};
  static unsigned saved_orb_method{};
  static std::string current_mode;
  const bool is60 = mode == "animation_60" || mode == "animation_60_linear" || mode == "animation_60_staged";
  lv_obj_t *objects[] = {id(orb_listen), id(orb_think), id(orb_morph),
                        id(orb_speak), id(orb_settle), id(cards),
                        id(reply_lbl), id(net_pill), lv_obj_get_child(id(page_tavla)->obj, 0),
                        lv_obj_get_child(id(page_orb)->obj, 0), lv_obj_get_child(id(page_ota)->obj, 0),
                        id(you_row), id(jv_row)};
  lv_obj_t *labels[] = {id(you_lbl), id(reply_lbl), id(net_lbl), id(you_txt), id(jv_txt)};
  const bool busy = id(assist).is_running() ||
                    id(panel_media)->state == media_player::MEDIA_PLAYER_STATE_ANNOUNCING || id(timer_ringing) ||
                    id(h_smoke_alarm) || id(h_water_alarm) || id(perf_running);
  auto restore = [&]() {
    if (!original) return;
#ifdef HALLPANEL_DETAIL_LOAD
    panel_detail_load::fixture.restore();
#endif
#ifdef HALLPANEL_MUSIC_LOAD
    const bool music_load = current_mode == "music_load" || current_mode.rfind("detail_",0)==0;
    panel_music_load::fixture.restore();
    if(music_load) id(music_load_restore).execute();
#endif
    // Do not replace actual voice/alarm feedback if it preempted the test.
    if (!busy) {
      for (unsigned i = 0; i < 11; ++i) {
        if (saved_hidden[i]) lv_obj_add_flag(objects[i], LV_OBJ_FLAG_HIDDEN);
        else lv_obj_remove_flag(objects[i], LV_OBJ_FLAG_HIDDEN);
      }
      for (unsigned i = 0; i < 3; ++i)
        if (text_changed[i] && injected_text[i] == lv_label_get_text(labels[i])) {
          if(i == 1 && lv_obj_get_style_text_font(labels[i], LV_PART_MAIN) == fixture_reply_font)
            lv_obj_set_style_text_font(labels[i], saved_reply_font, LV_PART_MAIN);
          lv_label_set_text(labels[i], saved_text[i].c_str());
        }
      if (lv_screen_active() == expected) lv_screen_load(original);
    }
    // Network text and background visibility are independent of real voice UI.
    for (unsigned i = 7; i < 11; ++i) {
      if (saved_hidden[i]) lv_obj_add_flag(objects[i], LV_OBJ_FLAG_HIDDEN);
      else lv_obj_remove_flag(objects[i], LV_OBJ_FLAG_HIDDEN);
    }
    if (text_changed[2] && injected_text[2] == lv_label_get_text(labels[2]))
      lv_label_set_text(labels[2], saved_text[2].c_str());
    // Only restore chat rows still owned by this fixture, even if real voice preempted it.
    for (unsigned i = 3; i < 5; ++i) {
      if (!text_changed[i] || injected_text[i] != lv_label_get_text(labels[i])) continue;
      lv_label_set_text(labels[i], saved_text[i].c_str());
      if (saved_hidden[i+8]) lv_obj_add_flag(objects[i+8], LV_OBJ_FLAG_HIDDEN);
      else lv_obj_remove_flag(objects[i+8], LV_OBJ_FLAG_HIDDEN);
    }
#ifdef HALLPANEL_ORB_VIRTUAL
    packed_orb::select_timeline(true);
    packed_orb::synthetic_voice = false;
#endif
#ifdef HALLPANEL_ORB_PREFETCH
    packed_orb::prefetch.set_method(saved_orb_method);
#endif
    lv_animimg_set_duration(id(orb_listen), saved_listen_duration);
    lv_animimg_start(id(orb_listen));
    lv_animimg_start(id(orb_think));
    lv_animimg_start(id(orb_speak));
    panel_vinyl::page.test_spin(false);
    original = expected = nullptr;
    current_mode.clear();
    id(panel_test_active) = false;
  };
  if (mode == "restore" || mode == "idle") { restore(); return true; }
  if (busy || id(night) || !id(assets_loaded) || !lv_obj_has_flag(id(settings), LV_OBJ_FLAG_HIDDEN))
    return false;
  // speaking_live: the real director and the panel-drawn voice wave, driven by a synthetic voice (no audio).
  const bool live_speech = mode == "speaking_live";
  if (mode != "listening" && mode != "animation" && mode != "animation_live" && !is60 && mode != "thinking" && mode != "speaking" && !live_speech && mode != "night" &&
      mode != "ota" && mode != "wifi_disconnected" && mode != "ha_disconnected" &&
      mode != "api_timeout" && mode != "missing_image" && mode != "chat" && mode != "chat_scroll" &&
      mode != "turntable" && mode != "music_load" && mode != "detail_weather" && mode != "detail_calendar" && mode != "detail_ai") return false;
#ifndef HALLPANEL_DETAIL_LOAD
  if(mode.rfind("detail_",0)==0)return false;
#endif
  lv_obj_t *active = lv_screen_active();
  if (!original && active != id(page_glass_v2)->obj && active != id(page_hemmet)->obj &&
      active != id(page_tavla)->obj) return false;
  if (original && active != expected) { restore(); return false; }
  // Re-arming the active mode only extends its deadline: a long measurement keeps
  // the running frames instead of resetting them to image 0 every 30 seconds.
  if (original && mode == current_mode) return true;
  if (!original) {
    original = active;
    saved_listen_duration = lv_animimg_get_duration(id(orb_listen));
    saved_reply_font = lv_obj_get_style_text_font(id(reply_lbl), LV_PART_MAIN);
#ifdef HALLPANEL_ORB_PREFETCH
    saved_orb_method = packed_orb::prefetch.method();
#endif
    for (unsigned i = 0; i < 13; ++i) saved_hidden[i] = lv_obj_has_flag(objects[i], LV_OBJ_FLAG_HIDDEN);
    for (unsigned i = 0; i < 5; ++i) { saved_text[i] = lv_label_get_text(labels[i]); text_changed[i] = false; }
  }
  id(panel_test_active) = true;
#ifdef HALLPANEL_DETAIL_LOAD
  panel_detail_load::fixture.restore();
#endif
#ifdef HALLPANEL_MUSIC_LOAD
  if(panel_music_load::fixture.active) {
    panel_music_load::fixture.restore();id(music_load_restore).execute();
  }
#endif
  current_mode = mode;
#ifdef HALLPANEL_ORB_VIRTUAL
  packed_orb::select_timeline(is60 || live_speech);
  packed_orb::synthetic_voice = live_speech;
#endif
#ifdef HALLPANEL_ORB_PREFETCH
  packed_orb::prefetch.set_method(mode == "animation_60_linear" ? 1 : mode == "animation_60_staged" ? 2 : saved_orb_method);
#endif
  auto text = [&](unsigned i, const char *value) {
    injected_text[i] = value;
    text_changed[i] = true;
    if(i == 1) lv_obj_set_style_text_font(labels[i], fixture_reply_font, LV_PART_MAIN);
    lv_label_set_text(labels[i], value);
  };
  // Freeze the first frame for reproducible golden screenshots. Animation load
  // is measured separately by the existing performance_test script.
  for (unsigned i = 0; i < 5; ++i) {
    lv_anim_delete(objects[i], nullptr);
    const void **sources = lv_animimg_get_src(objects[i]);
    if (sources && lv_animimg_get_src_count(objects[i])) lv_image_set_src(objects[i], sources[0]);
    lv_obj_add_flag(objects[i], LV_OBJ_FLAG_HIDDEN);
  }
  lv_obj_add_flag(id(cards), LV_OBJ_FLAG_HIDDEN);
  lv_obj_remove_flag(id(reply_lbl), LV_OBJ_FLAG_HIDDEN);
  lv_obj_remove_flag(objects[8], LV_OBJ_FLAG_HIDDEN);
  if (mode == "animation_live" || is60) lv_obj_remove_flag(objects[9], LV_OBJ_FLAG_HIDDEN);
  else lv_obj_add_flag(objects[9], LV_OBJ_FLAG_HIDDEN);
  lv_obj_add_flag(objects[10], LV_OBJ_FLAG_HIDDEN);
  if (mode == "chat" || mode == "chat_scroll") {
    text(3, "Can we switch to English?");
    if (mode == "chat") text(4, "Of course. We can continue in English.");
    else text(4, "First paragraph: a local scroll test without a microphone or model.\n"
                 "Second paragraph: drag inside this bubble to read earlier lines.\n"
                 "Third paragraph: the card and controls stay in place.\n"
                 "Fourth paragraph: another line for the scroll test.\n"
                 "Last paragraph: end of the scroll test.");
    lv_obj_remove_flag(id(you_row), LV_OBJ_FLAG_HIDDEN);
    lv_obj_remove_flag(id(jv_row), LV_OBJ_FLAG_HIDDEN);
    expected = id(page_glass_v2)->obj;
  }
  else if (mode == "night") expected = id(page_natt)->obj;
  // turntable: Spelas nu's record turns as it does while music plays, without music (tools/turntable_fps.py).
  // DETAIL_LOAD_BRANCH
  else if (mode == "turntable" || mode == "music_load") {
#ifdef HALLPANEL_MUSIC_LOAD
    if(mode == "music_load") panel_music_load::fixture.begin(
      {id(mp_caption),id(mp_title),id(mp_artist),id(mp_album)}, id(mp_group), id(mp_empty), millis(),
      [](const lv_image_dsc_t *image, uint64_t identity) {
        panel_vinyl::page.source(image,panel_vinyl::Kind::PICTURE,identity);
      });
#endif
    panel_vinyl::page.test_spin(true);
    expected = id(page_musik)->obj;
  }
  else if (mode == "ota") expected = id(page_ota)->obj;
  else if (mode == "missing_image") {
    lv_obj_add_flag(objects[8], LV_OBJ_FLAG_HIDDEN);
    expected = id(page_tavla)->obj;
  } else if (mode == "wifi_disconnected" || mode == "ha_disconnected") {
    text(2, mode == "wifi_disconnected" ? "Nätet är nere – timers och larm fungerar" : "Ingen kontakt med Jarvis");
    lv_obj_remove_flag(id(net_pill), LV_OBJ_FLAG_HIDDEN);
    expected = id(page_glass_v2)->obj;
  } else {
    text(0, (mode == "listening" || mode == "animation" || mode == "animation_live" || is60) ? "Jag lyssnar…" : "Test av Hallpanel");
    text(1, mode == "speaking" ? "Det här är ett lokalt test utan ljud eller modell." :
            live_speech ? "Vågen följer en påhittad röst, utan ljud eller modell." :
            mode == "api_timeout" ? "Det gick inte just nu." : "");
    unsigned state = mode == "thinking" ? 1 : (mode == "speaking" || live_speech) ? 3 : 0;
    lv_obj_remove_flag(objects[state], LV_OBJ_FLAG_HIDDEN);
#ifdef HALLPANEL_ORB_VIRTUAL
    const auto frame_count = packed_orb::source_count(id(orb_listen));
#else
    const auto frame_count = lv_animimg_get_src_count(id(orb_listen));
#endif
    // animation/animation_live are the responsiveness protocol's 25 fps workload: one native image
    // per 40.625 ms, as the original 64-image 2.6 s loop, whatever the production loop length.
    lv_animimg_set_duration(id(orb_listen), is60 ? (1000 * frame_count + 30) / 60 :
                           (mode == "animation" || mode == "animation_live") ? (325 * frame_count + 4) / 8 :
                           saved_listen_duration);
    if (mode == "animation" || mode == "animation_live" || is60) lv_animimg_start(id(orb_listen));
    if (live_speech) lv_animimg_start(id(orb_speak));   // a running state: the director draws it
    expected = id(page_orb)->obj;
  }
  lv_screen_load(expected);
  return true;
});
'''


def apply(cfg, root=None):
    """Mutate the final voice board config; call once after its normal scripts exist."""
    required = {'page_glass_v2', 'page_hemmet', 'page_tavla', 'page_natt', 'page_orb', 'page_ota'}
    if not required <= {page.get('id') for page in cfg.get('lvgl', {}).get('pages', [])}:
        raise ValueError('Synthetic diagnostics require all six Hallpanel pages')
    globals_ = cfg.setdefault('globals', [])
    if any(item.get('id') == 'panel_test_active' for item in globals_):
        raise ValueError('Synthetic diagnostics already attached')
    globals_.append({'id': 'panel_test_active', 'type': 'bool', 'initial_value': 'false'})
    night_auto = next(item for item in cfg['script'] if item.get('id') == 'night_auto')
    condition = night_auto['then'][0]['if']['condition']
    condition['lambda'] = 'if (id(panel_test_active)) return false;\n' + condition['lambda']
    boot = cfg['esphome'].setdefault('on_boot', [])
    if not isinstance(boot, list):
        boot = [boot]
        cfg['esphome']['on_boot'] = boot
    callback=TEST_CALLBACK
    if {'hm_wx_hours','hm_wx_week','hm_wx_facts','hm_cal_week','ai_panel'} <= {s.get('id') for s in cfg.get('text_sensor',[])}:
        import panel_detail_codegen
        samples=panel_detail_codegen.apply(cfg)
        callback=callback.replace('// DETAIL_LOAD_BRANCH',panel_detail_codegen.callback(samples))
    boot.append({'priority': -101, 'then': [{'lambda': callback}]})
    return cfg
