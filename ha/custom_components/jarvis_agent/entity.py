"""Shared device for the Jarvis entities."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo

from .const import DOMAIN


def jarvis_device(entry: ConfigEntry) -> DeviceInfo:
    """One service device that groups the agent, the voice and the chat sensor."""
    return DeviceInfo(identifiers={(DOMAIN, entry.entry_id)}, name="Jarvis", manufacturer="hallpanel",
                      model="Jarvis server", entry_type=DeviceEntryType.SERVICE)
