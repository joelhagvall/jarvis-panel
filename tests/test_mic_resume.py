"""Use ESPHome's actual optimistic switch write ordering to exercise the generated mic hooks."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import esphome.components.template.switch as template_switch

ROOT=Path(__file__).resolve().parents[1]

def condition(c):
    if 'and' in c:return '('+'&&'.join(condition(x) for x in c['and'])+')'
    if 'not' in c:return '!'+condition(c['not'])
    if 'switch.is_on' in c:return 'mic.state'
    if 'voice_assistant.is_running' in c:return 'voice_running'
    if 'api.connected' in c:return 'connected'
    raise AssertionError(c)

def actions(items):
    out=[]
    for a in items:
        if 'if' in a:
            v=a['if'];out.append('if('+condition(v['condition'])+'){'+actions(v['then'])+'}')
        elif 'micro_wake_word.start' in a:out.append('++starts;wake_running=true;')
        elif 'micro_wake_word.stop' in a:out.append('wake_running=false;')
        elif 'voice_assistant.stop' in a:out.append('voice_running=false;')
        elif 'logger.log' not in a:raise AssertionError(a)
    return ''.join(out)

class MicResumeTest(unittest.TestCase):
    def test_old_value_guard_is_skipped_by_actions_but_new_hooks_restart_after_publish(self):
        cfg=json.loads(subprocess.run([sys.executable,str(ROOT/'board.py'),'--voice','jarvis_orb.py',
            '--remote','http://example.invalid'],cwd=ROOT/'styles/glass-v2',check=True,capture_output=True,text=True).stdout)
        mic=next(s for s in cfg['switch'] if s['id']=='mic_on')
        dep=Path(template_switch.__file__).with_name('template_switch.cpp').read_text()
        method=dep[dep.index('void TemplateSwitch::write_state(bool state)'):dep.index('void TemplateSwitch::set_optimistic')]
        source=r'''
#include <functional>
#include <cassert>
bool connected=true,voice_running=false,wake_running=false;unsigned starts=0;
struct Trigger {std::function<void()> action=[]{};void trigger(){action();}void stop_action(){}};
struct TemplateSwitch {
 bool state=false,optimistic_=true;Trigger turn_on_trigger_,turn_off_trigger_,*prev_trigger_=nullptr;
 std::function<void(bool)> changed=[](bool){};
 void write_state(bool state);
 void publish_state(bool value){if(state==value)return;state=value;changed(value);}
};
METHOD
void wire(TemplateSwitch &mic,bool old){
 mic.turn_on_trigger_.action=[&mic,old](){if(old){ON}};
 mic.turn_off_trigger_.action=[old](){if(old){OFF}};
 mic.changed=[&mic,old](bool on){if(!old){if(on){ON}else{OFF}}};
}
int main(){
 TemplateSwitch mic;wire(mic,false);
 for(unsigned n=0;n<4;++n){mic.write_state(true);assert(mic.state&&wake_running&&starts==n+1);
   mic.write_state(false);assert(!mic.state&&!wake_running&&!voice_running);}
 connected=false;mic.write_state(true);assert(!wake_running);mic.write_state(false);
 connected=true;voice_running=true;mic.write_state(true);assert(!wake_running);mic.write_state(false);
 // Demonstrate the actual reported failure with exactly the same actions on the old hook.
 starts=0;TemplateSwitch legacy;wire(legacy,true);legacy.write_state(true);
 assert(legacy.state&&!wake_running&&starts==0);
}
'''.replace('METHOD',method).replace('ON',actions(mic['on_turn_on'])).replace('OFF',actions(mic['on_turn_off']))
        # Mutating back to turn_on_action must fail before compilation, not silently change test wiring.
        self.assertNotIn('turn_on_action',mic)
        self.assertNotIn('turn_off_action',mic)
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);(p/'t.cpp').write_text(source)
            subprocess.run(['c++','-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',
                str(p/'t.cpp'),'-o',str(p/'test')],check=True)
            subprocess.run([str(p/'test')],check=True)
