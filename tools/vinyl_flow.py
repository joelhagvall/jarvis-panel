#!/usr/bin/env python3
"""Bounded real music test: steady, pause/play, one next track. No model; preserves volume and restores Natt.

Use tools/panel vinyl-flow --out NEW_DIRECTORY. Music must already play on Spelas nu. The script can open
that tab from Natt/a day tab. Raw firmware/host/presentation samples are retained; pause's intentional idle
and play's first interval across idle are excluded from motion timing. Compare identical protocols only.
"""
import argparse
import json
import re
import signal
import subprocess
from pathlib import Path
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parent))
from panel_remote import PanelClient
from panel_gates import leave_night, leave_music, restore_night
from panel_metrics import private_write
from panel_responsiveness import stats, telemetry, presentation, delta, presentation_delta
PROTOCOL='vinyl-steady10-pause2-play3-next8-v2'

def motion_summary(rows, seconds=None):
    if seconds is not None:
        rows=[r for r in rows if r['at_s']<=seconds]
    if len(rows)<2:
        return {}
    result={'seconds':rows[-1]['at_s']-rows[0]['at_s'], 'host_ms':stats([r['host_ms'] for r in rows]),
            'observer_main_ms':stats([r['status']['status_main_us']/1000 for r in rows])}
    a,b=rows[0]['status'],rows[-1]['status']; elapsed=(b['uptime_ms']-a['uptime_ms'])/1000
    result['redraws_per_s']=(b['render_frames_total']-a['render_frames_total'])/elapsed if elapsed else 0
    intervals=[]; render=[]; vinyl=[]
    for pair,(before,after) in enumerate(zip(rows,rows[1:])):
        # Presentation gaps while stopped, including the first interval on resume, are intentional.
        values=presentation_delta(before,after)
        if before['stage']=='play' and pair==0:
            values=values[1:]  # Exactly one interval crosses the intentional paused state.
        intervals.extend(v/1000 for v in values)
        render.extend(v/1000 for v in delta(before,after,'refresh_work'))
        va,vb=before['status'].get('vinyl'),after['status'].get('vinyl')
        if va and vb:
            n=vb['interval_total']-va['interval_total']
            if n<0 or n>len(vb['interval_ms']):
                raise RuntimeError('Vinyl interval samples overwritten/reset')
            if n:
                vinyl.extend(vb['interval_ms'][-n:])
    if intervals:
        result['presented_ms']=stats(intervals)
        result['late_over_24ms']=sum(v>24 for v in intervals)
    if render: result['render_ms']=stats(render)
    if vinyl: result['vinyl_interval_ms']=stats(vinyl)
    # Status sees the worker's latest completed frame, not every frame. Keep that sampling limit explicit.
    for field in ('worker_us','sample_us','arm_prepare_us','arm_work_us'):
        values=[r['status']['vinyl'][field]/1000 for r in rows if field in r['status'].get('vinyl',{})]
        if values:result[field.removesuffix('_us')+'_status_samples_ms']=stats(values)
    return result

# Stable 60 means one presentation per display interval during motion, not an average diluted by intentional
# idle while waiting for playback confirmation or after braking. A missed boundary (~33ms) fails the max.
STABLE_MIN_FPS=59.0
STABLE_MAX_INTERVAL_MS=25.0
STABLE_P99_MS=18.0

