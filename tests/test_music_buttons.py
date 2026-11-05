"""Only music button transition bindings disappear; the shared theme and pressed styling stay intact."""
from pathlib import Path
import subprocess
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
class MusicButtonsTest(unittest.TestCase):
    def test_detach_transition_only_bindings_preserves_shared_and_local_styles(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);(p/'src/core').mkdir(parents=True)
            (p/'src/core/lv_obj_private.h').write_text('#pragma once\n')
            (p/'lvgl.h').write_text(r'''
#pragma once
#include <cstdint>
constexpr int LV_STYLE_TRANSITION=1,LV_STYLE_RES_FOUND=1;
struct lv_style_value_t{const void *ptr;};
struct lv_style_t{unsigned prop_cnt;bool transition;};
struct Binding{lv_style_t *style;unsigned selector;bool is_local=false,is_trans=false;};
struct lv_obj_t{Binding styles[8];unsigned style_cnt=0;};
inline int lv_style_get_prop(const lv_style_t*s,int,lv_style_value_t*){return s->transition?1:0;}
inline void lv_obj_remove_style(lv_obj_t *o,const lv_style_t*s,unsigned selector){
 for(unsigned i=0;i<o->style_cnt;)if(o->styles[i].style==s&&o->styles[i].selector==selector){
  for(unsigned j=i+1;j<o->style_cnt;++j)o->styles[j-1]=o->styles[j];--o->style_cnt;
 }else ++i;
}
''')
            (p/'test.cpp').write_text(r'''
#include "styles/glass-v2/music_buttons.h"
#include <cassert>
int main(){
 lv_style_t transition{1,true},pressed{2,false},local{1,true},mixed{2,true},constant{255,true};
 lv_obj_t button{{{&transition,0},{&pressed,1},{&transition,1},{&local,0,true},
                  {&local,1,false,true},{&mixed,0},{&constant,0}},7};
 lv_obj_t other{{{&transition,0}},1};
 assert(panel_music_ui::instant_button(&button)==2&&button.style_cnt==5);
 assert(button.styles[0].style==&pressed&&button.styles[0].selector==1);
 assert(button.styles[1].style==&local&&button.styles[1].is_local);
 assert(button.styles[2].style==&local&&button.styles[2].is_trans);
 assert(button.styles[3].style==&mixed&&button.styles[4].style==&constant);
 assert(transition.prop_cnt==1&&transition.transition&&other.style_cnt==1);
 assert(panel_music_ui::instant_button(&button)==0&&button.style_cnt==5);
 lv_obj_t empty;assert(panel_music_ui::instant_button(&empty)==0);
}
''')
            subprocess.run(['c++','-std=c++17','-O1','-fsanitize=address,undefined','-I',str(p),'-I',str(ROOT),
                            str(p/'test.cpp'),'-o',str(p/'test')],check=True)
            subprocess.run([str(p/'test')],check=True)
