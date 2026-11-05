#!/usr/bin/env python3
"""Glass v2 with the stateful Jarvis line: no status label, the line shows what Jarvis is doing.

Since 2026-10-28 the orb is a free glowing line on the painting (orbs/line_orb.py, orbs/line-jarvis), not the
plasma glass sphere (orbs/glass-jarvis, still in atlases v1-v7 for older firmware), which Joel found heavy.

  listening  a short line with a light gliding along it: 5.2 s per loop, from 96 drawn frames on the panel
             (orbs/line-jarvis/calm_*), livelier with your voice, "Jag lyssnar…" in faint text
  thinking   the same frames, easing up to 1.8 s per loop (the panel's director, orb_director.h)
  answering  the line grows into the voice wave (swing_* on the panel, morph_* in the simulator), then the
             wave: drawn live on the panel from Jarvis's loudness (voice_wave.h), 72 drawn frames (talk_*) in
             the simulator, while the reply is revealed word by word
  done       the wave settles into a flat line (rest_* / settle_*), then back to the dashboard
On the panel each state starts at a drawn frame it shares with the one before (orb_director.h).

Each state is its own animimg on the same spot; the script shows one and hides the rest.
The frames come from `python3 orbs/line_orb.py`.

A reply with details (an outing, a plane) shows one or two cards under the orb instead of its text, one per
subject: a title and rows of an icon and one line, at most six lines in all. Jarvis writes them himself with
the tool skarm (server/agents/screen.py) and picks the icons from Material Design Icons by what each row is
about, and writes a one-line summary of everything he says and shows, which takes the place of your sentence
over the cards (or of the reply's text, when there are no cards); they reach the panel as sensor.jarvis_chat's
last_card. He still speaks the reply.

The reply's text (board.py's on_tts_start, through show_reply) always shows whole: 30 px when it fits, else
22 px or 19 px (reply_fit.h). A reply too long even at 19 px, about 600 characters, is never cut (Joel,
2026-11-01): it shows at 22 px in reply_box and rolls up a line at a time with Jarvis's voice (11 characters a
second, measured), the line he is saying second from the top; voice_done shows its end when the voice stops.
Your sentence has two lines at 22 px; a longer one shows whole at 19 px in up to three lines down to the reply
(Joel, 2026-11-01), and only one longer still ends in "…" (over cards one line, which start right under it). A reply with
details gets cards. While Jarvis
works, his sentence before the tools ("Genast. Jag kollar flyget …", sensor.jarvis_chat's working) shows
there.

Usage: python3 jarvis_orb.py [--demo] [--orb [--card]] [--page hemmet|natt] [--remote URL] [--bg PNG] > jarvis-orb.yaml
              (run from styles/glass-v2)
  --demo        play the whole flow once from boot, for recording
  --orb         start on the orb page in the speaking state with the whole reply
  --card        with --orb: show the example cards instead of the reply
  --listening   with --orb: the listening state ("Jag lyssnar…") instead of the reply, for a still
  --page        start on the Hemmet or Natt page (gen.py), for a still
  --then JSON   ESPHome actions 8 s after boot, e.g. a page.show for a still with the right tab marked:
                --page falls back to the dashboard in the simulator (tools/sim_shot.sh, docs/public-export.md)
  --remote URL  download bg.png, art.png, its lightly frosted voice_bg.png and transparent
                orb_atlas_v8.lz4 into PSRAM at boot. URL is Home Assistant on Jarvis,
                http://jarvis.local:8123/local/hallpanel (tools/deploy_jarvis.sh puts them there).
                Native LVGL decoding expands only the current lossless RGB565+A8 frame.
"""
import importlib.util
import json
import sys
import urllib.request
from pathlib import Path

import gen
import boot_cache

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "orbs"))
import atlas as orb_atlas

HERE = Path(__file__).resolve().parent
FRAMES = HERE.parent.parent / "orbs" / "line-jarvis"   # the simulator's compiled-in frames, without --remote
# The frames baked onto bg_orb (orbs/opaque.py): opaque RGB565, so LVGL copies them instead of blending (2026-10-22).
SETS = {"listen": 24, "morph": 8, "speak": 24, "settle": 6}
STATES = ["orb_listen", "orb_think", "orb_morph", "orb_speak", "orb_settle"]
X, Y = gen.ORB_CENTER[0] - gen.ORB_SIZE // 2, gen.ORB_CENTER[1] - gen.ORB_SIZE // 2
LISTENING = "Jag lyssnar…"
CARDS, CARD_ROWS = 2, 5
CARD_FONT = "mdi_24"
# Fictional demonstration cards, independent of captured conversations.
CARD_SAID = "Boka ett besök i Demoparken på tisdag eftermiddag."
CARD_SUMMARY = "Demoparken · tisdag 15:10 inlagt"
CARD_EXAMPLE = [("Demoparken", [("calendar", "Tisdag 10 november · 15:10–16:10"),
                               ("weather-rainy", "Demoväder · 11°"),
                               ("check-circle", "Fiktiv kalenderpost")])]


# The splash must be the brand image's own background after RGB565: with 0x05060C (green level 1) the opaque
# 320x320 logo, whose border is green level 0, stood out as a darker box behind the orb and "Jarvis" (2026-10-26).
# tests/test_boot_experience.py derives the logo's border colour from assets/branding and checks this matches.
BOOT_BG = "0x02020A"


