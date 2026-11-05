"""Target selection is passive; absent/mismatched USB must stop, never fall back."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import panel_preflight as pre

PORT = {'device': '/dev/cu.test-board', 'vid': 0x303a, 'pid': 0x1001, 'serial_number': 'known-board'}

class PreflightTest(unittest.TestCase):
    def test_default_and_explicit_network_never_enumerate_open_or_pick_a_usb_device(self):
        with patch.object(pre, 'usb_ports', side_effect=AssertionError('unexpected USB selection')):
            self.assertEqual(pre.validate_target()['device'], 'hallpanel.local')
            result = pre.validate_target('192.0.2.1')
            self.assertEqual(result['transport'], 'network'); self.assertFalse(result['network_contacted'])

    def test_missing_port_does_not_fall_back_to_network(self):
        with patch.object(pre, 'usb_ports', return_value=[]), self.assertRaisesRegex(ValueError, 'absent'):
            pre.validate_target('/dev/cu.remembered-port')

    def test_known_serial_matches_only_the_explicit_live_port(self):
        with patch.object(pre, 'usb_ports', return_value=[PORT]), patch.object(Path, 'exists', return_value=True):
            result = pre.validate_target(PORT['device'], serial_number='known-board')
            self.assertEqual(result['usb']['serial_number'], 'known-board')
            self.assertFalse(result['board_identity_verified'])
            with self.assertRaisesRegex(ValueError, 'serial number differs'):
                pre.validate_target(PORT['device'], serial_number='different-board')

    def test_unplugged_or_ambiguous_metadata_fails_before_opening(self):
        for ports, present in (([PORT], False), ([PORT, PORT], True)):
            with self.subTest(present=present), patch.object(pre,'usb_ports',return_value=ports), patch.object(Path,'exists',return_value=present):
                with self.assertRaisesRegex(ValueError,'absent or ambiguous'): pre.validate_target(PORT['device'])

    def test_require_usb_needs_explicit_selection_even_with_candidates(self):
        output=io.StringIO()
        with patch.object(pre,'usb_ports',return_value=[PORT]) as ports, contextlib.redirect_stdout(output):
            self.assertEqual(pre.main(['--require-usb']),1)
        ports.assert_not_called(); self.assertFalse(json.loads(output.getvalue())['ok'])

    def test_usb_inventory_never_opens_a_serial_connection(self):
        from serial.tools import list_ports
        import serial
        with patch.object(list_ports,'comports',return_value=[]), patch.object(serial,'Serial') as opened:
            self.assertEqual(pre.usb_ports(),[])
        opened.assert_not_called()

    def test_credential_url_is_rejected_without_echoing_its_secret(self):
        output=io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(pre.main(['--device','http://user:private-secret@host.invalid']),1)
        self.assertNotIn('private-secret',output.getvalue())
        self.assertFalse(json.loads(output.getvalue())['ok'])

    def test_other_virtual_environment_is_rejected_before_device_work(self):
        output=io.StringIO()
        with patch.object(sys,'prefix','/tmp/not-the-project-venv'), contextlib.redirect_stdout(output):
            self.assertEqual(pre.main([]),1)
        self.assertIn('project virtual environment',json.loads(output.getvalue())['error'])

    def test_global_python_gets_actionable_error_not_a_late_import_failure(self):
        output=io.StringIO()
        with patch.object(sys,'prefix',sys.base_prefix), contextlib.redirect_stdout(output):
            self.assertEqual(pre.main([]),1)
        self.assertIn('tools/panel preflight',json.loads(output.getvalue())['error'])

    def test_stable_entry_point_works_from_another_directory_without_contact(self):
        with tempfile.TemporaryDirectory() as folder:
            result=subprocess.run([str(ROOT/'tools/panel'),'preflight'],cwd=folder,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        data=json.loads(result.stdout);self.assertEqual(data['device'],'hallpanel.local')
        self.assertFalse(data['network_contacted']);self.assertTrue(data['ok'])

if __name__ == '__main__': unittest.main()
