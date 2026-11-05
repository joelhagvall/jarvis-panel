#!/usr/bin/env python3
"""Restart Spotify Soloist when it says "playing" but no sound reaches the JBL.

2026-10-29: the JBL dropped off Bluetooth and came back; Soloist kept reporting "playing" while PipeWire's
`spotify` stream and `jbl` sink sat idle/suspended, and only a restart of hallpanel-soloist brought the sound back
(docs/spotify.md). Run every 30 s by hallpanel-soloist-watch.timer. It acts only after two silent checks in a row,
never while the JBL is not connected (a restart cannot help then), and at most three times an hour.
Each decision that changes something is logged to the journal and to ~/.local/state/hallpanel/soloist-watch.json.

2026-10-30 13:18 and 2026-10-31 07:55: the JBL was switched off while Spotify played here and came back on its own,
but the sound did not: PipeWire's `jbl` sink still held the dead BlueALSA PCM ("Could not look up revents: No such
device", 17 500 lines a second) and both `spotify` and `jbl` said "running", so the silent check above saw nothing.
Restarting Soloist alone did not help; restarting PipeWire and WirePlumber, then Soloist with activate and play did.
So a JBL drop (BlueALSA's `Freeing device: <JBL>`) while Spotify played here is remembered, and when the JBL is back
the watch "heals": PipeWire, WirePlumber, the jbl45 filter and Soloist are restarted, and play resumes only if
Spotify still plays here (never taking the session from Joel's phone). Heals count in the same hourly budget.

Stuck at a track's end (2026-10-29 several times, 2026-10-31 07:59-08:07): Soloist kept logging "playing" at
[2:52 / 2:52] and never went on to the next track; `next` went on at once. When Soloist's last position line in the
journal is at the end of its track on two checks in a row while it plays here, the watch sends `next`. Skips count
in the same hourly budget.
"""
import json
import os
from pathlib import Path
import subprocess
import time

SOLOIST = Path.home() / '.local/share/soloist/bin/soloist'
WS = os.environ.get('JARVIS_SOLOIST_WS', '127.0.0.1:9431')
STATE = Path.home() / '.local/state/hallpanel/soloist-watch.json'
JBL = os.environ.get('JARVIS_JBL_MAC', '02:00:00:00:00:03')
SILENT_CHECKS = 2          # about a minute of "playing" without sound
RESTARTS_PER_HOUR = 3
HEAL_WINDOW = 3600         # a drop older than this is forgotten: the next play opens the sink afresh anyway


def budget_spent(state):
    """Restarting did not help: stop instead of cutting the music in a loop. One log line per stuck period."""
    if not state.get('gave_up'):
        state['gave_up'] = True
        return 'give_up', state
    return None, state


def decide(playing, connected, stream_running, sink_running, state, now, dropped=False, at_end=False):
    """Return (action, new_state). action is 'heal', 'skip', 'restart', 'give_up' or None.

    dropped: the JBL left Bluetooth since the previous check (BlueALSA `Freeing device`).
    at_end: Soloist's last logged position equals its track's length."""
    state = dict(state)
    restarts = [t for t in state.get('restarts', []) if now - t < 3600]
    state['restarts'] = restarts
    was_playing = state.get('was_playing', False)
    state['was_playing'] = playing
    if dropped and (was_playing or playing):
        state['heal_pending'] = now
    pending = state.get('heal_pending')
    if pending is not None and now - pending > HEAL_WINDOW:
        state.pop('heal_pending')
        pending = None
    if pending is not None and connected:
        state.pop('heal_pending')
        if len(restarts) >= RESTARTS_PER_HOUR:
            return budget_spent(state)
        state.update(silent=0, at_end=0, gave_up=False, restarts=restarts + [now])
        return 'heal', state
    stuck = playing and connected and at_end
    state['at_end'] = state.get('at_end', 0) + 1 if stuck else 0
    if state['at_end'] >= SILENT_CHECKS:
        state['at_end'] = 0
        if len(restarts) >= RESTARTS_PER_HOUR:
            return budget_spent(state)
        state.update(silent=0, gave_up=False, restarts=restarts + [now])
        return 'skip', state
    silent = playing and connected and not (stream_running and sink_running)
    state['silent'] = state.get('silent', 0) + 1 if silent else 0
    if state['silent'] < SILENT_CHECKS:
        return None, state
    if len(restarts) >= RESTARTS_PER_HOUR:
        return budget_spent(state)
    state.update(silent=0, gave_up=False, restarts=restarts + [now])
    return 'restart', state


