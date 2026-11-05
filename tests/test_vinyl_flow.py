"""Vinyl diagnostics preserve slow samples and distinguish a paused interval from animation jank."""
import sys
from pathlib import Path
import unittest
import tempfile
import json
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from vinyl_flow import log_events,explain,motion_summary,track_album,active_presentations,stable_verdict,controls,collect,PAUSE,NEXT,album_change_verified

def music_ui(offset=0):
    widgets=[{'path':'screen/2/0/2','type':'label','text':'A new song'},
             {'path':'screen/2/0/4','type':'label','text':'Same album'}]
    for child,x,icon in ((1,536,PAUSE),(3,712,NEXT)):
        path='screen/2/0/8/'+str(child)
        widgets.extend([{'path':path,'type':'button','x':x,'y':319+offset,'width':80,'height':80},
                        {'path':path+'/0','type':'label','text':icon}])
    return {'widgets':widgets}

def row(stage,at,total,values):
    return {'stage':stage,'at_s':at,'host_ms':20,'status':{'uptime_ms':int(at*1000),'boot_id':1,
       'render_frames_total':total,'status_main_us':350},
       'presentation':{'total':total,'us':values,'vsync_total':total,'presented_total':total},
       'timing':{'refresh_work':{'total':total,'us':[8000]*total}}}
