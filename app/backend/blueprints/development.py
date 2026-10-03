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

from utils import get_db, row_to_dict, rows_to_list, json_body, normalize_author
from logger import get_logger
from validators import (validate_enum, validate_date, validate_text_length,
                        NOTE_MAX_LENGTH, NAME_MAX_LENGTH)
import growth_utils

bp = Blueprint("development", __name__)
logger = get_logger("development")

# ---- 各记录模块的可选值（逐个与前端下拉的 option value 对齐过，别凭空猜）----
VALID_TEETH_POSITIONS = {"upper", "lower"}
VALID_TEETH_SIDES = {"left", "right", "center"}
VALID_FOOD_CATEGORIES = {"vegetable", "fruit", "meat", "grain", "dairy", "other"}
VALID_FOOD_REACTIONS = {"none", "mild", "severe"}
VALID_FONTANELLE_STATUS = {"open", "closing", "closed"}
LEAP_NUMBER_MIN, LEAP_NUMBER_MAX = 1, 10        # Wonder Weeks 共 10 次飞跃


# ==================== API: 里程碑 ====================

@bp.route("/api/babies/<int:baby_id>/milestones", methods=["GET"])
def list_milestones(baby_id):
    """获取里程碑列表。

    里程碑与「第一次」共用同一张表 milestones，靠 is_first 区分。
    2026-09-19 起「第一次」不再是一个独立页面，已被并进「成长成就」页
    当作一个分类筛选（前端拉全量后本地按 is_first 过滤，不再传 first）；
    这里的 first=1 仅作向后兼容保留（老备份恢复 / 老调用方）。
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
        """INSERT INTO milestones (baby_id, title, description, achieved_date, category, is_first, author)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (baby_id, data["title"], data.get("description", ""),
         data["achieved_date"], data.get("category", "other"),
         1 if data.get("is_first") else 0, normalize_author(data))
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
    cur = db.execute("DELETE FROM milestones WHERE id = ? AND baby_id = ?", (record_id, baby_id))
    db.commit()
    if cur.rowcount == 0:
        return jsonify({"success": False, "message": "里程碑不存在"}), 404
    return jsonify({"success": True, "message": "里程碑已删除"})


# ==================== API: ASQ筛查 ====================
# 定位先说清楚：这里是**记录分数**的地方，不是判读工具。
# ASQ-3 的判读要按月龄区间、对照授权量表上的观察区上下限（cutoff），
# 那是版权常量且必须由受过训练的专业人员来做，本工具一律不做。
# 所以下面只有「该用哪一份问卷」这一层公开信息，不内置任何界值。

# ASQ-3 问卷月份表（21 份，覆盖 1~66 月龄无缺口）
# 来源：ASQ-3 Technical Report 的 Administration chart，与官方培训材料一致：
#   2~24 月每 2 个月一份（2/4/6/8/9/10/12/14/16/18/20/22/24）
#   27/30/33/36 月每 3 个月一份
#   42/48/54/60 月每 6 个月一份
# 每条：(问卷月龄, 施测下限, 施测上限)
ASQ_INTERVALS = [
    (2, "1个月0天", "2个月30天"),
    (4, "3个月0天", "4个月30天"),
    (6, "5个月0天", "6个月30天"),
    (8, "7个月0天", "8个月30天"),
    (9, "9个月0天", "9个月30天"),
    (10, "10个月0天", "10个月30天"),
    (12, "11个月0天", "12个月30天"),
    (14, "13个月0天", "14个月30天"),
    (16, "15个月0天", "16个月30天"),
    (18, "17个月0天", "18个月30天"),
    (20, "19个月0天", "20个月30天"),
    (22, "21个月0天", "22个月30天"),
    (24, "23个月0天", "25个月15天"),
    (27, "25个月16天", "28个月15天"),
    (30, "28个月16天", "31个月15天"),
    (33, "31个月16天", "34个月15天"),
    (36, "34个月16天", "38个月30天"),
    (42, "39个月0天", "44个月30天"),
    (48, "45个月0天", "50个月30天"),
    (54, "51个月0天", "56个月30天"),
    (60, "57个月0天", "66个月0天"),
]

