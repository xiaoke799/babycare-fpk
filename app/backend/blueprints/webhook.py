#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xiaoke799
"""
入站 Webhook：给智能按钮 / 语音助手 / 自动化脚本一条「一句话记一笔」的通道。

为什么要有：Home Assistant 集成能覆盖绝大多数场景，但实体按钮（贴在床头柜上的
无线按钮）、自动化脚本、手表语音这些不在 HA 里的设备，也需要能往记录里写一笔。
Baby Buddy 这类应用都有这个入口。

安全设计（这是**对外开放**的网络入口，比集成 Token 敏感得多）：
  1. 独立 Token（bcw_ 前缀，PBKDF2 只存哈希），与集成 Token 分开存 ——
     吊销其中一个不影响另一个。
  2. 只允许「新增记录」，不提供删除 / 修改 / 改设置的任何入口。
  3. 事件类型与字段都是白名单常量，不接受调用方传列名，杜绝 SQL 注入面。
  4. 限流：单 IP 每分钟 N 次（内存计数，多 worker 下是每 worker 独立，
     属于「防误触和简单暴力」级别，不是防 DDoS）。
  5. 每次调用都落审计表（成功/失败都记），出问题能查是谁在调。
  6. 校验失败统一返回 401，不透露「token 存在但不对」这类信息。
"""
from __future__ import annotations

import datetime
import time

from flask import Blueprint, g, jsonify, request

from api_token import verify_webhook_token
from logger import get_logger
from utils import get_db, normalize_author
from validators import validate_date, validate_datetime

bp = Blueprint("webhook", __name__, url_prefix="/api/webhook")

# ---- 限流：每 IP 每分钟最多 30 次 ----
_RATE_LIMIT = 30
_RATE_WINDOW = 60
_hits: dict[str, list[float]] = {}

# ---- 事件白名单：key -> (表名, {外部字段: 内部列}, 必填字段) ----
# 刻意写死映射而不是拼 SQL 字符串：调用方永远碰不到列名。
_EVENTS: dict[str, dict] = {
    "feeding": {
        "table": "feeding_records",
        "time_col": "start_time",
        "fields": {
            "feeding_type": "feeding_type",
            "amount": "amount",
            "start_time": "start_time",
            "note": "note",
        },
        "required": ("feeding_type",),
        "defaults": {"feeding_type": "breast", "note": "来自 webhook"},
    },
    "diaper": {
        "table": "diaper_records",
        "time_col": "change_time",
        "fields": {
            "diaper_type": "diaper_type",
            "change_time": "change_time",
            "note": "note",
        },
        "required": ("diaper_type",),
        "defaults": {"diaper_type": "wet", "note": "来自 webhook"},
    },
    "sleep": {
        "table": "sleep_records",
        "time_col": "start_time",
        "fields": {
            "start_time": "start_time",
            "end_time": "end_time",
            "duration_minutes": "duration_minutes",
            "note": "note",
        },
        "required": ("start_time",),
        "defaults": {"note": "来自 webhook"},
    },
    "temperature": {
        "table": "temperature_records",
        "time_col": "measure_time",
        "fields": {
            "temperature": "temperature",
            "measure_time": "measure_time",
            "measure_method": "measure_method",
            "note": "note",
        },
        "required": ("temperature",),
        "defaults": {"measure_method": "armpit", "note": "来自 webhook"},
    },
}

# 数值字段：范围钳制，避免写入离谱的值（体温 999 度不是记录，是脏数据）
_NUMERIC_LIMITS = {
    "amount": (0, 5000),
    "duration_minutes": (0, 24 * 60),
    "temperature": (30, 45),
}


