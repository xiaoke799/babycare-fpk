#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xiaoke799
"""
发育记录路由 Blueprint。
提供里程碑、ASQ筛查、出牙、体检、辅食、飞跃期、囟门、成长照片、日记等记录管理功能。
"""

import datetime
import json
from flask import Blueprint, request, jsonify

from utils import get_db, row_to_dict, rows_to_list
from logger import get_logger
import growth_utils

bp = Blueprint("development", __name__)
logger = get_logger("development")


# ==================== API: 里程碑 ====================

@bp.route("/api/babies/<int:baby_id>/milestones", methods=["GET"])
def list_milestones(baby_id):
    """获取里程碑列表。

    里程碑与「第一次」共用同一张表，靠 is_first 区分：
    - 不传参数：返回全部（里程碑页使用）
    - first=1：只返回「第一次」成就（第一次页使用）
    """
    db = get_db()
    only_first = request.args.get("first", "")
    sql = "SELECT * FROM milestones WHERE baby_id = ?"
    params = [baby_id]
    if only_first == "1":
        sql += " AND is_first = 1"
    sql += " ORDER BY achieved_date DESC"
    rows = db.execute(sql, params).fetchall()
    return jsonify({"success": True, "data": [dict(r) for r in rows]})


@bp.route("/api/babies/<int:baby_id>/milestones", methods=["POST"])
def add_milestone(baby_id):
    """添加里程碑"""
    data = request.get_json()
    if not data or not data.get("title") or not data.get("achieved_date"):
        return jsonify({"success": False, "message": "请填写标题和达成日期"}), 400

    db = get_db()
    db.execute(
        """INSERT INTO milestones (baby_id, title, description, achieved_date, category, is_first)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (baby_id, data["title"], data.get("description", ""),
         data["achieved_date"], data.get("category", "other"),
         1 if data.get("is_first") else 0)
    )
    db.commit()
    return jsonify({"success": True, "message": "里程碑已添加"})


@bp.route("/api/babies/<int:baby_id>/milestones/<int:record_id>", methods=["PUT"])
def update_milestone(baby_id, record_id):
    """更新里程碑"""
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "message": "无效数据"}), 400
    db = get_db()
    fields = []
    values = []
    for key in ("title", "description", "achieved_date", "category"):
        if key in data:
            fields.append(f"{key} = ?")
            values.append(data[key])
    if "is_first" in data:
        fields.append("is_first = ?")
        values.append(1 if data["is_first"] else 0)
    if not fields:
        return jsonify({"success": False, "message": "无更新字段"}), 400
    values.extend([record_id, baby_id])
    db.execute(f'UPDATE milestones SET {", ".join(fields)} WHERE id = ? AND baby_id = ?', values)
    db.commit()
    return jsonify({"success": True, "message": "里程碑已更新"})


@bp.route("/api/babies/<int:baby_id>/milestones/<int:record_id>", methods=["DELETE"])
def delete_milestone(baby_id, record_id):
    """删除里程碑"""
    db = get_db()
    db.execute("DELETE FROM milestones WHERE id = ? AND baby_id = ?", (record_id, baby_id))
    db.commit()
    return jsonify({"success": True, "message": "里程碑已删除"})


# ==================== API: ASQ筛查 ====================

@bp.route("/api/babies/<int:baby_id>/asq", methods=["POST"])
def add_asq_screening(baby_id):
    """添加ASQ筛查记录"""
    data = request.get_json()
    if not data or not data.get("age_months"):
        return jsonify({"success": False, "message": "请填写月龄"}), 400

    comm = data.get("communication_score", 0)
    gross = data.get("gross_motor_score", 0)
    fine = data.get("fine_motor_score", 0)
    prob = data.get("problem_solving_score", 0)
    pers = data.get("personal_social_score", 0)
    total = comm + gross + fine + prob + pers

    # 简单评估：总分>=150正常，100-149需关注，<100建议转介
    if total >= 150:
        result = "normal"
    elif total >= 100:
        result = "monitor"
    else:
        result = "refer"

    db = get_db()
    db.execute(
        """INSERT INTO asq_screenings (baby_id, screening_date, age_months,
           communication_score, gross_motor_score, fine_motor_score,
           problem_solving_score, personal_social_score, total_score, result, note)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (baby_id, data.get("screening_date", datetime.date.today().strftime("%Y-%m-%d")),
         data["age_months"], comm, gross, fine, prob, pers, total, result, data.get("note", ""))
    )
    db.commit()
    return jsonify({"success": True, "message": "筛查记录已添加", "data": {"total_score": total, "result": result}})


