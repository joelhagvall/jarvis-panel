"""The reminder page (Joel, 2026-11-03): a tap on Hemmet's "Påminnelser och timers" card opens every running timer
and every pending reminder, as the calendar card opens the week (calendar_page.py).

Left: up to four timers, shortest first, each with **+1 min** and **Avbryt**, and **5/10/15 min** under them to
start one. Right: up to nine reminders, soonest first, "+N till" past them. A tap on a reminder shows it whole over
the page with **Ta bort**; a tap beside the card closes it. A tap anywhere else goes back, like the calendar page.

Data: the reminders are sensor.jarvis_hemmet's `rem_list` (server/reminders.py `panel_rows`), the timers the
panel's own Assist timers (board_config/voice.py fills the rows with TIMER_ROWS on every timer tick). Ta bort,
+1 min and one timer's Avbryt send the exact id through reminders_view.h's call(); creating reminders stays with the
voice. Memory: about 90 small LVGL objects, no pictures and no fonts of its own.
"""

PAGE = "page_paminnelser"
M = 40                                   # the side margin, as on the calendar page
COL_W = 452
COL_X = (M, 1024 - M - COL_W)
TOP = 84
ROWS = 9                                 # reminders_view::ROWS, server/reminders.py PANEL_ROWS
ROW_H = 44
TIMERS = 4                               # reminders_view::TIMERS
TIMER_H = 76
WHEN_W = 124
QUICK = (5, 10, 15)                      # gen.QUICK_TIMERS
ORANGE, RED = "0xFF9F0A", "0xFF453A"


def button(ui, key, text, on_click, width=92, color=None):
    """A pill like Hemmet's quick timers (gen.timer_button), with its own actions."""
    return {"button": {"id": key, "width": width, "height": 44, "radius": 22, "bg_color": color or ui.WHITE,
                       "bg_opa": "85%" if color else "16%", "border_width": 0, "shadow_opa": "TRANSP", "pad_all": 0,
                       "pressed": {"bg_color": color or ui.WHITE, "bg_opa": "100%" if color else "34%"},
                       "widgets": [ui.label(text, ui.f(600, 15), align="CENTER")], "on_click": on_click}}


def placed(b, **where):
    """A button with its position."""
    return {"button": {**b["button"], **where}}


def call(action, data):
    """C++: one Home Assistant action with ids the panel holds (reminders_view.h)."""
    pairs = ", ".join(f'{{"{k}", {v}}}' for k, v in data.items())
    return {"lambda": f'reminders_view::call("jarvis_agent.{action}", {{{pairs}}});'}


def timer_row(ui, i):
    """Timer i: its name, the time left in orange, +1 min and Avbryt."""
    s = f"rm_t{i}"
    log = lambda what: {"logger.log": {"format": f"Reminder page: timer {i} {what}", "level": "INFO"}}
    return ui.plain(id=s, x=0, y=34 + i * TIMER_H, width=COL_W, height=TIMER_H - 8, hidden=True, clickable=False,
                    widgets=[
        ui.label("", ui.f(500, 16), sec=True, id=f"{s}_name", x=0, y=0, width=COL_W - 210, height=20, long_mode="DOT",
                 clickable=False),
        ui.label("", ui.f(600, 30), ORANGE, id=f"{s}_left", x=0, y=24, clickable=False),
        placed(button(ui, f"{s}_add", "+1 min", [log("+1 min"), call("timer_add", {
            "timer_id": f"reminders_view::timer_id[{i}]", "seconds": 'std::string("60")'})]), align="RIGHT_MID", x=-100),
        placed(button(ui, f"{s}_stop", "Avbryt", [log("Avbryt"), call("timer_cancel", {
            "timer_id": f"reminders_view::timer_id[{i}]"})]), align="RIGHT_MID", x=0)])


def quick(ui, m):
    """Start one more timer; the same action as Hemmet's quick timers."""
    act = {"action": "jarvis_agent.timer_start", "data": {"minutes": str(m)},
           "on_error": [{"logger.log": {"format": f"Reminder page: {m} min refused", "level": "WARN"}}]}
    return button(ui, f"rm_quick_{m}", f"{m} min", [{"logger.log": {"format": f"Reminder page: {m} min",
                                                                    "level": "INFO"}}, {"homeassistant.action": act}])


def timers(ui):
    return ui.plain(x=COL_X[0], y=TOP, width=COL_W, height=600 - TOP - 20, clickable=False, widgets=[
        ui.label("Timers", ui.f(600, 19), clickable=False, x=0, y=0),
        ui.label("Ingen timer igång", ui.f(500, 16), sec=True, id="rm_t_none", x=0, y=36, clickable=False),
        *[timer_row(ui, i) for i in range(TIMERS)],
        ui.label("Ny timer", ui.f(500, 15), sec=True, x=0, y=34 + TIMERS * TIMER_H + 8, clickable=False),
        ui.plain(x=0, y=34 + TIMERS * TIMER_H + 34, width="SIZE_CONTENT", height=44, clickable=False,
                 layout={"type": "flex", "flex_flow": "ROW", "pad_column": 10},
                 widgets=[quick(ui, m) for m in QUICK])])


