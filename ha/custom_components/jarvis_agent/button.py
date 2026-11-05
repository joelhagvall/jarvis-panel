"""Koppla om: the panel's button beside Ljud ut, also in Home Assistant. It disconnects the Bluetooth speaker on
Jarvis, connects it again and heals the sound path (server/agents/speaker.py reconnect, docs/bluetooth-speaker.md).
2026-11-01: the JBL played nothing while Spotify said "playing", and Joel wanted to fix it himself from the panel."""
import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import jarvis_device

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    async_add_entities([JarvisSpeakerReconnect(entry)])


class JarvisSpeakerReconnect(ButtonEntity):
    """button.jarvis_koppla_om_hogtalaren."""

    _attr_name = "Jarvis koppla om högtalaren"
    _attr_icon = "mdi:bluetooth-connect"

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}-speaker-reconnect"
        self._attr_device_info = jarvis_device(entry)

    async def async_press(self) -> None:
        # Jarvis answers 400 with the reason ("JBL Charge 4 svarar inte ...") when the speaker does not come back.
        try:
            now = await self._entry.runtime_data.speaker_reconnect()
        except HomeAssistantError as err:
            raise ServiceValidationError(str(err)) from err
        _LOGGER.info("Koppla om: %s", now.get("said") or now.get("error"))
        if now.get("error"):
            raise ServiceValidationError(now["error"])