def _client_ip() -> str:
    # 走网关时 X-Forwarded-For 由网关注入；直连时退回 remote_addr
    fwd = request.headers.get("X-Forwarded-For", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.remote_addr or "unknown"


def _rate_limited(ip: str) -> bool:
    now = time.time()
    hits = [t for t in _hits.get(ip, []) if now - t < _RATE_WINDOW]
    if len(hits) >= _RATE_LIMIT:
        _hits[ip] = hits
        return True
    hits.append(now)
    _hits[ip] = hits
    return False


def _log_attempt(event: str, baby_id, ok: bool, reason: str, ip: str) -> None:
    """审计：成功失败都记。表不存在时不能反过来影响主流程，所以整段吞掉。"""
    try:
        db = get_db()
        db.execute(
            "INSERT INTO webhook_logs (created_at, event, baby_id, ok, reason, remote_addr)"
            " VALUES (?,?,?,?,?,?)",
            (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
             event, baby_id, 1 if ok else 0, reason[:200], ip),
        )
        db.commit()
    except Exception:  # noqa: BLE001
        pass


def _extract_token() -> str | None:
    auth = request.headers.get("Authorization", "") or ""
    parts = auth.split(None, 1)
    if len(parts) == 2 and parts[0].lower() in ("bearer", "token"):
        return parts[1].strip()
    # 智能按钮类设备往往没法自定义请求头，给一个专用头
    return (request.headers.get("X-BabyCare-Token", "") or "").strip() or None


@bp.route("/log", methods=["POST"])
def webhook_log():
    """记录一笔。请求体：{event, baby_id, ...事件字段}"""
    logger = get_logger(__name__)
    ip = _client_ip()

    if not verify_webhook_token(_extract_token()):
        _log_attempt("", None, False, "token 无效", ip)
        # 不区分「token 不存在」和「token 不对」，不给探测者反馈
        return jsonify({"success": False, "message": "Unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    event = str(data.get("event") or "").strip().lower()
    spec = _EVENTS.get(event)
    if not spec:
        _log_attempt(event[:30], None, False, "未知事件类型", ip)
        return jsonify({"success": False,
                        "message": "event 只能是 " + " / ".join(_EVENTS)}), 400

    if _rate_limited(ip):
        _log_attempt(event, None, False, "超过限流", ip)
        return jsonify({"success": False, "message": "请求过于频繁"}), 429

    try:
        baby_id = int(data.get("baby_id"))
    except (TypeError, ValueError):
        _log_attempt(event, None, False, "baby_id 非法", ip)
        return jsonify({"success": False, "message": "缺少合法的 baby_id"}), 400

    db = get_db()
    baby = db.execute("SELECT id FROM babies WHERE id = ?", (baby_id,)).fetchone()
    if not baby:
        _log_attempt(event, baby_id, False, "宝宝不存在", ip)
        return jsonify({"success": False, "message": "宝宝不存在"}), 404

    for key in spec["required"]:
        if data.get(key) in (None, ""):
            _log_attempt(event, baby_id, False, f"缺少必填 {key}", ip)
            return jsonify({"success": False, "message": f"缺少必填字段 {key}"}), 400

    cols = {"baby_id": baby_id, "author": normalize_author({"author": data.get("author")})}
    vals: list = [baby_id, cols["author"]]
    unknown = []
    for ext, col in spec["fields"].items():
        raw = data.get(ext, spec["defaults"].get(ext))
        if raw in (None, ""):
            raw = spec["defaults"].get(ext)
        if raw is None:
            continue
        if col in _NUMERIC_LIMITS:
            try:
                num = float(raw)
            except (TypeError, ValueError):
                _log_attempt(event, baby_id, False, f"{ext} 不是数字", ip)
                return jsonify({"success": False,
                                "message": f"{ext} 必须是数字"}), 400
            lo, hi = _NUMERIC_LIMITS[col]
            num = max(lo, min(hi, num))       # 钳制，超范围不报错但也不放行脏值
            raw = int(num) if col == "duration_minutes" else round(num, 1)
        elif "time" in col or col.endswith("_date"):
            # 时间列统一补秒；只有日期时走 validate_date。
            # 注意 validate_* 返回 (ok, 值, 错误) 三元组，不是裸值。
            ok, norm, _ = validate_datetime(raw)
            if not ok:
                ok2, norm2, _ = validate_date(raw)
                norm = norm2 if ok2 else str(raw)[:32]
            raw = norm
        else:
            raw = str(raw)[:200]              # 文本一律截断，别让调用方塞长文本
        if ext not in data:
            unknown.append(ext)                # 只是记录，不影响（默认值路径）
        cols[col] = raw
        vals.append(raw)

    # 时间列必填且没有库级默认值（feeding.start_time / diaper.change_time）。
    # webhook 的典型用法就是床头按一下记一笔，没带时间就记当下；
    # 直接丢给数据库会因 NOT NULL 报 500，那种报错对调用方毫无意义。
    if spec["time_col"] not in cols:
        cols[spec["time_col"]] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        vals.append(cols[spec["time_col"]])

    col_names = ", ".join(cols)
    placeholders = ", ".join("?" * len(cols))
    try:
        cur = db.execute(
            f"INSERT INTO {spec['table']} ({col_names}) VALUES ({placeholders})",  # noqa: S608
            tuple(vals))
        db.commit()
    except Exception as e:  # noqa: BLE001
        logger.error("[Webhook] 写入 %s 失败：%s", spec["table"], e)
        _log_attempt(event, baby_id, False, "写库失败", ip)
        return jsonify({"success": False, "message": "写入失败"}), 500

    _log_attempt(event, baby_id, True, f"记录 {cur.lastrowid}", ip)
    return jsonify({"success": True, "data": {"id": cur.lastrowid, "event": event}}), 201


@bp.route("/status", methods=["GET"])
def webhook_status():
    """给集成方探活用：只看是否配置、不泄露任何凭据。"""
    from api_token import has_webhook_token
    return jsonify({"success": True, "data": {
        "configured": has_webhook_token(),
        "events": sorted(_EVENTS),
        "rate_limit_per_minute": _RATE_LIMIT,
    }})
