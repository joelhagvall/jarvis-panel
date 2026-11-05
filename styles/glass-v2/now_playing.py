"""The compact music row and the Spelas nu page. No layout changes while music is idle.

The row is on Gå hemifrån and, since 2026-10-28, on Hemmet too (Joel's choice), at the same place under the
page's heading text: BARS names each copy's widget suffix, and RENDER fills all of them. The Musik tab (also
2026-10-28) shows the same music full screen with a large spinning record (vinyl.h). A tap on the row opens that
tab; the sheet it opened before is gone (Joel, 2026-10-28: "bort med den helt")."""
import json
from pathlib import Path

BARS = ("", "_home")        # widget id suffix per copy: Gå hemifrån, Hemmet
BAR_Y = {"": 204, "_home": 168}   # under the date; on Hemmet under "Allt lugnt hemma." where the summary was
SHEET_BG = "0x141826"       # the sheet's and the Musik card's colour (vinyl_overlay.py's SHEET_BG)
COVER = 288                 # the downloaded cover: the Spelas nu page's record; the row gets a 144 px copy
VOLUME_Y = 508             # the Spelas nu page's volume row, to the bottom of the screen
PAGE_MID = 254             # the middle of the page above it
GROUP_H = VOLUME_Y - 8     # mp_group, the column's parent: LEFT_MID's y counts from its middle
DISC_XY = (72, PAGE_MID - 144)   # the page's record, centred on that middle
RADIO_XY = (216 - 180, PAGE_MID - 125)   # the page's radio, centred where the record is
TURNTABLE_XY = (29, PAGE_MID - 187)      # turntable.py's ORIGIN: the plinth's top left
_SQ = (72, PAGE_MID - 144, 360, PAGE_MID + 144)      # the record's square
_PL = (29, PAGE_MID - 187, 436, PAGE_MID + 187)      # the plinth
TURNTABLE_PIECES = {"top": (_PL[0], _PL[1], _PL[2], _SQ[1]), "bottom": (_PL[0], _SQ[3], _PL[2], _PL[3]),
                    "left": (_PL[0], _SQ[1], _SQ[0], _SQ[3]), "right": (_SQ[2], _SQ[1], _PL[2], _SQ[3])}
TEXT_X, TEXT_W = 456, 512  # the page's text column, right of the tonearm's pivot
SLEEVE_XY = (29, PAGE_MID - 144)   # sleeve.py's: the sleeve, its record's strip to x 436 (music_sleeve.h)
SLEEVE_W = 407
SLEEVE_TAP = r'''
lv_indev_t *indev = lv_indev_active();
if (!indev) return false;
lv_point_t p;
lv_indev_get_point(indev, &p);
const bool swapped = panel_sleeve::sleeve.tap(p.x, p.y);
if (swapped) ESP_LOGI("vinyl", "Spelas nu: %s", panel_sleeve::sleeve.on ? "sleeve" : "turntable");
return swapped;
'''


def cover(ui, key, x, y, size, radius, art="np_art"):
    """A cover container (`key`) with its image (`key_image`), hidden until a cover is in. A record (144 or 288 px)
    draws its turned cover into that image (vinyl.h); the row's 40 px thumbnail is the 144 px copy scaled."""
    small = size == 40
    return ui.plain(id=key, x=x, y=y, width=size, height=size, radius=radius, clip_corner=True, clickable=False,
        hidden=True, widgets=[{"image": {"id": key+"_image", "src": art, "x": 0, "y": 0, "clickable": False,
            "pivot_x": 0, "pivot_y": 0, "scale": 40/144 if small else 1}}])


def tonearm(ui, key, scale=1):
    """The record's tonearm (Joel's T4, 2026-10-28): a dark graphite arm that still shows on dark covers, a silver ring
    at the pivot, a counterweight, a white cartridge, and a rest on the right that the lifted arm lies on. vinyl.h
    moves the lines and shows it all for a record; scale 2 is the page's. Every line over the record is drawn again
    each frame: a lighter rim along the whole arm took the page from 60 to 34 images a second, so it has none."""
    def line(part, width, color):
        return {"line": {"id": f"{key}_{part}", "x": 0, "y": 0, "points": [[0, 0], [1, 1]], "line_width": width,
                         "line_color": color, "line_rounded": True, "hidden": True, "clickable": False}}

    def dot(part, size, color, border=None):
        return ui.plain(id=f"{key}_{part}", x=0, y=0, width=size, height=size, radius=size // 2, bg_color=color,
                        bg_opa="COVER", hidden=True, clickable=False,
                        **({"border_width": scale, "border_color": border} if border else {}))
    k = scale
    return [dot("rest", 9 * k, "0x24262C", "0x6A6E78"),
            dot("base", 24 * k, "0xD2D6DC"),
            line("weight", 5 * k, "0x18181C"),
            line("arm", 4 * k, "0x2C2F36"),
            line("head_rim", 9 * k, "0x4A4E58"), line("head", 7 * k, "0x18181C"),
            line("cart", 4 * k, "0xE6E8EC"),
            dot("pivot", 13 * k, "0x141416")]


def button(ui, key, icon, script, x, y, size=52, bright=False):
    return {"button": {"id": key, "x": x, "y": y, "width": size, "height": size, "radius": size//2,
        "bg_color": ui.WHITE, "bg_opa": "COVER" if bright else "16%", "border_width": 0, "shadow_opa": "TRANSP",
        "pad_all": 0, "widgets": [ui.label(ui.ICONS[icon], "mdi_ui", SHEET_BG if bright else ui.WHITE,
                                           align="CENTER", id=key+"_icon")],
        "on_click": [{"script.execute": script}]}}


