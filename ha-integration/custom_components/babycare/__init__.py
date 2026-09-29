"""BabyCare Home Assistant integration setup."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import PLATFORMS
from .coordinator import BabyCareCoordinator
from .services import async_setup_services


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the BabyCare component."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up BabyCare from a config entry."""
    coordinator = BabyCareCoordinator(hass, entry)
    hass.data.setdefault(entry.domain, {})
    hass.data[entry.entry_id] = {
        "coordinator": coordinator,
        "client": coordinator.client,
    }
    # services 也挂在 DOMAIN 下，便于取 client
    hass.data.setdefault("babycare", {})
    hass.data["babycare"][entry.entry_id] = hass.data[entry.entry_id]

    await coordinator.async_config_entry_first_refresh()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data.get(entry.domain, {}).pop(entry.entry_id, None)
        hass.data.get("babycare", {}).pop(entry.entry_id, None)
    return unload_ok
