"""The board itself: ESP32-P4 and its C6, display, touch, backlight, OTA, health sensors and night light."""
import json

from .common import ROOT, SYNC_SLIDER, scripts

# C++: the backlight to the day's level (Inställningar's Skärmljus).
DAY_LIGHT = """auto call = id(backlight).turn_on();
call.set_brightness(id(day_light));
call.set_transition_length(300);
call.perform();"""


def apply(cfg, flags, build_id):
    script = scripts(cfg)
    for key in ("host", "display", "touchscreen"):
        cfg.pop(key, None)
    cfg["esphome"].pop("platformio_options", None)   # simulator-only build flags
    cfg["esphome"]["name"] = "hallpanel"
    cfg["esphome"]["friendly_name"] = "Hallpanel"
    # XIP from PSRAM: the firmware runs from PSRAM, so writing the flash (OTA, saved settings) no longer stalls the
    # display, which made the screen flicker during an update. ESP-IDF asks for profiling: see docs/baseline.
    cfg["esp32"] = {"variant": "esp32p4", "flash_size": "16MB", "cpu_frequency": "360MHZ",
                    "framework": {"type": "esp-idf", "sdkconfig_options": {
                        "CONFIG_SPIRAM_XIP_FROM_PSRAM": "y",
                        # Prefer ESP-IDF's supported -O2 performance profile over the default -Os.
                        "CONFIG_COMPILER_OPTIMIZATION_SIZE": "n",
                        "CONFIG_COMPILER_OPTIMIZATION_PERF": "y"}}}
    if "--es" in flags:
        cfg["esp32"]["engineering_sample"] = True
    # sdkconfig_options here are integers/bools; ESPHome silently drops unknown
    # or non-Kconfig-compatible values (a string "DEBUG" for CONFIG_LOG_DEFAULT_LEVEL
    # did nothing on 2026-10-25). CONFIG_LWIP_MAX_SOCKETS=10 default: the API's
    # own clients, panel_debug's 3 sockets, OTA listener, mDNS and the image
    # downloads all share that pool, so a burst of one-shot connections wedges
    # the debug HTTP server with "error in accept (23)" until reboot. 16 restores
    # headroom; 2026-10-25 night session, see the panel_debug wedge note.
    cfg["esp32"]["framework"]["sdkconfig_options"]["CONFIG_LWIP_MAX_SOCKETS"] = "16"
    # Received TCP data waits in the hosted SDIO's internal DMA buffers until the reader takes it, up to the
    # receive window per connection. With IDF's 64 kB each boot image download took 60-90 kB of internal RAM;
    # two at once left 8 kB internal / 1 kB DMA and some boots aborted (2026-10-29, `tools/panel boot-heap`).
    # 16 kB still allows ~3 MB/s on the LAN.
    cfg["esp32"]["framework"]["sdkconfig_options"]["CONFIG_LWIP_TCP_WND_DEFAULT"] = "16384"
    # The hosted SDIO driver sleeps this long unconditionally after every C6 reset
    # (sdio_drv.c transport_gpio_reset; Kconfig default 1500). The card-init retry
    # loop behind it probes at 100 ms steps up to 1.5 s. The corrected 100 ms
    # experiment gave no measured boot gain, so retain the proven 600 ms wait.
    # Zero selects the upstream 1500 ms fallback instead of removing the wait.
    cfg["esp32"]["framework"]["sdkconfig_options"]["CONFIG_ESP_HOSTED_SDIO_RESET_DELAY_MS"] = "600"
    # Not CONFIG_ESP_HOSTED_SLAVE_RESET_ONLY_IF_NECESSARY: with the C6's firmware the link never came up without
    # a reset and the task watchdog rolled the OTA back (2026-10-30, docs/baseline/wifi-boot-2026-10-26.md).
    # DHCP's ARP probe of the offered address cost a fixed ~1 s before the IP on every boot. Without it the
    # IP comes 13-241 ms after association (n=6 A/B, docs/baseline/wifi-boot-2026-10-26.md); the router's
    # lease is the only conflict it could catch. CONFIG_LWIP_DHCP_DOES_ARP_CHECK: "n" is ignored, since only
    # this member of the Kconfig choice selects it.
    cfg["esp32"]["framework"]["sdkconfig_options"]["CONFIG_LWIP_DHCP_DOES_NOT_CHECK_OFFERED_IP"] = "y"
    # IDF writes and reads back all 32 MB of PSRAM before app_main on every boot, before millis() starts, so
    # no boot_*_ms shows it. A software restart took ~2.0 s from reset to millis() 0 (host ping, 2026-10-30,
    # docs/baseline/wifi-boot-2026-10-26.md#before-millis); the bootloader's share is 0.34 s.
    cfg["esp32"]["framework"]["sdkconfig_options"]["CONFIG_SPIRAM_MEMTEST"] = "n"
    # Lets EXT_RAM_BSS_ATTR put cold zero-initialised statics in PSRAM (docs/memory.md, 2026-11-03).
    cfg["esp32"]["framework"]["sdkconfig_options"]["CONFIG_SPIRAM_ALLOW_BSS_SEG_EXTERNAL_MEMORY"] = "y"
    cfg["psram"] = {"mode": "hex", "speed": "200MHz"}
    # Where setup() spends the time before the splash (boot_ui_ms): one log-ring line per phase.
    boot = cfg["esphome"].get("on_boot", [])
    cfg["esphome"]["on_boot"] = boot if isinstance(boot, list) else [boot]
    cfg["esphome"]["on_boot"].extend(
        {"priority": priority, "then": [{"lambda": f'ESP_LOGI("boot", "Phase {name} at %u ms", (unsigned) millis());'}]}
        for priority, name in ((900, "buses"), (590, "hardware"), (401, "before-lvgl"), (399, "after-lvgl-audio"), (391, "widgets-bound")))
    cfg["esp_ldo"] = [{"voltage": "2.5V", "channel": 3}]
    # Wi-Fi comes from the ESP32-C6 over SDIO.
    cfg["esp32_hosted"] = {"variant": "ESP32C6", "reset_pin": "GPIO54", "cmd_pin": "GPIO19",
                           "clk_pin": "GPIO18", "d0_pin": "GPIO14", "d1_pin": "GPIO15",
                           "d2_pin": "GPIO16", "d3_pin": "GPIO17", "active_high": True}
    cfg["wifi"] = {"ssid": "placeholder-ssid", "password": "placeholder-password", "power_save_mode": "NONE"}
    cfg["api"] = {}
    # A diagnostic API client is not Home Assistant. Keep fast connection events
    # out of the visible boot copy and retain only a one-shot timing milestone.
    if "boot_lbl" in json.dumps(cfg["lvgl"]):
        cfg["wifi"]["on_connect"] = [{"lambda": "if (!id(boot_wifi_ms)) id(boot_wifi_ms) = millis();"}]
    # Home Assistant's time on the board. A generator's host clock (glass-v2's ha_time) becomes it, keeping
    # its on_time.
    times = cfg.setdefault("time", [])
    ha_time = next((t for t in times if t.get("id") == "ha_time"), None)
    if ha_time is None:
        ha_time = {"id": "ha_time"}
        times.append(ha_time)
    ha_time["platform"] = "homeassistant"
    ha_time.pop("timezone", None)
    if script("boot_cache_refresh"):
        # Start a real local-router clock request as soon as Wi-Fi has an IP.
        # The SDK restart is queued on tcpip, never a synchronous network query.
        cfg["esp32"]["framework"]["sdkconfig_options"]["CONFIG_LWIP_SNTP_STARTUP_DELAY"] = "n"
        cfg["wifi"].setdefault("on_connect", []).append({"lambda": "esp_sntp_restart();"})
    # Jarvis publishes NASA as soon as it is available. bg_version updates the
    # panel immediately; the clock no longer adds a fixed 07:45 refresh.
    # Online images: start the boot downloads once Wi-Fi is up, not while the ESP32-C6 link still comes up.
    online_ids = {im["id"] for im in cfg.get("image", []) if im.get("platform") == "online_image"}
    for boot in (lambda b: b if isinstance(b, list) else [b])(cfg["esphome"].get("on_boot", [])):
        then = boot.get("then", [])
        if then and any(isinstance(action, dict) and action.get("component.update") in online_ids for action in then):
            then.insert(0, {"wait_until": {"condition": {"wifi.connected": {}}}})
    # id ota_main: panel.yaml adds the password from secrets.yaml to this entry (with `!extend`).
    cfg["ota"] = [{"platform": "esphome", "id": "ota_main"}]
    # glass-v2's update screen (jarvis_orb.py's page_ota) while OTA writes the flash. Writing the flash keeps the
    # display from fetching its frames, so the screen flickers: the ring and percent redraw only every 5 %.
    # Without that page the screen goes dark for the update instead.
    ota_page = next((p for p in cfg["lvgl"]["pages"] if p["id"] == "page_ota"), None)
    if ota_page:
        cfg["ota"][0].update({
            "on_begin": [{"lvgl.label.update": {"id": "ota_title", "text": "Jarvis uppdaterar sig"}},
                         {"lvgl.page.show": {"id": "page_ota", "animation": "NONE"}},
                         {"lambda": "lv_refr_now(nullptr);"}],
            "on_progress": [{"lambda": "static int shown = -1;\nint p = (int) x;\nif (p / 5 == shown) return;\n"
                                       "shown = p / 5;\nlv_arc_set_value(id(ota_arc), p);\n"
                                       "lv_label_set_text_fmt(id(ota_pct), \"%d %%\", p);\n"
                                       # OTA holds the main loop, so LVGL draws only when told to
                                       "lv_refr_now(nullptr);"}],
            "on_end": [{"lambda": "lv_arc_set_value(id(ota_arc), 100);"},
                       {"lvgl.label.update": {"id": "ota_title", "text": "Klar. Startar om …"}},
                       {"lvgl.label.update": {"id": "ota_pct", "text": "100 %"}},
                       # "Klar" for a moment, then the backlight off before the restart (the panel showed teal
                       # while it rebooted); boot turns it on again
                       {"lambda": "lv_refr_now(nullptr);\nesphome::delay(700);\nid(backlight_pwm).set_level(0.0f);"}],
            "on_error": [{"lvgl.label.update": {"id": "ota_title", "text": "Uppdateringen misslyckades"}},
                         {"delay": "4s"}, {"lvgl.page.show": {"id": "page_glass_v2", "animation": "FADE_IN"}}]})
    else:
        cfg["ota"][0].update({"on_begin": [{"lvgl.pause": {}}, {"light.turn_off": {"id": "backlight", "transition_length": "0s"}}],
                              "on_error": [{"lvgl.resume": {}}, {"light.turn_on": "backlight"}]})
    # Health in Home Assistant's history, every minute, so leaks, Wi-Fi drops and silent restarts show over days
    # (the hallpanel skill's `health` reads it back). Diagnostic entities on the panel's device page.
    diag = {"entity_category": "diagnostic"}
    cfg["debug"] = {"update_interval": "60s"}
    cfg["sensor"] = cfg.get("sensor", []) + [
        {"platform": "debug",
         "free": {"name": "Fritt internminne", **diag}, "min_free": {"name": "Lägsta internminne", **diag},
         "block": {"name": "Största minnesblock", **diag}, "loop_time": {"name": "Looptid", **diag},
         "psram": {"name": "Fritt PSRAM", **diag}},
        {"platform": "uptime", "name": "Drifttid", "update_interval": "60s", **diag},
        # Same entity as ESPHome's wifi_signal sensor, read from panel_debug's sampling task: on this
        # P4/C6 board the query is a ~30 ms RPC to the C6 and must not block the UI task every minute.
        {"platform": "template", "name": "Wi-Fi-signal", "unit_of_measurement": "dBm",
         "device_class": "signal_strength", "state_class": "measurement", "accuracy_decimals": 0,
         "update_interval": "60s", "lambda": "return id(physical_ui).radio_rssi();", **diag}]
    cfg["text_sensor"] = cfg.get("text_sensor", []) + [
        {"platform": "debug", "reset_reason": {"name": "Senaste omstart", **diag}}]
    cfg["logger"] = {"level": "INFO"}
    cfg["i2c"] = [{"id": "bus_a", "sda": "GPIO7", "scl": "GPIO8", "frequency": "400kHz"}]
    cfg["display"] = [{"platform": "mipi_dsi", "id": "panel",
                       "model": "WAVESHARE-ESP32-P4-WIFI6-TOUCH-LCD-7B",
                       "auto_clear_enabled": False, "update_interval": "never"}]
    # Two off-screen/visible buffers prevent drawing into the image being scanned.
    # Single-buffer experiments must be explicitly requested.
    direct = "--fast-display" not in flags
    double = "--double-buffer" in flags or "--direct-display" not in flags
    if direct:
        cfg["external_components"] = [{"source": {"type": "local", "path": str(ROOT / "components")},
                                       "components": ["mipi_dsi"]}]
        cfg["display"][0]["frame_buffers"] = 2 if double else 1
        cfg["esphome"].setdefault("includes", []).append(str(ROOT / "bench/direct_display.h"))
        boot = cfg["esphome"].get("on_boot", [])
        # The frame interrupt's clock paces the production orb and times refresh starts.
        attach_clock = ("\n#ifdef HALLPANEL_ORB_VIRTUAL\npacked_orb::timelines.set_clock(&direct_display::clock);\n#endif\n"
                        "responsiveness::recorder.clock = &direct_display::clock;")
        cfg["esphome"]["on_boot"] = [{"priority": -95, "then": [{"lambda": f"direct_display::install(id(panel), {str(double).lower()});" + attach_clock}]},
                                     *(boot if isinstance(boot, list) else [boot])]
        cfg.setdefault("interval", []).append({"interval": "10s", "then": [{"lambda": "direct_display::report();"}]})
    if "--fast-display" in flags:
        cfg["esphome"].setdefault("includes", []).append(str(ROOT / "bench/fast_display.h"))
        boot = cfg["esphome"].get("on_boot", [])
        cfg["esphome"]["on_boot"] = [{"priority": -95, "then": [{"lambda": "fast_display::install();"}]},
                                     *(boot if isinstance(boot, list) else [boot])]
    # runtime_image: upstream's QOI decoder painted each pixel through two virtual calls and float scaling, so
    # the 770 kB background took 2.3 s while the orb atlas came in at 1.7 MB/s (components/runtime_image/README.md).
    cfg.setdefault("external_components", []).append({
        "source": {"type": "local", "path": str(ROOT / "components")},
        "components": ["panel_debug", "panel_restart", "async_artwork", "runtime_image"]})
    cfg["panel_restart"] = {}
    # The build id is a component setting, so it lands in main.cpp only. As a global -D build flag
    # it changed every object's flags and forced a full ~2 min rebuild of all ~2000 objects per flash.
    cfg["panel_debug"] = {"id": "physical_ui", **({"build_id": build_id} if build_id else {})}
    cfg["esphome"].setdefault("build_flags", []).extend([
        "-DLV_USE_DRAW_SW_ASM=LV_DRAW_SW_ASM_CUSTOM",
        f'-DLV_DRAW_SW_ASM_CUSTOM_INCLUDE=\\"{ROOT}/components/panel_debug/pixel_copy.h\\"'])
    pages = cfg["lvgl"]["pages"]
    page_names = "\n".join(f'if (active == id({p["id"]})->obj) return {json.dumps(p["id"])};' for p in pages)
    hooks = "id(physical_ui).set_page_callback([]() -> const char * {\n" + \
            "auto *active = lv_screen_active();\n" + page_names + '\nreturn "unknown";\n});'
    if direct and double:
        hooks += "\nid(physical_ui).set_capture_callback([](uint8_t *pixels, size_t size, uint32_t &seq) {\n" \
                 "return direct_display::copy_presented(pixels, size, seq);\n});"
        hooks += "\nid(physical_ui).set_frame_callback([](const uint8_t *&pixels, uint32_t &seq, uint32_t &at) {\n" \
                 "return direct_display::presented(pixels, seq, at);\n});"
    dumps = (
        # One-shot GT911 diagnosis at boot. The component never writes a configuration,
        # so the controller runs whatever the factory config holds. Dump the control
        # registers and the full config block (0x8040-0x8100, checksum and flash flag
        # included) once after setup, read-only, so the live report rate, sensitivity
        # and version are in every boot log. The old touch poller shares this task;
        # the dump runs after gt911's own reset dance has completed.
        "uint8_t ptr[2];\nuint8_t buf[0x20];\nchar line[0x20 * 3 + 8];\n"
        "for (uint16_t reg = 0x8040; reg <= 0x8100; reg += 0x20) {\n"
        "  size_t chunk = 0x8101 - reg;\n"
        "  if (chunk > 0x20) chunk = 0x20;\n"
        "  ptr[0] = (uint8_t) (reg >> 8);\nptr[1] = (uint8_t) (reg & 0xFF);\n"
        "  auto err = touch->write_read(ptr, 2, buf, chunk);\n"
        "  if (err != i2c::ERROR_OK) { ESP_LOGW(\"gt911.dump\", \"read 0x%04X: %d\", reg, (int) err); break; }\n"
        "  for (size_t i = 0; i < chunk; i++) snprintf(line + i * 3, 4, \"%02X \", buf[i]);\n"
        "  line[3 * chunk] = 0;\n"
        "  ESP_LOGI(\"gt911.dump\", \"0x%04X: %s\", reg, line);\n"
        "}\n"
        "ESP_LOGI(\"gt911.dump\", \"done\");")
    cfg["esphome"].setdefault("on_boot", []).append({"priority": -96, "then": [{"lambda": hooks}, {"lambda": dumps}]})
    # The 7B's GT911 reports turned 180° against the panel (a tap on the tab bar at the bottom came in near
    # the top, the cog on the right came in on the left), so both axes are mirrored.
    cfg["touchscreen"] = [{"platform": "gt911", "id": "touch", "i2c_id": "bus_a", "reset_pin": "GPIO23",
                           "update_interval": "5ms",
                           "transform": {"mirror_x": True, "mirror_y": True}}]
    # The GT911 reports a new position about every 13 ms; LVGL reads every 6-7 ms, so every other read repeats
    # the last point. LVGL zeroes a gesture's distance on any read that moved less than gesture_min_velocity
    # (3 px), so those repeats restarted every swipe: on 2026-10-29 Joel's swipes switched page 30-60 ms late
    # and 2 of 4 never became a gesture (`tools/panel touches`, docs/swipe-navigation.md). 0 keeps the distance
    # across repeated reads; sliders and scroll areas do not reach a swipe handler (checked in the same doc).
    cfg["esphome"].setdefault("on_boot", []).append({"priority": -96, "then": [{"lambda":
        "for (auto *indev = lv_indev_get_next(nullptr); indev; indev = lv_indev_get_next(indev))\n"
        "  if (lv_indev_get_type(indev) == LV_INDEV_TYPE_POINTER) lv_indev_set_gesture_min_velocity(indev, 0);"}]})
    # Pre-rendered glyphs (styles/glass-v2/glyph_cache.h): each glyph unpacks from 4 bpp once, not on every draw.
    if cfg.get("font"):
        cfg["esphome"].setdefault("includes", []).append(str(ROOT / "styles/glass-v2/glyph_cache.h"))
        # Whole labels as ready masks (styles/glass-v2/label_cache.h, proved by tools/check_label_cache.py).
        cfg["esphome"]["includes"].append(str(ROOT / "styles/glass-v2/label_cache.h"))
        cfg["esphome"]["on_boot"].append({"priority": -96, "then": [{"lambda": "label_cache::install();"}]})
        cfg["esphome"]["on_boot"].append({"priority": 600, "then": [{"lambda": "".join(
            f"glyph_cache::install(id({f['id']})->get_lv_font());\n" for f in cfg["font"])}]})
    cfg["output"] = [{"platform": "ledc", "id": "backlight_pwm", "pin": "GPIO32",
                      "inverted": True, "frequency": "1000Hz"}]
    # gamma_correct 1: with ESPHome's default 2.8, Nattljus 12 % drove the backlight at 0.3 % (a black screen).
    cfg["light"] = [{"platform": "monochromatic", "id": "backlight", "name": "Skärmljus",
                     "output": "backlight_pwm", "restore_mode": "ALWAYS_OFF", "gamma_correct": 1.0}]
    # ESPHome buckets percentages into 1, 1/2, 1/4 or 1/8 and tries internal RAM
    # for sizes <=1/4. Select 1/2 explicitly so the auxiliary buffer uses PSRAM.
    # This retained buffer serves auxiliary paths; normal drawing uses hardware buffers.
    cfg["lvgl"]["buffer_size"] = "50%"
    cfg["lvgl"]["byte_order"] = "little_endian"
    # Night mode (glass-v2): the screen down to Nattljus (12 % at first, set in Home Assistant and kept on the
    # panel), and back up to full when it ends.
    if script("night_hw_on"):
        cfg["number"] = cfg.get("number", []) + [{
            "platform": "template", "id": "night_light", "name": "Nattljus", "optimistic": True,
            "restore_value": True, "initial_value": 12, "min_value": 1, "max_value": 100, "step": 1,
            "unit_of_measurement": "%", "mode": "slider", "entity_category": "config", "icon": "mdi:brightness-4"}]
        script("night_hw_on")["then"].append({"lambda": "auto call = id(backlight).turn_on();\n"
                                                        "call.set_brightness(id(night_light).state / 100.0f);\n"
                                                        "call.set_transition_length(1000);\ncall.perform();"})
        script("night_hw_off")["then"].append({"lambda": DAY_LIGHT})
    if script("bright_set"):
        # Inställningar (glass-v2). Skärmljus is the day's level: kept on the panel (day_light), set again at
        # boot and after night mode, and followed when Home Assistant changes the light by day. Not at night
        # and not during an alarm (full brightness), and not before boot has set it.
        cfg["globals"] = cfg.get("globals", []) + [
            {"id": "day_light", "type": "float", "restore_value": True, "initial_value": "1.0"},
            {"id": "light_ready", "type": "bool", "initial_value": "false"}]
        cfg["light"][0]["on_state"] = [{"lambda": "auto &v = id(backlight).remote_values;\n"
                                                  "if (id(light_ready) && v.is_on() && !id(night) && "
                                                  "!id(h_smoke_alarm) && !id(h_water_alarm))\n"
                                                  "  id(day_light) = v.get_brightness();"}]
        boot = cfg["esphome"].get("on_boot", [])
        # LVGL is priority 400 and creates its widgets at 395. Draw the compiled
        # splash before Wi-Fi setup (250). LightCall only schedules a write; its
        # ordinary loop cannot run during blocking Wi-Fi setup. Write the restored
        # brightness now, after the complete frame, without a deferred transition.
        first_frame = 'lv_refr_now(nullptr);\nid(boot_ui_ms) = millis();\n' if script('boot_done') else ''
        cfg["esphome"]["on_boot"] = [*(boot if isinstance(boot, list) else [boot]),
                                     {"priority": 390, "then": [{"lambda": first_frame +
                                      DAY_LIGHT.replace('set_transition_length(300)', 'set_transition_length(0)') +
                                      "\nid(backlight).get_output()->write_state(id(backlight));\n" +
                                      ("id(boot_light_ms) = millis();\n"
                                       "lv_display_add_event_cb(lv_display_get_default(), [](lv_event_t *) {\n"
                                       "  if (id(boot_shown) && !id(boot_shown_ms) &&\n"
                                       "      lv_obj_has_flag(id(boot_cover), LV_OBJ_FLAG_HIDDEN))\n"
                                       "    id(boot_shown_ms) = millis();\n"
                                       "}, LV_EVENT_REFR_READY, nullptr);\n" if script("boot_done") else "") +
                                      "id(light_ready) = true;"}]}]
        script("alarm_wake")["then"].insert(0, {"lambda": "auto call = id(backlight).turn_on();\n"
                                                          "call.set_brightness(1.0f);\ncall.perform();"})
        # Follow the finger locally without a fade or a stream of Home Assistant/flash updates.
        # Only releasing the slider commits the chosen day level.
        script("bright_preview")["then"] = [{"lambda": "auto call = id(backlight).turn_on();\n"
                                                       "call.set_brightness(id(bright_pct) / 100.0f);\n"
                                                       "call.set_transition_length(0);\ncall.set_publish(false);\n"
                                                       "call.set_save(false);\ncall.perform();"}]
        script("bright_set")["then"] = [{"lambda": "id(day_light) = id(bright_pct) / 100.0f;\n"
                                                   + DAY_LIGHT.replace("set_transition_length(300)",
                                                                       "set_transition_length(0)")}]
        # Nattljus: the screen shows the level while the slider moves (not kept, not sent to Home Assistant),
        # and goes back to the day's when it is let go.
        script("nightl_preview")["then"] = [{"lambda": "auto call = id(backlight).turn_on();\n"
                                                       "call.set_brightness(id(nightl_pct) / 100.0f);\n"
                                                       "call.set_transition_length(0);\ncall.set_publish(false);\n"
                                                       "call.set_save(false);\ncall.perform();"}]
        script("nightl_set")["then"] = [{"lambda": "id(night_light).make_call().set_value(id(nightl_pct)).perform();\n"
                                                   + DAY_LIGHT}]
        script("settings_open")["then"].insert(0, {"lambda": SYNC_SLIDER.format(key="bright", value="id(day_light) * 100")
                                                   + SYNC_SLIDER.format(key="nightl", value="id(night_light).state")})
    # Lost Home Assistant for 30 s (Wi-Fi, Home Assistant restarting, Jarvis down): the pill says so.
    if script("net_show"):
        cfg.setdefault("globals", []).append({"id": "ha_away_s", "type": "int", "initial_value": "0"})
        cfg.setdefault("interval", []).append({"interval": "5s", "then": [{"if": {
            "condition": {"api.connected": {}},
            "then": [{"lambda": "id(ha_away_s) = 0;"},
                     {"if": {"condition": {"lambda": "return id(ha_lost);"},
                             "then": [{"lambda": "id(ha_lost) = false;"}, {"script.execute": "net_show"}]}}],
            "else": [{"lambda": "id(ha_away_s) += 5;"},
                     {"if": {"condition": {"lambda": "return id(ha_away_s) >= 30 && !id(ha_lost);"},
                             "then": [{"lambda": "id(ha_lost) = true;"}, {"script.execute": "net_show"}]}}]}}]})