def load_screen():
    """server/agents/screen.py, the icons Jarvis can pick from: the panel compiles exactly these."""
    spec = importlib.util.spec_from_file_location("screen", HERE.parent.parent / "server" / "agents" / "screen.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


screen = load_screen()


def anim(wid, frames, duration, hidden=True, once=False):
    d = {"id": wid, "src": frames, "duration": duration, "x": X, "y": Y, "hidden": hidden}
    if once:
        d.update({"repeat_count": 1, "auto_start": False})
    return {"animimg": d}


def frames(key):
    return [f"{key}_{i:02d}" for i in range(SETS[key])]


def glyph(icon):
    return chr(int(screen.ICONS[icon], 16))


def cards(example):
    """The cards under the orb, stacked: glass panels like the dashboard's pills, each with a title and up to
    CARD_ROWS rows of icon and text. All hidden until a reply has cards; unused titles and rows are hidden."""
    content = CARD_EXAMPLE if example else []
    boxes = []
    for c in range(CARDS):
        title, rows = content[c] if c < len(content) else ("", [])
        rows = rows + [("dots-horizontal", "")] * (CARD_ROWS - len(rows))
        boxes.append(gen.plain(
            id=f"card{c}", width=700, height="SIZE_CONTENT", radius=22, hidden=c >= len(content),
            bg_color=gen.WHITE, bg_opa="10%", border_width=1, border_color=gen.WHITE, border_opa="22%",
            pad_top=10, pad_bottom=12, pad_left=22, pad_right=22,
            layout={"type": "flex", "flex_flow": "COLUMN", "pad_row": 4},
            widgets=[gen.label(title, gen.f(700, 22), id=f"card{c}_title", width=656, height=28, long_mode="DOT",
                               hidden=not title)] + [
                gen.plain(id=f"card{c}_r{n}", width=656, height="SIZE_CONTENT", hidden=not text,
                          layout={"type": "flex", "flex_flow": "ROW", "flex_align_cross": "CENTER",
                                  "pad_column": 12},
                          widgets=[gen.label(glyph(icon), CARD_FONT, "0x" + screen.COLORS.get(icon, "FFFFFF"),
                                             id=f"card{c}_i{n}", width=26),
                                   gen.label(text, gen.f(500, 21), id=f"card{c}_t{n}", width=618, height=26,
                                             long_mode="DOT")])
                for n, (icon, text) in enumerate(rows)]))
    return gen.plain(id="cards", align="TOP_MID", y=CARDS_Y, width=700, height="SIZE_CONTENT", hidden=not example,
                     layout={"type": "flex", "flex_flow": "COLUMN", "pad_row": 8}, widgets=boxes)


# The reply's box under the orb: from y=392 to 14 px above the bottom. In it 30 px holds five lines, 22 px seven
# and 19 px eight (about 600 characters); all three fonts are already in gen.FONTS. A longer reply rolls in the
# box at 22 px (REPLY_SCROLL_FONT), which reads from across the room.
REPLY_Y, REPLY_WIDTH = 392, 820
REPLY_HEIGHT = 600 - REPLY_Y - 14
REPLY_FONTS = [gen.f(600, 30), gen.f(600, 22), gen.f(600, 19)]
REPLY_SCROLL_FONT = 1
# Your sentence: two lines of 22 px over the reply, one over cards (they start at CARDS_Y).
YOU_Y, YOU_LINE = 316, 29
# ...or, when it does not fit there, all of it at 19 px in up to three lines down to the reply (Joel, 2026-11-01:
# a long question ended in "…"); over cards one line, 19 px when 22 px is too long (reply_fit.h's show_you).
YOU_MAX_H = REPLY_Y - YOU_Y
YOU_FONTS = (gen.f(600, 22), gen.f(600, 19))


def fit_you(max_h, lines):
    big, small = YOU_FONTS
    return (f"panel_reply::show_you(id(you_lbl), id({big})->get_lv_font(), id({small})->get_lv_font(), 760, "
            f"{max_h}, {lines});\n")


CARDS_Y = 350
# C++: the reply on the voice page from id(reply_text): one paragraph, all of it in the biggest font where it
# fits, or rolling with the voice (reply_fit.h).
SHOW_REPLY = ("const lv_font_t *fonts[] = {" + ", ".join(f"id({f})->get_lv_font()" for f in REPLY_FONTS) + "};\n"
              f"panel_reply::show(id(reply_lbl), id(reply_text), fonts, {len(REPLY_FONTS)}, "
              f"{REPLY_WIDTH}, {REPLY_HEIGHT}, {REPLY_SCROLL_FONT});")


# C++: fill the cards from x, sensor.jarvis_chat's last_card (server/agents/screen.py): "@stamp", ">summary", then
# per card "#title" and per row "glyph|RRGGBB|text". Cards show instead of the reply's text, with the summary in
# full white where your sentence was; a summary alone replaces the reply's text; "" shows the text again.
FILL_CARDS = ("if (x == id(last_card_rendered)) return;\nid(last_card_rendered) = x;\n"
              "lv_obj_t *box[] = {" + ", ".join(f"id(card{c})" for c in range(CARDS)) + "};\n"
              "lv_obj_t *title[] = {" + ", ".join(f"id(card{c}_title)" for c in range(CARDS)) + "};\n"
              + "".join(f"lv_obj_t *{k}{c}[] = {{" + ", ".join(f"id(card{c}_{k}{n})" for n in range(CARD_ROWS)) + "};\n"
                        for c in range(CARDS) for k in ("r", "i", "t"))
              + "lv_obj_t **row[] = {" + ", ".join(f"r{c}" for c in range(CARDS)) + "};\n"
              "lv_obj_t **icon[] = {" + ", ".join(f"i{c}" for c in range(CARDS)) + "};\n"
              "lv_obj_t **text[] = {" + ", ".join(f"t{c}" for c in range(CARDS)) + "};\n"
              f"for (int c = 0; c < {CARDS}; c++) {{\n"
              "  lv_obj_add_flag(box[c], LV_OBJ_FLAG_HIDDEN);\n"
              f"  for (int n = 0; n < {CARD_ROWS}; n++) lv_obj_add_flag(row[c][n], LV_OBJ_FLAG_HIDDEN);\n"
              "}\n"
              "int c = -1, n = 0;\n"
              "std::string summary;\n"
              "size_t start = 0;\n"
              "while (start < x.size()) {\n"
              "  size_t end = x.find('\\n', start);\n"
              "  if (end == std::string::npos) end = x.size();\n"
              "  std::string line = x.substr(start, end - start);\n"
              "  start = end + 1;\n"
              "  if (line.empty() || line[0] == '@') continue;\n"
              "  if (line[0] == '>') {\n"
              "    summary = line.substr(1);\n"
              "    continue;\n"
              "  }\n"
              "  if (line[0] == '#') {\n"
              f"    if (++c >= {CARDS}) break;\n"
              "    n = 0;\n"
              "    lv_label_set_text(title[c], line.c_str() + 1);\n"
              "    if (line.size() > 1) lv_obj_remove_flag(title[c], LV_OBJ_FLAG_HIDDEN);\n"
              "    else lv_obj_add_flag(title[c], LV_OBJ_FLAG_HIDDEN);\n"
              "    lv_obj_remove_flag(box[c], LV_OBJ_FLAG_HIDDEN);\n"
              "    continue;\n"
              "  }\n"
              "  size_t a = line.find('|'), b = a == std::string::npos ? a : line.find('|', a + 1);\n"
              f"  if (c < 0 || n >= {CARD_ROWS} || b == std::string::npos) continue;\n"
              "  std::string colour = line.substr(a + 1, b - a - 1);\n"
              "  lv_label_set_text(icon[c][n], line.substr(0, a).c_str());\n"
              "  lv_obj_set_style_text_color(icon[c][n], lv_color_hex(colour.empty() ? 0xFFFFFF : "
              "strtoul(colour.c_str(), nullptr, 16)), LV_PART_MAIN);\n"
              "  lv_label_set_text(text[c][n], line.c_str() + b + 1);\n"
              "  lv_obj_remove_flag(row[c][n], LV_OBJ_FLAG_HIDDEN);\n"
              "  n++;\n"
              "}\n"
              "if (c >= 0) {\n"
              "  lv_obj_add_flag(id(reply_lbl), LV_OBJ_FLAG_HIDDEN);\n"
              "  lv_obj_remove_flag(id(cards), LV_OBJ_FLAG_HIDDEN);\n"
              "  if (!summary.empty()) {\n"
              "    lv_label_set_text(id(you_lbl), summary.c_str());\n"
              "    lv_obj_set_style_text_opa(id(you_lbl), LV_OPA_COVER, LV_PART_MAIN);\n"
              "  }\n"
              "  " + fit_you(YOU_LINE, 1) +
              "} else {\n"
              "  " + fit_you(YOU_MAX_H, 2) +
              "  lv_obj_add_flag(id(cards), LV_OBJ_FLAG_HIDDEN);\n"
              "  lv_obj_remove_flag(id(reply_lbl), LV_OBJ_FLAG_HIDDEN);\n"
              "  if (!summary.empty()) {\n"
              "    id(reply_text) = summary;\n"
              "    id(show_reply).execute();\n"
              "  }\n"
              "}\n")


def orb_page(final, with_card=False):
    """The voice page. Your sentence is two lines (a long one ends in "…"); under it the reply, or its card."""
    return {
        "id": "page_orb", "bg_color": "0x05060C", "pad_all": 0, "scrollable": False,
        "widgets": [
            {"image": {"src": "art", "x": 0, "y": 0}},
            {"obj": {"x": 0, "y": 0, "width": 1024, "height": 600, "bg_color": "0x000000", "bg_opa": "55%",
                     "border_width": 0, "radius": 0, "pad_all": 0, "scrollable": False, "clickable": False}},
            anim("orb_listen", frames("listen"), "5200ms", hidden=final),
            anim("orb_think", frames("listen"), "1800ms"),
            anim("orb_morph", frames("morph"), "650ms", once=True),
            anim("orb_speak", frames("speak"), "3000ms", hidden=not final),
            anim("orb_settle", frames("settle"), "600ms", once=True),
            gen.label((CARD_SUMMARY if with_card else gen.SAID) if final else LISTENING, gen.f(600, 22),
                      text_opa="COVER", id="you_lbl", align="TOP_MID", y=YOU_Y, width=760,
                      height=YOU_LINE if with_card else 2 * YOU_LINE, long_mode="DOT", text_align="CENTER"),
            # Clipped, untouchable box the reply rolls in (reply_fit.h); a tap goes through to the page.
            gen.plain(id="reply_box", align="TOP_MID", y=REPLY_Y, width=REPLY_WIDTH, height=REPLY_HEIGHT,
                      radius=0, clickable=False, scrollbar_mode="OFF",
                      widgets=[gen.label(gen.REPLY if final else "", REPLY_FONTS[0], id="reply_lbl", align="TOP_MID",
                                         y=0, width=REPLY_WIDTH, text_align="CENTER", hidden=with_card)]),
            cards(with_card),
        ],
    }


def ota_page():
    """The update screen (board.py's OTA triggers): the chosen picture, darkened, with a ring that fills with the
    upload and the percent in it. Nothing animates: OTA holds the main loop, so board.py redraws every 5 %."""
    size, cx, cy = 260, 512, 250
    return {
        "id": "page_ota", "bg_color": "0x05060C", "pad_all": 0, "scrollable": False,
        "widgets": [
            {"image": {"src": "art", "x": 0, "y": 0}},
            {"obj": {"x": 0, "y": 0, "width": 1024, "height": 600, "bg_color": "0x000000", "bg_opa": "55%",
                     "border_width": 0, "radius": 0, "pad_all": 0, "scrollable": False, "clickable": False}},
            {"arc": {"id": "ota_arc", "x": cx - size // 2, "y": cy - size // 2, "width": size, "height": size,
                     "min_value": 0, "max_value": 100, "value": 0, "rotation": 270, "start_angle": 0,
                     "end_angle": 360, "adjustable": False, "clickable": False, "arc_width": 4,
                     "arc_color": gen.WHITE, "arc_opa": "18%", "bg_opa": "TRANSP",
                     "indicator": {"arc_width": 4, "arc_color": "0x8FD3FF", "arc_rounded": True},
                     "knob": {"bg_opa": "TRANSP", "pad_all": 0}}},
            gen.label("0 %", gen.f(600, 30), id="ota_pct", x=0, y=cy - 18, width=1024, text_align="CENTER"),
            gen.label("Jarvis uppdaterar sig", gen.f(600, 30), id="ota_title", align="TOP_MID", y=cy + size // 2 + 34,
                      width=820, text_align="CENTER"),
            gen.label("Dra inte ur sladden", gen.f(500, 21), sec=True, id="ota_sub", align="TOP_MID",
                      y=cy + size // 2 + 80, width=820, text_align="CENTER"),
        ],
    }


def only(state):
    """Show one orb state and hide the others."""
    return [{"lvgl.widget.hide": [s for s in STATES if s != state]}, {"lvgl.widget.show": state}]


def demo_script():
    """The flow with delays, for the simulator. On the board the same steps hang off
    voice_assistant: on_wake_word_detected -> fade in, listening; on_stt_end -> your
    sentence, thinking; on_tts_start -> morph, speaking, reveal the reply paced to the
    speech; on_end -> settle, then fade back to the dashboard."""
    words = gen.REPLY.split(" ")
    steps = [{"delay": "4500ms"},   # time for a recorder to find the window
             {"lvgl.label.update": {"id": "you_lbl", "text": LISTENING}},
             {"lvgl.label.update": {"id": "reply_lbl", "text": ""}},
             *only("orb_listen"),
             {"lvgl.page.show": {"id": "page_orb", "animation": "FADE_IN", "time": "400ms"}},
             {"delay": "2200ms"},
             {"lvgl.label.update": {"id": "you_lbl", "text": gen.SAID}},
             *only("orb_think"),
             {"delay": "1500ms"},
             *only("orb_morph"), {"lvgl.animimg.start": "orb_morph"},
             {"delay": "650ms"},
             *only("orb_speak")]
    for k in range(1, len(words) + 1):
        steps += [{"lvgl.label.update": {"id": "reply_lbl", "text": " ".join(words[:k])}}, {"delay": "230ms"}]
    steps += [{"delay": "1200ms"},
              *only("orb_settle"), {"lvgl.animimg.start": "orb_settle"},
              {"delay": "1800ms"},
              {"lvgl.page.show": {"id": "page_glass_v2", "animation": "FADE_IN", "time": "400ms"}}]
    return [{"id": "demo", "then": steps}]


def remote_images(cfg, base, demo):
    """Current dashboard/art/glass backgrounds, then a transparent atlas shared by any painting."""
    frame_ids = {f"{k}_{i:02d}" for k, n in SETS.items() for i in range(n)}
    ids = ["bg", "art", "voice_bg"]
    for page in cfg["lvgl"]["pages"]:
        if page["id"] == "page_orb":
            page["widgets"][0]["image"]["src"] = "voice_bg"
            del page["widgets"][1]  # tint/frost are cached on Jarvis, avoiding a blend every frame
    keep = [im for im in cfg["image"] if im["id"] not in frame_ids | set(ids)]
    # While downloading, all states show the compiled boot orb. The atlas installs real sources
    # directly after decoding, with descriptors into PSRAM rather than extra image buffers.
    for page in cfg["lvgl"]["pages"]:
        for widget in page["widgets"]:
            if "animimg" in widget and widget["animimg"]["id"] in STATES:
                widget["animimg"]["src"] = ["boot_orb"]
    cfg["esphome"].setdefault("includes", []).extend(
        [str(HERE.parent.parent / "bench" / "vsync_clock.h")] +
        [str(HERE / name) for name in ("prefetch_slots.h", "packed_atlas.h", "packed_loader.h", "virtual_frames.h", "voice_wave.h", "packed_prefetch.h", "orb_director.h", "packed_orb.h")])
    cfg["esphome"].setdefault("build_flags", []).extend([
        "-DLV_USE_LZ4=1", "-DLV_USE_LZ4_INTERNAL=1", "-DLV_BIN_DECODER_RAM_LOAD=1"])
    keep.append({"platform": "file", "file": "assets/mic_small.png", "id": "blank", "type": "RGB565",
                 "transparency": "alpha_channel", "resize": "1x1"})
    # Every page's background widget gets an id (the dashboard and Hemmet both show bg), so it can be
    # re-pointed once its image is in.
    backgrounds = []
    for page in cfg["lvgl"]["pages"]:
        for w in page["widgets"]:
            if "image" in w and w["image"].get("src") in ("bg", "bg_orb", "art", "voice_bg"):
                w["image"]["id"] = "orb_bg_img" if w["image"]["src"] == "bg_orb" and page["id"] == "page_orb" else "tavla_art" \
                    if w["image"]["src"] == "art" and page["id"] == "page_tavla" else "dash_bg" if page["id"] == gen.DASH else f"{page['id']}_bg"
                backgrounds.append((w["image"]["id"], w["image"]["src"]))
    online = []
    # bg is downloaded when Jarvis publishes a new background, and then art, the same
    # picture without glass: once the boot chain is done, a new one only repaints the pages that show it
    # instead of starting the chain over.
    loaded = {"lambda": "return id(assets_loaded);"}

    def repaint(src):
        return [step for wid, s in backgrounds if s == src
                for step in ({"lvgl.image.update": {"id": wid, "src": "blank"}},
                             {"lvgl.image.update": {"id": wid, "src": src}})]
    # a new bg also gets the shared tab bar's glass cut out of it again (gen.py's shared_bar, bar_cut.h)
    cut = [{"lambda": gen.BAR_CUT_CALL}]
    # OnlineImage calls its success automation before closing the HTTP response.
    # Yield before opening the next image so boot/refresh do not accumulate sockets.
    # a background just picked in Inställningar closes the sheet once it is painted (gen.py bg_shown)
    morning = {"bg": repaint("bg") + cut + [{"script.execute": "bg_shown"}, {"delay": "1ms"}, {"component.update": "art"}],
               "art": repaint("art") + [{"delay": "1ms"}, {"component.update": "voice_bg"}],
               "voice_bg": repaint("voice_bg")}
    # The compiled brand mark is available before networking. Keep the status
    # still during a normal boot; only a genuinely long wait needs an explanation.
    cfg["lvgl"].setdefault("top_layer", {}).setdefault("widgets", []).append(
        {"obj": {"id": "boot_cover", "x": 0, "y": 0, "width": 1024, "height": 600, "bg_color": BOOT_BG,
                 "bg_opa": "COVER", "border_width": 0, "radius": 0, "pad_all": 0, "scrollable": False,
                 "widgets": [{"image": {"id": "boot_brand_img", "src": "blank", "x": 352, "y": 70}},
                             gen.label("Jarvis", gen.f(400, 56), align="TOP_MID", y=332, text_align="CENTER"),
                             gen.label("Startar …", gen.f(500, 16), sec=True, id="boot_lbl", align="TOP_MID", y=414,
                                       width=600, text_align="CENTER")]}})
    cfg.setdefault("globals", []).extend([
        {"id": "boot_shown", "type": "bool", "initial_value": "false"},
        {"id": "backgrounds_loaded", "type": "bool", "initial_value": "false"},
        {"id": "orb_loaded", "type": "bool", "initial_value": "false"},
        *[{"id": f"{name}_valid", "type": "bool", "initial_value": "false"} for name in ids],
        *[{"id": f"{name}_attempts", "type": "unsigned", "initial_value": "0"} for name in ids],
        *[{"id": f"boot_{name}_ms", "type": "uint32_t", "initial_value": "0"}
          for name in ("ui", "light", "shown", "sound", "data", "fade_start", "fade_end", "wifi", "bg", "backgrounds", "orb", "voice_client")]])
    boot_cache.install(cfg)
    # All visible dashboard values must have received their first HA state. Empty
    # calendar/chat values are valid; waiting for nonempty text would never finish.
    initial_fields = ["hm_w_temp", "hm_w_line", "hm_w_tip", "hm_w_icon", "hm_w_days", "hm_sl_walk",
                      *[f"hm_sl{i}" for i in range(4)], "hm_cal_head",
                      *[f"hm_cal{i}" for i in range(3)], "chat_said", "chat_reply", "chat_card", "chat_working"]
    content_ready = "id(bg_valid) && id(ha_time).now().is_valid() && " + " && ".join(
        f"id({name}).has_state()" for name in initial_fields)
    boot_join = (f"const bool complete = {content_ready};\n"
                 "if (complete && !id(boot_data_ms)) id(boot_data_ms) = millis();\n" +
                 ("if (id(bg_valid) && id(boot_cache_available)) { id(boot_cache_used) = !complete && id(boot_cache_active); return true; }\n" if gen.BOARD else "") +
                 "return complete || id(h_smoke_alarm) || id(h_water_alarm) || millis() >= 30000;")
    cfg.setdefault("script", []).append({"id": "boot_done", "mode": "single", "then": [
        {"if": {"condition": {"lambda": "return !id(boot_shown);"}, "then": [
            {"wait_until": {"condition": {"lambda": boot_join}}},
            {"script.execute": "clock_update"},
            {"lambda": "id(boot_shown) = true;"},
            {"lvgl.widget.hide": "boot_cover"},
            # Yield to the ordinary LVGL loop. Never force a nested refresh from
            # OnlineImage's completion callback while its HTTP/decoder frame is live.
            {"wait_until": {"condition": {"lambda": "return id(boot_shown_ms) != 0;"}, "timeout": "1s"}}]}}]})
    uncover = [{"script.execute": "boot_done"}]
    cfg.setdefault("interval", []).append({"interval": "30s", "startup_delay": "30s",
                                           "then": [{"script.execute": "boot_done"}]})
    cfg["interval"].append({"interval": "5s", "startup_delay": "15s", "then": [{"if": {
        "condition": {"lambda": "return !id(boot_shown);"}, "then": [{"lambda":
            'lv_label_set_text(id(boot_lbl), id(bg_valid) ? "Hämtar information …" : id(boot_wifi_ms) ? "Bilden tar lite längre tid …" : "Väntar på hemnätet …");'}]}}]})
    for n, iid in enumerate(ids):
        nxt = ([{"component.update": ids[n + 1]}] if n + 1 < len(ids) else [
            {"lambda": "id(backgrounds_loaded) = true;\n"
                        "if (id(bg_valid) && id(art_valid) && id(voice_bg_valid) && !id(boot_backgrounds_ms)) "
                        "id(boot_backgrounds_ms) = millis();"},
            {"script.execute": "assets_ready"}])
        # A background shows the moment it is in (about 4 s after Wi-Fi), not after the orb's 60-odd frames;
        # bg also lifts the black boot cover, so the cards and the painting appear together.
        first = ([{"lambda": "if (!id(boot_bg_ms)) id(boot_bg_ms) = millis();"}] if iid == "bg" else []) + repaint(iid) + (cut + uncover) * (iid == "bg") + [{"delay": "1ms"}] + nxt
        done = ([{"lambda": f"id({iid}_valid) = true; id({iid}_attempts) = 0;"}] +
                ([{"if": {"condition": loaded, "then": morning[iid], "else": first}}] if iid in morning else first))
        # A failed download calls on_error from inside the next update, so without the delay a chain of
        # failures nests until loopTask's stack runs out (seen on the board when the network was not up yet).
        exhausted = (uncover if iid == "bg" else []) + [{"delay": "100ms"}, *nxt]
        failed = [{"if": {"condition": {"not": loaded}, "then": [
            {"lambda": f"++id({iid}_attempts);"},
            {"if": {"condition": {"lambda": f"return id({iid}_attempts) < 3;"},
                    "then": [{"delay": "250ms"}, {"component.update": iid}], "else": exhausted}}]}}]
        # The C6 SDIO receive queue stalled in a real boot with 32 KiB reads,
        # blocking loopTask for 5.5 s. Keep the proven 8 KiB streaming budget.
        online.append({"platform": "online_image", "id": iid, "url": f"{base}/{iid}.qoi", "format": "QOI",
                       "buffer_size": 8192,
                       "type": "RGB565",
                       # These three backgrounds are opaque; the separate HPA3 orb atlas carries alpha.
                       "transparency": "opaque",
                       "on_download_finished": done,
                       "on_error": [{"logger.log": {"format": f"Could not download {iid}", "level": "WARN"}}, *failed]})
    cfg["image"] = keep + online
    # the boot screen's orb is in the firmware, since the orb frames are downloaded: one plain frame, ~150 kB
    cfg["image"].append({"platform": "file", "file": "assets/orb/orb_00.png", "id": "boot_orb", "type": "RGB565",
                         "transparency": "alpha_channel"})
    # Firmware code/rodata are copied to PSRAM. Keep the identical brand pixels
    # compressed there; LVGL releases its transient decode when the atlas disables
    # the image cache. Bind after widget creation (395), before first render (390).
    cfg["esphome"]["includes"].append(str(HERE / "boot_logo.h"))
    cfg["esphome"].setdefault("on_boot", []).append({"priority": 394, "then": [
        {"lambda": "lv_image_set_src(id(boot_brand_img), boot_brand::image());"}]})
    cfg["http_request"] = {"id": "asset_http", "timeout": "2s"}
    panel_counts = orb_atlas.LAYOUTS[orb_atlas.PANEL_VERSION][1]
    counts = ", ".join(str(n) for n in panel_counts.values())
    url = json.dumps(f"{base}/orb_atlas_v{orb_atlas.PANEL_VERSION}.lz4")
    cfg.setdefault("script", []).append({"id": "orb_load", "mode": "single", "then": [
        {"lambda": f"packed_orb::loader.begin(id(asset_http), {url}, {sum(panel_counts.values())});"},
        {"while": {"condition": {"lambda": "return !packed_orb::loader.done();"}, "then": [
            {"lambda": "packed_orb::loader.step();"}, {"delay": "1ms"}]}},
        {"if": {"condition": {"lambda": "return packed_orb::loader.ok();"}, "then": [
            {"lambda": f"id(orb_loaded) = packed_orb::install<{counts}>(packed_orb::loader.atlas(), id(orb_listen), "
                       "id(orb_think), id(orb_morph), id(orb_speak), id(orb_settle));"}]}},
        # Production frames from the director (orb_director.h): eased speed changes and cross-faded states.
        {"lambda": "if (id(orb_loaded)) { packed_orb::direct(); id(boot_orb_ms) = millis(); }"},
        {"script.execute": "assets_ready"}]})
    if "host" in cfg:   # the simulator's HTTP client needs OpenSSL; Homebrew's on the Mac
        ssl = "/opt/homebrew/opt/openssl@3"
        cfg["esphome"]["platformio_options"] = {"build_flags": [f"-I{ssl}/include", f"-L{ssl}/lib", "-lssl", "-lcrypto"]}
    # Re-point every widget once the downloads are done, so LVGL reads the real image sizes.
    ready = [{"logger.log": "All panel images downloaded"}, {"lambda": "id(assets_loaded) = true;"}]
    cfg.setdefault("globals", []).append({"id": "assets_loaded", "type": "bool", "initial_value": "false"})
    for wid, src in backgrounds:
        ready += [{"lvgl.image.update": {"id": wid, "src": "blank"}}, {"lvgl.image.update": {"id": wid, "src": src}}]
    if demo:
        ready.append({"script.execute": "demo"})
    cfg.setdefault("script", []).append({"id": "assets_ready", "then": [{"if": {
        "condition": {"lambda": "return !id(assets_loaded) && id(backgrounds_loaded) && id(orb_loaded) && "
                                  "id(bg_valid) && id(art_valid) && id(voice_bg_valid);"},
        "then": ready}}]})
    # A new background on Jarvis (another choice in select.jarvis_bakgrund, a new link, NASA's new picture):
    # sensor.jarvis_hemmet's bg_version changes, so fetch it again; bg then repaints and fetches art as in the
    # morning. The first value after boot is only noted.
    cfg.setdefault("text_sensor", []).append({
        "platform": "homeassistant", "id": "bg_choice", "entity_id": "sensor.jarvis_hemmet", "attribute": "bg_version",
        "on_value": [{"if": {"condition": {"lambda": "static std::string last;\n"
                                                     "bool changed = !last.empty() && x != last;\n"
                                                     "last = x;\nreturn changed && id(assets_loaded);"},
                             "then": [{"delay": "3s"}, {"component.update": "bg"}]}}]})
    boot = cfg["esphome"].get("on_boot", [])   # added to, so gen.py's bar_hook stays
    cfg["esphome"]["on_boot"] = [*(boot if isinstance(boot, list) else [boot]),
                                 {"priority": -100, "then": [
                                     {"script.execute": "orb_load"},
                                     {"component.update": ids[0]}]}]
    # Tavla's Earth picture: its own download after the backgrounds, never in the boot chain (tavla_earth.py).
    import tavla_earth
    tavla_earth.remote(cfg, base)


def config(demo=False, start_orb=False, remote=None, bg="assets/bg_starry.png", start=None, with_card=False,
           listening=False):
    cfg = gen.config(bg, demo=False, start_orb=False, start=start)
    cfg["esphome"] = {**cfg["esphome"], "name": "style-glass-jarvis"}   # keeps gen.py's includes
    cfg["esphome"]["includes"] = cfg["esphome"]["includes"] + [str(HERE / "reply_fit.h")]
    if demo:
        cfg["esphome"]["on_boot"] = {"priority": -100, "then": [{"script.execute": "demo"}]}
        cfg["script"] = cfg.get("script", []) + demo_script()
    images = [im for im in cfg["image"] if not im["id"].startswith("orb_") and im["id"] != "bg_orb"]
    for key, n in SETS.items():
        images += [{"platform": "file", "file": str(FRAMES / f"{key}_{i:02d}.png"), "id": f"{key}_{i:02d}",
                    "type": "RGB565", "transparency": "alpha_channel"} for i in range(n)]
    cfg["image"] = images
    # The cards' icons: every glyph Jarvis can pick, and the text sensor that fills them.
    cfg["font"].append({"file": screen.FONT_URL, "id": CARD_FONT, "size": 24, "bpp": 4,
                        "glyphs": [chr(int(code, 16)) for code in screen.ICONS.values()]})
    cfg.setdefault("text_sensor", []).extend([
        {"platform": "homeassistant", "id": "chat_card", "entity_id": gen.CHAT, "attribute": "last_card",
         "on_value": [{"lambda": FILL_CARDS}]},
        # What Jarvis says before his tools, shown while he works, unless cards are already up.
        {"platform": "homeassistant", "id": "chat_working", "entity_id": gen.CHAT, "attribute": "working",
         "on_value": [{"if": {"condition": {"lambda": "return !x.empty() && "
                                                      "lv_obj_has_flag(id(cards), LV_OBJ_FLAG_HIDDEN);"},
                              "then": [{"lambda": "id(reply_text) = x;"}, {"script.execute": "show_reply"}]}}]}])
    cfg.setdefault("globals", []).append({"id": "reply_text", "type": "std::string"})
    cfg["globals"].append({"id": "last_card_rendered", "type": "std::string"})
    cfg.setdefault("script", []).append({"id": "show_reply", "mode": "restart", "then": [{"lambda": SHOW_REPLY}]})
    others = [p for p in cfg["lvgl"]["pages"] if p["id"] != "page_orb"]
    cfg["lvgl"]["pages"] = [orb_page(not listening, with_card)] + others if start_orb else others + [orb_page(False)]
    cfg["lvgl"]["pages"].append(ota_page())
    gen.bar_events(cfg["lvgl"]["pages"])
    gen.bar_hook(cfg)   # the voice page and the update screen hide the bar too
    if remote:
        remote_images(cfg, remote.rstrip("/"), demo)
        if not gen.BOARD:
            plaque_from(cfg, remote.rstrip("/"))
            sim_choices(cfg, remote.rstrip("/"))
    return cfg


def plaque_from(cfg, base):
    """The simulator has no Home Assistant to fill Tavla's plaque: put today's title and credit from
    base/apod.json (server/apod.py) in as its text, so it matches the picture it downloads."""
    try:
        with urllib.request.urlopen(f"{base}/apod.json", timeout=3) as r:
            art = json.load(r)
    except (OSError, ValueError):
        return
    texts = {"t_title": art.get("title") or "", "t_credit": art.get("credit") or ""}
    for page in cfg["lvgl"]["pages"]:
        for w in page["widgets"]:
            for child in (w.get("obj") or {}).get("widgets") or []:
                lbl = child.get("label") or {}
                if lbl.get("id") in texts:
                    lbl["text"] = texts[lbl["id"]]


def sim_choices(cfg, base):
    """The simulator has no Home Assistant for Inställningar's Bakgrund list either. When base/choices.json
    exists (tools/sim_backgrounds.py makes it: [{"name", "dir", "title", "credit"}], at most gen.PICKS), the
    list shows those names and picking one points bg, art and voice_bg at base/<dir>/ and downloads them
    again, like a new bg_version does on the board. Without the file nothing changes."""
    def cstr(text):                    # a C++ string literal, UTF-8 as it is
        return json.dumps(text, ensure_ascii=False)
    try:
        with urllib.request.urlopen(f"{base}/choices.json", timeout=3) as r:
            choices = json.load(r)[:gen.PICKS]
    except (OSError, ValueError):
        return
    if not choices:
        return
    names = "\n".join(str(c["name"]) for c in choices)
    cfg["esphome"]["on_boot"].append({"priority": -100, "then": [{"lambda":
        f"choice_list::bg.options({cstr(names)});\n"
        "lv_dropdown_set_options(id(s_bg)->obj, choice_list::bg.joined().c_str());\n"
        f"choice_list::bg.current = {cstr(choices[0]['name'])};\n"
        "lv_dropdown_set_selected(id(s_bg)->obj, 0);"}]})
    picks = {f"bg_pick_{i}": c for i, c in enumerate(choices)}
    for script in cfg["script"]:
        c = picks.get(script["id"])
        if c is None:
            continue
        folder = f"{base}/{c['dir']}" if c.get("dir") else base
        script["then"] = [{"lambda": "".join(f"id({iid}).set_url({cstr(f'{folder}/{iid}.qoi')});\n"
                                             for iid in ("bg", "art", "voice_bg")) +
                           f"choice_list::bg.current = {cstr(c['name'])};\n"
                           f"lv_label_set_text(id(t_title), {cstr(c.get('title') or '')});\n"
                           f"lv_label_set_text(id(t_credit), {cstr(c.get('credit') or '')});\n"
                           f"lv_label_set_text(id(s_bg_note), {cstr(gen.BG_NOTE)});\n"
                           f'ESP_LOGI("sim", "background: %s", {cstr(c["name"])});'},
                          {"component.update": "bg"}]


if __name__ == "__main__":
    remote = sys.argv[sys.argv.index("--remote") + 1] if "--remote" in sys.argv else None
    bg = sys.argv[sys.argv.index("--bg") + 1] if "--bg" in sys.argv else "assets/bg_starry.png"
    cfg = config("--demo" in sys.argv, "--orb" in sys.argv, remote, bg, gen.start_page(sys.argv),
                 with_card="--card" in sys.argv, listening="--listening" in sys.argv)
    if "--then" in sys.argv:
        boot = cfg["esphome"].get("on_boot", [])
        cfg["esphome"]["on_boot"] = [*(boot if isinstance(boot, list) else [boot]),
                                     {"priority": -200, "then": [{"delay": "8s"},
                                                                 *json.loads(sys.argv[sys.argv.index("--then") + 1])]}]
    json.dump(cfg, sys.stdout, ensure_ascii=False, indent=1)
