#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xiaoke799
"""
全局搜索路由 Blueprint。

数据攒久了没有搜索就是死数据：日记写过的某句话、上次过敏是什么时候、
那次体检医生怎么说的，靠翻页面是翻不出来的。

设计取舍：
- 用 LIKE %q% 而不是 FTS5。数据量是单个家庭、单库几万行的量级，
  LIKE 足够快；上 FTS 要维护虚拟表与触发器，迁移风险不成比例。
- 表驱动（_SEARCH_SOURCES），和时间线同一套写法：新增可搜的类型只加一行。
- 每表单独 try：某张表列名对不上就跳过它，其余照常返回（历史库缺列很常见）。
- 返回 snippet（关键字前后一小段），前端拿去直接展示，不再回查整条记录。
"""

from flask import Blueprint, request, jsonify

from utils import get_db, _get_int_arg
from logger import get_logger

bp = Blueprint("search", __name__)
logger = get_logger("search")

# (类型标识, 表名, 时间列, 标题列, 参与匹配的文本列)
# ⚠️ 类型标识要与前端 TIMELINE_TYPE_MAP 的键对得上，否则结果页会退化成兜底图标。
_SEARCH_SOURCES = [
    ("diary", "diary_entries", "entry_date", "title", ["title", "content"]),
    ("milestone", "milestones", "achieved_date", "title", ["title", "description"]),
    ("checkup", "checkup_records", "checkup_date", "hospital",
     ["hospital", "doctor", "result", "advice", "note"]),
    ("health", "health_records", "record_date", "title",
     ["title", "hospital", "doctor", "symptom", "medication"]),
    ("medication", "medication_records", "measure_time", "medication_name",
     ["medication_name", "note"]),
    ("allergy", "allergy_history", "first_occurrence_date", "allergen_name",
     ["allergen_name", "reaction_detail", "note"]),
    ("vaccine", "vaccine_details", "scheduled_date", "vaccine_name",
     ["vaccine_name", "hospital", "reaction_detail", "note"]),
    ("solidfood", "solid_food_records", "first_try_date", "food_name",
     ["food_name", "note"]),
    ("photo", "photos", "photo_date", "description", ["description"]),
    # 记录备注：平时多为 NULL，但真写了的往往是关键信息（「便便有奶瓣」之类）
    ("feeding", "feeding_records", "start_time", "feeding_type", ["note"]),
    ("sleep", "sleep_records", "start_time", "sleep_quality", ["note"]),
    ("diaper", "diaper_records", "change_time", "diaper_type", ["note"]),
]

_SNIPPET_RADIUS = 30   # 关键字前后各取多少字


def _make_snippet(text, q):
    """截出关键字周围的一小段，前端直接展示。命中不到就从头截。"""
    s = str(text or "")
    if not s:
        return ""
    pos = s.find(q)
    if pos < 0:
        pos = s.lower().find(q.lower())
    if pos < 0:
        return s[:_SNIPPET_RADIUS * 2] + ("…" if len(s) > _SNIPPET_RADIUS * 2 else "")
    start = max(0, pos - _SNIPPET_RADIUS)
    end = min(len(s), pos + len(q) + _SNIPPET_RADIUS)
    return ("…" if start > 0 else "") + s[start:end] + ("…" if end < len(s) else "")


@bp.route("/api/babies/<int:baby_id>/search", methods=["GET"])
def search_baby(baby_id):
    """全局搜索：一次检索日记/里程碑/体检/用药/过敏/疫苗/辅食/照片/各类备注。

    参数：
      q     —— 关键字，必填，至少 1 个字符
      limit —— 可选，默认 50，上限 200
    """
    db = get_db()
    baby = db.execute("SELECT id FROM babies WHERE id = ?", (baby_id,)).fetchone()
    if not baby:
        return jsonify({"success": False, "message": "宝宝不存在"}), 404

    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify({"success": False, "message": "请输入搜索关键字"}), 400
    if len(q) > 100:
        return jsonify({"success": False, "message": "关键字过长"}), 400

    limit, err = _get_int_arg("limit", 50, minimum=1, maximum=200)
    if err:
        return err

    # LIKE 用参数化传入，q 里的 % _ 不会被当成通配符（下面显式 ESCAPE）
    pattern = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"

    hits = []
    for type_key, table, time_col, title_col, text_cols in _SEARCH_SOURCES:
        cols = [title_col] + [c for c in text_cols if c != title_col]
        select_sql = "".join(", {} AS c{}".format(c, i) for i, c in enumerate(cols))
        where_sql = " OR ".join(
            "{} LIKE ? ESCAPE '\\'".format(c) for c in cols)
        query = "SELECT id, {} AS time{} FROM {} WHERE baby_id = ? AND ({})".format(
            time_col, select_sql, table, where_sql)
        params = [baby_id] + [pattern] * len(cols)

        try:
            rows = db.execute(query, params).fetchall()
        except Exception as e:
            # 历史库缺列 / 表名变更：跳过这一源，不拖垮整个搜索
            logger.warning("搜索跳过数据源 %s(%s)：%s", type_key, table, e)
            continue

        for r in rows:
            title = r["c0"]
            # 摘要优先取「非标题列里第一个命中的」，这样用户看到的是上下文而不是标题本身
            snippet = ""
            for i in range(1, len(cols)):
                val = r["c%d" % i]
                if val and q in str(val):
                    snippet = _make_snippet(val, q)
                    break
            if not snippet:
                snippet = _make_snippet(title, q)
            hits.append({
                "type": type_key,
                "id": r["id"],
                "time": r["time"] or "",
                "title": title or "",
                "snippet": snippet,
            })

    # 时间倒序；时间列为空的排后面（它们挂不到时间轴上）
    hits.sort(key=lambda x: x["time"] or "", reverse=True)
    hits = hits[:limit]
    return jsonify({
        "success": True,
        "data": hits,
        "query": q,
        "count": len(hits),
        # 告诉前端搜了哪些类型，便于结果为空时给出「支持搜这些」的提示
        "scopes": [s[0] for s in _SEARCH_SOURCES],
    })
