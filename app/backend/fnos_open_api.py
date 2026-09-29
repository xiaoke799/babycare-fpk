#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xiaoke799
"""
fnOS 开放 API 客户端（后端 Unix Socket）。

文档：api/calling.md、api/authorization/shared-access.md
- 仅应用服务端可调用：POST /api/v1/trimapp
  Unix Socket: /var/run/trim_open_gateway_apiscope.socket
- Token 由系统注入 TRIM_API_TOKEN，禁止持久化或下发前端
- 本模块用于查询「应用中心 → 授权目录」中管理员授权的共享路径
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
import uuid

SOCKET_PATH = "/var/run/trim_open_gateway_apiscope.socket"
APP_NAME = "babycare-fpk"


def _api_token() -> str:
    return os.environ.get("TRIM_API_TOKEN", "")


def call_trim_api(req: str, data: dict | None = None, timeout: float = 8.0) -> dict:
    """调用 fnOS 开放 API。返回 {code, msg, data}；网络失败时 code != 0。"""
    token = _api_token()
    if not token:
        return {"code": -1, "msg": "TRIM_API_TOKEN 未注入（非 fnOS 运行环境）", "data": {}}

    payload = {
        "reqId": uuid.uuid4().hex,
        "req": req,
        "appName": APP_NAME,
        "data": data or {},
    }
    body = json.dumps(payload).encode("utf-8")

    # Python 标准库访问 Unix Socket HTTP
    class _UnixHTTPConnection(urllib.request.HTTPConnection):
        def connect(self):
            import socket
            self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.sock.settimeout(timeout)
            self.sock.connect(SOCKET_PATH)

    try:
        conn = _UnixHTTPConnection("localhost")
        conn.request(
            "POST",
            "/api/v1/trimapp",
            body=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
        )
        resp = conn.getresponse()
        raw = resp.read()
        conn.close()
        parsed = json.loads(raw.decode("utf-8") or "{}")
        return {
            "code": parsed.get("code", -1),
            "msg": parsed.get("msg", ""),
            "data": parsed.get("data") or {},
        }
    except (OSError, ValueError, urllib.error.URLError) as e:
        return {"code": -2, "msg": f"调用 fnOS API 失败: {e}", "data": {}}


def get_shared_accessible_folders() -> list[str]:
    """查询管理员在「应用中心 → 授权目录」为本应用授权的路径列表。"""
    result = call_trim_api("trim.file.getSharedAccessibleFolders")
    if result.get("code") != 0:
        return []
    paths = (result.get("data") or {}).get("paths") or []
    return [str(p) for p in paths if p]


def del_shared_accessible_folder(path: str) -> bool:
    """删除一条应用共享授权路径（一般由应用中心设置页操作，应用内少用）。"""
    result = call_trim_api("trim.file.delSharedAccessibleFolder", {"path": path})
    return result.get("code") == 0 and bool((result.get("data") or {}).get("suc"))