def page(ui):
    """Spelas nu: what plays, the whole screen (Joel, 2026-10-28: no painting around it), layout B of his mocks.
    The record left; right of it where it comes from, the song large, the artist, the album and its year, a time line
    and the controls; Ljud ut top right; the volume across the bottom. Nothing playing: the turntable stays and a
    short hint takes the column's place. A tap beside the controls goes back; swipes move along the tabs."""
    ui.ICONS.update({"play": "\U000F040A", "pause": "\U000F03E4", "stop": "\U000F04DB",
                     "skip_next": "\U000F04AD", "skip_previous": "\U000F04AE"})
    x0, w = TEXT_X, TEXT_W
    controls = ui.plain(width=w, height=96, clickable=False, widgets=[
        button(ui, "mp_previous", "skip_previous", "music_previous", 0, 24, 64),
        button(ui, "mp_toggle", "pause", "music_toggle", 80, 16, 80, bright=True),
        button(ui, "mp_stop", "stop", "music_stop", 176, 24, 64),
        button(ui, "mp_next", "skip_next", "music_next", 256, 24, 64)])
    # Where the song is: a thin line and the times, for Spotify (radio has none).
    progress = ui.plain(id="mp_progress_row", width=w, height=36, hidden=True, clickable=False, widgets=[
        {"bar": {"id": "mp_progress", "x": 0, "y": 6, "width": w, "height": 4, "radius": 2, "min_value": 0,
                 "max_value": 1000, "value": 0, "bg_color": ui.WHITE, "bg_opa": "18%",
                 "indicator": {"bg_color": ui.WHITE, "bg_opa": "COVER", "radius": 2}}},
        ui.label("0:00", ui.f(500, 13), sec=True, x=0, y=18, id="mp_position"),
        ui.label("0:00", ui.f(500, 13), sec=True, align="TOP_RIGHT", x=0, y=18, id="mp_duration")])
    # Joel's hierarchy (2026-10-28): the song large and wrapping, the artist clearly under it, then the album and
    # its year; the small top line only says where it comes from. One column, so a two-line title pushes the rest down.
    column = ui.plain(x=x0, y=PAGE_MID - GROUP_H // 2, align="LEFT_MID", width=w, height="SIZE_CONTENT", clickable=False,
        layout={"type": "flex", "flex_flow": "COLUMN", "flex_align_main": "START", "flex_align_cross": "START",
                "flex_align_track": "START", "pad_row": 4},
        widgets=[
            ui.label("", ui.f(500, 16), sec=True, width=w, height=21, long_mode="DOT", id="mp_caption"),
            ui.plain(width=1, height=4),
            # A fixed two-line box, the title at its bottom (2026-10-31): a title wrapping to another line count
            # no longer moves artist, album and progress, which redrew the whole column in one ~30 ms frame.
            ui.plain(width=w, height=84, clickable=False, widgets=[
                ui.label("", ui.f(700, 34), align="BOTTOM_LEFT", width=w, height="SIZE_CONTENT", max_height=84,
                         long_mode="DOT", id="mp_title")]),
            ui.label("", ui.f(600, 22), width=w, height=28, long_mode="DOT", id="mp_artist"),
            ui.label("", ui.f(500, 16), sec=True, width=w, height=21, long_mode="DOT", id="mp_album"),
            ui.plain(width=1, height=14), progress,
            ui.plain(id="mp_live", width="SIZE_CONTENT", height=28, radius=14, bg_color="0xE84040", bg_opa="18%",
                     pad_left=12, pad_right=14, hidden=True,
                     layout={"type": "flex", "flex_flow": "ROW", "flex_align_main": "START", "flex_align_cross": "CENTER",
                             "flex_align_track": "CENTER", "pad_column": 8},
                     widgets=[ui.plain(width=10, height=10, radius=5, bg_color="0xF04848", bg_opa="COVER"),
                              ui.label("DIREKT", ui.f(700, 13), "0xFF8C8C")]),
            controls,
            # A control that Home Assistant refused (the sheet showed this before it went, 2026-10-28).
            ui.label("Kunde inte styra musiken. Försök igen.", ui.f(500, 14), ui.ORANGE, width=w, hidden=True,
                     id="mp_error")])
    # Not clickable, nor anything that only holds widgets: the full-width group took every tap beside the
    # controls, and the page went back only after a stop hid it (Joel, 2026-10-28).
    playing = ui.plain(id="mp_group", x=0, y=0, width=1024, height=GROUP_H, hidden=True, clickable=False, widgets=[column])
    # Nothing playing: the turntable stays (a black record, the arm up) and the hint takes the column's place.
    idle = ui.plain(id="mp_empty", x=x0, y=PAGE_MID - 300, align="LEFT_MID", width=w, height="SIZE_CONTENT", clickable=False,
                    widgets=[ui.label("Inget spelas just nu", ui.f(700, 34), x=0, y=0),
                             ui.label("Säg ”hey Jarvis, spela …” eller välj Jarvis i Spotify.", ui.f(500, 16),
                                      sec=True, x=0, y=52, width=w, long_mode="WRAP"),
                             ui.label("", ui.f(500, 16), sec=True, x=0, y=100, width=w, height=21,
                                      long_mode="DOT", id="mp_empty_feedback")])
    # The volume across the bottom (Joel: a little lower than in the mock), the sheet's value.
    volume = ui.plain(x=0, y=VOLUME_Y, width=1024, height=600 - VOLUME_Y, clickable=False, widgets=[
        ui.plain(x=56, y=0, width=912, height=1, bg_color=ui.WHITE, bg_opa="8%"),
        # Tap the quiet speaker for 5 % less, the loud one for 5 % more (Joel, 2026-10-29).
        volume_step(ui, "mp_vol_down", "volume_low", -5, x=36, align="LEFT_MID"),
        # A drag on it is the volume, never a swipe to another tab: it went home at times (Joel, 2026-10-28).
        {"slider": {"id": "s_musicpage", "x": 120, "align": "LEFT_MID", "y": 4, "width": 700, "height": 8,
                    "gesture_bubble": False, "ext_click_area": 20,
                    "radius": 4, "min_value": 0, "max_value": 100, "value": 50, "animated": False,
                    "bg_color": ui.WHITE, "bg_opa": "18%", "border_width": 0, "pad_all": 0,
                    "indicator": {"bg_color": ui.WHITE, "bg_opa": "COVER", "radius": 4},
                    "knob": {"bg_color": ui.WHITE, "bg_opa": "COVER", "radius": 14, "pad_all": 10,
                             "shadow_opa": "TRANSP", "border_width": 0},
                    "on_value": [{"lambda": ui.slider_value("musicpage", 0)}],
                    "on_release": [{"lambda": ui.slider_value("musicpage", 0, release=True)},
                                   {"script.execute": "musicpage_set"}]}},
        ui.label("50 %", ui.f(600, 16), sec=True, align="RIGHT_MID", x=-112, y=4, id="s_musicpage_val"),
        volume_step(ui, "mp_vol_up", "volume", 5, x=-36, align="RIGHT_MID")])
    # Ljud ut top right: Inställningar's list (Panelen or the Bluetooth speaker), for the radio. Spotify always
    # plays on the speaker through Soloist, so then a plain chip says so instead.
    out = ui.plain(id="mp_out_row", align="TOP_RIGHT", x=-24, y=20, width=280, height=44, clickable=False, widgets=[
        {"dropdown": {"id": "mp_out", "align": "RIGHT_MID", "x": 0, "width": 230, "height": 44, "radius": 22,
                      "options": [ui.sample("Panelen", "–")], "dir": "BOTTOM", "symbol": ui.ICONS["down"],
                      "bg_color": ui.WHITE, "bg_opa": "12%", "border_width": 0, "shadow_opa": "TRANSP",
                      "text_font": ui.f(600, 16), "text_color": ui.WHITE, "pad_left": 44, "pad_right": 14,
                      "pad_top": 12, "pad_bottom": 12,
                      "indicator": {"text_font": "mdi_ui", "text_color": ui.WHITE},
                      "dropdown_list": {"bg_color": "0x1F2433", "bg_opa": "COVER", "text_font": ui.f(600, 16),
                                        "text_color": ui.WHITE, "border_width": 1, "border_color": ui.WHITE,
                                        "border_opa": "14%", "radius": 16, "max_height": 260,
                                        "selected": {"bg_color": ui.WHITE, "bg_opa": "20%"}},
                      "on_change": [{"lambda": "switch (lv_dropdown_get_selected(id(mp_out)->obj)) {\n" + "\n".join(
                          f"  case {i}: id(out_pick_{i}).execute(); break;" for i in range(ui.PICKS)) + "\n}"}]}},
        ui.label(ui.ICONS["speaker"], "mdi_ui", ui.WHITE, x=-200, align="RIGHT_MID", clickable=False)])
    # Spotify through Jarvis plays only on the speaker: a tap says so for a few seconds (it did nothing before).
    spotify_out = ui.plain(id="mp_out_fixed", align="TOP_RIGHT", x=-24, y=20, width="SIZE_CONTENT", height=44,
        radius=22, bg_color=ui.WHITE, bg_opa="12%", pad_left=16, pad_right=18, hidden=True, clickable=True,
        on_click=[{"script.execute": "music_out_note"}],
        layout={"type": "flex", "flex_flow": "ROW", "flex_align_main": "START", "flex_align_cross": "CENTER",
                "flex_align_track": "CENTER", "pad_column": 8},
        widgets=[ui.label(ui.ICONS["speaker"], "mdi_ui", ui.WHITE),
                 ui.label("JBL-högtalaren", ui.f(600, 16), id="mp_out_fixed_lbl")])
    # Radio: a walnut radio instead of the turntable (Joel's choice B1, 2026-10-28; radio_cabinet.py, radio_dial.h):
    # the station's logo and frequency in the dial window, a red needle that glides to it.
    rx, ry = RADIO_XY
    radio = ui.plain(id="mp_radio", x=rx, y=ry, width=360, height=250, hidden=True, clickable=False, widgets=[
        {"image": {"src": "radio_b1", "x": 0, "y": 0, "clickable": False}},
        ui.plain(x=194, y=38, width=40, height=40, radius=8, clip_corner=True, clickable=False, widgets=[
            {"image": {"id": "mp_radio_logo", "src": "np_art", "x": 0, "y": 0, "clickable": False,
                       "pivot_x": 0, "pivot_y": 0, "scale": 40 / COVER}}]),
        # The panel's own text (radio_dial.h: "FM 99,3" or the station's name), not Home Assistant's.
        ui.label("FM 99,3", ui.f(700, 13), "0x462814", align="TOP_RIGHT", x=-32, y=50, id="mp_radio_freq"),
        ui.plain(id="mp_radio_needle", x=241, y=38, width=3, height=94, radius=1, bg_color="0xC81E14",
                 bg_opa="COVER", clickable=False)])
    # The sleeve (sleeve.py, music_sleeve.h): the cover straight, with the record's strip out of it on the right.
    sx, sy = SLEEVE_XY
    sleeve = ui.plain(id="mp_sleeve", x=sx, y=sy, width=SLEEVE_W, height=COVER, hidden=True, clickable=False, widgets=[
        {"image": {"src": "sleeve_record", "x": COVER, "y": 0, "clickable": False}},
        ui.plain(x=0, y=0, width=COVER, height=COVER, border_width=1, border_color=ui.WHITE, border_opa="10%",
                 clickable=False, widgets=[{"image": {"id": "mp_sleeve_image", "src": "np_art", "x": 0, "y": 0,
                                                      "clickable": False}}])])
    # A tap anywhere but on a control goes back, as on Tavla (Joel: no back button, 2026-10-28); a tap on a cover's
    # record shows the sleeve, and one on the sleeve the record (Joel, 2026-10-29). The record and the sleeve stay
    # unclickable so nothing new lies over the turning record: the page's own tap decides by where it landed.
    return {"id": ui.MUSIC, "bg_color": SHEET_BG, "bg_opa": "COVER", "pad_all": 0, "scrollable": False, "widgets": [
        ui.plain(x=0, y=0, width=1024, height=600, on_click=[{"if": {"condition": {"lambda": SLEEVE_TAP},
                 "then": [{"script.execute": "music_render"}], "else": ui.bar_back("FADE_IN", "300ms")}}]),
        idle, playing,
        # The walnut turntable around the record (Joel's choice P1, 2026-10-28; turntable.py), in four pieces so
        # none of it lies under the record.
        ui.plain(id="mp_turntable", x=0, y=0, width=1024, height=VOLUME_Y, clickable=False, widgets=[
            *({"image": {"src": f"turntable_p1_{name}", "x": x0, "y": y0, "clickable": False}}
              for name, (x0, y0, _, _) in TURNTABLE_PIECES.items()),
            *speed_knob(ui)]),
        cover(ui, "mp_art_frame", *DISC_XY, COVER, 24), *tonearm(ui, "mp", 2), radio, sleeve, volume, out, spotify_out,
        ui.label("", ui.f(500, 16), sec=True, align="TOP_RIGHT", x=-24, y=72, width=500, height=21,
                 long_mode="DOT", text_align="RIGHT", id="mp_out_status"),
        ui.label("Spotify spelar via Jarvis på JBL-högtalaren. Byt högtalare i Spotify-appen.", ui.f(500, 16), sec=True,
                 align="TOP_RIGHT", x=-24, y=72, width=440, long_mode="WRAP", text_align="RIGHT", hidden=True,
                 id="mp_out_note")]}


