"""Liquid glass v2 (iOS 26 style) for the hallway panel, ESPHome + LVGL.

bake.py draws every glass shape into the background PNG: refraction at the rim,
light blur, tint, specular rim, inner shade and drop shadow, with continuous
(squircle) corners. LVGL draws only the content on top: text, icons, chat
bubbles and the selected-tab pill. Moving a shape means changing RECTS and
baking again.

Pages: the dashboard ("Gå hemifrån"), Hemmet (the home status from Home Assistant's sensor.jarvis_hemmet,
on the same baked glass), Tavla (the day's picture alone, without glass, with a small plaque), Natt (a black
page with a dim clock) and the voice page with the orb. The tab bar switches between them, and its cog opens
Inställningar, a sheet over every page: Skärmljus, Nattljus, Volym, Mikrofon, Bakgrund and Ljud ut.

Live parts, through Home Assistant's API (`api:` works in the simulator too, it just never gets data there):
  sensor.jarvis_hemmet   the Hemmet page, the alarm wake-up, the offline pill (Jarvis's internet) and
                         Tavla's plaque (art_title, art_credit: NASA's picture of the day)
  sensor.jarvis_chat     the two chat bubbles (last_said, last_reply); Rensa calls jarvis_agent.clear
  ha_time                the clock and date (the Mac's clock in the simulator; board.py makes it Home
                         Assistant's), night mode on and off at the times below
  Natt börjar, Natt slutar  two time entities on the panel (22:00 and 06:00 at first; demonstration values only),
                         set in Home Assistant and kept on the panel, so they hold when Home Assistant is down;
                         Jarvis's quiet hours (server/notify.py) read the same two
  select.jarvis_bakgrund the sheet's Bakgrund row: its name, and ‹ › step through the choices in Home Assistant
  select.jarvis_ljud_ut  the sheet's Ljud ut row: Panelen or a Bluetooth speaker on Jarvis, ‹ › connect the next
Night mode: the Natt tab, Natt börjar, or two minutes without a touch between the two times show the Natt page;
a tap anywhere leaves it, and Natt slutar ends it. The scripts night_hw_on/night_hw_off are empty here: board.py
puts the backlight and the speaker volume in them. The same goes for the sheet's bright_set, nightl_preview,
nightl_set and vol_set: here they only log, board.py makes them change the screen and the speaker.

The simulator shows example values. board.py sets HALLPANEL_BOARD=1, and then every live label starts
empty or "–" until Home Assistant has sent the real value: the panel must never show "Ingen rök" it has
not read.

Usage: python3 gen.py [assets/bg_starry.png] [--demo] [--orb] [--page hemmet|natt|tavla|installningar] > glass-v2.yaml
  --demo  play the voice flow once from boot (dashboard -> orb -> dashboard), for recording
  --orb   start on the orb page with the whole reply showing, for a still
  --page  start on the Hemmet, Natt or Tavla page, or the dashboard with Inställningar open, for a still
"""
import json
from pathlib import Path
import os
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))   # tavla_earth.py and now_playing.py live beside it
import weather_page  # noqa: E402
import calendar_page  # noqa: E402
import reminders_page  # noqa: E402
import ai_page  # noqa: E402

WHITE, INK = "0xFFFFFF", "0x0B1220"
GREEN, ORANGE, RED, BLUE = "0x30D158", "0xFF9F0A", "0xFF453A", "0x0A84FF"
CYAN, PURPLE = "0x64D2FF", "0xAF52DE"
WARM = "0xFFD9B0"  # the night clock: warm white, kind to dark-adapted eyes
SEC = "60%"  # secondary text: white at 60 %
DASH, HOME, NIGHT, ORB, FRAME = "page_glass_v2", "page_hemmet", "page_natt", "page_orb", "page_tavla"
MUSIC = "page_musik"   # the Spelas nu tab: what plays, full screen, with the spinning record (now_playing.py)
BOARD = os.environ.get("HALLPANEL_BOARD") == "1"
HEMMET, CHAT = "sensor.jarvis_hemmet", "sensor.jarvis_chat"
EARLIER = 6        # the chat's bubbles before the newest question (jarvis_agent/chat_view.py EARLIER)
BAKGRUND = "select.jarvis_bakgrund"
LJUD_UT = "select.jarvis_ljud_ut"     # Panelen or a Bluetooth speaker on Jarvis (docs/bluetooth-speaker.md)
WAKEUP = "sensor.jarvis_nasta_vackning"      # its panel attribute: Natt's "Väckning 08:45" (docs/wakeups.md)
WAKING = "binary_sensor.jarvis_vackning_pagar"   # on while Snooze and Stopp mean something
QUICK_TIMERS = (5, 10, 15)                   # Hemmet's quick timers (jarvis_agent.timer_start)


def _ui():
    """This module's helpers for tavla_earth.py and weather_page.py, also when a test loaded gen.py under another
    name; called at module level too, so only what is defined above the call is in it."""
    from types import SimpleNamespace
    return SimpleNamespace(**globals())


def sample(example, placeholder=""):
    """The example in the simulator, the placeholder on the board until Home Assistant sends the value."""
    return placeholder if BOARD else example

# name: (x, y, width, height, shape, tint). Shared with bake.py.
# Grid: 4 columns of 230 px with 16 px gutters, 28 px side margins, 2 rows from y 24.
RECTS = {
    "weather": (520, 24, 230, 230, "squircle", "dark"),
    "calendar": (766, 24, 230, 230, "squircle", "dark"),
    "sl": (28, 270, 476, 230, "squircle", "dark"),
    "jarvis": (520, 270, 476, 230, "squircle", "dark"),
    "tabs": (246, 516, 460, 60, "capsule", "dark"),
    "mic": (718, 516, 60, 60, "capsule", "dark"),
}
CLOCK_AREA = (28, 24, 476, 230)  # no glass, the bake darkens it softly

# The chat bubbles and reminders show any text Jarvis or Joel says, so a few more letters and quotes.
GLYPHS = [chr(c) for c in range(0x20, 0x7F)] + list("åäöÅÄÖ°µ·–³…→éèüÜæøÆØ’‘”“—€")
# Tavla's plaque shows NASA's title and credit, which name people from all over Europe ("Večerník").
# Its two fonts also get Latin-1 and Latin Extended-A (Inter has no deprecated U+0149, nor is the soft
# hyphen drawn); the server folds anything else
# (server/agents/background.py plaque_text, which keeps the same set).
PLAQUE_FONTS = {(600, 19), (500, 14), (500, 16)}   # 16: NASA's explanation under the plaque (tavla_earth.py)
# Spelas nu's fonts (now_playing.py) have the plaque's characters too: "Rosalía" showed a box (2026-10-27).
# Home Assistant folds what is still missing (jarvis_agent/glyphs.py; tests keep the sets equal).
MUSIC_FONTS = {(500, 13), (600, 16), (500, 16), (700, 34), (600, 22)}   # 34, 22: Spelas nu; (700, 22) went with the sheet
PLAQUE_GLYPHS = sorted(set(GLYPHS) | {chr(c) for c in range(0xA1, 0x180) if c not in (0xAD, 0x149)})
FONTS = [(400, 56), (500, 13), (500, 14), (500, 15), (500, 16), (500, 21), (600, 14), (600, 15), (600, 16),
         (600, 19), (600, 22), (600, 30), (700, 13), (700, 22), (700, 34)]
# Material Design Icons for the tab bar's cog and Inställningar (the same font as Jarvis's cards).
MDI = "https://cdn.jsdelivr.net/npm/@mdi/font@7.4.47/fonts/materialdesignicons-webfont.ttf"
ICONS = {k: chr(int(v, 16)) for k, v in (("cog", "F0493"), ("sun", "F00DF"), ("moon", "F0594"),
                                          ("volume", "F057E"), ("mic", "F036C"), ("image", "F02E9"),
                                          ("prev", "F0141"), ("next", "F0142"), ("speaker", "F04C3"),
                                          ("down", "F0140"), ("volume_low", "F057F"),
                                          ("reconnect", "F0450"), ("alarm", "F0020"),
                                          # the calendar's event card (calendar_page.py)
                                          ("bell", "F009C"), ("place", "F07D9"), ("repeat", "F0456"),
                                          ("notes", "F11D7"), ("link", "F0339"))}


def f(w, s):
    return f"i{w}_{s}"


def label(text, font, color=WHITE, sec=False, **kw):
    d = {"text": text, "text_font": font, "text_color": color}
    if sec:
        d["text_opa"] = SEC
    d.update(kw)
    return {"label": d}


def plain(**kw):
    d = {"bg_opa": "TRANSP", "border_width": 0, "pad_all": 0, "scrollable": False}
    d.update(kw)
    return {"obj": d}


def shape(color, w, h, r, **kw):
    d = {"width": w, "height": h, "radius": r, "bg_color": color, "bg_opa": "COVER", "border_width": 0,
         "pad_all": 0, "scrollable": False}
    d.update(kw)
    return {"obj": d}


def widget(key, widgets, pad=16):
    """Content layer over a baked glass shape. LVGL draws no background or border here."""
    x, y, w, h = RECTS[key][:4]
    return plain(x=x, y=y, width=w, height=h, pad_all=pad, widgets=widgets)


def dep(i, line, dest, note, mins, state):
    """Departure row i (`sl<i>_row`, `_line`, `_dest`, `_note`, `_min`), filled by the metro sensor. On the
    board it starts hidden: no train is shown that SL has not reported."""
    go = state == "go"
    row = {"id": f"sl{i}_row", "x": 0, "y": 26 + i * 43, "width": 444, "height": 40, "radius": 20,
           "border_width": 0, "pad_all": 0, "scrollable": False, "bg_color": WHITE,
           "bg_opa": "14%" if go else "TRANSP", "hidden": BOARD,
           "widgets": [
               shape(BLUE, 28, 28, 14, x=6, y=6, widgets=[label(line, f(700, 13), align="CENTER", id=f"sl{i}_line")]),
               label(dest, f(600, 16), x=44, y=2, width=300, long_mode="DOT", id=f"sl{i}_dest"),
               label(note, f(500, 13), GREEN if go else WHITE, sec=not go, x=44, y=21, id=f"sl{i}_note"),
               label("min", f(500, 13), sec=True, align="RIGHT_MID", x=-12, y=3),
               # a fixed, right-aligned box, so "2" and "12" both end next to "min"
               label(mins, f(700, 22), GREEN if go else WHITE, align="RIGHT_MID", x=-42, y=0, width=60,
                     text_align="RIGHT", id=f"sl{i}_min"),
           ]}
    if state == "missed":
        row["opa"] = "45%"
    return {"obj": row}


def head(text, icon=None, color=WHITE, **kw):
    """A card's title, with a small white icon before it."""
    if not icon:
        return [label(text, f(600, 15), color, x=0, y=0, **kw)]
    return [{"image": {"src": icon, "x": 0, "y": 0}}, label(text, f(600, 15), color, x=24, y=0, **kw)]


def event(i, title, time, color):
    """Calendar slot i: a coloured bar (`cal<i>_bar`), the title and the time; filled by the calendar sensor."""
    y = 26 + i * 56
    return [shape(color, 4, 40, 2, x=0, y=y, id=f"cal{i}_bar", hidden=BOARD),
            # DOT needs a fixed height: SIZE_CONTENT otherwise grows to a second
            # line and paints over the independent time label below it.
            label(sample(title), f(600, 16), x=12, y=y - 2, width=186, height=20,
                  long_mode="DOT", id=f"cal{i}_t"),
            label(sample(time), f(500, 14), sec=True, x=12, y=y + 19, width=186, height=18,
                  long_mode="DOT", id=f"cal{i}_time")]


