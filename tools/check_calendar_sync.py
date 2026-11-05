#!/usr/bin/env python3
"""Measure create/edit/delete from iCloud acknowledgement to the real panel. Free; own probe only."""
import argparse
import json
from pathlib import Path
import re
import secrets
import subprocess
import time

from panel_remote import PanelClient
from panel_ui_test import bar_page,expect_page


def preempted(before,state):
    return (state.get('boot_id')!=before.get('boot_id')
            or any(state.get(k) for k in ('physical_touch_active','voice_active','alarm_active','announcement_active'))
            or state.get('test_mode','idle')!='idle'
            or state.get('uptime_ms',0)-state.get('physical_touch_idle_ms',0)>before.get('uptime_ms',0))


def remote(host,action,nonce,log_folder=None):
    if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.@:-]*',host):raise ValueError('Invalid SSH host')
    command=['ssh','-o','BatchMode=yes','-o','ConnectTimeout=5','-o','IgnoreUnknown=UseKeychain',
             '-o','UseKeychain=yes','-o','ControlMaster=auto','-o',str('ControlPath='+str(Path.home()/'.ssh/cm-%C')),
             '-o','ControlPersist=10m',host,'python3 ~/hallpanel-live/server/check_calendar_sync.py']
    result=subprocess.run(command,input=json.dumps({'action':action,'nonce':nonce}),capture_output=True,
                          text=True,timeout=45)
    if log_folder is not None and result.stderr:
        path=log_folder/('remote-'+action+'.log');path.write_text(result.stderr[-16000:]);path.chmod(0o600)
    data=json.loads(result.stdout)
    if result.returncode or not data.get('ok'):raise RuntimeError('Calendar probe operation failed: '+action)
    return data


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host',default='user@jarvis.local')
    parser.add_argument('--url',default='http://hallpanel.local:8080')
    parser.add_argument('--metadata',action='store_true',help='Read-only metadata capability check')
    parser.add_argument('--live',action='store_true',help='Create, edit and delete one own silent probe event')
    parser.add_argument('--cleanup',metavar='NONCE',help='Remove only an owned probe left by a prior interrupted test')
    parser.add_argument('--out',required=True,type=Path)
    args=parser.parse_args()
    if sum(bool(v) for v in (args.metadata,args.live,args.cleanup))!=1:parser.error('Select --metadata, --live or --cleanup')
    args.out.mkdir(parents=True,exist_ok=False,mode=0o700)
    report={'ok':False,'nonce':args.cleanup or secrets.token_hex(8),'measurements':[],
            'origin':'iCloud acknowledgement received by runner; Mac upload and SSH return delay excluded'}
    def call(action):return remote(args.host,action,report['nonce'],args.out)
    try:
        if args.metadata:
            report.update(call('metadata'));return 0
        if args.cleanup:
            report.update(call('cleanup'));return 0
        with PanelClient(args.url,persistent=True) as panel,panel.exclusive():
            before=panel.status();original=before['page']
            report['original_page']=original
            if original not in ('page_glass_v2','page_hemmet','page_tavla') or any(before.get(k) for k in
                    ('voice_active','alarm_active','announcement_active','physical_touch_active')):
                raise RuntimeError('Panel is not idle')
            try:
                report['phase']='before-snapshot'
                panel.inspect(args.out/'before',lossless=True)
                report['phase']='navigation'
                bar_page(panel);panel.tap_text('Gå hemifrån');expect_page(panel,'page_glass_v2')
                for action in ('create','edit','cleanup'):
                    report['phase']=action+'-ack'
                    requested=time.monotonic()
                    ack=call(action)
                    report['phase']=action+'-observe'
                    start=time.monotonic();deadline=start+45
                    while True:
                        state=panel.status()
                        if state['page']!='page_glass_v2' or preempted(before,state):
                            raise RuntimeError('Panel preempted probe')
                        texts=[w.get('text','') for w in panel.ui()['widgets']]
                        matches=[text for text in texts if report['nonce'] in text]
                        found=(not matches) if action=='cleanup' else ack['display_title'] in matches
                        if found:break
                        if time.monotonic()>=deadline:raise TimeoutError('Calendar update did not reach panel in 45 seconds')
                        time.sleep(.5)
                    report['measurements'].append({'action':action,
                        'observed_after_ack_ms':round((time.monotonic()-start)*1000,1),
                        'request_to_panel_ms':round((time.monotonic()-requested)*1000,1)})
                    report['phase']=action+'-status'
                    report[action+'_status']=call('status')
                    report['phase']=action+'-snapshot'
                    panel.inspect(args.out/action,lossless=True)
                report['phase']='complete'
                report['ok']=True
            except Exception:
                try:
                    report['failure_status']=call('status')
                    panel.inspect(args.out/'failure',lossless=True)
                except Exception as diagnostic:
                    report['diagnostic_error']=type(diagnostic).__name__
                raise
            finally:
                try:report['cleanup']=call('cleanup')
                finally:
                    state=panel.status()
                    report['restored']=False
                    if not preempted(before,state) and state['page'] in ('page_glass_v2','page_hemmet','page_tavla'):
                        bar_page(panel)
                        panel.tap_text({'page_glass_v2':'Gå hemifrån','page_hemmet':'Hemmet','page_tavla':'Tavla'}[original])
                        expect_page(panel,original)
                        report['restored']=True
        return 0
    except Exception as error:
        report['ok']=False;report['error']=type(error).__name__;return 1
    finally:
        (args.out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
        (args.out/'report.json').chmod(0o600)
        print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':raise SystemExit(main())
