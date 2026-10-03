"""Services for BabyCare Home Assistant integration."""
from __future__ import annotations

from datetime import datetime
from typing import Any

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .const import (
    ATTR_AMOUNT,
    ATTR_BABY_ID,
    ATTR_DIAPER_TYPE,
    ATTR_END,
    ATTR_HEIGHT,
    ATTR_NOTE,
    ATTR_RECORD_DATE,
    ATTR_SIDE,
    ATTR_SLEEP_QUALITY,
    ATTR_START,
    ATTR_TYPE,
    ATTR_WEIGHT,
    DOMAIN,
)


def async_setup_services(hass: HomeAssistant) -> None:
    async def _client_for(call: ServiceCall):
        """取任一已加载 config entry 的 client。

        服务注册在 async_setup（官方 action-setup 规则要求如此），
        所以调用时 entry 未必已加载 —— 这时必须抛 ServiceValidationError：
        它会以「这个动作用不了，原因是…」的形式显示给用户。
        抛 ValueError 的话 HA 只能报一句泛化的内部错误，用户看不出到底该做什么。
        """
        store = hass.data.get(DOMAIN) or {}
        for entry_data in store.values():
            return entry_data["client"] if isinstance(entry_data, dict) else entry_data.client
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="not_configured",
        )

    async def add_feeding(call: ServiceCall) -> None:
        client = await _client_for(call)
        baby_id = call.data[ATTR_BABY_ID]
        payload: dict[str, Any] = {
            "start_time": call.data.get(ATTR_START) or datetime.now().isoformat(timespec="minutes"),
            "feeding_type": call.data.get(ATTR_TYPE, "bottle"),
        }
        if call.data.get(ATTR_END):
            payload["end_time"] = call.data[ATTR_END]
        if call.data.get(ATTR_AMOUNT) is not None:
            payload["amount"] = call.data[ATTR_AMOUNT]
        if call.data.get(ATTR_SIDE):
            payload["side"] = call.data[ATTR_SIDE]
        if call.data.get(ATTR_NOTE):
            payload["note"] = call.data[ATTR_NOTE]
        await client.async_add_feeding(baby_id, payload)

    async def add_sleep(call: ServiceCall) -> None:
        client = await _client_for(call)
        baby_id = call.data[ATTR_BABY_ID]
        payload = {
            "start_time": call.data.get(ATTR_START) or datetime.now().isoformat(timespec="minutes"),
        }
        if call.data.get(ATTR_END):
            payload["end_time"] = call.data[ATTR_END]
        if call.data.get(ATTR_SLEEP_QUALITY):
            payload["sleep_quality"] = call.data[ATTR_SLEEP_QUALITY]
        if call.data.get(ATTR_NOTE):
            payload["note"] = call.data[ATTR_NOTE]
        await client.async_add_sleep(baby_id, payload)

    async def add_diaper(call: ServiceCall) -> None:
        client = await _client_for(call)
        await client.async_add_diaper(
            call.data[ATTR_BABY_ID],
            {
                "change_time": call.data.get(ATTR_START) or datetime.now().isoformat(timespec="minutes"),
                "diaper_type": call.data.get(ATTR_DIAPER_TYPE, "wet"),
                "note": call.data.get(ATTR_NOTE, ""),
            },
        )

    async def add_growth(call: ServiceCall) -> None:
        client = await _client_for(call)
        payload: dict[str, Any] = {
            "record_date": call.data.get(ATTR_RECORD_DATE) or datetime.now().date().isoformat(),
        }
        if call.data.get(ATTR_HEIGHT) is not None:
            payload["height"] = call.data[ATTR_HEIGHT]
        if call.data.get(ATTR_WEIGHT) is not None:
            payload["weight"] = call.data[ATTR_WEIGHT]
        if call.data.get(ATTR_NOTE):
            payload["note"] = call.data[ATTR_NOTE]
        await client.async_add_growth(call.data[ATTR_BABY_ID], payload)

    async def start_timer(call: ServiceCall) -> None:
        client = await _client_for(call)
        await client.async_start_timer(
            call.data[ATTR_BABY_ID],
            {
                "timer_type": call.data.get(ATTR_TYPE, "feeding"),
                "name": call.data.get(ATTR_NOTE, ""),
            },
        )

    async def stop_timer(call: ServiceCall) -> None:
        client = await _client_for(call)
        await client.async_stop_timer(
            int(call.data["timer_id"]),
            create_record=bool(call.data.get("create_record", True)),
        )

    async def delete_last_entry(call: ServiceCall) -> None:
        client = await _client_for(call)
        await client.async_delete_last(
            int(call.data[ATTR_BABY_ID]),
            str(call.data.get(ATTR_TYPE, "feeding")),
        )

    hass.services.async_register(
        DOMAIN, "add_feeding", add_feeding,
        schema=vol.Schema({
            vol.Required(ATTR_BABY_ID): cv.positive_int,
            vol.Optional(ATTR_START): cv.string,
            vol.Optional(ATTR_END): cv.string,
            vol.Optional(ATTR_TYPE): cv.string,
            vol.Optional(ATTR_AMOUNT): vol.Coerce(float),
            vol.Optional(ATTR_SIDE): cv.string,
            vol.Optional(ATTR_NOTE): cv.string,
        }),
    )
    hass.services.async_register(
        DOMAIN, "add_sleep", add_sleep,
        schema=vol.Schema({
            vol.Required(ATTR_BABY_ID): cv.positive_int,
            vol.Optional(ATTR_START): cv.string,
            vol.Optional(ATTR_END): cv.string,
            vol.Optional(ATTR_SLEEP_QUALITY): cv.string,
            vol.Optional(ATTR_NOTE): cv.string,
        }),
    )
    hass.services.async_register(
        DOMAIN, "add_diaper", add_diaper,
        schema=vol.Schema({
            vol.Required(ATTR_BABY_ID): cv.positive_int,
            vol.Optional(ATTR_START): cv.string,
            vol.Optional(ATTR_DIAPER_TYPE): cv.string,
            vol.Optional(ATTR_NOTE): cv.string,
        }),
    )
    hass.services.async_register(
        DOMAIN, "add_growth", add_growth,
        schema=vol.Schema({
            vol.Required(ATTR_BABY_ID): cv.positive_int,
            vol.Optional(ATTR_RECORD_DATE): cv.string,
            vol.Optional(ATTR_HEIGHT): vol.Coerce(float),
            vol.Optional(ATTR_WEIGHT): vol.Coerce(float),
            vol.Optional(ATTR_NOTE): cv.string,
        }),
    )
    hass.services.async_register(
        DOMAIN, "start_timer", start_timer,
        schema=vol.Schema({
            vol.Required(ATTR_BABY_ID): cv.positive_int,
            vol.Optional(ATTR_TYPE, default="feeding"): cv.string,
            vol.Optional(ATTR_NOTE): cv.string,
        }),
    )
    hass.services.async_register(
        DOMAIN, "stop_timer", stop_timer,
        schema=vol.Schema({
            vol.Required("timer_id"): cv.positive_int,
            vol.Optional("create_record", default=True): cv.boolean,
        }),
    )
    hass.services.async_register(
        DOMAIN, "delete_last_entry", delete_last_entry,
        schema=vol.Schema({
            vol.Required(ATTR_BABY_ID): cv.positive_int,
            vol.Optional(ATTR_TYPE, default="feeding"): vol.In(
                ["feeding", "sleep", "diaper", "growth", "pumping"]
            ),
        }),
    )
