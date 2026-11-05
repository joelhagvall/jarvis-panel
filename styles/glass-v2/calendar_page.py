"""The calendar page (Joel, 2026-10-30): a tap on the dashboard's calendar card opens the next seven days. Today
and tomorrow fill the left column, the five days after them the right one; a tap anywhere goes back, like the
weather page. server/agents/calendar_week.py fills cal_week and decides what fits; the panel only fills rows.

A tap on an event shows it whole over the page (2026-11-02): its day and time, the full title and its calendar;
a tap anywhere closes it. Since 2026-11-04 the card also shows what iCloud holds about it (Joel: "vill ju ha koll"):
its alarms as the times they ring, place, how it repeats, notes and link, each row with its icon and hidden when
empty. calendar_week.py writes the text; the card only shows it.

When the last read failed, the week number's corner says "Kunde inte uppdatera · senast 14:05" in orange instead,
over the older events (GPT review, 2026-11-02).

Memory: about 160 small LVGL objects, no pictures and no fonts of its own.
"""

PAGE = "page_kalender"
M = 40                                   # the page's side margin, as on the weather page
COL_W = 452
COL_X = (M, 1024 - M - COL_W)
TOP = 84
CAPS = (6, 6, 3, 3, 3, 3, 3)             # calendar_week.CAPS
SLOT_H = 30
TIME_W = 112


def slot(ui, d, k):
    """Day d's row k: a calendar-coloured bar, the time (or "heldag") and the title."""
    s = f"cw{d}_{k}"
    # Not clickable until WEEK has hooked its tap (DETAIL); pressed: a faint highlight under the finger.
    return ui.plain(id=s, width=COL_W, height=SLOT_H, hidden=True, clickable=False, radius=8,
                    pressed={"bg_color": ui.WHITE, "bg_opa": "10%"}, widgets=[
        ui.shape(ui.WHITE, 4, 22, 2, x=0, y=4, id=f"{s}_bar", clickable=False),
        ui.label("", ui.f(500, 14), sec=True, id=f"{s}_time", x=14, y=6, width=TIME_W, height=18, long_mode="DOT"),
        # DOT needs a fixed height, or a long title grows to a second line (gen.py's event()).
        ui.label("", ui.f(600, 16), id=f"{s}_title", x=14 + TIME_W, y=4, width=COL_W - 14 - TIME_W, height=20,
                 long_mode="DOT")])


def day(ui, d):
    head = ui.plain(width=COL_W, height="SIZE_CONTENT", clickable=False, pad_bottom=2,
                    layout={"type": "flex", "flex_flow": "ROW", "pad_column": 10, "flex_align_cross": "END"},
                    widgets=[ui.label("", ui.f(600, 19), id=f"cw{d}_day", clickable=False),
                             ui.label("", ui.f(500, 15), sec=True, id=f"cw{d}_date", clickable=False)])
    return ui.plain(id=f"cw{d}", width=COL_W, height="SIZE_CONTENT", hidden=True, clickable=False,
                    layout={"type": "flex", "flex_flow": "COLUMN", "pad_row": 2},
                    widgets=[head, *[slot(ui, d, k) for k in range(CAPS[d])]])


def column(ui, x, days):
    return ui.plain(x=x, y=TOP, width=COL_W, height=600 - TOP - 20, clickable=False,
                    layout={"type": "flex", "flex_flow": "COLUMN", "pad_row": 14},
                    widgets=[day(ui, d) for d in days])


def open_page():
    return [{"lvgl.widget.hide": "cw_detail"}, {"lvgl.page.show": {"id": PAGE, "animation": "FADE_IN", "time": "300ms"}}]


# The card's iCloud rows, in calendar_week's field order after the calendar: (id, icon). The url is one line.
INFO = (("place", "place"), ("repeat", "repeat"), ("alarms", "bell"), ("notes", "notes"), ("url", "link"))
CARD_W = 584


def info_row(ui, key, icon):
    """An icon and its text, which wraps (the url is cut); hidden until a tapped event has the field."""
    one_line = {"height": 20, "long_mode": "DOT"} if key == "url" else {}
    return ui.plain(id=f"cw_d_{key}_row", width=CARD_W, height="SIZE_CONTENT", hidden=True, clickable=False, widgets=[
        ui.label(ui.ICONS[icon], "mdi_ui", ui.WHITE, text_opa="60%", x=-3, y=-3, clickable=False),
        ui.label("", ui.f(500, 16), id=f"cw_d_{key}", x=34, y=0, width=CARD_W - 34, clickable=False, **one_line)])


