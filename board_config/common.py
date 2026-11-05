from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def scripts(cfg):
    """script(sid): the generator's script with that id, or None."""
    return lambda sid: next((s for s in cfg.get("script", []) if s["id"] == sid), None)


# C++: one of Inställningar's sliders and its value to what the panel has now.
SYNC_SLIDER = """{{
  int v = (int) roundf({value});
  id({key}_pct) = v;
  lv_slider_set_value(id(s_{key}), v, LV_ANIM_OFF);
  lv_label_set_text(id(s_{key}_val), (std::to_string(v) + " %").c_str());
}}
"""
