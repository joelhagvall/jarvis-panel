"""Host-link diagnosis: a Mac sharing the panel's 2.4 GHz channel must be named before latency gates."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import panel_host_link as link


def profile(channel, status='spairport_status_connected'):
    """Shape of `system_profiler SPAirPortDataType -json` on macOS 27, with names that must never leak."""
    station = {'_name': 'en0', 'spairport_status_information': status,
               'spairport_wireless_mac_address': '02:00:00:00:00:07',
               'spairport_current_network_information': {
                   '_name': 'SecretHomeNetwork', 'spairport_network_channel': channel,
                   'spairport_network_phymode': '802.11n', 'spairport_network_country_code': 'SE',
                   'spairport_signal_noise': '-52 dBm / -95 dBm'},
               'spairport_airport_other_local_wireless_networks': [
                   {'_name': 'NeighbourNetwork', 'spairport_network_channel': '1 (2GHz, 20MHz)'}]}
    return {'SPAirPortDataType': [{'spairport_airport_interfaces': [
        station, {'_name': 'awdl0', 'spairport_current_network_information': {}}]}]}


ROUTE_WIFI = '   route to: 192.0.2.10\ndestination: 192.0.2.0\n  interface: en0\n      flags: <UP,DONE>\n'
ROUTE_WIRED = ROUTE_WIFI.replace('en0', 'en5')
PANEL = {'wifi_connected': True, 'rssi_dbm': -49, 'rssi_age_ms': 900, 'channel': None}


def ping_output(times, sent=None):
    lines = [f'64 bytes from 192.0.2.10: icmp_seq={i} ttl=64 time={t} ms' for i, t in enumerate(times)]
    sent = len(times) if sent is None else sent
    return '\n'.join(['PING 192.0.2.10 (192.0.2.10): 56 data bytes', *lines, '',
                      '--- 192.0.2.10 ping statistics ---',
                      f'{sent} packets transmitted, {len(times)} packets received, 0.0% packet loss',
                      'round-trip min/avg/max/stddev = 1/2/3/4 ms'])


CLEAR = link.parse_ping(ping_output([5.7] * 18 + [9.0, 13.0]))
# 2026-10-26: Mac on 2.4 GHz channel 6 with the panel, 90 ms average and 245 ms maximum.
CONGESTED = link.parse_ping(ping_output([60.0] * 15 + [150.0, 180.0, 200.0, 230.0, 245.0]))


def raw(channel, route=ROUTE_WIFI, ping=CLEAR, panel=PANEL):
    return {'profile': profile(channel), 'route': route, 'panel': panel, 'status_http_ms': 31.24,
            'panel_error': None, 'ping': ping}


class HostLinkTest(unittest.TestCase):
    def test_mac_on_panel_band_is_named_with_a_concrete_action(self):
        result = link.report('192.0.2.10', raw('6 (2GHz, 20MHz)', ping=CONGESTED))
        self.assertFalse(result['ok'])
        self.assertEqual(result['mac']['wifi']['band'], '2.4 GHz')
        self.assertEqual(result['mac']['wifi']['channel'], 6)
        self.assertTrue(any('2.4 GHz channel 6' in w and '5 GHz or Ethernet' in w for w in result['warnings']))
        self.assertTrue(any('ping p95 230.0 ms' in w for w in result['warnings']))
        self.assertTrue(any('does not report its Wi-Fi channel' in n for n in result['notes']))

    def test_same_or_overlapping_reported_panel_channel_warns_distant_one_does_not(self):
        same = link.report('192.0.2.10', raw('6 (2GHz, 20MHz)', panel=dict(PANEL, channel=6)))
        self.assertIn("Mac shares the panel's 2.4 GHz channel 6; switch the Mac to 5 GHz or Ethernet "
                      'before latency gates', same['warnings'])
        overlap = link.report('192.0.2.10', raw('4 (2GHz, 20MHz)', panel=dict(PANEL, channel=6)))
        self.assertTrue(any('overlaps' in w and 'Mac on 4' in w for w in overlap['warnings']))
        distant = link.report('192.0.2.10', raw('11 (2GHz, 20MHz)', panel=dict(PANEL, channel=6)))
        self.assertEqual(distant['warnings'], [])

    def test_clear_5ghz_link_passes(self):
        result = link.report('192.0.2.10', raw('64 (5GHz, 80MHz)'))
        self.assertTrue(result['ok'], result['warnings'])
        self.assertEqual((result['mac']['medium'], result['mac']['wifi']['band'], result['mac']['wifi']['width_mhz']),
                         ('wifi', '5 GHz', 80))
        self.assertEqual(result['panel']['status_http_ms'], 31.2)

    def test_wired_route_ignores_the_macs_own_2_4ghz_wifi(self):
        result = link.report('192.0.2.10', raw('6 (2GHz, 20MHz)', route=ROUTE_WIRED))
        self.assertEqual((result['mac']['medium'], result['mac']['interface']), ('wired', 'en5'))
        self.assertTrue(result['ok'], result['warnings'])

    def test_slow_5ghz_link_still_warns_on_measured_ping(self):
        result = link.report('192.0.2.10', raw('64 (5GHz, 80MHz)', ping=CONGESTED))
        self.assertEqual(len(result['warnings']), 1)
        self.assertIn('network, not the panel', result['warnings'][0])

    def test_unreachable_panel_and_lost_pings_warn(self):
        lost = link.parse_ping(ping_output([], sent=20))
        self.assertEqual((lost['loss_percent'], lost['p95_ms']), (100.0, None))
        result = link.report('192.0.2.10', dict(raw('64 (5GHz, 80MHz)', ping=lost, panel=None), panel_error='URLError'))
        self.assertEqual(result['panel']['error'], 'URLError')
        self.assertEqual(len(result['warnings']), 2)

    def test_output_never_contains_network_names_or_hardware_addresses(self):
        text = json.dumps(link.report('192.0.2.10', raw('6 (2GHz, 20MHz)')))
        for secret in ('SecretHomeNetwork', 'NeighbourNetwork', 'aa:bb:cc', '"SE"'):
            self.assertNotIn(secret, text)

    def test_ping_statistics_use_nearest_rank(self):
        self.assertEqual((CLEAR['avg_ms'], CLEAR['p95_ms'], CLEAR['max_ms'], CLEAR['loss_percent']),
                         (6.23, 9.0, 13.0, 0.0))
        self.assertIsNone(link.parse_ping('ping: cannot resolve host'))

    def test_profile_parsing_tolerates_missing_or_disconnected_wifi(self):
        self.assertIsNone(link.mac_wifi(None))
        idle = link.mac_wifi(profile('6 (2GHz, 20MHz)', status='spairport_status_off'))
        self.assertEqual((idle['connected'], idle['band']), (False, None))
        self.assertIsNone(link.parse_channel('garbage'))
        self.assertEqual(link.mac_link(None, None)['medium'], 'unknown')

    def test_ping_runs_before_the_wifi_profiler_which_disturbs_the_radio(self):
        calls = []
        def fake(command, timeout):
            calls.append(' '.join(command[:1] + command[-1:]))
            if command[0] == 'route':
                return ROUTE_WIFI + '    gateway: 192.0.2.1\n' if command[-1] == 'default' else ROUTE_WIFI
            return {'ping': ping_output([5.0] * 20),
                    'system_profiler': json.dumps(profile('64 (5GHz, 80MHz)'))}[command[0]]
        class Router:
            def __init__(self, command, **options):
                calls.append('router ' + command[-1])
            def communicate(self, timeout=None):
                return ping_output([3.0] * 20), ''
        with patch.object(link, 'command_output', side_effect=fake), patch.object(link.subprocess, 'Popen', Router), \
                patch('panel_remote.PanelClient', side_effect=OSError('offline')):
            result = link.run_checks('192.0.2.10', 8080, 20)
        self.assertEqual(calls, ['route 192.0.2.10', 'route default', 'router 192.0.2.1', 'ping 192.0.2.10',
                                 'system_profiler basic'])
        self.assertEqual((result['panel'], result['panel_error']), (None, 'OSError'))
        self.assertEqual(result['gateway_ping']['p95_ms'], 3.0)
        self.assertNotIn('192.0.2.1"', json.dumps(link.report('192.0.2.10', result)))

    def test_router_ping_says_which_half_of_the_path_is_slow(self):
        clear_router = link.parse_ping(ping_output([3.0] * 20))
        panel_side = link.report('192.0.2.10', dict(raw('64 (5GHz, 80MHz)', ping=CONGESTED), gateway_ping=clear_router))
        self.assertIn("panel's 2.4 GHz side", panel_side['warnings'][0])
        mac_side = link.report('192.0.2.10', dict(raw('64 (5GHz, 80MHz)', ping=CONGESTED), gateway_ping=CONGESTED))
        self.assertIn("fix the Mac's own link", mac_side['warnings'][0])
        self.assertEqual(panel_side['router_ping'], clear_router)

    def test_panel_channel_comes_from_the_boot_wifi_channel_field(self):
        self.assertEqual(link.panel_facts({'boot_wifi_channel': 6})['channel'], 6)
        self.assertIsNone(link.panel_facts({'boot_wifi_channel': 0})['channel'])

    def test_refuses_to_run_beside_a_latency_gate(self):
        import contextlib, io
        out = io.StringIO()
        with patch.object(link, 'measurement_running', return_value=True), \
                patch.object(link, 'run_checks', side_effect=AssertionError('measured during a gate')), \
                contextlib.redirect_stdout(out):
            self.assertEqual(link.main(['--url', 'http://192.0.2.10:8080']), 1)
        self.assertIn('device lock', out.getvalue())

    def test_url_validation_and_ip_literal_skip_resolution(self):
        with patch.object(link.socket, 'getaddrinfo', side_effect=AssertionError('resolved an IP literal')):
            self.assertEqual(link.resolve('http://192.0.2.10:8080'), ('192.0.2.10', 8080))
        for bad in ('ftp://hallpanel.local', 'http://user:pw@hallpanel.local:8080', 'http://hallpanel.local:8080/x'):
            with self.assertRaises(ValueError):
                link.resolve(bad)


if __name__ == '__main__':
    unittest.main()