def bubble(text, you, width=None, key=None):
    """One chat bubble. With key, the row is `<key>_row` and the text `<key>_txt`, so the chat sensor can
    fill them; an empty text hides the row."""
    lbl = label(text, f(500, 16), WHITE, clickable=False)
    if width:
        lbl["label"]["width"] = width
    else:
        lbl["label"]["max_width"] = 300
    bub = {"obj": {"width": "SIZE_CONTENT", "height": "SIZE_CONTENT", "radius": 18,
                   "border_width": 1, "border_color": WHITE, "border_opa": "18%",
                   "bg_color": BLUE if you else "0x1C1C1E", "bg_opa": "COVER" if you else "92%",
                   "pad_top": 9, "pad_bottom": 9, "pad_left": 14, "pad_right": 14, "scrollable": False, "clickable": False,
                   "widgets": [lbl]}}
    row = plain(width="100%", height="SIZE_CONTENT", clickable=False,
                layout={"type": "flex", "flex_flow": "ROW", "flex_align_main": "END" if you else "START",
                        "flex_align_cross": "CENTER", "flex_align_track": "CENTER"},
                widgets=[bub])
    if key:
        lbl["label"]["id"] = f"{key}_txt"
        row["obj"].update(id=f"{key}_row", hidden=not text)
    return row


TABS = ["Gå hemifrån", "Hemmet", "Spelas nu", "Tavla", "Natt"]   # in SWIPE_ORDER's order (tab_go)
TAB_ACTIONS = [[{"lvgl.page.show": {"id": DASH, "animation": "FADE_IN", "time": "250ms"}}],
               [{"lvgl.page.show": {"id": HOME, "animation": "FADE_IN", "time": "250ms"}}],
               [{"lvgl.page.show": {"id": MUSIC, "animation": "FADE_IN", "time": "250ms"}}],
               [{"lvgl.page.show": {"id": FRAME, "animation": "FADE_IN", "time": "400ms"}}],
               [{"script.execute": "night_on"}]]


# Five tabs and the cog share the bar's 448 px (the baked glass is unchanged): each tab is its label (Inter 600
# 15 px: 92, 64, 74, 42, 30 px) and about 20 px around it, so "Gå hemifrån" fits where five equal tabs would not.
# "Spelas nu", not "Musik": the page shows radio too (Joel, 2026-10-28).
TAB_WIDTHS = [112, 84, 94, 61, 49]
TAB_X = [sum(TAB_WIDTHS[:i]) for i in range(len(TAB_WIDTHS))]
TAB_W = sum(TAB_WIDTHS) // len(TAB_WIDTHS)   # the average, for tools/check_tab_gesture.py's model of the bar


BAR_PAGES = [DASH, HOME]           # the pages with the tab bar (the top layer's `bar`)
SWIPE_ORDER = [DASH, HOME, MUSIC, FRAME, NIGHT]


def bar_back(anim, ms):
    """Back to the page with the bar he came from (Hemmet or Gå hemifrån), from Musik, Tavla or Natt."""
    return [{"if": {"condition": {"lambda": f"return id(bar_page) == {i};"},
                    "then": [{"lvgl.page.show": {"id": pid, "animation": anim, "time": ms}}]}}
            for i, pid in enumerate(BAR_PAGES)]


def go(src, dst, move):
    """From one tab's page to another. Natt is entered through night_on and left by hand (backlight, volume)."""
    if dst == NIGHT:
        return [{"script.execute": "night_on"}]
    leave = ([{"if": {"condition": {"lambda": "return id(night);"},
                      "then": [{"lambda": "id(night) = false;"}, {"script.execute": "night_hw_off"}]}}]
             if src == NIGHT else [])
    return leave + [{"lvgl.page.show": {"id": dst, "animation": move, "time": "250ms"}}]


def first_match(branches):
    """Choose one route even when page.show synchronously changes cur_page."""
    result = []
    for branch in reversed(branches):
        condition = dict(branch["if"])
        if result:
            condition["else"] = result
        result = [{"if": condition}]
    return result


def shared_bar(cfg):
    """One tab bar for Gå hemifrån and Hemmet, on the top layer, so it stands still while the pages slide under
    it. Its glass is cut out of the background picture on the panel (bar_cut.h, after every new bg), and it
    hides on the pages without a bar. Swipes left and right move along the tabs, on a page or on the bar (a
    swipe that starts on a button is not a tap)."""
    # the page on its way in (BAR_ON_LOAD's cur_page): during a 600 ms fade to Natt the screen is still Tavla,
    # and a quick swipe went on from Tavla with night mode left on (2026-10-23)
    on = lambda pid: {"lambda": f"return id(cur_page) == {SWIPE_ORDER.index(pid)};"}
    swipe = {"left": [], "right": []}
    for i, src in enumerate(SWIPE_ORDER):
        if i + 1 < len(SWIPE_ORDER):
            swipe["left"].append({"if": {"condition": on(src), "then": go(src, SWIPE_ORDER[i + 1], "MOVE_LEFT")}})
        if i > 0:
            swipe["right"].append({"if": {"condition": on(src), "then": go(src, SWIPE_ORDER[i - 1], "MOVE_RIGHT")}})
    tab_go = [{"if": {"condition": {"and": [on(src), {"lambda": f"return id(tab_target) == {j};"}]},
                      "then": go(src, dst, "MOVE_LEFT" if j > i else "MOVE_RIGHT")}}
              for i, src in enumerate(SWIPE_ORDER) for j, dst in enumerate(SWIPE_ORDER) if src in BAR_PAGES and i != j]
    select = "".join(f"lv_obj_set_style_bg_opa(id(tab_{i}), id(bar_page) == {i} ? LV_OPA_20 : LV_OPA_TRANSP, 0);\n"
                     f"lv_obj_set_style_text_opa(id(tab_{i}_lbl), id(bar_page) == {i} ? LV_OPA_COVER : 184, 0);\n"
                     for i in range(len(TABS)))
    cfg["script"] = cfg["script"] + [
        {"id": "swipe_left", "mode": "single", "then": first_match(swipe["left"])},
        {"id": "swipe_right", "mode": "single", "then": first_match(swipe["right"])},
        {"id": "tab_go", "mode": "single", "then": tab_go},
        {"id": "bar_select", "mode": "restart", "then": [{"lambda": select}]}]
    cfg["globals"] = cfg["globals"] + [{"id": "bar_page", "type": "int", "initial_value": "0"},
                                       {"id": "cur_page", "type": "int", "initial_value": "0"},
                                       {"id": "tab_target", "type": "int", "initial_value": "0"}]
    bar_events(cfg["lvgl"]["pages"])
    cfg["lvgl"]["top_layer"]["widgets"].insert(0, {"obj": {
        "id": "bar", "x": 0, "y": 0, "width": 1024, "height": 600, "bg_opa": "TRANSP", "border_width": 0,
        "radius": 0, "pad_all": 0, "scrollable": False, "clickable": False,
        # Stop LVGL's gesture-parent search here. Otherwise a drag on a tab
        # bypasses these handlers and can become that tab's release click.
        "gesture_bubble": False,
        "on_swipe_left": [SWIPE_NO_TAP, {"script.execute": "swipe_left"}],
        "on_swipe_right": [SWIPE_NO_TAP, {"script.execute": "swipe_right"}],
        "widgets": [{"image": {"id": "bar_img", "src": "mic_icon", "x": BAR_CUT[0], "y": BAR_CUT[1], "hidden": True}},
                    tabs(0), mic_button("bar"), wake_strip()]}})
    cfg["esphome"]["includes"] = cfg["esphome"].get("includes", []) + [str(Path(__file__).resolve().parent / name) for name in ("bar_cut.h", "calendar_color.h", "slider_detents.h", "chat_scroll.h", "screen_swap.h", "choice_list.h", "music_buttons.h", "output_feedback.h", "ui_update.h", "reminders_view.h")]
    boot = cfg["esphome"].get("on_boot", [])
    # The tabs and the bar's mic button change state at once, like the music buttons. With the theme's 80 ms press transition still running
    # when a tap let go, the tab redrew in its own frame just before the page change, which then waited for that
    # frame to be shown: 5.5-8.5 ms of every tab change (bench/page_draw.h, docs/responsiveness.md).
    instant = ", ".join(f"id(tab_{i})" for i in range(len(TABS))) + ", id(tab_cog), id(bar_mic)"
    cfg["esphome"]["on_boot"] = [*(boot if isinstance(boot, list) else [boot]),
                                 {"priority": -100, "then": [{"lambda": BAR_CUT_CALL},
                                  {"lambda": f"unsigned removed = 0;\nfor (lv_obj_t *tab : {{{instant}}})\n"
                                             "  removed += panel_music_ui::instant_button(tab);\n"
                                             'ESP_LOGI("tabs", "%u transition-only theme styles detached", removed);'}]}]
    bar_hook(cfg)


BAR_CUT = (222, 500, 580, 100)   # the bar and mic glass with their shadow (bake.py's 8 px drop, 12 px blur)


def wake_strip():
    """Snooze and Stopp left of the tab bar (2026-11-02), on Gå hemifrån and Hemmet while a wakeup plays or ended
    within 30 minutes (binary_sensor.jarvis_vackning_pagar). A tap on the voice page silences the wakeup and comes
    back here; Snooze then plays it again in five minutes, Stopp ends it (and a pending snooze) and hides the strip
    until the next wakeup. Clear of BAR_CUT, so the bar's cut-out glass stays whole."""
    def button(key, text, action, then=()):
        return {"button": {"id": key, "width": 84, "height": 48, "radius": 24, "bg_color": WHITE, "bg_opa": "16%",
                           "border_width": 0, "shadow_opa": "TRANSP", "pad_all": 0,
                           "pressed": {"bg_color": WHITE, "bg_opa": "34%"},
                           "widgets": [label(text, f(600, 15), align="CENTER", id=f"{key}_lbl")],
                           "on_click": [{"logger.log": {"format": f"Wakeup: {text} pressed", "level": "INFO"}},
                                        {"homeassistant.action": {"action": action, "on_error": [{"logger.log": {
                                            "format": f"Wakeup: {text} refused by Home Assistant", "level": "WARN"}}]}},
                                        *then]}}
    return plain(id="wake_strip", x=28, y=RECTS["tabs"][1], width=188, height=60, radius=30, bg_color="0x141826",
                 bg_opa="88%", border_width=1, border_color=WHITE, border_opa="14%", pad_all=6, hidden=True,
                 layout={"type": "flex", "flex_flow": "ROW", "flex_align_main": "SPACE_BETWEEN",
                         "flex_align_cross": "CENTER", "flex_align_track": "CENTER"},
                 widgets=[button("wake_snooze", "Snooze", "jarvis_agent.wakeup_snooze",
                                 [{"lvgl.label.update": {"id": "wake_snooze_lbl", "text": "Snoozad"}}]),
                          button("wake_stop", "Stopp", "jarvis_agent.wakeup_stop",
                                 [{"lambda": "id(wake_dismissed) = true;"}, {"lvgl.widget.hide": "wake_strip"}])])
BAR_CUT_CALL = "bar_cut(id(bg)->get_lv_image_dsc(), id(bar_img));"


