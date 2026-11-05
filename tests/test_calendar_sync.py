"""Fast sync, bounded failures and real change/delete semantics, without iCloud or HA."""
import copy
from datetime import datetime, timedelta
import io
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'server'))
sys.path.insert(0,str(ROOT/'server/agents'))
import calendar_sync as sync
import calendar_palette as palette
from watch import calendar_events

NOW=datetime(2026,10,24,12,tzinfo=sync.TZ)
EVENT_A='2026-10-24 15:00-16:00 | Annat | Meeting | id A'
EVENT_B='2026-10-24 16:00-17:00 | Jobb | Work | id B'
META={'https://p42-caldav.icloud.com/a/':{'name':'Annat','token':'a1','colour':'#8A928E'},
      'https://p42-caldav.icloud.com/b/':{'name':'Jobb','token':'b1','colour':'#FFCC00'}}


class Reader:
    def __init__(self):self.calls=[];self.answers={None:EVENT_A+'\n'+EVENT_B,'Annat':EVENT_A,'Jobb':EVENT_B}
    def start(self):pass
    def run(self,name,args,timeout):
        self.calls.append(args.copy());value=self.answers[args.get('calendar')]
        if isinstance(value,Exception):raise value
        return False,value


class Meta:
    def __init__(self):self.value=copy.deepcopy(META);self.calls=0
    def poll(self):
        self.calls+=1
        if isinstance(self.value,Exception):raise self.value
        return copy.deepcopy(self.value)


class Colours:
    def __init__(self):self.value={}
    def update(self,value):
        changed=value!=self.value;self.value=value;return changed


