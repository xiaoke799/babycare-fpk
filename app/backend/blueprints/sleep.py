#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xiaoke799
"""
睡眠记录路由 Blueprint。
提供睡眠记录增删功能。
"""

import datetime
from flask import Blueprint, request, jsonify

from utils import get_db
from validators import validate_enum, validate_text_length, VALID_SLEEP_QUALITY, NOTE_MAX_LENGTH

bp = Blueprint("sleep", __name__)


def _safe_parse_dt(value):
    """安全解析日期时间，支持 ISO 格式和 %Y-%m-%d %H:%M:%S 格式"""
    if not value:
        return None
    try:
        return datetime.datetime.fromisoformat(str(value))
    except (ValueError, TypeError):
        try:
            return datetime.datetime.strptime(str(value).strip(), "%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            return None


@bp.route("/api/babies/<int:baby_id>/sleep", methods=["POST"])
def add_sleep_record(baby_id):
    """添加睡眠记录"""
    data = request.get_json()
    if not data or not data.get("start_time"):
        return jsonify({"success": False, "message": "请填写开始时间"}), 400

    # 校验睡眠质量枚举
    ok, quality, err = validate_enum(data.get("sleep_quality"), VALID_SLEEP_QUALITY, "睡眠质量")
    if not ok:
        return jsonify({"success": False, "message": err}), 400

    # 校验文本长度
    ok, note, err = validate_text_length(data.get("note"), NOTE_MAX_LENGTH, "备注")
    if not ok:
        return jsonify({"success": False, "message": err}), 400

    # 计算睡眠时长
    duration = data.get("duration_minutes")
    if not duration and data.get("end_time"):
        start = _safe_parse_dt(data["start_time"])
        end = _safe_parse_dt(data["end_time"])
        if start and end:
            duration = int((end - start).total_seconds() / 60)
        else:
            duration = None

    # 自动判定白天小憩（默认 6:00-18:00 区间为白天小睡）
    is_nap = data.get("is_nap")
    if is_nap is None and data.get("start_time"):
        start_dt = _safe_parse_dt(data["start_time"])
        if start_dt:
            is_nap = 1 if 6 <= start_dt.hour < 18 else 0
        else:
            is_nap = None

    db = get_db()
    db.execute(
        """INSERT INTO sleep_records (baby_id, start_time, end_time, duration_minutes, sleep_quality, is_nap, note)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (baby_id, data["start_time"], data.get("end_time"),
         duration, quality, is_nap, note),
    )
    db.commit()
    return jsonify({"success": True, "message": "记录已添加"})


@bp.route("/api/babies/<int:baby_id>/sleep", methods=["GET"])
def list_sleep_records(baby_id):
    """获取睡眠记录列表"""
    db = get_db()
    start = request.args.get("start", "")
    end = request.args.get("end", "")
    query = "SELECT * FROM sleep_records WHERE baby_id = ?"
    params = [baby_id]
    if start:
        query += " AND start_time >= ?"
        params.append(start)
    if end:
        query += " AND start_time <= ?"
        params.append(end + " 23:59:59")
    query += " ORDER BY start_time DESC LIMIT 100"
    rows = db.execute(query, params).fetchall()
    return jsonify({"success": True, "data": [dict(r) for r in rows]})


@bp.route("/api/sleep/<int:record_id>", methods=["DELETE"])
def delete_sleep_record(record_id):
    """删除睡眠记录"""
    db = get_db()
    db.execute("DELETE FROM sleep_records WHERE id = ?", (record_id,))
    db.commit()
    return jsonify({"success": True, "message": "已删除"})


@bp.route("/api/babies/<int:baby_id>/sleep/<int:record_id>", methods=["GET"])
def get_sleep_record(baby_id, record_id):
    """获取单条睡眠记录"""
    db = get_db()
    row = db.execute(
        "SELECT * FROM sleep_records WHERE id = ? AND baby_id = ?",
        (record_id, baby_id)
    ).fetchone()
    if not row:
        return jsonify({"success": False, "message": "记录不存在"}), 404
    return jsonify({"success": True, "data": dict(row)})


@bp.route("/api/babies/<int:baby_id>/sleep/<int:record_id>", methods=["PUT"])
def update_sleep_record(baby_id, record_id):
    """更新睡眠记录"""
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "message": "无效数据"}), 400

    # 校验睡眠质量枚举
    ok, quality, err = validate_enum(data.get("sleep_quality"), VALID_SLEEP_QUALITY, "睡眠质量")
    if not ok:
        return jsonify({"success": False, "message": err}), 400

    # 校验文本长度
    ok, note, err = validate_text_length(data.get("note"), NOTE_MAX_LENGTH, "备注")
    if not ok:
        return jsonify({"success": False, "message": err}), 400

    # 计算睡眠时长
    duration = data.get("duration_minutes")
    if not duration and data.get("end_time") and data.get("start_time"):
        start = _safe_parse_dt(data["start_time"])
        end = _safe_parse_dt(data["end_time"])
        if start and end:
            duration = int((end - start).total_seconds() / 60)

    # 自动判定白天小憩
    is_nap = data.get("is_nap")
    if is_nap is None and data.get("start_time"):
        start_dt = _safe_parse_dt(data["start_time"])
        if start_dt:
            is_nap = 1 if 6 <= start_dt.hour < 18 else 0

    db = get_db()
    db.execute(
        """UPDATE sleep_records SET start_time=?, end_time=?, duration_minutes=?, sleep_quality=?, is_nap=?, note=?
           WHERE id=? AND baby_id=?""",
        (data.get("start_time"), data.get("end_time"), duration, quality, is_nap, note, record_id, baby_id)
    )
    db.commit()
    return jsonify({"success": True, "message": "已更新"})
