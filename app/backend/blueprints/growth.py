#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xiaoke799
"""
成长记录路由 Blueprint。
提供生长记录增删、生长速度计算等功能。
"""

import datetime
from flask import Blueprint, request, jsonify

from utils import get_db, row_to_dict
from validators import validate_height, validate_weight, validate_bmi, validate_head_circumference, validate_text_length, NOTE_MAX_LENGTH
import growth_utils

bp = Blueprint("growth", __name__)


@bp.route("/api/babies/<int:baby_id>/growth", methods=["POST"])
def add_growth_record(baby_id):
    """添加成长记录"""
    data = request.get_json()
    if not data or not data.get("record_date"):
        return jsonify({"success": False, "message": "请填写记录日期"}), 400

    # 校验数值字段
    ok, height, err = validate_height(data.get("height"), "身高")
    if not ok:
        return jsonify({"success": False, "message": err}), 400

    ok, weight, err = validate_weight(data.get("weight"), "体重")
    if not ok:
        return jsonify({"success": False, "message": err}), 400

    ok, head_circ, err = validate_head_circumference(data.get("head_circumference"), "头围")
    if not ok:
        return jsonify({"success": False, "message": err}), 400

    bmi = data.get("bmi")
    # 如果提供了身高和体重但没有 BMI，自动计算
    if bmi is None and height and weight:
        try:
            h_m = float(height) / 100.0
            bmi = round(float(weight) / (h_m * h_m), 1)
        except (ValueError, ZeroDivisionError):
            bmi = None
    else:
        ok, bmi, err = validate_bmi(bmi, "BMI")
        if not ok:
            return jsonify({"success": False, "message": err}), 400

    # 校验文本长度
    ok, note, err = validate_text_length(data.get("note"), NOTE_MAX_LENGTH, "备注")
    if not ok:
        return jsonify({"success": False, "message": err}), 400

    db = get_db()
    db.execute(
        """INSERT INTO growth_records (baby_id, record_date, height, weight, head_circumference, bmi, note)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (baby_id, data["record_date"], height, weight,
         head_circ, bmi, note),
    )
    db.commit()
    return jsonify({"success": True, "message": "记录已添加"})


@bp.route("/api/babies/<int:baby_id>/growth", methods=["GET"])
def list_growth_records(baby_id):
    """获取成长记录列表"""
    db = get_db()
    rows = db.execute(
        "SELECT * FROM growth_records WHERE baby_id = ? ORDER BY record_date DESC LIMIT 100",
        (baby_id,)
    ).fetchall()
    return jsonify({"success": True, "data": [dict(r) for r in rows]})


@bp.route("/api/growth/<int:record_id>", methods=["DELETE"])
def delete_growth_record(record_id):
    """删除成长记录"""
    db = get_db()
    db.execute("DELETE FROM growth_records WHERE id = ?", (record_id,))
    db.commit()
    return jsonify({"success": True, "message": "已删除"})


@bp.route("/api/babies/<int:baby_id>/growth/<int:record_id>", methods=["GET"])
def get_growth_record(baby_id, record_id):
    """获取单条成长记录"""
    db = get_db()
    row = db.execute(
        "SELECT * FROM growth_records WHERE id = ? AND baby_id = ?",
        (record_id, baby_id)
    ).fetchone()
    if not row:
        return jsonify({"success": False, "message": "记录不存在"}), 404
    return jsonify({"success": True, "data": dict(row)})


@bp.route("/api/babies/<int:baby_id>/growth/<int:record_id>", methods=["PUT"])
def update_growth_record(baby_id, record_id):
    """更新成长记录"""
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "message": "无效数据"}), 400

    # 校验数值字段
    ok, height, err = validate_height(data.get("height"), "身高")
    if not ok:
        return jsonify({"success": False, "message": err}), 400

    ok, weight, err = validate_weight(data.get("weight"), "体重")
    if not ok:
        return jsonify({"success": False, "message": err}), 400

    ok, head_circ, err = validate_head_circumference(data.get("head_circumference"), "头围")
    if not ok:
        return jsonify({"success": False, "message": err}), 400

    bmi = data.get("bmi")
    # 如果提供了身高和体重但没有 BMI，自动计算
    if bmi is None and height and weight:
        try:
            h_m = float(height) / 100.0
            bmi = round(float(weight) / (h_m * h_m), 1)
        except (ValueError, ZeroDivisionError):
            bmi = None
    else:
        ok, bmi, err = validate_bmi(bmi, "BMI")
        if not ok:
            return jsonify({"success": False, "message": err}), 400

    # 校验文本长度
    ok, note, err = validate_text_length(data.get("note"), NOTE_MAX_LENGTH, "备注")
    if not ok:
        return jsonify({"success": False, "message": err}), 400

    db = get_db()
    db.execute(
        """UPDATE growth_records SET record_date=?, height=?, weight=?, head_circumference=?, bmi=?, note=?
           WHERE id=? AND baby_id=?""",
        (data.get("record_date"), height, weight, head_circ, bmi, note, record_id, baby_id)
    )
    db.commit()
    return jsonify({"success": True, "message": "已更新"})


@bp.route("/api/babies/<int:baby_id>/growth-velocity", methods=["GET"])
def get_growth_velocity(baby_id):
    """计算生长速度"""
    db = get_db()
    records = db.execute(
        "SELECT * FROM growth_records WHERE baby_id = ? ORDER BY record_date ASC",
        (baby_id,),
    ).fetchall()

    if len(records) < 2:
        return jsonify({"success": True, "data": None, "message": "需要至少2条记录才能计算生长速度"})

    latest = records[-1]
    previous = records[-2]

    # 取宝宝信息以获得分龄评估所需的（纠正）月龄
    baby_row = db.execute("SELECT * FROM babies WHERE id = ?", (baby_id,)).fetchone()
    age_months_latest = None
    if baby_row:
        baby_d = row_to_dict(baby_row)
        age_months_latest, _ = growth_utils.corrected_age_months(
            baby_d.get("birthday"), baby_d.get("due_date"), latest["record_date"]
        )

    days_diff = 0
    try:
        latest_date = datetime.datetime.strptime(latest["record_date"], "%Y-%m-%d")
        previous_date = datetime.datetime.strptime(previous["record_date"], "%Y-%m-%d")
        days_diff = (latest_date - previous_date).days
    except (ValueError, TypeError):
        return jsonify({"success": True, "data": None, "message": "记录日期格式错误"})

    if days_diff == 0:
        return jsonify({"success": True, "data": None, "message": "两次记录日期相同"})

    result = {"days_diff": days_diff}

    # 体重速度 (kg/day)
    if latest["weight"] and previous["weight"]:
        weight_diff = latest["weight"] - previous["weight"]
        result["weight_velocity"] = round(weight_diff / days_diff, 4)
        result["weight_diff"] = round(weight_diff, 2)
        # 评估：按（纠正）月龄分档，速度带见 growth_utils._WEIGHT_BANDS
        daily_g = weight_diff * 1000 / days_diff
        result["weight_assessment"] = growth_utils.assess_weight_velocity(daily_g, age_months_latest)

    # 身高速度 (cm/day)
    if latest["height"] and previous["height"]:
        height_diff = latest["height"] - previous["height"]
        result["height_velocity"] = round(height_diff / days_diff, 4)
        result["height_diff"] = round(height_diff, 1)
        daily_cm = height_diff / days_diff
        result["height_assessment"] = growth_utils.assess_height_velocity(daily_cm, age_months_latest)

    # 头围速度 (cm/day)
    if latest["head_circumference"] and previous["head_circumference"]:
        head_diff = latest["head_circumference"] - previous["head_circumference"]
        result["head_velocity"] = round(head_diff / days_diff, 4)
        result["head_diff"] = round(head_diff, 1)

    # 总增长（从第一条记录开始）
    if len(records) > 2:
        first = records[0]
        try:
            total_days = (
                datetime.datetime.strptime(latest["record_date"], "%Y-%m-%d")
                - datetime.datetime.strptime(first["record_date"], "%Y-%m-%d")
            ).days
        except (ValueError, TypeError):
            total_days = 0
        if total_days > 0:
            result["total_days"] = total_days
            if latest["weight"] and first["weight"]:
                result["total_weight_gain"] = round(latest["weight"] - first["weight"], 2)
            if latest["height"] and first["height"]:
                result["total_height_gain"] = round(latest["height"] - first["height"], 1)

    return jsonify({"success": True, "data": result})


@bp.route("/api/babies/<int:baby_id>/record-growth-correlation", methods=["GET"])
def get_record_growth_correlation(baby_id):
    """获取记录与成长的关联统计数据（喂养/睡眠与体重增长的关系）"""
    db = get_db()
    today = datetime.date.today()
    month_ago = (today - datetime.timedelta(days=30)).strftime("%Y-%m-%d")

    # 近30天喂养统计
    feeding_stats = db.execute(
        """SELECT COUNT(*) as total_count,
                  COALESCE(SUM(amount), 0) as total_amount,
                  COALESCE(AVG(amount), 0) as avg_amount,
                  COUNT(DISTINCT date(start_time)) as active_days
           FROM feeding_records
           WHERE baby_id = ? AND date(start_time) >= ?""",
        (baby_id, month_ago),
    ).fetchone()

    # 近30天睡眠统计
    sleep_stats = db.execute(
        """SELECT COALESCE(SUM(duration_minutes), 0) as total_minutes,
                  COALESCE(AVG(duration_minutes), 0) as avg_session,
                  COUNT(*) as total_sessions
           FROM sleep_records
           WHERE baby_id = ? AND date(start_time) >= ?""",
        (baby_id, month_ago),
    ).fetchone()

    # 最近两次成长记录（用于计算增长）
    growth_records = db.execute(
        """SELECT * FROM growth_records
           WHERE baby_id = ? ORDER BY record_date DESC LIMIT 2""",
        (baby_id,),
    ).fetchall()

    growth_data = None
    if len(growth_records) >= 2:
        latest = growth_records[0]
        previous = growth_records[1]
        try:
            latest_date = datetime.datetime.strptime(latest["record_date"], "%Y-%m-%d")
            previous_date = datetime.datetime.strptime(previous["record_date"], "%Y-%m-%d")
            days_diff = (latest_date - previous_date).days
        except (ValueError, TypeError):
            days_diff = 0

        if days_diff > 0:
            growth_data = {
                "latest_date": latest["record_date"],
                "previous_date": previous["record_date"],
                "days_diff": days_diff,
                "latest_weight": latest["weight"],
                "previous_weight": previous["weight"],
                "weight_diff": round((latest["weight"] or 0) - (previous["weight"] or 0), 2) if latest["weight"] and previous["weight"] else None,
                "latest_height": latest["height"],
                "previous_height": previous["height"],
                "height_diff": round((latest["height"] or 0) - (previous["height"] or 0), 1) if latest["height"] and previous["height"] else None,
                "weight_velocity": round(((latest["weight"] or 0) - (previous["weight"] or 0)) / days_diff * 1000, 1) if latest["weight"] and previous["weight"] else None,
            }

    # 最新成长数据
    latest_growth = growth_records[0] if growth_records else None

    return jsonify({
        "success": True,
        "data": {
            "feeding_30d": {
                "total_count": feeding_stats["total_count"],
                "total_amount": feeding_stats["total_amount"],
                "avg_amount": round(feeding_stats["avg_amount"], 1),
                "active_days": feeding_stats["active_days"],
            },
            "sleep_30d": {
                "total_hours": round(sleep_stats["total_minutes"] / 60, 1),
                "avg_session_minutes": round(sleep_stats["avg_session"], 1),
                "total_sessions": sleep_stats["total_sessions"],
            },
            "growth_trend": growth_data,
            "latest": {
                "weight": latest_growth["weight"] if latest_growth else None,
                "height": latest_growth["height"] if latest_growth else None,
                "bmi": latest_growth["bmi"] if latest_growth else None,
                "head_circumference": latest_growth["head_circumference"] if latest_growth else None,
                "record_date": latest_growth["record_date"] if latest_growth else None,
            },
        },
    })