def run(*args, timeout=10):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def soloist(*args):
    return run(str(SOLOIST), 'ctl', '--ws', WS, *args)


def playing_here(now):
    """Soloist's `now` mirrors the account's playback: "playing" while Joel's iPhone plays it too. Only the
    active device is this speaker. 2026-10-29: counting the phone's playback as silence restarted Soloist, which
    restored the session and took the music from the phone every ~70 s."""
    return isinstance(now, dict) and now.get('status') == 'playing' and now.get('is_active') is True


def playing():
    result = soloist('now', '--json')
    try:
        return playing_here(json.loads(result.stdout))
    except ValueError:
        return False


def nodes():
    try:
        dump = json.loads(run('pw-dump').stdout)
    except (ValueError, OSError, subprocess.TimeoutExpired):
        return {}
    found = {}
    for item in dump:
        if item.get('type') != 'PipeWire:Interface:Node':
            continue
        info = item.get('info') or {}
        name = (info.get('props') or {}).get('node.name')
        if name:
            found[name] = info.get('state')
    return found


def muted_spotify():
    """The `spotify` stream's node id when its main volume is 0, else None. 2026-11-01: a silent pw-play at volume 0
    made WirePlumber save 0 for media.role Music, and Soloist's streams got it: "playing", every node running, nothing
    heard (Joel, 13:32-14:28). Soloist sets only channelVolumes, so a main volume of 0 is never Joel's choice."""
    try:
        dump = json.loads(run('pw-dump').stdout)
    except (ValueError, OSError, subprocess.TimeoutExpired):
        return None
    for item in dump:
        info = item.get('info') or {}
        if item.get('type') != 'PipeWire:Interface:Node' or (info.get('props') or {}).get('node.name') != 'spotify':
            continue
        for props in (info.get('params') or {}).get('Props') or []:
            if isinstance(props, dict) and 'volume' in props and props['volume'] == 0:
                return item['id']
    return None


def unmute_spotify(node):
    run('pw-cli', 'set-param', str(node), 'Props', '{ volume: 1.0 }')


def connected():
    return 'Connected: yes' in run('bluetoothctl', 'info', JBL).stdout


def dropped_since(since):
    """Did BlueALSA free the JBL (it left Bluetooth) after the unix time `since`?"""
    try:
        out = run('journalctl', '-q', '--no-pager', '-t', 'bluealsa', '--since', f'@{int(since)}',
                  '--grep', f'Freeing device: {JBL}').stdout
    except (OSError, subprocess.TimeoutExpired):
        return False
    return JBL in out


def at_track_end():
    """Is Soloist's last position line (it logs one every 10 s) at its track's end, e.g. "[2:52 / 2:52]"?"""
    import re
    try:
        out = run('journalctl', '-q', '--no-pager', '-o', 'cat', '_SYSTEMD_USER_UNIT=hallpanel-soloist.service',
                  '--since', '-25s', '--grep', 'playing spotify:').stdout
    except (OSError, subprocess.TimeoutExpired):
        return False
    lines = out.strip().splitlines()
    match = re.search(r'\[(\d+:\d+) / (\d+:\d+)\]\s*$', lines[-1]) if lines else None
    return bool(match) and match.group(1) == match.group(2)


def wait_for(check, seconds):
    for _ in range(seconds):
        if check():
            return True
        time.sleep(1)
    return False


def restart_soloist(resume, activate=False):
    run('systemctl', '--user', 'restart', 'hallpanel-soloist.service', timeout=30)
    # the WebSocket comes up a few seconds after the restart
    wait_for(lambda: 'logged in: yes' in soloist('status').stdout, 15)
    if resume or activate:
        soloist('activate')
    if resume:
        soloist('play')


def active_here():
    """Is this speaker Spotify's active device, playing or paused? Never true while Joel's phone has the session."""
    try:
        now = json.loads(soloist('now', '--json').stdout)
    except ValueError:
        return False
    return isinstance(now, dict) and now.get('is_active') is True


