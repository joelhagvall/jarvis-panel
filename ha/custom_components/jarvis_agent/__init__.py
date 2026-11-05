"""Jarvis: the hallway panel's agent, voice and chat history in Home Assistant.

The panel only talks to Home Assistant. This integration passes its questions to the Jarvis server
on the same machine (127.0.0.1:8765), so no port has to be opened in Jarvis's firewall. It adds:

  conversation.jarvis   the agent (gpt-6-luna with the calendar), for an Assist pipeline; streams its reply
  tts.jarvis_mattias    Mattias (Home Assistant Cloud) with shortened pauses; speaks a streamed reply
                        sentence by sentence, so the panel starts playing before the reply is complete
  stt.jarvis_cloud      Home Assistant Cloud, adjustable pauses and up to 60 s per question
  number.jarvis_talpaus  silence before ending speech: 0.3–2 s, default 0.8, restored after restart
  sensor.jarvis_chat    the latest messages, for the panel's chat page after a reboot
  sensor.jarvis_hemmet  the home as short strings for the panel's Hemmet page and status line, every 15 s
  select.jarvis_bakgrund  the panel's background: NASA's picture of the day, a painting or an uploaded picture
  text.jarvis_bakgrund_lank  a link to a picture, which becomes the background "Egen bild"
  jarvis_agent.warm     action the panel calls on the wake word, so the agent is warm in time
  jarvis_agent.clear    action behind Rensa on the panel: Jarvis forgets the conversation
  jarvis_agent.timer_start, timer_cancel  Hemmet's quick timers: HA's Assist timers on the panel (panel_timers.py);
                        timer_cancel with timer_id and timer_add stop or extend one from the reminder page
  jarvis_agent.reminder_cancel  Ta bort on Hemmet's reminder page: Jarvis cancels that reminder
  (no entity)           turns the Bluetooth speaker down while the panel listens and answers (speaker_duck.py)
"""

import logging

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.typing import ConfigType
import voluptuous as vol

from . import panel_timers
from .client import JarvisClient
from .cloud_speech import JarvisCloudSpeechView
from .media_artwork import JarvisMediaArtworkView
from .wakeup import WakeupController
from .speaker_duck import SoloistDuck, SpeakerDuck
from .const import CONF_TOKEN, CONF_URL, DOMAIN, SIGNAL_NEW_MESSAGE

