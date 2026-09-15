#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xiaoke799
"""
比价记账路由 Blueprint。
提供多品类育儿用品比价（product_prices）与支出记账（expense_records）功能：
- 用品价格增删改查 + 按品类/规格/品牌筛选
- 单价计算（价格 ÷ 包装量）与性价比排序（最划算置顶）
- 支出记录增删查 + 月度汇总 / 分类统计 / 近 6 个月趋势
"""

import datetime
from flask import Blueprint, request, jsonify

from utils import get_db, json_body

bp = Blueprint("shopping", __name__)

# 支持的用品品类（key 与前端保持一致）
VALID_CATEGORIES = {
    "diaper": "纸尿裤",
    "formula": "奶粉",
    "wipes": "湿巾",
    "food": "辅食",
    "care": "洗护用品",
    "toy": "玩具",
    "other": "其他",
}

# 记账支出分类
VALID_EXPENSE_CATEGORIES = {
    "diaper": "纸尿裤",
    "formula": "奶粉辅食",
    "daily": "日常用品",
    "medical": "医疗健康",
    "toy": "玩具绘本",
    "education": "早教教育",
    "travel": "出行游玩",
    "other": "其他",
}


def _to_float(value, default=0.0):
    try:
        result = float(value)
        return result if result == result else default  # 过滤 NaN
    except (TypeError, ValueError):
        return default


def _validate_category(category, valid_set, label):
    category = str(category or "").strip()
    if category not in valid_set:
        return None, f"{label}无效"
    return category, None


def _group_key(item):
    """比价分组键：同品类 + 同单位。

    只有这两个都一样，单价才具备可比性：
    纸尿裤的 ¥/片 与湿巾的 ¥/片 单位字面相同但不能互相比较（品类不同），
    湿巾的 ¥/片 与 ¥/包 也不能比较（单位不同）。
    """
    return (item.get("category") or "other", (item.get("unit") or "件").strip() or "件")


# ==================== 用品比价 ====================

@bp.route("/api/product-prices", methods=["GET"])
def list_product_prices():
    """获取用品价格列表（支持品类/规格/品牌筛选）。

    关键：**排序与「最划算」判定都限定在「同品类 + 同单位」内**。
    不同品类（湿巾 ¥0.05/片 vs 纸尿裤 ¥1.2/片）、不同单位（¥/罐 vs ¥/g）
    的单价比不出任何结论——旧版把全库按单价升序排，结果永远是最便宜的那个品类垫底置顶，
    "最划算"徽章也乱标。所以这里按品类聚拢、组内比价，并把比较结果算好给前端用：

    - best_unit_price：同品类同单位内的最低单价（标杆）
    - peer_count：同一标杆下有几条可比记录（<2 说明这组没有可比对象）
    - is_best：仅当组内 ≥2 条可比记录且自己是最低价时才为 True
    - vs_best_pct：比标杆贵多少百分比（0 表示就是最低价）
    """
    db = get_db()
    category = request.args.get("category", "").strip()
    spec = request.args.get("spec", "").strip()
    brand = request.args.get("brand", "").strip()

    query = "SELECT * FROM product_prices WHERE 1=1"
    params = []
    if category:
        query += " AND category = ?"
        params.append(category)
    if spec:
        query += " AND spec LIKE ?"
        params.append(f"%{spec}%")
    if brand:
        query += " AND (brand LIKE ? OR series LIKE ?)"
        like = f"%{brand}%"
        params.extend([like, like])
    query += " ORDER BY updated_at DESC"

    rows = db.execute(query, params).fetchall()
    data = []
    for r in rows:
        item = dict(r)
        size = _to_float(item.get("package_size"))
        price = _to_float(item.get("price"))
        item["unit_price"] = round(price / size, 4) if size > 0 and price > 0 else None
        data.append(item)

    # 同品类 + 同单位 -> 一组，组内取最低单价当标杆
    groups = {}
    for item in data:
        if item["unit_price"] is None:
            continue
        groups.setdefault(_group_key(item), []).append(item["unit_price"])

    for item in data:
        peers = groups.get(_group_key(item)) or []
        best = min(peers) if peers else None
        item["best_unit_price"] = round(best, 4) if best is not None else None
        item["peer_count"] = len(peers)
        item["is_best"] = bool(best is not None and len(peers) >= 2 and item["unit_price"] == best)
        # 差价只在「真的比较过」（组内 ≥2 条）时给值；单条成组时给 None 而不是 0，
        # 免得下游把 0 误读成"它就是最低价"
        if len(peers) >= 2 and best and best > 0 and item["unit_price"] is not None:
            item["vs_best_pct"] = round((item["unit_price"] - best) / best * 100, 1)
        else:
            item["vs_best_pct"] = None

    # 按品类（沿用 VALID_CATEGORIES 的顺序）聚拢，组内按单位 + 单价升序，无单价沉底
    cat_order = {k: i for i, k in enumerate(VALID_CATEGORIES)}
    data.sort(key=lambda x: (
        cat_order.get(x["category"], len(cat_order)),
        x.get("unit") or "",
        x["unit_price"] is None,
        x["unit_price"] or 0,
    ))
    return jsonify({"success": True, "data": data})


