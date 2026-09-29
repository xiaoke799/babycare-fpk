#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xiaoke799
"""
智能家居 / 外部集成 API Token。

Home Assistant 等外部客户端用 Authorization: Bearer <token> 或 Token <token> 访问。
- Token 只存哈希（PBKDF2-HMAC-SHA256），明文仅在生成时返回一次
- 与 fnOS 网关会话并行：有合法 Token 即视为已认证的集成用户（is_admin=False），
  写接口仍按路由上的 require_admin 控制；记录类接口（喂奶/睡眠/尿布/生长/计时）
  不挂 require_admin，因此集成 Token 可写入记录，但不能改设置或删宝宝。
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets

from utils import get_db

_TOKEN_PREFIX = "bcb_"
_ITERATIONS = 120_000


def _hash_token(token: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", token.encode("utf-8"), salt, _ITERATIONS)


def generate_api_token() -> str:
    """生成新 Token 并覆盖存储哈希。返回明文（仅此一次）。"""
    token = _TOKEN_PREFIX + secrets.token_urlsafe(32)
    salt = os.urandom(16)
    digest = _hash_token(token, salt)
    db = get_db()
    db.execute(
        "INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)",
        ("integration_api_token", salt.hex() + ":" + digest.hex()),
    )
    db.commit()
    return token


def revoke_api_token() -> None:
    db = get_db()
    db.execute("DELETE FROM app_settings WHERE key = ?", ("integration_api_token",))
    db.commit()


def has_api_token() -> bool:
    try:
        row = get_db().execute(
            "SELECT value FROM app_settings WHERE key = ?", ("integration_api_token",)
        ).fetchone()
        return bool(row and row[0])
    except Exception:
        return False


def verify_api_token(token: str | None) -> bool:
    if not token or not isinstance(token, str):
        return False
    token = token.strip()
    if not token.startswith(_TOKEN_PREFIX):
        return False
    try:
        row = get_db().execute(
            "SELECT value FROM app_settings WHERE key = ?", ("integration_api_token",)
        ).fetchone()
        if not row or not row[0]:
            return False
        salt_hex, digest_hex = row[0].split(":", 1)
        salt = bytes.fromhex(salt_hex)
        expect = bytes.fromhex(digest_hex)
        actual = _hash_token(token, salt)
        return hmac.compare_digest(actual, expect)
    except Exception:
        return False


def extract_bearer_token() -> str | None:
    """从 Authorization 头提取 Bearer/Token 凭证。"""
    from flask import request
    auth = request.headers.get("Authorization", "") or ""
    parts = auth.split(None, 1)
    if len(parts) != 2:
        return None
    scheme, value = parts[0].lower(), parts[1].strip()
    if scheme in ("bearer", "token"):
        return value
    return None
