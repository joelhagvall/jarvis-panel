"""The panel's quick timers (Hemmet's 5, 10 and 15 min and Avbryt, 2026-11-02).

The panel's timers are Home Assistant's own Assist timers, which belong to a device: a voice request carries the
panel's device, a button press does not. These two actions start and cancel the same timers through Home
Assistant's timer intents with the panel's device id, so they count down, chime and stop exactly as a spoken
"sätt en timer på tio minuter" does. Logged at INFO with the reason when Home Assistant refuses.

Hemmet's reminder page (2026-11-03) lists every timer with its own Avbryt and +1 min. Those name the timer by the id
the panel got from Home Assistant, so they go to Home Assistant's timer manager directly (an intent picks timers by
name or length, not id), and only for a timer that belongs to the panel.
"""
import logging

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import intent

try:                                    # Home Assistant's own timer manager (intent integration), 2026.10
    from homeassistant.components.intent.const import TIMER_DATA
    from homeassistant.components.intent.timers import TimerNotFoundError
except ImportError:                     # the free tests run without Home Assistant's components
    TIMER_DATA, TimerNotFoundError = "intent.timer", LookupError

from .wakeup import SATELLITE

_LOGGER = logging.getLogger(__name__)
MINUTES = (5, 10, 15)


def panel_device(hass):
    """The panel's device id, from its Assist satellite (the device the timers belong to)."""
    entry = er.async_get(hass).async_get(SATELLITE)
    if entry is None or not entry.device_id:
        raise HomeAssistantError(f"{SATELLITE} has no device; the panel is not set up in Home Assistant")
    return entry.device_id


async def start(hass, minutes):
    device = panel_device(hass)
    try:
        await intent.async_handle(hass, "jarvis_agent", intent.INTENT_START_TIMER, {"minutes": {"value": minutes}},
                                  language="sv", device_id=device)
    except intent.IntentError as err:
        _LOGGER.info("Panel timer of %s min refused: %s", minutes, err)
        raise HomeAssistantError(f"Timern startade inte: {err}") from err
    _LOGGER.info("Panel timer started: %s min", minutes)


async def cancel(hass):
    device = panel_device(hass)
    try:
        await intent.async_handle(hass, "jarvis_agent", intent.INTENT_CANCEL_ALL_TIMERS, {}, language="sv",
                                  device_id=device)
    except intent.IntentError as err:
        _LOGGER.info("Panel timers not cancelled: %s", err)
        raise HomeAssistantError(f"Timrarna avbröts inte: {err}") from err
    _LOGGER.info("Panel timers cancelled")


def _panel_timer(hass, timer_id):
    """The panel's own timer with this id, from Home Assistant's timer manager."""
    manager = hass.data.get(TIMER_DATA)
    timer = manager.timers.get(timer_id) if manager is not None else None
    if timer is None or timer.device_id != panel_device(hass):
        _LOGGER.info("Panel timer %s: not running on the panel", timer_id)
        raise HomeAssistantError("Den timern går inte längre")
    return manager, timer


def cancel_one(hass, timer_id):
    manager, timer = _panel_timer(hass, timer_id)
    try:
        manager.cancel_timer(timer_id)
    except TimerNotFoundError as err:
        raise HomeAssistantError("Den timern går inte längre") from err
    _LOGGER.info("Panel timer cancelled: %s (%s s left)", timer.name or timer_id, timer.seconds_left)


def add_time(hass, timer_id, seconds):
    manager, timer = _panel_timer(hass, timer_id)
    try:
        manager.add_time(timer_id, seconds)
    except TimerNotFoundError as err:
        raise HomeAssistantError("Den timern går inte längre") from err
    _LOGGER.info("Panel timer %s: %s s added", timer.name or timer_id, seconds)