@bp.route("/api/asq/<int:record_id>", methods=["DELETE"])
def delete_asq_screening(record_id):
    """删除ASQ筛查记录"""
    db = get_db()
    db.execute("DELETE FROM asq_screenings WHERE id = ?", (record_id,))
    db.commit()
    return jsonify({"success": True, "message": "已删除"})


@bp.route("/api/babies/<int:baby_id>/asq", methods=["GET"])
def get_asq_screenings(baby_id):
    """获取ASQ筛查记录列表"""
    try:
        db = get_db()
        rows = db.execute("SELECT * FROM asq_screenings WHERE baby_id = ? ORDER BY screening_date DESC", (baby_id,)).fetchall()
        return jsonify({"success": True, "screenings": rows_to_list(rows)})
    except Exception as e:
        logger.error("get_asq_screenings error: %s", e, exc_info=True)
        return jsonify({"success": True, "screenings": []})


# ==================== API: 出牙记录 ====================

@bp.route("/api/babies/<int:baby_id>/teething", methods=["POST"])
def add_teething_record(baby_id):
    """添加出牙记录"""
    data = request.get_json()
    if not data or not data.get("tooth_name") or not data.get("erupt_date"):
        return jsonify({"success": False, "message": "请填写牙齿名称和萌出日期"}), 400

    db = get_db()
    db.execute(
        """INSERT INTO teething_records (baby_id, tooth_name, erupt_date, position, side, note)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (baby_id, data["tooth_name"], data["erupt_date"],
         data.get("position", ""), data.get("side", ""), data.get("note", "")),
    )
    db.commit()
    return jsonify({"success": True, "message": "出牙记录已添加"})


@bp.route("/api/babies/<int:baby_id>/teething/<int:record_id>", methods=["GET"])
def get_teething_record(baby_id, record_id):
    """按 ID 获取单条出牙记录（编辑回显用）"""
    db = get_db()
    row = db.execute(
        "SELECT * FROM teething_records WHERE id = ? AND baby_id = ?",
        (record_id, baby_id),
    ).fetchone()
    if row is None:
        return jsonify({"success": False, "message": "记录不存在"}), 404
    return jsonify({"success": True, "data": dict(row)})


@bp.route("/api/babies/<int:baby_id>/teething/<int:record_id>", methods=["PUT"])
def update_teething_record(baby_id, record_id):
    """更新出牙记录"""
    data = request.get_json()
    if not data or not data.get("tooth_name") or not data.get("erupt_date"):
        return jsonify({"success": False, "message": "请填写牙齿名称和萌出日期"}), 400

    db = get_db()
    cur = db.execute(
        """UPDATE teething_records
           SET tooth_name = ?, erupt_date = ?, position = ?, side = ?, note = ?
           WHERE id = ? AND baby_id = ?""",
        (data["tooth_name"], data["erupt_date"],
         data.get("position", ""), data.get("side", ""), data.get("note", ""),
         record_id, baby_id),
    )
    if cur.rowcount == 0:
        return jsonify({"success": False, "message": "记录不存在"}), 404
    db.commit()
    return jsonify({"success": True, "message": "出牙记录已更新"})


@bp.route("/api/teething/<int:record_id>", methods=["DELETE"])
def delete_teething_record(record_id):
    """删除出牙记录"""
    db = get_db()
    db.execute("DELETE FROM teething_records WHERE id = ?", (record_id,))
    db.commit()
    return jsonify({"success": True, "message": "已删除"})


@bp.route("/api/babies/<int:baby_id>/teething", methods=["GET"])
def get_teething_records(baby_id):
    """获取出牙记录列表"""
    db = get_db()
    rows = db.execute("SELECT * FROM teething_records WHERE baby_id = ? ORDER BY erupt_date DESC", (baby_id,)).fetchall()
    return jsonify({"success": True, "data": rows_to_list(rows)})


# ==================== API: 体检记录 ====================

@bp.route("/api/babies/<int:baby_id>/checkups", methods=["POST"])
def add_checkup_record(baby_id):
    """添加体检记录（自动同步生成成长记录）"""
    data = request.get_json()
    if not data or not data.get("checkup_date"):
        return jsonify({"success": False, "message": "请填写体检日期"}), 400

    db = get_db()
    height = data.get("height")
    weight = data.get("weight")
    head_circumference = data.get("head_circumference")

    # 保存体检记录
    db.execute(
        """INSERT INTO checkup_records (baby_id, checkup_date, age_months, height, weight,
           head_circumference, doctor, hospital, result, advice, next_checkup_date, note)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (baby_id, data["checkup_date"], data.get("age_months"),
         height, weight, head_circumference,
         data.get("doctor", ""), data.get("hospital", ""), data.get("result", "normal"),
         data.get("advice", ""), data.get("next_checkup_date", ""), data.get("note", ""))
    )

    # 自动同步：如果体检包含身高/体重/头围，自动创建成长记录（避免重复）
    if height is not None or weight is not None or head_circumference is not None:
        # 检查同一天是否已有成长记录
        existing = db.execute(
            "SELECT id FROM growth_records WHERE baby_id = ? AND record_date = ?",
            (baby_id, data["checkup_date"])
        ).fetchone()

        if not existing:
            # 计算 BMI
            bmi = None
            if height and weight:
                try:
                    h_m = float(height) / 100.0
                    bmi = round(float(weight) / (h_m * h_m), 1)
                except (ValueError, ZeroDivisionError):
                    pass

            db.execute(
                """INSERT INTO growth_records (baby_id, record_date, height, weight, head_circumference, bmi, note)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (baby_id, data["checkup_date"], height, weight, head_circumference, bmi,
                 f"体检自动同步: {data.get('hospital', '')}")
            )

    db.commit()
    return jsonify({"success": True, "message": "体检记录已添加（已自动同步到成长记录）"})


@bp.route("/api/checkups/<int:record_id>", methods=["DELETE"])
def delete_checkup_record(record_id):
    """删除体检记录"""
    db = get_db()
    db.execute("DELETE FROM checkup_records WHERE id = ?", (record_id,))
    db.commit()
    return jsonify({"success": True, "message": "已删除"})


@bp.route("/api/babies/<int:baby_id>/checkups", methods=["GET"])
def get_checkup_list(baby_id):
    """获取体检记录列表"""
    db = get_db()
    rows = db.execute("SELECT * FROM checkup_records WHERE baby_id = ? ORDER BY checkup_date DESC", (baby_id,)).fetchall()
    return jsonify({"success": True, "data": rows_to_list(rows)})


# ==================== API: 辅食添加记录 ====================

@bp.route("/api/babies/<int:baby_id>/solid-food", methods=["GET"])
def list_solid_food_records(baby_id):
    """获取辅食记录列表"""
    db = get_db()
    rows = db.execute(
        "SELECT * FROM solid_food_records WHERE baby_id = ? ORDER BY first_try_date DESC",
        (baby_id,)
    ).fetchall()
    return jsonify({"success": True, "data": [dict(r) for r in rows]})


@bp.route("/api/babies/<int:baby_id>/solid-food", methods=["POST"])
def add_solid_food_record(baby_id):
    """添加辅食记录"""
    data = request.get_json()
    if not data or not data.get("food_name") or not data.get("first_try_date"):
        return jsonify({"success": False, "message": "请填写食物名称和首次尝试日期"}), 400

    db = get_db()
    db.execute(
        """INSERT INTO solid_food_records (baby_id, food_name, food_category,
           first_try_date, amount, reaction, reaction_detail, is_favorite, note)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (baby_id, data["food_name"], data.get("food_category", "vegetable"),
         data["first_try_date"], data.get("amount", ""), data.get("reaction", "none"),
         data.get("reaction_detail", ""), data.get("is_favorite", 0), data.get("note", ""))
    )
    db.commit()
    return jsonify({"success": True, "message": "辅食记录已添加"})


