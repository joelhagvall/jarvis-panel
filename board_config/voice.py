"""--voice: microphones, speaker, wake word, Home Assistant's voice assistant and its timers."""
from .common import ROOT, SYNC_SLIDER, scripts

# C++ for the voice section. The labels are glass-v2's: timer_pill/timer_lbl over the dashboard's clock,
# n_timer on Natt and h_timer on Hemmet; reply_lbl on the voice page (through show_reply when the style has it,
# so a short error does not keep a long reply's small font).
# A failed voice turn's reason, shown on the voice page and spoken from the panel's own flash (Joel, 2026-11-03: a
# silent wake felt like nothing happened). name: (file in assets/sounds, text). Change a text, then run
# assets/sounds/make_errors.py, which renders it in Jarvis's voice; offline too, so "nätet är nere" can be heard.
ERROR_SOUNDS = {"error_net": ("error_net.wav", "Nätet är nere, jag hör dig inte just nu."),
                "error_missed": ("error_missed.wav", "Jag uppfattade inte det."),
                "error_failed": ("error_failed.wav", "Det gick inte just nu.")}
ERROR_TEXT = f"""const char *why = "{ERROR_SOUNDS['error_failed'][1]}";
if (code.rfind("stt", 0) == 0)
  why = id(net_down) ? "{ERROR_SOUNDS['error_net'][1]}" : "{ERROR_SOUNDS['error_missed'][1]}";
lv_label_set_text(id(reply_lbl), why);"""


def error_sound(net_down="id(net_down)"):
    """on_error's spoken reason: the same choice as ERROR_TEXT, as on-device announcements."""
    def play(name):
        return {"media_player.speaker.play_on_device_media_file": {"media_file": name, "announcement": True}}
    stt = 'code.rfind("stt", 0) == 0'
    return {"if": {"condition": {"lambda": f"return {stt} && {net_down};"}, "then": [play("error_net")],
                   "else": [{"if": {"condition": {"lambda": f"return {stt};"}, "then": [play("error_missed")],
                                    "else": [play("error_failed")]}}]}}
TIMERS_SHOW = r"""if (id(timer_ringing)) return;   // "... är klar" stays while the chime rings
const auto &timers = id(assist).get_timers();
const voice_assistant::Timer *t = nullptr;
for (const auto &it : timers)
  if (t == nullptr || it.seconds_left < t->seconds_left) t = &it;
if (t == nullptr) {
  lv_obj_add_flag(id(timer_pill), LV_OBJ_FLAG_HIDDEN);
  lv_obj_add_flag(id(n_timer), LV_OBJ_FLAG_HIDDEN);
  lv_label_set_text(id(h_timer), "Ingen timer igång");
  lv_obj_set_style_text_color(id(h_timer), lv_color_hex(0xFFFFFF), LV_PART_MAIN);
  lv_obj_set_style_text_opa(id(h_timer), 153, LV_PART_MAIN);
  return;
}
uint32_t s = t->seconds_left;
char left[16];
if (s >= 3600)
  snprintf(left, sizeof(left), "%u:%02u:%02u", (unsigned) (s / 3600), (unsigned) (s / 60 % 60), (unsigned) (s % 60));
else
  snprintf(left, sizeof(left), "%u:%02u", (unsigned) (s / 60), (unsigned) (s % 60));
std::string name = t->name.empty() ? std::string("Timer") : t->name;
name[0] = toupper((unsigned char) name[0]);
std::string text = name + " · " + left;
if (!t->is_active) text += " (paus)";
if (timers.size() > 1) text += " +" + std::to_string(timers.size() - 1);
lv_label_set_text(id(timer_lbl), text.c_str());
lv_label_set_text(id(n_timer), text.c_str());
lv_label_set_text(id(h_timer), (text + " kvar").c_str());
lv_obj_remove_flag(id(timer_pill), LV_OBJ_FLAG_HIDDEN);
lv_obj_remove_flag(id(n_timer), LV_OBJ_FLAG_HIDDEN);
lv_obj_set_style_text_color(id(h_timer), lv_color_hex(0xFF9F0A), LV_PART_MAIN);
lv_obj_set_style_text_opa(id(h_timer), 255, LV_PART_MAIN);"""
# Before TIMERS_SHOW (which returns early): the quick timers while none runs, Avbryt and room for the text while one does.
QUICK_SHOW = r"""{
const bool running = !id(assist).get_timers().empty();
if (running) lv_obj_add_flag(id(h_timer_quick), LV_OBJ_FLAG_HIDDEN); else lv_obj_remove_flag(id(h_timer_quick), LV_OBJ_FLAG_HIDDEN);
if (running) lv_obj_remove_flag(id(h_timer_cancel), LV_OBJ_FLAG_HIDDEN); else lv_obj_add_flag(id(h_timer_cancel), LV_OBJ_FLAG_HIDDEN);
lv_obj_set_width(id(h_timer), running ? 340 : 196);
}
"""
READY_OK = r"""if (boot_was_software()) return false;
auto t = id(ha_time).now();
if (!t.is_valid()) return false;
int m = t.hour * 60 + t.minute;
int on = id(night_start).hour * 60 + id(night_start).minute, off = id(night_end).hour * 60 + id(night_end).minute;
return !(on > off ? (m >= on || m < off) : (m >= on && m < off));"""
TIMER_DONE = r"""id(timer_ringing) = true;
std::string name = timer.name.empty() ? std::string("Timern") : timer.name;
name[0] = toupper((unsigned char) name[0]);
std::string text = name + " är klar";
lv_label_set_text(id(timer_lbl), text.c_str());
lv_label_set_text(id(n_timer), text.c_str());
lv_label_set_text(id(h_timer), text.c_str());
lv_obj_remove_flag(id(timer_pill), LV_OBJ_FLAG_HIDDEN);
lv_obj_remove_flag(id(n_timer), LV_OBJ_FLAG_HIDDEN);
lv_obj_set_style_text_color(id(h_timer), lv_color_hex(0xFF9F0A), LV_PART_MAIN);
lv_obj_set_style_text_opa(id(h_timer), 255, LV_PART_MAIN);"""


