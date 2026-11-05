"""The weather (Joel, 2026-10-29; design: sketch/weather_mock.py): the dashboard card gets SMHI's next three days
under a thin line, and a tap on it opens a page with now, the next 24 hours as a curve with rain bars, and
seven days. A tap anywhere on the page goes back, like Tavla and Spelas nu. server/agents/weather.py panel()
fills w_days and wx_*; weather_view.h parses them and names the icons, drawn from Material Design Icons.

Memory: the page is about 120 small LVGL objects and three icon fonts with eleven glyphs each; no pictures.
"""

PAGE = "page_vader"
M = 40                                   # the page's side margin
WEEK_W = (1024 - 2 * M) // 7             # 134
ICONS = ["F0599", "F0594", "F0595", "F0F31", "F0590", "F0591", "F0597", "F0596", "F067E", "F067F", "F0598"]
FONT_SIZES = (24, 32, 44)
CARD_W = 198                             # the card's content: 230 less 16 px padding each side
SEP = "0xFFFFFF"
RAIN = "0x5AAAFF"                        # the mock-up's rain blue, lighter than BLUE on the dark page


def icon_font(size):
    return f"mdi_w{size}"


def glyph_label(ui, wid, size, **kw):
    return ui.label("", icon_font(size), id=wid, **kw)


def column_temps(ui, wid, font, **kw):
    """High and low side by side, centred: "10°" white, "5°" faint."""
    return ui.plain(id=wid, width="SIZE_CONTENT", height="SIZE_CONTENT", clickable=False,
                    layout={"type": "flex", "flex_flow": "ROW", "pad_column": 5}, **kw,
                    widgets=[ui.label("", font, id=f"{wid}_hi"), ui.label("", font, sec=True, id=f"{wid}_lo")])


def card(ui):
    """The weather card's content (gen.py's widget("weather")): now on top, then the next three days."""
    col = CARD_W // 3
    days = [ui.plain(x=col * i, y=138, width=col, height=60, clickable=False, widgets=[
        ui.label("", ui.f(500, 13), sec=True, id=f"wd{i}_name", align="TOP_MID", y=0),
        glyph_label(ui, f"wd{i}_icon", 24, align="TOP_MID", y=16),
        column_temps(ui, f"wd{i}_t", ui.f(600, 14), align="TOP_MID", y=44)]) for i in range(3)]
    return [
        ui.label("Kungsholmen", ui.f(600, 15), id="w_heading", x=0, y=0),
        ui.label(ui.sample("6°", "–"), ui.f(400, 56), x=-3, y=14, id="w_temp"),
        glyph_label(ui, "w_icon_lbl", 44, align="TOP_RIGHT", x=0, y=22),
        ui.label(ui.sample("Regn från 16:00"), ui.f(600, 14), x=0, y=84, width=CARD_W, long_mode="DOT", id="w_line"),
        ui.label(ui.sample("Ta paraply"), ui.f(600, 14), ui.ORANGE, x=0, y=104, id="w_tip"),
        ui.plain(x=0, y=128, width=CARD_W, height=1, bg_color=SEP, bg_opa="18%", clickable=False),
        *days]


def open_page():
    return [{"lvgl.page.show": {"id": PAGE, "animation": "FADE_IN", "time": "300ms"}}]


