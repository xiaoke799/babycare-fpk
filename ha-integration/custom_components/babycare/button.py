"""Quick-record buttons for BabyCare.

快捷记录按钮的设计要点：
RESTful switch 得靠「开 2 秒自动回弹」模拟瞬时动作——那是 RESTful 平台
没有 button 实体的妥协。这里是标准 custom component，直接用原生
ButtonEntity：async_press 触发一次记录，无状态、无需回弹计时器。

两个按钮刻意选了「无参数即完整」的记录语义（不替用户编数据）：
- 记亲喂：feeding_type=breast，亲喂本就没有奶量语义，start_time=现在
- 记换尿布：diaper_type=wet，一次换尿布就是一次，无需额外字段
奶瓶喂必须带量，不适合无参按钮 —— 请用 babycare.add_feeding 服务
（可指定 amount / feeding_type）或喂奶计时开关（停止时自动生成记录）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import BabyCareCoordinator
from .const import DOMAIN
from .entity import baby_device_info

# kind → (实体名, 图标, 说明)。加新按钮先想清楚「无参数能否构成一条完整记录」。
QUICK_BUTTONS: dict[str, dict[str, Any]] = {
    "quick_feeding_breast": {
        "label": "记亲喂",
        "icon": "mdi:baby-bottle-outline",
        "endpoint": "feeding",
        "payload_builder": lambda now: {
            "start_time": now,
            "feeding_type": "breast",
        },
    },
    "quick_diaper_wet": {
        "label": "记换尿布",
        "icon": "mdi:diaper",
        "endpoint": "diaper",
        "payload_builder": lambda now: {
            "change_time": now,
            "diaper_type": "wet",
        },
    },
}


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    """每宝宝 × 每个快捷按钮一个 ButtonEntity。"""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: BabyCareCoordinator = data["coordinator"]
    babies = (coordinator.data.get("babies")) or []
    async_add_entities(
        BabyCareQuickButton(coordinator, baby, kind)
        for baby in babies
        for kind in QUICK_BUTTONS
    )


class BabyCareQuickButton(CoordinatorEntity[BabyCareCoordinator], ButtonEntity):
    """按一下 = 写一条记录到育儿宝（等价于在 App 里手点一次快捷记录）。"""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: BabyCareCoordinator,
        baby: dict[str, Any],
        kind: str,
    ) -> None:
        super().__init__(coordinator)
        self._baby = baby
        self._bid = int(baby["id"])
        self._kind = kind
        spec = QUICK_BUTTONS[kind]
        # has_entity_name=True：宝宝名由设备承载，这里只放实体部分（见 sensor.py 同处注释）
        self._attr_name = spec["label"]
        self._attr_unique_id = f"{DOMAIN}_{self._bid}_{kind}"
        self._attr_device_info = baby_device_info(baby)
        self._attr_icon = spec["icon"]

    async def async_press(self) -> None:
        """按下：写一条记录，然后刷新协调器让「最近喂奶/尿布」传感器立即跟上。"""
        spec = QUICK_BUTTONS[self._kind]
        # dt_util.now() = HA 所在机器的本地时区；后端库存本地 naive 时间字符串
        # 'YYYY-MM-DD HH:MM:SS'（见 app.js/migrations 各时间列的统一约定），
        # 这里用同样格式，别用 toISOString（UTC 会差 8 小时）。
        now = datetime.now(dt_util.get_default_timezone()).strftime("%Y-%m-%d %H:%M:%S")
        payload = spec["payload_builder"](now)
        if spec["endpoint"] == "feeding":
            res = await self.coordinator.client.async_add_feeding(self._bid, payload)
        else:
            res = await self.coordinator.client.async_add_diaper(self._bid, payload)
        if isinstance(res, dict) and res.get("success") is False:
            # 后端明确拒绝（校验不过等）：抛错让 HA 面板弹出失败提示，
            # 静默吞掉会让用户以为记上了 —— 假成功比失败更糟。
            raise HomeAssistantError(res.get("message") or "记录失败")
        await self.coordinator.async_request_refresh()