# A swipe is not also a tap: LVGL sends the click on release when the finger stayed on the object (Tavla's and
# Natt's full-screen tap went back at once after a swipe to Natt, 2026-10-23).
SWIPE_NO_TAP = {"lambda": "lv_indev_wait_release(lv_indev_active());"}


def bar_events(pages):
    """Swipes on each page of the tabs run the shared scripts."""
    for page in pages:
        if page["id"] in SWIPE_ORDER:
            page["on_swipe_left"] = [SWIPE_NO_TAP, {"script.execute": "swipe_left"}]
            page["on_swipe_right"] = [SWIPE_NO_TAP, {"script.execute": "swipe_right"}]


# As a page starts to load: the bar shows on the pages that have it, marks their tab and remembers the page for
# bar_back; on any other page it hides. lvgl's own on_screen_load_start only reaches the first page, so the
# callback goes on every page at boot (bar_hook, again when jarvis_orb.py has added its pages).
BAR_ON_LOAD = ("lv_obj_t *s = lv_event_get_target_obj(event);\n"
    # the page coming in, by its place among the tabs (-1: the voice page, the update screen)
    + "id(cur_page) = -1;\n" + "".join(f"if (s == id({pid})->obj) id(cur_page) = {i};\n" for i, pid in enumerate(SWIPE_ORDER))
    # any tab page but Natt ends night mode, however it was reached (the backlight and volume come back)
    + f"if (id(cur_page) >= 0 && s != id({NIGHT})->obj && id(night)) {{ id(night) = false; id(night_hw_off).execute(); }}\n"
    + "".join(
    f"if (s == id({pid})->obj) {{ id(bar_page) = {i}; lv_obj_remove_flag(id(bar), LV_OBJ_FLAG_HIDDEN); "
    f"id(bar_select).execute(); return; }}\n" for i, pid in enumerate(BAR_PAGES))
    + "lv_obj_add_flag(id(bar), LV_OBJ_FLAG_HIDDEN);")


def bar_hook(cfg):
    """The boot step that puts BAR_ON_LOAD on every page; replaces the one from before (pages added since)."""
    pages = ", ".join(f"id({p['id']})->obj" for p in cfg["lvgl"]["pages"])
    hook = {"priority": -100, "then": [{"lambda": "// bar_hook\nstatic auto on_load = [](lv_event_t *event) {\n"
                                                  + BAR_ON_LOAD + "\n};\n"
                                                  f"for (lv_obj_t *page : {{{pages}}})\n"
                                                  "  lv_obj_add_event_cb(page, on_load, LV_EVENT_SCREEN_LOAD_START, nullptr);\n"
                                                  # screen_swap.h: no fill under an opaque background picture, and a
                                                  # change between pages with the same picture redraws only what differs
                                                  "screen_swap::install();\n"
                                                  f"for (lv_obj_t *page : {{{pages}}})\n"
                                                  "  screen_swap::cover_backdrop(page);"}]}
    boot = cfg["esphome"].get("on_boot", [])
    boot = [b for b in (boot if isinstance(boot, list) else [boot]) if "// bar_hook" not in str(b)]
    cfg["esphome"]["on_boot"] = boot + [hook]


def tabs(selected=0):
    """The tab bar's buttons (`tab_<i>`, labels `tab_<i>_lbl`). It is shared by every page that has it (the top
    layer's `bar`), so bar_select marks the current tab and a tap goes through tab_go."""
    # A tab changes the page on release (a swipe that starts on a tab is not a tap), so it lights up on press:
    # without it nothing moved while the finger rested on a tab, 0.4-0.6 s in Joel's slow-motion video (2026-11-02).
    pressed = {"bg_color": WHITE, "bg_opa": "34%"}
    out = []
    for i, name in enumerate(TABS):
        on = i == selected
        button = {"id": f"tab_{i}", "x": TAB_X[i], "y": 0, "width": TAB_WIDTHS[i], "height": 48, "radius": 24,
                  "bg_color": WHITE, "bg_opa": "20%" if on else "TRANSP", "border_width": 0,
                  "shadow_opa": "TRANSP", "pressed": pressed,
                  "widgets": [label(name, f(600, 15), WHITE, id=f"tab_{i}_lbl",
                                    text_opa="COVER" if on else "72%", align="CENTER")],
                  "on_click": [{"lambda": f"id(tab_target) = {i};"}, {"script.execute": "tab_go"}]}
        out.append({"button": button})
    out.append({"button": {"id": "tab_cog", "x": sum(TAB_WIDTHS), "y": 0, "width": 48, "height": 48, "radius": 24,
                           "bg_color": WHITE, "bg_opa": "TRANSP", "border_width": 0, "shadow_opa": "TRANSP",
                           "pressed": pressed,
                           "widgets": [label(ICONS["cog"], "mdi_ui", WHITE, text_opa="72%", align="CENTER")],
                           "on_click": [{"script.execute": "settings_open"}]}})
    return widget("tabs", out, pad=6)


def mic_button(key):
    """The round mic button by the tab bar (`<key>_mic`, its icon `<key>_mic_img`) over the baked glass: a tap
    talks to Jarvis without the wake word, a long press turns the microphone off or on (mic_tap, mic_toggle).
    While it is off the button is red with a slashed mic (mic_show)."""
    x, y, w, h = RECTS["mic"][:4]
    return {"button": {"id": f"{key}_mic", "x": x, "y": y, "width": w, "height": h, "radius": h // 2,
                       "bg_color": RED, "bg_opa": "TRANSP", "border_width": 0, "shadow_opa": "TRANSP", "pad_all": 0,
                       "widgets": [{"image": {"src": "mic_icon", "align": "CENTER", "id": f"{key}_mic_img"}}],
                       "on_short_click": [{"script.execute": "mic_tap"}],
                       "on_long_press": [{"script.execute": "mic_toggle"}]}}


MIC_BUTTONS = ["bar"]     # the big round one, in the shared bar (the Jarvis card's small one went 2026-11-02)


def pill(key, dot, text):
    """A small status pill (`<key>_pill` with the text `<key>_lbl`), hidden until a script shows it."""
    return plain(id=f"{key}_pill", hidden=True, width="SIZE_CONTENT", height=30, radius=15, bg_color=WHITE,
                 bg_opa="16%", border_width=1, border_color=WHITE, border_opa="24%", pad_left=12, pad_right=14,
                 layout={"type": "flex", "flex_flow": "ROW", "flex_align_main": "CENTER",
                         "flex_align_cross": "CENTER", "flex_align_track": "CENTER", "pad_column": 8},
                 widgets=[shape(dot, 8, 8, 4), label(text, f(600, 14), id=f"{key}_lbl")])


def heading_feedback(control, heading):
    """An immediate small press hint; avoid repainting the whole translucent card for a tap."""
    return {"id":control,"on_press":[{"lambda":f'''static bool hooked=false;
if(!hooked){{hooked=true;lv_obj_add_event_cb(id({control}),[](lv_event_t*){{
  ui_update::text_opa(id({heading}),255,LV_PART_MAIN);
}},LV_EVENT_PRESS_LOST,nullptr);}}
ui_update::text_opa(id({heading}),153,LV_PART_MAIN);'''}],
        "on_release":[{"lambda":f'ui_update::text_opa(id({heading}),255,LV_PART_MAIN);'}]}


page = {
    "id": DASH, "bg_color": "0x0B1020", "pad_all": 0, "scrollable": False,
    "widgets": [
        {"image": {"src": "bg", "x": 0, "y": 0}},
        # Above the clock: a running timer (board.py) and "the net is down" or "no contact with Jarvis".
        plain(x=CLOCK_AREA[0] + 4, y=26, width=470, height=32,
              layout={"type": "flex", "flex_flow": "ROW", "flex_align_main": "START",
                      "flex_align_cross": "CENTER", "flex_align_track": "CENTER", "pad_column": 8},
              widgets=[pill("timer", ORANGE, "Timer"), pill("net", RED, "Nätet är nere")]),
        # Lock-screen clock: date above, big bold time below, both set by the script clock_update. 6 px higher
        # since 2026-10-28 (date 68 -> 62, time 80 -> 74), so Spelas nu at y 204 is not pressed against the digits.
        label(sample("onsdag 21 oktober"), f(600, 22), WHITE, text_opa="88%", x=CLOCK_AREA[0] + 4, y=62,
              id="date_lbl"),
        label(sample("07:42"), "clock_font", WHITE, x=CLOCK_AREA[0] - 2, y=74, id="clock_lbl"),
        # The weather (SMHI), the next events and the metro, all from sensor.jarvis_hemmet.
        # Now and SMHI's next three days; a tap opens the weather page (weather_page.py).
        {"obj": {**widget("weather", weather_page.card(_ui()))["obj"], **heading_feedback("weather_entry","w_heading"), "on_click": weather_page.open_page()}},
        # The next events; a tap opens the week (calendar_page.py).
        {"obj": {**widget("calendar", [
            label(sample("IDAG"), f(700, 13), RED, text_letter_space=1, x=0, y=0, id="cal_head"),
            *event(0, "Exempelmöte", "14:10", BLUE),
            *event(1, "Exempellunch", "13:20", PURPLE),
            *event(2, "Demouppgift", "18:40", ORANGE),
        ])["obj"], **heading_feedback("calendar_entry","cal_head"), "on_click": calendar_page.open_page()}},
        widget("sl", [
            shape(BLUE, 18, 18, 9, x=0, y=0, widgets=[label("T", f(700, 13), align="CENTER")]),
            label("Stadshagen", f(600, 15), x=26, y=0),
            label(sample("3 min att gå", ""), f(500, 14), sec=True, align="TOP_RIGHT", x=-2, y=1, id="sl_walk"),
            dep(0, "10", "Kungsträdgården", "hinner inte", "2", "missed"),
            dep(1, "11", "Kungsträdgården", "gå nu", "4", "go"),
            dep(2, "10", "Hjulsta", "gå om 2 min", "6", ""),
            dep(3, "11", "Akalla", "gå om 5 min", "9", ""),
            # SL's disruption line (home_status.metro_note; Joel, 2026-11-02): in the fourth row, which the server
            # leaves empty while it shows, two lines at most.
            plain(id="sl_note_row", x=0, y=26 + 3 * 43, width=444, height=40, hidden=True, clickable=False,
                  widgets=[shape(ORANGE, 8, 8, 4, x=16, y=6),
                           label(sample("Signalfel vid Fridhemsplan, räkna med förseningar"), f(500, 14), ORANGE,
                                 x=44, y=0, width=396, height=38, long_mode="DOT", id="sl_note")]),
        ]),
        widget("jarvis", [
            label("Jarvis", f(600, 15), x=0, y=0),
            # A tap on the heading opens the AI page (ai_page.py; Joel, 2026-11-01). Under Rensa, left of it.
            plain(x=-16, y=-16, width=220, height=40, pressed={"bg_color":WHITE,"bg_opa":"8%","radius":16}, on_click=ai_page.open_page()),
            # Rensa: empties the bubbles and makes Jarvis forget the conversation (jarvis_agent.clear).
            {"button": {"align": "TOP_RIGHT", "x": 2, "y": -5, "width": "SIZE_CONTENT", "height": 28,
                        "radius": 14, "bg_color": WHITE, "bg_opa": "16%", "border_width": 0,
                        "shadow_opa": "TRANSP", "pad_left": 12, "pad_right": 12, "pad_top": 0, "pad_bottom": 0,
                        "widgets": [label("Rensa", f(600, 14), align="CENTER")],
                        "on_click": [{"script.execute": "rensa"}]}},
            # Härnäst (2026-11-02): what is coming while the chat is empty, from sensor.jarvis_hemmet's next_up
            # (home_status.next_up); under the chat, which shows nothing then.
            label(sample("20:30 Ring Pelle\nVäckning 08:45"), f(500, 16), WHITE,
                  text_opa="88%", x=0, y=30, width=444, height=168, text_line_space=10, long_mode="CLIP",
                  clickable=False, id="jv_next", hidden=True),
            # The whole card under its heading since the small mic and its hint went (2026-11-02): the round
            # mic by the tab bar does the same and is red while the microphone is off.
            plain(id="chat_viewport", x=0, y=24, width=444, height=174,
                  scrollable=True, scroll_dir="VER", scrollbar_mode="AUTO",
                  scroll_chain_ver=False, gesture_bubble=False, scroll_elastic=False,
                  widgets=[plain(width="100%", height="SIZE_CONTENT", min_height="100%", clickable=False,
                      layout={"type": "flex", "flex_flow": "COLUMN", "flex_align_main": "END",
                              "flex_align_cross": "START", "flex_align_track": "START", "pad_row": 8},
                      widgets=[
                          # Earlier in the same conversation (chat_view.py), filled by panel_chat::earlier.
                          *[bubble("", False, key=f"e{i}") for i in range(EARLIER)],
                          bubble(sample("Lägg in Demohändelse på tisdag 15:10"), True, key="you"),
                          bubble(sample("Fiktiv kalenderpost: tisdag 15:10."), False, 270, key="jv"),
                      ])]),
        ]),
    ],
}


