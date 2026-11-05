#!/usr/bin/env python3
"""Private Jarvis side of tools/panel calendar-check; owns only its nonce-tagged probe event."""
import json
import os
from pathlib import Path
import re
import sys
import time
from datetime import datetime, timedelta
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

sys.path.insert(0,str(Path(__file__).resolve().parent/'agents'))
from agent_core import mcp_client
from calendar_sync import Metadata


def settings():
    values={}
    for line in (Path.home()/'.config/hallpanel/server.env').read_text().splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            key,value=line.split('=',1)
            if key in ('HALLPANEL_TOKEN','JARVIS_CALENDAR_MCP','JARVIS_CALENDAR_MCP_TOKEN_FILE'):
                values[key]=value.strip().strip('"').strip("'")
    return {**values,**{k:v for k,v in os.environ.items() if k in values}}


def run(command):
    action,nonce=command['action'],command.get('nonce','')
    if action=='metadata':
        meta=Metadata().poll()
        return {'ok':True,'calendars':len(meta),'change_tokens':sum(bool(v['token']) for v in meta.values())}
    if not re.fullmatch('[0-9a-f]{16}',nonce):raise ValueError('Invalid probe nonce')
    config=settings()
    def get(path):
        req=Request('http://127.0.0.1:8765'+path,headers={'X-Hallpanel-Token':config['HALLPANEL_TOKEN']})
        with urlopen(req,timeout=10) as response:
            return json.load(response)
    if action=='status':
        return {'ok':True,'sync':get('/calendar/status'),
                'ha_payload_contains_probe':any(nonce in str(v) for k,v in get('/panel').items() if k in ('cal0','cal1','cal2'))}
    if action not in ('create','edit','cleanup'):raise ValueError('Invalid probe action')
    client=mcp_client(config['JARVIS_CALENDAR_MCP'],config['JARVIS_CALENDAR_MCP_TOKEN_FILE'])
    client.start()
    def call(name,args):
        failed,text=client.run(name,args,timeout=25)
        if failed or text.startswith(('FAILED:','REFUSED:','STILL WORKING')):
            raise RuntimeError('Calendar probe call was not confirmed')
        return text
    folder=Path.home()/'.local/state/hallpanel/calendar-probes';folder.mkdir(parents=True,exist_ok=True,mode=0o700)
    path=folder/(nonce+'.json')
    title='T '+nonce  # Entire identity must fit the physical calendar label, including after edit.
    if action=='create':
        if path.exists():raise RuntimeError('Probe already exists; never retry creation')
        start=datetime.now(ZoneInfo('Europe/Stockholm'))+timedelta(minutes=2)
        state={'title':title,'calendar':'Att göra','start':start.strftime('%Y-%m-%d %H:%M')}
        fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(fd,'w') as out:json.dump(state,out)
        text=call('add_event',{'calendar':'Att göra','title':title,'start':state['start'],
                             'end':(start+timedelta(minutes=5)).strftime('%Y-%m-%d %H:%M'),
                             'notes':'Tillfälligt Hallpanel-test. Tas bort automatiskt.','alarms':[]})
        found=re.findall(r'\(id ([^;\s()]+); change [^\s()]+\)',text)
        if len(found)!=1:raise RuntimeError('Probe creation needs cleanup search')
        state['uid']=found[0];path.write_text(json.dumps(state))
        return {'ok':True,'display_title':title,'acknowledged_at':time.time()}
    if not path.exists():return {'ok':True,'already_absent':True}
    state=json.loads(path.read_text())
    if state['title'] not in (title,'Paneltest '+nonce) or state['calendar']!='Att göra':raise RuntimeError('Probe ownership mismatch')
    title=state['title']
    updated_title='U '+nonce if title.startswith('T ') else title+' uppdaterad'
    # Find by the unique title before every mutation; never accept an arbitrary UID from a caller.
    day=state['start'][:10]
    text=call('search',{'text':nonce,'start':day,'end':day,'calendar':'Att göra'})
    owned=[line for line in text.splitlines() if '| '+title+' |' in line or '| '+updated_title+' |' in line]
    ids=[m.group(1) for line in owned if (m:=re.search(r'\| id (\S+)',line))]
    if not ids and action=='cleanup':
        if not state.get('uid'):
            raise RuntimeError('Unconfirmed creation; retain the cleanup record and inspect later')
        path.unlink();return {'ok':True,'already_absent':True,'acknowledged_at':time.time()}
    if len(ids)!=1 or (state.get('uid') and state['uid']!=ids[0]):raise RuntimeError('Probe identity mismatch')
    if action=='edit':
        call('edit_event',{'id':ids[0],'title':updated_title})
    else:
        call('delete_event',{'id':ids[0]});path.unlink()
    return {'ok':True,'display_title':updated_title if action=='edit' else None,'acknowledged_at':time.time()}


if __name__=='__main__':
    try:print(json.dumps(run(json.load(sys.stdin)),ensure_ascii=False))
    except Exception as error:
        print(json.dumps({'ok':False,'error':type(error).__name__}));sys.exit(1)
