"""Cold state and background task stacks stay in PSRAM (docs/memory.md, 2026-11-03: ~100 kB internal free)."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class MemoryPlacementTests(unittest.TestCase):
    def test_cold_statics_are_marked_for_psram_bss(self):
        for path, line in (('bench/responsiveness.h', 'inline Recorder recorder EXT_RAM_BSS_ATTR;'),
                           ('bench/boot_cache_esphome.h', 'inline PanelCache panel_cache EXT_RAM_BSS_ATTR;'),
                           ('styles/glass-v2/music_load.h', 'inline Fixture fixture EXT_RAM_BSS_ATTR;'),
                           ('styles/glass-v2/detail_load.h', 'inline Fixture fixture EXT_RAM_BSS_ATTR;'),
                           ('styles/glass-v2/calendar_page.py', 'static std::string detail[7][6][9] EXT_RAM_BSS_ATTR;')):
            with self.subTest(path=path):
                self.assertIn(line, (ROOT / path).read_text())
        # The calendar lambda gets the host fallback through a header every build includes.
        self.assertIn('#define EXT_RAM_BSS_ATTR', (ROOT / 'styles/glass-v2/calendar_color.h').read_text())

    def test_background_stacks_are_in_psram(self):
        for path, name in (('components/async_artwork/async_artwork.cpp', '"artwork"'),
                           ('styles/glass-v2/vinyl.h', '"vinyl-build"'),
                           ('components/panel_debug/panel_debug.cpp', '"radio-rssi"')):
            with self.subTest(task=name):
                source = (ROOT / path).read_text()
                calls = [source[i:source.index('pdPASS', i)] for i in range(len(source))
                         if source.startswith('xTaskCreate', i)]
                call = next(c for c in calls if name in c)
                self.assertTrue(call.startswith('xTaskCreatePinnedToCoreWithCaps'), call)
                self.assertIn('MALLOC_CAP_SPIRAM', call)

    def test_bss_in_psram_is_enabled(self):
        from board_config.hardware import apply  # noqa: F401  (import check only)
        text = (ROOT / 'board_config/hardware.py').read_text()
        self.assertIn('"CONFIG_SPIRAM_ALLOW_BSS_SEG_EXTERNAL_MEMORY"] = "y"', text)


if __name__ == '__main__':
    unittest.main()
