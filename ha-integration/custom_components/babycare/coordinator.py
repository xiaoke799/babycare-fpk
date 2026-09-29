"""Data update coordinator for BabyCare."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .client import BabyCareClient, BabyCareError
from .const import CONF_API_KEY, CONF_PATH, DEFAULT_PATH, DEFAULT_SCAN_INTERVAL, DOMAIN


@dataclass
class BabyCareData:
    coordinator: "BabyCareCoordinator"
    client: BabyCareClient


class BabyCareCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Poll babies and last records."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            logger=__import__("logging").getLogger(__name__),
            name=DOMAIN,
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        self.entry = entry
        self.client = BabyCareClient(
            entry.data[CONF_HOST],
            entry.data[CONF_PORT],
            entry.data.get(CONF_PATH, DEFAULT_PATH),
            entry.data[CONF_API_KEY],
            async_get_clientsession(hass),
        )

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            babies = await self.client.async_get_babies()
            result: dict[str, Any] = {"babies": babies, "last": {}, "timers": {}, "stats": {}}
            for baby in babies:
                bid = int(baby["id"])
                result["last"][bid] = {
                    "feeding": await self.client.async_get_last_feeding(bid),
                    "sleep": await self.client.async_get_last_sleep(bid),
                    "diaper": await self.client.async_get_last_diaper(bid),
                    "growth": await self.client.async_get_last_growth(bid),
                }
                result["timers"][bid] = await self.client.async_get_timers(bid)
                result["stats"][bid] = await self.client.async_get_today_stats(bid)
            return result
        except BabyCareError as err:
            raise UpdateFailed(str(err)) from err
