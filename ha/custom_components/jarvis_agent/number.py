"""How long Jarvis waits after speech: fast by default, adjustable for thinking pauses."""
import math

from homeassistant.components.number import NumberMode, RestoreNumber
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import VOICE_SILENCE_S
from .entity import jarvis_device


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    async_add_entities([JarvisSpeechPause(entry)])


class JarvisSpeechPause(RestoreNumber):
    _attr_name = "Jarvis talpaus"
    _attr_icon = "mdi:microphone-message"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_min_value = 0.3
    _attr_native_max_value = 2.0
    _attr_native_step = 0.1
    _attr_native_unit_of_measurement = "s"
    _attr_native_value = VOICE_SILENCE_S
    _attr_mode = NumberMode.SLIDER

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}-speech-pause"
        self._attr_device_info = jarvis_device(entry)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (previous := await self.async_get_last_number_data()) and previous.native_value is not None:
            value = previous.native_value
            if math.isfinite(value) and 0.3 <= value <= 2.0:
                self._attr_native_value = value
        self._entry.runtime_data.silence_seconds = self._attr_native_value

    async def async_set_native_value(self, value: float) -> None:
        if not math.isfinite(value) or not 0.3 <= value <= 2.0:
            raise ValueError("Talpaus måste vara mellan 0,3 och 2 sekunder")
        self._attr_native_value = value
        self._entry.runtime_data.silence_seconds = value
        self.async_write_ha_state()
