#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xiaoke799
"""同意记录：把「已告知并取得同意」变成可举证的事实。

为什么不能只存 localStorage：那是客户端状态，卸载即失、无法自证。
本应用处理的是**不满十四周岁未成年人信息**（敏感个人信息），
《个人信息保护法》第三十一条要求取得监护人同意 —— 因此同意的时间、
政策版本、是否由监护人确认，都应当在服务端（此处为本地数据库）留痕。

留痕内容**不含任何儿童信息**，只是一条同意事实。
"""
from __future__ import annotations

import datetime

from flask import Blueprint, jsonify, request, url_for

from logger import get_logger
from utils import get_db

bp = Blueprint("consent", __name__, url_prefix="/api/consent")

# 政策版本号：内容有**实质性变更**时必须 +1，前端会据此重新征求同意
TERMS_VERSION = "1.2"
PRIVACY_VERSION = "2026-10-03"

DOC_BASE = "/legal"


def _doc_url(filename):
    """生成协议页面 URL。

    用 url_for 而不是拼字符串：fnOS 通过网关挂载时应用带一段路径前缀
    （形如 /app/babycare-fpk），硬编码 '/legal/xxx.html' 在网关下会 404。
    url_for 会自动带上脚本根，前端拿到即可直接用。
    """
    try:
        return url_for("static.legal", filename=filename)
    except Exception:  # noqa: BLE001
        return f"{DOC_BASE}/{filename}"


def _ensure_table():
    """幂等建表。放在函数里而不是 import 时，避免影响启动顺序。"""
    db = get_db()
    db.execute(
        """CREATE TABLE IF NOT EXISTS consent_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agreed_at TEXT NOT NULL,
            terms_version TEXT NOT NULL,
            privacy_version TEXT NOT NULL,
            is_guardian INTEGER DEFAULT 0,
            app_version TEXT DEFAULT '',
            withdrawn_at TEXT DEFAULT NULL
        )"""
    )
    db.commit()


@bp.route("/required", methods=["GET"])
def consent_required():
    """前端启动时问：需不需要征求同意 / 当前版本是什么。"""
    _ensure_table()
    db = get_db()
    row = db.execute(
        "SELECT terms_version, privacy_version, is_guardian, agreed_at, withdrawn_at"
        " FROM consent_records ORDER BY id DESC LIMIT 1"
    ).fetchone()
    latest = dict(row) if row else None
    # 撤回后（withdrawn_at 非空）视为需要重新同意
    active = bool(latest) and not latest.get("withdrawn_at") \
        and latest.get("terms_version") == TERMS_VERSION \
        and latest.get("privacy_version") == PRIVACY_VERSION
    return jsonify({
        "success": True,
        "data": {
            "need_consent": not active,
            "terms_version": TERMS_VERSION,
            "privacy_version": PRIVACY_VERSION,
            "terms_url": _doc_url("terms.html"),
            "privacy_url": _doc_url("privacy.html"),
            "agreed_at": (latest or {}).get("agreed_at"),
        },
    })


@bp.route("", methods=["POST"])
def consent_grant():
    """记录一次同意。必须显式声明自己是监护人。"""
    _ensure_table()
    data = request.get_json(silent=True) or {}
    # 没勾选监护人确认就不给记 —— 不能让前端漏传就默认通过
    if not data.get("is_guardian"):
        return jsonify({"success": False,
                        "message": "请确认您是儿童的监护人"}), 400
    db = get_db()
    db.execute(
        "INSERT INTO consent_records"
        " (agreed_at, terms_version, privacy_version, is_guardian, app_version)"
        " VALUES (?,?,?,?,?)",
        (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
         TERMS_VERSION, PRIVACY_VERSION, 1,
         str(data.get("app_version") or "")[:32]),
    )
    db.commit()
    get_logger(__name__).info("记录用户同意：协议 %s / 隐私 %s", TERMS_VERSION, PRIVACY_VERSION)
    return jsonify({"success": True, "data": {
        "terms_version": TERMS_VERSION, "privacy_version": PRIVACY_VERSION}})


@bp.route("", methods=["DELETE"])
def consent_withdraw():
    """撤回同意：标记撤回（不物理删除留痕，撤回本身也是事实）。

    撤回后前端应停用 AI 数据外发；下次启动会重新征求同意。
    """
    _ensure_table()
    db = get_db()
    db.execute(
        "UPDATE consent_records SET withdrawn_at = ? WHERE withdrawn_at IS NULL",
        (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),),
    )
    db.commit()
    return jsonify({"success": True, "message": "已撤回同意，AI 数据外发已停用"})
