#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xiaoke799
"""
宝宝管理路由 Blueprint。
提供宝宝信息的增删查改功能。
"""

from flask import Blueprint, request, jsonify
from utils import get_db, get_baby_age, get_baby_age_months, row_to_dict, rows_to_list
from user_context import require_admin

bp = Blueprint("babies", __name__, url_prefix="/api/babies")


@bp.route("", methods=["GET"])
def get_babies():
    """获取宝宝列表"""
    db = get_db()
    babies = db.execute("SELECT * FROM babies ORDER BY birthday DESC").fetchall()
    result = []
    for b in babies:
        baby = dict(b)
        baby["age"] = get_baby_age(baby.get("birthday", ""))
        baby["age_months"] = get_baby_age_months(baby.get("birthday", ""))
        result.append(baby)
    return jsonify({"success": True, "data": result})


@bp.route("/<int:baby_id>", methods=["GET"])
def get_baby(baby_id):
    """获取单个宝宝信息

    前端「参考」页读 res.data.birthday 判断生日是否已设置，此前该 URL 只有
    DELETE 没有 GET，请求直接 405，页面永远显示"请设置宝宝生日后查看参考"。
    """
    db = get_db()
    baby = db.execute("SELECT * FROM babies WHERE id = ?", (baby_id,)).fetchone()
    if not baby:
        return jsonify({"success": False, "message": "宝宝不存在"}), 404
    result = row_to_dict(baby)
    result["age"] = get_baby_age(result.get("birthday", ""))
    result["age_months"] = get_baby_age_months(result.get("birthday", ""))
    return jsonify({"success": True, "data": result})


@bp.route("", methods=["POST"])
def add_baby():
    """添加宝宝"""
    data = request.get_json()
    if not data or not data.get("name") or not data.get("birthday"):
        return jsonify({"success": False, "message": "请填写宝宝昵称和生日"}), 400

    db = get_db()
    cursor = db.execute(
        "INSERT INTO babies (name, birthday, gender, due_date) VALUES (?, ?, ?, ?)",
        (data["name"], data["birthday"], data.get("gender", "other"), data.get("due_date")),
    )
    db.commit()

    baby_id = cursor.lastrowid
    baby = db.execute("SELECT * FROM babies WHERE id = ?", (baby_id,)).fetchone()
    result = row_to_dict(baby)
    result["age"] = get_baby_age(result["birthday"])
    return jsonify({"success": True, "data": result})


@bp.route("/<int:baby_id>", methods=["DELETE"])
@require_admin
def delete_baby(baby_id):
    """删除宝宝及其所有记录（危险操作，仅管理员）"""
    db = get_db()
    db.execute("DELETE FROM babies WHERE id = ?", (baby_id,))
    db.commit()
    return jsonify({"success": True, "message": "已删除"})


@bp.route("/<int:baby_id>/export", methods=["GET"])
@require_admin
def export_baby_data(baby_id):
    """导出指定宝宝的所有数据（仅管理员）"""
    db = get_db()
    baby = db.execute("SELECT * FROM babies WHERE id = ?", (baby_id,)).fetchone()
    if not baby:
        return jsonify({"success": False, "message": "宝宝不存在"}), 404

    # 按 baby_id 关联的表（表名必须与 server.py init_db() 一致）
    baby_scoped_tables = [
        'growth_records', 'feeding_records', 'sleep_records', 'diaper_records',
        'pumping_records', 'milestones', 'diary_entries', 'vaccines',
        'tummy_time_records', 'temperature_records', 'medication_records',
        'allergy_tests', 'baby_teeth', 'asq_screenings',
        'photos', 'timers', 'solid_food_records', 'leap_records',
        'fontanelle_records', 'growth_photos',
        'health_records', 'medication_reminders',
        'chat_history', 'feeding_summary', 'milestone_details',
    ]

    existing_tables = set(
        row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    )

    baby_data = {'baby': row_to_dict(baby)}
    for table in baby_scoped_tables:
        if table not in existing_tables:
            continue
        try:
            baby_data[table] = rows_to_list(
                db.execute(f'SELECT * FROM {table} WHERE baby_id = ?', (baby_id,)).fetchall()
            )
        except Exception:
            pass

    return jsonify({'success': True, 'data': baby_data})