# 五个领域：入库列名 → 中文名
ASQ_AREAS = [
    ("communication_score", "沟通"),
    ("gross_motor_score", "大运动"),
    ("fine_motor_score", "精细运动"),
    ("problem_solving_score", "解决问题"),
    ("personal_social_score", "个人社交"),
]

# 每个领域 6 题、每题 0/5/10 分 ⇒ 满分 60
ASQ_MAX_AREA_SCORE = 60
ASQ_MAX_TOTAL = ASQ_MAX_AREA_SCORE * len(ASQ_AREAS)

ASQ_DISCLAIMER = (
    "本工具只帮你记录 ASQ 问卷的分数，**不能代替专业筛查**。"
    "结果仅供参考，不构成任何医疗诊断或发育评估结论。"
    "早产宝宝请用矫正月龄（早产满 3 周以上，矫正到 2 岁）选择问卷。"
    "如对发育有任何担心，请前往正规医院的儿童保健科/儿科做标准筛查。"
)

ASQ_MIN_MONTHS = 1
ASQ_MAX_MONTHS = 66


def _asq_pick_interval(months):
    """按官方施测节奏，给定月龄推荐用哪一份问卷。返回 (问卷月龄, 说明)。"""
    if months <= 2:
        return 2, "当前月龄在 2 月龄问卷的施测窗口内（1 个月 0 天 ~ 2 个月 30 天）"
    for iv, lo, hi in ASQ_INTERVALS:
        # 窗口的月数跨度最大 5 个月（60 月龄那档），用问卷月龄做就近匹配
        if months <= iv:
            return iv, "%d 月龄问卷的施测窗口为 %s ~ %s" % (iv, lo, hi)
    return 60, "已超出 60 月龄问卷的施测窗口（%s ~ %s），更大的月龄建议按机构安排" % (
        ASQ_INTERVALS[-1][1], ASQ_INTERVALS[-1][2])


def _asq_int(data, key, lo, hi, default=None):
    """严格取整数：bool、字符串、浮点、超范围一律判非法。

    这里必须严：旧写法 data.get(key, 0) 直接参与求和，传字符串会 TypeError → 500，
    而家长看到的只是「保存失败」，不知道是自己填错了。

    缺字段 vs 显式空值是两回事：
      - 缺字段（没传这个 key）→ 用 default（分数可以只填一部分）
      - 显式传 null / 空串 → 判非法（客户端发了脏数据，不能悄悄当成 0）
    """
    if key not in data:
        if default is not None:
            return default
        raise ValueError("请填写%s" % key)
    v = data[key]
    if v is None or v == "":
        raise ValueError("%s 不能为空" % key)
    if isinstance(v, bool):
        raise ValueError("%s 必须是数字" % key)
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise ValueError("%s 必须是数字" % key)
    if n < lo or n > hi:
        raise ValueError("%s 应在 %d~%d 之间" % (key, lo, hi))
    return n


def _validate_asq_payload(data):
    """校验并归一化 ASQ 提交数据，非法就抛 ValueError（由调用方转 400）。"""
    months = _asq_int(data, "age_months", ASQ_MIN_MONTHS, ASQ_MAX_MONTHS)
    scores = []
    for col, label in ASQ_AREAS:
        scores.append(_asq_int(data, col, 0, ASQ_MAX_AREA_SCORE, default=0))

    date_str = str(data.get("screening_date") or "").strip()
    if not date_str:
        date_str = datetime.date.today().strftime("%Y-%m-%d")
    else:
        try:
            datetime.datetime.strptime(date_str[:10], "%Y-%m-%d")
        except ValueError:
            raise ValueError("筛查日期格式不正确")

    note = str(data.get("note") or "")[:500]
    return months, scores, sum(scores), date_str, note