@bp.route("/api/solid-food/<int:record_id>", methods=["DELETE"])
def delete_solid_food_record(record_id):
    """删除辅食记录"""
    db = get_db()
    db.execute("DELETE FROM solid_food_records WHERE id = ?", (record_id,))
    db.commit()
    return jsonify({"success": True, "message": "已删除"})


@bp.route("/api/babies/<int:baby_id>/solid-food/<int:record_id>", methods=["GET"])
def get_solid_food_record(baby_id, record_id):
    """按 ID 获取单条辅食记录（编辑回显用）"""
    db = get_db()
    row = db.execute(
        "SELECT * FROM solid_food_records WHERE id = ? AND baby_id = ?",
        (record_id, baby_id),
    ).fetchone()
    if row is None:
        return jsonify({"success": False, "message": "记录不存在"}), 404
    return jsonify({"success": True, "data": dict(row)})


@bp.route("/api/babies/<int:baby_id>/solid-food/<int:record_id>", methods=["PUT"])
def update_solid_food_record(baby_id, record_id):
    """更新辅食记录"""
    data = request.get_json()
    if not data or not data.get("food_name") or not data.get("first_try_date"):
        return jsonify({"success": False, "message": "请填写食物名称和首次尝试日期"}), 400

    db = get_db()
    cur = db.execute(
        """UPDATE solid_food_records
           SET food_name = ?, food_category = ?, first_try_date = ?,
               amount = ?, reaction = ?, reaction_detail = ?, is_favorite = ?, note = ?
           WHERE id = ? AND baby_id = ?""",
        (data["food_name"], data.get("food_category", "vegetable"),
         data["first_try_date"], data.get("amount", ""),
         data.get("reaction", "none"), data.get("reaction_detail", ""),
         data.get("is_favorite", 0), data.get("note", ""),
         record_id, baby_id),
    )
    if cur.rowcount == 0:
        return jsonify({"success": False, "message": "记录不存在"}), 404
    db.commit()
    return jsonify({"success": True, "message": "辅食记录已更新"})