# The Larm card's text right of its dots: the card's content width (16 px padding on both sides) less the dot's 14.
DOT_TEXT_W = RECTS["calendar"][2] - 2 * 16 - 14


def dot_row(y, key, title, sub):
    """A status line in the Larm card: dot `<key>_dot`, value `<key>` and a faint line `<key>_sub` below. Both
    lines stop at the card's right padding, as far in as the left (a long door line ran to the edge, 2026-10-29)."""
    return [shape(GREEN if not BOARD else WHITE, 8, 8, 4, x=0, y=y + 7, id=f"{key}_dot",
                  **({"bg_opa": "40%"} if BOARD else {})),
            label(title, f(600, 16), x=14, y=y, id=key, width=DOT_TEXT_W, long_mode="DOT"),
            label(sub, f(500, 13), sec=True, x=14, y=y + 21, id=f"{key}_sub", width=DOT_TEXT_W, long_mode="DOT")]


def fact_row(y, title, value, key):
    """A faint caption with a value below, as in the Tvätt card."""
    return [label(title, f(500, 13), sec=True, x=0, y=y),
            label(value, f(600, 16), x=0, y=y + 18, id=key, width=444, long_mode="DOT")]


TIMER_TEXT_W = {True: 196, False: 340}     # h_timer's width beside the quick timers (idle) or Avbryt (running)


def timer_button(key, text, action, data=None, width=74):
    """One of Hemmet's timer buttons: Home Assistant's action, logged when it refuses (the HA log says why)."""
    call = {"action": action, **({"data": data} if data else {}),
            "on_error": [{"logger.log": {"format": f"Quick timer {text}: Home Assistant refused", "level": "WARN"}}]}
    return {"button": {"id": key, "width": width, "height": 40, "radius": 20, "bg_color": WHITE, "bg_opa": "16%",
                       "border_width": 0, "shadow_opa": "TRANSP", "pad_all": 0,
                       "pressed": {"bg_color": WHITE, "bg_opa": "34%"},
                       "widgets": [label(text, f(600, 15), align="CENTER")],
                       "on_click": [{"logger.log": {"format": f"Quick timer: {text}", "level": "INFO"}},
                                    {"homeassistant.action": call}]}}


def quick_timers(running=False):
    """Hemmet's quick timers (2026-11-02): 5, 10 and 15 min while none runs, Avbryt while one does. They start and
    cancel Home Assistant's own timers on the panel (jarvis_agent.timer_start, timer_cancel), the same as by voice."""
    row = {"type": "flex", "flex_flow": "ROW", "flex_align_main": "END", "flex_align_cross": "CENTER",
           "flex_align_track": "CENTER", "pad_column": 8}
    return [plain(id="h_timer_quick", align="TOP_RIGHT", x=0, y=20, width="SIZE_CONTENT", height=40, layout=row,
                  hidden=running, widgets=[timer_button(f"h_timer_{m}", f"{m} min", "jarvis_agent.timer_start",
                                                        {"minutes": str(m)}) for m in QUICK_TIMERS]),
            plain(id="h_timer_cancel", align="TOP_RIGHT", x=0, y=20, width="SIZE_CONTENT", height=40, layout=row,
                  hidden=not running, widgets=[timer_button("h_timer_stop", "Avbryt", "jarvis_agent.timer_cancel",
                                                            width=92)])]


def hemmet_page():
    """Hemmet: the home at a glance, from sensor.jarvis_hemmet, on the dashboard's baked glass shapes."""
    return {
        "id": HOME, "bg_color": "0x0B1020", "pad_all": 0, "scrollable": False,
        "widgets": [
            {"image": {"src": "bg", "x": 0, "y": 0}},
            label("Hemmet", f(700, 22), WHITE, text_opa="88%", x=CLOCK_AREA[0] + 4, y=56),
            # A green check when all is calm, an orange "!" when something is wrong (the alert attribute).
            {"image": {"src": "s_ok", "x": CLOCK_AREA[0] + 4, "y": 98, "id": "h_status_icon", "hidden": BOARD}},
            label(sample("Allt lugnt hemma.", "–"), f(600, 30), x=CLOCK_AREA[0] + 60, y=102, width=410,
                  id="h_status"),
            # No summary line under the status: it repeated the cards beside it (removed 2026-10-28, Joel's
            # choice); Spelas nu takes its place (now_playing.py).
            widget("weather", [
                *head("Inne", "i_thermo"),
                label(sample("21,5°", "–"), f(400, 56), x=-3, y=14, id="h_inside"),
                label(sample("PM2,5 4 µg/m³"), f(600, 14), x=0, y=92, id="h_pm25"),
                label(sample("Bra luft"), f(600, 14), GREEN, x=0, y=112, id="h_air"),
                label("Luftrenaren", f(500, 13), sec=True, x=0, y=146),
                label(sample("Auto · filter 63 %"), f(600, 14), x=0, y=164, width=198, long_mode="DOT",
                      id="h_purifier"),
            ]),
            widget("calendar", [
                *head("Larm", "i_shield"),
                *dot_row(28, "h_smoke", sample("Ingen rök", "–"), "Brandvarnaren"),
                *dot_row(84, "h_water", sample("Ingen läcka", "–"), "Vattenlarmet"),
                *dot_row(140, "h_door", sample("Stängd", "–"), sample("Ytterdörren · demostatus", "Ytterdörren")),
            ]),
            # Every timer and reminder; a tap opens them (reminders_page.py; Joel, 2026-11-03). The quick timers
            # inside are buttons of their own, so their taps do not open it.
            {"obj": {**widget("sl", [
                *head("Påminnelser och timers", "i_bell", id="h_rem_head"),
                # board.py's timers fill this; "Ingen timer igång" at 60 % when none runs. Its width leaves room
                # for the quick timers or Avbryt beside it (voice.py's TIMERS_SHOW sets it).
                label(sample("Demotimer · 4:12 kvar", "Ingen timer igång"), f(600, 19), ORANGE if not BOARD else WHITE,
                      x=0, y=28, width=TIMER_TEXT_W[BOARD], long_mode="DOT", id="h_timer",
                      **({"text_opa": SEC} if BOARD else {})),
                *quick_timers(running=not BOARD),
                shape(WHITE, 444, 1, 0, x=0, y=62, bg_opa="18%"),
                label(sample("18:40 Demouppgift\n19:20 Demoaktivitet\ntis 14:45 Gå till tvättstugan", "Inga påminnelser"),
                      f(500, 16), x=0, y=76, width=444, text_line_space=8, id="h_rem"),
            ])["obj"], **heading_feedback("reminders_entry", "h_rem_head"), "on_click": reminders_page.open_page()}},
            widget("jarvis", [
                *head("Tvätt, sol och måne", "i_washer"),
                *fact_row(30, "Nästa tvättid", sample("tis 10 nov 15:00–18:00", "–"), "h_laundry"),
                label(sample("Gul + Röd"), f(500, 14), sec=True, x=0, y=72, id="h_rooms"),
                # Tighter since the moon came in (Joel, 2026-10-28; home_status.moon_line)
                *fact_row(102, "Dagsljus", sample("Solen går ner 16:41", "–"), "h_sun"),
                *fact_row(150, "Månen", sample("Avtagande måne, 88 % · fullmåne 24 nov", "–"), "h_moon"),
            ]),
            # The house line (Joel, 2026-11-02; home_status.house_line): an outage from his own providers over
            # home in orange, hidden when there is none; one line, above Spelas nu (now_playing.BAR_Y 168).
            # Last on the page: the visual policy anchors the cards as screen/4..7 and a hidden widget leaves the
            # tree, so a line among them broke Hemmet's check whenever it hid (2026-11-02).
            label(sample("Ellevio: strömavbrott pågår, till ca 23:00"), f(500, 15), ORANGE,
                  text_opa="70%", x=CLOCK_AREA[0] + 4, y=144, width=CLOCK_AREA[2] - 4, height=20, long_mode="DOT",
                  hidden=True, id="h_house"),
        ],
    }


def natt_page():
    """Natt: black, a dim warm clock and the date, a running timer and the next reminder. A tap anywhere
    leaves night mode (the invisible layer on top catches it)."""
    return {
        "id": NIGHT, "bg_color": "0x000000", "pad_all": 0, "scrollable": False,
        "widgets": [
            label(sample("23:41"), "clock_font", WARM, align="CENTER", y=-56, text_opa="45%", id="n_clock"),
            label(sample("onsdag 21 oktober"), f(600, 22), WARM, align="CENTER", y=26, text_opa="35%", id="n_date"),
            # The next wakeup (2026-11-02), so the morning can be checked before bed: "Väckning 08:45" from
            # sensor.jarvis_nasta_vackning's panel attribute (jarvis_agent/wakeup.py panel_line).
            plain(id="n_wake", align="CENTER", y=72, width="SIZE_CONTENT", height=30, hidden=BOARD,
                  layout={"type": "flex", "flex_flow": "ROW", "flex_align_main": "CENTER", "flex_align_cross": "CENTER",
                          "flex_align_track": "CENTER", "pad_column": 8},
                  widgets=[label(ICONS["alarm"], "mdi_ui", WARM, text_opa="45%"),
                           label(sample("Väckning 08:45"), f(600, 19), WARM, text_opa="50%", id="n_wake_lbl")]),
            label(sample("Demotimer · 4:12"), f(600, 19), ORANGE, align="CENTER", y=110, text_opa="65%", id="n_timer",
                  hidden=BOARD),
            label(sample("tis 14:45 Gå till tvättstugan"), f(500, 16), WARM, align="CENTER", y=146, text_opa="30%",
                  id="n_rem"),
            plain(x=0, y=0, width=1024, height=600, on_click=[{"script.execute": "night_off"}]),
        ],
    }