@bp.route("/api/asq/info", methods=["GET"])
def get_asq_info():
    """ASQ 问卷月份表 / 领域 / 免责说明（前端渲染下拉与提示用）"""
    return jsonify({
        "success": True,
        "data": {
            "intervals": [
                {"months": iv, "from": lo, "to": hi} for iv, lo, hi in ASQ_INTERVALS
            ],
            "areas": [{"key": col, "label": label} for col, label in ASQ_AREAS],
            "max_area_score": ASQ_MAX_AREA_SCORE,
            "max_total": ASQ_MAX_TOTAL,
            "min_months": ASQ_MIN_MONTHS,
            "max_months": ASQ_MAX_MONTHS,
            "disclaimer": ASQ_DISCLAIMER,
        },
    })


@bp.route("/api/babies/<int:baby_id>/asq", methods=["POST"])
def add_asq_screening(baby_id):
    """添加 ASQ 筛查记录

    只存原始分，不做临床判读：标准 ASQ-3 要按月龄区间对照常模观察区上下限，
    绝不是五维求和卡 150/100——那会误导家长。所以 result 固定 score_only。
    """
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not data:
        return jsonify({"success": False, "message": "请填写筛查内容"}), 400

    if not get_db().execute(
            "SELECT id FROM babies WHERE id = ?", (baby_id,)).fetchone():
        return jsonify({"success": False, "message": "宝宝不存在"}), 404

    try:
        months, scores, total, date_str, note = _validate_asq_payload(data)
    except ValueError as e:
        return jsonify({"success": False, "message": str(e)}), 400

    comm, gross, fine, prob, pers = scores
    result = "score_only"
    interval, interval_note = _asq_pick_interval(months)

    db = get_db()
    db.execute(
        """INSERT INTO asq_screenings (baby_id, screening_date, age_months,
           communication_score, gross_motor_score, fine_motor_score,
           problem_solving_score, personal_social_score, total_score, result, note)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (baby_id, date_str, months, comm, gross, fine, prob, pers, total, result, note)
    )
    db.commit()
    return jsonify({
        "success": True,
        "message": "筛查记录已添加",
        "data": {
            "total_score": total,
            "max_total": ASQ_MAX_TOTAL,
            "result": result,
            "interval": interval,
            "interval_note": interval_note,
            "disclaimer": ASQ_DISCLAIMER,
        },
    })


@bp.route("/api/asq/<int:record_id>", methods=["DELETE"])
def delete_asq_screening(record_id):
    """删除ASQ筛查记录"""
    db = get_db()
    cur = db.execute("DELETE FROM asq_screenings WHERE id = ?", (record_id,))
    db.commit()
    if cur.rowcount == 0:
        return jsonify({"success": False, "message": "筛查记录不存在"}), 404
    return jsonify({"success": True, "message": "已删除"})


@bp.route("/api/babies/<int:baby_id>/asq", methods=["GET"])
def get_asq_screenings(baby_id):
    """获取ASQ筛查记录列表"""
    try:
        db = get_db()
        rows = db.execute("SELECT * FROM asq_screenings WHERE baby_id = ? ORDER BY screening_date DESC", (baby_id,)).fetchall()
        screenings = rows_to_list(rows)
        # 附上「每条记录对应哪一份问卷」，方便回看时知道当时做的是哪个月的量表
        for r in screenings:
            try:
                iv, _note = _asq_pick_interval(int(r.get("age_months") or 0))
                r["interval"] = iv
            except (TypeError, ValueError):
                r["interval"] = None
        return jsonify({
            "success": True,
            "screenings": screenings,
            "disclaimer": ASQ_DISCLAIMER,
            "max_total": ASQ_MAX_TOTAL,
            "max_area_score": ASQ_MAX_AREA_SCORE,
        })
    except Exception as e:
        logger.error("get_asq_screenings error: %s", e, exc_info=True)
        return jsonify({
            "success": True,
            "screenings": [],
            "disclaimer": ASQ_DISCLAIMER,
        })


# ==================== API: 出牙记录 ====================

def _validate_teething_payload(data):
    """校验出牙记录公共字段，返回 (values, error_response)。

    此前这个模块**没有任何校验**：日期可以填 "8月20号"、牙位可以填任意字符串，
    都会原样入库；而 erupt_date 是 DATE 语义（列表按它排序、前端按天分组），
    格式一乱整个时间线就错位。
    """
    ok, tooth_name, err = validate_text_length(data.get("tooth_name"), NAME_MAX_LENGTH, "牙齿名称")
    if not ok:
        return None, (jsonify({"success": False, "message": err}), 400)

    ok, erupt_date, err = validate_date(data.get("erupt_date"), "萌出日期")
    if not ok:
        return None, (jsonify({"success": False, "message": err}), 400)

    ok, position, err = validate_enum(data.get("position"), VALID_TEETH_POSITIONS, "牙位（上/下颌）")
    if not ok:
        return None, (jsonify({"success": False, "message": err}), 400)

    ok, side, err = validate_enum(data.get("side"), VALID_TEETH_SIDES, "左右位置")
    if not ok:
        return None, (jsonify({"success": False, "message": err}), 400)

    ok, note, err = validate_text_length(data.get("note"), NOTE_MAX_LENGTH, "备注")
    if not ok:
        return None, (jsonify({"success": False, "message": err}), 400)

    return (tooth_name, erupt_date, position or "", side or "", note or ""), None


@bp.route("/api/babies/<int:baby_id>/teething", methods=["POST"])
def add_teething_record(baby_id):
    """添加出牙记录"""
    data = json_body()
    if not data.get("tooth_name") or not data.get("erupt_date"):
        return jsonify({"success": False, "message": "请填写牙齿名称和萌出日期"}), 400

    values, err_resp = _validate_teething_payload(data)
    if err_resp:
        return err_resp

    db = get_db()
    db.execute(
        """INSERT INTO teething_records (baby_id, tooth_name, erupt_date, position, side, note)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (baby_id,) + tuple(values),
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
    data = json_body()
    if not data.get("tooth_name") or not data.get("erupt_date"):
        return jsonify({"success": False, "message": "请填写牙齿名称和萌出日期"}), 400

    values, err_resp = _validate_teething_payload(data)
    if err_resp:
        return err_resp

    db = get_db()
    cur = db.execute(
        """UPDATE teething_records
           SET tooth_name = ?, erupt_date = ?, position = ?, side = ?, note = ?
           WHERE id = ? AND baby_id = ?""",
        tuple(values) + (record_id, baby_id),
    )
    if cur.rowcount == 0:
        return jsonify({"success": False, "message": "记录不存在"}), 404
    db.commit()
    return jsonify({"success": True, "message": "出牙记录已更新"})


@bp.route("/api/teething/<int:record_id>", methods=["DELETE"])
def delete_teething_record(record_id):
    """删除出牙记录（不存在的 id 返回 404）"""
    db = get_db()
    cur = db.execute("DELETE FROM teething_records WHERE id = ?", (record_id,))
    db.commit()
    if cur.rowcount == 0:
        return jsonify({"success": False, "message": "记录不存在"}), 404
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
           head_circumference, doctor, hospital, result, advice, next_checkup_date, note, author)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (baby_id, data["checkup_date"], data.get("age_months"),
         height, weight, head_circumference,
         data.get("doctor", ""), data.get("hospital", ""), data.get("result", "normal"),
         data.get("advice", ""), data.get("next_checkup_date", ""), data.get("note", ""),
         normalize_author(data))
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
    cur = db.execute("DELETE FROM checkup_records WHERE id = ?", (record_id,))
    db.commit()
    if cur.rowcount == 0:
        return jsonify({"success": False, "message": "体检记录不存在"}), 404
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