@bp.route("/api/babies/<int:baby_id>/solid-food/stats", methods=["GET"])
def get_solid_food_stats(baby_id):
    """获取辅食统计"""
    db = get_db()
    total = db.execute(
        "SELECT COUNT(*) FROM solid_food_records WHERE baby_id = ?", (baby_id,)
    ).fetchone()[0]
    favorites = db.execute(
        "SELECT COUNT(*) FROM solid_food_records WHERE baby_id = ? AND is_favorite = 1", (baby_id,)
    ).fetchone()[0]
    reactions = db.execute(
        "SELECT COUNT(*) FROM solid_food_records WHERE baby_id = ? AND reaction != 'none'", (baby_id,)
    ).fetchone()[0]
    categories = db.execute(
        """SELECT food_category, COUNT(*) as count FROM solid_food_records
           WHERE baby_id = ? GROUP BY food_category""", (baby_id,)
    ).fetchall()

    return jsonify({"success": True, "data": {
        "total": total,
        "favorites": favorites,
        "reactions": reactions,
        "categories": {row["food_category"]: row["count"] for row in categories}
    }})


# ==================== API: 发育飞跃期 ====================

@bp.route("/api/leaps/<int:record_id>", methods=["DELETE"])
def delete_leap_record(record_id):
    """删除飞跃期记录"""
    db = get_db()
    db.execute("DELETE FROM leap_records WHERE id = ?", (record_id,))
    db.commit()
    return jsonify({"success": True, "message": "已删除"})