RENDER = r'''
// The speaker component can publish at priority 400, before LVGL builds widgets at 395.
if (!id(np_bar) || !id(np_bar_home) || !id(mp_group)) return;
// Ignore unchanged state resyncs during the silent fixture, including background work after leaving the page.
if(panel_music_load::fixture.active) {
  if(panel_music_load::fixture.unchanged())return;
  panel_music_load::fixture.restore();
}
const auto &m = panel_music::view();   // the held song while a new one's cover is on its way
bool cover = m.active() && panel_music::artwork.shown();
struct Bar { lv_obj_t *bar, *art, *caption, *title, *toggle, *toggle_icon; };
const Bar bars[] = {
  {id(np_bar), id(np_small_art_frame), id(np_caption), id(np_small_title), id(np_small_toggle), id(np_small_toggle_icon)},
  {id(np_bar_home), id(np_small_art_frame_home), id(np_caption_home), id(np_small_title_home), id(np_small_toggle_home),
   id(np_small_toggle_home_icon)}};
for (auto *widget : {bars[0].art, bars[1].art}) {   // the records show themselves (vinyl.h)
  if (cover) lv_obj_remove_flag(widget, LV_OBJ_FLAG_HIDDEN);
  else lv_obj_add_flag(widget, LV_OBJ_FLAG_HIDDEN);
}
for (const auto &b : bars) {
  panel_music_ui::x(b.caption, cover ? 58 : 14);
  panel_music_ui::x(b.title, cover ? 58 : 14);
  panel_music_ui::width(b.caption, cover ? 290 : 304);
  panel_music_ui::width(b.title, cover ? 290 : 304);
}
// The turntable is always there (vinyl.h): it turns while music plays. A new cover becomes its record when it is
// downloaded (np_art's on_download_finished); until then the old record stays, so a new song never blinks.
// Nothing playing: a plain black record.
// The real state, not the held view: while the first music after boot waits for its cover the view is "nothing",
// and a black record there replaced the cover just downloaded (2026-10-29).
if (!panel_music::state.active()) panel_vinyl::page.source(nullptr, panel_vinyl::Kind::BLANK);
panel_vinyl::page.set(m.state == "playing");
// Spelas nu: radio shows the walnut radio instead of the turntable, its needle gliding to the station.
const bool radio = m.active() && m.source != "Spotify" && m.source != "Musik";
// A tap on a cover's record shows it as a sleeve (music_sleeve.h); the record stops being drawn meanwhile.
const bool sleeve = panel_sleeve::sleeve.shown(cover && !radio, millis()) && !radio;
panel_vinyl::page.hide(radio || sleeve);
if (sleeve) lv_obj_remove_flag(id(mp_sleeve), LV_OBJ_FLAG_HIDDEN);
else lv_obj_add_flag(id(mp_sleeve), LV_OBJ_FLAG_HIDDEN);
if (radio || sleeve) lv_obj_add_flag(id(mp_turntable), LV_OBJ_FLAG_HIDDEN);
else lv_obj_remove_flag(id(mp_turntable), LV_OBJ_FLAG_HIDDEN);
if (radio) {
  lv_obj_remove_flag(id(mp_radio), LV_OBJ_FLAG_HIDDEN);
  lv_obj_remove_flag(id(mp_live), LV_OBJ_FLAG_HIDDEN);
  panel_music_ui::text(id(mp_radio_freq), panel_radio::label(m.source).c_str());
  const int to = panel_radio::needle_x(panel_radio::mhz(m.source));
  const int from = lv_obj_get_x(id(mp_radio_needle));
  if (to != from && !lv_anim_get(id(mp_radio_needle), nullptr)) {
    lv_anim_t a;
    lv_anim_init(&a);
    lv_anim_set_var(&a, id(mp_radio_needle));
    lv_anim_set_values(&a, from, to);
    lv_anim_set_duration(&a, 700);
    lv_anim_set_path_cb(&a, lv_anim_path_ease_in_out);
    lv_anim_set_exec_cb(&a, [](void *obj, int32_t x) { lv_obj_set_x(static_cast<lv_obj_t *>(obj), x); });
    lv_anim_start(&a);
  }
} else {
  lv_obj_add_flag(id(mp_radio), LV_OBJ_FLAG_HIDDEN);
  lv_obj_add_flag(id(mp_live), LV_OBJ_FLAG_HIDDEN);
}
if (m.active()) { lv_obj_remove_flag(id(mp_group), LV_OBJ_FLAG_HIDDEN); lv_obj_add_flag(id(mp_empty), LV_OBJ_FLAG_HIDDEN); }
else { lv_obj_add_flag(id(mp_group), LV_OBJ_FLAG_HIDDEN); lv_obj_remove_flag(id(mp_empty), LV_OBJ_FLAG_HIDDEN); }
if (!m.active()) {
  for (const auto &b : bars) lv_obj_add_flag(b.bar, LV_OBJ_FLAG_HIDDEN);
  return;
}
const char *feedback = panel_music::pending.message();
std::string caption = *feedback ? feedback : m.byline();
for (const auto &b : bars) {
  lv_obj_remove_flag(b.bar, LV_OBJ_FLAG_HIDDEN);
  panel_music_ui::text(b.caption, caption.c_str());
  panel_music_ui::text(b.title, m.title.c_str());
}
// Spelas nu: the top line says where it comes from ("Spotify", "P3"); where it plays is Ljud ut, top right.
panel_music_ui::text(id(mp_caption), *feedback ? feedback : m.source.c_str());
const bool spotify = m.source == "Spotify";
if (spotify) { lv_obj_add_flag(id(mp_out_row), LV_OBJ_FLAG_HIDDEN); lv_obj_remove_flag(id(mp_out_fixed), LV_OBJ_FLAG_HIDDEN); }
else { lv_obj_remove_flag(id(mp_out_row), LV_OBJ_FLAG_HIDDEN); lv_obj_add_flag(id(mp_out_fixed), LV_OBJ_FLAG_HIDDEN); }
// The speaker by its own name, "JBL Charge 4", from Home Assistant (Joel, 2026-10-28), not "JBL-högtalaren".
const std::string speaker = m.place.empty() ? "JBL-högtalaren" : m.place;
panel_music_ui::text(id(mp_out_fixed_lbl), speaker.c_str());
panel_music_ui::text(id(mp_out_note), ("Spotify spelar via Jarvis på " + speaker + ". Byt högtalare i Spotify-appen.").c_str());
id(music_progress_render).execute();
panel_music_ui::text(id(mp_title), m.title.c_str());
panel_music_ui::text(id(mp_artist), m.artist.c_str());
if (m.artist.empty()) lv_obj_add_flag(id(mp_artist), LV_OBJ_FLAG_HIDDEN);
else lv_obj_remove_flag(id(mp_artist), LV_OBJ_FLAG_HIDDEN);
panel_music_ui::text(id(mp_album), panel_music::view_album().c_str());
if (panel_music::view_album().empty()) lv_obj_add_flag(id(mp_album), LV_OBJ_FLAG_HIDDEN);
else lv_obj_remove_flag(id(mp_album), LV_OBJ_FLAG_HIDDEN);
const char *icon = m.state == "paused" ? "\U000F040A" : "\U000F03E4";
panel_music_ui::text(id(mp_toggle_icon), icon);
for (const auto &b : bars) panel_music_ui::text(b.toggle_icon, icon);
// Dimmed only when the source cannot do it (no next song). Not while a press waits for Home Assistant: every
// button dimmed and came back 0.3-1 s later, a blink at each press (Joel, 2026-10-28); the scripts already
// ignore a second press until the first is answered (pending.waiting in their condition).
auto enabled = [](lv_obj_t *obj, bool on) {
  if (on) lv_obj_remove_state(obj, LV_STATE_DISABLED);
  else lv_obj_add_state(obj, LV_STATE_DISABLED);
  panel_music_ui::opacity(obj, on ? 255 : 70);
};
enabled(id(mp_previous), m.previous);
enabled(id(mp_next), m.next);
enabled(id(mp_toggle), true);
for (const auto &b : bars) enabled(b.toggle, true);
'''

