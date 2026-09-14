#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xiaoke799
"""
喂奶与吸奶记录路由 Blueprint。
提供喂奶记录增删、吸奶记录管理、统计等功能。
"""

import datetime
from flask import Blueprint, request, jsonify

from utils import get_db
from validators import (
    validate_amount, validate_duration, validate_enum,
    validate_text_length, truncate_text,
    VALID_FEEDING_TYPES, NOTE_MAX_LENGTH, NAME_MAX_LENGTH,
)

bp = Blueprint("feeding", __name__)


@bp.route("/api/babies/<int:baby_id>/feeding", methods=["POST"])
def add_feeding_record(baby_id):
    """添加喂奶记录"""
    data = request.get_json()
    if not data or not data.get("start_time") or not data.get("feeding_type"):
        return jsonify({"success": False, "message": "请填写开始时间和喂养类型"}), 400

    # 校验喂养类型
    ok, feeding_type, err = validate_enum(data["feeding_type"], VALID_FEEDING_TYPES, "喂养类型")
    if not ok:
        return jsonify({"success": False, "message": err}), 400

    # 校验数值字段
    ok, amount, err = validate_amount(data.get("amount"), "奶量")
    if not ok:
        return jsonify({"success": False, "message": err}), 400

    ok, left_dur, err = validate_duration(data.get("left_duration"), "左侧哺乳时长")
    if not ok:
        return jsonify({"success": False, "message": err}), 400

    ok, right_dur, err = validate_duration(data.get("right_duration"), "右侧哺乳时长")
    if not ok:
        return jsonify({"success": False, "message": err}), 400

    # 校验文本长度
    ok, note, err = validate_text_length(data.get("note"), NOTE_MAX_LENGTH, "备注")
    if not ok:
        return jsonify({"success": False, "message": err}), 400

    db = get_db()
    db.execute(
        """INSERT INTO feeding_records (baby_id, start_time, end_time, feeding_type, amount, side, note, left_duration, right_duration)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (baby_id, data["start_time"], data.get("end_time"),
         feeding_type, amount,
         data.get("side"), note,
         left_dur, right_dur),
    )
    db.commit()
    return jsonify({"success": True, "message": "记录已添加"})


@bp.route("/api/babies/<int:baby_id>/feeding", methods=["GET"])
def list_feeding_records(baby_id):
    """获取喂奶记录列表"""
    db = get_db()
    # 支持按日期范围筛选
    start = request.args.get("start", "")
    end = request.args.get("end", "")
    query = "SELECT * FROM feeding_records WHERE baby_id = ?"
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


@bp.route("/api/babies/<int:baby_id>/feedings", methods=["GET"])
def list_feeding_records_plural(baby_id):
    """获取喂奶记录列表（复数形式，兼容前端）"""
    return list_feeding_records(baby_id)


@bp.route("/api/feeding/<int:record_id>", methods=["DELETE"])
def delete_feeding_record(record_id):
    """删除喂奶记录"""
    db = get_db()
    db.execute("DELETE FROM feeding_records WHERE id = ?", (record_id,))
    db.commit()
    return jsonify({"success": True, "message": "已删除"})


@bp.route("/api/babies/<int:baby_id>/feeding/<int:record_id>", methods=["GET"])
def get_feeding_record(baby_id, record_id):
    """获取单条喂奶记录"""
    db = get_db()
    row = db.execute(
        "SELECT * FROM feeding_records WHERE id = ? AND baby_id = ?",
        (record_id, baby_id)
    ).fetchone()
    if not row:
        return jsonify({"success": False, "message": "记录不存在"}), 404
    return jsonify({"success": True, "data": dict(row)})


@bp.route("/api/babies/<int:baby_id>/feeding/<int:record_id>", methods=["PUT"])
def update_feeding_record(baby_id, record_id):
    """更新喂奶记录"""
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "message": "无效数据"}), 400

    # 校验喂养类型
    ok, feeding_type, err = validate_enum(data.get("feeding_type"), VALID_FEEDING_TYPES, "喂养类型")
    if not ok:
        return jsonify({"success": False, "message": err}), 400

    # 校验数值字段
    ok, amount, err = validate_amount(data.get("amount"), "奶量")
    if not ok:
        return jsonify({"success": False, "message": err}), 400

    # 校验文本长度
    ok, note, err = validate_text_length(data.get("note"), NOTE_MAX_LENGTH, "备注")
    if not ok:
        return jsonify({"success": False, "message": err}), 400

    db = get_db()
    db.execute(
        """UPDATE feeding_records SET start_time=?, feeding_type=?, amount=?, side=?, note=?
           WHERE id=? AND baby_id=?""",
        (data.get("start_time"), feeding_type, amount, data.get("side"), note, record_id, baby_id)
    )
    db.commit()
    return jsonify({"success": True, "message": "已更新"})


@bp.route("/api/babies/<int:baby_id>/feeding/stats", methods=["GET"])
def get_feeding_stats(baby_id):
    """获取喂奶统计"""
    db = get_db()
    today = datetime.date.today().strftime("%Y-%m-%d")

    # 今日喂奶统计
    today_records = db.execute(
        """SELECT feeding_type, COUNT(*) as count, COALESCE(SUM(amount), 0) as total_amount
           FROM feeding_records
           WHERE baby_id = ? AND date(start_time) = ?
           GROUP BY feeding_type""",
        (baby_id, today),
    ).fetchall()

    stats = {
        "today": {
            row["feeding_type"]: {"count": row["count"], "amount": row["total_amount"]}
            for row in today_records
        }
    }
    return jsonify({"success": True, "data": stats})


@bp.route("/api/babies/<int:baby_id>/pumping", methods=["POST"])
def add_pumping_record(baby_id):
    """添加吸奶记录"""
    data = request.get_json()
    if not data or not data.get("pump_time"):
        return jsonify({"success": False, "message": "请填写吸奶时间"}), 400

    # 校验数值字段
    ok, left, err = validate_amount(data.get("left_amount"), "左侧奶量")
    if not ok:
        return jsonify({"success": False, "message": err}), 400

    ok, right, err = validate_amount(data.get("right_amount"), "右侧奶量")
    if not ok:
        return jsonify({"success": False, "message": err}), 400

    ok, total, err = validate_amount(data.get("total_amount"), "总奶量")
    if not ok:
        return jsonify({"success": False, "message": err}), 400

    ok, duration, err = validate_duration(data.get("duration_minutes"), "吸奶时长")
    if not ok:
        return jsonify({"success": False, "message": err}), 400

    # 左右侧计时时长（秒），由前端计时器提供，可独立/双侧同时
    ok, left_dur, err = validate_duration(data.get("left_duration"), "左侧时长")
    if not ok:
        return jsonify({"success": False, "message": err}), 400
    ok, right_dur, err = validate_duration(data.get("right_duration"), "右侧时长")
    if not ok:
        return jsonify({"success": False, "message": err}), 400

    # 计算总奶量
    if total is None and (left is not None or right is not None):
        total = (left or 0) + (right or 0)

    # 仅计时未填总时长时，用左右侧秒数之和折算总分钟数
    if duration is None and (left_dur or right_dur):
        duration = round(((left_dur or 0) + (right_dur or 0)) / 60)

    # 校验文本长度
    ok, note, err = validate_text_length(data.get("note"), NOTE_MAX_LENGTH, "备注")
    if not ok:
        return jsonify({"success": False, "message": err}), 400

    db = get_db()
    db.execute(
        """INSERT INTO pumping_records (baby_id, pump_time, duration_minutes, left_amount, right_amount, total_amount, pump_type, side, left_duration, right_duration, note)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (baby_id, data["pump_time"], duration,
         left, right, total, data.get("pump_type"), data.get("side"),
         left_dur, right_dur, note),
    )
    db.commit()
    return jsonify({"success": True, "message": "记录已添加"})


