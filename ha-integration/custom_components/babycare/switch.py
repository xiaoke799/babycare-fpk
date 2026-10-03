"""Switch platform — real BabyCare timers."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import BabyCareCoordinator
from .entity import baby_device_info


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    # 同 sensor.py：官方推荐 entry.runtime_data，此处退回 hass.data 以兼容老 HA
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: BabyCareCoordinator = data["coordinator"]
    babies = coordinator.data.get("babies") or []
    async_add_entities(
        BabyCareTimerSwitch(coordinator, baby, timer_type)
        for baby in babies
        for timer_type in ("feeding", "sleep")
    )


class BabyCareTimerSwitch(SwitchEntity):
    """开：启动育儿宝计时器；关：停止活动计时器。"""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: BabyCareCoordinator,
        baby: dict[str, Any],
        timer_type: str,
    ) -> None:
        self._coordinator = coordinator
        self._baby = baby
        self._bid = int(baby["id"])
        self._timer_type = timer_type
        label = "喂奶计时" if timer_type == "feeding" else "睡眠计时"
        # 设备名 = 宝宝名，实体名只放实体部分（见 sensor.py 同处注释）
        self._label = label
        self._attr_name = label
        self._attr_unique_id = f"{DOMAIN}_{self._bid}_{timer_type}_timer"
        self._attr_device_info = baby_device_info(baby)
        self._timer_id: int | None = None

    def _active_timer(self) -> dict[str, Any] | None:
        timers = (self._coordinator.data.get("timers") or {}).get(self._bid) or []
        for t in timers:
            if t.get("is_active") in (1, True, "1") and t.get("timer_type") == self._timer_type:
                return t
        return None

    @property
    def is_on(self) -> bool:
        return self._active_timer() is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        t = self._active_timer() or {}
        return {
            "baby_id": self._bid,
            "timer_type": self._timer_type,
            "timer_id": t.get("id"),
            "start_time": t.get("start_time"),
        }

    async def async_turn_on(self, **kwargs) -> None:
        if self._active_timer():
            return
        res = await self._coordinator.client.async_start_timer(
            self._bid,
            {
                "timer_type": self._timer_type,
                # 计时器名字要带宝宝名，否则多宝宝时在育儿宝里分不清是谁的
                "name": f"{self._baby.get('name', '宝宝')} {self._label}",
            },
        )
        data = res.get("data") if isinstance(res, dict) else None
        if data and data.get("id"):
            self._timer_id = int(data["id"])
        await self._coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        t = self._active_timer()
        if not t:
            return
        # 停止时写入喂奶/睡眠记录（对齐 Baby Buddy timer 联动）
        await self._coordinator.client.async_stop_timer(int(t["id"]), create_record=True)
        await self._coordinator.async_request_refresh()
