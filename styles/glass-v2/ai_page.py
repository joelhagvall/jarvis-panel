"""The AI page (Joel, 2026-11-01): how much of his AI subscriptions is used. Tokens today, this month, last
month and last month (cache reads included, so billions; no all-time total, since the logs reach back
unevenly), and per account how much of the 5-hour and 7-day limits
is gone, as bars. A tap on the Jarvis card's heading opens it, and so does asking Jarvis (the tool
ai_anvandning, server/agents/ai_usage.py: the page shows once he has finished speaking). A tap anywhere goes
back, like the weather page.

The numbers come from ~/jarvis-docs/scripts/bin/ai-usage.py on Jarvis (every 15 min), which also formats them:
sensor.ai_tokens's attribute `panel`, rows "a|b;c|d" (weather_view::table). The panel only fills labels and bars.

Memory: about 60 small LVGL objects, no pictures and no fonts of its own.
"""

PAGE = "page_ai"
ENTITY = "sensor.ai_tokens"
OPEN_ENTITY = "sensor.jarvis_open_page"   # "ai@<unix time>", written by server/agents/ai_usage.py
M = 40                                    # the page's side margin, as on the weather page
STATS = 3                                 # today, this month, last month; no all-time total (Joel, 2026-11-01)
STAT_W = (1024 - 2 * M) // STATS
ACCOUNTS = 3
ROW_Y, ROW_H = 262, 108
BAR_W = 250
ROW_W = 1024 - 2 * M
# The 5-hour and the 7-day bar, inside the account's row (which starts at M): the 7-day bar ends at its right
# edge. They were first given screen x and the 7-day bar was cut off on the panel (2026-11-01).
BAR_X = (ROW_W - 2 * BAR_W - 32, ROW_W - BAR_W)
BAR_CAPTIONS = ("5 timmar", "7 dagar")
# Each account's logo left of its name (Joel, 2026-11-01), in ai-usage.py's row order: Claude, Claude, Codex.
# 26 px PNGs from Simple Icons' SVGs in assets/logos/ (Claude in its orange, OpenAI white):
#   magick -background none -density 600 assets/logos/<n>.svg -resize 26x26 PNG32:assets/logo_<n>.png
LOGOS = ("logo_claude", "logo_claude", "logo_openai")
LOGO = 26


def stat(ui, i):
    return ui.plain(x=M + STAT_W * i, y=104, width=STAT_W, height=124, clickable=False, widgets=[
        ui.label(ui.sample(("Idag", "November", "Oktober")[i]), ui.f(500, 15), sec=True, id=f"ai_s{i}_cap",
                 x=0, y=0),
        ui.label(ui.sample(("6 M", "6 M", "34,4 B")[i], "–"), ui.f(400, 56), id=f"ai_s{i}_val", x=0, y=18),
        ui.label(ui.sample(("output 48 k", "output 48 k", "output 69 M")[i]), ui.f(500, 14),
                 sec=True, id=f"ai_s{i}_sub", x=0, y=92, width=STAT_W - 16, long_mode="DOT")])


def bar(ui, a, k):
    """Account a's bar k (0: 5 hours, 1: 7 days): caption, percent, track, fill and when it resets."""
    b = f"ai_a{a}_b{k}"
    return ui.plain(x=BAR_X[k], y=0, width=BAR_W, height=ROW_H - 20, clickable=False, widgets=[
        ui.label(BAR_CAPTIONS[k], ui.f(500, 13), sec=True, x=0, y=6),
        ui.label(ui.sample(("2 %", "27 %")[k], "–"), ui.f(600, 19), id=f"{b}_pct", align="TOP_RIGHT", x=0, y=0),
        ui.shape(ui.WHITE, BAR_W, 10, 5, x=0, y=30, bg_opa="14%", clickable=False),
        ui.shape(ui.GREEN, 10, 10, 5, x=0, y=30, id=f"{b}_fill", hidden=True, clickable=False),
        ui.label(ui.sample(("nollställs 11:29", "nollställs tors 05:59")[k]), ui.f(500, 13), sec=True,
                 id=f"{b}_when", x=0, y=50, width=BAR_W, long_mode="DOT")])


def account(ui, a):
    return ui.plain(x=M, y=ROW_Y + ROW_H * a, width=ROW_W, height=ROW_H - 20, clickable=False, widgets=[
        {"image": {"src": LOGOS[a], "x": 0, "y": 5, "clickable": False}},
        ui.label(ui.sample(("Claude 1", "Claude 2", "Codex")[a], "–"), ui.f(600, 22), id=f"ai_a{a}_name", x=LOGO + 10,
                 y=4),
        ui.label(ui.sample("Mac · nov 6 M · okt 28,1 B"), ui.f(500, 14), sec=True, id=f"ai_a{a}_sub", x=0, y=36,
                 width=BAR_X[0] - 24, long_mode="DOT"),
        bar(ui, a, 0), bar(ui, a, 1)])