@bp.route("/api/babies/<int:baby_id>/pumping", methods=["GET"])
def list_pumping_records(baby_id):
    """获取吸奶记录列表"""
    db = get_db()
    rows = db.execute(
        "SELECT * FROM pumping_records WHERE baby_id = ? ORDER BY pump_time DESC LIMIT 100",
        (baby_id,)
    ).fetchall()
    return jsonify({"success": True, "data": [dict(r) for r in rows]})


@bp.route("/api/pumping/<int:record_id>", methods=["DELETE"])
def delete_pumping_record(record_id):
    """删除吸奶记录"""
    db = get_db()
    db.execute("DELETE FROM pumping_records WHERE id = ?", (record_id,))
    db.commit()
    return jsonify({"success": True, "message": "已删除"})


@bp.route("/api/babies/<int:baby_id>/pumping/<int:record_id>", methods=["GET"])
def get_pumping_record(baby_id, record_id):
    """获取单条吸奶记录（供编辑回显）"""
    db = get_db()
    row = db.execute(
        "SELECT * FROM pumping_records WHERE id = ? AND baby_id = ?",
        (record_id, baby_id),
    ).fetchone()
    if not row:
        return jsonify({"success": False, "message": "记录不存在"}), 404
    return jsonify({"success": True, "data": dict(row)})