def active_presentations(rows):
    values=[];record_values=[];seconds=0;frames=0;record_frames=0
    scanned=[];scanned_frames=0;scanned_boundaries=0;have_scanned=False
    for a,b in zip(rows,rows[1:]):
        va=a['status'].get('vinyl',{});vb=b['status'].get('vinyl',{})
        if va.get('speed',0)<=0 or vb.get('speed',0)<=0:
            continue
        fresh=presentation_delta(a,b);values.extend(v/1000 for v in fresh)
        n=vb.get('interval_total',0)-va.get('interval_total',0)
        if n<0 or n>len(vb.get('interval_ms',[])):
            raise RuntimeError('Moving vinyl interval samples overwritten/reset')
        if n:record_values.extend(vb['interval_ms'][-n:])
        record_frames+=n
        seconds+=(b['status']['uptime_ms']-a['status']['uptime_ms'])/1000
        frames+=b['presentation']['presented_total']-a['presentation']['presented_total']
        if a.get('record_presentation') is not None and b.get('record_presentation') is not None:
            ra,rb=a['record_presentation'],b['record_presentation'];have_scanned=True
            scanned.extend(v/1000 for v in presentation_delta(
                {'status':a['status'],'presentation':ra},{'status':b['status'],'presentation':rb}))
            scanned_frames+=rb['presented_total']-ra['presented_total']
            scanned_boundaries+=rb['vsync_total']-ra['vsync_total']
    result={'seconds':seconds,'presented_per_s':frames/seconds if seconds else 0,
            'presented_ms':stats(values) if values else None,'late_over_25ms':sum(v>25 for v in values),
            'record_per_s':record_frames/seconds if seconds else 0,
            'record_ms':stats(record_values) if record_values else None}
    if have_scanned:result.update(record_presented_per_s=scanned_frames/seconds if seconds else 0,
        record_presented_ms=stats(scanned) if scanned else None,
        record_active_boundaries=scanned_boundaries,record_repeated=scanned_boundaries-scanned_frames)
    return result

def stable_verdict(report):
    failures=[]
    stages=('next',) if report['protocol'].startswith('vinyl-next8-') else ('steady','pause','play','next')
    actual_record=report['protocol'].endswith('-v2')
    minimum={'steady':300,'pause':15,'play':60,'next':120}
    for stage in stages:
        item=report['summary'].get(stage,{}).get('active_motion',{});timing=item.get('presented_ms') or {}
        if timing.get('n',0)<minimum[stage]:failures.append(stage+': insufficient moving-frame samples')
        if item.get('presented_per_s',0)<STABLE_MIN_FPS:failures.append(stage+': below 59 presentations/s during motion')
        if timing.get('max',float('inf'))>STABLE_MAX_INTERVAL_MS:failures.append(stage+': presentation interval exceeds 25 ms')
        if timing.get('p99',float('inf'))>STABLE_P99_MS:failures.append(stage+': presentation p99 exceeds 18 ms')
        record=item.get('record_presented_ms' if actual_record else 'record_ms') or {}
        if record.get('n',0)<minimum[stage]:failures.append(stage+': insufficient record-image samples')
        if item.get('record_presented_per_s' if actual_record else 'record_per_s',0)<STABLE_MIN_FPS:failures.append(stage+': below 59 record images/s during motion')
        if record.get('max',float('inf'))>STABLE_MAX_INTERVAL_MS:failures.append(stage+': record-image interval exceeds 25 ms')
        if record.get('p99',float('inf'))>STABLE_P99_MS:failures.append(stage+': record-image p99 exceeds 18 ms')
    rows=report.get('rows',[])
    if rows:
        for name in ('artwork','vinyl'):
            field='errors' if name=='artwork' else 'gpu_errors'
            if rows[-1]['status'].get(name,{}).get(field,0)!=rows[0]['status'].get(name,{}).get(field,0):
                failures.append(name+': new worker/hardware error')
        if rows[-1]['status'].get('vinyl',{}).get('arm_errors',0)!=rows[0]['status'].get('vinyl',{}).get('arm_errors',0):
            failures.append('vinyl: new arm worker/publication error')
    return {'ok':not failures,'failures':failures,'minimum_fps':STABLE_MIN_FPS,
            'maximum_interval_ms':STABLE_MAX_INTERVAL_MS,'maximum_p99_ms':STABLE_P99_MS,
            'record_measurement':'source serial at scanout' if actual_record else 'UI handoff intervals'}

