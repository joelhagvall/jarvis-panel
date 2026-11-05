"""External-video annotation uncertainty and missed/ambiguous taps must remain explicit."""
import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from panel_optical import analyse

def sample(name='1',**more):
    return dict(sample=name,condition='idle',action='tab',status='complete',contact_before='9',contact_after='10',
                response_before='13',response_after='14',observation_end='',**more)

class OpticalTest(unittest.TestCase):
    def test_brackets_give_an_interval_not_false_subframe_precision(self):
        r=analyse([sample()],240);s=r['samples'][0]
        self.assertAlmostEqual(s['lower_ms'],12.5);self.assertAlmostEqual(s['upper_ms'],20.8333333333)
        self.assertAlmostEqual(s['midpoint_ms'],16.6666666667)

    def test_slow_negative_and_unanswered_samples_are_retained(self):
        slow=sample('slow');slow.update(response_before='249',response_after='250')
        negative=sample('early');negative.update(response_before='6',response_after='7')
        missing=sample('missing');missing.update(status='no_response',response_before='',response_after='',observation_end='490')
        r=analyse([slow,negative,missing],240)
        self.assertEqual(len(r['samples']),3)
        self.assertLess(r['samples'][1]['upper_ms'],0)
        self.assertEqual(r['summary'][0]['no_response'],1)
        self.assertEqual(r['summary'][0]['needs_review'],2)
        self.assertGreater(r['summary'][0]['completed_only_midpoint_ms']['max'],900)
        self.assertAlmostEqual(r['samples'][2]['lower_bound_ms'],2000)

    def test_no_data_bad_rate_duplicate_or_reversed_brackets_fail(self):
        for rows,fps in [([],240),([sample()],0),([sample(),sample()],240)]:
            with self.assertRaises(ValueError):analyse(rows,fps)
        row=sample();row['response_before']='15'
        with self.assertRaises(ValueError):analyse([row],240)