class FeedTest(unittest.TestCase):
    def setUp(self):
        self.reader,self.meta,self.colours,self.published=Reader(),Meta(),Colours(),[]
        self.feed=sync.CalendarFeed(self.reader,calendar_events,lambda e:self.published.append(e),
                                    self.meta,self.colours)
    def step(self,at,now=NOW):self.feed.step(at,now)

    def test_unchanged_metadata_does_not_refetch_agenda_or_push(self):
        self.step(0);self.step(10);self.step(20)
        self.assertEqual(self.reader.calls,[{'days':2}]);self.assertEqual(len(self.published),1)
        self.assertEqual(self.meta.calls,3)

    def test_changed_calendar_only_updates_its_own_events(self):
        self.step(0)
        self.meta.value[next(iter(META))]['token']='a2'
        self.reader.answers['Annat']=EVENT_A.replace('Meeting','New title')
        self.step(10)
        self.assertEqual(self.reader.calls[-1],{'days':2,'calendar':'Annat'})
        self.assertEqual([e[2] for e in self.published[-1]],['New title','Work'])

    def test_an_edit_outside_the_agenda_moves_token_changes_not_the_revision(self):
        # An edited alarm or note: the calendar page re-reads the tap's details (calendar_week.py, 2026-11-04).
        self.step(0);before=self.feed.status()
        self.meta.value[next(iter(META))]['token']='a2'
        self.step(10);after=self.feed.status()
        self.assertEqual(after['revision'],before['revision'])
        self.assertEqual(after['token_changes'],before['token_changes']+1)
        self.step(20);self.assertEqual(self.feed.status()['token_changes'],after['token_changes'])

    def test_deleted_event_and_deleted_collection_are_removed(self):
        self.step(0)
        self.meta.value[next(iter(META))]['token']='a2'
        self.reader.answers['Annat']='Nothing in that period.'
        self.step(10);self.assertEqual([e[3] for e in self.feed.events],['B'])
        self.meta.value={};self.step(20);self.assertEqual(self.feed.events,[])

    def test_a_calendar_with_only_all_day_events_is_a_good_read(self):
        # The real agenda writes all-day events without "heldag" (seen on Jarvis 2026-10-30).
        self.step(0)
        for line in ('2026-10-24 | Annat | Namnsdag | id D | repeats, occurrence 2026-10-24',
                     '2026-10-24 heldag | Annat | Namnsdag | id D'):
            self.meta.value[next(iter(META))]['token']+='x'
            self.reader.answers['Annat']=line
            self.step(len(self.reader.calls)*10)
            self.assertEqual([e[3] for e in self.feed.events],['B'])
            self.assertIsNone(self.feed.status()['last_error'])

    def test_failed_or_unparseable_reads_preserve_events_and_change_tokens(self):
        for failure in (OSError('timeout'),'FAILED: refusal','REFUSED: bad','unexpected text',
                        EVENT_A+'\n... and 4 more; ask for a shorter period or one calendar.'):
            self.setUp();self.step(0)
            self.meta.value[next(iter(META))]['token']='a2'
            self.reader.answers['Annat']=failure
            self.step(10)
            self.assertEqual(self.feed.previous,META)
            self.assertEqual([e[3] for e in self.feed.events],['A','B'])
            self.reader.answers['Annat']=EVENT_A.replace('Meeting','Recovered')
            self.step(20);self.assertEqual(self.feed.events[0][2],'Recovered')

    def test_metadata_failure_uses_full_fallback_and_does_not_clear_events(self):
        self.step(0);self.meta.value=OSError('down')
        self.step(10);self.step(20)
        self.assertEqual(len(self.feed.events),2)
        self.step(301);self.assertEqual(self.reader.calls,[{'days':2},{'days':2}])

    def test_midnight_forces_refresh_even_if_tokens_are_unchanged(self):
        self.step(0);self.step(10,NOW+timedelta(days=1))
        self.assertEqual(len(self.reader.calls),2)

    def test_missing_tokens_use_scoped_30_second_fallback(self):
        self.meta.value[next(iter(META))]['token']=None
        self.step(0);self.step(10);self.step(20);self.step(30);self.step(40)
        self.assertEqual([c.get('calendar') for c in self.reader.calls],[None,'Annat'])

    def test_push_failure_is_retried_without_reverting_new_events(self):
        def publish(events):
            self.published.append(events)
            if len(self.published)==1:raise OSError('HA unavailable')
        self.feed.publish=publish;self.step(0)
        self.assertTrue(self.feed.pending);self.step(10)
        self.assertFalse(self.feed.pending);self.assertEqual(self.feed.status()['pushes'],1)

    def test_repeated_push_failure_backs_off_instead_of_hammering_ha(self):
        calls=[]
        def unavailable(events):calls.append(1);raise OSError('offline')
        self.feed.publish=unavailable
        for at in (0,10,20,30):self.step(at)
        self.assertEqual(len(calls),3)
        self.assertEqual(self.feed.retry_at,70)

    def test_colour_only_change_pushes_and_status_contains_no_private_content(self):
        self.step(0);self.meta.value[next(iter(META))]['colour']='#123456';self.step(10)
        self.assertEqual(len(self.published),2);status=json.dumps(self.feed.status())
        for value in ('Meeting','Work','icloud.com','a1','a2'):self.assertNotIn(value,status)


def response(properties,href='/calendar/'):
    root=ET.Element('{DAV:}multistatus');r=ET.SubElement(root,'{DAV:}response')
    ET.SubElement(r,'{DAV:}href').text=href
    stat=ET.SubElement(r,'{DAV:}propstat');ET.SubElement(stat,'{DAV:}status').text='HTTP/1.1 200 OK'
    prop=ET.SubElement(stat,'{DAV:}prop')
    for tag,value in properties.items():
        e=ET.SubElement(prop,tag)
        if isinstance(value,tuple):ET.SubElement(e,value[0])
        else:e.text=value
    return root


