"""Unavailable home/calendar data must be explicit, with no real service or provider calls."""
from datetime import datetime
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock,patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'server'),str(ROOT/'server/agents')]
import hallpanel_server as hs


class PanelStatusTest(unittest.TestCase):
    def test_failed_home_read_does_not_leave_old_or_calm_status(self):
        home=NS(states=Mock(side_effect=OSError('HA restarting')),outside=lambda now:'',
                weather=lambda *a,**k:{},metro=lambda *a,**k:{},disruptions=lambda *a:[])
        watch=NS(home=home,calendar=None,net_ok=True,latest_states=lambda:None)
        with patch.object(hs,'WATCH',watch),patch.object(hs,'REMINDERS',None), \
             patch.object(hs.background,'art',return_value=('','')),patch.object(hs.background,'version',return_value='test'):
            result=hs.panel_status()
        self.assertEqual((result['status'],result['alert']),('Hemstatus kunde inte läsas.','on'))

    def test_stale_calendar_is_labelled_unavailable_instead_of_empty_or_current(self):
        watch=NS(home=None,calendar=True,net_ok=True,events=[],calendar_fresh=lambda now:False)
        with patch.object(hs,'WATCH',watch),patch.object(hs,'REMINDERS',None), \
             patch.object(hs.background,'art',return_value=('','')),patch.object(hs.background,'version',return_value='test'):
            result=hs.panel_status()
        self.assertEqual(result['cal0'],'Kalendern kan inte uppdateras||')
        self.assertEqual((result['cal1'],result['cal2']),('',''))