def reminder_row(ui, k):
    """Reminder k: when and what; a tap shows it whole (rm_show)."""
    s = f"rm_r{k}"
    return ui.plain(id=s, width=COL_W, height=ROW_H, hidden=True, radius=10,
                    pressed={"bg_color": ui.WHITE, "bg_opa": "10%"},
                    on_click=[{"script.execute": {"id": "rm_show", "i": k}}], widgets=[
        ui.label("", ui.f(500, 15), sec=True, id=f"{s}_when", x=8, y=12, width=WHEN_W, height=20, long_mode="DOT",
                 clickable=False),
        ui.label("", ui.f(600, 16), id=f"{s}_text", x=8 + WHEN_W, y=11, width=COL_W - 16 - WHEN_W, height=22,
                 long_mode="DOT", clickable=False)])


def reminders(ui):
    return ui.plain(x=COL_X[1], y=TOP, width=COL_W, height=600 - TOP - 20, clickable=False, widgets=[
        ui.label("Påminnelser", ui.f(600, 19), clickable=False, x=0, y=0),
        ui.label("Inga påminnelser", ui.f(500, 16), sec=True, id="rm_none", x=0, y=36, clickable=False),
        ui.plain(x=0, y=26, width=COL_W, height="SIZE_CONTENT", clickable=False,
                 layout={"type": "flex", "flex_flow": "COLUMN", "pad_row": 0},
                 widgets=[*[reminder_row(ui, k) for k in range(ROWS)],
                          ui.label("", ui.f(500, 15), sec=True, id="rm_more", hidden=True, clickable=False,
                                   pad_left=8, pad_top=8)])])


def detail(ui):
    """One reminder whole, over a dimmed page, with Ta bort; a tap beside the buttons closes it."""
    remove = [{"lambda": '''if (reminders_view::open_id.empty()) return;
ESP_LOGI("reminders", "Ta bort: reminder %s", reminders_view::open_id.c_str());
lv_obj_t *rows[] = {''' + _list("id(rm_r{k})", ROWS) + '''};
if (reminders_view::call("jarvis_agent.reminder_cancel", {{"id", reminders_view::open_id}}))
  for (int k = 0; k < reminders_view::ROWS; ++k)       // dimmed until Home Assistant's next rem_list
    if (reminders_view::rem_id[k] == reminders_view::open_id) ui_update::opacity(rows[k], 90);
reminders_view::open_id.clear();'''}, {"lvgl.widget.hide": "rm_detail"}]
    card = ui.plain(align="CENTER", width=640, height="SIZE_CONTENT", radius=28, bg_color="0x1F2433", bg_opa="COVER",
                    border_width=1, border_color=ui.WHITE, border_opa="14%", pad_all=28, clickable=False,
                    layout={"type": "flex", "flex_flow": "COLUMN", "pad_row": 12},
                    widgets=[ui.label("", ui.f(500, 16), sec=True, id="rm_d_when", clickable=False),
                             ui.label("", ui.f(600, 22), id="rm_d_text", width=584, clickable=False),
                             ui.plain(width=584, height=56, clickable=False, pad_top=12, widgets=[
                                 button(ui, "rm_d_remove", "Ta bort", remove, width=120, color=RED),
                                 placed(button(ui, "rm_d_close", "Stäng", [{"lvgl.widget.hide": "rm_detail"},
                                        {"lambda": "reminders_view::open_id.clear();"}], width=120), x=136)])])
    return ui.plain(id="rm_detail", x=0, y=0, width=1024, height=600, bg_color="0x000000", bg_opa="55%", hidden=True,
                    widgets=[card], on_click=[{"lvgl.widget.hide": "rm_detail"},
                                              {"lambda": "reminders_view::open_id.clear();"}])


def open_page():
    return [{"lvgl.widget.hide": "rm_detail"}, {"lambda": "reminders_view::open_id.clear();"},
            {"lvgl.page.show": {"id": PAGE, "animation": "FADE_IN", "time": "300ms"}}]


def page(ui):
    return {
        "id": PAGE, "bg_color": "0x141826", "bg_opa": "COVER", "pad_all": 0, "scrollable": False, "widgets": [
            # The page's own tap, under everything: back to the page with the bar, like the calendar page.
            ui.plain(id="rm_back", x=0, y=0, width=1024, height=600, on_click=ui.bar_back("FADE_IN", "300ms"),
                     on_press=[{"lambda": '''static bool hooked=false;
if(!hooked){hooked=true;lv_obj_add_event_cb(id(rm_back),[](lv_event_t*){
  ui_update::text_opa(id(rm_heading),255,LV_PART_MAIN);
},LV_EVENT_PRESS_LOST,nullptr);}
ui_update::text_opa(id(rm_heading),153,LV_PART_MAIN);'''}],
                     on_release=[{"lambda": "ui_update::text_opa(id(rm_heading),255,LV_PART_MAIN);"}]),
            ui.label("Påminnelser och timers", ui.f(600, 30), id="rm_heading", x=M, y=30),
            timers(ui),
            ui.plain(x=COL_X[1] - 24, y=TOP, width=1, height=480, bg_color=ui.WHITE, bg_opa="12%", clickable=False),
            reminders(ui),
            detail(ui)]}