class VinylFlowTest(unittest.TestCase):
    def test_log_events_keep_only_artwork_and_vinyl_timing(self):
        text=('10:14:06 [10:14:06.935][I][artwork:091][artwork]: Artwork ready: 116115 bytes in 637 ms on worker\n'
              '10:14:07 [10:14:07.001][I][latency:047]: Touch sample -> next refresh ready 3.7 ms\n'
              '10:14:08 [10:14:08.208][I][vinyl:370][vinyl]: Page: new cover turns after 1256 ms (half its images)\n')
        self.assertEqual([(e['at'],e['tag']) for e in log_events(text)],[('10:14:06.935','artwork'),('10:14:08.208','vinyl')])
        self.assertIn('637 ms',log_events(text)[0]['line'])
    def test_explain_names_each_missed_moving_boundary_and_the_queue(self):
        def moving(at,total,values,taken):
            r=row('pause',at,total,values);r['status']['vinyl']={'speed':.5,'worker_us':9000,'taken_boundary':taken,
                'taken_late':0,'taken_spare':taken};r['status']['psram_free']=11000000;return r
        report={'rows':[moving(0,0,[],0),moving(.2,3,[16600,33100,16600],3)],'summary':{}}
        result=explain(report)
        self.assertEqual(len(result['misses']),1)
        self.assertEqual(result['misses'][0]['ui_ms'],[16.6,33.1,16.6])
        self.assertEqual(result['misses'][0]['vinyl']['worker_us'],9000)
        self.assertEqual(result['stages']['pause']['queue'],{'taken_boundary':3,'taken_late':0,'taken_spare':3})
        nxt=lambda at,rows,gen:{'stage':'next','at_s':at,'status':{'vinyl':{'prepare_rows':rows,'generation':gen,'prepare_ms':2575}}}
        self.assertEqual(explain({'rows':[nxt(0,15552,3),nxt(1.97,40,3),nxt(4.59,15552,4)]})['next_timeline'],
                         {'build_started_s':1.97,'new_record_s':4.59,'prepare_ms':2575})
        report['rows'][1]['status']['vinyl']['speed']=0     # an intentional stop is not a miss
        self.assertEqual(explain(report)['misses'],[])
    def test_album_metadata_alone_cannot_prove_a_new_record_was_shown(self):
        self.assertFalse(album_change_verified({'album_changed':True,'record_generation_changed':False}))
        self.assertFalse(album_change_verified({'album_changed':False,'record_generation_changed':True}))
        self.assertTrue(album_change_verified({'album_changed':True,'record_generation_changed':True}))
    def test_album_check_uses_the_album_label_not_the_song_title(self):
        ui=music_ui()
        self.assertEqual(track_album(ui),'Same album')
        ui['widgets']=ui['widgets'][1:]
        self.assertEqual(track_album(ui),'Same album')

    def test_wrapped_title_moves_controls_without_changing_identity(self):
        for offset in (0,20,42):
            self.assertEqual(controls(music_ui(offset))['toggle'],(576,359+offset))
            self.assertEqual(controls(music_ui(offset))['next'],(752,359+offset))
            self.assertEqual(track_album(music_ui(offset)),'Same album')

    def test_ambiguous_disabled_or_unrelated_controls_fail_before_input(self):
        duplicate=music_ui();duplicate['widgets']+=music_ui()['widgets'][2:4]
        duplicate['widgets'][-2]=dict(duplicate['widgets'][-2],path='screen/3/1')
        duplicate['widgets'][-1]=dict(duplicate['widgets'][-1],path='screen/3/1/0')
        disabled=music_ui();disabled['widgets'][2]['disabled']=True
        unrelated=music_ui();unrelated['widgets'][-2]['path']='screen/3/3';unrelated['widgets'][-1]['path']='screen/3/3/0'
        for ui in (duplicate,disabled,unrelated):
            with self.assertRaises(RuntimeError):controls(ui)

    def test_restore_failure_preserves_input_timeout_and_never_replays_it(self):
        class Client:
            taps=0
            def status(self):
                if self.taps:raise OSError('lease lost')
                return {'page':'page_musik','boot_id':1}
            def ui(self):return music_ui(20)
            def tap(self,*xy):
                self.taps+=1
                raise TimeoutError('input outcome unknown')
        client=Client()
        with tempfile.TemporaryDirectory() as tmp, patch('vinyl_flow.leave_night',return_value=False), \
             patch('vinyl_flow.telemetry',return_value={}),patch('vinyl_flow.presentation',return_value={}):
            folder=Path(tmp)/'flow'
            with self.assertRaisesRegex(TimeoutError,'input outcome unknown'):collect(client,folder,True)
            report=json.loads((folder/'report.json').read_text())
            self.assertEqual(report['failure']['type'],'TimeoutError')
            self.assertEqual(report['restore_error']['message'],'lease lost')
        self.assertEqual(client.taps,1)

    def test_missing_playback_restores_the_original_day_tab(self):
        class Client:
            page='page_glass_v2'
            def status(self):return {'page':self.page,'boot_id':1,'uptime_ms':1000,'physical_touch_idle_ms':1000}
            def ui(self):return {'widgets':[]}
            def tap_text(self,text):self.page='page_musik' if text=='Spelas nu' else 'page_glass_v2'
            def swipe(self,*args):self.page='page_hemmet'
        client=Client()
        with tempfile.TemporaryDirectory() as tmp,patch('vinyl_flow.leave_night',return_value=False),patch('vinyl_flow.time.sleep'):
            with self.assertRaisesRegex(RuntimeError,'Expected one enabled music toggle'):
                collect(client,Path(tmp)/'flow',True)
            self.assertEqual(client.page,'page_glass_v2')

    def test_a_slow_album_change_is_retained(self):
        report=motion_summary([row('next',0,0,[]),row('next',.5,1,[160000])])
        self.assertEqual(report['presented_ms']['max'],160)
        self.assertEqual(report['late_over_24ms'],1)
    def test_worker_timings_are_explicit_status_samples_and_retain_slow_work(self):
        a=row('next',0,0,[]);b=row('next',.5,1,[16000])
        a['status']['vinyl']={'worker_us':1000};b['status']['vinyl']={'worker_us':123000,'sample_us':3000}
        for r in (a,b):r['status']['vinyl'].update(interval_total=0,interval_ms=[])
        report=motion_summary([a,b])
        self.assertEqual(report['worker_status_samples_ms']['max'],123)
        self.assertEqual(report['worker_status_samples_ms']['n'],2)
        self.assertEqual(report['sample_status_samples_ms']['n'],1)
    def test_only_first_resume_interval_crosses_intentional_idle(self):
        report=motion_summary([row('play',0,0,[]),row('play',.5,2,[2500000,17000]),row('play',1,3,[2500000,17000,150000])])
        self.assertEqual(report['presented_ms']['n'],2)
        self.assertEqual(report['presented_ms']['max'],150)
    def test_intentional_stillness_is_excluded_but_active_stalls_are_not(self):
        a=row('play',0,0,[]);b=row('play',.5,1,[500000]);c=row('play',1,2,[500000,33000])
        a['status']['vinyl']={'speed':0};b['status']['vinyl']={'speed':.2};c['status']['vinyl']={'speed':1}
        summary=active_presentations([a,b,c])
        self.assertEqual(summary['presented_ms']['n'],1)
        self.assertEqual(summary['presented_ms']['max'],33)
        self.assertEqual(summary['late_over_25ms'],1)
    def test_high_average_cannot_hide_one_missed_presentation(self):
        good={'presented_per_s':60.4,'presented_ms':{'n':500,'p99':16.57,'max':16.58},
              'record_per_s':60.4,'record_ms':{'n':500,'p99':17,'max':18}}
        report={'protocol':'vinyl-next8-v1','summary':{'next':{'active_motion':good}}}
        self.assertTrue(stable_verdict(report)['ok'])
        good['presented_ms']['max']=33.125
        self.assertFalse(stable_verdict(report)['ok'])
    def test_other_widgets_cannot_hide_a_stalled_record(self):
        good={'presented_per_s':60.4,'presented_ms':{'n':500,'p99':16.57,'max':16.58},
              'record_per_s':60.4,'record_ms':{'n':500,'p99':17,'max':33}}
        report={'protocol':'vinyl-next8-v1','summary':{'next':{'active_motion':good}}}
        self.assertFalse(stable_verdict(report)['ok'])
    def test_v2_requires_actual_new_record_presentations_and_keeps_the_same_limits(self):
        good={'presented_per_s':60.4,'presented_ms':{'n':500,'p99':16.57,'max':16.58},
              'record_per_s':60.4,'record_ms':{'n':500,'p99':17,'max':18}}
        report={'protocol':'vinyl-next8-v2','summary':{'next':{'active_motion':good}}}
        self.assertFalse(stable_verdict(report)['ok']) # UI handoffs alone never prove v2.
        good.update(record_presented_per_s=60.4,record_presented_ms={'n':500,'p99':16.57,'max':16.58})
        self.assertTrue(stable_verdict(report)['ok'])
        good['record_presented_ms']['max']=33.12
        self.assertFalse(stable_verdict(report)['ok'])
        good['record_presented_ms']['max']=16.58;good['record_presented_per_s']=50
        self.assertFalse(stable_verdict(report)['ok'])
    def test_v2_retains_source_repeats_even_when_other_widgets_present_each_boundary(self):
        a=row('next',0,0,[]);b=row('next',.5,30,[16563]*30)
        for r in (a,b):r['status']['vinyl']={'speed':1,'interval_total':0,'interval_ms':[]}
        a['record_presentation']={'total':0,'us':[],'vsync_total':0,'presented_total':0}
        b['record_presentation']={'total':24,'us':[33000]+[16563]*23,'vsync_total':30,'presented_total':25}
        report=active_presentations([a,b])
        self.assertEqual(report['record_repeated'],5)
        self.assertEqual(report['record_presented_per_s'],50)
        self.assertEqual(report['record_presented_ms']['max'],33)
    def test_smooth_timing_cannot_hide_an_arm_worker_error(self):
        good={'presented_per_s':60.4,'presented_ms':{'n':500,'p99':16.57,'max':16.58},
              'record_per_s':60.4,'record_ms':{'n':500,'p99':17,'max':18}}
        report={'protocol':'vinyl-next8-v1','summary':{'next':{'active_motion':good}},
                'rows':[{'status':{'vinyl':{'arm_errors':0}}},{'status':{'vinyl':{'arm_errors':1}}}]}
        self.assertFalse(stable_verdict(report)['ok'])
    def test_one_sample_has_no_measurable_cadence(self):
        self.assertEqual(motion_summary([row('next',0,0,[])]),{})
