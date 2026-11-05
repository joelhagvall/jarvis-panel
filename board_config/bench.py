"""Measurements: frame pacing, render time and touch latency in Home Assistant, the on-demand performance test
and --bench's two-second reports."""
from .common import ROOT, scripts


def diagnostics(cfg):
    script = scripts(cfg)
    # Rendering in Home Assistant's history too (bench/bench.h's counters, read every minute): how often LVGL
    # drew and how long a frame took, the worst one included, so lag shows over days, not only in a bench run.
    cfg["esphome"]["includes"] = cfg["esphome"].get("includes", []) + [str(ROOT / "bench" / "frame_pacing.h"), str(ROOT / "bench" / "responsiveness.h"), str(ROOT / "bench" / "bench.h")]
    boot = cfg["esphome"].get("on_boot", [])
    cfg["esphome"]["on_boot"] = [{"priority": -100, "then": [{"lambda": "frame_pacing_install(); bench_install();"}]},
                                 *(boot if isinstance(boot, list) else [boot])]
    # Every page change logs its render time per draw task type (bench/page_draw.h, `tools/panel logs`).
    cfg["esphome"]["includes"].append(str(ROOT / "bench" / "page_draw.h"))
    page_names = "".join(f'if (active == id({p["id"]})->obj) return "{p["id"]}";\n' for p in cfg["lvgl"]["pages"])
    cfg["esphome"]["on_boot"].append({"priority": -96, "then": [{"lambda":
        "page_draw::install([]() -> const char * {\nauto *active = lv_screen_active();\n" + page_names +
        'return "unknown";\n});'}]})
    cfg.setdefault("interval", []).append({"interval": "1s", "then": [{"lambda": "page_draw::report();"}]})
    diag = {"entity_category": "diagnostic", "update_interval": "never", "accuracy_decimals": 1}
    cfg["sensor"] = cfg.get("sensor", []) + [
        {"platform": "template", "id": "render_rate", "name": "Ritningar per sekund", "unit_of_measurement": "/s", **diag},
        {"platform": "template", "id": "render_avg", "name": "Rendertid snitt", "unit_of_measurement": "ms", **diag},
        {"platform": "template", "id": "render_max", "name": "Rendertid max", "unit_of_measurement": "ms", **diag}]
    cfg["interval"] = cfg.get("interval", []) + [{"interval": "60s", "then": [{"lambda":
        "float rate, avg, worst;\nbench_take(rate, avg, worst);\nid(render_rate).publish_state(rate);\n"
        "id(render_avg).publish_state(avg);\nid(render_max).publish_state(worst);"}]}]
    if any(p["id"] == "page_orb" for p in cfg["lvgl"]["pages"]):
        # Exercise real pages and the animated orb on demand, without another flash or model calls.
        # Home Assistant exposes esphome.hallpanel_performance_test. Restore the original page,
        # and stop immediately if a real voice interaction starts during the visual-only run.
        cfg["globals"] += [{"id": "perf_return", "type": "lv_obj_t *", "initial_value": "nullptr"},
                           {"id": "perf_running", "type": "bool", "initial_value": "false"},
                           {"id": "perf_was_night", "type": "bool", "initial_value": "false"}]
        script("night_auto")["then"][0]["if"]["condition"]["lambda"] = (
            "if (id(perf_running)) return false;\n" + script("night_auto")["then"][0]["if"]["condition"]["lambda"])
        for interval in cfg.get("interval", []):
            if interval.get("interval") == "60s":
                for action in interval.get("then", []):
                    if "lambda" in action and "bench_take" in action["lambda"]:
                        action["lambda"] = "if (id(perf_running)) return;\n" + action["lambda"]
        cfg["api"].setdefault("actions", []).append(
            {"action": "performance_test", "then": [{"script.execute": "performance_test"}]})
        steps = [{"lambda": "id(perf_return) = lv_screen_active();\nid(perf_was_night) = id(night);\nid(perf_running) = true;\nESP_LOGI(\"bench\", \"Performance test started\");"}]
        for _ in range(3):
            for page in ("page_glass_v2", "page_hemmet", "page_tavla", "page_natt", "page_orb"):
                steps += [{"if": {"condition": {"voice_assistant.is_running": {}},
                                  "then": [{"lambda": "id(perf_running) = false;"}, {"script.stop": "performance_test"}]}},
                          {"lambda": "float r, a, w; bench_take(r, a, w);"},
                          {"lvgl.page.show": {"id": page, "animation": "NONE"}},
                          {"delay": "2s"}, {"lambda": f'bench_report("{page}");'}]
        steps += [{"lambda": "id(perf_running) = false;"},
                  {"if": {"condition": {"lambda": "return id(perf_was_night);"},
                          "then": [{"script.execute": "night_on"}],
                          "else": [{"lambda": "if (id(perf_return)) lv_screen_load(id(perf_return));"}]}},
                  {"lambda": 'ESP_LOGI("bench", "Performance test finished");'}]
        cfg["script"].append({"id": "performance_test", "mode": "single", "then": steps})