def open_page():
    return [{"lvgl.page.show": {"id": PAGE, "animation": "FADE_IN", "time": "300ms"}}]


def page(ui):
    return {
        "id": PAGE, "bg_color": "0x141826", "bg_opa": "COVER", "pad_all": 0, "scrollable": False, "widgets": [
            # The page's own tap, under everything: back to the page with the bar, like Tavla.
            ui.plain(id="ai_back", x=0, y=0, width=1024, height=600, on_click=ui.bar_back("FADE_IN", "300ms"),
                     on_press=[{"lambda":'''static bool hooked=false;
if(!hooked){hooked=true;lv_obj_add_event_cb(id(ai_back),[](lv_event_t*){
  ui_update::text_opa(id(ai_heading),255,LV_PART_MAIN);
},LV_EVENT_PRESS_LOST,nullptr);}
ui_update::text_opa(id(ai_heading),153,LV_PART_MAIN);'''}],
                     on_release=[{"lambda":"ui_update::text_opa(id(ai_heading),255,LV_PART_MAIN);"}]),
            ui.label("AI-användning", ui.f(600, 30), id="ai_heading", x=M, y=30),
            ui.label(ui.sample("uppdaterad 07:28"), ui.f(500, 16), sec=True, id="ai_updated", align="TOP_RIGHT",
                     x=-M, y=42),
            *[stat(ui, i) for i in range(STATS)],
            ui.plain(x=M, y=ROW_Y - 22, width=1024 - 2 * M, height=1, bg_color=ui.WHITE, bg_opa="12%",
                     clickable=False),
            *[account(ui, a) for a in range(ACCOUNTS)]]}


def _ids(fmt, n):
    return ", ".join(fmt.format(i=i) for i in range(n))


# C++: fill the page from ai-usage.py's rows. A bar's fill is green, orange from 70 %, red from 90 %.
FILL = """auto rows = weather_view::table(x);
lv_obj_t *caps[""" + str(STATS) + "] = {" + _ids("id(ai_s{i}_cap)", STATS) + """};
lv_obj_t *vals[""" + str(STATS) + "] = {" + _ids("id(ai_s{i}_val)", STATS) + """};
lv_obj_t *subs[""" + str(STATS) + "] = {" + _ids("id(ai_s{i}_sub)", STATS) + """};
lv_obj_t *names[""" + str(ACCOUNTS) + "] = {" + _ids("id(ai_a{i}_name)", ACCOUNTS) + """};
lv_obj_t *lines[""" + str(ACCOUNTS) + "] = {" + _ids("id(ai_a{i}_sub)", ACCOUNTS) + """};
lv_obj_t *pcts[""" + str(ACCOUNTS) + "][2] = {" + ", ".join(
    f"{{id(ai_a{a}_b0_pct), id(ai_a{a}_b1_pct)}}" for a in range(ACCOUNTS)) + """};
lv_obj_t *fills[""" + str(ACCOUNTS) + "][2] = {" + ", ".join(
    f"{{id(ai_a{a}_b0_fill), id(ai_a{a}_b1_fill)}}" for a in range(ACCOUNTS)) + """};
lv_obj_t *whens[""" + str(ACCOUNTS) + "][2] = {" + ", ".join(
    f"{{id(ai_a{a}_b0_when), id(ai_a{a}_b1_when)}}" for a in range(ACCOUNTS)) + """};
auto bar = [](lv_obj_t *pct, lv_obj_t *fill, lv_obj_t *reset, const std::string &p, const std::string &note) {
  ui_update::text(reset, note.c_str());
  if (p.empty()) {
    ui_update::text(pct, "–");
    ui_update::visible(fill,false);
    return;
  }
  const int v = std::max(0, std::min(100, std::atoi(p.c_str())));
  ui_update::text(pct, (std::to_string(v) + " %").c_str());
  if (v == 0) { ui_update::visible(fill,false); return; }
  ui_update::width(fill, std::max(10, """ + str(BAR_W) + """ * v / 100));
  ui_update::bg_color(fill, lv_color_hex(v >= 90 ? 0xFF453A : v >= 70 ? 0xFF9F0A : 0x30D158), LV_PART_MAIN);
  ui_update::visible(fill,true);
};
int s = 0, a = 0;
for (auto &r : rows) {
  if (r.empty()) continue;
  if (r[0] == "u" && r.size() >= 2) {
    ui_update::text(id(ai_updated), r[1].c_str());
  } else if (r[0] == "s" && r.size() >= 4 && s < """ + str(STATS) + """) {
    ui_update::text(caps[s], r[1].c_str());
    ui_update::text(vals[s], r[2].c_str());
    ui_update::text(subs[s], r[3].c_str());
    ++s;
  } else if (r[0] == "a" && r.size() >= 7 && a < """ + str(ACCOUNTS) + """) {
    ui_update::text(names[a], r[1].c_str());
    ui_update::text(lines[a], r[2].c_str());
    bar(pcts[a][0], fills[a][0], whens[a][0], r[3], r[4]);
    bar(pcts[a][1], fills[a][1], whens[a][1], r[5], r[6]);
    ++a;
  }
}
"""

