"""A link to a picture for the panel's background (Egen bild)."""

import logging

from homeassistant.components.text import TextEntity, TextMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import jarvis_device

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    """Add the link field."""
    async_add_entities([JarvisBackgroundLink(entry)], update_before_add=True)


class JarvisBackgroundLink(TextEntity):
    """text.jarvis_bakgrund_lank. Paste a picture's link: Jarvis downloads it, bakes the glass (about 8 s) and
    makes it the background as Egen bild. A link that is not a picture shows Jarvis's reason as an error."""

    _attr_name = "Jarvis bakgrund länk"
    _attr_icon = "mdi:link-variant"
    _attr_mode = TextMode.TEXT
    _attr_native_max = 255
    _attr_native_value = ""

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}-background-link"
        self._attr_device_info = jarvis_device(entry)

    async def async_update(self) -> None:
        try:
            now = await self._entry.runtime_data.background()
        except HomeAssistantError as err:
            _LOGGER.debug("No background from Jarvis: %s", err)
            return
        self._attr_native_value = now.get("link") or ""

    async def async_set_value(self, value: str) -> None:
        if not value.strip():
            return
        await self._entry.runtime_data.background(url=value.strip())
        self._attr_native_value = value.strip()
        self.async_write_ha_state()
