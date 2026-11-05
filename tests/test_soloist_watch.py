"""The Soloist watchdog restarts only a silent "playing" Soloist, after two checks, at most three times an hour."""
import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server/soloist'))
from server.soloist.watch import decide, playing_here, HEAL_WINDOW, RESTARTS_PER_HOUR


class SoloistWatchTest(unittest.TestCase):
    def test_two_silent_checks_restart_once(self):
        action, state = decide(True, True, False, False, {}, 1000)
        self.assertIsNone(action)                       # one silent sample can be a play just starting
        action, state = decide(True, True, False, False, state, 1030)
        self.assertEqual(action, 'restart')
        self.assertEqual(state['restarts'], [1030])
        action, state = decide(True, True, False, False, state, 1060)
        self.assertIsNone(action)                       # the count starts again after a restart

    def test_sound_paused_or_no_jbl_never_restarts(self):
        for args in ((True, True, True, True), (False, True, False, False), (True, False, False, False)):
            state = {}
            for t in range(5):
                action, state = decide(*args, state, 1000 + 30 * t)
                self.assertIsNone(action)
        _, state = decide(True, True, False, False, {}, 1000)
        _, state = decide(True, True, True, True, state, 1030)      # sound came back: no restart
        self.assertEqual(state['silent'], 0)

    def test_gives_up_after_three_restarts_an_hour_and_logs_once(self):
        state = {'restarts': [1000 + i for i in range(RESTARTS_PER_HOUR)]}
        actions = []
        for t in range(4):
            action, state = decide(True, True, False, False, state, 2000 + 30 * t)
            actions.append(action)
        self.assertEqual(actions, [None, 'give_up', None, None])
        action, state = decide(True, True, False, False, state, 1000 + 3601)   # an hour later it may try again
        self.assertEqual(action, 'restart')


class NoStreamCaptureTest(unittest.TestCase):
    """2026-11-01 14:12: a 3 s pw-record of the `spotify` stream read 2 048 frames, peak 0 while Joel heard the music,
    and the restart it caused cut the song. A capture is no silence test: the watch must not use one."""
    def test_the_watch_never_records_the_stream(self):
        source = (Path(__file__).resolve().parents[1] / 'server/soloist/watch.py').read_text()
        self.assertNotIn('pw-record', source)
        self.assertNotIn('flowing', source)


class MutedStreamTest(unittest.TestCase):
    """2026-11-01: Spotify "played" with the `spotify` stream's main volume at 0, saved by WirePlumber from a silent
    pw-play at volume 0. The watch sees it in pw-dump and sets the volume back, without a restart."""
    def dump(self, volume):
        import json
        return json.dumps([{'id': 58, 'type': 'PipeWire:Interface:Node',
                            'info': {'props': {'node.name': 'spotify'}, 'state': 'running',
                                     'params': {'Props': [{'volume': volume, 'channelVolumes': [0.064, 0.064]}]}}},
                           {'id': 32, 'type': 'PipeWire:Interface:Node',
                            'info': {'props': {'node.name': 'jbl'}, 'params': {'Props': [{'volume': 0.0}]}}}])

    def test_volume_0_is_found_and_set_back(self):
        from server.soloist import watch
        from unittest import mock
        calls = []
        def run(*args, timeout=10):
            calls.append(args)
            return mock.Mock(stdout=self.dump(0.0) if args[0] == 'pw-dump' else '')
        with mock.patch.object(watch, 'run', run):
            self.assertEqual(watch.muted_spotify(), 58)               # the jbl sink at 0 is not Spotify
            watch.unmute_spotify(58)
        self.assertIn(('pw-cli', 'set-param', '58', 'Props', '{ volume: 1.0 }'), calls)

    def test_a_normal_stream_is_left_alone(self):
        from server.soloist import watch
        from unittest import mock
        with mock.patch.object(watch, 'run', return_value=mock.Mock(stdout=self.dump(1.0))):
            self.assertIsNone(watch.muted_spotify())
        with mock.patch.object(watch, 'run', side_effect=OSError):
            self.assertIsNone(watch.muted_spotify())


class WakeLinkVolumeTest(unittest.TestCase):
    """The voice path's silence to the JBL must not save a volume for Soloist's role, and WirePlumber must not
    restore saved volumes onto Spotify (2026-11-01)."""
    def test_wake_link_is_silent_by_its_zeros_not_by_volume_0(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / 'server/agents/spotify.py').read_text()
        start = source.index('def _wake_link():')
        body = source[start:source.index('\ndef ', start + 1)]
        self.assertNotIn('"--volume"', body)
        self.assertIn('"--media-role", WAKE_ROLE', body)
        self.assertNotEqual(__import__('re').search(r'WAKE_ROLE = "([^"]+)"', source).group(1), 'Music')
        conf = (root / 'server/soloist/wireplumber-jarvis.conf').read_text()
        self.assertIn('application.name = "Spotify"', conf)
        self.assertIn('state.restore-props = false', conf)