def tavla_page():
    """Tavla: the day's picture alone (art, the same crop as the background, without glass or dimming) and
    a small plaque with its title and who made it. No tab bar; a tap anywhere goes back to the dashboard.
    A tap on the plaque opens NASA's explanation, and the Jorden button shows the Earth (tavla_earth.py)."""
    import tavla_earth
    ui = _ui()
    return {
        "id": FRAME, "bg_color": "0x000000", "pad_all": 0, "scrollable": False,
        "widgets": [
            {"image": {"src": "art", "x": 0, "y": 0, "id": "tavla_art"}},
            # Under the plaque and the Earth view, so a tap on them is theirs and anywhere else goes back.
            plain(x=0, y=0, width=1024, height=600, on_click=bar_back("FADE_IN", "300ms")),
            tavla_earth.card(ui, "t_plaque", "BOTTOM_LEFT", 28, [
                label(sample("Stjärnenatten"), f(600, 19), id="t_title", long_mode="DOT", max_width=560),
                label(sample("Vincent van Gogh, 1889"), f(500, 14), sec=True, id="t_credit", long_mode="DOT",
                      max_width=560),
                tavla_earth.explanation(ui)], hidden=BOARD, on_click=tavla_earth.plaque_tap(ui)),
            *tavla_earth.widgets(ui),
        ],
    }


SHEET_W, SHEET_PAD = 640, 28
ROW_W = SHEET_W - 2 * SHEET_PAD


