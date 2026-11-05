"""tools/panel_crash.py: which crash reports in an install's log capture belong to the candidate."""
import io
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import panel_crash

# The old firmware's first boot after the candidate aborted on 2026-10-28, as esphome logs printed it.
CRASH = '''07:05:54 [07:05:54.461][E][esp32.crash:436]: *** CRASH DETECTED ON PREVIOUS BOOT ***
07:05:54 [07:05:54.522][E][esp32.crash:441]:   Reason: Abort
07:05:54 [07:05:54.523][E][esp32.crash:443]:   Crashed core: 1
07:05:54 [07:05:54.523][E][esp32.crash:410]:   Captured by a different firmware build; addresses belong to that build's ELF
07:05:54 [07:05:54.523][E][esp32.crash:411]:   pc: 0x4FF2AE00
07:05:54 [07:05:54.523][E][esp32.crash:404]:   bt0: 0x4FF2AE00
07:05:54 [07:05:54.523][E][esp32.crash:404]:   bt1: 0x4FF2A9DE
07:05:54 [07:05:54.523][I][app:151]: ESPHome version 2026.9.1 compiled on 2026-10-27 15:12:33 +0100
'''
ROLLBACK = ('07:05:54 [07:05:54.957][W][safe_mode:094]: OTA rollback detected! Rolled back from partition \'app0\'\n')
QUIET = '07:06:06 [07:06:06.640][I][panel_audio:066]: Audio 48000->16000: elapsed_ms=10000\n'
GONE = '07:07:26 ' + panel_crash.MARK + '\n'


