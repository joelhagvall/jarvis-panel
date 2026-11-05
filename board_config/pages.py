"""Page changes on the board: the packed orb's paced starts and immediate (unanimated) navigation."""


def apply(cfg):
    if any(str(inc).endswith("packed_orb.h") for inc in cfg["esphome"].get("includes", [])):
        # The packed orb paces its production images per display frame; start a state through it.
        def paced_starts(value):
            if isinstance(value, dict):
                for key, action in list(value.items()):
                    if key == "lvgl.animimg.start" and isinstance(action, str) and action.startswith("orb_"):
                        del value[key]
                        value["lambda"] = f"packed_orb::restart(id({action}));"
                    else:
                        paced_starts(action)
            elif isinstance(value, list):
                for item in value:
                    paced_starts(item)
        paced_starts(cfg)
        if any(m.get("id") == "mics" and m.get("platform") == "panel_audio" for m in cfg.get("microphone", [])):
            # The speaking wave follows Jarvis's voice as the microphone next to the speaker hears it.
            cfg["esphome"].setdefault("on_boot", []).append({"priority": -100, "then": [{"lambda":
                "packed_orb::voice_source = []() { return id(mics)->level(); };"}]})
    # Full-screen alpha fades stalled the main loop for 218–256 ms on the 7B. Keep navigation
    # immediate, including swipes, night mode and voice; audio still uses its own transitions.
    def immediate_pages(value):
        if isinstance(value, dict):
            for key, action in value.items():
                if key == "lvgl.page.show" and isinstance(action, dict):
                    action["animation"] = "NONE"
                    action.pop("time", None)
                else:
                    immediate_pages(action)
        elif isinstance(value, list):
            for item in value:
                immediate_pages(item)
    immediate_pages(cfg)
