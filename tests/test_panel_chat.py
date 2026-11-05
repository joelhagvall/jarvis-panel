"""The dashboard keeps full replies in a contained, vertically scrollable chat."""
import importlib.util
from pathlib import Path
import unittest
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('chat_gen', ROOT/'styles/glass-v2/gen.py')
gen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gen)


def objects(widgets):
    for widget in widgets:
        for obj in widget.values():
            if isinstance(obj, dict):
                yield obj
                yield from objects(obj.get('widgets', []))


class PanelChatTest(unittest.TestCase):
    def test_scroll_is_contained_and_controls_stay_outside(self):
        viewport = next(x for x in objects(gen.page['widgets']) if x.get('id') == 'chat_viewport')
        self.assertTrue(viewport['scrollable'])
        self.assertEqual(viewport['scroll_dir'], 'VER')
        self.assertFalse(viewport['gesture_bubble'])
        self.assertFalse(viewport['scroll_chain_ver'])
        self.assertEqual((viewport["width"], viewport["height"]), (444, 174))
        inside = {x.get('id') for x in objects(viewport['widgets'])}
        self.assertEqual(inside & {'you_row', 'you_txt', 'jv_row', 'jv_txt'}, {'you_row', 'you_txt', 'jv_row', 'jv_txt'})
        self.assertTrue({f'e{i}_txt' for i in range(gen.EARLIER)} <= inside)
        self.assertNotIn('jv_mic', inside)
        self.assertEqual(viewport['widgets'][0]['obj']['min_height'], '100%')

    def test_both_roles_pass_full_text_to_the_same_scroll_update(self):
        for key in ('you', 'jv'):
            self.assertEqual(gen.bubble_text(key), f'panel_chat::update(id(chat_viewport), id({key}_row), id({key}_txt), x);')


    def test_native_pointer_drags_over_bubbles_and_updates_preserve_reader_position(self):
        viewport = next(x for x in objects(gen.page['widgets']) if x.get('id') == 'chat_viewport')
        # Render the actual generated subtree, including input flags/layout.
        lines = []
        def emit(widget, parent):
            kind, body = next(iter(widget.items()))
            name = body.get('id', f'n{len(lines)}')
            factory = 'lv_label_create' if kind == 'label' else 'lv_obj_create'
            lines.append(f'auto *{name}={factory}({parent});')
            for prop in ('width', 'height', 'min_height', 'max_width', 'pad_all', 'pad_left', 'pad_right', 'pad_top', 'pad_bottom', 'border_width', 'x', 'y'):
                if prop not in body: continue
                value = body[prop]
                if value == 'SIZE_CONTENT': value = 'LV_SIZE_CONTENT'
                elif isinstance(value, str) and value.endswith('%'): value = 'lv_pct('+value[:-1]+')'
                lines.append(f'lv_obj_set_style_{prop}({name},{value},0);')
            for prop in ('scrollable', 'clickable', 'gesture_bubble', 'scroll_chain_ver', 'scroll_elastic', 'hidden'):
                if prop in body:
                    fn = 'add' if body[prop] else 'remove'
                    lines.append(f'lv_obj_{fn}_flag({name},LV_OBJ_FLAG_{prop.upper()});')
            if kind == 'label': lines.append(f'lv_label_set_text({name},"");')
            if body.get('scroll_dir'): lines.append(f'lv_obj_set_scroll_dir({name},LV_DIR_VER);')
            layout = body.get('layout')
            if layout:
                lines.append(f'lv_obj_set_flex_flow({name},LV_FLEX_FLOW_{layout["flex_flow"]});')
                lines.append(f'lv_obj_set_flex_align({name},LV_FLEX_ALIGN_{layout["flex_align_main"]},LV_FLEX_ALIGN_{layout["flex_align_cross"]},LV_FLEX_ALIGN_{layout["flex_align_track"]});')
                if 'pad_row' in layout: lines.append(f'lv_obj_set_style_pad_row({name},{layout["pad_row"]},0);')
            for child in body.get('widgets', []): emit(child, name)
        emit({'obj': viewport}, 'lv_screen_active()')
        source = r'''
#include "lvgl.h"
#include "styles/glass-v2/chat_scroll.h"
#include <cassert>
#include <cstdlib>
#include <cstring>
extern "C" {
void lv_mem_init() {} void lv_mem_deinit() {}
void *lv_malloc_core(size_t n){return std::malloc(n);} void lv_free_core(void*p){std::free(p);}
void *lv_realloc_core(void*p,size_t n){return std::realloc(p,n);}
lv_result_t lv_mem_test_core(){return LV_RESULT_OK;} void lv_mem_monitor_core(lv_mem_monitor_t*p){std::memset(p,0,sizeof(*p));}
}
static uint32_t ticks=0;static lv_point_t point;static bool pressed=false;
int main(){
 lv_init();auto *d=lv_display_create(1024,600);lv_tick_set_cb([]()->uint32_t{return ticks;});
 TREE
 std::string reply;for(int i=0;i<20;i++)reply+="Long reply paragraph "+std::to_string(i)+" with enough text to wrap.\n";
 panel_chat::update(chat_viewport,you_row,you_txt,"A full question");
 panel_chat::update(chat_viewport,jv_row,jv_txt,reply);
 assert(lv_obj_get_scroll_bottom(chat_viewport)==0);
 const int end=lv_obj_get_scroll_y(chat_viewport);assert(end>126);
 auto *indev=lv_indev_create();lv_indev_set_type(indev,LV_INDEV_TYPE_POINTER);
 lv_indev_set_mode(indev,LV_INDEV_MODE_EVENT);
 lv_indev_set_read_cb(indev,[](lv_indev_t*,lv_indev_data_t*d){d->point=point;d->state=pressed?LV_INDEV_STATE_PRESSED:LV_INDEV_STATE_RELEASED;});
 lv_area_t box;lv_obj_get_coords(chat_viewport,&box);
 lv_obj_t *pressed_object=nullptr;
 lv_obj_add_event_cb(chat_viewport,[](lv_event_t *e){
   *static_cast<lv_obj_t **>(lv_event_get_user_data(e))=static_cast<lv_obj_t *>(lv_event_get_target(e));
 },LV_EVENT_PRESSED,&pressed_object);
 point={box.x1+60,box.y1+15};pressed=true;lv_indev_read(indev);
 // Descendants are presentation only: the viewport owns every drag.
 assert(pressed_object==chat_viewport);
 for(int i=1;i<=16;i++){point.y=box.y1+15+i*5;ticks+=16;lv_indev_read(indev);}
 pressed=false;lv_indev_read(indev);
 const int reading=lv_obj_get_scroll_y(chat_viewport);assert(reading<end-30);
 panel_chat::update(chat_viewport,jv_row,jv_txt,reply+"A later update");
 assert(lv_obj_get_scroll_y(chat_viewport)==reading);
 panel_chat::update(chat_viewport,jv_row,jv_txt,reply+"A later update");
 assert(lv_obj_get_scroll_y(chat_viewport)==reading);
 lv_obj_t *earlier[6]={e0_txt,e1_txt,e2_txt,e3_txt,e4_txt,e5_txt};
 // Scrolled back: an earlier exchange arrives above without moving the reader.
 panel_chat::earlier(chat_viewport,earlier,6,std::string("uHur m\xc3\xa5r du?")+'\x1f'+"jBra, tack!");
 assert(!lv_obj_has_flag(e0_row,LV_OBJ_FLAG_HIDDEN)&&!lv_obj_has_flag(e1_row,LV_OBJ_FLAG_HIDDEN));
 assert(lv_obj_has_flag(e2_row,LV_OBJ_FLAG_HIDDEN));
 assert(std::strcmp(lv_label_get_text(e0_txt),"Hur m\xc3\xa5r du?")==0&&std::strcmp(lv_label_get_text(e1_txt),"Bra, tack!")==0);
 assert(lv_obj_get_style_flex_main_place(e0_row,LV_PART_MAIN)==LV_FLEX_ALIGN_END);
 assert(lv_obj_get_style_flex_main_place(e1_row,LV_PART_MAIN)==LV_FLEX_ALIGN_START);
 assert(lv_obj_get_scroll_y(chat_viewport)==reading);
 // At the end: the chat follows, and the earliest bubble can be scrolled to.
 lv_obj_scroll_to_y(chat_viewport,lv_obj_get_scroll_y(chat_viewport)+lv_obj_get_scroll_bottom(chat_viewport),LV_ANIM_OFF);
 std::string many;for(int i=0;i<8;i++){if(i)many+='\x1f';many+=(i%2?"j":"u")+std::to_string(i);}
 panel_chat::earlier(chat_viewport,earlier,6,many);
 assert(lv_obj_get_scroll_bottom(chat_viewport)==0);
 assert(std::strcmp(lv_label_get_text(e0_txt),"2")==0&&std::strcmp(lv_label_get_text(e5_txt),"7")==0);
 panel_chat::earlier(chat_viewport,earlier,6,"");
 for(auto *l:earlier)assert(lv_obj_has_flag(lv_obj_get_parent(lv_obj_get_parent(l)),LV_OBJ_FLAG_HIDDEN));
 lv_obj_scroll_to_y(chat_viewport,0,LV_ANIM_OFF);assert(lv_obj_get_scroll_top(chat_viewport)==0);
 assert(std::strcmp(lv_label_get_text(jv_txt),(reply+"A later update").c_str())==0);
 lv_deinit();
}
'''.replace('TREE', '\n'.join(lines))
        build = ROOT/'styles/glass-v2/.esphome/build/style-glass-v2'
        lib = list(build.glob('.pioenvs/*/lib*/liblvgl.a'))
        headers = list(build.glob('.piolibdeps/*/lvgl/lvgl.h'))
        self.assertEqual((len(lib),len(headers)),(1,1),'Native LVGL simulator dependency required')
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp);cpp=tmp/'chat.cpp';binary=tmp/'chat'
            cpp.write_text(source);(tmp/'lv_conf.h').write_text((build/'src/lv_conf.h').read_text())
            result=subprocess.run([shutil.which('clang++') or 'g++','-std=c++17','-fsanitize=address,undefined',
                '-DLV_CONF_INCLUDE_SIMPLE','-I'+str(ROOT),'-I'+str(tmp),'-I'+str(headers[0].parent),
                str(cpp),str(lib[0]),'-o',str(binary)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            result=subprocess.run([str(binary)],capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)



class ChatViewTest(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('chat_view', ROOT/'ha/custom_components/jarvis_agent/chat_view.py')
        self.view = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.view)

    def test_earlier_is_what_came_before_the_newest_question(self):
        u = lambda t: {'role': 'user', 'text': t}
        j = lambda t: {'role': 'jarvis', 'text': t}
        # 2026-10-30: "Hur mår du idag?" vanished when "Det är bara bra, tack!" came, and could not be scrolled to.
        fresh = [u('Hur mår du idag?'), j('God morgon! Jag mår utmärkt.'), u('Det är bara bra, tack!'), j('Fint.')]
        self.assertEqual(self.view.earlier(fresh).split('\x1f'), ['uHur mår du idag?', 'jGod morgon! Jag mår utmärkt.'])
        self.assertEqual(self.view.earlier(fresh[:2]), '')
        self.assertEqual(self.view.earlier([j('Påminnelse.'), j('Nätet är nere.')]), 'jPåminnelse.')
        many = [m for i in range(5) for m in (u(f'q{i}'), j(f'a{i}'))]
        self.assertEqual(self.view.earlier(many).split('\x1f'), ['uq1', 'ja1', 'uq2', 'ja2', 'uq3', 'ja3'])
        self.assertEqual(gen.EARLIER, self.view.EARLIER)
        self.assertNotIn('\x1f\x1f', self.view.earlier([u('a\x1fb'), j('c'), u('d')]))


if __name__ == '__main__':
    unittest.main()