class CrashScanTest(unittest.TestCase):
    def scan(self, text, elf=None, addr2line=None):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'flash.log'
            path.write_text(text)
            out = io.StringIO()
            code = panel_crash.scan(path, elf or Path(tmp) / 'missing.elf', out, addr2line)
            return code, out.getvalue()

    def test_the_parsed_report_has_reason_core_pc_and_backtrace(self):
        crash = panel_crash.reports(CRASH)[0]['crashes'][0]
        self.assertEqual(crash, {'reason': 'Abort', 'core': '1', 'pc': '0x4FF2AE00', 'bt': ['0x4FF2AE00', '0x4FF2A9DE']})

    def test_a_clean_install_passes(self):
        self.assertEqual(self.scan(QUIET + GONE + QUIET)[0], 0)
    def test_esphome_internal_reconnect_separates_the_candidates_crash_without_wrapper_restart(self):
        connected='INFO Successfully connected to hallpanel @ 192.0.2.1\n'
        code,out=self.scan(connected+QUIET+'INFO Processing unexpected disconnect\n'+connected+CRASH)
        self.assertEqual(code,1)
        self.assertIn('CANDIDATE CRASHED: Abort on core 1, pc 0x4FF2AE00',out)
        self.assertEqual(self.scan(connected+CRASH+connected+CRASH)[0],0)

    def test_a_crash_from_before_the_candidate_is_only_noted(self):
        code, out = self.scan(CRASH + QUIET)
        self.assertEqual(code, 0)
        self.assertIn('boot before the candidate', out)

    def test_the_same_report_again_after_a_wifi_reconnect_is_not_new(self):
        self.assertEqual(self.scan(CRASH + GONE + CRASH)[0], 0)

    def test_the_candidates_crash_and_rollback_fail_and_keep_the_elf(self):
        with tempfile.TemporaryDirectory() as tmp:
            elf = Path(tmp) / 'firmware.elf'
            elf.write_bytes(b'elf')
            tool = Path(tmp) / 'addr2line'
            tool.write_text('#!/bin/sh\necho "0x4ff2ae00: panic_abort at panic.c:491"\n')
            tool.chmod(0o755)
            code, out = self.scan(QUIET + GONE + CRASH + ROLLBACK, elf, str(tool))
        self.assertEqual(code, 1)
        self.assertIn('CANDIDATE CRASHED: Abort on core 1, pc 0x4FF2AE00', out)
        self.assertIn('panic_abort at panic.c:491', out)
        self.assertIn('CANDIDATE ROLLED BACK', out)
        self.assertIn('The candidate ELF is kept at', out)

    def test_the_same_crash_again_after_a_panic_reboot_is_the_candidates(self):
        # 2026-10-28 10:32: the candidate's first boot printed the stale report of an earlier identical crash,
        # then crashed the same way; only the reboot's own reason told them apart.
        panic = "10:32:55 [10:32:55.0][S][text_sensor]: 'Senaste omstart' >> 'exception/panic'\n"
        ota = "10:32:19 [10:32:19.2][S][text_sensor]: 'Senaste omstart' >> 'Reboot request from esphome.ota'\n"
        self.assertEqual(self.scan(CRASH + ota + GONE + CRASH + ota)[0], 0)      # a normal restart: old report
        code, out = self.scan(CRASH + ota + GONE + CRASH + panic)
        self.assertEqual(code, 1)
        self.assertIn("CANDIDATE CRASHED: Abort", out)

    def test_a_panel_on_another_build_afterwards_is_a_rollback_even_without_its_reboot_in_the_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'flash.log'
            path.write_text(CRASH + QUIET + GONE + CRASH)       # the stale report twice, the reboot unseen
            out = io.StringIO()
            code = panel_crash.scan(path, Path(tmp) / 'none.elf', out, build='new', running=('old', '4'))
        self.assertEqual(code, 1)
        self.assertIn('CANDIDATE ROLLED BACK: the panel runs old, not the candidate new (reset reason 4)', out.getvalue())
        self.assertIn('CANDIDATE CRASHED: Abort', out.getvalue())
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'flash.log'
            path.write_text(CRASH + QUIET)
            self.assertEqual(panel_crash.scan(path, Path(tmp) / 'none.elf', io.StringIO(), build='new',
                                              running=('new', '3')), 0)

    def test_addresses_outside_every_function_are_marked_as_noise(self):
        with tempfile.TemporaryDirectory() as tmp:
            elf = Path(tmp) / 'firmware.elf'
            elf.write_bytes(b'elf')
            tool = Path(tmp) / 'addr2line'
            tool.write_text('#!/bin/sh\necho "0x4ff2ae00: panic_abort at panic.c:491"\necho "0x4ff39008: ?? ??:0"\n')
            tool.chmod(0o755)
            text = panel_crash.decode({'pc': '0x4FF2AE00', 'bt': ['0x4FF39008']}, elf, str(tool),
                                      ranges=[(0x4FF2AD00, 0x4FF2AF00)])
        self.assertIn('panic_abort at panic.c:491', text)
        self.assertIn('0x4ff39008: ?? ??:0   (not code in this ELF: stack noise)', text)
        self.assertNotIn('panic.c:491   (not code', text)

    def test_the_consoles_assert_text_is_printed(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / 'flash.log'
            log.write_text(QUIET)
            (Path(tmp) / 'flash.serial.log').write_text(
                "10:32:54 I (1234) wifi: ok\n"
                "10:32:54 assert failed: xQueueGenericSend queue.c:936 (pxQueue)\n"
                "10:32:54 Backtrace: 0x4ff2ae00:0x4ff9e1a0\n")
            out = io.StringIO()
            panel_crash.scan(log, Path(tmp) / 'none.elf', out)
        self.assertIn('CONSOLE: 10:32:54 assert failed: xQueueGenericSend queue.c:936 (pxQueue)', out.getvalue())

    def test_a_rollback_alone_fails(self):
        self.assertEqual(self.scan(QUIET + GONE + ROLLBACK)[0], 1)

    def test_no_capture_is_not_a_failure(self):
        out = io.StringIO()
        self.assertEqual(panel_crash.scan(Path('/nonexistent/flash.log'), out=out), 0)
        self.assertIn('nothing checked', out.getvalue())


if __name__ == '__main__':
    unittest.main()