def row_head(icon, name, key, height=26):
    """An icon and a name on the left of a row in Inställningar (the name `<key>_title`)."""
    return [label(ICONS[icon], "mdi_ui", WHITE, text_opa="72%", x=0, y=(height - 24) // 2),
            label(name, f(600, 16), x=36, y=(height - 20) // 2, id=f"{key}_title")]


def slider_value(key, low, release=False):
    """Use one snapped value for the knob, label, preview and release commit.

    Programmatic updates keep restored/Home Assistant values such as 12% exact.
    Only real pointer dragging and its final release use five-percent stops.
    """
    raw = f"lv_slider_get_value(id(s_{key}))" if release else "(int) x"
    guard = "true" if release else f"lv_slider_is_dragged(id(s_{key}))"
    return (f"int value = {raw};\n"
            f"if ({guard}) {{\n"
            f"  value = slider_detents::nearest(value, {low});\n"
            f"  lv_slider_set_value(id(s_{key}), value, LV_ANIM_OFF);\n"
            "}\n"
            f"id({key}_pct) = value;\n"
            f'lv_label_set_text(id(s_{key}_val), (std::to_string(value) + " %").c_str());\n')


def slider_row(key, icon, name, value, low):
    """A five-percent touch slider. Brightness previews locally; release saves once."""
    preview = [{"script.execute": f"{key}_preview"}] if key in ("bright", "nightl") else []
    return plain(width=ROW_W, height=60, widgets=[
        *row_head(icon, name, f"s_{key}"),
        label(f"{value} %", f(600, 16), sec=True, align="TOP_RIGHT", x=0, y=3, id=f"s_{key}_val"),
        # The knob reaches 14 px past the track's ends, so the track is inset by that much.
        {"slider": {"id": f"s_{key}", "x": 14, "y": 42, "width": ROW_W - 28, "height": 8, "radius": 4,
                    "min_value": low, "max_value": 100, "value": value, "animated": False,
                    "bg_color": WHITE, "bg_opa": "18%", "border_width": 0, "pad_all": 0,
                    "indicator": {"bg_color": WHITE, "bg_opa": "COVER", "radius": 4},
                    "knob": {"bg_color": WHITE, "bg_opa": "COVER", "radius": 14, "pad_all": 10,
                             "shadow_opa": "TRANSP", "border_width": 0},
                    "on_value": [{"lambda": slider_value(key, low)}, *preview],
                    "on_release": [{"lambda": slider_value(key, low, release=True)},
                                   {"script.execute": f"{key}_set"}]}}])

BG_NOTE = "Egna bilder laddar du upp i Home Assistant"
BG_PICK_WINDOW_MS = 60000   # a picked background closes Inställningar when it arrives within this (bg_shown)
OUT_NOTE = "Där radion spelar"
RECONNECT = "button.jarvis_koppla_om_hogtalaren"   # Koppla om beside Ljud ut (docs/bluetooth-speaker.md)
RECONNECT_BUSY = "Kopplar om …"
PICKS = 8   # choice_list::MAX: one pick script per list entry


def reconnect_button(key):
    """Koppla om (2026-11-01, Joel: "en reconnect grej i hallpanelen, så man kan trigga själv"): a round button left
    of the Ljud ut list that runs `{key}_reconnect` (Home Assistant's button.jarvis_koppla_om_hogtalaren)."""
    return {"button": {"id": f"s_{key}_reconnect", "align": "RIGHT_MID", "x": -228, "width": 40, "height": 40,
                       "radius": 20, "bg_color": WHITE, "bg_opa": "14%", "border_width": 0, "shadow_opa": "TRANSP",
                       "pad_all": 0, "pressed": {"bg_color": WHITE, "bg_opa": "30%"},
                       "widgets": [label(ICONS["reconnect"], "mdi_ui", WHITE, align="CENTER")],
                       "on_click": [{"script.execute": f"{key}_reconnect"}]}}


def reconnect_script(key, note):
    """Keep waiting until HA confirms the Bluetooth reconnect, or a bounded timeout/refusal occurs."""
    return {"id": f"{key}_reconnect", "mode": "single", "then": [{"if": {
        "condition": {"lambda": "return !output_feedback::state.busy();"},
        "then": [{"lambda": '''
output_feedback::state.begin(output_feedback::Kind::RECONNECT, "", choice_list::out.current, millis());
ESP_LOGI("output", "reconnect sent (%u)", unsigned(output_feedback::state.ticket));
id(output_feedback_render).execute();
id(out_reconnect_send).execute(output_feedback::state.ticket);
'''}], "else": [{"script.execute": "output_feedback_render"}]}}]}


def output_callbacks():
    return {"on_success": [{"lambda": '''
if(output_feedback::state.current(ticket)) {
  output_feedback::state.acknowledge(ticket, millis());
  ESP_LOGI("output", "request %u accepted, phase %u", unsigned(ticket), unsigned(output_feedback::state.phase));
  id(output_feedback_render).execute();
}
'''}], "on_error": [{"lambda": '''
if(output_feedback::state.fail(ticket, millis())) {
  ESP_LOGW("output", "request %u refused; inspect Jarvis journal for reason", unsigned(ticket));
  id(output_feedback_render).execute();
}
'''}]}


def output_scripts():
    return [{"id": "out_reconnect_send", "parameters": {"ticket": "uint32_t"}, "then": [
        {"homeassistant.action": {"action": "button.press", "data": {"entity_id": RECONNECT}, **output_callbacks()}}]},
        {"id": "output_feedback_render", "then": [{"lambda": '''
const auto &op = output_feedback::state;
const auto message = op.message();
const char *note = message.empty() ? "Där radion spelar" : message.c_str();
if(std::strcmp(lv_label_get_text(id(s_out_note)), note)) lv_label_set_text(id(s_out_note), note);
int selected = choice_list::out.index();
if(op.busy() && op.kind == output_feedback::Kind::CHOICE) {
  for(size_t i=0;i<choice_list::out.names.size();++i)
    if(choice_list::out.names[i]==op.target) selected=int(i);
}
if(selected>=0 && lv_dropdown_get_selected(id(s_out)->obj)!=selected)
  lv_dropdown_set_selected(id(s_out)->obj,selected);
'''}]}]


def choice_row(key, icon, name, note, example, busy, reconnect=False):
    """A row whose choice opens as a list (Joel's wish over ‹ ›, 2026-10-27): Home Assistant's select gives the
    entries (its panel_options attribute, live()), and picking one runs the entry's pick script (choice_scripts).
    reconnect adds Koppla om left of the list; the note then ends before it."""
    picks = "\n".join(f"  case {i}: id({key}_pick_{i}).execute(); break;" for i in range(PICKS))
    return plain(width=ROW_W, height=44, widgets=[
        *row_head(icon, name, f"s_{key}", 24),
        label(note, f(500, 14), sec=True, x=36, y=24, id=f"s_{key}_note", width=264 if reconnect else 300,
              long_mode="DOT"),
        *([reconnect_button(key)] if reconnect else []),
        {"dropdown": {"id": f"s_{key}", "align": "RIGHT_MID", "x": 0, "width": 220, "height": 40, "radius": 20,
                      "options": [sample(example, "–")], "dir": "TOP", "symbol": ICONS["down"],
                      "bg_color": WHITE, "bg_opa": "14%", "border_width": 0, "shadow_opa": "TRANSP",
                      "text_font": f(600, 16), "text_color": WHITE, "pad_left": 16, "pad_right": 12,
                      "pad_top": 10, "pad_bottom": 10,
                      "indicator": {"text_font": "mdi_ui", "text_color": WHITE},
                      "dropdown_list": {"bg_color": "0x1F2433", "bg_opa": "COVER", "text_font": f(600, 16),
                                        "text_color": WHITE, "border_width": 1, "border_color": WHITE,
                                        "border_opa": "14%", "radius": 16, "max_height": 260,
                                        "selected": {"bg_color": WHITE, "bg_opa": "20%"}},
                      "on_change": [*([{"lambda": "id(bg_picked_ms) = millis();"}] if key == "bg" else []),
                                    {"lvgl.label.update": {"id": f"s_{key}_note", "text": busy}},
                                    {"lambda": f"switch (lv_dropdown_get_selected(id(s_{key})->obj)) {{\n{picks}\n}}"}]}}])


def pick_template(entity, i):
    """Entry i of the list the panel shows (the select's panel_options), not of its options: Home Assistant puts the
    current choice last there when it is past the first eight (an uploaded picture, 2026-11-03), so entry 7 can be
    another option than options[7]."""
    return f"{{{{ state_attr('{entity}', 'panel_options').split('\\n')[{i}] }}}}"


def choice_scripts(key, entity):
    """One script per list entry: Home Assistant picks entry i of the panel's list (a template), since an action's
    data cannot carry the panel's text here (the config is JSON, without !lambda)."""
    if key == "out":
        scripts = []
        for i in range(PICKS):
            scripts.append({"id": f"out_send_{i}", "parameters": {"ticket": "uint32_t"}, "then": [
                {"homeassistant.action": {"action": "select.select_option", "data": {"entity_id": entity},
                    "data_template": {"option": pick_template(entity, i)}, **output_callbacks()}}]})
            scripts.append({"id": f"out_pick_{i}", "then": [{"if": {
                "condition": {"lambda": f"return !output_feedback::state.busy() && choice_list::out.names.size() > {i};"},
                "then": [{"lambda": f'''
output_feedback::state.begin(output_feedback::Kind::CHOICE, choice_list::out.names[{i}], choice_list::out.current, millis());
ESP_LOGI("output", "choice {i} sent (%u)", unsigned(output_feedback::state.ticket));
id(output_feedback_render).execute();
id(out_send_{i}).execute(output_feedback::state.ticket);
'''}], "else": [{"script.execute": "output_feedback_render"}]}}]})
        return scripts
    return [{"id": f"{key}_pick_{i}", "then": [{"homeassistant.action": {
        "action": "select.select_option", "data": {"entity_id": entity},
        "data_template": {"option": pick_template(entity, i)}}}]}
        for i in range(PICKS)]


def choice_sensors(key, entity, note):
    """The list's entries and its current choice from Home Assistant; the note comes back with the choice."""
    show = (f"int i = choice_list::{key}.index();\n"
            f"if (i >= 0) lv_dropdown_set_selected(id(s_{key})->obj, i);\n")
    result = [
        {"platform": "homeassistant", "id": f"{key}_options", "entity_id": entity, "attribute": "panel_options",
         "on_value": [{"lambda": f"if (choice_list::{key}.options(x))\n"
                                 f"  lv_dropdown_set_options(id(s_{key})->obj, choice_list::{key}.joined().c_str());\n"
                                 + show}]},
        {"platform": "homeassistant", "id": f"{key}_name", "entity_id": entity,
         "on_value": [{"lambda": f"choice_list::{key}.current = x;\n" + show},
                      {"lvgl.label.update": {"id": f"s_{key}_note", "text": note}}]}]
    if key == "out":
        result[0]["on_value"].append({"script.execute": "output_feedback_render"})
        result[1]["on_value"] = [{"lambda": '''
choice_list::out.current = x;
if(output_feedback::state.observe(x, millis()))
  ESP_LOGI("output", "choice %u confirmed by current output", unsigned(output_feedback::state.ticket));
'''}, {"script.execute": "output_feedback_render"}]
    return result


def settings_layer(open_=False):
    """Inställningar: a sheet in the middle of a dimmed screen, on LVGL's top layer so it shows over any page.
    A tap beside it, Klar, Natt or the voice page closes it."""
    sheet = plain(
        # Keep underlying page text out of the controls; the surrounding scrim stays translucent.
        # 24 px up from the middle, clear of the tab bar.
        align="CENTER", y=-24, width=SHEET_W, height="SIZE_CONTENT", radius=28, bg_color="0x141826", bg_opa="COVER",
        border_width=1, border_color=WHITE, border_opa="14%", pad_all=SHEET_PAD,
        # 10 px between rows since Ljud ut (2026-10-27): with 18 the sheet reached under the tab bar.
        layout={"type": "flex", "flex_flow": "COLUMN", "pad_row": 10},
        widgets=[
            plain(width=ROW_W, height=34, widgets=[
                label("Inställningar", f(700, 22), x=0, y=3),
                {"button": {"align": "TOP_RIGHT", "x": 0, "y": 0, "width": "SIZE_CONTENT", "height": 34,
                            "radius": 17, "bg_color": WHITE, "bg_opa": "16%", "border_width": 0,
                            "shadow_opa": "TRANSP", "pad_left": 16, "pad_right": 16, "pad_top": 0, "pad_bottom": 0,
                            "widgets": [label("Klar", f(600, 15), align="CENTER")],
                            "on_click": [{"script.execute": "settings_close"}]}}]),
            slider_row("bright", "sun", "Skärmljus", 100, 5),
            slider_row("nightl", "moon", "Nattljus", 12, 1),
            slider_row("vol", "volume", "Volym", 50, 0),
            plain(width=ROW_W, height=44, widgets=[
                *row_head("mic", "Mikrofon", "s_mic", 24),
                label('Lyssnar efter "hey Jarvis"', f(500, 14), sec=True, x=36, y=24, id="s_mic_sub"),
                {"switch": {"id": "s_mic", "align": "RIGHT_MID", "x": 0, "width": 56, "height": 32,
                            "state": {"checked": True}, "bg_color": WHITE, "bg_opa": "20%",
                            "indicator": {"bg_color": WHITE, "bg_opa": "20%", "checked": {"bg_color": GREEN,
                                                                                       "bg_opa": "COVER"}},
                            "knob": {"bg_color": WHITE, "pad_all": -3},
                            "on_click": [{"script.execute": "mic_toggle"}]}}]),
            choice_row("bg", "image", "Bakgrund", BG_NOTE, "Stjärnenatten", "Byter bild …"),
            choice_row("out", "speaker", "Ljud ut", OUT_NOTE, "Panelen", "Kopplar …", reconnect=True),
        ])
    return {"id": "settings", "x": 0, "y": 0, "width": 1024, "height": 600, "bg_color": "0x000000",
            "bg_opa": "55%", "border_width": 0, "radius": 0, "pad_all": 0, "scrollable": False,
            "hidden": not open_, "on_click": [{"script.execute": "settings_close"}], "widgets": [sheet]}


SAID = "Boka tvättstugan på tisdag eftermiddag"
REPLY = "Klart, tisdag 15-18 är bokat. Påminnelsen ligger i iCloud-kalendern 14:45."
ORB_FRAMES, ORB_SIZE, ORB_CENTER = 24, 220, (512, 205)   # see orb.py and bake.py


def orb_page(final):
    """Voice page: blurred painting with an edge glow (baked), the animated orb (pre-rendered
    frames in an animimg), a small status pill, your sentence and Jarvis's reply."""
    return {
        "id": "page_orb", "bg_color": "0x05060C", "pad_all": 0, "scrollable": False,
        "widgets": [
            {"image": {"src": "bg_orb", "x": 0, "y": 0}},
            {"animimg": {"id": "orb_anim", "src": [f"orb_{i:02d}" for i in range(ORB_FRAMES)],
                         "duration": "2000ms", "x": ORB_CENTER[0] - ORB_SIZE // 2,
                         "y": ORB_CENTER[1] - ORB_SIZE // 2}},
            plain(align="TOP_MID", y=326, width="SIZE_CONTENT", height=34, radius=17, bg_color=WHITE,
                  bg_opa="14%", border_width=1, border_color=WHITE, border_opa="30%",
                  pad_left=14, pad_right=16,
                  layout={"type": "flex", "flex_flow": "ROW", "flex_align_main": "CENTER",
                          "flex_align_cross": "CENTER", "flex_align_track": "CENTER", "pad_column": 8},
                  widgets=[shape(BLUE if final else CYAN, 8, 8, 4, id="status_dot"),
                           label("Svarar" if final else "Lyssnar…", f(600, 15), id="status_lbl")]),
            label(SAID if final else "", f(500, 21), sec=True, id="you_lbl", align="TOP_MID", y=378,
                  width=760, text_align="CENTER"),
            label(REPLY if final else "", f(600, 30), id="reply_lbl", align="TOP_MID", y=416,
                  width=820, text_align="CENTER"),
        ],
    }


def demo_script():
    """The whole voice flow, driven by delays so the simulator can be recorded.

    On the board the same steps hang off voice_assistant triggers instead:
    on_wake_word_detected -> fade in page_orb and show "Lyssnar…";
    on_stt_end -> x is your whole sentence (it arrives at once, never word by word);
    on_intent_start -> "Tänker…";
    on_tts_start -> x is Jarvis's whole reply; we reveal it word by word, paced to the speech;
    on_end -> wait a few seconds, then fade back to the dashboard.
    """
    def status(text, color):
        return [{"lvgl.label.update": {"id": "status_lbl", "text": text}},
                {"lvgl.obj.update": {"id": "status_dot", "bg_color": color}}]

    words = REPLY.split(" ")
    steps = [{"delay": "2s"},
             {"lvgl.label.update": {"id": "you_lbl", "text": ""}},
             {"lvgl.label.update": {"id": "reply_lbl", "text": ""}},
             *status("Lyssnar…", CYAN),
             {"lvgl.page.show": {"id": "page_orb", "animation": "FADE_IN", "time": "400ms"}},
             {"delay": "1800ms"},
             {"lvgl.label.update": {"id": "you_lbl", "text": SAID}},
             *status("Tänker…", PURPLE),
             {"delay": "1400ms"},
             *status("Svarar", BLUE)]
    for k in range(1, len(words) + 1):
        steps += [{"lvgl.label.update": {"id": "reply_lbl", "text": " ".join(words[:k])}}, {"delay": "230ms"}]
    steps += [{"delay": "2800ms"},
              {"lvgl.page.show": {"id": "page_glass_v2", "animation": "FADE_IN", "time": "400ms"}}]
    return [{"id": "demo", "then": steps}]


# -- live data, clock and night mode -------------------------------------------------------------------------
def shown(wid, cond):
    """C++: show the widget when cond holds, else hide it."""
    return (f"if ({cond}) lv_obj_remove_flag(id({wid}), LV_OBJ_FLAG_HIDDEN);\n"
            f"else lv_obj_add_flag(id({wid}), LV_OBJ_FLAG_HIDDEN);\n")


def text(wid, empty="–"):
    """C++: the label shows x, or `empty` when Home Assistant sent an empty string."""
    return f'lv_label_set_text(id({wid}), x.empty() ? "{empty}" : x.c_str());\n'


def colour(wid, cond, yes, no, part="text"):
    """C++: the label's text colour (or the shape's background) is `yes` when cond holds, else `no`."""
    prop = "text_color" if part == "text" else "bg_color"
    opa = "" if part == "text" else f"lv_obj_set_style_bg_opa(id({wid}), LV_OPA_COVER, LV_PART_MAIN);\n"
    return f"lv_obj_set_style_{prop}(id({wid}), lv_color_hex(({cond}) ? {yes} : {no}), LV_PART_MAIN);\n" + opa


def earlier_text():
    """The bubbles before the newest question, so the chat scrolls back through the conversation."""
    labels = ", ".join(f"id(e{i}_txt)" for i in range(EARLIER))
    return f"lv_obj_t *labels[{EARLIER}] = {{{labels}}};\npanel_chat::earlier(id(chat_viewport), labels, {EARLIER}, x);"


def bubble_text(key):
    """Keep the full current question/reply; preserve a reader's position above the end."""
    return f"panel_chat::update(id(chat_viewport), id({key}_row), id({key}_txt), x);"


def alarm(key):
    """C++: smoke_on or water_on. A red dot and text while it is on, and the panel wakes up on Hemmet."""
    return (f"bool on = x == \"on\";\n" + colour(f"{key}_dot", "on", RED, GREEN, "bg") + colour(key, "on", RED, WHITE)
            + f"lv_obj_set_style_text_opa(id({key}), on ? 255 : 178, LV_PART_MAIN);\n"
            + f"bool was = id({key}_alarm);\n"
            f"id({key}_alarm) = on;\n"            # set first: board.py's backlight reads it during alarm_wake
            f"if (on && !was) id(alarm_wake).execute();\n")


def hemmet(attribute, lam, actions=()):
    """A text sensor that follows one attribute of sensor.jarvis_hemmet (its state when attribute is None):
    the lambda, then any further actions."""
    d = {"platform": "homeassistant", "id": f"hm_{attribute or 'state'}", "entity_id": HEMMET,
         "on_value": [{"lambda": lam}, *actions]}
    if attribute:
        d["attribute"] = attribute
    return d


def switch_image(wid, sources, default=None):
    """Actions: point image `wid` at sources[x] (x the sensor's value), or at default for any other value."""
    out = [{"if": {"condition": {"lambda": f'return x == "{value}";'},
                   "then": [{"lvgl.image.update": {"id": wid, "src": src}}]}} for value, src in sources.items()]
    if default:
        known = " && ".join(f'x != "{v}"' for v in sources)
        out.append({"if": {"condition": {"lambda": f"return {known};"},
                           "then": [{"lvgl.image.update": {"id": wid, "src": default}}]}})
    return out


# C++: split x on "|" into p.
SPLIT = r"""std::vector<std::string> p;
for (size_t s = 0;;) {
  size_t e = x.find('|', s);
  p.push_back(x.substr(s, e == std::string::npos ? std::string::npos : e - s));
  if (e == std::string::npos) break;
  s = e + 1;
}
"""


def departure(i):
    """C++: departure row i from "line|destination|note|minutes|state"; an empty value hides it."""
    r = f"sl{i}"
    return (SPLIT + f"if (x.empty() || p.size() < 5) {{ lv_obj_add_flag(id({r}_row), LV_OBJ_FLAG_HIDDEN); return; }}\n"
            f"lv_label_set_text(id({r}_line), p[0].c_str());\n"
            f"lv_label_set_text(id({r}_dest), p[1].c_str());\n"
            f"lv_label_set_text(id({r}_note), p[2].c_str());\n"
            f"lv_label_set_text(id({r}_min), p[3].c_str());\n"
            'bool go = p[4] == "go", missed = p[4] == "missed";\n'
            f"lv_obj_set_style_bg_opa(id({r}_row), go ? 36 : 0, LV_PART_MAIN);\n"
            f"lv_obj_set_style_opa(id({r}_row), missed ? 115 : 255, LV_PART_MAIN);\n"
            f"lv_obj_set_style_text_color(id({r}_note), lv_color_hex(go ? {GREEN} : {WHITE}), LV_PART_MAIN);\n"
            f"lv_obj_set_style_text_opa(id({r}_note), go ? 255 : 153, LV_PART_MAIN);\n"
            f"lv_obj_set_style_text_color(id({r}_min), lv_color_hex(go ? {GREEN} : {WHITE}), LV_PART_MAIN);\n"
            f"lv_obj_remove_flag(id({r}_row), LV_OBJ_FLAG_HIDDEN);\n")


def calendar_slot(i):
    """C++: calendar slot i from "title|HH:MM|calendar|#RRGGBB"; the bar's colour follows the calendar, and an empty
    value hides the slot ("Inget mer inlagt||" shows only the text)."""
    c = f"cal{i}"
    return (SPLIT + "while (p.size() < 4) p.push_back(\"\");\n"
            f"lv_label_set_text(id({c}_t), p[0].c_str());\n"
            f"lv_label_set_text(id({c}_time), p[1].c_str());\n"
            "uint32_t colour = calendar_color::parse(p[3]);\n"
            f"lv_obj_set_style_bg_color(id({c}_bar), lv_color_hex(colour), LV_PART_MAIN);\n"
            + shown(f"{c}_bar", "!p[2].empty()"))


# Swedish names for the clock_update lambda; ESPTime's day_of_week is 1 for Sunday.
CLOCK = r"""auto t = id(ha_time).now();
if (!t.is_valid()) return;
int m = t.hour * 60 + t.minute;
int on = id(night_start).hour * 60 + id(night_start).minute, off = id(night_end).hour * 60 + id(night_end).minute;
id(night_hours) = on > off ? (m >= on || m < off) : (m >= on && m < off);
static int shown = -1;
if (m == shown) return;
bool started = shown >= 0;   // not at boot: only when the clock reaches the time
shown = m;
if (started && m == on) id(night_auto).execute();
if (started && m == off && id(night)) id(night_off).execute();
static const char *const DAYS[] = {"söndag", "måndag", "tisdag", "onsdag", "torsdag", "fredag", "lördag"};
static const char *const MONTHS[] = {"januari", "februari", "mars", "april", "maj", "juni", "juli", "augusti",
                                     "september", "oktober", "november", "december"};
char hm[8], date[40];
snprintf(hm, sizeof(hm), "%02d:%02d", t.hour, t.minute);
snprintf(date, sizeof(date), "%s %d %s", DAYS[(t.day_of_week + 6) % 7], t.day_of_month, MONTHS[t.month - 1]);
lv_label_set_text(id(clock_lbl), hm);
lv_label_set_text(id(n_clock), hm);
lv_label_set_text(id(date_lbl), date);
lv_label_set_text(id(n_date), date);"""
NET = r"""if (id(ha_lost)) lv_label_set_text(id(net_lbl), "Ingen kontakt med Jarvis");
else if (id(net_down)) lv_label_set_text(id(net_lbl), "Nätet är nere – timers och larm fungerar");
""" + shown("net_pill", "id(ha_lost) || id(net_down)")


def live():
    """Home Assistant's sensors, the clock, night mode and Rensa: config keys for config()."""
    import tavla_earth
    page_show = lambda pid, ms: {"lvgl.page.show": {"id": pid, "animation": "FADE_IN", "time": ms}}
    is_night = {"lambda": "return id(night);"}
    sensors = [
        hemmet(None, text("h_status")),
        hemmet("alert", colour("h_status", 'x == "on"', ORANGE, WHITE)
               + "lv_obj_remove_flag(id(h_status_icon), LV_OBJ_FLAG_HIDDEN);\n",
               switch_image("h_status_icon", {"on": "s_warn"}, "s_ok")),
        hemmet("laundry_rooms", text("h_rooms", "")),
        # the dashboard: weather, metro and calendar
        hemmet("w_temp", text("w_temp") + text("wx_temp")),
        hemmet("w_line", text("w_line", "")),
        hemmet("w_tip", text("w_tip", "")),
        hemmet("w_icon", weather_page.ICON),
        *weather_page.sensors(_ui()),
        *calendar_page.sensors(_ui()),
        *reminders_page.sensors(_ui()),
        hemmet("sl_walk", text("sl_walk", "")),
        hemmet("sl_note", text("sl_note", "") + shown("sl_note_row", "!x.empty()")),
        *[hemmet(f"sl{i}", departure(i)) for i in range(4)],
        hemmet("cal_head", text("cal_head", "IDAG")),
        *[hemmet(f"cal{i}", calendar_slot(i)) for i in range(3)],
        hemmet("inside", text("h_inside")),
        hemmet("pm25", 'lv_label_set_text(id(h_pm25), x.empty() ? "" : ("PM2,5 " + x).c_str());\n'),
        hemmet("air", text("h_air", "") + colour("h_air", 'x.find("Dålig") != std::string::npos', ORANGE, GREEN)),
        hemmet("purifier", text("h_purifier")),
        hemmet("house", 'size_t bar = x.find(\'|\');\n'
                        'bool cut = x.empty() || bar == std::string::npos, problem = x.rfind("störning|", 0) == 0;\n'
                        'lv_label_set_text(id(h_house), cut ? "" : x.substr(bar + 1).c_str());\n'
                        + colour("h_house", "problem", ORANGE, WHITE)
                        + 'lv_obj_set_style_text_opa(id(h_house), problem ? 255 : 178, LV_PART_MAIN);\n'
                        + shown("h_house", "!cut")),
        hemmet("smoke", text("h_smoke")),
        hemmet("smoke_on", alarm("h_smoke")),
        hemmet("water", text("h_water")),
        hemmet("water_on", alarm("h_water")),
        hemmet("door", text("h_door") + colour("h_door_dot", 'x == "Öppen"', ORANGE, GREEN, "bg")
               + colour("h_door", 'x == "Öppen"', ORANGE, WHITE)
               + 'lv_obj_set_style_text_opa(id(h_door), x == "Öppen" ? 255 : 178, LV_PART_MAIN);\n'),
        hemmet("door_since", 'lv_label_set_text(id(h_door_sub), x.empty() ? "Ytterdörren" : '
                             '("Ytterdörren · " + x).c_str());\n'),
        hemmet("laundry", text("h_laundry")),
        hemmet("sun", text("h_sun")),
        hemmet("moon", text("h_moon")),
        hemmet("reminders", text("h_rem", "Inga påminnelser")
               + 'lv_obj_set_style_text_opa(id(h_rem), x.empty() ? 153 : 255, LV_PART_MAIN);\n'
               + 'lv_label_set_text(id(n_rem), x.substr(0, x.find(\'\\n\')).c_str());\n'),
        hemmet("online", 'id(net_down) = x == "off";\nid(net_show).execute();\n'),
        # The Jarvis card's Härnäst while its chat is empty (next_show)
        hemmet("next_up", 'lv_label_set_text(id(jv_next), x.c_str());\n', [{"script.execute": "next_show"}]),
        # Natt's next wakeup, and Snooze/Stopp by the tab bar while a wakeup is on (wake_strip)
        {"platform": "homeassistant", "id": "wake_next", "entity_id": WAKEUP, "attribute": "panel",
         "on_value": [{"lambda": 'lv_label_set_text(id(n_wake_lbl), x.c_str());\n' + shown("n_wake", "!x.empty()")}]},
        {"platform": "homeassistant", "id": "wake_on", "entity_id": WAKING, "on_value": [{"lambda":
            'bool on = x == "on";\n'
            'if (!on && id(wake_dismissed)) ESP_LOGI("wakeup", "Snooze/Stopp gone: the wakeup is over");\n'
            'if (!on) { id(wake_dismissed) = false; lv_label_set_text(id(wake_snooze_lbl), "Snooze"); }\n'
            'ESP_LOGI("wakeup", "Wakeup on: %s", on ? "yes" : "no");\n'
            + shown("wake_strip", "on && !id(wake_dismissed)")}]},
        # Tavla's plaque: NASA's title and credit; without a title the plaque goes
        hemmet("art_title", text("t_title", "") + shown("t_plaque", "!x.empty()")),
        hemmet("art_credit", text("t_credit", "") + shown("t_credit", "!x.empty()")),
        *tavla_earth.sensors(_ui()),
        {"platform": "homeassistant", "id": "chat_said", "entity_id": CHAT, "attribute": "last_said",
         "on_value": [{"lambda": bubble_text("you")}, {"script.execute": "next_show"}]},
        {"platform": "homeassistant", "id": "chat_reply", "entity_id": CHAT, "attribute": "last_reply",
         "on_value": [{"lambda": bubble_text("jv")}, {"script.execute": "next_show"}]},
        {"platform": "homeassistant", "id": "chat_earlier", "entity_id": CHAT, "attribute": "earlier",
         "on_value": [{"lambda": earlier_text()}, {"script.execute": "next_show"}]},
        # Inställningar's lists: Bakgrund, and Ljud ut (where the radio plays; the note back once Jarvis connected)
        *choice_sensors("bg", BAKGRUND, BG_NOTE),
        *choice_sensors("out", LJUD_UT, OUT_NOTE),
    ]
    flags = ["night", "night_hours", "net_down", "ha_lost", "h_smoke_alarm", "h_water_alarm", "mic_muted",
             "wake_dismissed"]
    scripts = [
        *choice_scripts("bg", BAKGRUND), *choice_scripts("out", LJUD_UT), reconnect_script("out", OUT_NOTE),
        *output_scripts(),
        {"id": "clock_update", "mode": "restart", "then": [{"lambda": CLOCK}]},
        {"id": "net_show", "mode": "restart", "then": [{"lambda": NET}]},
        # board.py adds the backlight and the speaker volume to these two
        {"id": "night_hw_on", "then": [{"logger.log": {"format": "Night mode on", "level": "DEBUG"}}]},
        {"id": "night_hw_off", "then": [{"logger.log": {"format": "Night mode off", "level": "DEBUG"}}]},
        {"id": "night_on", "mode": "restart", "then": [
            {"if": {"condition": {"lambda": "return !id(night);"},
                    "then": [{"lambda": "id(night) = true;"}, {"script.execute": "night_hw_on"}]}},
            {"script.execute": "settings_close"},
            page_show(NIGHT, "600ms")]},
        {"id": "night_off", "mode": "restart", "then": [
            {"if": {"condition": is_night,
                    "then": [{"lambda": "id(night) = false;"}, {"script.execute": "night_hw_off"}]}},
            *bar_back("FADE_IN", "300ms")]},
        # By itself (Natt börjar, or idle at night): never over the voice page or while an alarm is on.
        {"id": "night_auto", "then": [
            {"if": {"condition": {"lambda": f"return !id(night) && !id(h_smoke_alarm) && !id(h_water_alarm) && "
                                            f"lv_screen_active() != id({ORB})->obj;"},
                    "then": [{"script.execute": "night_on"}]}}]},
        # Smoke or a leak: full brightness and the Hemmet page, at any hour.
        {"id": "alarm_wake", "mode": "restart", "then": [
            {"if": {"condition": is_night,
                    "then": [{"lambda": "id(night) = false;"}, {"script.execute": "night_hw_off"}]}},
            {"script.execute": "settings_close"},
            page_show(HOME, "300ms")]},
        # Inställningar. board.py syncs the sliders when it opens and makes the four *_set scripts real.
        {"id": "settings_open", "mode": "restart", "then": [{"lvgl.widget.show": "settings"}]},
        {"id": "settings_close", "mode": "restart", "then": [{"lvgl.widget.hide": "settings"}]},
        # A background picked in Inställningar closes the sheet once its picture is on screen, so it shows at
        # once without Klar (Joel, 2026-11-02). Only a pick in the last BG_PICK_WINDOW_MS: NASA's new picture
        # or a change in Home Assistant leaves an open sheet alone. jarvis_orb.py runs it after bg repaints.
        {"id": "bg_shown", "then": [{"if": {
            "condition": {"lambda": f"return id(bg_picked_ms) && millis() - id(bg_picked_ms) < {BG_PICK_WINDOW_MS};"},
            "then": [{"lambda": "id(bg_picked_ms) = 0;"},
                     {"logger.log": {"format": "Settings: closed, the picked background is in", "level": "INFO"}},
                     {"script.execute": "settings_close"}]}}]},
        *[{"id": sid, "then": [{"logger.log": {"format": f"{sid} %d", "args": [f"id({sid.split('_')[0]}_pct)"],
                                               "level": "INFO"}}]}
          for sid in ("bright_preview", "bright_set", "nightl_preview", "nightl_set", "vol_set")],
        # The mic buttons. board.py fills mic_listen with the voice assistant and mic_toggle with the Mikrofon
        # switch; here (the simulator) they only show what the panel would do.
        {"id": "mic_tap", "then": [{"if": {"condition": {"lambda": "return id(mic_muted);"},
                                           "then": [{"script.execute": "mic_blink"}],
                                           "else": [{"script.execute": "mic_listen"}]}}]},
        {"id": "mic_listen", "then": [{"logger.log": {"format": "Mic tap: Jarvis would listen", "level": "INFO"}}]},
        {"id": "mic_toggle", "then": [{"lambda": "id(mic_muted) = !id(mic_muted);"}, {"script.execute": "mic_show"}]},
        # Muted: the buttons red with a slashed mic and the Jarvis card says so; a tap only blinks. No pill over the
        # clock: the red button already shows it (Joel, 2026-10-23).
        {"id": "mic_show", "mode": "restart", "then": [{"if": {
            "condition": {"lambda": "return id(mic_muted);"},
            "then": [{"lvgl.widget.update": {"id": [f"{k}_mic" for k in MIC_BUTTONS], "bg_opa": "COVER"}},
                     *[{"lvgl.image.update": {"id": f"{k}_mic_img", "src": "mic_off_icon"}} for k in MIC_BUTTONS],
                     {"lvgl.switch.update": {"id": "s_mic", "state": {"checked": False}}},
                     {"lvgl.label.update": {"id": "s_mic_sub", "text": "Av, Jarvis hör ingenting"}}],
            "else": [{"lvgl.widget.update": {"id": [f"{k}_mic" for k in MIC_BUTTONS], "bg_opa": "TRANSP"}},
                     *[{"lvgl.image.update": {"id": f"{k}_mic_img", "src": "mic_icon"}} for k in MIC_BUTTONS],
                     {"lvgl.switch.update": {"id": "s_mic", "state": {"checked": True}}},
                     {"lvgl.label.update": {"id": "s_mic_sub", "text": 'Lyssnar efter "hey Jarvis"'}}]}}]},
        {"id": "mic_blink", "mode": "restart", "then": [
            {"lvgl.widget.update": {"id": [f"{k}_mic" for k in MIC_BUTTONS], "opa": "40%"}},
            {"delay": "250ms"},
            {"lvgl.widget.update": {"id": [f"{k}_mic" for k in MIC_BUTTONS], "opa": "100%"}}]},
        {"id": "rensa", "mode": "restart", "then": [
            {"lvgl.widget.hide": ["you_row", "jv_row", *[f"e{i}_row" for i in range(EARLIER)]]},
            {"lvgl.label.update": {"id": "you_txt", "text": ""}},
            {"lvgl.label.update": {"id": "jv_txt", "text": ""}},
            {"script.execute": "next_show"},
            {"homeassistant.action": {"action": "jarvis_agent.clear"}}]},
        # Härnäst shows only while the chat has no bubble and there is something coming (2026-11-02).
        {"id": "next_show", "mode": "restart", "then": [{"lambda":
            shown("jv_next", "panel_chat::empty(id(chat_viewport)) && lv_label_get_text(id(jv_next))[0]")}]},
    ]
    return {
        "api": {},
        "text_sensor": sensors,
        "globals": [{"id": g, "type": "bool", "initial_value": "false"} for g in flags]
                   + [{"id": "bg_picked_ms", "type": "uint32_t", "initial_value": "0"}]
                   # Inställningar's sliders, where they were let go
                   + [{"id": f"{k}_pct", "type": "int", "initial_value": v}
                      for k, v in (("bright", "100"), ("nightl", "12"), ("vol", "50"))],
        "script": scripts,
        # The Mac's clock in the simulator; board.py turns this into Home Assistant's time. clock_update starts
        # and ends night mode at the two times.
        "time": [{"platform": "host", "id": "ha_time", "timezone": "Europe/Stockholm"}],
        "datetime": [{"platform": "template", "type": "time", "id": tid, "name": name, "optimistic": True,
                      "restore_value": True, "initial_value": start, "entity_category": "config",
                      "icon": "mdi:weather-night"}
                     for tid, name, start in (("night_start", "Natt börjar", "22:00:00"),
                                              ("night_end", "Natt slutar", "06:00:00"))],
        # The clock checks every 2 s and redraws only when the minute changed.
        "interval": [{"interval": "2s", "then": [{"script.execute": "clock_update"}]}],
        "on_idle": [{"timeout": "120s", "then": [
            {"if": {"condition": {"lambda": "return id(night_hours);"}, "then": [{"script.execute": "night_auto"}]}}]}],
    }


def config(bg, demo=False, start_orb=False, start=None):
    """start: "hemmet", "natt" or "tavla" to open on that page, "installningar" on the dashboard with Inställningar
    open (for a still); start_orb opens on the voice page."""
    orb_bg = bg.replace("assets/bg_", "assets/bg_orb_")
    art = bg.replace("assets/bg_", "assets/art_")
    import now_playing
    pages = [page, hemmet_page(), now_playing.page(sys.modules[__name__]), tavla_page(), natt_page(), orb_page(start_orb),
             weather_page.page(_ui()), calendar_page.page(_ui()), reminders_page.page(_ui()), ai_page.page(_ui())]
    first = ORB if start_orb else {"hemmet": HOME, "natt": NIGHT, "tavla": FRAME, "musik": MUSIC}.get(start, DASH)
    pages.sort(key=lambda p: p["id"] != first)
    esphome = {"name": "style-glass-v2"}
    lv = live()
    extra = {k: lv[k] for k in ("api", "text_sensor", "globals", "time", "datetime", "interval")}
    extra["script"] = lv["script"]
    if demo:
        esphome["on_boot"] = {"priority": -100, "then": [{"script.execute": "demo"}]}
        extra["script"] = lv["script"] + demo_script()
    cfg = {
        "esphome": esphome,
        **extra,
        "host": {},
        "logger": {"level": "WARN"},
        "display": [{"platform": "sdl", "id": "sdl_display", "auto_clear_enabled": False,
                     "update_interval": "never", "dimensions": {"width": 1024, "height": 600}}],
        "touchscreen": [{"platform": "sdl", "id": "sdl_touch"}],
        "font": [{"file": f"gfonts://Inter@{w}", "id": f(w, s), "size": s, "bpp": 4,
                  "glyphs": PLAQUE_GLYPHS if (w, s) in PLAQUE_FONTS | MUSIC_FONTS else GLYPHS}
                 for w, s in FONTS]
                + [{"file": "gfonts://Inter@700", "id": "clock_font", "size": 112, "bpp": 4,
                    "glyphs": list("0123456789:-")},
                   {"file": MDI, "id": "mdi_ui", "size": 24, "bpp": 4, "glyphs": list(ICONS.values())}],
        "image": [
            {"platform": "file", "file": bg, "id": "bg", "type": "RGB565"},
            {"platform": "file", "file": art, "id": "art", "type": "RGB565"},
            {"platform": "file", "file": "assets/mic.png", "id": "mic_icon", "type": "RGB565",
             "transparency": "alpha_channel"},
            {"platform": "file", "file": "assets/mic_small.png", "id": "mic_small", "type": "RGB565",
             "transparency": "alpha_channel"},
            # bake.py's draw_more_icons: Hemmet's cards and its status (the weather's icons are now Material
            # Design Icons glyphs, weather_page.py)
            *[{"platform": "file", "file": f"assets/{name}.png", "id": iid, "type": "RGB565",
               "transparency": "alpha_channel"}
              for name, iid in (("i_thermo", "i_thermo"),
                                ("i_shield", "i_shield"), ("i_bell", "i_bell"), ("i_washer", "i_washer"),
                                ("s_ok", "s_ok"), ("s_warn", "s_warn"), ("mic_off", "mic_off_icon"),
                                ("mic_off_small", "mic_off_small"))],
            {"platform": "file", "file": orb_bg, "id": "bg_orb", "type": "RGB565"},
        ] + [{"platform": "file", "file": f"assets/orb/orb_{i:02d}.png", "id": f"orb_{i:02d}", "type": "RGB565",
              "transparency": "alpha_channel"} for i in range(ORB_FRAMES)],
        "lvgl": {"color_depth": 16, "bg_color": "0x0B1020", "on_idle": lv["on_idle"], "pages": pages,
                 "top_layer": {"widgets": [{"obj": settings_layer(start == "installningar")}]}},
    }
    shared_bar(cfg)
    cfg.setdefault("interval", []).append({"interval": "100ms", "then": [{"lambda": '''
if(output_feedback::state.tick(millis())) {
  if(output_feedback::state.phase==output_feedback::Phase::TIMEOUT)
    ESP_LOGW("output", "request %u timed out; no automatic retry", unsigned(output_feedback::state.ticket));
  id(output_feedback_render).execute();
}
'''}]})
    now_playing.install(cfg, sys.modules[__name__])
    import tavla_earth
    tavla_earth.install(cfg)
    weather_page.install(cfg, MDI)
    ai_page.install(cfg, _ui())
    cfg["script"] = cfg.get("script", []) + reminders_page.scripts()
    return cfg


def start_page(argv):
    """The page after --page (hemmet, natt or tavla), or None."""
    return argv[argv.index("--page") + 1] if "--page" in argv else None


if __name__ == "__main__":
    start = start_page(sys.argv)
    args = [a for a in sys.argv[1:] if not a.startswith("--") and a != start]
    bg = args[0] if args else "assets/bg_starry.png"
    # JSON is valid YAML, so dump it as JSON and keep quoting simple.
    json.dump(config(bg, demo="--demo" in sys.argv, start_orb="--orb" in sys.argv, start=start), sys.stdout,
              ensure_ascii=False, indent=1)