# The record (vinyl.h) runs while Spelas nu is the screen. Entering it also brings its volume slider up to date
# (ESPHome's page triggers reach only the first page).
KNOB = (59, PAGE_MID + 157)   # turntable.py's speed knob: 30 px in from the plinth's left and bottom


def volume_step(ui, key, icon, delta, **position):
    """A 56 px touch area around a speaker icon that moves the music volume one 5 % stop (musicpage_step)."""
    return ui.plain(id=key, width=56, height=56, y=4, ext_click_area=8, gesture_bubble=False, **position,
                    on_click=[{"script.execute": {"id": "musicpage_step", "delta": delta}}],
                    widgets=[ui.label(ui.ICONS[icon], "mdi_ui", ui.WHITE, text_opa="72%", align="CENTER",
                                      clickable=False)])


# One 5 % stop down or up from the slider's value, then the same commit as a slider release.
VOLUME_STEP = r'''
const int v = panel_music::volume_step(id(musicpage_pct), delta);
id(musicpage_pct) = v;
lv_slider_set_value(id(s_musicpage), v, LV_ANIM_OFF);
lv_label_set_text(id(s_musicpage_val), (std::to_string(v) + " %").c_str());
'''


def speed_knob(ui):
    """The turntable's 33/45 knob (Joel, 2026-10-28): its pointer and the two speeds over turntable.py's knob face,
    and a touch area that asks Home Assistant to switch (jarvis_agent.turntable_speed). 45 pitches Spotify up as a
    real 45 would (server/speed.py) and turns the record faster; Home Assistant puts it back to 33 at the next song."""
    kx, ky = KNOB
    return [{"line": {"id": "mp_knob_line", "x": 0, "y": 0, "points": [[kx, ky], [kx - 9, ky - 11]], "line_width": 3,
                      "line_color": "0x50545E", "line_rounded": True, "clickable": False}},
            ui.label("33", ui.f(700, 13), "0xE6D6C4", x=kx + 22, y=ky - 17, id="mp_knob_33"),
            ui.label("45", ui.f(700, 13), "0xE6D6C4", x=kx + 22, y=ky - 1, text_opa="45%", id="mp_knob_45"),
            ui.plain(id="mp_knob", x=kx - 22, y=ky - 24, width=70, height=48, clickable=True,
                     on_click=[{"homeassistant.action": {"action": "jarvis_agent.turntable_speed"}}])]


KNOB_RENDER = r'''
// The knob shows Home Assistant's speed; the records turn 45 / 33 1/3 as fast at 45.
const bool fast = x == "45";
panel_vinyl::page.motion.rpm_scale = fast ? 1.35f : 1.0f;
static lv_point_precise_t pointer[2];
pointer[0] = {KX, KY};
pointer[1] = {fast ? KX + 9 : KX - 9, KY - 11};
lv_line_set_points(id(mp_knob_line)->obj, pointer, 2);
lv_obj_set_style_text_opa(id(mp_knob_33), fast ? 115 : 255, LV_PART_MAIN);
lv_obj_set_style_text_opa(id(mp_knob_45), fast ? 255 : 115, LV_PART_MAIN);
ESP_LOGI("vinyl", "Turntable at %s rpm", fast ? "45" : "33");
'''.replace("KX", str(KNOB[0])).replace("KY", str(KNOB[1]))


def arm_parts(key):
    """The tonearm's widgets for vinyl.h's Disc::Parts, in its order (tonearm() makes them)."""
    plain = ("base", "pivot", "rest")
    return "panel_vinyl::Disc::Parts{" + ", ".join(
        f"id({key}_{part})" + ("" if part in plain else "->obj")
        for part in ("base", "pivot", "rest", "weight", "arm", "head_rim", "head", "cart")) + "}"