def _list(fmt, n):
    return ", ".join(fmt.format(k=k) for k in range(n))


# C++: the reminder rows from rem_list ("r|id|when|text|u" and "m|N"); a late delivery's time in orange. The open
# card closes when its reminder is gone (said, or removed by voice).
REMINDERS = """auto rows = weather_view::table(x);
lv_obj_t *slots[""" + str(ROWS) + "] = {" + _list("id(rm_r{k})", ROWS) + """};
lv_obj_t *whens[""" + str(ROWS) + "] = {" + _list("id(rm_r{k}_when)", ROWS) + """};
lv_obj_t *texts[""" + str(ROWS) + "] = {" + _list("id(rm_r{k}_text)", ROWS) + """};
int k = 0;
std::string more;
for (auto &r : rows) {
  if (r.empty()) continue;
  if (r[0] == "m") { more = r.size() > 1 ? "+" + r[1] + " till" : ""; continue; }
  if (r[0] != "r" || k >= reminders_view::ROWS) continue;
  while (r.size() < 5) r.push_back("");
  reminders_view::rem_id[k] = r[1];
  reminders_view::rem_when[k] = r[2];
  reminders_view::rem_text[k] = r[3];
  ui_update::text(whens[k], r[2].c_str());
  ui_update::text_color(whens[k], lv_color_hex(r[4] == "1" ? 0xFF9F0A : 0xFFFFFF), LV_PART_MAIN);
  ui_update::text(texts[k], r[3].c_str());
  ui_update::opacity(slots[k], 255);
  ui_update::visible(slots[k], true);
  ++k;
}
bool open = reminders_view::open_id.empty();
for (int j = 0; j < reminders_view::ROWS; ++j) {
  if (j >= k) { reminders_view::rem_id[j].clear(); ui_update::visible(slots[j], false); }
  else if (reminders_view::rem_id[j] == reminders_view::open_id) open = true;
}
if (!open) { lv_obj_add_flag(id(rm_detail), LV_OBJ_FLAG_HIDDEN); reminders_view::open_id.clear(); }
ui_update::text(id(rm_more), more.c_str());
ui_update::visible(id(rm_more), !more.empty());
ui_update::visible(id(rm_none), k == 0);
"""

# The tap on reminder i: it whole, over the page.
SHOW = """if (i < 0 || i >= reminders_view::ROWS || reminders_view::rem_id[i].empty()) return;
reminders_view::open_id = reminders_view::rem_id[i];
lv_label_set_text(id(rm_d_when), reminders_view::rem_when[i].c_str());
lv_label_set_text(id(rm_d_text), reminders_view::rem_text[i].c_str());
lv_obj_remove_flag(id(rm_detail), LV_OBJ_FLAG_HIDDEN);
ESP_LOGI("reminders", "Reminder shown whole: row %d", i);
"""

# C++ for board_config/voice.py, on every timer tick and change: the panel's timers, shortest first.
TIMER_ROWS = """{
const auto &all = id(assist).get_timers();
std::vector<const voice_assistant::Timer *> ts;
for (const auto &t : all) ts.push_back(&t);
std::sort(ts.begin(), ts.end(), [](auto *a, auto *b) { return a->seconds_left < b->seconds_left; });
lv_obj_t *rows[""" + str(TIMERS) + "] = {" + _list("id(rm_t{k})", TIMERS) + """};
lv_obj_t *names[""" + str(TIMERS) + "] = {" + _list("id(rm_t{k}_name)", TIMERS) + """};
lv_obj_t *lefts[""" + str(TIMERS) + "] = {" + _list("id(rm_t{k}_left)", TIMERS) + """};
for (int i = 0; i < reminders_view::TIMERS; ++i) {
  if (i >= (int) ts.size()) { reminders_view::timer_id[i].clear(); ui_update::visible(rows[i], false); continue; }
  const auto *t = ts[i];
  reminders_view::timer_id[i] = t->id;
  std::string name = t->name.empty() ? std::string("Timer") : t->name;
  name[0] = toupper((unsigned char) name[0]);
  uint32_t s = t->seconds_left;
  char left[24];
  if (s >= 3600)
    snprintf(left, sizeof(left), "%u:%02u:%02u", (unsigned) (s / 3600), (unsigned) (s / 60 % 60), (unsigned) (s % 60));
  else
    snprintf(left, sizeof(left), "%u:%02u", (unsigned) (s / 60), (unsigned) (s % 60));
  ui_update::text(names[i], name.c_str());
  ui_update::text(lefts[i], (std::string(left) + (t->is_active ? "" : " (paus)")).c_str());
  ui_update::visible(rows[i], true);
}
ui_update::visible(id(rm_t_none), ts.empty());
}
"""


def sensors(ui):
    return [ui.hemmet("rem_list", REMINDERS)]


def scripts():
    return [{"id": "rm_show", "parameters": {"i": "int"}, "then": [{"lambda": SHOW}]}]
