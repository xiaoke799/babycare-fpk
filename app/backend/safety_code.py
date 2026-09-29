#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xiaoke799
"""
删除保护安全码。

设计：
- 安全码本身不落盘，只存 PBKDF2-HMAC-SHA256 摘要 + 随机盐
- 存放在 app_settings 表（导出备份时该表会剥离凭据）
- 未设置安全码时，禁止删除宝宝，避免误触
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

_KEY_SALT = "delete_safety_code_salt"
_KEY_HASH = "delete_safety_code_hash"
_ITERATIONS = 120_000
_MIN_LEN = 4
_MAX_LEN = 64


def _hash_code(code: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", code.encode("utf-8"), salt.encode("utf-8"), _ITERATIONS
    ).hex()


def _get_app_setting(db, key: str):
    row = db.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def _set_app_setting(db, key: str, value: str) -> None:
    db.execute(
        "INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)",
        (key, value),
    )


def _del_app_setting(db, key: str) -> None:
    db.execute("DELETE FROM app_settings WHERE key = ?", (key,))


def is_set(db) -> bool:
    """是否已设置删除安全码。"""
    return bool(_get_app_setting(db, _KEY_HASH) and _get_app_setting(db, _KEY_SALT))


def set_code(db, code: str) -> tuple[bool, str]:
    """设置/修改安全码。成功返回 (True, 提示)。"""
    if not isinstance(code, str):
        return False, "安全码格式不正确"
    code = code.strip()
    if len(code) < _MIN_LEN:
        return False, f"安全码至少 {_MIN_LEN} 位"
    if len(code) > _MAX_LEN:
        return False, f"安全码不超过 {_MAX_LEN} 位"
    if any(ch.isspace() for ch in code):
        return False, "安全码不能包含空格"

    salt = secrets.token_hex(16)
    _set_app_setting(db, _KEY_SALT, salt)
    _set_app_setting(db, _KEY_HASH, _hash_code(code, salt))
    return True, "安全码已设置"


def clear_code(db) -> tuple[bool, str]:
    """清除安全码（清除后无法删除宝宝，需重新设置）。"""
    if not is_set(db):
        return False, "尚未设置安全码"
    _del_app_setting(db, _KEY_SALT)
    _del_app_setting(db, _KEY_HASH)
    return True, "安全码已清除"


def verify(db, code: str) -> tuple[bool, str]:
    """校验安全码。未设置时拒绝，要求先设置。"""
    if not is_set(db):
        return False, "尚未设置安全码，请先在「设置」中设置后再删除"
    if not isinstance(code, str) or not code:
        return False, "请输入安全码"

    salt = _get_app_setting(db, _KEY_SALT)
    expected = _get_app_setting(db, _KEY_HASH)
    actual = _hash_code(code, salt)
    if not hmac.compare_digest(actual, expected):
        return False, "安全码不正确"
    return True, "ok"
