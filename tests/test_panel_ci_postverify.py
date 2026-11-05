"""Fail-closed continuation after a measured user voice interruption, never another OTA."""
from contextlib import ExitStack, nullcontext, contextmanager
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import panel_ci as ci
import panel_lock
import panel_remote
import panel_recovery
import panel_responsiveness


class InstalledVerificationTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.state=Path(self.tmp.name)/'.panel-ci';self.run=self.state/'run-interrupted';self.run.mkdir(parents=True)
        self.before={'build_id':'a'*32,'boot_id':7,'firmware_elf_sha256':'123456789','page':'page_hemmet',
            'uptime_ms':70000,'reset_reason':3,'boot_healthy':True,'test_mode':'idle',
            'internal_free':250000,'psram_free':19000000,'wifi_connected':True,
            'voice_active':False,'alarm_active':False,'announcement_active':False,'physical_touch_active':False}
        self.candidate={'head':'head','branch':'main','build_id':'a'*32,
                        'files':{'board.py':{'sha256':'runtime'},'tools/panel_ci.py':{'sha256':'old-harness'}}}
        self.current=copy.deepcopy(self.candidate);self.current['files']['tools/panel_ci.py']['sha256']='new-harness'
        self.report={'ok':False,'mode':'physical','build_id':'a'*32,
            'firmware_sha256':hashlib.sha256(b'firmware').hexdigest(),
            'stages':[{'name':name,'ok':True,'result':{'status':self.before} if name=='boot-and-identity' else {}}
                      for name in ('boot-and-identity','boot-log-review','physical-integration',
                                   'synthetic-voice-and-faults','visual-regression')]+
                     [{'name':'performance-capture','ok':False}]}
        self.quarantine={'build_id':'a'*32,'report':str(self.run/'report.json')}
        self.samples={'workload':'idle','samples':[dict(self.before),{**self.before,'voice_active':True,'page':'page_orb'}]}
        self.write('candidate.json',self.candidate);self.write('report.json',self.report)
        self.write('metrics/metrics.json',self.samples)
        (self.run/'firmware.ota.bin').write_bytes(b'firmware')
        (self.run/'boot.log').write_text('Successfully connected\nUpdate complete\nRebooting safely\nSuccessfully connected\n')
        (self.run/'runtime.log').write_text('Successfully connected\n')
        (self.state/'quarantine.json').write_text(json.dumps(self.quarantine))

    def write(self,name,data):
        p=self.run/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(data))

    def proof(self):return ci.interrupted_install(self.run,self.current,self.quarantine)

    def test_only_verification_harness_changes_are_allowed(self):
        self.assertEqual(self.proof()['non_firmware_changes'],['tools/panel_ci.py'])
        host=copy.deepcopy(self.current);host['files']['server/transcription.py']={'sha256':'host-only'}
        self.assertIn('server/transcription.py',ci.interrupted_install(self.run,host,self.quarantine)['non_firmware_changes'])
        for path in ('board.py','server/hallpanel_server.py','tests/performance/policy.json','tools/panel_metrics.py'):
            with self.subTest(path=path):
                current=copy.deepcopy(self.current);current['files'][path]={'sha256':'changed'}
                with self.assertRaisesRegex(RuntimeError,'inputs changed'):
                    ci.interrupted_install(self.run,current,self.quarantine)

    def test_voice_evidence_and_same_boot_are_required(self):
        for change in ({'voice_active':False},{'boot_id':8},{'build_id':'different'}):
            samples=copy.deepcopy(self.samples);samples['samples'][1].update(change)
            self.write('metrics/metrics.json',samples)
            with self.assertRaisesRegex(RuntimeError,'interruption evidence'):self.proof()

    def test_prior_failure_or_altered_artifact_is_never_resumed(self):
        self.report['stages'][1]['ok']=False;self.write('report.json',self.report)
        with self.assertRaisesRegex(RuntimeError,'Earlier verification'):self.proof()
        self.report['stages'][1]['ok']=True;self.write('report.json',self.report)
        (self.run/'firmware.ota.bin').write_bytes(b'other image')
        with self.assertRaisesRegex(RuntimeError,'hash mismatch'):self.proof()

    def test_crash_log_or_another_quarantine_cannot_be_cleared(self):
        self.quarantine['build_id']='another'
        with self.assertRaisesRegex(RuntimeError,'another installation'):self.proof()
        self.quarantine['build_id']='a'*32
        (self.run/'runtime.log').write_text('Successfully connected\nCORRUPT HEAP\n')
        with self.assertRaisesRegex(RuntimeError,'Crash/bootloop'):self.proof()

    def execute(self,fail=False,changed_quarantine=False,different_boot=False,changed_artifact=False,transport=None):
        events=[]
        status={**self.before,'uptime_ms':100000,'boot_id':8 if different_boot else 7}
        class Lock:
            def __init__(self,*a,**kw):pass
            def __enter__(self):return self
            def __exit__(self,*a):pass
            def subprocess_options(self):return {}
        class Client(Lock):
            def status(self):return status
            def exclusive(self,**kw):return nullcontext(self)
        def command(argv,*a,**kw):
            events.append('checks')
            self.assertNotIn('upload',map(str,argv));self.assertNotIn('compile',map(str,argv))
        def qa(client,folder,before,stage,*a):
            events.append('physical-qa')
            if fail:raise RuntimeError('physical QA failed')
            stage('shared-physical-qa',lambda:{'ok':True})
            if changed_quarantine:(self.state/'quarantine.json').write_text('{"new":"incident"}')
            if changed_artifact:(self.run/'firmware.ota.bin').write_bytes(b'changed during QA')
        replacements=[(ci,'STATE',self.state),(ci,'inventory',lambda:self.current),(ci,'unchanged',lambda *_:None),
            (ci,'static_checks',lambda *_:{'ok':True}),(ci,'command',command),
            (ci,'qa_configuration',lambda *_:()),(ci,'physical_qa',qa),
            (panel_lock,'PanelLock',Lock),(panel_remote,'PanelClient',Client)]
        with ExitStack() as stack:
            for obj,name,value in replacements:stack.enter_context(patch.object(obj,name,value))
            result=ci.verify_installed(NS(verify_installed=self.run,url='http://fake',device='fake',transport_diagnosis=transport))
        return result,events

    def test_success_repeats_qa_without_build_upload_and_keeps_original_report(self):
        original=(self.run/'report.json').read_bytes()
        result,events=self.execute()
        self.assertEqual(result,0);self.assertEqual(events,['checks','physical-qa'])
        self.assertFalse((self.state/'quarantine.json').exists())
        self.assertEqual((self.run/'report.json').read_bytes(),original)
        self.assertEqual((self.state/'verified'/('a'*32)/'firmware.ota.bin').read_bytes(),b'firmware')

    def test_failure_retains_quarantine(self):
        result,_=self.execute(fail=True);self.assertEqual(result,1)
        self.assertTrue((self.state/'quarantine.json').exists())
        self.assertFalse((self.state/'verified').exists())

    def test_changed_quarantine_is_never_overwritten(self):
        result,_=self.execute(changed_quarantine=True);self.assertEqual(result,1)
        self.assertEqual(json.loads((self.state/'quarantine.json').read_text()),{'new':'incident'})

    def test_new_boot_stops_before_interaction(self):
        result,events=self.execute(different_boot=True);self.assertEqual(result,1)
        self.assertEqual(events,['checks']);self.assertTrue((self.state/'quarantine.json').exists())

    def test_changed_artifact_during_qa_is_never_archived_or_cleared(self):
        result,_=self.execute(changed_artifact=True);self.assertEqual(result,1)
        self.assertTrue((self.state/'quarantine.json').exists())
        self.assertFalse((self.state/'verified').exists())

    @contextmanager
    def transport_fixture(self):
        verdict={'ok':False,'failures':['status delay'],'baseline_checked':True,
                 'comparisons':[{'metric':'status_roundtrip_ms','ok':False}]}
        passing={**verdict,'ok':True,'failures':[],
                 'comparisons':[{'metric':'status_roundtrip_ms','ok':True}]}
        self.candidate['files']['docs/diagnosis.md']={'sha256':'original-diagnosis'}
        self.current=copy.deepcopy(self.candidate)
        self.write('candidate.json',self.candidate)
        prior=self.state/'prior-installed.json';prior.write_text('{}')
        (self.run/'firmware.elf').write_bytes(b'elf')
        plan=self.state/'plan.json'
        data={'run':str(self.run),'candidate_sha256':panel_recovery.sha(self.run/'candidate.json'),
              'diagnosis':'docs/diagnosis.md','diagnosis_sha256':'original-diagnosis',
              'firmware_sha256':panel_recovery.sha(self.run/'firmware.ota.bin'),
              'elf_sha256':panel_recovery.sha(self.run/'firmware.elf'),
              'installed_guard':{'report':str(prior),'report_sha256':panel_recovery.sha(prior)},
              'max_attempts':1,'quarantine_sha256':panel_recovery.sha(self.state/'quarantine.json')}
        plan.write_text(json.dumps(data))
        panel_recovery.sidecar(plan,'.approval.json').write_text(json.dumps({
            'plan_sha256':panel_recovery.sha(plan),'standing_authorization':panel_recovery.STANDING_AUTHORIZATION}))
        claim={'attempt':1,'plan_sha256':panel_recovery.sha(plan),'report':str(self.run/'report.json')}
        claim_path=panel_recovery.sidecar(plan,'.attempts')/'attempt-01.json'
        claim_path.parent.mkdir();claim_path.write_text(json.dumps(claim))
        self.report.update(error='Responsiveness regression; baseline preserved',recovery=claim)
        self.report['stages']=self.report['stages'][:-1]
        self.report['stages'] += [{'name':n,'ok':True,'result':{}} for n in
            ('restart-linkage','ota','performance-regression','animation-regression','responsiveness-capture')]
        self.report['stages'] += [{'name':'recovery-authorization','ok':True,'result':{
            'plan':str(plan),'plan_sha256':panel_recovery.sha(plan)}},
            {'name':'responsiveness-regression','ok':True,'result':verdict}]
        self.write('report.json',self.report)
        sample={'complete':True,'restored':True,'records':[{'status':{**self.before,'boot_id':'7'}}],
                'fixture_verdict':verdict}
        self.write('responsiveness/responsiveness.json',sample)
        diagnosis=self.state/'transport-ab'
        for label in ('without-logs','with-logs'):
            p=diagnosis/label/'responsiveness.json';p.parent.mkdir(parents=True)
            p.write_text(json.dumps({**sample,'fixture_verdict':passing}))
        with patch.object(ci,'STATE',self.state), patch.object(panel_responsiveness,'load_baseline',lambda *_:{}), \
             patch.object(panel_responsiveness,'evaluate',lambda raw,_:raw['fixture_verdict']):
            yield diagnosis

    def test_transport_proof_accepts_sealed_same_boot_fixed_ab_and_preserves_failed_run(self):
        with self.transport_fixture() as diagnosis:
            before=(self.run/'report.json').read_bytes()
            proof=ci.interrupted_install(self.run,self.current,self.quarantine,diagnosis)
            self.assertEqual(proof['boot']['boot_id'],7)
            self.assertEqual(len(proof['external_evidence']),9)
            self.assertEqual((self.run/'report.json').read_bytes(),before)
            self.assertTrue((self.state/'quarantine.json').exists())

    def test_transport_proof_rejects_non_host_regression_and_wrong_plan_binding(self):
        with self.transport_fixture() as diagnosis:
            verdict=self.report['stages'][-1]['result']
            verdict['comparisons'].append({'metric':'animation.jank_percent','ok':False})
            self.write('report.json',self.report)
            with self.assertRaisesRegex(RuntimeError,'isolated host'):ci.interrupted_install(self.run,self.current,self.quarantine,diagnosis)
            verdict['comparisons'].pop()
            self.report['recovery']['plan_sha256']='different'
            self.write('report.json',self.report)
            with self.assertRaisesRegex(RuntimeError,'sealed single'):ci.interrupted_install(self.run,self.current,self.quarantine,diagnosis)

    def test_transport_proof_rejects_failed_ab_different_boot_or_unrestored_case(self):
        for mutation in ('failed','boot','restore'):
            with self.subTest(mutation=mutation), self.transport_fixture() as diagnosis:
                p=diagnosis/'with-logs/responsiveness.json';sample=json.loads(p.read_text())
                if mutation=='failed':sample['fixture_verdict']['ok']=False
                elif mutation=='boot':sample['records'][0]['status']['boot_id']=8
                else:sample['restored']=False
                p.write_text(json.dumps(sample))
                with self.assertRaises(RuntimeError):ci.interrupted_install(self.run,self.current,self.quarantine,diagnosis)
            # Each case gets fresh exclusive fixture files.
            import shutil
            shutil.rmtree(panel_recovery.sidecar(self.state/'plan.json','.attempts'))
            shutil.rmtree(diagnosis)

    def test_transport_continuation_is_consumed_after_failure_without_ota_or_retry(self):
        with self.transport_fixture() as diagnosis:
            result,events=self.execute(fail=True,transport=diagnosis)
            self.assertEqual(result,1);self.assertEqual(events,['checks','physical-qa'])
            self.assertTrue((self.run/'transport-continuation.json').exists())
            result,events=self.execute(transport=diagnosis)
            self.assertEqual(result,1);self.assertEqual(events,['checks'])
            self.assertTrue((self.state/'quarantine.json').exists())


if __name__=='__main__':unittest.main()
