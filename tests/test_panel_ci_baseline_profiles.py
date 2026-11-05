"""Explicit profile selection preserves legacy references and unchanged acceptance checks."""
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
import panel_ci as ci
import panel_metrics
import panel_visual
import panel_responsiveness
import panel_throughput


class BaselineProfilesTest(unittest.TestCase):
    def exercise(self,explicit=True,invalid=False,missing=False):
        with tempfile.TemporaryDirectory() as folder,ExitStack() as stack:
            state=Path(folder);(state/'baselines').mkdir()
            originals={}
            for name,workload in (('performance','idle'),('animation','animation')):
                path=state/'baselines'/f'{name}.json'
                path.write_text(json.dumps({'workload':workload,'profile':'legacy'}));originals[path]=path.read_bytes()
                if not missing:
                    (state/f'{name}-v5.json').write_text(json.dumps({'workload':'wrong' if invalid else workload,'profile':'v5'}))
            args=SimpleNamespace(visual_baseline=state,
                performance_baseline=state/'performance-v5.json' if explicit else None,
                animation_baseline=state/'animation-v5.json' if explicit else None)
            stack.enter_context(patch.object(ci,'STATE',state))
            stack.enter_context(patch.object(panel_visual,'load_capture',return_value={'scenes':{}}))
            evaluator=stack.enter_context(patch.object(panel_metrics,'evaluate',return_value={'ok':True}))
            stack.enter_context(patch.object(panel_responsiveness,'load_baseline',return_value={}))
            stack.enter_context(patch.object(panel_throughput,'load_baseline',return_value={}))
            evidence={}
            def stage(name,fn):evidence[name]=fn();return evidence[name]
            if invalid:
                with self.assertRaisesRegex(RuntimeError,'Invalid/incomplete'):ci.qa_configuration(args,stage)
            elif missing:
                with self.assertRaises(FileNotFoundError):ci.qa_configuration(args,stage)
            else:
                _,_,idle,animation,_,_=ci.qa_configuration(args,stage)
                self.assertEqual((idle['profile'],animation['profile']),('v5','v5') if explicit else ('legacy','legacy'))
                self.assertEqual(evaluator.call_count,2)
                for workload,selected in (('idle',args.performance_baseline or state/'baselines/performance.json'),
                                           ('animation',args.animation_baseline or state/'baselines/animation.json')):
                    self.assertEqual(evidence[workload+'-reference-preflight'],
                        {'path':str(selected.resolve()),'sha256':hashlib.sha256(selected.read_bytes()).hexdigest()})
            for path,raw in originals.items():self.assertEqual(path.read_bytes(),raw)

    def test_explicit_reviewed_paths_and_hashes_are_recorded(self):self.exercise()
    def test_default_legacy_paths_still_work(self):self.exercise(explicit=False)
    def test_explicit_missing_path_has_no_silent_fallback(self):self.exercise(missing=True)
    def test_workload_mismatch_is_still_rejected(self):self.exercise(invalid=True)

if __name__=='__main__':unittest.main()