VINYL_SETUP = r'''
for(auto *button : {id(mp_previous), id(mp_toggle), id(mp_stop), id(mp_next)}) {
  const unsigned removed=panel_music_ui::instant_button(button);
  ESP_LOGI("vinyl", "Music button: %u transition-only theme styles detached", removed);
}
// Continuous exact-angle images, prepared on core 0, displayed from two buffers.
panel_vinyl::page.setup("Page", 144, 24, 2, []() { return lv_screen_active() == id(page_musik)->obj; },
  id(mp_art_frame), id(mp_art_frame_image), id(np_vinyl_page)->get_lv_image_dsc(), ''' + arm_parts("mp") + r''');
#ifdef HALLPANEL_DIRECT_DISPLAY
panel_vinyl::page.clock = {&direct_display::clock.count, &direct_display::clock.last_us, &direct_display::clock.pending,
                         &direct_display::rendering_vinyl};
#endif
if(!panel_vinyl::page.enable_right(id(turntable_p1_top)->get_lv_image_dsc(),id(turntable_p1_right)->get_lv_image_dsc()))
  ESP_LOGW("vinyl", "Opaque arm strip unavailable; A8 renderer retained");
lv_obj_add_event_cb(id(page_musik)->obj, [](lv_event_t *) { id(musicvol_sync).execute(); },
                    LV_EVENT_SCREEN_LOAD_START, nullptr);
// Spelas nu always opens on the turntable: the sleeve goes once the page is out of sight.
lv_obj_add_event_cb(id(page_musik)->obj, [](lv_event_t *) {
  if (!panel_sleeve::sleeve.on) return;
  panel_sleeve::sleeve.leave();
  id(music_render).execute();
}, LV_EVENT_SCREEN_UNLOADED, nullptr);
panel_vinyl::page.source(nullptr, panel_vinyl::Kind::BLANK);   // the black record until something plays
'''