@bp.route("/api/product-prices", methods=["POST"])
def add_product_price():
    """添加用品价格记录"""
    data = json_body()

    brand = str(data.get("brand") or "").strip()
    if not brand:
        return jsonify({"success": False, "message": "请填写品牌"}), 400
    if len(brand) > 50:
        return jsonify({"success": False, "message": "品牌名称过长"}), 400

    category, err = _validate_category(data.get("category"), VALID_CATEGORIES, "品类")
    if err:
        return jsonify({"success": False, "message": err}), 400

    price = _to_float(data.get("price"))
    if price <= 0:
        return jsonify({"success": False, "message": "请填写正确的价格"}), 400

    package_size = _to_float(data.get("package_size"))
    if package_size < 0:
        return jsonify({"success": False, "message": "包装量不能为负数"}), 400

    db = get_db()
    db.execute(
        """INSERT INTO product_prices
           (category, brand, series, spec, package_size, unit, price, purchase_channel, purchase_date, note)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            category,
            brand,
            str(data.get("series") or "").strip()[:50],
            str(data.get("spec") or "").strip()[:30],
            package_size,
            str(data.get("unit") or "件").strip()[:6],
            round(price, 2),
            str(data.get("purchase_channel") or "").strip()[:50],
            str(data.get("purchase_date") or "").strip()[:10],
            str(data.get("note") or "").strip()[:200],
        ),
    )
    db.commit()
    return jsonify({"success": True, "message": "已添加价格记录"})


@bp.route("/api/product-prices/<int:record_id>", methods=["PUT"])
def update_product_price(record_id):
    """修改用品价格记录"""
    data = json_body()

    brand = str(data.get("brand") or "").strip()
    if not brand:
        return jsonify({"success": False, "message": "请填写品牌"}), 400

    category, err = _validate_category(data.get("category"), VALID_CATEGORIES, "品类")
    if err:
        return jsonify({"success": False, "message": err}), 400

    price = _to_float(data.get("price"))
    if price <= 0:
        return jsonify({"success": False, "message": "请填写正确的价格"}), 400

    # 与 POST 保持同一套校验：旧版 PUT 漏了包装量校验，
    # 编辑时能把包装量改成负数（负数包装量算不出单价，记录会静默失去比价能力）
    package_size = _to_float(data.get("package_size"))
    if package_size < 0:
        return jsonify({"success": False, "message": "包装量不能为负数"}), 400

    db = get_db()
    cur = db.execute(
        """UPDATE product_prices
           SET category = ?, brand = ?, series = ?, spec = ?, package_size = ?, unit = ?,
               price = ?, purchase_channel = ?, purchase_date = ?, note = ?,
               updated_at = datetime('now', 'localtime')
           WHERE id = ?""",
        (
            category,
            brand,
            str(data.get("series") or "").strip()[:50],
            str(data.get("spec") or "").strip()[:30],
            package_size,
            str(data.get("unit") or "件").strip()[:6],
            round(price, 2),
            str(data.get("purchase_channel") or "").strip()[:50],
            str(data.get("purchase_date") or "").strip()[:10],
            str(data.get("note") or "").strip()[:200],
            record_id,
        ),
    )
    db.commit()
    if cur.rowcount == 0:
        return jsonify({"success": False, "message": "记录不存在"}), 404
    return jsonify({"success": True, "message": "已更新"})


@bp.route("/api/product-prices/<int:record_id>", methods=["DELETE"])
def delete_product_price(record_id):
    """删除用品价格记录（不存在的 id 返回 404，避免前端"已删除"但其实没删）"""
    db = get_db()
    cur = db.execute("DELETE FROM product_prices WHERE id = ?", (record_id,))
    db.commit()
    if cur.rowcount == 0:
        return jsonify({"success": False, "message": "记录不存在"}), 404
    return jsonify({"success": True})


# ==================== 支出记账 ====================

@bp.route("/api/expenses", methods=["GET"])
def list_expenses():
    """获取支出记录列表（按月份/分类筛选，日期倒序）"""
    db = get_db()
    month = request.args.get("month", "").strip()      # YYYY-MM
    category = request.args.get("category", "").strip()

    query = "SELECT * FROM expense_records WHERE 1=1"
    params = []
    if month:
        query += " AND substr(expense_date, 1, 7) = ?"
        params.append(month)
    if category:
        query += " AND category = ?"
        params.append(category)
    query += " ORDER BY expense_date DESC, id DESC LIMIT 500"

    rows = db.execute(query, params).fetchall()
    return jsonify({"success": True, "data": [dict(r) for r in rows]})


@bp.route("/api/expenses", methods=["POST"])
def add_expense():
    """添加支出记录"""
    data = json_body()

    category, err = _validate_category(data.get("category"), VALID_EXPENSE_CATEGORIES, "支出分类")
    if err:
        return jsonify({"success": False, "message": err}), 400

    amount = _to_float(data.get("amount"))
    if amount <= 0:
        return jsonify({"success": False, "message": "请填写正确的金额"}), 400

    expense_date = str(data.get("expense_date") or "").strip()[:10]
    try:
        datetime.datetime.strptime(expense_date, "%Y-%m-%d")
    except ValueError:
        return jsonify({"success": False, "message": "日期格式应为 YYYY-MM-DD"}), 400

    db = get_db()
    db.execute(
        """INSERT INTO expense_records (category, amount, item_name, purchase_channel, expense_date, note)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            category,
            round(amount, 2),
            str(data.get("item_name") or "").strip()[:100],
            str(data.get("purchase_channel") or "").strip()[:50],
            expense_date,
            str(data.get("note") or "").strip()[:200],
        ),
    )
    db.commit()
    return jsonify({"success": True, "message": "已记账"})


