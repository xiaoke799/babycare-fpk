"""Shared entity helpers for BabyCare.

HA 规范：集成应该把实体挂到设备上（Gold 级 devices 规则），否则实体在
HA 界面里是一堆散条目，用户看不到"这是哪个宝宝的数据"。

这里按「一个宝宝 = 一个设备」注册：identifiers 用 (domain, baby_<id>)，
跨 HA 重启/重新配置保持稳定，所以设备不会重复创建。
"""
from __future__ import annotations

from typing import Any

from homeassistant.helpers.entity import DeviceInfo

from .const import DEFAULT_NAME, DOMAIN


def baby_device_info(baby: dict[str, Any]) -> DeviceInfo:
    """Return the DeviceInfo for one baby profile."""
    bid = int(baby["id"])
    return DeviceInfo(
        identifiers={(DOMAIN, f"baby_{bid}")},
        name=baby.get("name") or f"宝宝 {bid}",
        manufacturer=DEFAULT_NAME,
        model="BabyCare",
    )
