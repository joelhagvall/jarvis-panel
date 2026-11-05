"""Give real/remote pointer feedback priority over bounded noncritical detail updates."""
from .common import ROOT

FIELDS=('hm_wx_hours','hm_wx_week','hm_wx_facts','hm_cal_week','ai_panel','hm_wx_sky','hm_wx_sub','hm_wx_wet')

def apply(cfg):
    cfg['esphome'].setdefault('includes',[]).extend(str(ROOT/'styles/glass-v2'/name)
        for name in ('ui_priority.h','ui_priority_lvgl.h'))
    setup=['ui_priority::install();']
    scripts={s['id']:s for s in cfg['script']}
    sensors={s['id']:s for s in cfg['text_sensor'] if 'id' in s}
    for i,key in enumerate(FIELDS):
        name='detail_render_'+key
        if name in scripts:
            target=scripts[name]
        else:
            sensor=sensors[key]
            target={'id':name,'parameters':{'x':'string'},'then':sensor['on_value']}
            cfg['script'].append(target)
            sensor['on_value']=[{'lambda':f'id({name}).execute(x);'}]
        immediate='ui_apply_'+key
        cfg['script'].append({'id':immediate,'parameters':{'x':'string'},'then':target['then']})
        target['then']=[{'lambda':f'ui_priority::queue.submit({i},x,millis());'}]
        setup.append(f'ui_priority::queue.slots[{i}].render=[](const std::string &x){{id({immediate}).execute(x);}};')
    cfg['esphome'].setdefault('on_boot',[]).append({'priority':-94,'then':[{'lambda':'\n'.join(setup)}]})
    cfg.setdefault('interval',[]).append({'interval':'5ms','then':[{'lambda':
        'ui_priority::queue.tick(millis(),ui_priority::pointer_pressed());'}]})
    cfg['interval'].append({'interval':'60s','then':[{'lambda':'''static uint32_t previous=0;
const auto &s=ui_priority::queue.stats;
if(s.enqueued!=previous){previous=s.enqueued;
  ESP_LOGI("ui_work","applied %u, replaced %u, pending %u, deferred ticks %u, max wait %u ms",
    unsigned(s.applied),unsigned(s.replaced),unsigned(s.pending),unsigned(s.deferred),unsigned(s.max_wait_ms));
}'''}]})