@bp.route("/api/expenses/<int:record_id>", methods=["PUT"])
def update_expense(record_id):
    """更新支出记录"""
    data = json_body()

    category, err = _validate_category(data.get("category"), VALID_EXPENSE_CATEGORIES, "支出分类")
    if err:
        return jsonify({"success": False, "message": err}), 400

    amount = _to_float(data.get("amount"))
    if amount <= 0:
        return jsonify({"success": False, "message": "请填写正确的金额"}), 400

    expense_date = str(data.get("expense_date") or "").strip()[:10]
    try:
        datetime.datetime.strptime(expense_date, "%Y-%m-%d")
    except ValueError:
        return jsonify({"success": False, "message": "日期格式应为 YYYY-MM-DD"}), 400

    db = get_db()
    cur = db.execute(
        """UPDATE expense_records
           SET category=?, amount=?, item_name=?, purchase_channel=?, expense_date=?, note=?
           WHERE id=?""",
        (
            category,
            round(amount, 2),
            str(data.get("item_name") or "").strip()[:100],
            str(data.get("purchase_channel") or "").strip()[:50],
            expense_date,
            str(data.get("note") or "").strip()[:200],
            record_id,
        ),
    )
    db.commit()
    if cur.rowcount == 0:
        return jsonify({"success": False, "message": "记录不存在"}), 404
    return jsonify({"success": True, "message": "已更新"})


@bp.route("/api/expenses/<int:record_id>", methods=["DELETE"])
def delete_expense(record_id):
    """删除支出记录（不存在的 id 返回 404）"""
    db = get_db()
    cur = db.execute("DELETE FROM expense_records WHERE id = ?", (record_id,))
    db.commit()
    if cur.rowcount == 0:
        return jsonify({"success": False, "message": "记录不存在"}), 404
    return jsonify({"success": True})


@bp.route("/api/expenses/summary", methods=["GET"])
def expense_summary():
    """记账汇总：指定月份合计与分类占比 + 近 6 个月支出趋势"""
    db = get_db()

    month = request.args.get("month", "").strip()
    if not month:
        month = datetime.date.today().strftime("%Y-%m")
    # 简单校验 YYYY-MM
    try:
        datetime.datetime.strptime(month + "-01", "%Y-%m-%d")
    except ValueError:
        return jsonify({"success": False, "message": "月份格式应为 YYYY-MM"}), 400

    # 当月合计
    month_total = db.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM expense_records WHERE substr(expense_date, 1, 7) = ?",
        (month,),
    ).fetchone()[0]

    # 当月分类统计（含品名样例）
    by_category = []
    rows = db.execute(
        """SELECT category, SUM(amount) AS total, COUNT(*) AS cnt
           FROM expense_records
           WHERE substr(expense_date, 1, 7) = ?
           GROUP BY category ORDER BY total DESC""",
        (month,),
    ).fetchall()
    for r in rows:
        by_category.append({
            "category": r["category"],
            "label": VALID_EXPENSE_CATEGORIES.get(r["category"], r["category"]),
            "total": round(r["total"], 2),
            "count": r["cnt"],
        })

    # 近 6 个月趋势（含当前月）
    months = []
    y, m = int(month[:4]), int(month[5:7])
    for _ in range(6):
        months.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            y -= 1
            m = 12
    months.reverse()

    trend = []
    for mm in months:
        total = db.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM expense_records WHERE substr(expense_date, 1, 7) = ?",
            (mm,),
        ).fetchone()[0]
        trend.append({"month": mm, "total": round(total, 2)})

    return jsonify({
        "success": True,
        "data": {
            "month": month,
            "month_total": round(month_total, 2),
            "by_category": by_category,
            "trend": trend,
        },
    })
