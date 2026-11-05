#!/usr/bin/env python3
"""bench/wifi_boot.h: boot Wi-Fi milestones stay one-shot and passive, compiled on the host with stub ESP-IDF types.

The generator must install the probe after ESPHome's Wi-Fi setup (priority 250), which creates the event loop.
"""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

STUBS = {
    'esp_event.h': r'''#pragma once
#include <cstdint>
typedef const char *esp_event_base_t;
typedef int esp_err_t;
#define ESP_OK 0
typedef void (*esp_event_handler_t)(void *, esp_event_base_t, int32_t, void *);
static const char WIFI_EVENT_NAME[] = "WIFI_EVENT", IP_EVENT_NAME[] = "IP_EVENT";
#define WIFI_EVENT WIFI_EVENT_NAME
#define IP_EVENT IP_EVENT_NAME
#define ESP_EVENT_ANY_ID -1
inline int registrations = 0;
inline esp_err_t next_error = ESP_OK;
inline esp_event_handler_t last_handler = nullptr;
inline esp_err_t esp_event_handler_instance_register(esp_event_base_t, int32_t, esp_event_handler_t handler, void *,
                                                     void *) {
  registrations++;
  last_handler = handler;
  return next_error;
}
''',
    'esp_wifi.h': r'''#pragma once
#include <cstdint>
enum { WIFI_EVENT_STA_CONNECTED = 4, WIFI_EVENT_STA_DISCONNECTED = 5 };
typedef struct { uint8_t ssid[32]; uint8_t ssid_len; uint8_t bssid[6]; uint8_t channel; int authmode; uint16_t aid; }
    wifi_event_sta_connected_t;
typedef struct { uint8_t ssid[32]; uint8_t ssid_len; uint8_t bssid[6]; uint8_t reason; int8_t rssi; }
    wifi_event_sta_disconnected_t;
''',
    'esp_netif.h': '#pragma once\nenum { IP_EVENT_STA_GOT_IP = 0 };\n',
    'esphome/core/hal.h': '#pragma once\n#include <cstdint>\nnamespace esphome { inline uint32_t now_ms = 0; '
                          'inline uint32_t millis() { return now_ms; } }\n',
}

PROGRAM = r'''
#include "wifi_boot.h"
#include <iostream>
int main() {
  std::cout << "{\"before\":0" << wifi_boot::json() << "}\n";
  esphome::now_ms = 600;
  wifi_boot::install();
  wifi_boot::install();                       // once only
  std::cout << "{\"registrations\":" << registrations << wifi_boot::json() << "}\n";
  wifi_event_sta_disconnected_t lost{};
  lost.reason = 201;
  esphome::now_ms = 3000;
  wifi_boot::on_event(nullptr, WIFI_EVENT, WIFI_EVENT_STA_DISCONNECTED, &lost);
  wifi_event_sta_connected_t joined{};
  joined.channel = 6;
  joined.authmode = 3;
  esphome::now_ms = 5900;
  wifi_boot::on_event(nullptr, WIFI_EVENT, WIFI_EVENT_STA_CONNECTED, &joined);
  esphome::now_ms = 6500;
  wifi_boot::on_event(nullptr, IP_EVENT, IP_EVENT_STA_GOT_IP, nullptr);
  // Later reconnects never move the boot milestones.
  esphome::now_ms = 90000;
  joined.channel = 11;
  wifi_boot::on_event(nullptr, WIFI_EVENT, WIFI_EVENT_STA_DISCONNECTED, &lost);
  wifi_boot::on_event(nullptr, WIFI_EVENT, WIFI_EVENT_STA_CONNECTED, &joined);
  wifi_boot::on_event(nullptr, IP_EVENT, IP_EVENT_STA_GOT_IP, nullptr);
  std::cout << "{\"after\":1" << wifi_boot::json() << "}\n";
}
'''


class WifiBootTest(unittest.TestCase):
    def test_milestones_are_one_shot_and_json(self):
        compiler = shutil.which('clang++') or shutil.which('g++')
        self.assertIsNotNone(compiler, 'Native C++ compiler required')
        with tempfile.TemporaryDirectory(prefix='hallpanel-wifi-boot-') as folder:
            folder = Path(folder)
            for name, text in STUBS.items():
                (folder / name).parent.mkdir(parents=True, exist_ok=True)
                (folder / name).write_text(text)
            (folder / 'test.cpp').write_text(PROGRAM)
            built = subprocess.run([compiler, '-std=c++17', '-O1', '-fsanitize=address,undefined', '-I', str(folder),
                                    '-I', str(ROOT / 'bench'), str(folder / 'test.cpp'), '-o', str(folder / 'test')],
                                   capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stderr)
            run = subprocess.run([str(folder / 'test')], capture_output=True, text=True, timeout=10)
            self.assertEqual(run.returncode, 0, run.stderr)
            before, installed, after = map(json.loads, run.stdout.splitlines())
        self.assertEqual(before['boot_wifi_probe_error'], 1)
        self.assertEqual(installed['registrations'], 2)
        self.assertEqual((installed['boot_wifi_probe_error'], installed['boot_wifi_setup_ms']), (0, 600))
        self.assertEqual(after['boot_wifi_assoc_ms'], 5900)
        self.assertEqual(after['boot_wifi_ip_ms'], 6500)
        self.assertEqual((after['boot_wifi_authmode'], after['boot_wifi_channel']), (3, 6))
        self.assertEqual((after['boot_wifi_disconnects'], after['boot_wifi_last_reason']), (1, 201))

    def test_generator_installs_after_esphome_wifi_setup(self):
        generated = subprocess.run([sys.executable, str(ROOT / 'board.py'), '--voice', 'jarvis_orb.py',
                                    '--remote', 'http://example.invalid/assets'], cwd=ROOT / 'styles/glass-v2',
                                   capture_output=True, text=True, check=True)
        cfg = json.loads(generated.stdout)
        boots = [b for b in cfg['esphome']['on_boot'] if any('wifi_boot::install' in a.get('lambda', '')
                                                             for a in b['then'])]
        self.assertEqual(len(boots), 1)
        self.assertLess(boots[0]['priority'], 250)
        self.assertTrue(any(str(i).endswith('bench/wifi_boot.h') for i in cfg['esphome']['includes']))



if __name__ == '__main__':
    unittest.main()
