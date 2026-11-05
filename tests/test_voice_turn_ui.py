"""Run generated follow-up UI actions against real native LVGL; no microphone or model."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'styles/glass-v2'))
import jarvis_orb as orb  # noqa: E402


def visual_actions(actions):
    result = []
    for action in actions:
        if 'lambda' in action:
            result.append(action['lambda'])
        elif 'lvgl.label.update' in action:
            update = action['lvgl.label.update']
            value = 'x.c_str()' if isinstance(update['text'], dict) else json.dumps(update['text'], ensure_ascii=False)
            result.append(f'lv_label_set_text({update["id"]}, {value});')
        else:
            for key, function in (('lvgl.widget.hide', 'lv_obj_add_flag'), ('lvgl.widget.show', 'lv_obj_remove_flag')):
                if key in action:
                    names = action[key] if isinstance(action[key], list) else [action[key]]
                    result.extend(f'{function}({name}, LV_OBJ_FLAG_HIDDEN);' for name in names)
    return '\n'.join(result)


class VoiceTurnUiTest(unittest.TestCase):
    def test_new_followup_clears_previous_reply_and_cards_before_thinking(self):
        cfg = json.loads(subprocess.run([sys.executable, str(ROOT / 'board.py'), '--voice', 'jarvis_orb.py',
                                        '--remote', 'http://example.invalid/assets'], cwd=ROOT / 'styles/glass-v2',
                                       check=True, capture_output=True, text=True).stdout)
        build = ROOT / 'styles/glass-v2/.esphome/build/style-glass-v2'
        libraries = list(build.glob('.pioenvs/*/lib*/liblvgl.a'))
        headers = list(build.glob('.piolibdeps/*/lvgl/lvgl.h'))
        self.assertEqual((len(libraries), len(headers)), (1, 1), 'Native LVGL simulator dependency required')
        scripts = {s['id']: s for s in cfg['script']}
        card_widgets = []
        def collect(widgets):
            for widget in widgets:
                for kind, body in widget.items():
                    if not isinstance(body, dict):
                        continue
                    name = body.get('id', '')
                    if name.startswith('card') and name != 'cards':
                        card_widgets.append((name, 'lv_label_create' if kind == 'label' else 'lv_obj_create'))
                    collect(body.get('widgets', []))
        collect(next(p for p in cfg['lvgl']['pages'] if p['id'] == 'page_orb')['widgets'])
        card_callback = next(s for s in cfg['text_sensor'] if s['id'] == 'chat_card')['on_value'][0]['lambda']
        source = '''
#include "lvgl.h"
#include <cassert>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <string>
extern "C" {
void lv_mem_init() {}
void lv_mem_deinit() {}
void *lv_malloc_core(size_t n) { return std::malloc(n); }
void lv_free_core(void *p) { std::free(p); }
void *lv_realloc_core(void *p, size_t n) { return std::realloc(p, n); }
lv_result_t lv_mem_test_core() { return LV_RESULT_OK; }
void lv_mem_monitor_core(lv_mem_monitor_t *p) { std::memset(p, 0, sizeof(*p)); }
}
#include "reply_fit.h"
#define id(x) x
// The board's three reply fonts, by their line heights; the simulator has only Montserrat 14 for the glyphs.
struct TestFont {
  lv_font_t font;
  explicit TestFont(int32_t line_height) : font(lv_font_montserrat_14) { font.line_height = line_height; }
  const lv_font_t *get_lv_font() const { return &font; }
};
TestFont font30(36), font22(27), font19(23);
TestFont *i600_30 = &font30, *i600_22 = &font22, *i600_19 = &font19;
lv_obj_t *reply_box, *reply_lbl, *you_lbl, *cards, *orb_listen, *orb_think, *orb_morph, *orb_speak, *orb_settle;
bool orb_speaking = true;
std::string reply_text = "Previous answer";
std::string last_card_rendered;
''' + '\n'.join(f'lv_obj_t *{name};' for name, _ in card_widgets) + '''
uint32_t voice_text_ready_us;
uint32_t micros() { return 1; }
int64_t esp_timer_get_time() { return 1; }
#include "''' + str(ROOT / 'bench/speaker_gaps.h') + '''"
void listen() {
''' + visual_actions(cfg['voice_assistant']['on_listening']) + '''
}
void recognized(const std::string &x) {
''' + visual_actions(cfg['voice_assistant']['on_stt_end']) + '''
}
void render_reply() {
''' + scripts['show_reply']['then'][0]['lambda'] + '''
}
struct ReplyScript { void execute() { render_reply(); } } show_reply;
void receive_card(const std::string &x) {
''' + card_callback + '''
}
int main() {
  lv_init();
  auto *display = lv_display_create(1024, 600);
  auto *screen = lv_screen_active();
  // as jarvis_orb.py builds it: the reply's label in its clipped box
  reply_box = lv_obj_create(screen);
  lv_obj_set_size(reply_box, REPLY_BOX);
  lv_obj_set_style_pad_all(reply_box, 0, LV_PART_MAIN);
  lv_obj_set_style_border_width(reply_box, 0, LV_PART_MAIN);
  reply_lbl = lv_label_create(reply_box); you_lbl = lv_label_create(screen);
  lv_obj_set_width(reply_lbl, REPLY_W);
  cards = lv_obj_create(screen); orb_listen = lv_obj_create(screen); orb_think = lv_obj_create(screen);
  orb_morph = lv_obj_create(screen); orb_speak = lv_obj_create(screen); orb_settle = lv_obj_create(screen);
''' + '\n'.join(f'  {name} = {factory}(screen);' for name, factory in card_widgets) + '''
  lv_label_set_text(reply_lbl, "Previous answer");
  receive_card("@old-turn\\n>Previous answer");
  listen();
  assert(reply_text.empty() && std::strlen(lv_label_get_text(reply_lbl)) == 0);
  assert(lv_obj_has_flag(cards, LV_OBJ_FLAG_HIDDEN));
  assert(!lv_obj_has_flag(reply_lbl, LV_OBJ_FLAG_HIDDEN));
  assert(!lv_obj_has_flag(orb_listen, LV_OBJ_FLAG_HIDDEN));
  receive_card("@old-turn\\n>Previous answer"); // repeated HA attribute cannot resurrect the old answer
  assert(reply_text.empty() && std::strlen(lv_label_get_text(reply_lbl)) == 0);
  // 2026-11-01: a long question ended in "…" after two lines. Too long for two lines at 22 px, it shows whole
  // at 19 px in up to three lines down to the reply (76 px); a short one stays at 22 px in two lines.
  lv_obj_set_width(you_lbl, 760);
  recognized(std::string(200, 'a') + " " + std::string(120, 'b'));
  assert(lv_obj_get_style_text_font(you_lbl, LV_PART_MAIN) == &font19.font && lv_obj_get_style_height(you_lbl, LV_PART_MAIN) == 69);
  listen();
  assert(lv_obj_get_style_text_font(you_lbl, LV_PART_MAIN) == &font22.font);
  recognized("Och imorgon?");
  assert(std::strcmp(lv_label_get_text(you_lbl), "Och imorgon?") == 0);
  assert(lv_obj_get_style_text_font(you_lbl, LV_PART_MAIN) == &font22.font && lv_obj_get_style_height(you_lbl, LV_PART_MAIN) == 54);
  assert(std::strlen(lv_label_get_text(reply_lbl)) == 0);
  assert(!lv_obj_has_flag(orb_think, LV_OBJ_FLAG_HIDDEN));
  receive_card("@new-turn\\n>New answer");
  assert(std::strcmp(lv_label_get_text(reply_lbl), "New answer") == 0);
  listen(); // another automatic follow-up must also clear the newly displayed answer
  receive_card("@new-turn\\n>New answer");
  assert(reply_text.empty() && std::strlen(lv_label_get_text(reply_lbl)) == 0);
  // 2026-10-26: a 236-character reply showed only its first sentences. Now a reply that fits the box at the
  // smallest font shows whole, in the biggest font where it fits.
  auto font = [] { return lv_obj_get_style_text_font(reply_lbl, LV_PART_MAIN); };
  reply_text = "Kort svar.";
  show_reply.execute();
  assert(std::strcmp(lv_label_get_text(reply_lbl), "Kort svar.") == 0 && font() == &font30.font);
  std::string longer;
  while (longer.size() < 800) longer += "Hemma är det lugnt: 22 grader inne, bra luft och ingen rök eller läcka. ";
  longer.pop_back();
  reply_text = longer;
  show_reply.execute();
  assert(std::string(lv_label_get_text(reply_lbl)) == longer && font() != &font30.font);
  reply_text = "Rad ett.\\nRad  två.";
  show_reply.execute();   // one paragraph, and back to 30 px after a long reply's smaller font
  assert(std::strcmp(lv_label_get_text(reply_lbl), "Rad ett. Rad två.") == 0 && font() == &font30.font);
  assert(panel_reply::follow().timer == nullptr);   // a reply that fits does not roll
  std::string huge;
  while (huge.size() < 4000) huge += "Klockan är kl. 17 och det regnar. ";
  huge.pop_back();
  reply_text = huge;
  show_reply.execute();   // too long even at 19 px: all of it, at the scroll font, rolling (Joel, 2026-11-01)
  auto &f = panel_reply::follow();
  assert(std::string(lv_label_get_text(reply_lbl)) == huge && font() == &font22.font);
  assert(f.timer != nullptr && f.box == reply_box && f.line_h == 27 && f.visible == REPLY_BOX_H / 27);
  assert(f.lines > f.visible && f.max_y == lv_obj_get_height(reply_lbl) - REPLY_BOX_H);
  assert(f.speech_ms == huge.size() * 1000 / panel_reply::CHARS_PER_S);
  panel_reply::finish();  // the voice stopped: the end shows
  assert(f.timer == nullptr && f.top == f.lines - f.visible);
  show_reply.execute();
  reply_text = "Kort svar.";
  show_reply.execute();   // a new reply stops the old roll and starts at the top
  assert(f.timer == nullptr && lv_obj_get_scroll_y(reply_box) == 0 && font() == &font30.font);
  // the line being said stays second from the top, from the voice's start to the last screenful
  using panel_reply::follow_top;
  assert(follow_top(0, 10000, 600, 20, 7) == 0 && follow_top(600, 10000, 600, 20, 7) == 0);
  assert(follow_top(600 + 1500, 10000, 600, 20, 7) == 2);     // line 3 of 20 is being said: top line 2
  assert(follow_top(600 + 6000, 10000, 600, 20, 7) == 11);
  assert(follow_top(600 + 9999, 10000, 600, 20, 7) == 13 && follow_top(60000, 10000, 600, 20, 7) == 13);
  lv_display_delete(display); lv_deinit();
}
'''
        compiler = shutil.which('clang++') or shutil.which('g++')
        self.assertIsNotNone(compiler, 'C++ compiler required')
        with tempfile.TemporaryDirectory(prefix='hallpanel-voice-turn-') as tmp:
            path, binary = Path(tmp) / 'test.cpp', Path(tmp) / 'test'
            path.write_text(source)
            result = subprocess.run([compiler, '-std=c++17', '-O1', '-g', '-fsanitize=address,undefined',
                                     '-DLV_CONF_INCLUDE_SIMPLE', '-I'+str(build / 'src'), '-I'+str(headers[0].parent),
                                     '-I'+str(ROOT / 'styles/glass-v2'),
                                     f'-DREPLY_BOX={orb.REPLY_WIDTH},{orb.REPLY_HEIGHT}',
                                     f'-DREPLY_W={orb.REPLY_WIDTH}', f'-DREPLY_BOX_H={orb.REPLY_HEIGHT}',
                                     str(path), str(libraries[0]), '-o', str(binary)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(run.returncode, 0, run.stderr[-2000:])