def touch(cfg, flags, has_orb):
    cfg["touchscreen"][0].setdefault("on_touch", []).insert(0, {"lambda": "bench_touch_mark();"})
    cfg["touchscreen"][0].setdefault("on_release", []).insert(0, {"lambda": "bench_touch_release();"})
    remote_touch = "bench_touch_mark(true);"
    if "--voice" in flags:
        remote_touch += "\nif (id(timer_ringing)) id(timer_stop).execute();"
        if has_orb:
            remote_touch += "\nif (lv_screen_active() == id(page_orb)->obj) id(orb_leave).execute();"
    cfg["esphome"].setdefault("on_boot", []).append({"priority": -96, "then": [{"lambda":
        "id(physical_ui).set_touch_callback([]() {\n" + remote_touch + "\n});\nid(physical_ui).set_release_callback([]() { bench_touch_release(true); });"}]})
    cfg.setdefault("interval", []).append({"interval": "60s", "then": [{"lambda": "bench_touch_report();"}]})
    if "voice_assistant" in cfg:
        cfg["globals"].append({"id": "voice_text_ready_us", "type": "uint32_t", "initial_value": "0"})
        cfg["voice_assistant"]["on_stt_end"].insert(0, {"lambda": "id(voice_text_ready_us) = micros();\n"
                                                                    "panel_first_reply.text_us = esp_timer_get_time();"})
        # panel_first_reply (bench/speaker_gaps.h) then logs when the reply's audio really left the speaker.
        cfg["media_player"][0]["on_announcement"] = [{"lambda":
            'if (id(voice_text_ready_us)) { ESP_LOGI("latency", "STT text -> player announcing %u ms", '
            '(micros() - id(voice_text_ready_us)) / 1000); id(voice_text_ready_us) = 0; }\n'
            'if (panel_first_reply.text_us && !panel_first_reply.announce_us) '
            'panel_first_reply.announce_us = esp_timer_get_time();'}]


def bench(cfg, flags):
    cfg["logger"] = {**cfg.get("logger", {}), "level": "INFO"}
    cfg["esphome"]["includes"] = cfg["esphome"].get("includes", []) + [str(ROOT / "bench" / "frame_pacing.h"), str(ROOT / "bench" / "responsiveness.h"), str(ROOT / "bench" / "bench.h")]
    boot = cfg["esphome"].get("on_boot", [])
    boot = boot if isinstance(boot, list) else [boot]
    cfg["esphome"]["on_boot"] = [{"priority": -100, "then": [{"lambda": "frame_pacing_install(); bench_install();"}]}, *boot]
    pages = [p["id"] for p in cfg["lvgl"]["pages"]]
    phase = "".join(f'if (lv_screen_active() == id({p})->obj) phase = "{p}";\n' for p in pages)
    intervals = [{"interval": "2s", "then": [{"lambda": f'const char *phase = "fade";\n{phase}bench_report(phase);'}]}]
    if any(s.get("id") == "demo" for s in cfg.get("script", [])):
        intervals.append({"interval": "20s", "then": [{"script.execute": "demo"}]})
    cfg["interval"] = cfg.get("interval", []) + intervals
    if "--sim" not in flags:
        cfg["debug"] = {"update_interval": "5s"}
        cfg["sensor"] = cfg.get("sensor", []) + [{"platform": "debug", "free": {"name": "Heap fritt"},
                                                  "psram": {"name": "PSRAM fritt"},
                                                  "loop_time": {"name": "Looptid"}}]