def install(cfg, ui):
    next(f for f in cfg["font"] if f["id"] == "mdi_ui")["glyphs"] = list(ui.ICONS.values())
    here = Path(__file__).resolve().parent
    output_render = next(s for s in cfg["script"] if s["id"] == "output_feedback_render")
    output_render["then"].append({"lambda": '''
const auto &op=output_feedback::state;
const auto message=op.message();
panel_music_ui::urgent_text(id(mp_out_status),message.c_str());
const bool failed=op.phase==output_feedback::Phase::ERROR || op.phase==output_feedback::Phase::TIMEOUT;
lv_obj_set_style_text_color(id(mp_out_status),lv_color_hex(failed ? 0xFF9F0A : 0xFFFFFF),LV_PART_MAIN);
if(!message.empty())lv_obj_add_flag(id(mp_out_note),LV_OBJ_FLAG_HIDDEN);
int selected=choice_list::out.index();
if(op.busy() && op.kind==output_feedback::Kind::CHOICE)
  for(size_t i=0;i<choice_list::out.names.size();++i)if(choice_list::out.names[i]==op.target) selected=int(i);
if(selected>=0 && lv_dropdown_get_selected(id(mp_out)->obj)!=selected)lv_dropdown_set_selected(id(mp_out)->obj,selected);
'''})
    cfg["esphome"].setdefault("includes", []).extend(
        [str(Path(__file__).with_suffix(".h")), str(here / "vinyl_motion.h"), str(here / "vinyl_sampler.h"), str(here / "vinyl_line.h"), str(here / "vinyl_damage.h"), str(here / "music_ui.h"), str(here / "vinyl.h"),
         str(here / "cover_small.h"), str(here / "radio_dial.h"),
         str(here / "vinyl_right.h"), str(here / "vinyl_snapshot.h"), str(here / "music_sleeve.h"),
         str(here / "music_load.h")])
    cfg.setdefault("interval", []).append({"interval": "100ms", "then": [
        {"lambda": "panel_music_load::fixture.tick(millis());"}]})
    # gen.py's tab bar includes it already; the music buttons use it too.
    if str(here / "music_buttons.h") not in cfg["esphome"]["includes"]:
        cfg["esphome"]["includes"].append(str(here / "music_buttons.h"))
    # Simulator source; board() replaces this with a bounded online QOI image.
    cfg["image"].append({"platform": "file", "id": "np_art", "file": "assets/mic_small.png", "type": "RGB565"})
    cfg["image"].append({"platform": "file", "id": "sleeve_record", "file": "assets/sleeve_record.png",
                         "type": "RGB565"})   # opaque: sleeve.py draws it on the page's colour
    cfg["image"].append({"platform": "file", "id": "radio_b1", "file": "assets/radio_b1.png", "type": "RGB565",
                         "transparency": "alpha_channel"})
    # The record's still layer (vinyl_overlay.py makes it); vinyl.h blends it in as it turns the cover.
    # The page's record sits on the turntable (turntable.py): its still layer has the plinth around the circle,
    # so the record's image is opaque and the plinth under it is never drawn again per frame.
    cfg["image"].extend([
        {"platform": "file", "id": "np_vinyl_page", "file": "assets/vinyl_overlay_288_p1.png", "type": "RGB565",
         "transparency": "alpha_channel"},
        *({"platform": "file", "id": f"turntable_p1_{name}", "file": f"assets/turntable_p1_{name}.png",
           "type": "RGB565", **({"transparency": "alpha_channel"} if name in ("top","bottom") else {})}
          for name in TURNTABLE_PIECES)])

    def bar(k):
        # Under each page's heading text, in the unglazed clock area (gen.CLOCK_AREA ends at y 254).
        return ui.plain(id="np_bar"+k, x=32, y=BAR_Y[k], width=472, height=52, hidden=True,
            clickable=True, radius=22, bg_color="0x141826", bg_opa="86%", border_width=1,
            border_color=ui.WHITE, border_opa="16%", on_click=[{"script.execute": "music_open"}], widgets=[
                # One line each: without a height, DOT wrapped a long artist list over the title (2026-10-27).
                # The title on top, the artist and where it plays under it (2026-10-28).
                ui.label("", ui.f(600, 16), x=14, y=7, width=304, height=21, long_mode="DOT", id="np_small_title"+k),
                ui.label("", ui.f(500, 13), sec=True, x=14, y=29, width=304, height=17, long_mode="DOT", id="np_caption"+k),
                button(ui, "np_small_toggle"+k, "pause", "music_toggle", 368, 4, 44),
                button(ui, "np_small_stop"+k, "stop", "music_stop", 420, 4, 44),
                cover(ui, "np_small_art_frame"+k, 6, 6, 40, 8)])

    for k, page in zip(BARS, (ui.DASH, ui.HOME)):
        next(p for p in cfg["lvgl"]["pages"] if p["id"] == page)["widgets"].append(bar(k))
    # A script, so the boot chain names no cover image (tests/test_panel_performance.py: no cover download at boot).
    boot = cfg["esphome"].get("on_boot", [])   # added to, never replaced (gen.py's bar_hook lives there)
    cfg["esphome"]["on_boot"] = [*(boot if isinstance(boot, list) else [boot]),
                                 {"priority": -100, "then": [{"script.execute": "vinyl_setup"}]}]
    cfg.setdefault("globals", []).extend([
        {"id": "musicvol_pct", "type": "int", "initial_value": "50"},
        {"id": "musicpage_pct", "type": "int", "initial_value": "50"},
        # The Bluetooth speaker's volume from Home Assistant while the music plays there; -1 when not known.
        {"id": "speaker_vol", "type": "int", "initial_value": "-1"}])
    scripts = cfg.setdefault("script", [])
    scripts.extend([
        {"id": "music_render", "mode": "restart", "then": [{"lambda": RENDER}]},
        # The board's cover download (board() below); without a board there is nothing to fetch.
        {"id": "music_art_fetch", "then": [{"lambda": ""}]},
        {"id": "vinyl_setup", "then": [{"lambda": VINYL_SETUP}, {"script.execute": "music_render"}]},
        # The time line: where the song is now, from Home Assistant's last position and the time since.
        {"id": "music_progress_render", "mode": "restart", "then": [{"lambda": PROGRESS}]},
        {"id": "music_out_note", "mode": "restart", "then": [{"if": {
            "condition": {"lambda": "return output_feedback::state.message().empty();"},
            "then": [{"lvgl.widget.show": "mp_out_note"}, {"delay": "4s"}, {"lvgl.widget.hide": "mp_out_note"}],
            "else": [{"script.execute": "output_feedback_render"}]}}]},
        # The Spelas nu page's slider sets the music's volume through musicvol_set.
        {"id": "musicpage_set", "then": [{"lambda": "id(musicvol_pct) = id(musicpage_pct);"},
                                         {"script.execute": "musicvol_set"}]},
        {"id": "musicpage_step", "parameters": {"delta": "int"},
         "then": [{"lambda": VOLUME_STEP}, {"script.execute": "musicpage_set"}]},
        {"id": "musicvol_sync", "then": [{"logger.log": "Music volume sync (simulator)"}]},
        # A tap on the row goes to the Spelas nu tab, as its tab does (Joel, 2026-10-28: no sheet).
        {"id": "music_open", "then": [{"script.execute": "settings_close"}, {"lvgl.widget.hide": "mp_error"},
            {"lambda": f"id(tab_target) = {ui.SWIPE_ORDER.index(ui.MUSIC)};"}, {"script.execute": "tab_go"}]},
        {"id": "musicvol_set", "then": [{"logger.log": "Music volume (simulator)"}]},
        # The slider stops at every 5 %, so one script per stop sets the Bluetooth speaker's volume: an action's
        # data cannot carry the slider's value here (the config is JSON, without !lambda).
        *({"id": f"speakervol_{v}", "then": [{"homeassistant.action": {
            "action": "jarvis_agent.speaker_volume", "data": {"level": str(v)}}}]} for v in range(0, 101, 5))])
    scripts.append({"id": "music_feedback_render", "then": [{"lambda": r'''
const auto &m = panel_music::view();
const char *message = panel_music::pending.message();
const std::string caption = *message ? message : m.byline();
for (auto *label : {id(np_caption), id(np_caption_home)}) panel_music_ui::urgent_text(label, caption.c_str());
panel_music_ui::urgent_text(id(mp_caption), *message ? message : m.source.c_str());
panel_music_ui::urgent_text(id(mp_empty_feedback), message);
'''}]})
    scripts.append({"id": "music_load_restore", "then": [{"lambda": '''
panel_vinyl::page.source(id(np_art)->get_lv_image_dsc(),
  panel_music::state.active() ? ''' + KIND + ''' : panel_vinyl::Kind::BLANK,
  ''' + ART_IDENTITY + ''');
'''}]})
    scripts.append({"id": "music_control_recover", "then": [{"lambda": '''
// Restore the local optimistic view only. Never retry a refused or ambiguously timed-out command.
if(panel_music::pending.action == panel_music::Pending::Action::STOP) {
  panel_music::State reported;
  if(reported.wire(id(music_status).state) && reported.remote && reported.active()) {
    panel_music::state=std::move(reported);
    panel_music::artwork.restore();
    ESP_LOGW("music", "unconfirmed stop: the speaker's current row is back");
  }
} else if(panel_music::pending.action == panel_music::Pending::Action::NEXT) {
  panel_vinyl::page.source(id(np_art)->get_lv_image_dsc(), ''' + KIND + ''', ''' + ART_IDENTITY + ''');
}
id(music_render).execute();
id(music_feedback_render).execute();
'''}]})
    for action in ("toggle", "stop", "next", "previous"):
        # The dispatch script's parameter is captured by ESPHome's response callback. A late response after
        # timeout cannot finish/fail a newer command, including another press of the same button.
        scripts.append({"id": "music_send_"+action, "parameters": {"ticket": "uint32_t"}, "then": [
            {"homeassistant.action": {"action": "jarvis_agent.media_control", "data": {"action": action},
                "on_success": [{"lambda": '''
if (panel_music::pending.acknowledge(ticket, millis())) {
  ESP_LOGI("music", "control %u confirmed", ticket);
  id(music_feedback_render).execute();
}
'''}],
                "on_error": [{"if": {"condition": {"lambda": "return panel_music::pending.current(ticket);"},
                    "then": [{"lambda": '''
panel_music::pending.fail(ticket, millis());
ESP_LOGW("music", "control %u refused by Home Assistant", ticket);
'''}, {"lvgl.widget.hide": "mp_error"}, {"script.execute": "music_control_recover"}]}}]}}]})
        scripts.append({"id": "music_"+action, "mode": "single", "then": [{"if": {
            "condition": {"lambda": "return panel_music::state.active();" if action == "stop" else
                          "return !panel_music::pending.waiting && panel_music::state.active();"},
            "then": [{"lambda": f'''
panel_music::pending.begin(panel_music::Pending::Action::{action.upper()}, panel_music::state,
                           panel_music::progress.at(millis()), millis());
ESP_LOGI("music", "{action} sent (%u)", panel_music::pending.ticket);
id(music_feedback_render).execute();
'''}, {"lvgl.widget.hide": "mp_error"},
                     {"lambda": f"id(music_send_{action}).execute(panel_music::pending.ticket);"}]}}]})
    cfg.setdefault("text_sensor", []).append({"platform": "homeassistant", "id": "music_speaker_volume",
        "entity_id": "sensor.jarvis_uppspelning", "attribute": "speaker_volume", "on_value": [{"lambda": '''
char *end = nullptr;
long v = strtol(x.c_str(), &end, 10);
id(speaker_vol) = (end && *end == 0 && v >= 0 && v <= 100) ? int(v) : -1;
'''}]})
    # The turntable's speed, 33 or 45 (Home Assistant sets 33 again at the next song, a pause or a stop).
    cfg.setdefault("text_sensor", []).append({"platform": "homeassistant", "id": "music_rpm",
        "entity_id": "sensor.jarvis_uppspelning", "attribute": "rpm", "on_value": [{"lambda": KNOB_RENDER}]})
    # Where the song is ("position ms|duration ms|1 while playing"), for the Spelas nu page's time line.
    cfg.setdefault("text_sensor", []).append({"platform": "homeassistant", "id": "music_progress",
        "entity_id": "sensor.jarvis_uppspelning", "attribute": "progress", "on_value": [
            {"lambda": '''
if (panel_music::progress.wire(x, millis()) &&
    panel_music::pending.observe(panel_music::state, panel_music::progress.at(millis()), millis())) {
  ESP_LOGI("music", "control %u confirmed by position", panel_music::pending.ticket);
  id(music_feedback_render).execute();
}
'''}, {"script.execute": "music_progress_render"}]})
    cfg.setdefault("interval", []).append({"interval": "500ms", "then": [{"if": {
        "condition": {"lambda": "return panel_music::progress.duration_ms && lv_screen_active() == id(page_musik)->obj;"},
        "then": [{"script.execute": "music_progress_render"}]}}]})
    # Ljud ut on the page is Inställningar's list: the same entries and choice (gen.choice_sensors).
    for sensor in cfg.get("text_sensor", []):
        if sensor.get("id") == "out_options":
            sensor["on_value"].append({"lambda": "lv_dropdown_set_options(id(mp_out)->obj, choice_list::out.joined().c_str());\n"
                                       + "id(output_feedback_render).execute();"})
        if sensor.get("id") == "out_name":
            sensor["on_value"].append({"script.execute": "output_feedback_render"})
    # The album and its year ("Ghost Stories (2014)") for the Spelas nu page; empty for radio.
    cfg.setdefault("text_sensor", []).append({"platform": "homeassistant", "id": "music_album",
        "entity_id": "sensor.jarvis_uppspelning", "attribute": "album", "on_value": [
            {"lambda": "panel_music::album = x;"}, {"script.execute": "music_render"}]})
    cfg.setdefault("text_sensor", []).append({"platform": "homeassistant", "id": "music_status",
        "entity_id": "sensor.jarvis_uppspelning", "attribute": "wire",
        "on_value": [{"lambda": '''
const auto before = panel_music::state;
auto old_source = panel_music::state.source;
if (panel_music::state.wire(x)) {
  if (panel_music::pending.observe(panel_music::state, panel_music::progress.at(millis()), millis())) {
    ESP_LOGI("music", "control %u confirmed by player", panel_music::pending.ticket);
    id(music_feedback_render).execute();
  }
  // A new song on Spotify waits for its cover (Hold): the text, the row and the record change together.
  const auto &now = panel_music::state;
  if (now.title != before.title || now.state != before.state)
    ESP_LOGI("music", "now %s: %s", now.state.c_str(), now.title.c_str());
  if (now.active() && before.active() && now.source == "Spotify" && old_source == now.source &&
      (now.title != before.title || now.artist != before.artist))
    panel_music::hold.start(before, panel_music::album, panel_music::progress, panel_music::artwork.finished, millis());
  else if (panel_music::hold_first_cover(before, now, panel_music::artwork))
    panel_music::hold.start(before, panel_music::album, panel_music::progress, panel_music::artwork.finished, millis());
  else if (old_source != now.source || !now.active())
    panel_music::hold.on = false;
  // Only another source drops the cover at once. A new song on the same station keeps its logo: HA keeps the
  // logo's key, and would not send it again. A player's new track gets a new key from HA, which hides the old cover.
  if (panel_music::source_changed(old_source, panel_music::state.source))
    panel_music::artwork.forget();
  else if (panel_music::state.active())
    panel_music::artwork.restore();
  id(music_render).execute();
  id(music_art_fetch).execute();
}
'''}]})
    # The sleeve gives up waiting for a cover (music_sleeve.h GRACE_MS): show the turntable again.
    cfg.setdefault("interval", []).append({"interval": "500ms", "then": [{"if": {
        "condition": {"lambda": "return panel_sleeve::sleeve.lapsed(millis());"},
        "then": [{"lambda": 'ESP_LOGI("vinyl", "Spelas nu: no cover for 4 s, turntable");'},
                 {"script.execute": "music_render"}]}}]})
    # The held song lets go as soon as the new cover is downloaded (checked every 50 ms).
    cfg.setdefault("interval", []).append({"interval": "50ms", "then": [{"if": {
        "condition": {"lambda": "return panel_music::hold.on && panel_music::hold.release(panel_music::cover_in("
                                "panel_music::artwork, panel_music::hold.downloads_before), millis());"},
        "then": [{"script.execute": "music_render"}]}}]})
    cfg.setdefault("interval", []).append({"interval": "100ms", "then": [{"lambda": '''
if (panel_music::pending.tick(millis())) {
  if (panel_music::pending.result == panel_music::Pending::Result::TIMEOUT) {
    ESP_LOGW("music", "control %u: no player confirmation after 8 s", panel_music::pending.ticket);
    id(music_control_recover).execute();
  } else id(music_feedback_render).execute();
}
'''}]})


