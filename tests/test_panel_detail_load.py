"""Detail load must render fresh data, retain background work and restore current real values."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from panel_detail_load import target,verdict,layout_failures
from panel_detail_codegen import FIELDS,payloads,apply,callback
ROOT=Path(__file__).resolve().parents[1]

class DetailLoadTest(unittest.TestCase):
    def test_payloads_are_complete_and_renderers_have_one_source(self):
        samples=payloads()
        self.assertEqual(len(samples[0]['hm_wx_hours'].split(';')),24)
        self.assertEqual(len(samples[0]['hm_wx_week'].split(';')),7)
        self.assertEqual(samples[0]['hm_cal_week'].count('h|'),7)
        self.assertEqual(samples[0]['ai_panel'].count('a|'),3)
        keys=[key for _,_,fields in FIELDS.values() for key in fields]
        cfg={'esphome':{},'script':[],'text_sensor':[{'id':k,'on_value':[{'lambda':'render_original(x);'}]} for k in keys]}
        apply(cfg)
        self.assertEqual(len(cfg['script']),len(keys))
        for s in cfg['script']:self.assertEqual(s['then'],[{'lambda':'render_original(x);'}])
        self.assertIn('id(hm_cal_week).state',callback(samples)) # restore latest state, not saved fixture content
        with self.assertRaises(ValueError):apply(cfg)

    def test_low_frame_count_missing_load_clipping_and_slow_input_fail(self):
        good={'n':20,'p95':20,'max':30}
        case={'summary':{'update.refresh_work_ms':dict(good), 'navigation.remote_press_ms':dict(good),
                         'navigation.remote_release_ms':dict(good)},'update_steps':10,'background_covers':2}
        self.assertTrue(verdict(case)['ok'])
        for key,value in [('background_covers',0),('update_steps',0),('layout_failures',['clipped'])]:
            bad=dict(case,**{key:value});self.assertFalse(verdict(bad)['ok'])
        case['summary']['navigation.remote_release_ms']['max']=251
        self.assertFalse(verdict(case)['ok'])

    def test_semantic_entries_reject_ambiguity_and_geometry_catches_clipped_text(self):
        ui={'widgets':[{'type':'label','text':'Kungsholmen','x':536,'y':40,'width':100,'height':20}]}
        self.assertEqual(target(ui,'weather',True),(586,50))
        ui['widgets']*=2
        with self.assertRaises(RuntimeError):target(ui,'weather',True)
        ui={'widgets':[{'path':'screen','type':'object','x':0,'y':0,'width':1024,'height':600},
            {'path':'screen/0','type':'label','text':'last event','x':30,'y':590,'width':200,'height':20}]}
        self.assertTrue(layout_failures(ui))

    def test_native_expiry_restore_reads_latest_state_and_real_update_preempts(self):
        source=r'''
#include "styles/glass-v2/detail_load.h"
#include <cassert>
using namespace panel_detail_load;
unsigned rendered=0,real=1,shown=1;bool owned=true;
void update(unsigned n){rendered=n;shown=999;}
void restore(){shown=real;}
bool unchanged(){return owned;}
int main(){Fixture f;f.begin(Mode::WEATHER,100,update,restore,unchanged);
 assert(f.active&&shown==999);f.tick(699);assert(rendered==0);f.tick(700);assert(rendered==1);
 real=2;owned=false;f.tick(800);assert(!f.active&&shown==2);f.restore();assert(shown==2);
 owned=true;f.begin(Mode::AI,0xfffffff0u,update,restore,unchanged);f.tick(700);assert(rendered==1);
 f.restore();assert(shown==2&&!f.active);
}
'''
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder);(p/'t.cpp').write_text(source)
            subprocess.run(['c++','-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',
                            '-I',str(ROOT),str(p/'t.cpp'),'-o',str(p/'t')],check=True)
            subprocess.run([str(p/'t')],check=True)
