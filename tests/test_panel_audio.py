"""Real resampler spectrum/continuity and the generated shared-clock/voice contracts."""
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
from check_audio_resampler import build
from panel_audio_check import evaluate as check_audio_log


class MicrophoneResamplerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.binary = build(Path(cls.tmp.name)/'resampler')

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def samples(self, frequency, chunk=768):
        raw = subprocess.check_output([str(self.binary), str(frequency), str(chunk)])
        return np.frombuffer(raw, dtype='<i2')

    def test_passband_and_alias_rejection_keep_real_voice_bandwidth(self):
        for frequency in (100, 1000, 3000, 6000, 6500, 9000, 12000, 18000, 22000):
            with self.subTest(frequency=frequency):
                samples = self.samples(frequency)[400:-400].astype(float)
                rms = np.sqrt(np.mean(samples*samples))
                db = 20*math.log10(max(1, rms)/(10000/math.sqrt(2)))
                print('48k microphone test:', frequency, 'Hz ->', round(db, 3), 'dB',
                      '(below 16-bit measurement floor)' if rms == 0 else '')
                if frequency <= 6500:
                    self.assertLess(abs(db), .5)
                else:
                    self.assertLess(db, -55)

    def test_fragmentation_does_not_change_samples_or_drop_input(self):
        reference = self.samples(997)
        for chunk in (1, 7, 37, 511, 2048):
            with self.subTest(chunk=chunk):
                np.testing.assert_array_equal(self.samples(997, chunk), reference)


class SharedAudioConfigurationTest(unittest.TestCase):
    def test_music_clock_matches_both_codecs_while_voice_consumers_stay_16k(self):
        cfg=json.loads(subprocess.check_output([sys.executable, str(ROOT/'board.py'), '--voice',
            str(ROOT/'styles/glass-v2/jarvis_orb.py'), '--remote', 'http://example.test'], cwd=ROOT/'styles/glass-v2'))
        microphones={item['id']:item for item in cfg['microphone']}
        self.assertEqual(microphones['mics_raw']['sample_rate'],48000)
        self.assertEqual(microphones['mics']['sample_rate'],16000)
        self.assertEqual(microphones['mics']['microphone'],'mics_raw')
        self.assertEqual(cfg['audio_adc'][0]['sample_rate'],48000)
        self.assertEqual(cfg['audio_dac'][0]['sample_rate'],48000)
        speakers={item['id']:item for item in cfg['speaker']}
        self.assertEqual(speakers['speaker_out']['sample_rate'],48000)
        self.assertEqual(speakers['speaker_out']['i2s_mode'],'secondary')
        self.assertEqual(speakers['voice_spk']['platform'],'resampler')
        player=cfg['media_player'][0]
        self.assertEqual(player['media_pipeline']['sample_rate'],48000)
        self.assertEqual(player['media_pipeline']['format'],'FLAC')
        self.assertEqual(player['announcement_pipeline']['sample_rate'],16000)
        self.assertEqual(player['announcement_pipeline']['format'],'WAV')
        self.assertEqual(cfg['micro_wake_word']['microphone'],'mics')
        self.assertEqual(cfg['voice_assistant']['microphone'],'mics')

    def test_clock_gate_rejects_missing_wrong_rate_or_format_errors(self):
        good='Audio 48000->16000: elapsed_ms=10000 input=480000 output=160000 max_us=1200 clipped=0 errors=0\n'
        with tempfile.TemporaryDirectory() as tmp:
            log=Path(tmp)/'runtime.log'
            log.write_text(good*2)
            self.assertTrue(check_audio_log(log)['ok'])
            for bad in ('',good,good.replace('input=480000','input=160000')*2,
                        good.replace('output=160000','output=480000')*2,good.replace('errors=0','errors=1')*2,
                        good*2+'Microphone conversion stopped'):
                log.write_text(bad)
                with self.assertRaises(RuntimeError):check_audio_log(log)
