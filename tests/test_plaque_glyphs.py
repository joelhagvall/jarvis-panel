"""Tavla's plaque never shows a box: its fonts cover European names, and the server folds the rest."""
import importlib.util
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server' / 'agents'))
sys.path.insert(0, str(ROOT / 'server'))
import background  # noqa: E402

spec = importlib.util.spec_from_file_location('plaque_gen', ROOT / 'styles/glass-v2/gen.py')
gen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gen)


class PlaqueGlyphTest(unittest.TestCase):
    def test_server_and_panel_agree_on_the_plaque_characters(self):
        self.assertEqual(background.PLAQUE_GLYPHS, frozenset(gen.PLAQUE_GLYPHS))

    def test_plaque_fonts_are_the_plaque_labels_fonts(self):
        page = gen.tavla_page()
        plaque = next(w for w in page['widgets'] if w.get('obj', {}).get('id') == 't_plaque')
        fonts = {w['label']['text_font'] for w in plaque['obj']['widgets']}
        self.assertEqual(fonts, {gen.f(w, s) for w, s in gen.PLAQUE_FONTS})

    def test_nasa_credit_of_2026_10_27_is_drawn_unchanged(self):
        # "V. Höfer, D. Večerník & B. Večerníková" showed boxes for č, í and á.
        credit = 'V. Höfer, D. Večerník & B. Večerníková'
        self.assertEqual(background.plaque_text(credit), credit)
        self.assertTrue(set(credit) <= set(gen.PLAQUE_GLYPHS))

    def test_letters_outside_the_fonts_lose_their_accent_and_the_rest_become_question_marks(self):
        self.assertEqual(background.plaque_text('Ơn Ǆ'), 'On DZ')
        self.assertEqual(background.plaque_text('Ngọc 銀河'), 'Ngoc ??')
        self.assertEqual(background.plaque_text('Stjärnenatten – 1889'), 'Stjärnenatten – 1889')


class MusicGlyphTest(unittest.TestCase):
    """Spelas nu showed "Rosal□a" (2026-10-27): its fonts cover European names, and HA folds the rest."""
    def setUp(self):
        spec = importlib.util.spec_from_file_location('music_glyphs', ROOT / 'ha/custom_components/jarvis_agent/glyphs.py')
        self.glyphs = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.glyphs)

    def test_home_assistant_and_the_panel_agree_and_the_rows_fonts_are_covered(self):
        self.assertEqual(self.glyphs.PANEL_GLYPHS, frozenset(gen.PLAQUE_GLYPHS))
        source = (ROOT / 'styles/glass-v2/now_playing.py').read_text()
        import re
        # The labels that show Home Assistant's text start empty; "Klar" and the error line are fixed text.
        used = {(int(w), int(s)) for w, s in re.findall(r'ui\.label\("", ui\.f\((\d+), (\d+)\)', source)}
        self.assertEqual(len(used), 5)            # the row's two, the page's 16, 34 and 22 px (the sheet went, 2026-10-28)
        self.assertTrue(used <= gen.MUSIC_FONTS, used - gen.MUSIC_FONTS)

    def test_names_are_drawn_or_folded(self):
        self.assertEqual(self.glyphs.fold('Fontaines D.C. & Rosalía'), 'Fontaines D.C. & Rosalía')
        self.assertEqual(self.glyphs.fold('Sigur Rós · Björk · Mø'), 'Sigur Rós · Björk · Mø')
        self.assertEqual(self.glyphs.fold('Ngọc 銀河'), 'Ngoc ??')


if __name__ == '__main__':
    unittest.main()
