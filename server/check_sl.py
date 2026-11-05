#!/usr/bin/env python3
"""Jarvis side of `tools/panel sl-check`: is the panel's metro card healthy right now? Free; read-only.

Reads the running server's GET /sl/status (counts and ages, no departure text) and prints a verdict as JSON.
Exit 1 means our code lost or never showed a board SL had given us ("bug"), or Home Assistant stopped
reading the panel. A long SL outage is reported as "sl_outage" with exit 0: the panel says so honestly.

--notify (hallpanel-sl-check.timer, every 5 minutes on Jarvis) pushes to Joel's phone only when an alert
appears or clears: a problem above, the server unreachable twice in a row, "Saknar SL-kontakt" for 20 minutes,
or the panel's own health from the passive monitor (server/panel_health.py: restart loop, Wi-Fi, memory,
the monitor itself stalled; never "Panelen svarar inte", Joel's choice), plus one push per crash boot and per boot whose memory
nearly ran out (panel_health.memory_floors). Never at night (the panel's own night mode); what
is still open in the morning is sent then. State: ~/.local/state/hallpanel/sl-check.json.
"""
import argparse
import json
import os
from pathlib import Path
import sys
from urllib.request import Request, urlopen

MISSING, UPDATING = 'Saknar SL-kontakt', 'Uppdaterar avgångar'
HA_POLL_S = 15                                   # ha/custom_components/jarvis_agent HOME_INTERVAL_S
OUTAGE_ALERT_S = 20 * 60
STATE = Path.home() / '.local/state/hallpanel/sl-check.json'
ALL_CLEAR = 'Hallpanelen fungerar igen.'
ENV_KEYS = ('HALLPANEL_TOKEN', 'JARVIS_PHONE', 'JARVIS_HOME_TOKEN_FILE', 'JARVIS_QUIET_HOURS', 'HA_URL')


def verdict(status):
    """(problems, notes) for an /sl/status dict; problems mean exit 1."""
    if status.get('available') is False:
        return ['home status is not configured on this server'], []
    limits, problems, notes = status['limits'], [], []
    uptime, shown, shown_for = status['uptime_s'] or 0, status['shown'], status['shown_for_s'] or 0
    read_age, ok_age = status['last_panel_read_age_s'], status['last_ok_age_s']
    if uptime > 8 * HA_POLL_S and (read_age is None or read_age > 8 * HA_POLL_S):
        problems.append(f'Home Assistant has not read GET /panel for {read_age} s')
    if shown == UPDATING and shown_for > limits['stale_s'] + 4 * HA_POLL_S:
        problems.append(f'panel still says {UPDATING!r} after {shown_for} s')
    fresh_trains = status['last_ok_trains'] and ok_age is not None and ok_age < limits['max_age_s'] - limits['retry_s']
    if shown in (MISSING, 'Inga avgångar') and fresh_trains:
        problems.append(f'bug: panel says {shown!r} although SL gave trains {ok_age} s ago')
    if shown == MISSING and not fresh_trains:
        notes.append(f'sl_outage: no usable SL board for {shown_for} s; replies {status["replies"]}, '
                     f'HTTP {status["http"]}')
    if status['longest_missing_s'] > limits['max_age_s']:
        notes.append(f'longest {MISSING!r} stretch since start: {status["longest_missing_s"]} s')
    return problems, notes


def alerts(status, problems, unreachable):
    """{category: Swedish text} worth a push now."""
    out = {}
    if unreachable >= 2:
        out['server'] = 'Hallpanelens server på Jarvis svarar inte.'
    for problem in problems:
        if problem.startswith('bug:'):
            out['bug'] = 'SL-kortet tappade avgångar som SL hade gett (fel i Jarvis kod).'
        elif 'Home Assistant' in problem:
            out['ha'] = 'Home Assistant läser inte panelens data längre.'
        elif 'still says' in problem:
            out['stuck'] = 'SL-kortet har fastnat på "Uppdaterar avgångar".'
    if status and status.get('shown') == MISSING and (status.get('shown_for_s') or 0) >= OUTAGE_ALERT_S:
        out['outage'] = f'SL har inte gett avgångar på {round(status["shown_for_s"] / 60)} minuter.'
    return out


