"""Sensors for BabyCare."""
from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorEntity,
    SensorStateClass,
)
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
    # 官方推荐入口是 entry.runtime_data（__init__.py 里已写入），
    # 但为兼容未提供 runtime_data 的老版本 HA，这里仍从 hass.data 取。
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
                # 今日换尿布次数：后端 today-summary 本就返回 diaper_count（实测过返回体），
                # 这里只是补上实体暴露
                BabyCareStatsSensor(coordinator, baby, "today_diaper_count", "今日换尿布次数"),
            ]
        )
    async_add_entities(entities)


class BabyCareLastSensor(CoordinatorEntity[BabyCareCoordinator], SensorEntity):
    """最近一次记录的时间 + 明细。

    ⚠ **刻意不设 device_class: TIMESTAMP**（曾经想加，后按文档核实后放弃）。
    HA 官方对 `SensorDeviceClass.TIMESTAMP` 的要求是：
    「Requires `native_value` to return a Python `datetime.datetime` object,
    **with time zone information**, or `None`」。

    而后端返回的是 **naive 本地时间字符串**（实测确认）：
      feeding `start_time` = '2026-10-03 10:00:00'
      diaper  `change_time` = '2026-10-03 11:00:00'
      growth  `record_date`  = '2026-10-03'   ← 这个还是**纯日期**
    三者都不带时区，硬加 device_class 会让 HA 按 UTC 解释、算出错的时间，
    而界面看不出异常 —— 反而比不加更糟。
    等后端改成带时区的 ISO8601（末尾 +08:00）再改，那时才是真收益。

    此外 TIMESTAMP 与 state_class 互斥（文档：MEASUREMENT 的 device_class
    不得为 TIMESTAMP），别两个一起加。
    """

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
    """今日喂养统计（来自 /today-summary）。

    字段名**实测过后端真实响应**再写死，不要靠猜：
    实测 `/api/babies/<id>/today-summary` 返回的是
      {"baby_id", "date", "feeding_count", "feeding_ml", "sleep_minutes", ...}
    之前这里猜的是 today_count / today_ml / total_ml…… 全不命中
    （`feeding_count` 恰好蒙对，`feeding_ml` 没有 ⇒「今日奶量」实体恒为 None，
   界面上却看不出任何异常，属于典型的静默失效）。
    下面的候选列表保留了旧 key 作兼容：万一将来后端换字段名仍能兜住，
    且每轮都刷新 CLASS_FIELDS，别的实体（比如新增的睡眠时长）直接复用。
    """

    _attr_has_entity_name = True

    # 每个实体真正关心的字段，**实测后端响应得出**。放在最前面，命中即用。
    _FIELDS = {
        "today_feeding_count": ("feeding_count",),
        "today_feeding_ml": ("feeding_ml",),
        "today_diaper_count": ("diaper_count",),
    }

    def __init__(self, coordinator, baby, kind, label) -> None:
        super().__init__(coordinator)
        self._bid = int(baby["id"])
        self._kind = kind
        self._attr_name = label
        self._attr_unique_id = f"{DOMAIN}_{self._bid}_{kind}"
        self._attr_device_info = baby_device_info(baby)
        # 单位：奶量必须带单位，否则 HA 显示成裸数字（官方文档要求
        # 数值型传感器给 native_unit_of_measurement，否则曲线图无意义）
        if kind == "today_feeding_ml":
            self._attr_native_unit_of_measurement = "mL"
        # 今日喂奶/换尿布次数是「统计量」，MEASUREMENT 而非 TOTAL_INCREASING：
        # 它们每天归零，不是单调递增计数器（文档 state_class 章节明确要求
        # 累计增量才用 TOTAL_INCREASING）
        if kind in ("today_feeding_count", "today_diaper_count"):
            self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> float | int | None:
        stats = (self.coordinator.data.get("stats") or {}).get(self._bid) or {}
        keys = self._FIELDS.get(self._kind) or ()
        for key in keys:
            if key in stats:
                try:
                    value = float(stats[key])
                except (TypeError, ValueError):
                    continue
                return int(value) if self._kind.endswith("_count") else value
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        stats = (self.coordinator.data.get("stats") or {}).get(self._bid) or {}
        return {"baby_id": self._bid, "raw": stats}