OUT_SELECTED = "{ int i = choice_list::out.index(); if (i >= 0) lv_dropdown_set_selected(id(mp_out)->obj, i); }"
PROGRESS = r'''
const auto &p = panel_music::view_progress();
const auto &m = panel_music::view();
if (!p.duration_ms || m.source != "Spotify" || !m.active()) {
  lv_obj_add_flag(id(mp_progress_row), LV_OBJ_FLAG_HIDDEN);
  panel_vinyl::page.set_groove(0);
  return;
}
lv_obj_remove_flag(id(mp_progress_row), LV_OBJ_FLAG_HIDDEN);
const uint32_t at = p.at(millis());
// The stylus follows the song inward (vinyl_motion.h's arm).
panel_vinyl::page.set_groove(float(at) / p.duration_ms);
lv_bar_set_value(id(mp_progress), int(uint64_t(at) * 1000 / p.duration_ms), LV_ANIM_OFF);
panel_music_ui::text(id(mp_position), panel_music::Progress::clock(at).c_str());
// Joel: how much is left on the right ("-4:12"), the time gone on the left.
panel_music_ui::text(id(mp_duration), ("-" + panel_music::Progress::clock(p.duration_ms - at)).c_str());
'''
# The cover's identity for the record: only the board's async_artwork has one; the simulator's np_art is a plain
# image, so install() writes ART_IDENTITY and board() puts the real call in (the simulator failed to build).
ART_IDENTITY = "0 /* np_art identity */"
BOARD_IDENTITY = "id(np_art).picture_identity()"
KIND = ('(panel_music::state.source == "Spotify" || panel_music::state.source == "Musik" ? '
        'panel_vinyl::Kind::PICTURE : panel_vinyl::Kind::LABEL)')


