#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xiaoke799
"""
宝宝管理路由 Blueprint。
提供宝宝信息的增删查改功能。
"""

import datetime

from flask import Blueprint, request, jsonify
from utils import get_db, get_baby_age, get_baby_age_months, row_to_dict, rows_to_list, json_body
from user_context import require_admin, require_not_integration
from constants import BABY_SCOPED_TABLES
from validators import validate_date
import safety_code

bp = Blueprint("babies", __name__, url_prefix="/api/babies")

# 预产期与生日的合理间隔上限（天）。
# 为什么不是「预产期不能晚于生日」：早产儿的预产期本就晚于实际生日，
# 而 growth_utils.effective_age_basis 正是用预产期起算**纠正月龄**的
# （见该函数文档：早产儿才真正受影响）——旧规则把预产期卡在生日之前，
# 等于把最需要填预产期的早产儿家庭直接拒之门外，且它从前只作用于 PUT、
# POST 完全没校验，所以同一个门禁里「建早产儿成功」和「预产期晚于生日被拒」
# 才能长期共存不打架。现在改成相差不超过 120 天（约 4 个月）：
# 覆盖极端早产（≤15 周）与过期产，年份看串、把生日填进预产期这类仍会被拒。
DUE_DATE_MAX_GAP_DAYS = 120


@bp.route("", methods=["GET"])
def get_babies():
    """获取宝宝列表"""
    db = get_db()
    babies = db.execute("SELECT * FROM babies ORDER BY birthday DESC").fetchall()
    result = []
    for b in babies:
        baby = dict(b)
        baby["age"] = get_baby_age(baby.get("birthday", ""))
        baby["age_months"] = get_baby_age_months(
            baby.get("birthday", ""), baby.get("due_date")
        )
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
    result["age_months"] = get_baby_age_months(
        result.get("birthday", ""), result.get("due_date")
    )
    return jsonify({"success": True, "data": result})


@bp.route("", methods=["POST"])
@require_not_integration
def add_baby():
    """添加宝宝

    集成 Token（Home Assistant 等）按对外承诺只能读数据 + 写记录类
    （喂养/睡眠/尿布/生长/计时），不能增改宝宝档案。此前这里没挂守卫，
    与 ha-integration/README 的承诺不符。

    这里刻意用 require_not_integration 而不是 require_admin：
    家庭里的非管理员成员也是真人，本就该能添加宝宝，不该被机器凭证的规则误伤。
    """
    data = json_body()
    if not isinstance(data, dict):
        return jsonify({"success": False, "message": "请求格式错误"}), 400

    # POST 此前完全绕过 _validate_baby_payload（只判了非空）：脏生日「8月20号」
    # 能直接入库，之后 clinic-summary 的 strptime 每月都 500，昵称传 1MB 也收。
    # 新建与更新必须过同一道闸，否则「新建松、更新严」迟早漂移。
    err = _validate_baby_payload(data)
    if err:
        return jsonify({"success": False, "message": err}), 400

    db = get_db()
    cursor = db.execute(
        "INSERT INTO babies (name, birthday, gender, due_date) VALUES (?, ?, ?, ?)",
        (data["name"].strip(), data["birthday"], data.get("gender", "other"), data.get("due_date")),
    )
    db.commit()

    baby_id = cursor.lastrowid
    baby = db.execute("SELECT * FROM babies WHERE id = ?", (baby_id,)).fetchone()
    result = row_to_dict(baby)
    result["age"] = get_baby_age(result["birthday"])
    return jsonify({"success": True, "data": result})


def _validate_baby_payload(data, partial=False, current=None):
    """校验宝宝资料。partial=True 时只校验传入了的字段；current 为库中现值。"""
    if not isinstance(data, dict):
        return "请求格式错误"
    if not partial and (not data.get("name") or not data.get("birthday")):
        return "请填写宝宝昵称和生日"
    if "name" in data:
        name = data.get("name")
        if not isinstance(name, str):
            return "昵称必须为文字"
        name = name.strip()
        if not name:
            return "请填写宝宝昵称"
        if len(name) > 50:
            return "昵称不超过 50 字"
    if "birthday" in data:
        birthday = data.get("birthday")
        if not isinstance(birthday, str) or not birthday.strip():
            return "请填写生日"
        ok, birthday_norm, berr = validate_date(birthday, "生日")
        if not ok:
            return berr
        # 归一化回写：'2024-6-1' 这类能解析但不规范的值，落库一律 'YYYY-MM-DD'，
        # 否则以后按 substr(t,1,10) 取日期、ORDER BY 排序还是会错位
        data["birthday"] = birthday_norm
    else:
        birthday_norm = (current or {}).get("birthday")
    if "gender" in data and data.get("gender") not in ("boy", "girl", "other"):
        return "性别取值不正确"
    if "due_date" in data and data.get("due_date"):
        due = data["due_date"]
        if not isinstance(due, str):
            return "预产期格式不正确"
        ok, due_norm, derr = validate_date(due, "预产期")
        if not ok:
            return derr
        data["due_date"] = due_norm
        if birthday_norm:
            try:
                gap = abs(
                    (datetime.date.fromisoformat(due_norm)
                     - datetime.date.fromisoformat(str(birthday_norm)[:10])).days
                )
            except ValueError:
                # 生日本身是历史脏数据（本次请求没带 birthday 时会发生）：
                # 不拿它当判据，避免把「旧数据脏」误报成「这次填错」
                gap = 0
            if gap > DUE_DATE_MAX_GAP_DAYS:
                return "预产期与生日相差过大，请确认是否填写正确"
    if "is_premature" in data:
        # 早产儿标记：健康随访规划按它决定是否展示「高危早产儿增加的随访」档
        flag = data.get("is_premature")
        if flag not in (0, 1, True, False, "0", "1", "true", "false"):
            return "早产标记取值不正确"
        data["is_premature"] = 1 if flag in (1, True, "1", "true") else 0
    if "custom_milk_target" in data:
        # 宝宝级自定义每日奶量目标：空/None = 清除，回到自动估算
        mt = data.get("custom_milk_target")
        if mt in (None, "", "null"):
            data["custom_milk_target"] = None
        else:
            try:
                mt = float(mt)
            except (ValueError, TypeError):
                return "奶量目标必须是数字"
            if not (30 <= mt <= 3000):
                return "奶量目标应在 30-3000ml 之间"
            data["custom_milk_target"] = mt
    return None