def _validate_solid_food_payload(data):
    """校验辅食记录公共字段，返回 (values, error_response)。

    分类与过敏反应以前完全不校验：填错分类会让筛选（前端按 food_category 过滤）
    查不到这条，过敏反应写错则可能把"严重反应"漏判成普通值 —— 这两项是排查
    食物过敏的关键字段，值必须收敛到白名单。
    """
    ok, food_name, err = validate_text_length(data.get("food_name"), NAME_MAX_LENGTH, "食物名称")
    if not ok:
        return None, (jsonify({"success": False, "message": err}), 400)

    ok, first_try_date, err = validate_date(data.get("first_try_date"), "首次尝试日期")
    if not ok:
        return None, (jsonify({"success": False, "message": err}), 400)

    ok, food_category, err = validate_enum(data.get("food_category"), VALID_FOOD_CATEGORIES, "食物分类")
    if not ok:
        return None, (jsonify({"success": False, "message": err}), 400)

    ok, reaction, err = validate_enum(data.get("reaction"), VALID_FOOD_REACTIONS, "过敏反应")
    if not ok:
        return None, (jsonify({"success": False, "message": err}), 400)

    ok, note, err = validate_text_length(data.get("note"), NOTE_MAX_LENGTH, "备注")
    if not ok:
        return None, (jsonify({"success": False, "message": err}), 400)

    return (
        food_name,
        food_category or "vegetable",
        first_try_date,
        str(data.get("amount") or "")[:50],
        reaction or "none",
        str(data.get("reaction_detail") or "")[:200],
        1 if data.get("is_favorite") else 0,
        note or "",
    ), None


