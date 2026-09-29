"""Config flow for BabyCare."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .client import BabyCareAuthError, BabyCareClient, BabyCareError
from .const import CONF_API_KEY, CONF_PATH, DEFAULT_PATH, DEFAULT_PORT, DOMAIN


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, str]:
    session = async_get_clientsession(hass)
    client = BabyCareClient(
        data[CONF_HOST], data[CONF_PORT], data.get(CONF_PATH, DEFAULT_PATH),
        data[CONF_API_KEY], session,
    )
    await client.async_ping()
    babies = await client.async_get_babies()
    title = babies[0]["name"] if babies else "BabyCare"
    return {"title": title}


class BabyCareConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                info = await validate_input(self.hass, user_input)
            except BabyCareAuthError:
                errors["base"] = "invalid_auth"
            except BabyCareError:
                errors["base"] = "cannot_connect"
            except Exception:
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(user_input[CONF_HOST])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=info["title"], data=user_input)

        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default="http://127.0.0.1"): str,
                vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
                vol.Optional(CONF_PATH, default=DEFAULT_PATH): str,
                vol.Required(CONF_API_KEY): str,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)