@bp.route("/<int:baby_id>", methods=["PUT"])
@require_not_integration
def update_baby(baby_id):
    """修改宝宝信息（昵称 / 生日 / 性别 / 预产期）

    宝宝档案属于资料变更，集成 Token 不该能改（同 add_baby）。
    同样用 require_not_integration：非管理员的家庭成员仍可改。
    """
    data = request.get_json()
    db = get_db()
    existing = db.execute("SELECT * FROM babies WHERE id = ?", (baby_id,)).fetchone()
    if not existing:
        return jsonify({"success": False, "message": "宝宝不存在"}), 404

    err = _validate_baby_payload(data, partial=True, current=row_to_dict(existing))
    if err:
        return jsonify({"success": False, "message": err}), 400
    if not any(k in data for k in ("name", "birthday", "gender", "due_date", "is_premature", "custom_milk_target")):
        return jsonify({"success": False, "message": "没有需要更新的字段"}), 400

    fields = []
    values = []
    for key in ("name", "birthday", "gender", "due_date", "is_premature", "custom_milk_target"):
        if key in data:
            fields.append(f"{key} = ?")
            if key == "name":
                values.append(data[key].strip())
            elif key == "due_date":
                values.append(data.get("due_date") or None)
            elif key == "is_premature":
                values.append(1 if data[key] in (1, True, "1", "true") else 0)
            else:
                values.append(data[key])
    values.append(baby_id)

    db.execute(
        f"UPDATE babies SET {', '.join(fields)}, updated_at = datetime('now', 'localtime') WHERE id = ?",
        tuple(values),
    )
    db.commit()

    baby = db.execute("SELECT * FROM babies WHERE id = ?", (baby_id,)).fetchone()
    result = row_to_dict(baby)
    result["age"] = get_baby_age(result.get("birthday", ""))
    result["age_months"] = get_baby_age_months(
        result.get("birthday", ""), result.get("due_date")
    )
    return jsonify({"success": True, "data": result})


@bp.route("/<int:baby_id>", methods=["DELETE"])
@require_admin
def delete_baby(baby_id):
    """删除宝宝及其所有记录（危险操作，仅管理员，且必须通过安全码）"""
    data = json_body()
    ok, msg = safety_code.verify(get_db(), data.get("safety_code", ""))
    if not ok:
        return jsonify({"success": False, "message": msg, "needSafetyCode": True}), 403

    db = get_db()
    # 先查存在，避免「假成功」：删不存在的 id 也回 success
    existing = db.execute("SELECT id, name FROM babies WHERE id = ?", (baby_id,)).fetchone()
    if not existing:
        return jsonify({"success": False, "message": "宝宝不存在"}), 404
    cur = db.execute("DELETE FROM babies WHERE id = ?", (baby_id,))
    db.commit()
    if cur.rowcount == 0:
        return jsonify({"success": False, "message": "宝宝不存在"}), 404
    return jsonify({"success": True, "message": "已删除"})


@bp.route("/<int:baby_id>/export", methods=["GET"])
@require_admin
def export_baby_data(baby_id):
    """导出指定宝宝的所有数据（仅管理员）"""
    db = get_db()
    baby = db.execute("SELECT * FROM babies WHERE id = ?", (baby_id,)).fetchone()
    if not baby:
        return jsonify({"success": False, "message": "宝宝不存在"}), 404

    # 统一用 constants.BABY_SCOPED_TABLES（此前手写清单漏了 vaccine_details、
    # allergy_history、health_indicators 等，单宝宝导出会静默丢数据）
    baby_scoped_tables = sorted(BABY_SCOPED_TABLES)

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