class HealAfterDropTest(unittest.TestCase):
    """2026-10-31 07:55: the JBL went off mid-play and came back 8 s later; PipeWire's sink stayed dead while both
    nodes said "running". A drop while playing here heals once the JBL is back."""

    def test_drop_mid_play_heals_when_the_jbl_is_back(self):
        _, state = decide(True, True, True, True, {}, 1000)                       # playing on the JBL
        action, state = decide(True, False, True, True, state, 1030, dropped=True)
        self.assertIsNone(action)                                                  # off: nothing to heal yet
        action, state = decide(True, True, True, True, state, 1060)                # back, nodes "running"
        self.assertEqual(action, 'heal')
        self.assertEqual(state['restarts'], [1060])
        action, state = decide(True, True, True, True, state, 1090)
        self.assertIsNone(action)                                                  # once per drop

    def test_off_and_on_between_two_checks_heals_at_once(self):
        _, state = decide(True, True, True, True, {}, 1000)
        action, _ = decide(True, True, True, True, state, 1030, dropped=True)
        self.assertEqual(action, 'heal')

    def test_drop_while_nothing_played_here_never_heals(self):
        # Nine reconnects this week with nothing playing came back with sound; the phone's playback does not count.
        _, state = decide(False, True, False, False, {}, 1000)
        action, state = decide(False, False, False, False, state, 1030, dropped=True)
        self.assertIsNone(action)
        action, state = decide(False, True, False, False, state, 1060)
        self.assertIsNone(action)

    def test_a_drop_older_than_the_window_is_forgotten(self):
        _, state = decide(True, True, True, True, {}, 1000)
        _, state = decide(True, False, True, True, state, 1030, dropped=True)
        action, state = decide(False, True, False, False, state, 1030 + HEAL_WINDOW + 1)
        self.assertIsNone(action)
        self.assertNotIn('heal_pending', state)

    def test_heals_share_the_hourly_budget(self):
        state = {'restarts': [1000 + i for i in range(RESTARTS_PER_HOUR)], 'was_playing': True}
        action, state = decide(True, True, True, True, state, 2000, dropped=True)
        self.assertEqual(action, 'give_up')
        self.assertNotIn('heal_pending', state)


class HoldOffTest(unittest.TestCase):
    """The panel's Koppla om heals by itself; its own disconnect must not make the watch heal again."""
    def setUp(self):
        import tempfile
        from server.soloist import watch
        self.watch = watch
        tmp = Path(tempfile.mkdtemp()); self.addCleanup(__import__('shutil').rmtree, tmp)
        self.old, watch.STATE = watch.STATE, tmp / 'state.json'
        self.addCleanup(setattr, watch, 'STATE', self.old)

    def test_hold_off_forgets_a_pending_heal_and_looks_for_drops_only_later(self):
        import time
        self.watch.save({'heal_pending': 1.0, 'checked_at': 1.0})
        self.watch.hold_off()
        state = self.watch.load()
        self.assertNotIn('heal_pending', state)
        self.assertGreater(state['checked_at'], time.time() + 100)
        # 'dropped' is what main() asks journalctl from checked_at on: nothing before it counts
        action, _ = decide(True, True, True, True, state, time.time(), dropped=False)
        self.assertIsNone(action)

    def test_a_speaker_that_did_not_come_back_is_healed_by_the_watch_later(self):
        self.watch.hold_off(0, heal=True)
        action, _ = decide(False, True, False, False, self.watch.load(), __import__('time').time())
        self.assertEqual(action, 'heal')


