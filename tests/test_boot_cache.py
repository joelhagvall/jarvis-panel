"""Real bounded record/storage logic under ASan/UBSan and generated UI contracts."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

class BootCacheTest(unittest.TestCase):
    def test_corruption_bounds_clock_age_storage_failure_and_cross_reboot_write_budget(self):
        code = r"""
#include "bench/boot_cache.h"
#include <cassert>
#include <memory>
struct State { boot_cache::Record record{}; bool exists=false,fail=false; unsigned writes=0; };
struct Store {
 std::shared_ptr<State> state;
 bool load(boot_cache::Record *p) { if(!state->exists)return false;*p=state->record;return true; }
 bool save(const boot_cache::Record *p) { if(state->fail)return false;state->record=*p;state->exists=true;++state->writes;return true; }
};
int main() {
 using namespace boot_cache;
 constexpr uint32_t t=1792990000;
 Fields fields{"5°","Moln","Paraply","rain","IDAG","Möte|09:30|Jobb|#FF0000","",""};
 Record a;assert(!a.validate());assert(a.encode(t,"2026-10-26 05:00",fields));assert(a.validate());
 assert(a.usable(0));assert(a.usable(t));assert(a.usable(t+MAX_AGE_S));assert(!a.usable(t+MAX_AGE_S+1));assert(!a.usable(t-1));
 assert(a.field(0)=="5°");assert(a.field(6).empty());assert(a.field(8).empty());assert(a.stamp()=="2026-10-26 05:00");
 // Each byte is covered, including otherwise unused tail, lengths and timestamp.
 for(size_t i=0;i<RECORD_BYTES;++i){Record bad=a;bad.bytes[i]^=1;assert(!bad.validate());}
 Record bad=a;write32(bad.bytes.data()+4,SCHEMA+1);write32(bad.bytes.data(),checksum(bad.bytes));assert(!bad.validate());
 bad=a;bad.bytes[28]=255;bad.bytes[29]=255;write32(bad.bytes.data(),checksum(bad.bytes));assert(!bad.validate());
 bad=a;bad.bytes[12]='x';write32(bad.bytes.data(),checksum(bad.bytes));assert(!bad.validate());
 Fields huge=fields;std::string long_text(513,'x');huge[2]=long_text;assert(!bad.encode(t,"2026-10-26 05:00",huge));
 std::string nul("a\0b",3);huge=fields;huge[0]=nul;assert(!bad.encode(t,"2026-10-26 05:00",huge));
 assert(!bad.encode(t,"invalid",fields));assert(!bad.encode(0,"2026-10-26 05:00",fields));
 // Maximum legal payload fits. Invalid field lengths never truncate a valid cache.
 std::array<std::string,FIELD_COUNT> maximum;
 for(size_t i=0;i<FIELD_COUNT;++i){maximum[i].assign(LIMITS[i],'x');huge[i]=maximum[i];}
 assert(bad.encode(t,"2026-10-26 05:00",huge));
 auto state=std::make_shared<State>();Store store{state};Cache<Store> c;c.load(store);assert(!c.loaded);
 assert(!c.save(t,64999,"2026-10-26 05:00",fields));assert(!state->writes);
 assert(c.save(t,65000,"2026-10-26 05:00",fields));assert(state->writes==1);assert(c.saves==1);
 Fields changed=fields;changed[0]="6°";
 assert(!c.save(t+3599,70000,"2026-10-26 05:59",changed));
 Cache<Store> reboot;reboot.load(store);assert(reboot.loaded);assert(reboot.field(0)=="5°");
 assert(!reboot.save(t+3599,70000,"2026-10-26 05:59",changed)); // Rate persists across restart.
 state->fail=true;assert(!reboot.save(t+3600,70000,"2026-10-26 06:00",changed));assert(reboot.field(0)=="5°");assert(reboot.failures==1);
 state->fail=false;assert(reboot.save(t+3600,70000,"2026-10-26 06:00",changed));assert(state->writes==2);
 assert(!reboot.save(t+7200,70000,"2026-10-26 07:00",changed)); // Unchanged content needs no hourly writes.
 assert(!reboot.save(t-100,70000,"2026-10-26 04:00",changed));
 assert(reboot.save(t+7*3600,70000,"2026-10-26 12:00",changed)); // Refresh age at most six hours for unchanged values.
 assert(state->writes==3);
 state->record.bytes[100]^=1;Cache<Store> corrupt;corrupt.load(store);assert(!corrupt.loaded);assert(!corrupt.usable(t));
}
"""
        with tempfile.TemporaryDirectory() as d:
            cpp, binary = Path(d)/'cache.cpp', Path(d)/'cache'
            cpp.write_text(code)
            compiler = shutil.which('clang++') or shutil.which('g++')
            self.assertIsNotNone(compiler)
            p = subprocess.run([compiler,'-std=c++17','-Wall','-Wextra','-Werror','-O1','-g',
                '-fsanitize=address,undefined','-I'+str(ROOT),str(cpp),'-o',str(binary)],capture_output=True,text=True)
            self.assertEqual(p.returncode,0,p.stderr)
            p = subprocess.run([str(binary)],capture_output=True,text=True,timeout=10)
            self.assertEqual(p.returncode,0,p.stderr)

    def test_generated_cache_never_publishes_states_or_restores_relative_and_safety_values(self):
        p = subprocess.run([sys.executable,str(ROOT/'board.py'),'--voice','jarvis_orb.py','--remote',
            'http://example.invalid/assets'],cwd=ROOT/'styles/glass-v2',check=True,capture_output=True,text=True)
        cfg=json.loads(p.stdout)
        self.assertTrue({'boot_cache.h','boot_cache_esphome.h'} <= {Path(p).name for p in cfg['esphome']['includes']})
        scripts={s['id']:s for s in cfg['script']};source=json.dumps(cfg)
        init=next(b for b in cfg['esphome']['on_boot'] if b['priority']==380)
        initial=json.dumps(init)
        self.assertNotIn('publish_state',initial)
        self.assertNotIn('hm_smoke',initial);self.assertNotIn('hm_water',initial);self.assertNotIn('hm_door',initial)
        self.assertNotIn('chat_said',initial);self.assertNotIn('chat_reply',initial)
        self.assertIn('SPARAD KALENDER',initial);self.assertIn('Uppdaterar avg',initial)
        self.assertNotIn('boot_cache_note',source)
        self.assertNotIn('Sparade uppgifter',source)
        self.assertIn('--:--',initial)
        self.assertIn('esp_sntp_restart()',json.dumps(cfg['wifi']['on_connect']))
        self.assertEqual(cfg['esp32']['framework']['sdkconfig_options']['CONFIG_LWIP_SNTP_STARTUP_DELAY'],'n')
        self.assertEqual(next(t for t in cfg['time'] if t['id']=='boot_time')['servers'],['router.example.invalid'])
        for sensor in cfg['text_sensor']:
            if sensor.get('id','').startswith('hm_') and 'boot_cache_refresh' in json.dumps(sensor):
                actions=sensor['on_value'];self.assertEqual(actions[1]['if']['condition']['lambda'],'return !id(boot_cache_active);')
        refresh=scripts['boot_cache_refresh']['then'][0]['if'];fresh=json.dumps(refresh['then'])
        self.assertIn('boot_live_ready',fresh);self.assertIn('boot_live_ms',fresh)
        sys.path.insert(0,str(ROOT/'styles/glass-v2'));import boot_cache
        self.assertEqual(len(refresh['then'][1]['if']['then']),1+len(boot_cache.COHORT)) # off + one UI write per field; no yield.
        self.assertNotIn('delay',fresh);self.assertNotIn('publish_state',fresh)
        self.assertIn('id(boot_cache_available) = false',json.dumps(refresh['else']))
        save=next(i for i in cfg['interval'] if i.get('startup_delay')=='70s')
        self.assertIn('!id(ha_lost)',json.dumps(save))
        self.assertIn('id(boot_ha_connected)',json.dumps(save))
        self.assertIn(f'id(boot_seen) == {(1<<len(boot_cache.COHORT))-1}',json.dumps(save))
        self.assertIn('id(boot_seen) = 0',json.dumps(cfg['voice_assistant']['on_client_disconnected']))
        self.assertNotIn('boot_seen) = 0',json.dumps(cfg['voice_assistant']['on_client_connected']))
        self.assertNotIn('chat_',json.dumps(save));self.assertNotIn('hm_sl',json.dumps(save))


    def test_real_fresh_cohort_condition_rejects_retained_states_after_disconnect(self):
        import re
        generated=subprocess.run([sys.executable,str(ROOT/'board.py'),'--voice','jarvis_orb.py','--remote',
            'http://example.invalid/assets'],cwd=ROOT/'styles/glass-v2',check=True,capture_output=True,text=True)
        cfg=json.loads(generated.stdout)
        condition=next(s for s in cfg['script'] if s['id']=='boot_cache_refresh')['then'][0]['if']['condition']['lambda']
        names=re.findall(r'id\((\w+)\)\.has_state\(\)',condition)
        sys.path.insert(0,str(ROOT/'styles/glass-v2'));import boot_cache
        n=len(boot_cache.COHORT);self.assertEqual(len(set(names)),n)
        code=("#include <cassert>\n#include <cstdint>\n"
              "struct Sensor {bool ready=true;bool has_state()const{return ready;}} "+','.join(names)+";\n"
              "struct Clock {bool valid=true;Clock now()const{return *this;}bool is_valid()const{return valid;}} ha_time;\n"
              "uint32_t boot_seen=0;\n#define id(x) x\n"
              "bool fresh(){"+condition+"}\nint main(){Sensor* fields[]={"+','.join('&'+n for n in names)+"};\n"
              f"for(unsigned r=0;r<{n};++r){{boot_seen=0;for(unsigned i=0;i<{n};++i){{assert(!fresh());boot_seen|=1u<<((i+r)%{n});}}assert(fresh());}}\n"
              "ha_time.valid=false;assert(!fresh());ha_time.valid=true;\n"
              "for(auto*f:fields){f->ready=false;assert(!fresh());f->ready=true;}assert(fresh());\n"
              "boot_seen=0;assert(!fresh());}\n")
        with tempfile.TemporaryDirectory() as d:
            cpp,binary=Path(d)/'cohort.cpp',Path(d)/'cohort';cpp.write_text(code)
            compiler=shutil.which('clang++') or shutil.which('g++')
            result=subprocess.run([compiler,'-std=c++17','-O1','-fsanitize=address,undefined',str(cpp),'-o',str(binary)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            result=subprocess.run([str(binary)],capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)

if __name__=='__main__':unittest.main()
