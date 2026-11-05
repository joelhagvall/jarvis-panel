"""Tavla's NASA extras (Joel, 2026-10-29): a tap on the plaque opens NASA's English explanation of the picture,
and a Jorden button (top right) swaps the picture for EPIC's newest whole Earth with today's closest asteroids.
Anywhere else a tap still goes back and swipes still change tab; leaving Tavla puts the picture back and closes
the text. server/earth.py makes the Earth picture and earth.json; /panel's art_text, earth_* and neo* fill this.

Memory (checked 2026-10-29: 10.7 MB PSRAM free, lowest 7.1 MB): the Earth picture is 480x480 RGB565, 0.46 MB in
PSRAM once downloaded, with the backgrounds' 8 KiB read buffer. It is never part of the boot chain: it is fetched
EARTH_DELAY after the backgrounds are in and then only when earth_version changes (once a day), or at once when
the Jorden button is tapped before it is in. One download at a time (earth_busy).
"""

EARTH_SIDE = 480                                   # server/earth.py's EARTH_SIDE
EARTH_XY = ((1024 - EARTH_SIDE) // 2, (600 - EARTH_SIDE) // 2)
EARTH_DELAY = "20s"                                # after the backgrounds, so two downloads never overlap at boot
TEXT_W = 560                                       # the plaque's widest line, as its title's max_width


def card(ui, wid, align, x, widgets, **kw):
    """A plaque-style card: black at 45 %, rounded, its lines in a column."""
    return ui.plain(id=wid, x=x, y=-28, align=align, width="SIZE_CONTENT", height="SIZE_CONTENT", radius=16,
                    bg_color="0x000000", bg_opa="45%", pad_top=10, pad_bottom=11, pad_left=16, pad_right=18,
                    layout={"type": "flex", "flex_flow": "COLUMN", "flex_align_main": "START",
                            "flex_align_cross": "START", "flex_align_track": "START", "pad_row": 2},
                    widgets=widgets, **kw)


def plaque_tap(ui):
    """The plaque: open or close the explanation; without one, go back like the rest of the page."""
    return [{"if": {"condition": {"lambda": "return lv_label_get_text(id(t_text))[0] != 0;"},
                    "then": [{"lambda": "if (lv_obj_has_flag(id(t_text), LV_OBJ_FLAG_HIDDEN)) "
                                        "lv_obj_remove_flag(id(t_text), LV_OBJ_FLAG_HIDDEN);\n"
                                        "else lv_obj_add_flag(id(t_text), LV_OBJ_FLAG_HIDDEN);"}],
                    "else": ui.bar_back("FADE_IN", "300ms")}}]


def explanation(ui):
    """The explanation under the plaque's credit, hidden until the plaque is tapped."""
    return ui.label("", ui.f(500, 16), id="t_text", width=TEXT_W, text_opa="88%", pad_top=8, text_line_space=3,
                    hidden=True)


def widgets(ui):
    """The Earth layer and the Jorden button, over the picture, its plaque and the page's back tap."""
    earth = ui.plain(id="t_earth_layer", x=0, y=0, width=1024, height=600, hidden=True, clickable=False, widgets=[
        {"image": {"id": "t_earth_img", "src": "earth", "x": EARTH_XY[0], "y": EARTH_XY[1], "clickable": False}},
        card(ui, "t_eplaque", "BOTTOM_LEFT", 28, [
            ui.label(ui.sample("Jorden 26 oktober"), ui.f(600, 19), id="t_etitle"),
            ui.label(ui.sample("NASA EPIC · kl 10:50 · 1,5 miljoner km bort"), ui.f(500, 14), sec=True,
                     id="t_eline")], clickable=False),
        card(ui, "t_neo", "BOTTOM_RIGHT", -28, [
            ui.label(ui.sample("6 asteroider passerar i dag"), ui.f(600, 19), id="t_neo_head"),
            *[ui.label(ui.sample(row), ui.f(500, 14), sec=True, id=f"t_neo{i}")
              for i, row in enumerate(("2016 XB2 · 68 månavstånd · ~210 m", "2017 UL44 · 91 månavstånd · ~75 m",
                                       "2020 CB · 111 månavstånd · ~12 m"))]], clickable=False)])
    button = {"button": {"id": "t_switch", "align": "TOP_RIGHT", "x": -28, "y": 28, "width": "SIZE_CONTENT",
                         "height": 36, "radius": 18, "bg_color": "0x000000", "bg_opa": "45%", "border_width": 0,
                         "shadow_opa": "TRANSP", "pad_left": 16, "pad_right": 16, "pad_top": 0, "pad_bottom": 0,
                         "hidden": True,
                         "widgets": [ui.label("Jorden", ui.f(600, 15), id="t_switch_lbl", align="CENTER")],
                         "on_click": [{"lambda": "id(tavla_earth) = !id(tavla_earth);"},
                                      {"script.execute": "tavla_view"}, {"script.execute": "earth_now"}]}}
    return [earth, button]


VIEW = """const bool e = id(tavla_earth);
for (lv_obj_t *o : {id(tavla_art), id(t_plaque)}) {
  if (e) lv_obj_add_flag(o, LV_OBJ_FLAG_HIDDEN); else if (o != id(t_plaque) || lv_label_get_text(id(t_title))[0])
    lv_obj_remove_flag(o, LV_OBJ_FLAG_HIDDEN);
}
if (e) { lv_obj_remove_flag(id(t_earth_layer), LV_OBJ_FLAG_HIDDEN); lv_obj_add_flag(id(t_text), LV_OBJ_FLAG_HIDDEN); }
else lv_obj_add_flag(id(t_earth_layer), LV_OBJ_FLAG_HIDDEN);
lv_label_set_text(id(t_switch_lbl), e ? "Dagens bild" : "Jorden");
ESP_LOGI("tavla", "Tavla: %s", e ? "Jorden" : "dagens bild");"""

# Tavla always opens on the day's picture with the text closed.
LEAVE = """lv_obj_add_event_cb(id(page_tavla)->obj, [](lv_event_t *) {
  lv_obj_add_flag(id(t_text), LV_OBJ_FLAG_HIDDEN);
  if (!id(tavla_earth)) return;
  id(tavla_earth) = false;
  id(tavla_view).execute();
}, LV_EVENT_SCREEN_UNLOADED, nullptr);"""


def sensors(ui):
    """sensor.jarvis_hemmet's attributes for the plaque text and the Earth view."""
    rows = [ui.hemmet(f"neo{i}", ui.text(f"t_neo{i}", "") + ui.shown(f"t_neo{i}", "!x.empty()")) for i in range(3)]
    return [ui.hemmet("art_text", ui.text("t_text", "") + "if (x.empty()) lv_obj_add_flag(id(t_text), LV_OBJ_FLAG_HIDDEN);\n"),
            ui.hemmet("earth_title", ui.text("t_etitle", "Jorden")),
            ui.hemmet("earth_line", ui.text("t_eline", "")),
            ui.hemmet("neo_head", ui.text("t_neo_head", "") + ui.shown("t_neo", "!x.empty()")),
            *rows,
            ui.hemmet("earth_version", ui.shown("t_switch", "!x.empty()") + "id(earth_want) = x;\n",
                      [{"script.execute": "earth_fetch"}])]


def install(cfg):
    """The simulator's placeholder picture, the view's state and scripts, and the leave hook."""
    cfg["image"].append({"platform": "file", "id": "earth", "file": "assets/mic_small.png", "type": "RGB565"})
    cfg.setdefault("globals", []).extend([
        {"id": "tavla_earth", "type": "bool", "initial_value": "false"},
        {"id": "earth_want", "type": "std::string", "initial_value": '""'}])
    cfg.setdefault("script", []).extend([
        {"id": "tavla_view", "mode": "restart", "then": [{"lambda": VIEW}]},
        # The simulator has the picture compiled in: nothing to fetch. remote() gives these their downloads.
        {"id": "earth_fetch", "mode": "single", "then": []},
        {"id": "earth_now", "mode": "single", "then": []}])
    boot = cfg["esphome"].get("on_boot", [])   # added to, never replaced (gen.py's bar_hook lives there)
    cfg["esphome"]["on_boot"] = [*(boot if isinstance(boot, list) else [boot]),
                                 {"priority": -100, "then": [{"lambda": LEAVE}]}]


def remote(cfg, base):
    """The board's Earth picture: a QOI download from Home Assistant, like the backgrounds, outside the boot chain."""
    cfg["image"] = [im for im in cfg["image"] if im["id"] != "earth"]
    cfg["globals"].extend([{"id": "earth_loaded", "type": "std::string", "initial_value": '""'},
                           {"id": "earth_busy", "type": "bool", "initial_value": "false"}])
    start = [{"lambda": "id(earth_busy) = true;\nESP_LOGI(\"tavla\", \"Earth picture: downloading %s\", "
                        "id(earth_want).c_str());"},
             {"component.update": "earth"}]
    stale = "return !id(earth_busy) && !id(earth_want).empty() && id(earth_want) != id(earth_loaded);"
    for script in cfg["script"]:
        if script["id"] == "earth_fetch":     # a new picture on Jarvis: after the backgrounds, never during boot
            script["then"] = [{"wait_until": {"condition": {"lambda": "return id(assets_loaded);"}}},
                              {"delay": EARTH_DELAY},
                              {"if": {"condition": {"lambda": stale}, "then": start}}]
        elif script["id"] == "earth_now":     # the Jorden button before the picture is in
            script["then"] = [{"if": {"condition": {"lambda": "return id(tavla_earth) && id(assets_loaded) && " + stale[7:]},
                                      "then": start}}]
    cfg["image"].append({
        "platform": "online_image", "id": "earth", "url": f"{base}/earth.qoi", "format": "QOI",
        "buffer_size": 8192, "type": "RGB565", "transparency": "opaque",
        "on_download_finished": [
            {"lambda": "id(earth_busy) = false; id(earth_loaded) = id(earth_want);\n"
                       "ESP_LOGI(\"tavla\", \"Earth picture: in\");"},
            {"lvgl.image.update": {"id": "t_earth_img", "src": "blank"}},
            {"lvgl.image.update": {"id": "t_earth_img", "src": "earth"}}],
        "on_error": [{"lambda": "id(earth_busy) = false;"},
                     {"logger.log": {"format": "Could not download the Earth picture", "level": "WARN"}}]})
