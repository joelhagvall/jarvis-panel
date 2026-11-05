"""Timestamp boundaries and bounded SR monitor; no network or audio."""
import asyncio
import importlib.util
from pathlib import Path
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

# This module has no HA imports until a real fetch is requested.
ROOT=Path(__file__).resolve().parents[1]
MODULE=ROOT/'ha/custom_components/jarvis_agent/radio_metadata.py'
spec=importlib.util.spec_from_file_location('radio_metadata_test',MODULE)
radio=importlib.util.module_from_spec(spec);spec.loader.exec_module(radio)


def date(seconds):return f'/Date({int(seconds*1000)})/'
def documents(now):
    return ({'channel':{'currentscheduledepisode':{'title':'P3 med','subtitle':'Juan Havana',
        'description':'Music programme','starttimeutc':date(now-60),'endtimeutc':date(now+600)}}},
        {'playlist':{'song':{'title':'Bara Mellan Oss','artist':'Oskar Linnros',
            'starttimeutc':date(now-10),'stoptimeutc':date(now+10)},
            'previoussong':{'title':'Old song'}}})


class RadioDataTest(unittest.TestCase):
    def test_song_then_programme_then_channel_fallback_at_exact_expiry(self):
        programme,playlist=documents(1000)
        self.assertEqual(radio.current(programme,playlist,1000)['title'],'Bara Mellan Oss')
        self.assertEqual(radio.current(programme,playlist,1010)['title'],'P3 med Juan Havana')
        self.assertEqual(radio.current(programme,playlist,1600),{})
        self.assertEqual(radio.current(programme,playlist,900),{})

    def test_missing_song_or_dates_never_promote_previous_song(self):
        programme,playlist=documents(1000)
        del playlist['playlist']['song']['stoptimeutc']
        self.assertEqual(radio.current(programme,playlist,1000)['title'],'P3 med Juan Havana')
        self.assertEqual(radio.current({},playlist,1000),{})

    def test_full_programme_heading_and_song_artist_remain_distinct(self):
        programme,playlist=documents(1000)
        info=radio.current(programme,playlist,1000)
        self.assertEqual(info['programme'],'P3 med Juan Havana')
        self.assertEqual(info['artist'],'Oskar Linnros')
        programme['channel']['currentscheduledepisode']['title']='P3 med Juan Havana'
        self.assertEqual(radio.current(programme,{},1000)['title'],'P3 med Juan Havana')


class RadioMonitorTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.updated=Mock()
        self.monitor=radio.RadioMetadata(NS(async_create_task=lambda coro,name:asyncio.create_task(coro)),self.updated)
        self.addCleanup(self.monitor.close)

    async def test_updates_are_async_and_pause_stop_cancel_polling(self):
        now=time.time();programme,playlist=documents(now)
        async def get(method,channel):return programme if method=='scheduledepisodes' else playlist
        self.monitor._get=get
        self.monitor.select(164,True)
        self.assertEqual(self.monitor.read(),{})
        for _ in range(5):await asyncio.sleep(0)
        self.assertEqual(self.monitor.read()['artist'],'Oskar Linnros')
        task=self.monitor.task;self.monitor.select(164,False);await asyncio.sleep(0)
        self.assertTrue(task.cancelled());self.assertEqual(self.monitor.read()['title'],'Bara Mellan Oss')
        self.monitor.select(None,False);self.assertEqual(self.monitor.read(),{})

    def test_stale_documents_expire_even_when_the_feed_fails(self):
        self.monitor.request=(164,True)
        programme,playlist=documents(1000)
        self.monitor.documents={'programme':(programme,1000),'playlist':(playlist,1000)}
        with patch.object(radio.time,'time',return_value=1000):self.assertEqual(self.monitor.read()['title'],'Bara Mellan Oss')
        with patch.object(radio.time,'time',return_value=1091):self.assertEqual(self.monitor.read(),{})