def detail(ui):
    """One event whole, over a dimmed page; DETAIL fills it from the row that was tapped. A tap closes it."""
    card = ui.plain(align="CENTER", width=640, height="SIZE_CONTENT", radius=28, bg_color="0x1F2433", bg_opa="COVER",
                    border_width=1, border_color=ui.WHITE, border_opa="14%", pad_all=28, clickable=False,
                    layout={"type": "flex", "flex_flow": "COLUMN", "pad_row": 12},
                    widgets=[ui.label("", ui.f(500, 16), sec=True, id="cw_d_when", clickable=False),
                             ui.label("", ui.f(600, 22), id="cw_d_title", width=CARD_W, clickable=False),
                             ui.plain(width=CARD_W, height=22, clickable=False, widgets=[
                                 ui.shape(ui.WHITE, 4, 18, 2, x=0, y=2, id="cw_d_bar", clickable=False),
                                 ui.label("", ui.f(500, 16), sec=True, x=14, y=1, id="cw_d_cal", clickable=False)]),
                             *[info_row(ui, key, icon) for key, icon in INFO]])
    return ui.plain(id="cw_detail", x=0, y=0, width=1024, height=600, bg_color="0x000000", bg_opa="55%", hidden=True,
                    widgets=[card], on_click=[{"lvgl.widget.hide": "cw_detail"}])


def page(ui):
    return {
        "id": PAGE, "bg_color": "0x141826", "bg_opa": "COVER", "pad_all": 0, "scrollable": False, "widgets": [
            # The page's own tap, under everything: back to the page with the bar, like Tavla.
            ui.plain(id="cw_back", x=0, y=0, width=1024, height=600, on_click=ui.bar_back("FADE_IN", "300ms"),
                     on_press=[{"lambda":'''static bool hooked=false;
if(!hooked){hooked=true;lv_obj_add_event_cb(id(cw_back),[](lv_event_t*){
  ui_update::text_opa(id(cw_heading),255,LV_PART_MAIN);
},LV_EVENT_PRESS_LOST,nullptr);}
ui_update::text_opa(id(cw_heading),153,LV_PART_MAIN);'''}],
                     on_release=[{"lambda":"ui_update::text_opa(id(cw_heading),255,LV_PART_MAIN);"}]),
            ui.label("Kalender", ui.f(600, 30), id="cw_heading", x=M, y=30),
            ui.label("", ui.f(500, 16), sec=True, id="cw_week", align="TOP_RIGHT", x=-M, y=42),
            column(ui, COL_X[0], range(2)),
            ui.plain(x=COL_X[1] - 24, y=TOP, width=1, height=480, bg_color=ui.WHITE, bg_opa="12%", clickable=False),
            column(ui, COL_X[1], range(2, 7)),
            detail(ui)]}


def _ids(fmt):
    return ", ".join(fmt.format(d=d, k=k) if k < CAPS[d] else "nullptr" for d in range(7) for k in range(6))


# C++: fill the days from cal_week's rows (calendar_week.py's format); a day the rows do not reach is hidden.
WEEK = """auto rows = weather_view::table(x);
lv_obj_t *blocks[7] = {""" + ", ".join(f"id(cw{d})" for d in range(7)) + """};
lv_obj_t *names[7] = {""" + ", ".join(f"id(cw{d}_day)" for d in range(7)) + """};
lv_obj_t *dates[7] = {""" + ", ".join(f"id(cw{d}_date)" for d in range(7)) + """};
lv_obj_t *slots[7][6] = {""" + _ids("id(cw{d}_{k})") + """};
lv_obj_t *bars[7][6] = {""" + _ids("id(cw{d}_{k}_bar)") + """};
lv_obj_t *times[7][6] = {""" + _ids("id(cw{d}_{k}_time)") + """};
lv_obj_t *titles[7][6] = {""" + _ids("id(cw{d}_{k}_title)") + """};
const int caps[7] = {""" + ", ".join(map(str, CAPS)) + """};
// What a tap on a row shows (DETAIL): its day and time, title, calendar and colour, then place, repeat, alarms,
// notes and url (INFO); empty for a note row.
static std::string detail[7][6][9] EXT_RAM_BSS_ATTR;   // 10 kB of strings, cold: PSRAM (calendar_color.h)
std::string stale, day_text;
int used[7]{};
int d = -1, k = 0;
for (auto &r : rows) {
  if (r.empty()) continue;
  if (r[0] == "s") { stale = r.size() > 1 ? r[1] : ""; continue; }
  if (r[0] == "h") {
    if (++d >= 7) break;
    k = 0;
    day_text = (r.size() > 1 ? r[1] : "") + (r.size() > 2 ? " " + r[2] : "");
    ui_update::text(names[d], r.size() > 1 ? r[1].c_str() : "");
    ui_update::text(dates[d], r.size() > 2 ? r[2].c_str() : "");
    ui_update::visible(blocks[d],true);
    continue;
  }
  if (d < 0 || k >= caps[d]) continue;
  while (r.size() < 11) r.push_back("");
  const bool event = r[0] == "e" || r[0] == "a";
  lv_obj_t *s = slots[d][k++];
  used[d]=k;
  auto &info = detail[d][k - 1];
  info[0] = event ? r[2] : "";
  info[1] = event ? day_text + " · " + r[1] : "";
  info[2] = event ? r[5] : "";
  info[3] = event ? r[3] : "";
  for (int f = 0; f < 5; ++f) info[4 + f] = event ? r[6 + f] : "";
  ui_update::text(times[d][k - 1], event ? r[1].c_str() : "");
  ui_update::text(titles[d][k - 1], event ? r[2].c_str() : r[1].c_str());
  ui_update::text_opa(titles[d][k - 1], event ? 255 : 153, LV_PART_MAIN);
  ui_update::bg_color(bars[d][k - 1], lv_color_hex(calendar_color::parse(r[3])), LV_PART_MAIN);
  if (event) ui_update::visible(bars[d][k - 1],true); else ui_update::visible(bars[d][k - 1],false);
  // "Inget inlagt" and "+2 till" start where the titles do, under the times
  ui_update::x(titles[d][k - 1], event ? """ + str(14 + TIME_W) + """ : 0);
  ui_update::opacity(s,r[4] == "1" ? 115 : 255);
  ui_update::visible(s,true);
}
for(int day=0;day<7;++day) {
  if(day>d)ui_update::visible(blocks[day],false);
  for(int row=used[day];row<caps[day];++row)ui_update::visible(slots[day][row],false);
}
"""

