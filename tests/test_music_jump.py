"""Spelas nu after Jarvis starts music (styles/glass-v2/music_jump.h, now_playing.jump_after_voice). No panel."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

CODE = r'''
#include "music_jump.h"
#include <cassert>
using namespace panel_music_jump;
std::string spotify(const char *t) { return identity("playing", "Spotify", t); }
int main() {
  // Nothing played; "spela Cocktails" starts it during the turn.
  arm(1000, identity("idle", "", ""), 0);
  assert(!started(2000, identity("idle", "", "")));
  assert(started(3000, spotify("Cocktails")));
  assert(!started(3100, spotify("Other")));           // once per turn
  // The same track still playing, or paused: no jump.
  armed = false; arm(10000, spotify("A"), 200000);
  assert(!started(11000, spotify("A")));
  assert(!started(11000, identity("paused", "Spotify", "B")));
  // A station's songs change on their own; another station counts.
  armed = false; arm(20000, identity("playing", "P3", "Song 1"), 0);
  assert(!started(21000, identity("playing", "P3", "Song 2")));
  assert(started(22000, identity("playing", "P1", "News")));
  // A Spotify track that simply ran out is not his; "spela X" afterwards still is.
  armed = false; arm(30000, spotify("Ending"), 6000);      // ends at 36000
  assert(!started(35500, spotify("Next on its own")));
  assert(started(37000, spotify("What he asked for")));
  // A follow-up question keeps the first snapshot; the window closes 20 s after the turn.
  armed = false; arm(40000, identity("idle", "", ""), 0);
  arm(45000, spotify("Started meanwhile"), 0);
  assert(started(45100, spotify("Started meanwhile")));
  armed = false; arm(50000, identity("idle", "", ""), 0); settle(60000);
  assert(!started(80000, spotify("Too late")));
  armed = false; arm(90000, identity("idle", "", ""), 0);
  assert(!started(90000 + TURN_MS, spotify("Turn never ended")));
  // pending is taken once.
  pending = true; assert(take() && !take());
}
'''


class MusicJumpLogicTest(unittest.TestCase):
    def test_rules(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / 'test.cpp').write_text(CODE)
            subprocess.run(['c++', '-std=c++17', '-O1', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined',
                            '-I' + str(ROOT / 'styles/glass-v2'), str(folder / 'test.cpp'), '-o', str(folder / 'test')],
                           check=True, capture_output=True)
            subprocess.run([str(folder / 'test')], check=True, capture_output=True, timeout=10)


class ArtworkAheadBoardTest(unittest.TestCase):
    """The next Spotify cover is downloaded ahead under HA's artwork_next key (now_playing.board)."""
    def test_wired(self):
        out = subprocess.run([sys.executable, str(ROOT / 'board.py'), '--voice', 'jarvis_orb.py', '--remote',
                              'http://jarvis.local:8123/local/hallpanel'], cwd=ROOT / 'styles/glass-v2',
                             check=True, capture_output=True, text=True).stdout
        cfg = json.loads(out)
        sensor = next(t for t in cfg['text_sensor'] if t.get('id') == 'music_artwork_next')
        self.assertEqual((sensor['entity_id'], sensor['attribute']), ('sensor.jarvis_uppspelning', 'artwork_next'))
        self.assertIn('id(np_art).prefetch(', sensor['on_value'][0]['lambda'])
        self.assertIn('!panel_vinyl::page.building()', sensor['on_value'][0]['lambda'])   # never during a build
        retry = [i for i in cfg['interval'] if any('id(np_art).prefetch(' in a.get('lambda', '') for a in i['then'])]
        self.assertEqual([i['interval'] for i in retry], ['500ms'])


class MusicJumpBoardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        out = subprocess.run([sys.executable, str(ROOT / 'board.py'), '--voice', 'jarvis_orb.py', '--remote',
                              'http://jarvis.local:8123/local/hallpanel'], cwd=ROOT / 'styles/glass-v2',
                             check=True, capture_output=True, text=True).stdout
        cls.cfg = json.loads(out)
        cls.scripts = {s['id']: s for s in cls.cfg['script']}

    def test_armed_when_listening_and_fired_by_the_music_state(self):
        self.assertEqual(self.cfg['voice_assistant']['on_listening'][0], {'script.execute': 'music_arm'})
        self.assertIn('panel_music_jump::arm(', self.scripts['music_arm']['then'][0]['lambda'])
        status = next(t for t in self.cfg['text_sensor'] if t.get('id') == 'music_status')
        self.assertIn('panel_music_jump::started(', status['on_value'][-1]['lambda'])

    def test_voice_turn_and_tap_end_on_spelas_nu_by_day_only(self):
        for name in ('voice_done', 'orb_leave'):
            text = json.dumps(self.scripts[name])
            self.assertIn('panel_music_jump::settle(millis());', text, name)
            self.assertIn('return panel_music_jump::take() && !id(night) && !id(night_hours);', text, name)
            self.assertIn('"id": "page_musik"', text, name)

    def test_a_tap_on_the_voice_page_goes_back_before_waiting_for_the_assistant(self):
        # 2026-11-02: orb_leave waited up to 2 s for the assistant to stop before the page changed.
        leave = self.scripts['orb_leave']['then']
        shown = next(i for i, a in enumerate(leave) if '"lvgl.page.show"' in json.dumps(a))
        waited = next(i for i, a in enumerate(leave) if 'wait_until' in a)
        self.assertLess(shown, waited)
        self.assertEqual(leave[shown - 2], {'lambda': 'id(orb_leaving) = true;'})
        after = leave[waited + 1:]
        self.assertEqual(after[:2], [{'script.stop': 'voice_done'}, {'lambda': 'id(orb_leaving) = false;'}])
        self.assertIn('micro_wake_word.start', json.dumps(after))     # listens again only once stopped
        # the stopped run's late speech start does not bring the voice page back
        reshow = self.cfg['voice_assistant']['on_tts_start'][0]['if']['condition']['lambda']
        self.assertIn('!id(orb_leaving)', reshow)
        self.assertIn('orb_leaving', [g['id'] for g in self.cfg['globals']])

    def test_music_already_playing_skips_the_orbs_settle(self):
        turn = self.scripts['voice_done']['then'][-1]['if']['then']
        first = next(i for i, a in enumerate(turn) if 'if' in a and 'panel_music_jump::take()' in json.dumps(a['if']['condition']))
        text = json.dumps(turn[:first])
        self.assertIn('micro_wake_word.start', text)          # the wake word listens again first
        self.assertNotIn('"delay": "3s"', text)               # no settle before the first chance to jump
        self.assertIn('"delay": "3s"', json.dumps(turn[first]['if']['else']))   # the settle is only the other way

    def test_a_late_start_jumps_only_from_day_pages(self):
        condition = self.scripts['music_jump']['then'][0]['if']['condition']['lambda']
        self.assertIn('id(page_glass_v2)', condition)
        self.assertIn('!id(night)', condition)
        for page in ('page_natt', 'page_ota', 'page_orb', 'page_musik'):
            self.assertNotIn(f'id({page})', condition)


if __name__ == '__main__':
    unittest.main()
