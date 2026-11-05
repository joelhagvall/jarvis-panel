"""The panel's background, chosen in Home Assistant: NASA's picture of the day, a painting, a picture uploaded
to the media folder "hallpanel", or Egen bild from the link in text.jarvis_bakgrund_lank."""

from datetime import timedelta
import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import jarvis_device

_LOGGER = logging.getLogger(__name__)
OPTIONS = ["NASA", "Stjärnenatten", "Mandelblom"]   # until the server has answered
SCAN_INTERVAL = timedelta(seconds=10)               # a newly uploaded picture shows up as a choice within this
PANEL_ENTRIES = 8                                    # choice_list::MAX in styles/glass-v2/choice_list.h


def panel_options(options: list[str], current: str | None = None) -> dict:
    """The choices for the panel's list in Inställningar, one per line as LVGL's dropdown takes them: the first
    PANEL_ENTRIES, with the current choice in the last place when it is further down (an uploaded picture), or the
    list showed NASA as chosen (2026-11-03). The panel picks entry i of this list (gen.py pick_template)."""
    shown = list(options[:PANEL_ENTRIES])
    if current in options and current not in shown and shown:
        shown[-1] = current
    return {"panel_options": "\n".join(str(o).replace("\n", " ") for o in shown)}


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    """Add the background select."""
    async_add_entities([JarvisBackground(entry), JarvisSpeakerOutput(entry)], update_before_add=True)


class JarvisBackground(SelectEntity):
    """select.jarvis_bakgrund. The server keeps the choice and copies the picture to /local/hallpanel at once;
    the panel follows this entity and downloads the new picture a few seconds later."""

    _attr_name = "Jarvis bakgrund"
    _attr_icon = "mdi:image-frame"
    _attr_options = OPTIONS
    _attr_should_poll = True

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}-background"
        self._attr_device_info = jarvis_device(entry)

    async def async_update(self) -> None:
        try:
            now = await self._entry.runtime_data.background()
        except HomeAssistantError as err:
            _LOGGER.debug("No background from Jarvis: %s", err)
            self._attr_available = False
            return
        self._attr_available = True
        self._attr_options = list(now.get("options") or OPTIONS)
        self._attr_current_option = now.get("choice")
        self._attr_extra_state_attributes = panel_options(self._attr_options, self._attr_current_option)

    async def async_select_option(self, option: str) -> None:
        now = await self._entry.runtime_data.background(option)
        self._attr_current_option = now.get("choice", option)
        self._attr_extra_state_attributes = panel_options(self._attr_options, self._attr_current_option)
        self.async_write_ha_state()


class JarvisSpeakerOutput(SelectEntity):
    """select.jarvis_ljud_ut, the panel's Ljud ut setting: Panelen, or a Bluetooth speaker paired with Jarvis.
    Choosing a speaker connects it, and the radio follows (server/agents/speaker.py, docs/bluetooth-speaker.md)."""

    _attr_name = "Jarvis ljud ut"
    _attr_icon = "mdi:speaker-bluetooth"
    _attr_options = ["Panelen"]
    _attr_should_poll = True

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}-speaker"
        self._attr_device_info = jarvis_device(entry)

    def _take(self, now: dict) -> None:
        self._attr_options = list(now.get("options") or ["Panelen"])
        self._attr_current_option = now.get("choice") or "Panelen"
        self._attr_extra_state_attributes = panel_options(self._attr_options, self._attr_current_option)

    async def async_update(self) -> None:
        try:
            now = await self._entry.runtime_data.speaker()
        except HomeAssistantError as err:
            _LOGGER.debug("No speaker choice from Jarvis: %s", err)
            self._attr_available = False
            return
        self._attr_available = True
        self._take(now)

    async def async_select_option(self, option: str) -> None:
        # The server answers 400 with the reason ("JBL Charge 4 svarar inte ...") when a speaker cannot connect.
        try:
            now = await self._entry.runtime_data.speaker(option)
        except HomeAssistantError as err:
            # HA 2026.10's ESPHome notification path only returns service/validation failures.
            # A plain HomeAssistantError escapes that path and leaves the panel waiting for its timeout.
            raise ServiceValidationError(str(err)) from err
        self._take(now)
        self.async_write_ha_state()