def decide(state, current, night):
    """(push text or None, new state). Only changes are pushed; nothing at night."""
    sent = state.get('sent', {})
    if night:                                       # decide again in the morning, with nothing forgotten
        return None, state
    new = {k: v for k, v in current.items() if k not in sent}
    cleared = [k for k in sent if k not in current]
    state = {**state, 'sent': {**{k: v for k, v in sent.items() if k in current}, **new}}
    if new:
        return ' '.join(new.values()), state
    if cleared and not current:
        return ALL_CLEAR, state
    return None, state


def load_env():
    for line in (Path.home() / '.config/hallpanel/server.env').read_text().splitlines():
        key, _, value = line.partition('=')
        if key.strip() in ENV_KEYS:
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    if not os.environ.get('HALLPANEL_TOKEN'):
        raise SystemExit('HALLPANEL_TOKEN missing in ~/.config/hallpanel/server.env')


def read_status():
    request = Request('http://127.0.0.1:8765/sl/status', headers={'X-Hallpanel-Token': os.environ['HALLPANEL_TOKEN']})
    with urlopen(request, timeout=10) as response:
        return json.load(response)


def notify(status, problems, unreachable):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import notify as phone
    import panel_health
    try:
        state = json.loads(STATE.read_text())
    except (OSError, ValueError):
        state = {}
    state['unreachable'] = unreachable = (state.get('unreachable', 0) + 1) if unreachable else 0
    previous = state.get('sent', {})
    night, health = phone.is_night(), panel_health.load()
    current = {**alerts(status, problems, unreachable), **panel_health.lasting(health)}
    current.pop('panel_unreachable', None)          # Joel 2026-10-28: no push when the panel itself is unreachable
    text, state = decide(state, current, night)
    if 'crashes' not in state:                      # first run: crashes from before this alert existed are old news
        state['crashes'] = [boot for boot, _ in panel_health.crashes(health)]
    if 'memory_floors' not in state:                # same for low memory floors seen before this alert existed
        state['memory_floors'] = [boot for boot, _ in panel_health.memory_floors(health)]
    crashed = [] if night else panel_health.crashes(health, seen=state['crashes'])
    floors = [] if night else panel_health.memory_floors(health, seen=state['memory_floors'])
    if crashed:
        state['crashes'] = (state.get('crashes', []) + [boot for boot, _ in crashed])[-50:]
    if floors:
        state['memory_floors'] = (state.get('memory_floors', []) + [boot for boot, _ in floors])[-50:]
    if crashed or floors:
        text = ' '.join([message for _, message in crashed + floors] + ([text] if text and text != ALL_CLEAR else []))
    if text:
        try:
            failed, reply = phone.Notifier().push(text, title='Hallpanelen')
        except phone.DeliveryUnconfirmed:           # may have arrived: never risk a duplicate
            failed, reply = False, 'delivery unconfirmed'
        print(f'push: {reply}', file=sys.stderr)
        if failed:                                  # surely not sent: try again next run
            state['sent'] = previous
    STATE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    STATE.write_text(json.dumps(state, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--notify', action='store_true', help='push alert changes to the phone')
    args = parser.parse_args()
    load_env()
    try:
        status = read_status()
    except OSError as error:
        if args.notify:
            notify(None, [], True)
        print(json.dumps({'ok': False, 'problems': [f'server unreachable: {type(error).__name__}']}))
        return 1
    problems, notes = verdict(status)
    if args.notify:
        notify(status, problems, False)
    print(json.dumps({'ok': not problems, 'problems': problems, 'notes': notes, 'status': status},
                     ensure_ascii=False, indent=1))
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main())
