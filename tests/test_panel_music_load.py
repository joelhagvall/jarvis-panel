"""Mixed load cannot pass on smooth frame averages without actual metadata and cover work."""
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from panel_music_load import verdict,navigation


class MusicLoadTest(unittest.TestCase):
    def test_stopped_background_load_prevents_the_first_navigation_tap(self):
        class Client:
            def test_mode(self,*args):pass
            def status(self):return {'boot_id':1,'build_id':'b','uptime_ms':1000,'physical_touch_idle_ms':1000,
                'voice_active':False,'alarm_active':False,'announcement_active':False,'physical_touch_active':False,
                'test_mode':'music_load','wifi_connected':True,'music_load_active':False,'music_load_covers':0}
            def ui(self):return {'page':'page_glass_v2','widgets':[{'text':'Hemmet','x':0,'y':0,'width':50,'height':50}]}
            def tap(self,*args,**kwargs):raise AssertionError('Idle navigation must not proceed as loaded navigation')
        client=Client()
        with patch('panel_music_load.leave_music'),patch('panel_music_load.select_tab'):
            with self.assertRaisesRegex(RuntimeError,'Background load stopped before navigation'):
                navigation(client,client.status(),{},lambda:None)

    def report(self):
        timing = {'n': 1000, 'p99': 16.57, 'max': 16.58}
        active = {'presented_per_s': 60.4, 'presented_ms': dict(timing),
                  'record_presented_per_s': 60.4, 'record_presented_ms': dict(timing)}
        return {'summary': {'active_motion': active}, 'rows': [
            {'status': {'vinyl': {'generation': 2}, 'music_load_steps': 2, 'music_load_covers': 0}},
            {'status': {'vinyl': {'generation': 5}, 'music_load_steps': 32, 'music_load_covers': 6}}]}

    def test_moving_record_with_real_load_passes(self):
        self.assertTrue(verdict(self.report())['ok'])

    def test_no_cover_publication_and_missing_load_fail(self):
        for key, value in [('music_load_steps', 0), ('music_load_covers', 0)]:
            report = self.report()
            report['rows'][-1]['status'][key] = value
            self.assertFalse(verdict(report)['ok'])
        report = self.report()
        report['rows'][-1]['status']['vinyl']['generation'] = 2
        self.assertFalse(verdict(report)['ok'])

    def test_one_late_source_frame_fails_despite_high_average(self):
        report = self.report()
        report['summary']['active_motion']['record_presented_ms']['max'] = 33.12
        self.assertFalse(verdict(report)['ok'])

    def test_missing_load_or_one_slow_navigation_sample_fails(self):
        report=self.report()
        report['navigation_summary']={key:{'n':20,'p95':30,'max':40} for key in
            ('navigation.remote_press_ms','navigation.remote_release_ms')}
        report['navigation_cover_requests']=1
        self.assertTrue(verdict(report)['ok'])
        report['navigation_summary']['navigation.remote_release_ms']['max']=251
        self.assertFalse(verdict(report)['ok'])
        report['navigation_summary']['navigation.remote_release_ms']['max']=40
        report['navigation_cover_requests']=0
        self.assertFalse(verdict(report)['ok'])
