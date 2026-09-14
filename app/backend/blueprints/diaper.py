#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xiaoke799
"""
换尿布记录路由 Blueprint。
提供换尿布记录增删、间隔分析、皮肤状况追踪、报表统计等功能。
"""

import datetime
from flask import Blueprint, request, jsonify

from utils import get_db
from blueprints.analytics import _parse_datetime
from validators import validate_enum, validate_text_length, VALID_DIAPER_TYPES, NOTE_MAX_LENGTH

bp = Blueprint("diaper", __name__)

VALID_SKIN_CONDITIONS = ["normal", "slight_red", "rash", "severe_rash"]


@bp.route("/api/babies/<int:baby_id>/diaper", methods=["POST"])
def add_diaper_record(baby_id):
    """添加换尿布记录"""
    data = request.get_json()
    if not data or not data.get("change_time") or not data.get("diaper_type"):
        return jsonify({"success": False, "message": "请填写时间和类型"}), 400

    # 校验尿布类型枚举
    ok, diaper_type, err = validate_enum(data["diaper_type"], VALID_DIAPER_TYPES, "尿布类型")
    if not ok:
        return jsonify({"success": False, "message": err}), 400

    # 校验皮肤状况
    skin_condition = data.get("skin_condition")
    if skin_condition:
        ok, skin_condition, err = validate_enum(skin_condition, VALID_SKIN_CONDITIONS, "皮肤状况")
        if not ok:
            return jsonify({"success": False, "message": err}), 400

    # 校验文本长度
    ok, note, err = validate_text_length(data.get("note"), NOTE_MAX_LENGTH, "备注")
    if not ok:
        return jsonify({"success": False, "message": err}), 400

    db = get_db()
    db.execute(
        """INSERT INTO diaper_records
           (baby_id, change_time, diaper_type, color, skin_condition, brand, note)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (baby_id, data["change_time"], diaper_type, data.get("color"),
         skin_condition, data.get("brand"), note),
    )
    db.commit()
    return jsonify({"success": True, "message": "记录已添加"})


@bp.route("/api/babies/<int:baby_id>/diaper", methods=["GET"])
def list_diaper_records(baby_id):
    """获取换尿布记录列表"""
    db = get_db()
    start = request.args.get("start", "")
    end = request.args.get("end", "")
    query = "SELECT * FROM diaper_records WHERE baby_id = ?"
    params = [baby_id]
    if start:
        query += " AND change_time >= ?"
        params.append(start)
    if end:
        query += " AND change_time <= ?"
        params.append(end + " 23:59:59")
    query += " ORDER BY change_time DESC LIMIT 100"
    rows = db.execute(query, params).fetchall()
    return jsonify({"success": True, "data": [dict(r) for r in rows]})


@bp.route("/api/babies/<int:baby_id>/diapers", methods=["GET"])
def list_diaper_records_plural(baby_id):
    """获取换尿布记录列表（复数形式，兼容前端）"""
    return list_diaper_records(baby_id)


@bp.route("/api/diaper/<int:record_id>", methods=["DELETE"])
def delete_diaper_record(record_id):
    """删除换尿布记录"""
    db = get_db()
    db.execute("DELETE FROM diaper_records WHERE id = ?", (record_id,))
    db.commit()
    return jsonify({"success": True, "message": "已删除"})


@bp.route("/api/babies/<int:baby_id>/diaper/<int:record_id>", methods=["GET"])
def get_diaper_record(baby_id, record_id):
    """按 ID 获取单条换尿布记录（编辑回显用）"""
    db = get_db()
    row = db.execute(
        "SELECT * FROM diaper_records WHERE id = ? AND baby_id = ?",
        (record_id, baby_id),
    ).fetchone()
    if row is None:
        return jsonify({"success": False, "message": "记录不存在"}), 404
    return jsonify({"success": True, "data": dict(row)})


@bp.route("/api/babies/<int:baby_id>/diaper/<int:record_id>", methods=["PUT"])
def update_diaper_record(baby_id, record_id):
    """更新换尿布记录"""
    data = request.get_json()
    if not data or not data.get("change_time") or not data.get("diaper_type"):
        return jsonify({"success": False, "message": "请填写时间和类型"}), 400

    # 校验尿布类型枚举
    ok, diaper_type, err = validate_enum(data["diaper_type"], VALID_DIAPER_TYPES, "尿布类型")
    if not ok:
        return jsonify({"success": False, "message": err}), 400

    # 校验皮肤状况
    skin_condition = data.get("skin_condition")
    if skin_condition:
        ok, skin_condition, err = validate_enum(skin_condition, VALID_SKIN_CONDITIONS, "皮肤状况")
        if not ok:
            return jsonify({"success": False, "message": err}), 400

    # 校验文本长度
    ok, note, err = validate_text_length(data.get("note"), NOTE_MAX_LENGTH, "备注")
    if not ok:
        return jsonify({"success": False, "message": err}), 400

    db = get_db()
    cur = db.execute(
        """UPDATE diaper_records
           SET change_time = ?, diaper_type = ?, color = ?, skin_condition = ?, brand = ?, note = ?
           WHERE id = ? AND baby_id = ?""",
        (data["change_time"], diaper_type, data.get("color"),
         skin_condition, data.get("brand"), note, record_id, baby_id),
    )
    if cur.rowcount == 0:
        return jsonify({"success": False, "message": "记录不存在"}), 404
    db.commit()
    return jsonify({"success": True, "message": "已更新"})


@bp.route("/api/babies/<int:baby_id>/diaper/interval-analysis", methods=["GET"])
def get_diaper_interval_analysis(baby_id):
    """分析换尿布间隔规律"""
    db = get_db()
    # 获取最近7天的换尿布记录
    week_ago = (datetime.date.today() - datetime.timedelta(days=7)).strftime("%Y-%m-%d")
    records = db.execute(
        """SELECT * FROM diaper_records
           WHERE baby_id = ? AND date(change_time) >= ?
           ORDER BY change_time ASC""",
        (baby_id, week_ago),
    ).fetchall()

    if len(records) < 2:
        return jsonify({"success": True, "data": None, "message": "需要至少2条记录才能分析"})

    # 计算间隔
    intervals = []
    wet_intervals = []
    dirty_intervals = []

    for i in range(1, len(records)):
        prev_time = _parse_datetime(records[i - 1]["change_time"])
        curr_time = _parse_datetime(records[i]["change_time"])
        if prev_time is None or curr_time is None:
            continue
        interval_minutes = (curr_time - prev_time).total_seconds() / 60

        # 只统计合理间隔（排除睡眠时段超过4小时的情况）
        if interval_minutes < 240:
            intervals.append(interval_minutes)
            if records[i]["diaper_type"] == "wet":
                wet_intervals.append(interval_minutes)
            elif records[i]["diaper_type"] in ("dirty", "both"):
                dirty_intervals.append(interval_minutes)

    if not intervals:
        return jsonify({"success": True, "data": None, "message": "暂无有效间隔数据"})

    avg_interval = sum(intervals) / len(intervals)
    min_interval = min(intervals)
    max_interval = max(intervals)

    # 今日统计
    today = datetime.date.today().strftime("%Y-%m-%d")
    today_count = db.execute(
        "SELECT COUNT(*) FROM diaper_records WHERE baby_id = ? AND date(change_time) = ?",
        (baby_id, today),
    ).fetchone()[0]

    # 预测下次换尿布时间
    last_record = records[-1]
    last_time = _parse_datetime(last_record["change_time"])
    if last_time is None:
        last_time = datetime.datetime.now()
    predicted_next = last_time + datetime.timedelta(minutes=avg_interval)

    return jsonify({"success": True, "data": {
        "avg_interval_minutes": round(avg_interval, 1),
        "min_interval_minutes": round(min_interval, 1),
        "max_interval_minutes": round(max_interval, 1),
        "total_records": len(records),
        "today_count": today_count,
        "predicted_next": predicted_next.strftime("%H:%M"),
        "predicted_in_minutes": round((predicted_next - datetime.datetime.now()).total_seconds() / 60),
        "wet_avg": round(sum(wet_intervals) / len(wet_intervals), 1) if wet_intervals else None,
        "dirty_avg": round(sum(dirty_intervals) / len(dirty_intervals), 1) if dirty_intervals else None,
    }})


# ==================== API: 报表统计 ====================

@bp.route("/api/babies/<int:baby_id>/diaper/report", methods=["GET"])
def get_diaper_report(baby_id):
    """获取换尿布报表统计"""
    db = get_db()

    # 获取最近30天的记录
    month_ago = (datetime.date.today() - datetime.timedelta(days=30)).strftime("%Y-%m-%d")
    records = db.execute(
        """SELECT * FROM diaper_records
           WHERE baby_id = ? AND date(change_time) >= ?
           ORDER BY change_time ASC""",
        (baby_id, month_ago),
    ).fetchall()

    if not records:
        return jsonify({"success": True, "data": {
            "total_changes": 0,
            "avg_per_day": 0,
            "wet_count": 0,
            "dirty_count": 0,
            "both_count": 0,
            "dry_count": 0,
            "skin_issues": 0,
            "daily_stats": [],
            "color_distribution": {},
            "skin_trend": [],
        }})

    # 基础统计
    total = len(records)
    wet_count = sum(1 for r in records if r["diaper_type"] == "wet")
    dirty_count = sum(1 for r in records if r["diaper_type"] == "dirty")
    both_count = sum(1 for r in records if r["diaper_type"] == "both")
    dry_count = sum(1 for r in records if r["diaper_type"] == "dry")

    # 皮肤问题统计
    skin_issues = sum(1 for r in records if r["skin_condition"] in ("rash", "severe_rash", "slight_red"))

    # 每日统计
    daily_stats = {}
    for r in records:
        date = r["change_time"][:10]
        if date not in daily_stats:
            daily_stats[date] = {"date": date, "wet": 0, "dirty": 0, "both": 0, "dry": 0, "total": 0}
        daily_stats[date][r["diaper_type"] if r["diaper_type"] in daily_stats[date] else "wet"] += 1
        daily_stats[date]["total"] += 1

    daily_list = sorted(daily_stats.values(), key=lambda x: x["date"])

    # 大便颜色分布
    color_dist = {}
    for r in records:
        if r["color"]:
            color_dist[r["color"]] = color_dist.get(r["color"], 0) + 1

    # 皮肤状况趋势
    skin_trend = []
    for r in records:
        if r["skin_condition"]:
            skin_trend.append({"date": r["change_time"][:10], "condition": r["skin_condition"]})

    # 计算日均
    days_span = max((datetime.datetime.strptime(records[-1]["change_time"][:10], "%Y-%m-%d") -
                     datetime.datetime.strptime(records[0]["change_time"][:10], "%Y-%m-%d")).days, 1) + 1

    return jsonify({"success": True, "data": {
        "total_changes": total,
        "avg_per_day": round(total / days_span, 1),
        "wet_count": wet_count,
        "dirty_count": dirty_count,
        "both_count": both_count,
        "dry_count": dry_count,
        "skin_issues": skin_issues,
        "daily_stats": daily_list,
        "color_distribution": color_dist,
        "skin_trend": skin_trend,
    }})


# ==================== API: 批量添加 ====================

@bp.route("/api/babies/<int:baby_id>/diaper/batch", methods=["POST"])
def batch_add_diaper_records(baby_id):
    """批量添加换尿布记录"""
    data = request.get_json()
    if not data or not data.get("records"):
        return jsonify({"success": False, "message": "请提供记录数据"}), 400

    records = data["records"]
    if not isinstance(records, list) or len(records) == 0:
        return jsonify({"success": False, "message": "记录格式不正确"}), 400

    db = get_db()
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    inserted = 0

    for rec in records:
        diaper_type = rec.get("diaper_type")
        if not diaper_type:
            continue
        change_time = rec.get("change_time", now)
        db.execute(
            """INSERT INTO diaper_records
               (baby_id, change_time, diaper_type, color, skin_condition, brand, note)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (baby_id, change_time, diaper_type, rec.get("color"),
             rec.get("skin_condition"), rec.get("brand"), rec.get("note", "")),
        )
        inserted += 1

    db.commit()
    return jsonify({"success": True, "message": f"成功添加 {inserted} 条记录", "count": inserted})


# ==================== API: 获取常用品牌 ====================

@bp.route("/api/babies/<int:baby_id>/diaper/brands", methods=["GET"])
def get_diaper_brands(baby_id):
    """获取用户常用的尿布品牌列表"""
    db = get_db()
    rows = db.execute(
        """SELECT brand, COUNT(*) as count FROM diaper_records
           WHERE baby_id = ? AND brand IS NOT NULL AND brand != ''
           GROUP BY brand
           ORDER BY count DESC
           LIMIT 10""",
        (baby_id,),
    ).fetchall()

    return jsonify({
        "success": True,
        "data": [{"brand": r["brand"], "count": r["count"]} for r in rows]
    })