class StuckAtTrackEndTest(unittest.TestCase):
    """2026-10-31 07:59-08:07: Soloist logged "playing" at [2:52 / 2:52] for 8 minutes; `next` went on at once."""

    def test_two_checks_at_the_end_skip_once(self):
        action, state = decide(True, True, True, True, {}, 1000, at_end=True)
        self.assertIsNone(action)                       # one line at the end can be a normal track change
        action, state = decide(True, True, True, True, state, 1030, at_end=True)
        self.assertEqual(action, 'skip')
        action, state = decide(True, True, True, True, state, 1060, at_end=False)
        self.assertIsNone(action)

    def test_paused_phone_or_jbl_off_never_skips(self):
        for args in ((False, True), (True, False)):
            state = {}
            for t in range(4):
                action, state = decide(*args, True, True, state, 1000 + 30 * t, at_end=True)
                self.assertIsNone(action)

    def test_skips_share_the_hourly_budget(self):
        state = {'restarts': [1000 + i for i in range(RESTARTS_PER_HOUR)]}
        _, state = decide(True, True, True, True, state, 2000, at_end=True)
        action, _ = decide(True, True, True, True, state, 2030, at_end=True)
        self.assertEqual(action, 'give_up')


class AtTrackEndTest(unittest.TestCase):
    def test_reads_soloists_last_position_line(self):
        import types, watch
        cases = (('07:59:47.141: playing spotify:track:x "10:35" by "Tiësto" [2:52 / 2:52]', True),
                 ('08:07:28.141: playing spotify:track:y "I Lay" by "Westlife" [0:20 / 3:28]', False), ('', False))
        original = watch.run
        self.addCleanup(setattr, watch, 'run', original)
        for line, want in cases:
            watch.run = lambda *a, line=line, **k: types.SimpleNamespace(stdout=line + '\n')
            self.assertIs(watch.at_track_end(), want, line)


class PlayingHereTest(unittest.TestCase):
    def test_another_device_playing_is_not_this_speaker(self):
        # Joel's iPhone plays: Soloist mirrors "playing" with is_active false. Never a restart reason.
        self.assertFalse(playing_here({'status': 'playing', 'is_active': False}))
        self.assertFalse(playing_here({'status': 'playing'}))
        self.assertFalse(playing_here({'status': 'paused', 'is_active': True}))
        self.assertTrue(playing_here({'status': 'playing', 'is_active': True}))



FAKE_SOLOIST = """#!/bin/sh
# A new Soloist build: answers --version and `ctl`, records each ctl command. ACTIVE=false: the phone has the session.
[ "$1" = --version ] && { echo "soloist 9.9 (fake)"; exit 0; }
shift 3                                         # ctl --ws ADDR
echo "$*" >> "$LOG"
case "$1" in
  now) echo '{"status": "playing", "is_active": '"${ACTIVE:-true}"'}' ;;
  status) echo "  logged in: yes" ;;
esac
"""


class UpdateTest(unittest.TestCase):
    """update.sh restarts Soloist for a new build; 2026-10-30 that left the JBL's music paused."""
    def run_update(self, active):
        import os, subprocess, tarfile, tempfile
        tmp = Path(tempfile.mkdtemp()); self.addCleanup(__import__('shutil').rmtree, tmp)
        home, bin_dir, log = tmp / 'home', tmp / 'bin', tmp / 'log'
        (home / '.local/share/soloist/bin').mkdir(parents=True); bin_dir.mkdir()
        (home / '.local/share/soloist/bin/soloist').write_text('old build')
        new = tmp / 'soloist'; new.write_text(FAKE_SOLOIST); new.chmod(0o755)
        with tarfile.open(tmp / 'build.tar.gz', 'w:gz') as tar:
            tar.add(new, arcname='soloist')
        # curl hands out the fake build; systemctl says the speaker runs and records the restart
        (bin_dir / 'curl').write_text(f'#!/bin/sh\nwhile [ "$1" != -o ]; do shift; done\ncp {tmp}/build.tar.gz "$2"\n')
        (bin_dir / 'systemctl').write_text('#!/bin/sh\necho "systemctl $*" >> "$LOG"\n')
        for f in bin_dir.iterdir():
            f.chmod(0o755)
        env = {**os.environ, 'HOME': str(home), 'PATH': f'{bin_dir}:{os.environ["PATH"]}', 'LOG': str(log),
               'ACTIVE': 'true' if active else 'false'}
        script = Path(__file__).resolve().parents[1] / 'server/soloist/update.sh'
        result = subprocess.run(['sh', str(script)], env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return [line for line in log.read_text().splitlines() if not line.startswith('systemctl --user is-active')]

    def test_music_playing_on_the_jbl_plays_on_after_the_restart(self):
        self.assertEqual(self.run_update(True), ['now --json', 'systemctl --user restart hallpanel-soloist.service',
                                                 'status', 'activate', 'play'])

    def test_the_phones_session_is_not_taken_after_the_restart(self):
        self.assertEqual(self.run_update(False), ['now --json', 'systemctl --user restart hallpanel-soloist.service'])


if __name__ == '__main__':
    unittest.main()
