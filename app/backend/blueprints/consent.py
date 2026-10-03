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
import os

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


# ==================== 安装向导同意的落库 ====================
# 向导勾选结果只在**安装阶段**以环境变量存在（cmd/main 启动后端时不继承），
# 所以 install_callback 先把它落成数据目录下的标记文件，后端启动时再读走。
# 为什么不直接写在脚本里插数据库：那时数据库可能还没建好/没迁移完，
# 而且 CLI 与 Web 两个写入口容易各写各的。

_MARKER_NAME = ".wizard_consent"


def consume_wizard_consent(data_dir):
    """读取安装向导的同意标记并写入 consent_records。返回记录 id 或 None。

    带协议版本号校验：卸载重装时若协议已改版，**不认旧标记**，
    要求用户重新同意（否则用户看到的协议和实际同意的不一致）。
    """
    if not data_dir:
        return None
    marker = os.path.join(data_dir, _MARKER_NAME)
    if not os.path.isfile(marker):
        return None
    rid = None          # 版本不匹配时不会有记录 id，末尾统一 return
    info = {}
    try:
        with open(marker, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if "=" in line:
                    k, v = line.split("=", 1)
                    info[k.strip()] = v.strip()
    except Exception as e:  # noqa: BLE001
        get_logger(__name__).warning("读取向导同意标记失败：%s", e)

    if info.get("agreed_at") and info.get("terms_version") == TERMS_VERSION \
            and info.get("privacy_version") == PRIVACY_VERSION:
        try:
            _ensure_table()
            db = get_db()
            cur = db.execute(
                "INSERT INTO consent_records"
                " (agreed_at, terms_version, privacy_version, is_guardian, app_version)"
                " VALUES (?,?,?,?,?)",
                (info["agreed_at"], TERMS_VERSION, PRIVACY_VERSION, 1, "install_wizard"),
            )
            db.commit()
            get_logger(__name__).info("已记录安装向导中的监护人同意")
            rid = cur.lastrowid
        except Exception as e:  # noqa: BLE001
            get_logger(__name__).warning("写入向导同意记录失败：%s", e)
            return None
    else:
        get_logger(__name__).info(
            "向导同意标记缺失或版本不匹配（标记版本 %s / %s，需重新同意）",
            info.get("terms_version"), info.get("privacy_version"))
    # 无论是否采信都清掉标记：采信过就不再重复写；版本不匹配则要求重新征求
    try:
        os.remove(marker)
    except OSError:
        pass
    return rid if info.get("agreed_at") else None
