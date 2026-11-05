"""Silent detail-page loads using the production renderers and immutable synthetic payloads."""
import json
from datetime import datetime,timedelta
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
FIELDS={
    'weather':('WEATHER','page_vader',('hm_wx_hours','hm_wx_week','hm_wx_facts')),
    'calendar':('CALENDAR','page_kalender',('hm_cal_week',)),
    'ai':('AI','page_ai',('ai_panel',)),
}

def payloads():
    from server.agents.calendar_week import layout,TZ
    out={}
    for version in range(2):
        hours=';'.join(f'{i:02d}|{"rain" if (i+version)%3 else "sun"}|{3+(i+version)%11}|{(i+version)%4/2}' for i in range(24))
        week=';'.join(f'{d}|{"rain" if version else "cloud"}|{9+version}°|3°|2 mm|byar 8' for d in ('idag','tis','ons','tors','fre','lör','sön'))
        now=datetime(2026,11,2,8,tzinfo=TZ)
        events=[(now.replace(hour=9)+timedelta(days=d,hours=k),now.replace(hour=10)+timedelta(days=d,hours=k),
                 'Jobb',f'Test av kalenderns långa händelsetitel {version}-{d}-{k}') for d in range(7) for k in range(6)]
        calendar=layout(events,now,{'Jobb':'#007AFF'})['cal_week']
        ai=';'.join(['u|Testdata – inga API-anrop',*[f's|{n}|{9+version},8 M|output 48 k' for n in ('Idag','November','Oktober')],
            *[f'a|{n}|Testkonto|{30+version*60}|nollställs 11:29|{70+version*25}|nollställs tors 05:59' for n in ('Claude 1','Claude 2','Codex')]])
        out[version]={'hm_wx_hours':hours,'hm_wx_week':week,'hm_wx_facts':f'{4+version} m/s|89 %|50 %|1021 hPa',
                      'hm_cal_week':calendar,'ai_panel':ai}
    return out

def apply(cfg):
    if any(s['id'].startswith('detail_render_') for s in cfg['script']):
        raise ValueError('Detail diagnostics already installed')
    samples=payloads()
    cfg['esphome'].setdefault('includes',[]).append(str(ROOT/'styles/glass-v2/detail_load.h'))
    sensors={s['id']:s for s in cfg['text_sensor'] if 'id' in s}
    for _,(mode,_,fields) in FIELDS.items():
        for key in fields:
            sensor=sensors[key]
            actions=sensor['on_value']
            cfg['script'].append({'id':'detail_render_'+key,'parameters':{'x':'string'},'then':actions})
            sensor['on_value']=[{'lambda':f'''
if(panel_detail_load::fixture.active && panel_detail_load::fixture.mode==panel_detail_load::Mode::{mode})return;
id(detail_render_{key}).execute(x);
'''}]
    cfg.setdefault('interval',[]).append({'interval':'100ms','then':[{'lambda':'panel_detail_load::fixture.tick(millis());'}]})
    return samples

def callback(samples):
    branches=[]
    for name,(mode,page,fields) in FIELDS.items():
        saved=' '.join(f'static std::string saved_{key};saved_{key}=id({key}).state;' for key in fields)
        update='\n'.join(f'id(detail_render_{key}).execute(step%2 ? {json.dumps(samples[1][key],ensure_ascii=False)} : {json.dumps(samples[0][key],ensure_ascii=False)});' for key in fields)
        restore='\n'.join(f'id(detail_render_{key}).execute(id({key}).state);' for key in fields)
        owned=' && '.join(f'saved_{key}==id({key}).state' for key in fields)
        branches.append(f'''if(mode=="detail_{name}") {{
  {saved}
  panel_detail_load::fixture.begin(panel_detail_load::Mode::{mode},millis(),
    [](unsigned step){{ {update} }}, [](){{ {restore} }}, [](){{return {owned};}});
  panel_music_load::fixture.begin({{id(mp_caption),id(mp_title),id(mp_artist),id(mp_album)}},
    id(mp_group),id(mp_empty),millis(),[](const lv_image_dsc_t *image,uint64_t identity){{
      panel_vinyl::page.source(image,panel_vinyl::Kind::PICTURE,identity);
    }});
  expected=id({page})->obj;
}}''')
    return '#ifdef HALLPANEL_DETAIL_LOAD\n  else if(mode=="detail_weather" || mode=="detail_calendar" || mode=="detail_ai") {\n'+ '\n'.join(branches)+'\n  }\n#endif\n'