@bp.route("/api/babies/<int:baby_id>/solid-food", methods=["POST"])
def add_solid_food_record(baby_id):
    """添加辅食记录"""
    data = json_body()
    if not data.get("food_name") or not data.get("first_try_date"):
        return jsonify({"success": False, "message": "请填写食物名称和首次尝试日期"}), 400

    values, err_resp = _validate_solid_food_payload(data)
    if err_resp:
        return err_resp

    db = get_db()
    author = normalize_author(data)
    db.execute(
        """INSERT INTO solid_food_records (baby_id, food_name, food_category,
           first_try_date, amount, reaction, reaction_detail, is_favorite, note, author)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (baby_id, author) + tuple(values)
    )
    db.commit()
    return jsonify({"success": True, "message": "辅食记录已添加"})


@bp.route("/api/solid-food/<int:record_id>", methods=["DELETE"])
def delete_solid_food_record(record_id):
    """删除辅食记录（不存在的 id 返回 404）"""
    db = get_db()
    cur = db.execute("DELETE FROM solid_food_records WHERE id = ?", (record_id,))
    db.commit()
    if cur.rowcount == 0:
        return jsonify({"success": False, "message": "记录不存在"}), 404
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
    data = json_body()
    if not data.get("food_name") or not data.get("first_try_date"):
        return jsonify({"success": False, "message": "请填写食物名称和首次尝试日期"}), 400

    values, err_resp = _validate_solid_food_payload(data)
    if err_resp:
        return err_resp

    db = get_db()
    cur = db.execute(
        """UPDATE solid_food_records
           SET food_name = ?, food_category = ?, first_try_date = ?,
               amount = ?, reaction = ?, reaction_detail = ?, is_favorite = ?, note = ?
           WHERE id = ? AND baby_id = ?""",
        tuple(values) + (record_id, baby_id),
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
    """删除飞跃期记录（不存在的 id 返回 404）"""
    db = get_db()
    cur = db.execute("DELETE FROM leap_records WHERE id = ?", (record_id,))
    db.commit()
    if cur.rowcount == 0:
        return jsonify({"success": False, "message": "记录不存在"}), 404
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
    data = json_body()
    if not data.get("leap_number") or not data.get("start_date"):
        return jsonify({"success": False, "message": "请填写飞跃期数和开始日期"}), 400

    # 飞跃期数限定在 Wonder Weeks 的 1~10：早期不校验，填 99 也能入库，
    # 前端「第 N 次飞跃」和预测表就对不上了
    try:
        leap_number = int(data["leap_number"])
    except (TypeError, ValueError):
        return jsonify({"success": False, "message": "飞跃期数需要是数字"}), 400
    if not (LEAP_NUMBER_MIN <= leap_number <= LEAP_NUMBER_MAX):
        return jsonify({"success": False,
                        "message": "飞跃期数需在 %d~%d 之间" % (LEAP_NUMBER_MIN, LEAP_NUMBER_MAX)}), 400

    ok, start_date, err = validate_date(data.get("start_date"), "开始日期")
    if not ok:
        return jsonify({"success": False, "message": err}), 400

    end_date = None
    if data.get("end_date"):
        ok, end_date, err = validate_date(data.get("end_date"), "结束日期")
        if not ok:
            return jsonify({"success": False, "message": err}), 400
        if end_date < start_date:
            return jsonify({"success": False, "message": "结束日期不能早于开始日期"}), 400

    ok, note, err = validate_text_length(data.get("note"), NOTE_MAX_LENGTH, "备注")
    if not ok:
        return jsonify({"success": False, "message": err}), 400

    db = get_db()
    db.execute(
        """INSERT INTO leap_records (baby_id, leap_number, start_date, end_date, is_completed, note)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (baby_id, leap_number, start_date, end_date,
         1 if data.get("is_completed") else 0, note or "")
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
    data = json_body()
    if not data.get("check_date"):
        return jsonify({"success": False, "message": "请填写检查日期"}), 400

    ok, check_date, err = validate_date(data.get("check_date"), "检查日期")
    if not ok:
        return jsonify({"success": False, "message": err}), 400

    ok, anterior_status, err = validate_enum(data.get("anterior_status"), VALID_FONTANELLE_STATUS, "前囟状态")
    if not ok:
        return jsonify({"success": False, "message": err}), 400
    ok, posterior_status, err = validate_enum(data.get("posterior_status"), VALID_FONTANELLE_STATUS, "后囟状态")
    if not ok:
        return jsonify({"success": False, "message": err}), 400

    # 前囟大小（cm）：此前不校验，能存进 "很大" 或负数，
    # 前端画的闭合趋势图就直接被一个脏点带偏
    anterior_size = data.get("anterior_size")
    if anterior_size not in (None, ""):
        try:
            anterior_size = float(anterior_size)
        except (TypeError, ValueError):
            return jsonify({"success": False, "message": "前囟大小需要是数字（cm）"}), 400
        if not (0 <= anterior_size <= 10):
            return jsonify({"success": False, "message": "前囟大小应在 0~10cm 之间"}), 400
    else:
        anterior_size = None

    ok, note, err = validate_text_length(data.get("note"), NOTE_MAX_LENGTH, "备注")
    if not ok:
        return jsonify({"success": False, "message": err}), 400

    db = get_db()
    db.execute(
        """INSERT INTO fontanelle_records (baby_id, check_date, anterior_size,
           anterior_status, posterior_status, note)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (baby_id, check_date, anterior_size,
         anterior_status or "open", posterior_status or "open", note or "")
    )
    db.commit()
    return jsonify({"success": True, "message": "囟门记录已添加"})


@bp.route("/api/fontanelle/<int:record_id>", methods=["DELETE"])
def delete_fontanelle_record(record_id):
    """删除囟门记录（不存在的 id 返回 404）"""
    db = get_db()
    cur = db.execute("DELETE FROM fontanelle_records WHERE id = ?", (record_id,))
    db.commit()
    if cur.rowcount == 0:
        return jsonify({"success": False, "message": "记录不存在"}), 404
    return jsonify({"success": True, "message": "已删除"})


@bp.route("/api/babies/<int:baby_id>/fontanelle", methods=["GET"])
def get_fontanelle_records(baby_id):
    """获取囟门记录列表"""
    db = get_db()
    rows = db.execute("SELECT * FROM fontanelle_records WHERE baby_id = ? ORDER BY check_date DESC", (baby_id,)).fetchall()
    return jsonify({"success": True, "data": rows_to_list(rows)})


# 成长对比照片/日记路由已迁移到 blueprints/photos.py
