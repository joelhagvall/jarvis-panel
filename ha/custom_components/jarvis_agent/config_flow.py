"""Set up Jarvis from the UI: the server's address and token."""

from typing import Any

try:                          # Home Assistant 2026 schemas
    import probatio as vol
except ImportError:           # older Home Assistant
    import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.exceptions import HomeAssistantError

from .client import JarvisAuthError, JarvisClient
from .const import CONF_TOKEN, CONF_URL, DEFAULT_URL, DOMAIN


class JarvisConfigFlow(ConfigFlow, domain=DOMAIN):
    """Ask for the address and token, and check them with /warm."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._async_abort_entries_match({CONF_URL: user_input[CONF_URL]})
            try:
                await JarvisClient(self.hass, user_input[CONF_URL], user_input[CONF_TOKEN]).warm()
            except JarvisAuthError:
                errors["base"] = "invalid_auth"
            except HomeAssistantError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(title="Jarvis", data=user_input)
        schema = vol.Schema({vol.Required(CONF_URL, default=DEFAULT_URL): str, vol.Required(CONF_TOKEN): str})
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)