class MetadataTest(unittest.TestCase):
    def test_refused_credentials_back_off_and_changed_file_retries_immediately(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'credentials.json';path.write_text('{}')
            calls=[]
            def request(*a,**kw):
                calls.append(1);raise HTTPError('https://caldav.icloud.com/',401,'refused',{},io.BytesIO())
            m=sync.Metadata(path,request)
            with patch.object(palette,'discover',return_value=('https://caldav.icloud.com/home/','private')):
                for _ in range(2):
                    with self.assertRaises((HTTPError,PermissionError)):m.poll()
                self.assertEqual(len(calls),1)
                path.write_text('{"changed":true}')
                with self.assertRaises(HTTPError):m.poll()
                self.assertEqual(len(calls),2)

    def test_transient_auth_refusal_recovers_and_repeated_refusals_back_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'credentials.json';path.write_text('{}')
            root=response({'{DAV:}displayname':'Annat','{DAV:}resourcetype':(f'{{{palette.CAL}}}calendar',)})
            denied=HTTPError('https://caldav.icloud.com/',401,'refused',{},io.BytesIO())
            request=unittest.mock.Mock(side_effect=[denied,denied,root,denied])
            m=sync.Metadata(path,request)
            with patch.object(palette,'discover',return_value=('https://caldav.icloud.com/home/','private')) as discover, \
                    patch.object(sync.time,'monotonic',return_value=0) as clock:
                with self.assertRaises(HTTPError):m.poll()
                clock.return_value=299
                with self.assertRaises(PermissionError):m.poll()
                self.assertEqual(request.call_count,1)
                clock.return_value=300
                with self.assertRaises(HTTPError):m.poll()
                clock.return_value=899
                with self.assertRaises(PermissionError):m.poll()
                clock.return_value=900
                self.assertTrue(m.poll());self.assertFalse(m.refused)
                self.assertIsNone(m.denied);self.assertEqual(discover.call_count,3)
                clock.return_value=4500
                with self.assertRaises(HTTPError):m.poll()
                self.assertEqual(m.denied_until,4800)

    def test_reads_single_collection_metadata_request_and_uses_ctag_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'credentials.json';path.write_text('{}');calls=[]
            root=response({'{DAV:}displayname':'Annat','{DAV:}resourcetype':(f'{{{palette.CAL}}}calendar',),
                sync.CTAG:'tag1',f'{{{palette.APPLE}}}calendar-color':'#8A928EFF'})
            def request(*a,**kw):calls.append(kw);return root
            m=sync.Metadata(path,request)
            with patch.object(palette,'discover',return_value=('https://caldav.icloud.com/home/','private')) as discover:
                first=m.poll();m.poll();self.assertEqual(discover.call_count,1)
            self.assertEqual(next(iter(first.values())),{'name':'Annat','token':'tag1','colour':'#8A928E'})
            self.assertEqual(calls,[{'depth':1},{'depth':1}])


class ProbeTest(unittest.TestCase):
    def test_probe_owns_only_its_event_and_cleanup_is_idempotent(self):
        spec=importlib.util.spec_from_file_location('calendar_probe_remote',ROOT/'server/check_calendar_sync.py')
        probe=importlib.util.module_from_spec(spec);spec.loader.exec_module(probe)
        nonce='0123456789abcdef';store={};calls=[]
        class Client:
            def start(self):pass
            def run(self,name,args,timeout):
                calls.append(name)
                if name=='add_event':
                    store['title']=args['title']
                    return False,'added to Att göra: 2026-10-24 14:00-14:05 '+store['title']+' (id owned; change 20261024-140000-abcd)'
                elif name=='edit_event':store['title']=args['title']
                elif name=='delete_event':store.clear();return False,'Deleted'
                return False,('2026-10-24 14:00-14:05 | Att göra | '+store['title']+' | id owned') if store else 'No event matches.'
        with tempfile.TemporaryDirectory() as tmp, patch.object(Path,'home',return_value=Path(tmp)), \
                patch.object(probe,'settings',return_value={'JARVIS_CALENDAR_MCP':'private','JARVIS_CALENDAR_MCP_TOKEN_FILE':'private'}), \
                patch.object(probe,'mcp_client',return_value=Client()):
            for action in ('create','edit','cleanup','cleanup'):
                self.assertTrue(probe.run({'action':action,'nonce':nonce})['ok'])
            self.assertFalse(store)
            self.assertEqual(calls.count('add_event'),1);self.assertEqual(calls.count('delete_event'),1)
            with self.assertRaises(ValueError):probe.run({'action':'cleanup','nonce':'../../secret'})


if __name__=='__main__':unittest.main()