def board(cfg):
    """Local music stop works without HA; native music state excludes announcements and volume is silent."""
    def script(name):
        return next(s for s in cfg["script"] if s["id"] == name)
    from urllib.parse import urlsplit
    import json
    remote = next(im["url"] for im in cfg["image"] if im["id"] == "bg")
    url = urlsplit(remote)
    base = json.dumps(f"{url.scheme}://{url.netloc}/api/jarvis_agent/media-art/")
    cfg["image"] = [im for im in cfg["image"] if im["id"] != "np_art"]
    for name in ("music_load_restore", "music_control_recover"):     # np_art is async_artwork from here on
        for step in script(name)["then"]:
            if "lambda" in step:
                step["lambda"] = step["lambda"].replace(ART_IDENTITY, BOARD_IDENTITY)
    script("vinyl_setup")["then"].insert(1, {"lambda": "panel_vinyl::page.on_visible = [](bool on) { id(np_art).set_foreground(on); };"})
    cfg["image"].append({"platform": "async_artwork", "id": "np_art",
        "url": f"{url.scheme}://{url.netloc}/api/jarvis_agent/media-art/none.qoi",
        "on_download_finished": [{"lambda": "panel_music::artwork.finish(true);"},
            # The Spelas nu record turns the 288 px cover; the row shows a 144 px copy (cover_small.h), scaled,
            # or without one the big cover.
            # A track's cover is the whole record; a station's logo is a black record's label.
            {"lambda": "panel_vinyl::page.source(id(np_art)->get_lv_image_dsc(), " + KIND + ", id(np_art).picture_identity());"},
            {"lvgl.image.update": {"id": "mp_radio_logo", "src": "np_art"}},     # the radio's dial
            {"lvgl.image.update": {"id": "mp_sleeve_image", "src": "np_art"}},   # the sleeve
            {"lambda": 'panel_cover::small.attach(id(np_art).small_data());\n' + ''.join(
                f'panel_cover::show(id(np_small_art_frame{k}_image));\n' for k in BARS)},
            {"script.execute": "music_render"}],
        "on_error": [{"lambda": "panel_music::artwork.finish(false);"}, {"script.execute": "music_render"}]})
    # The next Spotify track's cover, downloaded ahead into the free buffer (HA prepares it from Soloist's queue);
    # when the track changes its key arrives as `artwork` and the cover is there at once. Retried after a download.
    # Not while a cover builds: the download would take core 0 from the record's new atlas (2026-10-30: the next
    # cover's download right after a skip slowed the build's first stage to 1.6 s). The 500 ms interval retries.
    ahead = (f'if (id(music_artwork_next).state.size() == 32 && panel_music::state.active() && '
             f'!panel_vinyl::page.building()) '
             f'id(np_art).prefetch(std::string({base}) + id(music_artwork_next).state + ".qoi");')
    cfg["text_sensor"].append({"platform": "homeassistant", "id": "music_artwork_next",
        "entity_id": "sensor.jarvis_uppspelning", "attribute": "artwork_next", "on_value": [{"lambda": ahead}]})
    cfg.setdefault("interval", []).append({"interval": "500ms", "then": [{"lambda": ahead}]})
    # The panel's own next button turns the cover built ahead at once; a refused skip puts the shown cover back
    # (from the record's `behind`, at once too).
    nxt = script("music_next")["then"][0]["if"]["then"]
    nxt.insert(1, {"lambda": "panel_vinyl::page.take_ahead();"})
    # The record builds that cover's first stage ahead too, so it turns the new cover as soon as the track changes.
    script("vinyl_setup")["then"].insert(1, {"lambda":
        "id(np_art)->add_on_ahead_callback([]() {\n"
        "  if (panel_music::state.source == \"Spotify\")\n"
        "    panel_vinyl::page.source_ahead(id(np_art)->ahead_pixels(), 288, id(np_art)->ahead_identity());\n"
        "});"})
    cfg["text_sensor"].append({"platform": "homeassistant", "id": "music_artwork",
        "entity_id": "sensor.jarvis_uppspelning", "attribute": "artwork", "on_value": [
            {"lambda": "panel_music::artwork.offer(x);"}, {"script.execute": "music_render"},
            {"script.execute": "music_art_fetch"}]})
    # Fetch as soon as the key or the music arrives (the 500 ms interval alone added up to half a second at boot);
    # the interval stays as the retry after a busy download.
    script("music_art_fetch")["then"] = [{"if": {
        "condition": {"lambda": "return panel_music::state.active() && panel_music::artwork.begin();"},
        "then": [{"lambda": f'id(np_art).set_url(std::string({base}) + panel_music::artwork.loading + ".qoi");'},
                 {"component.update": "np_art"}]}}]
    cfg["interval"].append({"interval": "500ms", "then": [{"script.execute": "music_art_fetch"}]})
    # Home Assistant shows this player's announcements (the boot chime, Jarvis's replies) as "playing", which made
    # "Spelas nu" appear for every chime (2026-10-26). The panel knows the music pipeline apart from announcements:
    # publish that, and ha/custom_components/jarvis_agent/media.py prefers it. ANNOUNCING never changes it.
    cfg["text_sensor"].append({"platform": "template", "id": "music_native", "name": "Music state",
                               "icon": "mdi:music", "update_interval": "never"})
    cfg["esphome"].setdefault("on_boot", []).append({"priority": -100, "then": [
        {"lambda": 'id(music_native).publish_state("idle");'}]})
    media = cfg["media_player"][0]
    media.setdefault("on_state", []).append({"lambda": '''
const auto s = id(panel_media)->state;
const char *music = nullptr;
if (s == media_player::MEDIA_PLAYER_STATE_PLAYING) { panel_music::state.native("playing"); music = "playing"; }
else if (s == media_player::MEDIA_PLAYER_STATE_PAUSED) { panel_music::state.native("paused"); music = "paused"; }
else if (s == media_player::MEDIA_PLAYER_STATE_IDLE || s == media_player::MEDIA_PLAYER_STATE_OFF) { music = "idle";
  // Music on Jarvis's Bluetooth speaker keeps its cover when the panel's own player goes idle (after a reply,
  // at boot, or when the radio moves back to the speaker): it shows HA's key again.
  panel_music::state.native("idle");
  if (panel_music::state.remote) panel_music::artwork.restore();
  else panel_music::artwork.select("");
}
if (music && id(music_native).state != music) id(music_native).publish_state(music);
id(music_render).execute();
'''})
    # Stop the music pipeline locally even if HA is disconnected; leave voice/alarms untouched.
    # Stop blanks the row at once, on Hem and the dashboard too, and Spelas nu (Joel, 2026-10-30). Music on Jarvis's
    # speaker is stopped by Home Assistant; Spotify's stop is a pause there, but HA reports it idle until it plays
    # again (media.py), so the record is not built a second time (the blink of 2026-10-28 came from HA's "paused").
    stop_steps = script("music_stop")["then"][0]["if"]["then"]
    stop_steps.insert(1, {"lambda": '''
if (!panel_music::state.remote)
  id(panel_media)->make_call().set_command(media_player::MEDIA_PLAYER_COMMAND_STOP).set_announcement(false).perform();
ESP_LOGI("music", "stop: row cleared (%s)", panel_music::state.remote ? "speaker" : "panel");
panel_music::state.clear();
panel_music::artwork.select("");
id(music_render).execute();
'''})
    sync = '''
// The slider is the speaker's while the music plays there (Jarvis's Bluetooth speaker), else the panel's own.
int v = panel_music::state.remote && id(speaker_vol) >= 0 ? id(speaker_vol) : (int) roundf(id(panel_media)->volume * 100);
id(musicvol_pct) = v;
id(musicpage_pct) = v;
lv_slider_set_value(id(s_musicpage), v, LV_ANIM_OFF);
panel_music_ui::text(id(s_musicpage_val), (std::to_string(v) + " %").c_str());
'''
    script("music_open")["then"].insert(0, {"lambda": sync})
    script("musicvol_sync")["then"] = [{"lambda": sync}]
    picks = "\n".join(f"  case {i}: id(speakervol_{i * 5}).execute(); break;" for i in range(21))
    script("musicvol_set")["then"] = [{"if": {"condition": {"lambda": "return panel_music::state.remote;"},
        "then": [{"lambda": "switch ((id(musicvol_pct) + 2) / 5) {\n" + picks + "\n}"}],
        "else": [{"lambda": '''
id(day_volume) = id(musicvol_pct) / 100.0f;
id(panel_media)->make_call().set_volume(id(day_volume)).perform();
'''}]}}]
    jump_after_voice(cfg, sync)


def jump_after_voice(cfg, sync):
    """Spelas nu after Jarvis starts music (music_jump.h): from the voice page when the turn ends, or from the
    page the turn went back to when the music comes a moment later. Never at night."""
    if "voice_assistant" not in cfg:
        return
    scripts = {s["id"]: s for s in cfg["script"]}
    cfg["esphome"]["includes"].append(str(Path(__file__).with_name("music_jump.h")))
    ident = "panel_music_jump::identity(panel_music::state.state, panel_music::state.source, panel_music::state.title)"
    left = ("(panel_music::progress.duration_ms ? panel_music::progress.duration_ms - std::min(panel_music::progress"
            ".at(millis()), panel_music::progress.duration_ms) : 0)")
    cfg["script"].append({"id": "music_arm", "mode": "single", "then": [
        {"lambda": f"panel_music_jump::arm(millis(), {ident}, {left});"}]})
    cfg["voice_assistant"].setdefault("on_listening", []).insert(0, {"script.execute": "music_arm"})
    day = " && !id(night) && !id(night_hours)" if any(g["id"] == "night_hours" for g in cfg.get("globals", [])) else ""
    close = [{"script.execute": "settings_close"}] if "settings_close" in scripts else []
    to_music = [*close, {"lambda": sync}, {"lvgl.page.show": {"id": "page_musik", "animation": "NONE"}}]
    settle = {"lambda": "panel_music_jump::settle(millis());"}

    def wrap(actions):
        return [settle, {"if": {"condition": {"lambda": f"return panel_music_jump::take(){day};"},
                                "then": to_music, "else": actions}}]
    # voice_done: the turn's last step goes back after the orb's 3 s settle. Music that already plays when Jarvis
    # has finished speaking skips the settle and shows at once (Joel, 2026-10-30); music during the settle still
    # ends it on Spelas nu.
    turn = scripts["voice_done"]["then"][-1]["if"]["then"]
    settled = next(i for i, a in enumerate(turn) if a.get("delay") == "3s") + 1
    turn[settled:] = wrap(turn[settled:])
    woken = next(i for i, a in enumerate(turn) if "micro_wake_word.start" in json.dumps(a)) + 1
    turn[woken:] = wrap(turn[woken:])
    # A tap on the voice page goes back at once, but music Jarvis has started still shows.
    # orb_leave goes back before it waits for the assistant to stop (voice.py): wrap the page change between.
    leave = scripts["orb_leave"]["then"]
    first = next(i for i, a in enumerate(leave) if a.get("lambda") == "id(orb_leaving) = true;") + 1
    last = next(i for i, a in enumerate(leave) if "wait_until" in a)
    leave[first:last] = wrap(leave[first:last])
    pages = ("page_glass_v2", "page_hemmet", "page_tavla", "page_kalender", "page_vader")
    here = " || ".join(f"lv_screen_active() == id({p})->obj" for p in pages if any(x["id"] == p for x in cfg["lvgl"]["pages"]))
    cfg["script"].append({"id": "music_jump", "mode": "single", "then": [{"if": {
        "condition": {"lambda": f"return ({here}){day};"}, "then": to_music}}]})
    status = next(t for t in cfg["text_sensor"] if t.get("id") == "music_status")
    status["on_value"].append({"lambda": f'''
if (panel_music_jump::started(millis(), {ident})) {{
  ESP_LOGI("music", "Jarvis started music: Spelas nu");
  if (lv_screen_active() == id(page_orb)->obj) panel_music_jump::pending = true;
  else id(music_jump).execute();
}}'''})
