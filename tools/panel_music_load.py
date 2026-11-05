#!/usr/bin/env python3
"""Silent mixed UI load on the real panel: record, cover preparation, metadata and command feedback.

No HA/model/audio calls. Raw timings stay private. A synthetic cover is not an HTTP/download test.
"""
import argparse
import json
from pathlib import Path
import time

from panel_remote import PanelClient
from panel_gates import leave_music, leave_night, restore_night
from panel_metrics import private_write
from panel_responsiveness import telemetry, presentation, delta, stats, guard, POLICY
from panel_ui_test import select_tab
from vinyl_flow import motion_summary, active_presentations, stable_verdict

PROTOCOL = 'music-mixed18-v1'


def verdict(report):
    active = report['summary']['active_motion']
    # Reuse the existing record/source scanout limits, without substituting redraw average for real images.
    result = stable_verdict(dict(report, protocol='vinyl-next8-v2', summary={'next': {'active_motion': active}}))
    rows = report['rows']
    if rows[-1]['status'].get('music_load_steps', 0) < 25:
        result['failures'].append('insufficient metadata/feedback changes')
    if rows[-1]['status'].get('music_load_covers', 0) < 5:
        result['failures'].append('insufficient cover preparations')
    if rows[-1]['status']['vinyl']['generation'] <= rows[0]['status']['vinyl']['generation']:
        result['failures'].append('no new cover presented')
    if 'navigation_summary' in report:
        limits=json.loads(POLICY.read_text())['limits']
        for key in ('navigation.remote_press_ms','navigation.remote_release_ms'):
            timing=report['navigation_summary'].get(key,{})
            if timing.get('n',0)!=20:result['failures'].append(key+': insufficient fresh samples')
            for statistic, ceiling in limits[key].items():
                if timing.get(statistic,float('inf'))>ceiling:
                    result['failures'].append(key+': '+statistic+' exceeds existing absolute limit')
        if report.get('navigation_cover_requests',0)<1:
            result['failures'].append('navigation had no new background cover work')
    result['ok'] = not result['failures']
    return result


def navigation(client, original, report, save):
    """Separate phase: 20 real tab taps while cover preparation continues offscreen; no pacing exclusions."""
    client.test_mode('music_load',26000)  # same running fixture; only renews its bounded deadline
    leave_music(client)
    select_tab(client,'page_glass_v2')
    report['navigation']=[]
    press=[];release=[];host=[]
    before=client.status();covers=before['music_load_covers']
    for index in range(20):
        page,label=('page_hemmet','Hemmet') if index%2==0 else ('page_glass_v2','Gå hemifrån')
        ui=client.ui()
        source='page_glass_v2' if index%2==0 else 'page_hemmet'
        if ui.get('page')!=source or ui.get('truncated'):raise RuntimeError('Navigation source changed')
        widgets=[w for w in ui['widgets'] if w.get('text')==label]
        if len(widgets)!=1:raise RuntimeError('Navigation target ambiguous')
        w=widgets[0];before=client.status();guard(before,original,'music_load')
        if not before.get('music_load_active'):raise RuntimeError('Background load stopped before navigation')
        a={'status':before,'timing':telemetry(before)}
        start=time.monotonic()
        client.tap(w['x']+w['width']//2,w['y']+w['height']//2,duration=60,settle=0)
        host.append((time.monotonic()-start)*1000)
        deadline=time.monotonic()+2
        while True:
            after=client.status();guard(after,original,'music_load')
            if not after.get('music_load_active'):raise RuntimeError('Background load stopped during navigation')
            b={'status':after,'timing':telemetry(after)}
            released=delta(a,b,'remote_release')
            if after['page']==page and len(released)==1:break
            if len(released)>1 or time.monotonic()>=deadline:raise RuntimeError('Navigation redraw not acknowledged')
            time.sleep(.02)
        pressed=delta(a,b,'remote_press')
        if len(pressed)!=1:raise RuntimeError('Navigation press missing/ambiguous')
        press.extend(v/1000 for v in pressed);release.extend(v/1000 for v in released)
        report['navigation'].append({'before':a,'after':b,'command_host_ms':host[-1]});save()
    report['navigation_summary']={'navigation.remote_press_ms':stats(press),
        'navigation.remote_release_ms':stats(release),'navigation.command_roundtrip_ms':stats(host)}
    report['navigation_cover_requests']=after['music_load_covers']-covers
    report['navigation_comparison']='Existing absolute input limits only; this mixed workload is not the idle baseline protocol.'


def collect(client, folder):
    folder.mkdir(parents=True, mode=0o700, exist_ok=False)
    report = {'protocol': PROTOCOL, 'rows': [], 'complete': False}
    initial = client.status()
    private_write(folder/'before.json', initial)
    night = False
    def save():
        private_write(folder/'report.json', report)
    try:
        night = leave_night(client, 10)
        leave_music(client)
        if client.status()['page'] not in ('page_glass_v2', 'page_hemmet', 'page_tavla'):
            raise RuntimeError('Mixed load requires an idle day page')
        client.test_mode('music_load', 26000)
        time.sleep(1.5)
        start = time.monotonic()
        while time.monotonic()-start < 18:
            before = time.monotonic()
            s = client.status()
            end = time.monotonic()
            row = {'stage': 'next', 'at_s': end-start, 'host_ms': (end-before)*1000, 'status': s,
                   'timing': telemetry(s), 'presentation': presentation(s),
                   'record_presentation': presentation({'presentation': s.get('vinyl_presentation')})}
            report['rows'].append(row)
            save()
            if (s['boot_id'] != initial['boot_id'] or s['page'] != 'page_musik' or
                not s.get('music_load_active') or s.get('physical_touch_active') or
                any(s.get(k) for k in ('voice_active', 'alarm_active', 'announcement_active'))):
                raise RuntimeError('Mixed load interrupted; real user/audio/metadata takes precedence')
            time.sleep(.1)
        report['summary'] = motion_summary(report['rows'])
        report['summary']['active_motion'] = active_presentations(report['rows'])
        navigation(client,initial,report,save)
        report['verdict'] = verdict(report)
        report['complete'] = True
    except Exception as error:
        report['error'] = {'type': type(error).__name__, 'message': str(error)}
        raise
    finally:
        try:
            client.test_mode('restore', 100)
            s = client.status()
            # Return through real navigation only while the original boot is still idle and unclaimed.
            if (s['boot_id'] == initial['boot_id'] and not s.get('physical_touch_active') and
                s.get('physical_touch_idle_ms', 0) >= s['uptime_ms']-initial['uptime_ms'] and
                not any(s.get(k) for k in ('voice_active', 'alarm_active', 'announcement_active'))):
                if night:
                    leave_music(client)
                    restore_night(client)
                elif initial['page'] == 'page_musik' and s['page'] != 'page_musik':
                    client.tap_text('Spelas nu')
                elif initial['page'] in ('page_glass_v2','page_hemmet','page_tavla'):
                    select_tab(client,initial['page'])
            report['restored_page'] = client.status()['page']
        except Exception as error:
            report['restore_error'] = str(error)
            if 'error' not in report:
                raise
        finally:
            save()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    with PanelClient(persistent=True) as client, client.exclusive():
        report = collect(client, args.out)
    print(json.dumps({k: report[k] for k in ('complete', 'summary', 'navigation_summary', 'verdict', 'restored_page')}, indent=2))
    return 0 if report['verdict']['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
