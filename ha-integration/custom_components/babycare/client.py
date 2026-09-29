"""API client for BabyCare (babycare-fpk)."""
from __future__ import annotations

import asyncio
from typing import Any

from aiohttp import ClientError, ClientSession, ClientTimeout

from .const import DOMAIN


class BabyCareError(Exception):
    """Base error."""


class BabyCareAuthError(BabyCareError):
    """Unauthorized."""


class BabyCareClient:
    """Talk to babycare-fpk REST API with Bearer token."""

    def __init__(
        self,
        host: str,
        port: int,
        path: str,
        api_key: str,
        session: ClientSession,
    ) -> None:
        self.url = f"{host}:{port}{path}".rstrip("/")
        self.headers = {"Authorization": f"Bearer {api_key}"}
        self.session = session

    async def _request(self, method: str, url: str, **kwargs: Any) -> Any:
        timeout = ClientTimeout(total=15)
        try:
            async with asyncio.timeout(15):
                resp = await self.session.request(
                    method, url, headers=self.headers, timeout=timeout, **kwargs
                )
        except (TimeoutError, ClientError) as err:
            raise BabyCareError(str(err)) from err
        if resp.status == 401:
            raise BabyCareAuthError("API Token 无效")
        if resp.status >= 400:
            text = await resp.text()
            raise BabyCareError(f"HTTP {resp.status}: {text[:200]}")
        return await resp.json()

    async def async_get(self, path: str) -> Any:
        return await self._request("GET", f"{self.url}{path}")

    async def async_post(self, path: str, data: dict[str, Any]) -> Any:
        return await self._request(
            "POST", f"{self.url}{path}", json=data
        )

    async def async_get_babies(self) -> list[dict[str, Any]]:
        res = await self.async_get("/api/babies")
        return res.get("data") or []

    async def async_get_last_feeding(self, baby_id: int) -> dict[str, Any] | None:
        res = await self.async_get(f"/api/babies/{baby_id}/feeding")
        items = res.get("data") or []
        return items[0] if items else None

    async def async_get_last_sleep(self, baby_id: int) -> dict[str, Any] | None:
        res = await self.async_get(f"/api/babies/{baby_id}/sleep")
        items = res.get("data") or []
        return items[0] if items else None

    async def async_get_last_diaper(self, baby_id: int) -> dict[str, Any] | None:
        res = await self.async_get(f"/api/babies/{baby_id}/diaper")
        items = res.get("data") or []
        return items[0] if items else None

    async def async_get_last_growth(self, baby_id: int) -> dict[str, Any] | None:
        res = await self.async_get(f"/api/babies/{baby_id}/growth")
        items = res.get("data") or []
        return items[0] if items else None

    async def async_add_feeding(self, baby_id: int, data: dict[str, Any]) -> Any:
        return await self.async_post(f"/api/babies/{baby_id}/feeding", data)

    async def async_add_sleep(self, baby_id: int, data: dict[str, Any]) -> Any:
        return await self.async_post(f"/api/babies/{baby_id}/sleep", data)

    async def async_add_diaper(self, baby_id: int, data: dict[str, Any]) -> Any:
        return await self.async_post(f"/api/babies/{baby_id}/diaper", data)

    async def async_add_growth(self, baby_id: int, data: dict[str, Any]) -> Any:
        return await self.async_post(f"/api/babies/{baby_id}/growth", data)

    async def async_get_timers(self, baby_id: int) -> list[dict[str, Any]]:
        res = await self.async_get(f"/api/babies/{baby_id}/timers")
        return res.get("data") or []

    async def async_start_timer(self, baby_id: int, data: dict[str, Any] | None = None) -> Any:
        return await self.async_post(f"/api/babies/{baby_id}/timers", data or {})

    async def async_stop_timer(self, timer_id: int, create_record: bool = True) -> Any:
        return await self.async_post(
            f"/api/timers/{timer_id}/stop", {"create_record": create_record}
        )

    async def async_get_today_stats(self, baby_id: int) -> dict[str, Any] | None:
        """今日汇总（优先 /today-summary）。"""
        for path in (
            f"/api/babies/{baby_id}/today-summary",
            f"/api/babies/{baby_id}/feeding/stats",
        ):
            try:
                res = await self.async_get(path)
                return res.get("data") or res
            except BabyCareError:
                continue
        return None

    async def async_ping(self) -> bool:
        try:
            await self.async_get("/api/babies")
            return True
        except BabyCareError:
            return False

    async def async_delete_last(self, baby_id: int, kind: str) -> Any:
        """删除最近一条记录：kind = feeding|sleep|diaper|growth|pumping。"""
        getters = {
            "feeding": self.async_get_last_feeding,
            "sleep": self.async_get_last_sleep,
            "diaper": self.async_get_last_diaper,
            "growth": self.async_get_last_growth,
        }
        paths = {
            "feeding": "/api/feeding/{}",
            "sleep": "/api/sleep/{}",
            "diaper": "/api/diaper/{}",
            "growth": "/api/growth/{}",
            "pumping": "/api/pumping/{}",
        }
        if kind == "pumping":
            res = await self.async_get(f"/api/babies/{baby_id}/pumping")
            items = res.get("data") or []
            last = items[0] if items else None
        else:
            getter = getters.get(kind)
            if not getter:
                raise BabyCareError(f"未知类型 {kind}")
            last = await getter(baby_id)
        if not last or last.get("id") is None:
            raise BabyCareError("没有可删除的记录")
        return await self._request("DELETE", f"{self.url}" + paths[kind].format(last["id"]))
