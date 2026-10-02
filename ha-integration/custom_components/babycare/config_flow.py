"""Config flow for BabyCare."""
from __future__ import annotations

from collections.abc import Mapping
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
    """Handle the UI configuration flow."""

    VERSION = 1
    MINOR_VERSION = 1

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

    # ---- 重新认证 ----
    # Token 在育儿宝里重新生成或吊销后，旧 Token 会 401。协调器把它转成
    # ConfigEntryAuthFailed，HA 就会走到这里让用户只重填 Token，
    # 不用把整个集成删掉重加（重加会丢掉已有的实体 ID 和自动化引用）。

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> FlowResult:
        """Token 失效，由 HA 自动发起重新认证。"""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        if entry is None:
            return self.async_abort(reason="reauth_failed")

        if user_input is not None:
            data = {**entry.data, CONF_API_KEY: user_input[CONF_API_KEY]}
            try:
                await validate_input(self.hass, data)
            except BabyCareAuthError:
                errors["base"] = "invalid_auth"
            except BabyCareError:
                errors["base"] = "cannot_connect"
            except Exception:
                errors["base"] = "unknown"
            else:
                self.hass.config_entries.async_update_entry(entry, data=data)
                await self.hass.config_entries.async_reload(entry.entry_id)
                return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_API_KEY): str}),
            errors=errors,
            description_placeholders={"host": entry.data.get(CONF_HOST, "")},
        )