# The tap on an event's row (hooked once, after WEEK has its arrays): the event whole, over the page.
DETAIL = """static bool hooked = false;
if (!hooked) {
  hooked = true;
  for (int day = 0; day < 7; ++day)
    for (int row = 0; row < caps[day]; ++row) {
      lv_obj_add_flag(slots[day][row], LV_OBJ_FLAG_CLICKABLE);
      lv_obj_add_event_cb(slots[day][row], [](lv_event_t *e) {
        const int i = (int) (intptr_t) lv_event_get_user_data(e);
        const auto &info = detail[i / 6][i % 6];
        if (info[0].empty()) return;          // "Inget inlagt", "+2 till"
        lv_label_set_text(id(cw_d_when), info[1].c_str());
        lv_label_set_text(id(cw_d_title), info[0].c_str());
        lv_label_set_text(id(cw_d_cal), info[2].c_str());
        lv_obj_set_style_bg_color(id(cw_d_bar), lv_color_hex(calendar_color::parse(info[3])), LV_PART_MAIN);
        if (info[2].empty()) lv_obj_add_flag(id(cw_d_bar), LV_OBJ_FLAG_HIDDEN);
        else lv_obj_remove_flag(id(cw_d_bar), LV_OBJ_FLAG_HIDDEN);
        lv_obj_t *texts[5] = {""" + ", ".join(f"id(cw_d_{k})" for k, _ in INFO) + """};
        lv_obj_t *rows[5] = {""" + ", ".join(f"id(cw_d_{k}_row)" for k, _ in INFO) + """};
        for (int f = 0; f < 5; ++f) {
          lv_label_set_text(texts[f], info[4 + f].c_str());
          if (info[4 + f].empty()) lv_obj_add_flag(rows[f], LV_OBJ_FLAG_HIDDEN);
          else lv_obj_remove_flag(rows[f], LV_OBJ_FLAG_HIDDEN);
        }
        lv_obj_remove_flag(id(cw_detail), LV_OBJ_FLAG_HIDDEN);
        ESP_LOGI("calendar", "Event shown whole: day %d row %d", i / 6, i % 6);
      }, LV_EVENT_CLICKED, (void *) (intptr_t) (day * 6 + row));
    }
}
"""

# The corner: the week number from the panel's own clock (ISO week, as Swedish calendars count), or in orange that
# the calendar could not be read since the time it says (the "s" row).
WEEK_NUMBER = """ui_update::text_color(id(cw_week), lv_color_hex(stale.empty() ? 0xFFFFFF : 0xFF9F0A), LV_PART_MAIN);
ui_update::text_opa(id(cw_week), stale.empty() ? 153 : 255, LV_PART_MAIN);
if (!stale.empty()) {
  ui_update::text(id(cw_week), stale.c_str());
  return;
}
auto t = id(ha_time).now();
if (!t.is_valid()) return;
char text[16];
struct tm tm = t.to_c_tm();
strftime(text, sizeof text, "vecka %V", &tm);
ui_update::text(id(cw_week), text);
"""


def sensors(ui):
    """sensor.jarvis_hemmet's cal_week; the tap on a row and the corner follow the same update."""
    return [ui.hemmet("cal_week", WEEK + DETAIL + "{\n" + WEEK_NUMBER + "}\n")]