def heal(resume, activate=False):
    """The JBL came back after a drop mid-play: give PipeWire a fresh `jbl` sink, then Soloist a fresh stream.
    activate (the panel's Koppla om while paused here): Soloist comes back as the active device, as it was."""
    run('systemctl', '--user', 'restart', 'pipewire.service', 'wireplumber.service', timeout=30)
    if not wait_for(lambda: 'jbl' in nodes(), 15):
        print('soloist-watch: heal: no jbl sink 15 s after the PipeWire restart', flush=True)
    time.sleep(5)            # 2026-10-31: Soloist restarted 3 s after PipeWire stayed silent; later it sounded
    run('systemctl', '--user', 'restart', 'hallpanel-jbl45.service', timeout=30)
    restart_soloist(resume, activate)


def hold_off(seconds=120, heal=False):
    """The panel's Koppla om (server/agents/speaker.py reconnect) heals by itself: forget a pending heal and look for
    drops only from `seconds` ahead, so its own disconnect is not healed a second time. hold_off(0) afterwards;
    heal=True when the speaker did not come back, so the watch heals once it does."""
    state = load()
    state.pop('heal_pending', None)
    if heal:
        state['heal_pending'] = time.time()
    state['checked_at'] = time.time() + seconds
    save(state)


def load():
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {}


def save(state):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE.with_suffix('.tmp')
    temporary.write_text(json.dumps(state))
    temporary.replace(STATE)


def main():
    import sys
    if '--check' in sys.argv[1:]:   # the diagnosis by hand, without acting (docs/spotify.md)
        found = nodes()
        print(json.dumps({'playing': playing(), 'jbl_connected': connected(), 'spotify': found.get('spotify'),
                          'jbl': found.get('jbl'), 'spotify_volume_0': muted_spotify() is not None,
                          'state': load()}))
        return
    now = time.time()
    found = nodes()
    previous = load()
    here = playing()
    muted = muted_spotify() if here else None
    if muted is not None:              # silent while "playing": the stream's own volume is 0; no restart needed
        unmute_spotify(muted)
        print(f'soloist-watch: the spotify stream played at volume 0; set it to 1.0 (node {muted})', flush=True)
    dropped = dropped_since(previous.get('checked_at', now - 60))
    if dropped:
        print(f'soloist-watch: the JBL left Bluetooth (playing here: {previous.get("was_playing", False) or here})',
              flush=True)
    action, state = decide(here, connected(), found.get('spotify') == 'running', found.get('jbl') == 'running',
                           previous, now, dropped=dropped, at_end=here and at_track_end())
    state['checked_at'] = now
    if action == 'heal':
        print(f'soloist-watch: the JBL is back after a drop mid-play; restarting PipeWire, WirePlumber, jbl45 and '
              f'Soloist{" and resuming" if here else " (not resuming: Spotify does not play here)"}', flush=True)
        heal(here)
        time.sleep(4)
        after = nodes()
        state['last'] = {'at': now, 'action': 'heal', 'spotify': after.get('spotify'), 'jbl': after.get('jbl')}
        print(f'soloist-watch: after heal spotify={after.get("spotify")} jbl={after.get("jbl")}', flush=True)
    elif action == 'skip':
        print('soloist-watch: "playing" but stuck at the end of its track; sending next', flush=True)
        soloist('next')
        state['last'] = {'at': now, 'action': 'skip'}
    elif action == 'restart':
        print(f'soloist-watch: "playing" but silent (spotify={found.get("spotify")}, jbl={found.get("jbl")}); '
              'restarting hallpanel-soloist', flush=True)
        restart_soloist(True)
        time.sleep(4)
        after = nodes()
        state['last'] = {'at': now, 'spotify': after.get('spotify'), 'jbl': after.get('jbl')}
        print(f'soloist-watch: after restart spotify={after.get("spotify")} jbl={after.get("jbl")}', flush=True)
    elif action == 'give_up':
        print(f'soloist-watch: still silent after {RESTARTS_PER_HOUR} restarts this hour; leaving it', flush=True)
    save(state)


if __name__ == '__main__':
    main()