def reminder_timer_rows():
    """reminders_page.TIMER_ROWS, before TIMERS_SHOW (which returns early while the chime rings)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("reminders_page", ROOT / "styles" / "glass-v2" / "reminders_page.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.TIMER_ROWS


def apply(cfg, flags):
    """Returns whether the config has the stateful orb page that the voice events drive."""
    script = scripts(cfg)
    error_text, net_down = ERROR_TEXT, "id(net_down)"
    page_ids = {p["id"] for p in cfg["lvgl"]["pages"]}
    widget_ids = set()

    def walk(ws):
        for w in ws or []:
            for body in w.values():
                if isinstance(body, dict):
                    if "id" in body:
                        widget_ids.add(body["id"])
                    walk(body.get("widgets"))
    for pg in cfg["lvgl"]["pages"]:
        walk(pg.get("widgets"))
    has_orb = "page_orb" in page_ids and {"orb_listen", "you_lbl", "reply_lbl"} <= widget_ids
    # Jarvis's cards under the orb (jarvis_orb.py): a new question or an announcement starts without them.
    fresh = ([{"lvgl.label.update": {"id": "reply_lbl", "text": ""}},
              *([{"lambda": "id(reply_text).clear();"}] if script('show_reply') else []),
              # a reply rolling in its box (reply_fit.h) stops, and your sentence gets its two lines back
              *([{"lambda": "panel_reply::stop();\nlv_obj_scroll_to_y(id(reply_box), 0, LV_ANIM_OFF);\n"
                            "lv_obj_set_height(id(you_lbl), 58);\n"
                            "lv_obj_set_style_text_font(id(you_lbl), id(i600_22)->get_lv_font(), LV_PART_MAIN);"}]
                if "reply_box" in widget_ids else []),
              *([{"lvgl.widget.hide": "cards"}] if "cards" in widget_ids else []),
              {"lvgl.widget.show": "reply_lbl"},
              {"lambda": "lv_obj_set_style_text_opa(id(you_lbl), LV_OPA_COVER, LV_PART_MAIN);"}]
             if has_orb else [])
    states = ["orb_listen", "orb_think", "orb_morph", "orb_speak", "orb_settle"]

    def only(state):
        if not has_orb:
            return []
        phase = ([{"lambda": "#ifdef HALLPANEL_ORB_VIRTUAL\n" +
                   f"packed_orb::sync_loop_phase(id({state}));\n#endif\n"}]
                 if state in ("orb_listen", "orb_think") else [])
        return [*phase, {"lvgl.widget.hide": [x for x in states if x != state]}, {"lvgl.widget.show": state}]

    dashboard = next(p["id"] for p in cfg["lvgl"]["pages"]
                     if p["id"] not in ("page_orb", "page_hemmet", "page_natt", "page_tavla"))
    # The voice page goes back to the page it was opened from (the dashboard, Hemmet or Tavla), Natt aside.
    back_pages = [dashboard] + [p["id"] for p in cfg["lvgl"]["pages"]
                                if p["id"] not in ("page_orb", "page_natt", dashboard)]
    remember_page = {"lambda": "".join(f"if (lv_screen_active() == id({pid})->obj) id(voice_from) = {i};\n"
                                       for i, pid in enumerate(back_pages))}
    has_night = script("night_on") is not None
    has_timer_ui = {"timer_pill", "timer_lbl", "n_timer", "h_timer"} <= widget_ids
    # Hemmet's quick timers (gen.py quick_timers): 5/10/15 min while none runs, Avbryt while one does
    has_quick = {"h_timer_quick", "h_timer_cancel"} <= widget_ids
    # Hemmet's reminder page (reminders_page.py): every timer in its own row
    timer_rows = reminder_timer_rows() if {"rm_t0", "rm_t_none"} <= widget_ids else ""
    if not any(g["id"] == "net_down" for g in cfg.get("globals", [])):   # a generator without the net status
        error_text = ERROR_TEXT.replace("id(net_down)", "false")
        net_down = "false"
    # Mikrofon: a switch in Home Assistant, kept on the panel. Off stops the wake word (guests, a phone call, the
    # TV waking Jarvis), and the mic buttons turn red; every place that starts the wake word asks it first.
    wake_start = {"if": {"condition": {"and": [{"switch.is_on": "mic_on"},
                                               {"not": {"voice_assistant.is_running": {}}}]},
                         "then": [{"micro_wake_word.start": {}}]}}
    cfg["switch"] = cfg.get("switch", []) + [
        # Off at boot, on once the ES8311 and I2S are set up (on_boot below): switched on at once, the
        # amplifier played the codec's start-up as a click.
        {"platform": "gpio", "id": "audio_amplifier", "pin": "GPIO53", "restore_mode": "ALWAYS_OFF"},
        {"platform": "template", "id": "mic_on", "name": "Mikrofon", "icon": "mdi:microphone", "optimistic": True,
         "restore_mode": "RESTORE_DEFAULT_ON",
         # TemplateSwitch runs turn_*_action before publishing its optimistic state. These post-state
         # hooks let wake_start see the new value, and prevent a stop callback from seeing stale "on".
         "on_turn_on": [{"logger.log": {"format": "Microphone enabled; starting wake-word listener", "level": "INFO"}},
                        {"if": {"condition": {"api.connected": {}}, "then": [wake_start]}}],
         "on_turn_off": [{"logger.log": {"format": "Microphone disabled; stopping voice and wake word", "level": "INFO"}},
                         {"voice_assistant.stop": {}}, {"micro_wake_word.stop": {}}]}]
    cfg.setdefault('interval',[]).append({'interval':'1s','then':[{'lambda':'''static unsigned previous=99;
const unsigned now=(id(mic_on).state?1:0)|(id(wake).is_running()?2:0)|(id(assist).is_running()?4:0);
if(now!=previous){previous=now;ESP_LOGI("mic_state","enabled=%u wake_running=%u assist_running=%u",
  now&1,(now>>1)&1,(now>>2)&1);}'''}]})
    if script("mic_show"):
        # The mic buttons (glass-v2): a long press toggles the switch and shows it at once; a change from Home
        # Assistant is picked up within a second (never before LVGL is up, as an interval).
        script("mic_toggle")["then"] = [{"switch.toggle": "mic_on"},
                                        {"lambda": "id(mic_muted) = !id(mic_on).state;"},
                                        {"script.execute": "mic_show"}]
        cfg["interval"] = cfg.get("interval", []) + [{"interval": "1s", "then": [{"if": {
            "condition": {"lambda": "return id(mic_muted) == id(mic_on).state;"},
            "then": [{"lambda": "id(mic_muted) = !id(mic_on).state;"}, {"script.execute": "mic_show"}]}}]}]
    # The ES8311 and the ES7210 share MCLK, BCLK and LRCLK, and ESPHome lets one device at a time hold an I2S
    # port ("Parent bus is busy" when the wake word listened). So two ports on the same pins: the microphones'
    # drives the clocks and never stops (mic_clock below keeps it running), the speaker's is secondary and only
    # reads them. Wake word and speech go on while Jarvis or the radio plays. The speaker's own port stops
    # after every sound (the mixer stops it), so it cannot be the one that drives them.
    boot = cfg["esphome"].get("on_boot", [])
    cfg["esphome"]["on_boot"] = [*(boot if isinstance(boot, list) else [boot]),
                                 {"priority": -100, "then": [{"delay": "1s"}, {"switch.turn_on": "audio_amplifier"}]}]
    shared = lambda pin: {"number": pin, "allow_other_uses": True}
    cfg["i2s_audio"] = [{"id": "audio_bus", "i2s_mclk_pin": "GPIO13",
                         "i2s_lrclk_pin": shared("GPIO10"), "i2s_bclk_pin": shared("GPIO12")},
                        {"id": "audio_out_bus", "i2s_lrclk_pin": shared("GPIO10"), "i2s_bclk_pin": shared("GPIO12")}]
    cfg["sensor"] = cfg.get("sensor", []) + [
        {"platform": "sound_level", "id": "mic_clock", "microphone": "mics", "passive": False,
         "measurement_duration": "60s", "peak": {"id": "mic_peak", "internal": True}}]
    cfg["audio_dac"] = [{"platform": "es8311", "id": "es8311_dac", "i2c_id": "bus_a", "address": 0x18,
                         "sample_rate": 48000}]
    cfg["audio_adc"] = [{"platform": "es7210", "id": "es7210_adc", "i2c_id": "bus_a", "address": 0x40,
                         "sample_rate": 48000}]
    cfg["external_components"].append({"source": {"type": "local", "path": str(ROOT / "components")},
                                       "components": ["panel_audio"]})
    cfg["microphone"] = [{"platform": "i2s_audio", "id": "mics_raw", "i2s_audio_id": "audio_bus", "i2s_din_pin": "GPIO11",
                          "sample_rate": 48000, "bits_per_sample": "16bit", "adc_type": "external"},
                         {"platform": "panel_audio", "id": "mics", "microphone": "mics_raw", "sample_rate": 16000}]
    cfg["speaker"] = [{"platform": "i2s_audio", "id": "speaker_out", "i2s_audio_id": "audio_out_bus",
                       "i2s_mode": "secondary", "i2s_dout_pin": "GPIO9",
                       "audio_dac": "es8311_dac", "dac_type": "external", "channel": "mono", "sample_rate": 48000,
                       # 500 ms, not 100: drawing the voice page starved the announcement chain for
                       # 20-80 ms about 2 s into a reply (audio_gap; none with the same WAV on the dashboard),
                       # heard as a stutter. The ring lives in PSRAM.
                       "bits_per_sample": "16bit", "buffer_duration": "500ms"},
                      # Two sources on the one speaker: Jarvis's voice (announcements) over the radio (media).
                      {"platform": "mixer", "id": "speaker_mixer", "output_speaker": "speaker_out", "num_channels": 1,
                       "source_speakers": [{"id": "voice_mix_spk"}, {"id": "media_spk"}]},
                      {"platform": "resampler", "id": "voice_spk", "output_speaker": "voice_mix_spk",
                       "buffer_duration": "100ms", "task_stack_in_psram": True}]
    # A stutter in a reply logs nothing by itself: the I2S task pads missing audio with silence.
    # bench/speaker_gaps.h finds the holes between real-audio buffers and logs them as audio_gap.
    cfg["esphome"].setdefault("includes", []).append(str(ROOT / "bench/speaker_gaps.h"))
    cfg["esphome"].setdefault("on_boot", []).append({"priority": -100, "then": [{"lambda":
        "id(speaker_out)->add_audio_output_callback("
        "[](uint32_t frames, int64_t end_us) { panel_speaker_gaps.played(frames, end_us); });\n"
        "id(audio_gaps).publish_state(0);"}]})
    # Also in Home Assistant's history, so a stutter Joel hears at any hour can be dated afterwards.
    cfg["sensor"] = cfg.get("sensor", []) + [
        {"platform": "template", "id": "audio_gaps", "name": "Ljudglapp", "state_class": "total_increasing",
         "accuracy_decimals": 0, "update_interval": "never", "entity_category": "diagnostic"},
        {"platform": "template", "id": "audio_gap_ms", "name": "Senaste ljudglapp", "unit_of_measurement": "ms",
         "state_class": "measurement", "accuracy_decimals": 0, "update_interval": "never",
         "entity_category": "diagnostic"}]
    cfg.setdefault("interval", []).append({"interval": "1s", "then": [{"lambda":
        "uint32_t gaps, last, into, worst, total;\n"
        "if (panel_speaker_gaps.report(gaps, last, into, worst, total)) {\n"
        "  ESP_LOGW(\"audio_gap\", \"speaker starved %u ms at %u ms into the sound (gaps %u, worst %u ms, total %u ms)\","
        " (unsigned) last, (unsigned) into, (unsigned) gaps, (unsigned) worst, (unsigned) total);\n"
        "  id(audio_gap_ms).publish_state(last);\n"
        "  id(audio_gaps).publish_state(gaps);\n"
        "}\n"
        "uint32_t after_announce, after_text;\n"
        "if (panel_first_reply.take(panel_speaker_gaps.sound_start_us(), esp_timer_get_time(), after_announce, after_text))\n"
        "  ESP_LOGI(\"latency\", \"Reply first audio out: %u ms after announcing, %u ms after STT text\","
        " (unsigned) after_announce, (unsigned) after_text);"}]})
    # A media player, not the bare speaker, so streamed replies can start early (Home Assistant sends
    # the reply's URL with intent-progress). Speech stays 16 kHz WAV and is resampled to the shared 48 kHz bus.
    # Music arrives as 48 kHz mono FLAC: full music bandwidth without 768 kbps raw PCM Wi-Fi traffic.
    # The media pipeline plays Sveriges Radio (the agent's radio tool); Home Assistant turns its MP3 into WAV too.
    # buffer_size stays at ESPHome's default (1 MB, shared by both pipelines). It also sets how much of a stream
    # must have arrived before playing starts, min(1 s of audio, 3/4 of the buffer), and only a buffer under
    # 43 kB (1.3 s of radio) would lower that; the voice stream pads a short first sentence to 1 s instead
    # (speech.py PRIME_S in the Jarvis integration).
    cfg["media_player"] = [{"platform": "speaker", "id": "panel_media", "name": "Hallpanel",
                            "announcement_pipeline": {"speaker": "voice_spk", "format": "WAV",
                                                      "sample_rate": 16000, "num_channels": 1},
                            "media_pipeline": {"speaker": "media_spk", "format": "FLAC",
                                               "sample_rate": 48000, "num_channels": 1},
                            # "Ekolod" (Joel's pick of 29, 2026-10-22): a soft ping at C6 with a long room,
                            # 0.95 s, each time the panel starts listening: after the wake word and when it
                            # listens again after a question.
                            "files": [{"id": "listen_sound",
                                       "file": {"type": "local", "path": str(ROOT / "assets" / "sounds" / "listen.wav")}},
                                      # once when the dashboard first shows (assets/sounds/make_ready.py)
                                      {"id": "ready_sound",
                                       "file": {"type": "local", "path": str(ROOT / "assets" / "sounds" / "ready.wav")}},
                                      # a soft two-note chime when a timer is done (assets/sounds/make_timer.py)
                                      {"id": "timer_sound",
                                       "file": {"type": "local", "path": str(ROOT / "assets" / "sounds" / "timer.wav")}},
                                      # a failed turn's reason, spoken (ERROR_SOUNDS, assets/sounds/make_errors.py)
                                      *({"id": name, "file": {"type": "local", "path": str(ROOT / "assets" / "sounds" / f)}}
                                        for name, (f, _) in ERROR_SOUNDS.items())]}]
    if has_night:
        # Keep manual volume across night/day, voice-page returns and reboots. A deferred callback
        # from an automatic night cap must never replace the stored preference with 35%.
        cfg["esphome"].setdefault("includes", []).append(str(ROOT / "bench/volume_state.h"))
        cfg.setdefault("globals", []).append({"id": "day_volume", "type": "float", "restore_value": True,
                                               "initial_value": "0.2"})
        cfg["media_player"][0]["on_volume"] = [{"lambda": "panel_volume_state.observed(x, id(day_volume));"}]
        def set_volume(value):
            return (f"float v = {value};\npanel_volume_state.automatic(v);\n"
                    "id(panel_media)->make_call().set_volume(v).perform();")
        script("night_hw_on")["then"].append({"lambda": set_volume("std::min(id(day_volume), 0.35f)")})
        script("night_hw_off")["then"].append({"lambda": set_volume("id(day_volume)")})
        cfg["esphome"].setdefault("on_boot", []).append({"priority": -100, "then": [
            {"delay": "1s"}, {"lambda": "panel_volume_state.ready = true;\n" +
             set_volume("id(night) ? std::min(id(day_volume), 0.35f) : id(day_volume)")} ]})
    if script("vol_set"):
        # Inställningar's Volym: the speaker's volume, and the listening chime at it so he hears the level.
        script("vol_set")["then"] = [{"lambda": "id(day_volume) = id(vol_pct) / 100.0f;\n"
                                                               "id(panel_media)->make_call().set_volume(id(day_volume)).perform();"},
                                     {"media_player.speaker.play_on_device_media_file": {"media_file": "listen_sound",
                                                                                          "announcement": True}}]
        script("settings_open")["then"].insert(0, {"lambda": SYNC_SLIDER.format(key="vol", value="id(panel_media)->volume * 100")})
    if has_night and script("vol_set") and script("bright_set") and script("nightl_set"):
        # GET/POST /settings (docs/remote-panel.md): read and set Joel's saved levels exactly, without the sliders.
        # Keys are panel_debug_settings::Key: 1 day volume %, 2 day brightness %, 3 night light %, 4/5 night start/end.
        # Same effect as the sliders, minus the chime; at night only the night level reaches the screen and
        # the speaker keeps its night cap.
        cfg["esphome"].setdefault("on_boot", []).append({"priority": -96, "then": [{"lambda": r'''
id(physical_ui).set_settings_callbacks([]() -> std::string {
  char out[320];
  snprintf(out, sizeof(out),
           "{\"day_volume\":%d,\"speaker_volume\":%d,\"brightness\":%d,\"night_light\":%d,"
           "\"night_start\":\"%02d:%02d\",\"night_end\":\"%02d:%02d\",\"night\":%s,\"night_hours\":%s}",
           (int) roundf(id(day_volume) * 100), (int) roundf(id(panel_media)->volume * 100),
           (int) roundf(id(day_light) * 100), (int) roundf(id(night_light).state),
           id(night_start).hour, id(night_start).minute, id(night_end).hour, id(night_end).minute,
           id(night) ? "true" : "false", id(night_hours) ? "true" : "false");
  return out;
}, [](int key, int a, int b) -> bool {
  if (key == 1) {
    id(day_volume) = a / 100.0f;
    id(vol_pct) = a;
    float v = id(night) ? std::min(id(day_volume), 0.35f) : id(day_volume);
    panel_volume_state.automatic(v);
    id(panel_media)->make_call().set_volume(v).perform();
  } else if (key == 2) {
    id(bright_pct) = a;
    id(day_light) = a / 100.0f;
    if (!id(night)) {
      auto call = id(backlight).turn_on();
      call.set_brightness(id(day_light));
      call.set_transition_length(0);
      call.perform();
    }
  } else if (key == 3) {
    id(nightl_pct) = a;
    id(night_light).make_call().set_value(a).perform();
    if (id(night)) {
      auto call = id(backlight).turn_on();
      call.set_brightness(a / 100.0f);
      call.set_transition_length(0);
      call.perform();
    }
  } else if (key == 4 || key == 5) {
    auto call = key == 4 ? id(night_start).make_call() : id(night_end).make_call();
    call.set_time(a, b, 0);
    call.perform();
  } else {
    return false;
  }
  return true;
});'''}]})
    duck = {"mixer_speaker.apply_ducking": {"id": "media_spk", "decibel_reduction": 20, "duration": "300ms"}}
    unduck = {"mixer_speaker.apply_ducking": {"id": "media_spk", "decibel_reduction": 0, "duration": "1s"}}
    if has_orb:
        cfg.setdefault("globals", []).append({"id": "orb_speaking", "type": "bool", "initial_value": "false"})
        cfg.setdefault("script", []).append({"id": "orb_to_speak", "mode": "single", "then": [
            {"if": {"condition": {"lambda": "return !id(orb_speaking);"},
                    "then": [{"lambda": "id(orb_speaking) = true;"}, *only("orb_morph"),
                             {"lvgl.animimg.start": "orb_morph"}, {"delay": "650ms"}, *only("orb_speak")]}}]})
    stop_ringing = {"if": {"condition": {"lambda": "return id(timer_ringing);"}, "then": [{"script.execute": "timer_stop"}]}}
    cfg["micro_wake_word"] = {"id": "wake", "microphone": "mics", "models": [{"model": "hey_jarvis"}],
                              "on_wake_word_detected": [
                                  stop_ringing,
                                  {"script.stop": "voice_done"},
                                  *([{"script.execute": "settings_close"}] if script("settings_close") else []),
                                  *([remember_page] if has_orb else []),
                                  *([{"lambda": "id(orb_speaking) = false;"},
                                     {"lvgl.label.update": {"id": "you_lbl", "text": "Jag lyssnar…"}},
                                     *fresh,
                                     *only("orb_listen"),
                                     {"lvgl.page.show": {"id": "page_orb", "animation": "FADE_IN", "time": "400ms"}}]
                                    if has_orb else []),
                                  # the radio 20 dB down while he talks and Jarvis answers
                                  duck,
                                  # warm the agent while Joel is still talking (Home Assistant's Jarvis integration)
                                  {"homeassistant.action": {"action": "jarvis_agent.warm"}},
                                  {"voice_assistant.start": {}}]}
    if script("mic_listen"):
        # A tap on a mic button (glass-v2): what the wake word does, without it; or, while Jarvis listens or
        # talks, stop him. voice_done starts the wake word again afterwards.
        script("mic_listen")["then"] = [{"if": {
            "condition": {"voice_assistant.is_running": {}},
            "then": [{"voice_assistant.stop": {}}],
            "else": [{"micro_wake_word.stop": {}}, *cfg["micro_wake_word"]["on_wake_word_detected"]]}}]
    cfg["voice_assistant"] = {
        "id": "assist", "microphone": "mics", "media_player": "panel_media", "micro_wake_word": "wake",
        "noise_suppression_level": 2, "auto_gain": "31dBFS", "volume_multiplier": 2.0,
        "on_client_connected": ([{"lambda":
            "if (!id(boot_voice_client_ms)) id(boot_voice_client_ms) = millis();"}]
            if script('boot_done') else []) + ([{"lambda": "id(boot_ha_connected) = true;"}]
            if script('boot_cache_refresh') else []) + [wake_start],
        "on_client_disconnected": ([{"lambda": "id(boot_ha_connected) = false; id(boot_seen) = 0; id(boot_live_ready) = false;"}]
            if script("boot_cache_refresh") else []) + [{"voice_assistant.stop": {}}, {"micro_wake_word.stop": {}}],
        "on_stt_end": ([{"lvgl.label.update": {"id": "you_lbl", "text": {"format": "%s", "args": ["x.c_str()"]}}},
                        # all of a long question: 19 px in up to three lines down to the reply (jarvis_orb.py's
                        # fit_you, reply_fit.h's show_you; Joel, 2026-11-01)
                        *([{"lambda": "panel_reply::show_you(id(you_lbl), id(i600_22)->get_lv_font(), "
                                      "id(i600_19)->get_lv_font(), 760, 76, 2);"}] if "reply_box" in widget_ids else []),
                        *only("orb_think")] if has_orb else []),
        # x is the reply's URL when Home Assistant starts streaming it, empty otherwise
        "on_intent_progress": ([{"if": {"condition": {"lambda": "return !x.empty();"},
                                        "then": [{"script.execute": "orb_to_speak"}]}}] if has_orb else []),
        # Also an announcement from Jarvis (a reminder, an alarm, the net going down; assist_satellite.announce):
        # ESPHome gives its text here too, and then the panel is not on the voice page yet.
        "on_tts_start": ([{"if": {"condition": {"lambda": "return lv_screen_active() != id(page_orb)->obj && !id(orb_leaving);"},
                                  "then": [remember_page, {"lambda": "id(orb_speaking) = false;"},
                                           *([{"script.execute": "settings_close"}] if script("settings_close") else []),
                                           {"lvgl.label.update": {"id": "you_lbl", "text": ""}},
                                           *fresh,
                                           *only("orb_listen"),
                                           {"lvgl.page.show": {"id": "page_orb", "animation": "FADE_IN", "time": "400ms"}}]}},
                          # all of the reply, in the biggest font where it fits (jarvis_orb.py's show_reply)
                          *([{"lambda": "id(reply_text) = x;"}, {"script.execute": "show_reply"}] if script("show_reply")
                            else [{"lvgl.label.update": {"id": "reply_lbl", "text": {"format": "%s", "args": ["x.c_str()"]}}}]),
                          {"script.execute": "orb_to_speak"}] if has_orb else []),
        # The run ends when Home Assistant is done, which can be while the reply is still playing. A reply
        # that ends in a question keeps the conversation going: once it has played, ESPHome listens again
        # without the wake word (continue_conversation), and that run ends with its own on_end. So the
        # script restarts on every on_end and goes back to the dashboard only once the assistant is idle.
        "on_end": [{"script.execute": "voice_done"}],
        # A short reason on the voice page, then voice_done goes back as after an answer. Speech to text is
        # Home Assistant Cloud, so it fails when Jarvis has no internet (net_down, from sensor.jarvis_hemmet).
        "on_error": ([{"if": {"condition": {"lambda": "return lv_screen_active() == id(page_orb)->obj;"},
                              "then": [{"lambda": error_text.replace("lv_label_set_text(id(reply_lbl), why);",
                                                                     "id(reply_text) = why;\nid(show_reply).execute();")
                                                  if script("show_reply") else error_text},
                                       error_sound(net_down),
                                       {"script.execute": "voice_done"}]}}] if has_orb else [])
                    + [wake_start],
    }
    cfg.setdefault("globals", []).append({"id": "voice_from", "type": "int", "initial_value": "0"})
    if has_orb:     # orb_leave's back-first wait (below)
        cfg["globals"].append({"id": "orb_leaving", "type": "bool", "initial_value": "false"})
    back = [{"lvgl.page.show": {"id": dashboard, "animation": "FADE_IN", "time": "400ms"}}]
    if len(back_pages) > 1:
        back = [{"if": {"condition": {"lambda": f"return id(voice_from) == {i};"},
                        "then": [{"lvgl.page.show": {"id": pid, "animation": "FADE_IN", "time": "400ms"}}]}}
                for i, pid in enumerate(back_pages)]
    if has_night:
        # At night (night mode, or between Natt börjar and Natt slutar after a tap woke the screen) back to
        # Natt, dimmed again.
        back = [{"if": {"condition": {"lambda": "return id(night) || id(night_hours);"},
                        "then": [{"script.execute": "night_on"}], "else": back}}]
    # A page Jarvis was asked for by voice (server/agents/panel.py -> Home Assistant -> esphome.hallpanel_show_page
    # with page: the page's id). While he answers it is kept in voice_to and shown when the voice page goes back,
    # even at night; otherwise voice_go shows it at once. An unknown id changes nothing.
    cfg["globals"].append({"id": "voice_to", "type": "int", "initial_value": "-1"})
    go_pages = [p for p in back_pages if p != "page_ota"] + (["page_natt"] if has_night and "page_natt" in page_ids else [])
    has_bar = any(g["id"] == "bar_page" for g in cfg["globals"])
    # One lambda, not one ESPHome `if` branch per page: the nine branches (about 90 actions, each a heap object
    # plus static storage in internal RAM) took ~5.5 kB of internal RAM and left the panel idling at 96.0 kB, just
    # under the monitor's 96 KiB floor, which pushed "Panelens minne är lågt" (2026-11-04, docs/memory.md).
    # Same steps as before: leave night, close settings, set the tab bar's page, show the page without animation
    # (board_config/pages.py makes every page change immediate); Natt goes through night_on.
    shown = [pid for pid in go_pages if pid != "page_natt"]
    lines = ["const int to = id(voice_to);", "id(voice_to) = -1;", f"if (to < 0 || to >= {len(go_pages)}) return;"]
    if "page_natt" in go_pages:
        lines.append(f"if (to == {go_pages.index('page_natt')}) {{ id(night_on).execute(); return; }}")
    if has_night:
        lines.append("if (id(night)) { id(night) = false; id(night_hw_off).execute(); }")
    if script("settings_close"):
        lines.append("id(settings_close).execute();")
    if has_bar:     # gen.py's bar_back goes back to bar_page (0 Gå hemifrån, 1 Hemmet) from Musik, Tavla and Natt
        lines += [f"if (to == {go_pages.index(pid)}) id(bar_page) = {n};"
                  for n, pid in enumerate(("page_glass_v2", "page_hemmet")) if pid in shown]
    lines += [f"LvPageType *const pages[{len(go_pages)}] = {{{', '.join(f'id({pid})' for pid in go_pages)}}};",
              "pages[to]->get_parent()->show_page(pages[to]->index, LV_SCREEN_LOAD_ANIM_NONE, 0);"]
    cfg["script"].append({"id": "voice_go", "mode": "restart", "then": [{"lambda": "\n".join(lines)}]})
    back = [{"if": {"condition": {"lambda": "return id(voice_to) >= 0;"},
                    "then": [{"script.execute": "voice_go"}], "else": back}}]
    on_voice_page = ({"lambda": "return lv_screen_active() == id(page_orb)->obj;"} if "page_orb" in page_ids
                     else {"lambda": "return false;"})
    cfg.setdefault("api", {}).setdefault("actions", []).append({
        "action": "show_page", "variables": {"page": "string"}, "then": [
            {"lambda": "id(voice_to) = -1;\n" + "".join(f'if (page == "{pid}") id(voice_to) = {i};\n'
                                                        for i, pid in enumerate(go_pages))
                       + 'ESP_LOGI("voice", "show_page %s -> %d", page.c_str(), id(voice_to));'},
            {"if": {"condition": {"not": on_voice_page}, "then": [{"script.execute": "voice_go"}]}}]})
    cfg.setdefault("script", []).append({"id": "voice_done", "mode": "restart", "then": [
        {"wait_until": {"condition": {"not": {"voice_assistant.is_running": {}}}, "timeout": "60s"}},
        # An announcement (a reminder, an alarm, assist_satellite.announce) has no assistant run to wait for:
        # without this the orb settled while Jarvis was still speaking it (2026-10-27).
        {"wait_until": {"condition": {"lambda":
            "return id(panel_media)->state != media_player::MEDIA_PLAYER_STATE_ANNOUNCING;"}, "timeout": "60s"}},
        # the wake word listens again at once, while the orb settles (it did only after the 3 s, so "Hey
        # Jarvis" right after an answer went unheard); a wake word in those 3 s stops this script
        {"if": {"condition": {"not": {"voice_assistant.is_running": {}}}, "then":
                [unduck, wake_start]
                # the voice has stopped: a long reply shows its end, in case the voice ran ahead of the roll
                + ([{"lambda": "panel_reply::finish();"}] if has_orb and "reply_box" in widget_ids else [])
                + ([*only("orb_settle"), {"lvgl.animimg.start": "orb_settle"}, {"delay": "3s"}, *back] if has_orb else [])}}]})
    if has_orb:
        # A tap on the voice page: back at once. While Jarvis listens or talks it stops him first (his voice
        # and an announcement too), as the mic button's tap does.
        stop_voice = ("id(panel_media)->make_call().set_command(media_player::MEDIA_PLAYER_COMMAND_STOP)"
                      ".set_announcement(true).perform();")
        # The page goes back first and the assistant's stop is awaited after it (2026-11-02): the tap waited up
        # to 2 s on the voice page before. While orb_leaving, a late on_tts_start of the stopped run does not
        # bring the voice page back, and the on_end it sends cannot take the page back again 3 s later.
        cfg["script"].append({"id": "orb_leave", "mode": "single", "then": [
            {"script.stop": "voice_done"},
            {"if": {"condition": {"voice_assistant.is_running": {}}, "then": [{"voice_assistant.stop": {}}]}},
            {"lambda": stop_voice},
            {"lambda": "id(orb_leaving) = true;"},
            *back,
            {"wait_until": {"condition": {"not": {"voice_assistant.is_running": {}}}, "timeout": "2s"}},
            {"script.stop": "voice_done"},
            {"lambda": "id(orb_leaving) = false;"},
            unduck, wake_start]})
    chime = {"media_player.speaker.play_on_device_media_file": {"media_file": "listen_sound", "announcement": True}}
    chime_timer = {"media_player.speaker.play_on_device_media_file": {"media_file": "timer_sound", "announcement": True}}
    # The chime may cut a reply still playing; the gap meter must not count that switch as a stutter.
    cfg["voice_assistant"]["on_listening"] = [{"lambda": "panel_speaker_gaps.new_sound(esp_timer_get_time());"}, chime]
    if script("boot_done"):
        # Replace the splash in one refresh: fading its foreground over the
        # dashboard made Jarvis/Startar flash across the newly visible cards.
        cfg["esphome"].setdefault("includes", []).append(str(ROOT / "bench" / "boot_reveal.h"))
        actions = script("boot_done")["then"][0]["if"]["then"]
        hide = next(i for i,a in enumerate(actions) if a.get("lvgl.widget.hide") == "boot_cover")
        actions[hide] = {"script.execute": "boot_fade"}
        # Retain timing IDs for existing diagnostics; no delayed animation remains.
        cfg["script"].append({"id": "boot_fade", "mode": "single", "then": [
            {"lambda": "id(boot_fade_start_ms) = millis();\n"
                       "boot_reveal::show(id(boot_cover), &id(boot_fade_end_ms), []() -> uint32_t { return millis(); });"}]})
        ready_play = [{"logger.log": {"format": "Ready sound", "level": "INFO"}},
                      {"lambda": "panel_volume_state.automatic(id(day_volume));\n"
                                 "id(panel_media)->make_call().set_volume(id(day_volume)).perform();"},
                      {"media_player.speaker.play_on_device_media_file": {"media_file": "ready_sound", "announcement": True}}]
        # The dashboard is already drawn. Background/orb downloads continue in
        # parallel; waiting for all of them made the chime lag by about four seconds.
        # Never interrupt a voice turn or another announcement for a boot sound.
        ready_idle = {"and": [{"lambda": "return id(bg_valid) && id(boot_shown_ms) && !id(h_smoke_alarm) && !id(h_water_alarm) && !id(timer_ringing);"},
                              {"not": {"voice_assistant.is_running": {}}},
                              {"lambda": "return id(panel_media)->state != media_player::MEDIA_PLAYER_STATE_ANNOUNCING;"}]}
        ready_play.insert(0, {"lambda": "id(boot_sound_ms) = millis();"})
        ready_play = [{"if": {"condition": ready_idle, "then": ready_play,
                              "else": [{"logger.log": "Ready sound skipped: no background or audio active"}]}}]
        # The ready sound after the dashboard is drawn: by day only (Natt börjar to Natt slutar is quiet, and so is a
        # boot without Home Assistant's time) and not after a software restart (an update at night, say).
        # Home Assistant's time can come a moment after the painting: wait for it (silent without it).
        script("boot_done")["then"][0]["if"]["then"] += [
            {"wait_until": {"condition": {"lambda": "return id(ha_time).now().is_valid();"}, "timeout": "15s"}},
            {"if": {"condition": {"lambda": READY_OK},
                    # Honour the saved manual volume for the ready sound too.
                    "then": ready_play,
                    "else": [{"logger.log": {"format": "Ready sound skipped: %s", "level": "INFO", "args": [
                        "boot_was_software() ? \"software restart\" : !id(ha_time).now().is_valid() ? "
                        "\"no time from Home Assistant\" : \"night\""]}}]}}]
    if has_orb:
        # A follow-up is a new visible turn: remove the previous reply/cards while
        # preserving the conversation context on Jarvis.
        cfg["voice_assistant"]["on_listening"] += [{"lambda": "id(orb_speaking) = false;"},
                                                   {"lvgl.label.update": {"id": "you_lbl", "text": "Jag lyssnar…"}},
                                                   *fresh,
                                                   *only("orb_listen")]

    # Timers: Home Assistant's own Assist timers ("sätt en timer på tio minuter", handled by Home Assistant in
    # about a second). Configuring these triggers tells Home Assistant that the panel can show and ring
    # them; without them it refuses timers from this device. The shortest one counts down in a pill on the
    # dashboard, on Natt and on Hemmet. When one is done the chime plays every 4 s for a minute; a touch or
    # the wake word stops it.
    cfg["globals"] = cfg.get("globals", []) + [{"id": "timer_ringing", "type": "bool", "initial_value": "false"}]
    soon = [{"script.execute": "timers_soon"}]
    cfg["voice_assistant"].update({
        "on_timer_started": soon, "on_timer_updated": soon, "on_timer_cancelled": soon,
        "on_timer_tick": [{"script.execute": "timers_show"}],
        "on_timer_finished": ([{"lambda": TIMER_DONE}] if has_timer_ui else [])
                             + [{"script.execute": "timer_alarm"}]})
    cfg["script"] += [
        {"id": "timers_show", "mode": "restart", "then": [{"lambda": (QUICK_SHOW if has_quick else "") + timer_rows
                                                                      + (TIMERS_SHOW if has_timer_ui else "")}]},
        # A cancelled or finished timer is still in the list while its trigger runs; look again just after.
        {"id": "timers_soon", "mode": "restart", "then": [{"delay": "100ms"}, {"script.execute": "timers_show"}]},
        {"id": "timer_alarm", "mode": "restart", "then": [
            {"repeat": {"count": 15, "then": [chime_timer, {"delay": "4s"}]}},
            {"script.execute": "timer_stop"}]},
        {"id": "timer_stop", "mode": "restart", "then": [
            {"lambda": "id(timer_ringing) = false;"}, {"script.stop": "timer_alarm"},
            {"script.execute": "timers_show"}]}]
    cfg["touchscreen"][0]["on_touch"] = [{"logger.log": {"format": "touch x=%d y=%d", "args": ["touch.x", "touch.y"]}},
                                          stop_ringing]
    if "--voice" in flags and has_orb:
        cfg["touchscreen"][0]["on_touch"].append(
            {"if": {"condition": {"lambda": "return lv_screen_active() == id(page_orb)->obj;"},
                    "then": [{"script.execute": "orb_leave"}]}})
    return has_orb
