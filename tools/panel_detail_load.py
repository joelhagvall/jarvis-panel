#!/usr/bin/env python3
"""Silent weather/calendar/AI updates and real navigation with background cover preparation."""
import argparse
import json
from pathlib import Path
import time
from panel_remote import PanelClient
from panel_metrics import private_write
from panel_responsiveness import telemetry,delta,stats,guard,POLICY
from panel_ui_test import select_tab
from panel_gates import leave_night,restore_night,leave_music

PAGES={'weather':'page_vader','calendar':'page_kalender','ai':'page_ai'}
PROTOCOL='detail-updates6s-navigation20-v1'

def target(ui,name,enter):
    widgets=ui['widgets']
    if ui.get('truncated'):raise RuntimeError('Incomplete UI tree')
    if not enter:
        items=[w for w in widgets if w['type']=='object' and w.get('clickable') and
               (w['x'],w['y'],w['width'],w['height'])==(0,0,1024,600)]
        if not items:raise RuntimeError('No full-screen detail-page return control')
        w=max(items,key=lambda w:w['path'].count('/'))
        return w['x']+w['width']//2,w['y']+w['height']//2
    if name=='calendar':
        items=[w for w in widgets if w['type']=='object' and w.get('clickable') and
               (w['x'],w['y'],w['width'],w['height'])==(766,24,230,230)]
    else:items=[w for w in widgets if w.get('text')==('Kungsholmen' if name=='weather' else 'Jarvis')]
    if len(items)!=1:raise RuntimeError('Detail-page entry ambiguous: '+name)
    w=items[0]
    return w['x']+w['width']//2,w['y']+w['height']//2

def layout_failures(ui):
    paths={w['path']:w for w in ui['widgets']};failures=[]
    for w in paths.values():
        if w['type']!='label' or not w.get('text'):continue
        parent=w['path']
        while '/' in parent:
            parent=parent.rsplit('/',1)[0];p=paths.get(parent)
            if p and (w['x']<p['x'] or w['y']<p['y'] or w['x']+w['width']>p['x']+p['width'] or
                      w['y']+w['height']>p['y']+p['height']):
                failures.append(w['path']+' clipped by '+parent);break
    return failures

def verdict(case):
    errors=list(case.get('layout_failures',[]));summary=case['summary']
    update=summary['update.refresh_work_ms']
    if update['n']<8:errors.append('too few fresh update frames')
    if update['p95']>40 or update['max']>100:errors.append('static update work exceeds 40 ms p95 / 100 ms maximum')
    limits=json.loads(POLICY.read_text())['limits']
    for key in ('navigation.remote_press_ms','navigation.remote_release_ms'):
        d=summary[key]
        if d['n']!=20:errors.append(key+': not 20 fresh samples')
        for statistic,limit in limits[key].items():
            if d[statistic]>limit:errors.append(key+': '+statistic+' exceeds existing input limit')
    if case['update_steps']<8 or case['background_covers']<1:errors.append('missing active update/background load')
    return {'ok':not errors,'failures':errors}