def page(ui):
    hours = []
    for k in range(8):                   # every third hour of 24: a dot, its temperature, an icon, the hour
        x = 48 + (976 - 48) * 3 * k // 23
        hours += [ui.plain(id=f"wx_dot{k}", x=x - 4, y=0, width=8, height=8, radius=4, bg_color=ui.WHITE,
                           bg_opa="COVER", hidden=True, clickable=False),
                  ui.label("", ui.f(600, 14), id=f"wx_t{k}", x=x - 30, y=0, width=60, text_align="CENTER"),
                  glyph_label(ui, f"wx_i{k}", 24, x=x - 30, y=318, width=60, text_align="CENTER"),
                  ui.label("", ui.f(500, 13), sec=True, id=f"wx_h{k}", x=x - 30, y=386, width=60,
                           text_align="CENTER")]
    bars = [ui.plain(id=f"wx_bar{i}", x=48 + (976 - 48) * i // 23 - 9, y=320, width=18, height=60, radius=3,
                     bg_color=RAIN, bg_opa="60%", hidden=True, clickable=False) for i in range(24)]
    week = []
    for i in range(7):
        week.append(ui.plain(x=M + WEEK_W * i, y=428, width=WEEK_W, height=140, clickable=False, widgets=[
            ui.label("", ui.f(600, 15), id=f"wk{i}_name", align="TOP_MID", y=4, **({} if i == 0 else {"text_opa": ui.SEC})),
            glyph_label(ui, f"wk{i}_icon", 32, align="TOP_MID", y=28),
            column_temps(ui, f"wk{i}_t", ui.f(600, 16), align="TOP_MID", y=72),
            ui.label("", ui.f(500, 13), sec=True, id=f"wk{i}_rain", align="TOP_MID", y=98),
            ui.label("", ui.f(500, 13), sec=True, id=f"wk{i}_gust", align="TOP_MID", y=118)]))
    facts = []
    for i, caption in enumerate(("Vind", "Luftfuktighet", "Regnrisk", "Lufttryck")):
        x, y = 1024 - M - 2 * 170 + (i % 2) * 170, 34 + (i // 2) * 44
        facts += [ui.label(caption, ui.f(500, 13), sec=True, x=x, y=y),
                  ui.label("", ui.f(600, 16), id=f"wx_f{i}", x=x, y=y + 17)]
    return {
        "id": PAGE, "bg_color": "0x141826", "bg_opa": "COVER", "pad_all": 0, "scrollable": False, "widgets": [
            # The page's own tap, under everything: back to the page with the bar, like Tavla.
            ui.plain(id="wx_back", x=0, y=0, width=1024, height=600, on_click=ui.bar_back("FADE_IN", "300ms"),
                     on_press=[{"lambda":'''static bool hooked=false;
if(!hooked){hooked=true;lv_obj_add_event_cb(id(wx_back),[](lv_event_t*){
  ui_update::text_opa(id(wx_heading),153,LV_PART_MAIN);
},LV_EVENT_PRESS_LOST,nullptr);}
ui_update::text_opa(id(wx_heading),255,LV_PART_MAIN);'''}],
                     on_release=[{"lambda":"ui_update::text_opa(id(wx_heading),153,LV_PART_MAIN);"}]),
            ui.label("Kungsholmen", ui.f(600, 16), sec=True, id="wx_heading", x=M, y=32),
            ui.label("", ui.f(400, 56), id="wx_temp", x=M - 3, y=52),
            glyph_label(ui, "wx_icon", 44, x=M + 104, y=64),
            ui.label("", ui.f(600, 22), id="wx_sky", x=M + 166, y=62),
            ui.label("", ui.f(500, 16), sec=True, id="wx_sub", x=M + 166, y=92, width=420, long_mode="DOT"),
            *facts,
            ui.label("Närmaste dygnet", ui.f(600, 16), x=M, y=150),
            ui.label("regn mm/h", ui.f(500, 13), RAIN, id="wx_rain_lbl", align="TOP_RIGHT", x=-M, y=152,
                     hidden=True),
            ui.plain(x=48, y=380, width=928, height=1, bg_color=SEP, bg_opa="12%", clickable=False),
            *bars,
            {"line": {"id": "wx_curve", "x": 48, "y": 196, "width": 929, "height": 105,
                      "points": [[0, 52], [928, 52]], "line_width": 3,
                      "line_color": ui.WHITE, "line_rounded": True, "clickable": False}},
            *hours,
            ui.plain(x=M, y=418, width=1024 - 2 * M, height=1, bg_color=SEP, bg_opa="12%", clickable=False),
            *week]}


def temps(wid, hi, lo):
    return (f"ui_update::text(id({wid}_hi), {hi}.c_str());\n"
            f"ui_update::text(id({wid}_lo), {lo}.c_str());\n")


DAYS = """auto rows = weather_view::table(x);
lv_obj_t *names[3] = {id(wd0_name), id(wd1_name), id(wd2_name)};
lv_obj_t *icons[3] = {id(wd0_icon), id(wd1_icon), id(wd2_icon)};
lv_obj_t *his[3] = {id(wd0_t_hi), id(wd1_t_hi), id(wd2_t_hi)};
lv_obj_t *los[3] = {id(wd0_t_lo), id(wd1_t_lo), id(wd2_t_lo)};
for (int i = 0; i < 3; ++i) {
  const bool ok = i < (int) rows.size() && rows[i].size() >= 4;
  ui_update::text(names[i], ok ? rows[i][0].c_str() : "");
  ui_update::text(icons[i], ok ? weather_view::glyph(rows[i][1]) : "");
  ui_update::text(his[i], ok ? rows[i][2].c_str() : "");
  ui_update::text(los[i], ok ? rows[i][3].c_str() : "");
}
"""

WEEK = """auto rows = weather_view::table(x);
// All seven fixed columns, including removed rows and old/new centred label positions.
ui_update::Batch batch(id(page_vader)->obj,{{40,428,983,568}});
""" + "".join(f"""{{ const bool ok = {i} < (int) rows.size() && rows[{i}].size() >= 6;
  ui_update::text(id(wk{i}_name), ok ? rows[{i}][0].c_str() : "");
  ui_update::text(id(wk{i}_icon), ok ? weather_view::glyph(rows[{i}][1]) : "");
  ui_update::text(id(wk{i}_t_hi), ok ? rows[{i}][2].c_str() : "");
  ui_update::text(id(wk{i}_t_lo), ok ? rows[{i}][3].c_str() : "");
  ui_update::text(id(wk{i}_rain), ok ? rows[{i}][4].c_str() : "");
  const bool wet = ok && rows[{i}][4] != "uppehåll";
  ui_update::text_color(id(wk{i}_rain), lv_color_hex(wet ? 0x5AAAFF : 0xFFFFFF), 0);
  ui_update::text_opa(id(wk{i}_rain), wet ? 255 : 153, 0);
  ui_update::text(id(wk{i}_gust), ok ? rows[{i}][5].c_str() : ""); }}
""" for i in range(7))

HOURS = """auto rows = weather_view::table(x);
// The header icon, full graph and hour labels. Intermediate growing bars are never presented.
ui_update::Batch batch(id(page_vader)->obj,{{128,48,204,124},{12,142,991,407}});
static lv_point_precise_t points[weather_view::HOURS];
lv_point_precise_t next[weather_view::HOURS];
std::vector<float> t;
for (auto &r : rows) if (r.size() >= 4 && t.size() < weather_view::HOURS) t.push_back(std::strtof(r[2].c_str(), nullptr));
float lo, hi;
weather_view::range(t, lo, hi);
const int n = t.size();
for (int i = 0; i < n; ++i) next[i] = {(lv_value_precise_t) (weather_view::x_at(i)-weather_view::CHART.left), (lv_value_precise_t) (weather_view::y_at(t[i], lo, hi)-weather_view::CHART.top)};
ui_update::text(id(wx_icon), !rows.empty() && rows[0].size() >= 2 ? weather_view::glyph(rows[0][1]) : "");
lv_obj_t *curve = id(wx_curve)->obj;   // ESPHome's line widget wraps the LVGL object
if (n >= 2) {
  bool changed=lv_line_get_point_count(curve)!=unsigned(n);
  const auto *old=lv_line_get_points(curve);
  if(!changed)for(int i=0;i<n;++i)if(old[i].x!=next[i].x||old[i].y!=next[i].y){changed=true;break;}
  if(changed){ui_update::changed(curve);std::copy(next,next+n,points);lv_line_set_points(curve,points,n);}
  ui_update::visible(curve,true);
}
else ui_update::visible(curve,false);
lv_obj_t *dots[8] = {""" + ", ".join(f"id(wx_dot{k})" for k in range(8)) + """};
lv_obj_t *temps[8] = {""" + ", ".join(f"id(wx_t{k})" for k in range(8)) + """};
lv_obj_t *icons[8] = {""" + ", ".join(f"id(wx_i{k})" for k in range(8)) + """};
lv_obj_t *hours[8] = {""" + ", ".join(f"id(wx_h{k})" for k in range(8)) + """};
for (int k = 0; k < 8; ++k) {
  const int i = 3 * k;
  if (i >= n) { ui_update::visible(dots[k],false); for (lv_obj_t *o : {temps[k], icons[k], hours[k]}) ui_update::text(o, ""); continue; }
  const int y = weather_view::y_at(t[i], lo, hi);
  ui_update::y(dots[k], y - 4);
  ui_update::visible(dots[k],true);
  ui_update::y(temps[k], y - 26);
  ui_update::text(temps[k], weather_view::deg(t[i]).c_str());
  ui_update::text(icons[k], weather_view::glyph(rows[i][1]));
  ui_update::text(hours[k], rows[i][0].c_str());
}
lv_obj_t *bars[24] = {""" + ", ".join(f"id(wx_bar{i})" for i in range(24)) + """};
bool wet = false;
for (int i = 0; i < 24; ++i) {
  const int h = i < (int) rows.size() && rows[i].size() >= 4 ? weather_view::bar_h(std::strtof(rows[i][3].c_str(), nullptr)) : 0;
  if (!h) { ui_update::visible(bars[i],false); continue; }
  wet = true;
  ui_update::height(bars[i], h);
  ui_update::y(bars[i], weather_view::BAR_BASE - h);
  ui_update::visible(bars[i],true);
}
if (wet) ui_update::visible(id(wx_rain_lbl),true); else ui_update::visible(id(wx_rain_lbl),false);
"""

FACTS = """auto rows = weather_view::table(x);
lv_obj_t *facts[4] = {id(wx_f0), id(wx_f1), id(wx_f2), id(wx_f3)};
for (int i = 0; i < 4; ++i)
  ui_update::text(facts[i], !rows.empty() && i < (int) rows[0].size() ? rows[0][i].c_str() : "–");
"""

# The card's icon (rain or snow when it starts within 3 hours); the page's header takes the "nu" hour's (HOURS).
ICON = """ui_update::text(id(w_icon_lbl), x.empty() ? "" : weather_view::glyph(x));
"""


def sensors(ui):
    """sensor.jarvis_hemmet's weather fields for the card's days and the page."""
    return [ui.hemmet("w_days", DAYS),
            ui.hemmet("wx_sky", ui.text("wx_sky", "")),
            ui.hemmet("wx_sub", ui.text("wx_sub", "")),
            ui.hemmet("wx_wet", ui.colour("wx_sub", 'x == "on"', ui.ORANGE, ui.WHITE)
                      + 'ui_update::text_opa(id(wx_sub), x == "on" ? 255 : 153, LV_PART_MAIN);\n'),
            ui.hemmet("wx_facts", FACTS),
            ui.hemmet("wx_hours", HOURS),
            ui.hemmet("wx_week", WEEK)]


def install(cfg, mdi):
    """The icon fonts and weather_view.h."""
    from pathlib import Path
    cfg["font"].extend({"file": mdi, "id": icon_font(s), "size": s, "bpp": 4,
                        "glyphs": [chr(int(c, 16)) for c in ICONS]} for s in FONT_SIZES)
    cfg["esphome"].setdefault("includes", []).append(str(Path(__file__).resolve().parent / "weather_view.h"))