@bp.route("/api/babies/<int:baby_id>/leaps", methods=["GET"])
def get_leap_records(baby_id):
    """获取飞跃期记录列表"""
    db = get_db()
    rows = db.execute(
        "SELECT * FROM leap_records WHERE baby_id = ? ORDER BY start_date DESC",
        (baby_id,)
    ).fetchall()
    return jsonify({"success": True, "data": rows_to_list(rows)})


@bp.route("/api/babies/<int:baby_id>/leaps", methods=["POST"])
def add_leap_record(baby_id):
    """添加飞跃期记录"""
    data = request.get_json()
    if not data or not data.get("leap_number") or not data.get("start_date"):
        return jsonify({"success": False, "message": "请填写飞跃期数和开始日期"}), 400
    db = get_db()
    db.execute(
        """INSERT INTO leap_records (baby_id, leap_number, start_date, end_date, is_completed, note)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (baby_id, data["leap_number"], data["start_date"],
         data.get("end_date"), 1 if data.get("is_completed") else 0, data.get("note", ""))
    )
    db.commit()
    return jsonify({"success": True, "message": "记录已添加"})


@bp.route("/api/babies/<int:baby_id>/leaps/predict", methods=["GET"])
def predict_leaps(baby_id):
    """预测飞跃期时间（Wonder Weeks：以预产期为基准，未填则退回生日）"""
    db = get_db()
    baby = db.execute("SELECT birthday FROM babies WHERE id = ?", (baby_id,)).fetchone()
    if not baby:
        return jsonify({"success": False, "message": "未找到宝宝信息"})

    # 需要预产期时取完整行（仅在有 due_date 列时）
    baby_all = db.execute("SELECT * FROM babies WHERE id = ?", (baby_id,)).fetchone()
    baby_d = row_to_dict(baby_all) if baby_all else {}
    schedule = growth_utils.build_leap_schedule(baby["birthday"], baby_d.get("due_date"))
    if schedule is None:
        return jsonify({"success": False, "message": "宝宝生日无效"})

    return jsonify({
        "success": True,
        "data": schedule,
        "base_on_due_date": bool(baby_d.get("due_date"))
    })


# ==================== API: 囟门记录 ====================

@bp.route("/api/babies/<int:baby_id>/fontanelle", methods=["POST"])
def add_fontanelle_record(baby_id):
    """添加囟门检查记录"""
    data = request.get_json()
    if not data or not data.get("check_date"):
        return jsonify({"success": False, "message": "请填写检查日期"}), 400

    db = get_db()
    db.execute(
        """INSERT INTO fontanelle_records (baby_id, check_date, anterior_size,
           anterior_status, posterior_status, note)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (baby_id, data["check_date"], data.get("anterior_size"),
         data.get("anterior_status", "open"), data.get("posterior_status", "open"),
         data.get("note", ""))
    )
    db.commit()
    return jsonify({"success": True, "message": "囟门记录已添加"})


@bp.route("/api/fontanelle/<int:record_id>", methods=["DELETE"])
def delete_fontanelle_record(record_id):
    """删除囟门记录"""
    db = get_db()
    db.execute("DELETE FROM fontanelle_records WHERE id = ?", (record_id,))
    db.commit()
    return jsonify({"success": True, "message": "已删除"})


@bp.route("/api/babies/<int:baby_id>/fontanelle", methods=["GET"])
def get_fontanelle_records(baby_id):
    """获取囟门记录列表"""
    db = get_db()
    rows = db.execute("SELECT * FROM fontanelle_records WHERE baby_id = ? ORDER BY check_date DESC", (baby_id,)).fetchall()
    return jsonify({"success": True, "data": rows_to_list(rows)})


# 成长对比照片/日记路由已迁移到 blueprints/photos.py