PLATFORMS = [Platform.CONVERSATION, Platform.TTS, Platform.STT, Platform.SENSOR, Platform.SELECT, Platform.TEXT,
             Platform.NUMBER, Platform.BUTTON]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register jarvis_agent.warm and jarvis_agent.clear."""

    async def warm(call: ServiceCall) -> None:
        for entry in hass.config_entries.async_entries(DOMAIN):
            if entry.state is ConfigEntryState.LOADED:
                await entry.runtime_data.warm()

    async def clear(call: ServiceCall) -> None:
        for entry in hass.config_entries.async_entries(DOMAIN):
            if entry.state is ConfigEntryState.LOADED:
                await entry.runtime_data.clear()
        async_dispatcher_send(hass, SIGNAL_NEW_MESSAGE)     # the chat sensor empties at once

    hass.http.register_view(JarvisCloudSpeechView(hass))
    hass.http.register_view(JarvisMediaArtworkView(hass))
    hass.services.async_register(DOMAIN, "warm", warm)
    hass.services.async_register(DOMAIN, "clear", clear)
    async def wakeup_action(call: ServiceCall) -> None:
        for entry in hass.config_entries.async_entries(DOMAIN):
            if entry.state is ConfigEntryState.LOADED:
                controller = entry.runtime_data.wakeups
                if call.service == "wakeup_preview":
                    await controller.preview()
                elif call.service == "wakeup_snooze":
                    await controller.snooze()
                elif call.service == "wakeup_stop":
                    await controller.stop()
    for name in ("wakeup_preview", "wakeup_snooze", "wakeup_stop"):
        hass.services.async_register(DOMAIN, name, wakeup_action)
    # Hemmet's quick timers: Home Assistant's own Assist timers on the panel's device (panel_timers.py).
    async def timer_start(call: ServiceCall) -> None:
        await panel_timers.start(hass, call.data["minutes"])
    async def timer_cancel(call: ServiceCall) -> None:
        if call.data.get("timer_id"):            # one timer, from Hemmet's reminder page
            panel_timers.cancel_one(hass, call.data["timer_id"])
        else:
            await panel_timers.cancel(hass)
    async def timer_add(call: ServiceCall) -> None:
        panel_timers.add_time(hass, call.data["timer_id"], call.data["seconds"])
    hass.services.async_register(DOMAIN, "timer_start", timer_start,
        schema=vol.Schema({vol.Required("minutes"): vol.All(vol.Coerce(int), vol.Range(1, 180))}))
    hass.services.async_register(DOMAIN, "timer_cancel", timer_cancel,
        schema=vol.Schema({vol.Optional("timer_id"): vol.All(str, vol.Length(max=64))}))
    hass.services.async_register(DOMAIN, "timer_add", timer_add,
        schema=vol.Schema({vol.Required("timer_id"): vol.All(str, vol.Length(min=1, max=64)),
                           vol.Required("seconds"): vol.All(vol.Coerce(int), vol.Range(1, 3600))}))
    # Ta bort on Hemmet's reminder page: Jarvis cancels it, and the page follows at once (sensor.jarvis_hemmet).
    async def reminder_cancel(call: ServiceCall) -> None:
        for entry in hass.config_entries.async_entries(DOMAIN):
            if entry.state is ConfigEntryState.LOADED:
                cancelled = await entry.runtime_data.reminder_cancel(call.data["id"])
                _LOGGER.info("Panel reminder %s: %s", call.data["id"], "cancelled" if cancelled else "no longer pending")
                home = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}-home")
                if home:
                    await hass.services.async_call("homeassistant", "update_entity", {"entity_id": home}, blocking=True)
                return
        raise HomeAssistantError("Jarvis is not ready")
    hass.services.async_register(DOMAIN, "reminder_cancel", reminder_cancel,
        schema=vol.Schema({vol.Required("id"): vol.All(vol.Coerce(int), vol.Range(min=1))}))
    async def media_control(call: ServiceCall) -> None:
        for entry in hass.config_entries.async_entries(DOMAIN):
            if entry.state is ConfigEntryState.LOADED:
                await entry.runtime_data.media.control(call.data["action"])
                return
        raise HomeAssistantError("Jarvis music controls are not ready")
    hass.services.async_register(DOMAIN, "media_control", media_control,
        schema=vol.Schema({vol.Required("action"): vol.In(("toggle", "stop", "next", "previous"))}))
    async def turntable_speed(call: ServiceCall) -> dict:
        """The panel turntable's 33/45 knob: Spotify on the JBL at its pitch (server/speed.py); no rpm toggles."""
        for entry in hass.config_entries.async_entries(DOMAIN):
            if entry.state is ConfigEntryState.LOADED:
                media = getattr(entry.runtime_data, "media", None)
                if media is None:
                    break
                return {"rpm": await media.speed(call.data.get("rpm"))}
        raise HomeAssistantError("Jarvis is not ready")
    hass.services.async_register(DOMAIN, "turntable_speed", turntable_speed, supports_response=SupportsResponse.OPTIONAL,
        schema=vol.Schema({vol.Optional("rpm"): vol.In((33, 45))}))
    async def speaker_volume(call: ServiceCall) -> dict:
        """The Bluetooth speaker's volume for Jarvis's volume tool (server/agents/volume.py), past the ducking."""
        for entry in hass.config_entries.async_entries(DOMAIN):
            if entry.state is ConfigEntryState.LOADED:
                # Spotify on the speaker has its own volume in Soloist; the radio's is MPD's.
                media = getattr(entry.runtime_data, "media", None)
                duck = (entry.runtime_data.spotify_duck if getattr(media, "_target", None) == "spotify"
                        else entry.runtime_data.speaker_duck)
                try:
                    level = await duck.volume(call.data.get("level"), call.data.get("step"))
                except (OSError, TimeoutError) as err:
                    raise HomeAssistantError(f"The Bluetooth speaker has no volume now: {err}") from err
                return {"volume": level}
        raise HomeAssistantError("Jarvis is not ready")
    hass.services.async_register(DOMAIN, "speaker_volume", speaker_volume, supports_response=SupportsResponse.OPTIONAL,
        schema=vol.Schema({vol.Optional("level"): vol.All(vol.Coerce(int), vol.Range(0, 100)),
                           vol.Optional("step"): vol.All(vol.Coerce(int), vol.Range(-100, 100))}))
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the agent, the voice and the chat sensor."""
    entry.runtime_data = JarvisClient(hass, entry.data[CONF_URL], entry.data[CONF_TOKEN])
    entry.runtime_data.wakeups = WakeupController(hass, entry.runtime_data, entry.entry_id)
    await entry.runtime_data.wakeups.load()
    entry.async_on_unload(entry.runtime_data.wakeups.close)
    duck = SpeakerDuck(hass)            # the Bluetooth speaker goes quiet while the panel listens and answers
    entry.runtime_data.speaker_duck = duck
    duck.start()
    entry.async_on_unload(duck.close)
    spotify_duck = SoloistDuck(hass)    # and Spotify on it (Soloist), the same way
    entry.runtime_data.spotify_duck = spotify_duck
    spotify_duck.start()
    entry.async_on_unload(spotify_duck.close)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.runtime_data.wakeups.start_when_ready()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload the platforms."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