# The analysis worked out by hand on 2026-10-28/29 for every missed boundary: which moving-record pair held it, the
# worker's costs then, the LVGL phases around it and how the image queue was taken. Keep it here, not in notes.
VINYL_FIELDS=('speed','worker_us','sample_us','sample_method','arm_prepare_us','render_pixels')
PHASES=('refresh_work','layout_sync','render_work','flush_wait','vsync_latency')
QUEUE=('taken_boundary','taken_late','taken_spare')
def explain(report,limit_ms=25.0):
    result={'stages':{},'misses':[]}
    for stage in ('steady','pause','play','next'):
        rows=[r for r in report.get('rows',[]) if r['stage']==stage]
        if len(rows)<2:continue
        item=report.get('summary',{}).get(stage,{}).get('active_motion',{})
        a,b=rows[0]['status'].get('vinyl',{}),rows[-1]['status'].get('vinyl',{})
        result['stages'][stage]={'record_per_s':item.get('record_presented_per_s'),'repeated':item.get('record_repeated'),
            'record_ms':item.get('record_presented_ms'),'queue':{k:b[k]-a[k] for k in QUEUE if k in a and k in b},
            'psram_min':min(r['status'].get('psram_free',0) for r in rows)}
        for before,after in zip(rows,rows[1:]):
            if before['status'].get('vinyl',{}).get('speed',0)<=0 or after['status'].get('vinyl',{}).get('speed',0)<=0:
                continue
            ui=presentation_delta(before,after)
            record=[]
            if before.get('record_presentation') is not None and after.get('record_presentation') is not None:
                record=presentation_delta({'status':before['status'],'presentation':before['record_presentation']},
                                          {'status':after['status'],'presentation':after['record_presentation']})
            if max(ui+record,default=0)<=limit_ms*1000:continue
            vinyl=after['status'].get('vinyl',{})
            result['misses'].append({'stage':stage,'at_s':round(after['at_s'],3),
                'vinyl':{k:vinyl[k] for k in VINYL_FIELDS if k in vinyl},
                'ui_ms':[round(v/1000,1) for v in ui],'record_ms':[round(v/1000,1) for v in record],
                'phases_ms':{m:[round(v/1000,1) for v in delta(before,after,m)] for m in PHASES
                             if m in before.get('timing',{}) and m in after.get('timing',{})}})
    result['next_timeline']=next_timeline(report)
    result['log_events']=report.get('log_events',[])
    return result

# The firmware's own timing lines (`[artwork] Artwork ready ... in N ms`, `[vinyl] ... turns after N ms`).
LOG_EVENT=re.compile(r'\[(\d\d:\d\d:\d\d\.\d{3})\]\[[IWE]\]\[(artwork|vinyl)[^\]]*\](?:\[[a-z]+\])?: (.*)')
def log_events(text):
    return [{'at':m.group(1),'tag':m.group(2),'line':m.group(3)[:160]} for m in map(LOG_EVENT.search,text.splitlines()) if m]

def next_timeline(report):
    """Seconds after the next tap until the new atlas started building and until the record showed it."""
    rows=[r for r in report.get('rows',[]) if r['stage']=='next']
    if not rows:return None
    first=rows[0]['status'].get('vinyl',{});building=shown=None
    for row in rows:
        vinyl=row['status'].get('vinyl',{})
        if building is None and vinyl.get('prepare_rows',0)<first.get('prepare_rows',0):building=round(row['at_s'],2)
        if vinyl.get('generation',0)>first.get('generation',0):
            shown=round(row['at_s'],2);break
    return {'build_started_s':building,'new_record_s':shown,
            'prepare_ms':rows[-1]['status'].get('vinyl',{}).get('prepare_ms')}

