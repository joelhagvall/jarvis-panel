"""Turn the passive panel monitor's history into phone alerts (read only; used by check_sl.py --notify).

hallpanel-health.timer (tools/panel_metrics.py monitor, installed under ~/.local/share/hallpanel-harness) samples
the panel's /status every 5 minutes into ~/.local/state/hallpanel/health.json and analyses it, but pushes nothing.
On 2026-10-26 that history held 12 samples after panic resets that nobody was told about.

  lasting(history, now)        {category: Swedish text} for problems that are still true now
  crashes(history, now, seen)  [(boot_id, text)] for crash boots in the last day not pushed yet
  memory_floors(history, now, seen)  [(boot_id, text)] for boots whose lowest memory since start came near empty

Normal restarts (power on, software restart after OTA/flash, USB) are not crashes.
"""
import json
from pathlib import Path
import time

HISTORY = Path.home() / '.local/state/hallpanel/health.json'
# esp_reset_reason_t: 4 panic, 5 interrupt watchdog, 6 task watchdog, 7 other watchdog, 9 brownout.
CRASH_REASONS = {'4': 'kraschade (panic)', '5': 'fastnade (interrupt-watchdog)', '6': 'fastnade (task-watchdog)',
                 '7': 'fastnade (watchdog)', '9': 'fick för låg spänning (brownout)'}
# Lowest free memory since boot (the firmware's *_min_free). Development builds went down to 3-16 KiB internal and
# 1.6 MB PSRAM on 2026-10-28/29 without the 96 KiB "now" floor noticing; normal 6-13 h runs bottomed at 80-200 KiB.
FLOORS = {'internal_min_free': 32 * 1024, 'psram_min_free': 1024 * 1024}
# Only a boot that stayed up this long: a development build flashed over within minutes is not pushed.
FLOOR_MIN_UPTIME_MS = 30 * 60 * 1000
STALE_S = 20 * 60
CRASH_WINDOW_S = 24 * 3600


def load(path=None):
    try:
        history = json.loads(Path(path or HISTORY).read_text())
    except (OSError, ValueError):
        return None
    return history if isinstance(history, dict) and isinstance(history.get('entries'), list) else None


def lasting(history, now=None):
    now = time.time() if now is None else now
    if history is None:
        return {'panel_monitor': 'Panelens hälsokontroll på Jarvis har ingen historik.'}
    out = {}
    entries = history['entries']
    if not entries or now - entries[-1].get('time', 0) > STALE_S:
        minutes = round((now - entries[-1]['time']) / 60) if entries else None
        out['panel_monitor'] = (f'Panelens hälsokontroll har inte körts på {minutes} minuter.' if minutes
                                else 'Panelens hälsokontroll har inte körts.')
    for alert in history.get('alerts') or []:
        kind, metric = alert.get('kind'), alert.get('metric', '')
        if kind == 'repeated_failures':
            out['panel_unreachable'] = f'Panelen svarar inte ({alert.get("count")} kontroller i rad, var 5:e minut).'
        elif kind == 'repeated_restarts':
            out['panel_restarts'] = f'Panelen har startat om {alert.get("count")} gånger på 15 minuter.'
        elif kind == 'wifi_disconnected':
            out['panel_wifi'] = 'Panelen har tappat Wi-Fi.'
        elif kind == 'possible_memory_leak':
            out[f'panel_leak_{metric}'] = f'Panelen kan ha en minnesläcka ({metric}).'
        elif kind in ('low_memory', 'below_minimum'):
            out[f'panel_memory_{metric}'] = f'Panelens minne är lågt ({metric}).'
    return out


def crashes(history, now=None, seen=()):
    """Crash boots seen in the last day, oldest first, that are not in seen."""
    now = time.time() if now is None else now
    found, boots = [], set()
    for entry in (history or {}).get('entries', []):
        status = entry.get('status') or {}
        boot, reason = status.get('boot_id'), str(status.get('reset_reason'))
        if not entry.get('ok') or not boot or boot in boots or now - entry.get('time', 0) > CRASH_WINDOW_S:
            continue
        boots.add(boot)
        if reason in CRASH_REASONS and boot not in seen:
            when = time.strftime('%H:%M', time.localtime(entry['time']))
            found.append((boot, f'Panelen {CRASH_REASONS[reason]} och startade om (sågs {when}).'))
    return found


def _size(field, value):
    return f'{value // 1024} KiB' if field == 'internal_min_free' else f'{value / 1e6:.1f} MB'


def memory_floors(history, now=None, seen=()):
    """Boots in the last day, up at least 30 minutes, whose lowest free memory fell under FLOORS; once per boot."""
    now = time.time() if now is None else now
    lowest, when = {}, {}
    for entry in (history or {}).get('entries', []):
        status = entry.get('status') or {}
        boot = status.get('boot_id')
        if not entry.get('ok') or not boot or boot in seen or now - entry.get('time', 0) > CRASH_WINDOW_S \
                or not isinstance(status.get('uptime_ms'), (int, float)) or status['uptime_ms'] < FLOOR_MIN_UPTIME_MS:
            continue
        for field, floor in FLOORS.items():
            value = status.get(field)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value < floor:
                low = lowest.setdefault(boot, {})
                low[field] = min(value, low.get(field, value))
                when.setdefault(boot, entry['time'])
    found = []
    for boot, low in lowest.items():
        parts = [('internt minne' if field == 'internal_min_free' else 'PSRAM') + ' ' + _size(field, value)
                 for field, value in sorted(low.items())]
        clock = time.strftime('%H:%M', time.localtime(when[boot]))
        found.append((boot, f'Panelens minne var nästan slut ({", ".join(parts)} kvar som lägst sedan start, sågs {clock}).'))
    return found