def run_case(client,name,initial,folder):
    mode='detail_'+name;page=PAGES[name]
    case={'name':name,'rows':[],'navigation':[],'complete':False}
    def save():private_write(folder/(name+'.json'),case)
    def read():
        start=time.monotonic();s=client.status();elapsed=(time.monotonic()-start)*1000
        guard(s,initial,mode)
        if not s.get('detail_load_active') or not s.get('music_load_active'):
            raise RuntimeError('Detail/background load stopped; real data preempted test')
        return {'status':s,'timing':telemetry(s),'host_ms':elapsed}
    try:
        select_tab(client,'page_glass_v2');client.test_mode(mode,30000);time.sleep(.8)
        ui=client.ui()
        if ui.get('page')!=page:raise RuntimeError('Wrong detail page')
        private_write(folder/(name+'-ui.json'),ui)
        case['layout_failures']=layout_failures(ui)
        case['capture']=client.screenshot(folder/(name+'.png'),lossless=True)
        client.test_mode(mode,30000)  # renew the same fixture, never reset its data/worker
        first=read();case['rows'].append(first);end=time.monotonic()+6
        while time.monotonic()<end:
            time.sleep(.15);case['rows'].append(read());save()
        work=[v/1000 for a,b in zip(case['rows'],case['rows'][1:]) for v in delta(a,b,'refresh_work')]
        case['update_steps']=case['rows'][-1]['status']['detail_load_steps']-first['status']['detail_load_steps']
        presses=[];releases=[];hosts=[]
        for index in range(20):
            enter=index%2==1
            ui=client.ui();source='page_glass_v2' if enter else page
            if ui.get('page')!=source:raise RuntimeError('Navigation source changed')
            xy=target(ui,name,enter);before=read();start=time.monotonic()
            client.tap(*xy,duration=60,settle=0);hosts.append((time.monotonic()-start)*1000)
            until=time.monotonic()+2;expected=page if enter else 'page_glass_v2'
            while True:
                after=read();released=delta(before,after,'remote_release')
                if after['status']['page']==expected and len(released)==1:break
                if len(released)>1 or time.monotonic()>=until:raise RuntimeError('Detail-page redraw not acknowledged exactly once')
                time.sleep(.02)
            pressed=delta(before,after,'remote_press')
            if len(pressed)!=1:raise RuntimeError('Detail-page press sample missing/ambiguous')
            presses.extend(v/1000 for v in pressed);releases.extend(v/1000 for v in released)
            case['navigation'].append({'enter':enter,'before':before,'after':after,'host_ms':hosts[-1]});save()
            time.sleep(.03)
        case['background_covers']=after['status']['music_load_covers']-first['status']['music_load_covers']
        case['summary']={'update.refresh_work_ms':stats(work),'navigation.remote_press_ms':stats(presses),
            'navigation.remote_release_ms':stats(releases),'command_host_ms':stats(hosts),
            'status_host_ms':stats([r['host_ms'] for r in case['rows']]),
            'observer_main_ms':stats([r['status']['status_main_us']/1000 for r in case['rows']])}
        if 'ui_update_deferred' in after['status']:
            case['input_priority']={k:after['status'][k]-first['status'][k] for k in
                ('ui_update_applied','ui_update_deferred','ui_update_replaced')}
        case['verdict']=verdict(case);case['complete']=True
    except Exception as error:
        case['error']={'type':type(error).__name__,'message':str(error)};raise
    finally:
        try:
            client.test_mode('restore',100)
            until=time.monotonic()+2
            while client.status().get('ui_update_pending',0):
                if time.monotonic()>until:raise RuntimeError('Latest actual data did not drain after fixture restore')
                time.sleep(.02)
        except Exception as error:
            case['restore_error']=str(error)
            if 'error' not in case:raise
        finally:save()
    return case

def collect(client,folder):
    folder.mkdir(mode=0o700,parents=True,exist_ok=False)
    original=client.status();night=False;report={'protocol':PROTOCOL,'cases':[],'complete':False}
    private_write(folder/'before.json',original)
    try:
        night=leave_night(client,10);leave_music(client);select_tab(client,'page_glass_v2')
        initial=client.status()
        for name in PAGES:
            case=run_case(client,name,initial,folder);report['cases'].append(case)
            print(name+': '+('PASS' if case['verdict']['ok'] else 'FAIL'),flush=True)
        report['complete']=True;report['ok']=all(c['verdict']['ok'] for c in report['cases'])
    except Exception as error:report['error']=str(error);raise
    finally:
        try:
            s=client.status()
            if (s['boot_id']==original['boot_id'] and s['uptime_ms']-s['physical_touch_idle_ms']<=original['uptime_ms'] and
                not any(s.get(k) for k in ('voice_active','alarm_active','announcement_active','physical_touch_active'))):
                if night:restore_night(client)
                elif original['page'] in ('page_glass_v2','page_hemmet','page_musik','page_tavla'):select_tab(client,original['page'])
            report['restored_page']=client.status()['page']
        except Exception as error:
            report['restore_error']=str(error)
            if 'error' not in report:raise
        finally:private_write(folder/'report.json',report)
    return report

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',type=Path,required=True);args=parser.parse_args()
    with PanelClient(persistent=True) as client,client.exclusive():report=collect(client,args.out)
    print(json.dumps({'ok':report['ok'],'restored_page':report['restored_page'],
        'cases':[{k:c[k] for k in ('name','summary','verdict')} for c in report['cases']]},indent=2))
    return 0 if report['ok'] else 1

if __name__=='__main__':raise SystemExit(main())