@bp.route("/api/babies/<int:baby_id>/pumping/<int:record_id>", methods=["PUT"])
def update_pumping_record(baby_id, record_id):
    """更新吸奶记录"""
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "message": "无效数据"}), 400

    ok, left, err = validate_amount(data.get("left_amount"), "左侧奶量")
    if not ok:
        return jsonify({"success": False, "message": err}), 400
    ok, right, err = validate_amount(data.get("right_amount"), "右侧奶量")
    if not ok:
        return jsonify({"success": False, "message": err}), 400
    ok, total, err = validate_amount(data.get("total_amount"), "总奶量")
    if not ok:
        return jsonify({"success": False, "message": err}), 400
    ok, duration, err = validate_duration(data.get("duration_minutes"), "吸奶时长")
    if not ok:
        return jsonify({"success": False, "message": err}), 400
    ok, left_dur, err = validate_duration(data.get("left_duration"), "左侧时长")
    if not ok:
        return jsonify({"success": False, "message": err}), 400
    ok, right_dur, err = validate_duration(data.get("right_duration"), "右侧时长")
    if not ok:
        return jsonify({"success": False, "message": err}), 400

    if total is None and (left is not None or right is not None):
        total = (left or 0) + (right or 0)
    if duration is None and (left_dur or right_dur):
        duration = round(((left_dur or 0) + (right_dur or 0)) / 60)

    ok, note, err = validate_text_length(data.get("note"), NOTE_MAX_LENGTH, "备注")
    if not ok:
        return jsonify({"success": False, "message": err}), 400

    db = get_db()
    db.execute(
        """UPDATE pumping_records
           SET pump_time=?, pump_type=?, side=?, duration_minutes=?, left_amount=?, right_amount=?,
               total_amount=?, left_duration=?, right_duration=?, note=?
           WHERE id=? AND baby_id=?""",
        (data.get("pump_time"), data.get("pump_type"), data.get("side"), duration,
         left, right, total, left_dur, right_dur, note, record_id, baby_id),
    )
    db.commit()
    return jsonify({"success": True, "message": "已更新"})


@bp.route("/api/babies/<int:baby_id>/pumping/stats", methods=["GET"])
def get_pumping_stats(baby_id):
    """获取吸奶统计"""
    db = get_db()
    today = datetime.date.today().strftime("%Y-%m-%d")

    # 今日统计
    today_stat = db.execute(
        """SELECT COUNT(*) as count, COALESCE(SUM(total_amount), 0) as total,
                  COALESCE(SUM(duration_minutes), 0) as duration
           FROM pumping_records WHERE baby_id = ? AND date(pump_time) = ?""",
        (baby_id, today),
    ).fetchone()

    # 近7天统计
    week_ago = (datetime.date.today() - datetime.timedelta(days=7)).strftime("%Y-%m-%d")
    week_stat = db.execute(
        """SELECT COUNT(*) as count, COALESCE(SUM(total_amount), 0) as total,
                  COALESCE(SUM(duration_minutes), 0) as duration
           FROM pumping_records WHERE baby_id = ? AND date(pump_time) >= ?""",
        (baby_id, week_ago),
    ).fetchone()

    return jsonify({"success": True, "data": {
        "today": {"count": today_stat["count"], "amount": today_stat["total"], "duration": today_stat["duration"]},
        "week": {"count": week_stat["count"], "amount": week_stat["total"], "duration": week_stat["duration"]},
    }})