# C++: Jarvis asked for the page ("ai@<unix time>"). Only a request from the last two minutes counts, so the
# value Home Assistant sends at boot never opens it. During a voice turn it waits for the turn to end (WAIT).
OPEN = """const size_t at = x.find('@');
if (at == std::string::npos || x.substr(0, at) != "ai") return;
auto t = id(ha_time).now();
const long stamp = std::atol(x.c_str() + at + 1);
if (!t.is_valid() || std::labs((long) t.timestamp - stamp) > 120) return;
ESP_LOGI("ai", "Jarvis asked for the AI page");
if (lv_screen_active() == id(page_orb)->obj) panel_ai_jump::pending = true;
else id(ai_jump).execute();
"""


def sensors(ui):
    return [{"platform": "homeassistant", "id": "ai_panel", "entity_id": ENTITY, "attribute": "panel",
             "on_value": [{"lambda": FILL}]},
            {"platform": "homeassistant", "id": "ai_open", "entity_id": OPEN_ENTITY,
             "on_value": [{"lambda": OPEN}]}]


def _day(cfg):
    names = {g["id"] for g in cfg.get("globals", [])}
    return "!id(night)" + (" && !id(night_hours)" if "night_hours" in names else "")


def _jump(cfg, animation=None):
    """The script ai_jump: show the page from a page with the tab bar, never at night. The board shows pages
    without animation (board_config/pages.py), so board() passes "NONE"."""
    scripts = {s["id"]: s for s in cfg["script"]}
    close = [{"script.execute": "settings_close"}] if "settings_close" in scripts else []
    show = open_page() if animation is None else [{"lvgl.page.show": {"id": PAGE, "animation": animation}}]
    here = " || ".join(f"lv_screen_active() == id({p})->obj" for p in ("page_glass_v2", "page_hemmet")
                       if any(x["id"] == p for x in cfg["lvgl"]["pages"]))
    return [{"if": {"condition": {"lambda": f"return ({here}) && {_day(cfg)};"}, "then": [*close, *show]}}]


def install(cfg, ui):
    """gen.py: the sensors, ai_jump.h and the script ai_jump."""
    from pathlib import Path
    cfg["text_sensor"].extend(sensors(ui))
    cfg["image"].extend({"platform": "file", "file": f"assets/{n}.png", "id": n, "type": "RGB565",
                         "transparency": "alpha_channel"} for n in sorted(set(LOGOS)))
    cfg["esphome"].setdefault("includes", []).append(str(Path(__file__).with_name("ai_jump.h")))
    cfg["script"].append({"id": "ai_jump", "mode": "single", "then": _jump(cfg)})


def board(cfg):
    """board.py, after now_playing.board: night_hours joins the night check, and every place a voice turn ends
    (voice_done right away and after its settle, orb_leave) shows this page when Jarvis asked for it during the
    turn. It hooks into the other branch of Spelas nu's check (now_playing.jump_after_voice's take()), so music
    Jarvis started still wins, and the turn's structure stays as test_music_jump expects."""
    scripts = {s["id"]: s for s in cfg["script"]}
    scripts["ai_jump"]["then"] = _jump(cfg, "NONE")
    show = scripts["ai_jump"]["then"][0]["if"]["then"]
    music = "panel_music_jump::take()"
    hooked = 0

    def walk(actions):
        nonlocal hooked
        for a in actions:
            if not isinstance(a, dict) or "if" not in a:
                continue
            branch = a["if"]
            if music in branch.get("condition", {}).get("lambda", ""):
                rest = branch.get("else", [])
                walk(rest)
                branch["else"] = [{"if": {"condition": {"lambda": f"return panel_ai_jump::take() && {_day(cfg)};"},
                                          "then": list(show), "else": rest}}]
                hooked += 1
            else:
                walk(branch.get("then", []))
                walk(branch.get("else", []))
    for name in ("voice_done", "orb_leave"):
        if name in scripts:
            walk(scripts[name]["then"])
    return hooked