PAUSE='\U000F03E4'
PLAY='\U000F040A'
NEXT='\U000F04AD'
def controls(ui):
    paths={w['path']:w for w in ui.get('widgets',[])}
    found={}
    for name,icons in (('toggle',(PAUSE,PLAY)),('next',(NEXT,))):
        matches=[]
        for widget in paths.values():
            if widget.get('text') not in icons:continue
            button=paths.get(widget['path'].rsplit('/',1)[0],{})
            if button.get('type')=='button' and not button.get('disabled'):
                matches.append((button,widget['text']))
        if len(matches)!=1:raise RuntimeError('Expected one enabled music '+name+' button')
        button,icon=matches[0]
        found[name]=(button['x']+button['width']//2,button['y']+button['height']//2)
        found[name+'_path']=button['path'];found[name+'_icon']=icon
    if found['toggle_path'].rsplit('/',1)[0]!=found['next_path'].rsplit('/',1)[0]:
        raise RuntimeError('Music controls do not share one row')
    return found

def track_album(ui):
    # The album is the column's fourth child. Derive the column from its real controls, not its changing y.
    column=controls(ui)['toggle_path'].rsplit('/',2)[0]
    return next((w.get('text','') for w in ui.get('widgets',[]) if w['path']==column+'/4' and w.get('type')=='label'),'')


def collect(client,folder,only_next=False,exist_ok=False):
    folder.mkdir(mode=0o700,parents=True,exist_ok=exist_ok)
    report={'protocol':'vinyl-next8-v2' if only_next else PROTOCOL,'rows':[],'actions':[],'complete':False}
    night=False; paused=False; initial=None
    original=client.status()
    def save():private_write(folder/'report.json',report)
    def read(stage,start):
        t=time.monotonic();s=client.status();end=time.monotonic()
        if s['page']!='page_musik' or s.get('voice_active') or s.get('physical_touch_active'):
            raise RuntimeError('Panel left music or is in use')
        if initial and s['boot_id']!=initial['boot_id']:raise RuntimeError('Panel restarted')
        row={'stage':stage,'at_s':end-start,'host_ms':(end-t)*1000,'status':s,'timing':telemetry(s),'presentation':presentation(s)}
        row['record_presentation']=presentation({'presentation':s.get('vinyl_presentation')})
        report['rows'].append(row);save();return row
    def phase(stage,seconds,action=None):
        nonlocal paused
        resolved_ms=None
        if action:
            began=time.monotonic();buttons=controls(client.ui());resolved_ms=(time.monotonic()-began)*1000
            if stage in ('pause','play') and buttons['toggle_icon']!=(PAUSE if stage=='pause' else PLAY):
                raise RuntimeError('Unexpected playback state before '+stage)
            xy=buttons[action]
        start=time.monotonic();read(stage,start)
        if action:
            t=time.monotonic();client.tap(*xy);report['actions'].append({'stage':stage,'host_ms':(time.monotonic()-t)*1000,
                'control_xy':list(xy),'resolve_host_ms':resolved_ms})
            if stage=='pause':paused=True
            elif stage=='play':paused=False
        until=start+seconds
        while time.monotonic()<until:
            time.sleep(min(.15,max(0,until-time.monotonic())));read(stage,start)
        rows=[r for r in report['rows'] if r['stage']==stage]
        report.setdefault('summary',{})[stage]=motion_summary(rows,1.0 if stage=='pause' else None)
        report['summary'][stage]['active_motion']=active_presentations(rows)
        if stage=='next':report['summary']['next_first2s']=motion_summary(rows,2)
        save()
    try:
        night=leave_night(client,10)
        if client.status()['page']!='page_musik':client.tap_text('Spelas nu');time.sleep(.4)
        initial=client.status();ui=client.ui();private_write(folder/'before-ui.json',ui)
        if controls(ui)['toggle_icon']!=PAUSE:raise RuntimeError('Music must already be playing')
        report['before_album']=track_album(ui)
        if not only_next:
            phase('steady',10)
            phase('pause',2,'toggle');private_write(folder/'pause-ui.json',client.ui())
            phase('play',3,'toggle')
        phase('next',8,'next');after=client.ui();private_write(folder/'after-ui.json',after)
        report['after_album']=track_album(after)
        report['album_changed']=bool(report['before_album'] and report['after_album'] and report['before_album']!=report['after_album'])
        next_rows=[r for r in report['rows'] if r['stage']=='next']
        report['record_generation_changed']=bool(next_rows and
            next_rows[-1]['status'].get('vinyl',{}).get('generation',0)>
            next_rows[0]['status'].get('vinyl',{}).get('generation',0))
        report['complete']=True
    except Exception as error:
        report['failure']={'type':type(error).__name__,'message':str(error)}
        raise
    finally:
        try:
            if paused:
                # Restore only if the page still offers play. Never blindly toggle an uncertain/user-changed state.
                ui=client.ui()
                buttons=controls(ui)
                if buttons['toggle_icon']==PLAY:client.tap(*buttons['toggle'])
            if night:leave_music(client);restore_night(client)
            elif original['page'] in ('page_glass_v2','page_hemmet'):
                current=client.status()
                if (current.get('boot_id')==original.get('boot_id') and
                    not any(current.get(k) for k in ('physical_touch_active','voice_active','alarm_active','announcement_active')) and
                    current.get('physical_touch_idle_ms',-1)>=current['uptime_ms']-original['uptime_ms']):
                    leave_music(client)
                    client.tap_text('Gå hemifrån' if original['page']=='page_glass_v2' else 'Hemmet')
            report['restored_page']=client.status()['page']
        except Exception as restore_error:
            report['restore_error']={'type':type(restore_error).__name__,'message':str(restore_error)}
            if 'failure' not in report:raise
        finally:save()
    return report

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,help='New private directory for a real run')
    parser.add_argument('--explain',type=Path,metavar='REPORT',help='Explain a saved report.json (no panel access)')
    parser.add_argument('--log',action='store_true',help='Record the ESPHome log meanwhile (OUT/esphome.log) and keep '
                        'its [artwork]/[vinyl] timing lines in the report')
    parser.add_argument('--queue',metavar='SPOTIFY_URI',help='Queue this track on Jarvis\'s Soloist first (another album '
                        'for --require-album-change)')
    parser.add_argument('--require-60',action='store_true',help='Fail on a missed presentation boundary during motion')
    parser.add_argument('--next-only',action='store_true',help='Only the eight-second next phase')
    parser.add_argument('--require-album-change',action='store_true',help='Fail the diagnosis when next stays on the same album')
    args=parser.parse_args()
    if args.explain:
        print(json.dumps(explain(json.loads(args.explain.read_text())),ensure_ascii=False,indent=1));return 0
    if not args.out:parser.error('--out is required for a run')
    here=Path(__file__).resolve().parent
    if args.queue:
        if not re.fullmatch(r'spotify:track:[A-Za-z0-9]{22}',args.queue):parser.error('--queue needs spotify:track:<id>')
        subprocess.run(['sh',str(here/'jarvis_ssh.sh'),'user@jarvis.local',
                        '~/.local/share/soloist/bin/soloist ctl --ws 127.0.0.1:9431 add-to-queue '+args.queue],check=True)
    capture=None
    if args.log:
        args.out.mkdir(mode=0o700,parents=True,exist_ok=False)
        capture=subprocess.Popen([str(here/'panel'),'crash','capture','--device','hallpanel.local',
                                  '--out',str(args.out/'esphome.log')],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        time.sleep(5)   # connect before the first tap
    try:
        with PanelClient(persistent=True) as client,client.exclusive():
            report=collect(client,args.out,args.next_only,exist_ok=bool(capture))
    finally:
        if capture:
            capture.send_signal(signal.SIGTERM)
            try:capture.wait(10)
            except subprocess.TimeoutExpired:capture.kill()
    if capture and (args.out/'esphome.log').exists():
        report['log_events']=log_events((args.out/'esphome.log').read_text(errors='replace'))
    report['stable_60']=stable_verdict(report)
    report['handoff_verdict_v1']=stable_verdict(dict(report,protocol=report['protocol'].removesuffix('-v2')+'-v1'))
    private_write(args.out/'report.json',report)
    print(json.dumps({'stable_60':report['stable_60'],'complete':report['complete'],'summary':report['summary'],'restored_page':report['restored_page'],'report':str(args.out/'report.json'),'album_changed':report['album_changed'],'record_generation_changed':report['record_generation_changed']},ensure_ascii=False,indent=2))
    if args.require_60 and not report['stable_60']['ok']:return 1
    return 2 if args.require_album_change and not album_change_verified(report) else 0

def album_change_verified(report):
    return bool(report.get('album_changed') and report.get('record_generation_changed'))
if __name__=='__main__':raise SystemExit(main())
