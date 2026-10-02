"""Sensors for BabyCare."""
from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import BabyCareCoordinator
from .entity import baby_device_info


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: BabyCareCoordinator = data["coordinator"]
    babies = coordinator.data.get("babies") or []
    entities: list[SensorEntity] = []
    for baby in babies:
        bid = int(baby["id"])
        entities.extend(
            [
                BabyCareLastSensor(coordinator, baby, "feeding", "最近喂奶"),
                BabyCareLastSensor(coordinator, baby, "sleep", "最近睡眠"),
                BabyCareLastSensor(coordinator, baby, "diaper", "最近尿布"),
                BabyCareLastSensor(coordinator, baby, "growth", "最近身高体重"),
                BabyCareStatsSensor(coordinator, baby, "today_feeding_count", "今日喂奶次数"),
                BabyCareStatsSensor(coordinator, baby, "today_feeding_ml", "今日奶量ml"),
            ]
        )
    async_add_entities(entities)


class BabyCareLastSensor(CoordinatorEntity[BabyCareCoordinator], SensorEntity):
    """Expose last record timestamp/type as state."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: BabyCareCoordinator,
        baby: dict[str, Any],
        kind: str,
        label: str,
    ) -> None:
        super().__init__(coordinator)
        self._baby = baby
        self._kind = kind
        self._bid = int(baby["id"])
        # has_entity_name=True 时 HA 显示「设备名 + 实体名」，所以这里只放实体部分，
        # 宝宝名由设备承载（否则会显示成「小甲 小甲 最近喂奶」）
        self._attr_name = label
        self._attr_unique_id = f"{DOMAIN}_{self._bid}_{kind}"
        self._attr_device_info = baby_device_info(baby)

    @property
    def native_value(self) -> str | None:
        last = (self.coordinator.data.get("last") or {}).get(self._bid, {}).get(self._kind)
        if not last:
            return None
        return str(
            last.get("start_time")
            or last.get("change_time")
            or last.get("record_date")
            or last.get("pump_time")
            or ""
        ) or None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        last = (self.coordinator.data.get("last") or {}).get(self._bid, {}).get(self._kind) or {}
        return {"baby_id": self._bid, **{k: v for k, v in last.items() if k != "id"}}


class BabyCareStatsSensor(CoordinatorEntity[BabyCareCoordinator], SensorEntity):
    """今日喂养统计（来自 /feeding/stats，字段名按响应自适应）。"""

    _attr_has_entity_name = True

    def __init__(self, coordinator, baby, kind, label) -> None:
        super().__init__(coordinator)
        self._bid = int(baby["id"])
        self._kind = kind
        self._attr_name = label
        self._attr_unique_id = f"{DOMAIN}_{self._bid}_{kind}"
        self._attr_device_info = baby_device_info(baby)

    @property
    def native_value(self) -> float | int | None:
        stats = (self.coordinator.data.get("stats") or {}).get(self._bid) or {}
        if self._kind == "today_feeding_count":
            for key in ("today_count", "count", "feeding_count", "today"):
                if key in stats:
                    try:
                        return int(stats[key])
                    except (TypeError, ValueError):
                        pass
            today = stats.get("today") or {}
            if isinstance(today, dict):
                return int(today.get("count") or 0)
            return None
        for key in ("today_ml", "today_amount", "amount", "total_ml"):
            if key in stats:
                try:
                    return float(stats[key])
                except (TypeError, ValueError):
                    pass
        today = stats.get("today") or {}
        if isinstance(today, dict):
            for key in ("ml", "amount", "total_amount"):
                if key in today:
                    try:
                        return float(today[key])
                    except (TypeError, ValueError):
                        pass
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        stats = (self.coordinator.data.get("stats") or {}).get(self._bid) or {}
        return {"baby_id": self._bid, "raw": stats}
