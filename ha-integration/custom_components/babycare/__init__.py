"""BabyCare Home Assistant integration setup."""
from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN, PLATFORMS
from .coordinator import BabyCareCoordinator
from .services import async_setup_services


async def async_setup(hass: HomeAssistant, config: ConfigType) -> None:
    """Set up the BabyCare component.

    服务**在这里**注册，而不是 async_setup_entry —— 官方 action-setup 规则
    （docs/core/integration-quality-scale/rules/action-setup.md）明文要求：
    在 async_setup_entry 里注册会让服务只在 entry 已加载时才可用，
    于是「entry 还没加载时也能校验用户自动化」这个能力就没了。
    """
    async_setup_services(hass)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Set up BabyCare from a config entry."""
    coordinator = BabyCareCoordinator(hass, entry)

    # ⚠ 官方现行推荐是 entry.runtime_data（见 docs/core/entity.md 与
    # bluetooth_fetching_data.md），但它只在较新的 HA 上存在 ——
    # 在老版本上访问 entry.runtime_data 会 AttributeError，整个集成起不来。
    # 而本集成要支持各家 NAS 套件里可能塞着的不同 HA 版本，
    # 所以**两处都写**：新版本走 runtime_data，老版本退回 hass.data。
    # 删除 hass.data 那份之前请先确认目标 HA 版本 ≥ 引入 runtime_data 的版本。
    store = hass.data.setdefault(DOMAIN, {})
    store[entry.entry_id] = {"coordinator": coordinator, "client": coordinator.client}
    try:
        entry.runtime_data = coordinator
    except AttributeError:  # pragma: no cover - 老版本 HA
        pass

    await coordinator.async_config_entry_first_refresh()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unload_ok