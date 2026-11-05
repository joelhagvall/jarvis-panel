"""Touch priority must retain fresh data, preserve atomic groups and recover from a tap with no draw."""
from pathlib import Path
import json
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]

class InputPriorityTest(unittest.TestCase):
    def test_generated_real_and_fixture_updates_share_one_queued_renderer(self):
        cfg=json.loads(subprocess.run([sys.executable,str(ROOT/'board.py'),'--voice','jarvis_orb.py',
            '--remote','http://example.invalid/assets'],cwd=ROOT/'styles/glass-v2',capture_output=True,text=True,check=True).stdout)
        scripts={s['id']:s for s in cfg['script']}
        names={Path(p).name for p in cfg['esphome']['includes']}
        self.assertTrue({'ui_priority.h','ui_priority_lvgl.h'}<=names,'ESPHome only ships explicitly included local headers')
        from board_config.ui_updates import FIELDS
        self.assertEqual(len(FIELDS),len(set(FIELDS)))
        for i,key in enumerate(FIELDS):
            self.assertEqual(scripts['detail_render_'+key]['then'],[{'lambda':f'ui_priority::queue.submit({i},x,millis());'}])
            self.assertTrue(scripts['ui_apply_'+key]['then'])
        body=json.dumps(cfg['text_sensor'])
        self.assertNotIn('ui_apply_',body) # HA cannot bypass priority while the fixture uses it.

    def test_native_held_release_empty_frame_wrap_and_latest_value(self):
        source=r'''
#include "styles/glass-v2/ui_priority.h"
#include <cassert>
ui_priority::Queue<2> q;
std::string first,second;
void a(const std::string &x){first=x;}
void b(const std::string &x){second=x;}
int main(){
 q.slots[0].render=a;q.slots[1].render=b;
 q.submit(0,"old",100);q.submit(1,"related",100);assert(!q.tick(107,false));
 q.input.mark(108);assert(!q.tick(200,true));q.submit(0,"latest",201);
 assert(q.stats.pending==2&&q.stats.replaced==1&&first.empty());
 q.input.mark(210);assert(!q.tick(225,false)); // Release feedback has not actually drawn yet.
 q.input.frame();assert(!q.tick(219,false));assert(q.tick(226,false));
 assert(first=="latest"&&second=="related"&&q.stats.pending==0&&q.stats.applied==2);
 q.input.mark(300);q.submit(0,"blank tap",301);assert(!q.tick(419,false));
 assert(q.tick(420,false)&&first=="blank tap"); // No redraw/empty refresh cannot hold data forever.
 q.input.mark(0xfffffff0u);q.submit(0,"wrapped",0xfffffff1u);
 assert(!q.tick(5,false));q.input.frame();assert(q.tick(6,false)&&first=="wrapped");
 q.slots[0].render=[](const std::string &x){first=x;q.submit(0,"new during apply",1000);assert(!q.tick(1009,false));};
 q.submit(0,"reentrant",990);assert(q.tick(1000,false));assert(q.stats.pending==1);
 q.slots[0].render=a;assert(q.tick(1010,false)&&first=="new during apply");
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);(p/'t.cpp').write_text(source)
            subprocess.run(['c++','-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',
                '-I',str(ROOT),str(p/'t.cpp'),'-o',str(p/'test')],check=True)
            subprocess.run([str(p/'test')],check=True)
