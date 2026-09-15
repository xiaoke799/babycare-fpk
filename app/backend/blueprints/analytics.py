#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xiaoke799
"""
数据分析路由 Blueprint。
提供仪表盘、时间线、WHO 百分位、睡眠分析、喂奶间隔分析等功能。
"""

import datetime
from flask import Blueprint, request, jsonify

from utils import get_db, row_to_dict, rows_to_list, _get_int_arg
from logger import get_logger
import growth_utils

bp = Blueprint("analytics", __name__)
logger = get_logger("analytics")


def _parse_datetime(value):
    """统一解析日期时间字符串，失败返回 None（P1-6: 避免裸露 strptime）"""
    if not value:
        return None
    try:
        return datetime.datetime.strptime(str(value).strip(), "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return None


def _calc_feeding_interval(db, baby_id):
    """计算喂奶间隔（供仪表盘调用）"""
    week_ago = (datetime.date.today() - datetime.timedelta(days=7)).strftime("%Y-%m-%d")
    feedings = db.execute(
        """SELECT start_time, feeding_type, amount FROM feeding_records
           WHERE baby_id = ? AND date(start_time) >= ?
           ORDER BY start_time DESC LIMIT 50""",
        (baby_id, week_ago),
    ).fetchall()

    if not feedings or len(feedings) < 2:
        return {"avg_interval_minutes": 0, "next_feeding": None, "last_feeding": None, "pattern": "数据不足"}

    intervals = []
    for i in range(len(feedings) - 1):
        t1 = _parse_datetime(feedings[i]["start_time"])
        t2 = _parse_datetime(feedings[i + 1]["start_time"])
        if t1 is None or t2 is None:
            continue
        diff = abs((t1 - t2).total_seconds() / 60)
        if diff < 600:
            intervals.append(diff)

    avg_interval = sum(intervals) / len(intervals) if intervals else 180
    last = feedings[0]
    last_time = _parse_datetime(last["start_time"])
    if last_time is None:
        last_time = datetime.datetime.now()
    next_time = last_time + datetime.timedelta(minutes=avg_interval)
    now = datetime.datetime.now()
    minutes_until_next = (next_time - now).total_seconds() / 60

    return {
        "avg_interval_minutes": round(avg_interval),
        "avg_interval_text": f"{int(avg_interval // 60)}小时{int(avg_interval % 60)}分钟",
        "next_feeding": next_time.strftime("%H:%M"),
        "next_feeding_minutes": round(minutes_until_next),
        "last_feeding": last_time.strftime("%m/%d %H:%M"),
        "last_feeding_type": last["feeding_type"],
        "last_amount": last["amount"],
        "pattern": f"平均每{int(avg_interval // 60)}小时{int(avg_interval % 60)}分钟喂奶一次",
    }


def _calc_diaper_interval(db, baby_id):
    """计算换尿布间隔（供仪表盘调用）"""
    week_ago = (datetime.date.today() - datetime.timedelta(days=7)).strftime("%Y-%m-%d")
    records = db.execute(
        """SELECT * FROM diaper_records
           WHERE baby_id = ? AND date(change_time) >= ?
           ORDER BY change_time ASC""",
        (baby_id, week_ago),
    ).fetchall()

    if len(records) < 2:
        return None

    intervals = []
    for i in range(1, len(records)):
        prev_time = datetime.datetime.fromisoformat(records[i - 1]["change_time"])
        curr_time = datetime.datetime.fromisoformat(records[i]["change_time"])
        interval_minutes = (curr_time - prev_time).total_seconds() / 60
        if interval_minutes < 240:
            intervals.append(interval_minutes)

    if not intervals:
        return None

    avg_interval = sum(intervals) / len(intervals)
    last_record = records[-1]
    last_time = datetime.datetime.fromisoformat(last_record["change_time"])
    predicted_next = last_time + datetime.timedelta(minutes=avg_interval)

    today = datetime.date.today().strftime("%Y-%m-%d")
    today_count = db.execute(
        "SELECT COUNT(*) FROM diaper_records WHERE baby_id = ? AND date(change_time) = ?",
        (baby_id, today),
    ).fetchone()[0]

    return {
        "avg_interval_minutes": round(avg_interval, 1),
        "total_records": len(records),
        "today_count": today_count,
        "predicted_next": predicted_next.strftime("%H:%M"),
        "predicted_in_minutes": round((predicted_next - datetime.datetime.now()).total_seconds() / 60),
    }


def _get_dashboard_ai_insights(db, baby_id):
    """获取仪表盘AI洞察（最多3条）"""
    try:
        from ai_engine import generate_full_report

        result = generate_full_report(db, baby_id)
        if not result.get("success"):
            return []
        insights = result.get("insights", [])
        priority_order = {"warning": 0, "success": 1, "info": 2}
        sorted_insights = sorted(insights, key=lambda x: priority_order.get(x.get("type", "info"), 2))
        return sorted_insights[:3]
    except Exception:
        return []


def _format_age(birthday_str):
    """根据生日返回中文年龄字符串（如 8个月 / 2岁3个月）"""
    if not birthday_str:
        return ""
    try:
        birth = datetime.datetime.strptime(str(birthday_str).strip(), "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return ""
    today = datetime.date.today()
    if today < birth:
        return "未出生"
    years = today.year - birth.year
    months = today.month - birth.month
    if today.day < birth.day:
        months -= 1
    if months < 0:
        years -= 1
        months += 12
    if years <= 0:
        return f"{months}个月"
    return f"{years}岁{months}个月"


GENDER_LABEL = {"male": "男宝", "female": "女宝", "男": "男宝", "女": "女宝"}
FEEDING_TYPE_LABEL = {"breast": "母乳", "bottle": "奶瓶", "solid": "辅食"}
DIAPER_TYPE_LABEL = {"wet": "尿湿", "dirty": "大便", "both": "大小便", "dry": "干爽"}
SIDE_LABEL = {"left": "左侧", "right": "右侧", "both": "双侧"}


def _format_baby_profile(baby_row, latest_growth_row):
    """格式化宝宝档案卡（供首页显示）

    注意：本项目的连接都设了 row_factory = sqlite3.Row（utils.get_db），
    Row 对象**没有 .get()**。直接对 Row 调 .get() 会抛 AttributeError，
    被 get_dashboard() 的 except 吞掉后整个首页接口返回 500，
    前端 loadDashboard 里 `if (!res.success) return` 直接退出 →
    首页永远停在占位（宝宝 / -- · -- · -- / 0 / 0m / 暂无记录）。
    所以这里必须先 dict(row) 再取字段。
    """
    if not baby_row:
        return None
    baby_row = dict(baby_row)
    if latest_growth_row is not None:
        latest_growth_row = dict(latest_growth_row)
    return {
        "id": baby_row["id"],
        "name": baby_row.get("name") or "宝宝",
        "age": _format_age(baby_row.get("birthday")),
        "gender": GENDER_LABEL.get(baby_row.get("gender"), baby_row.get("gender") or ""),
        "weight": latest_growth_row.get("weight") if latest_growth_row else None,
        "birthday": baby_row.get("birthday"),
    }


def _format_duration(minutes):
    """将分钟格式化为 xhym"""
    if minutes is None or minutes <= 0:
        return ""
    h, m = divmod(int(minutes), 60)
    if h > 0 and m > 0:
        return f"{h}h{m}m"
    if h > 0:
        return f"{h}h"
    return f"{m}m"


def _parse_time_str(value):
    """解析 yyyy-mm-dd HH:MM:SS / ISO / yyyy-mm-dd，失败返回 None"""
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(text, fmt)
        except ValueError:
            continue
    try:
        return datetime.datetime.fromisoformat(text)
    except ValueError:
        return None


def _get_recent_records(db, baby_id, limit=6):
    """获取最近跨类型记录，供首页最近记录列表"""
    records = []

    feedings = db.execute(
        """SELECT id, start_time, feeding_type, amount, side, note
           FROM feeding_records WHERE baby_id = ? ORDER BY start_time DESC LIMIT ?""",
        (baby_id, limit),
    ).fetchall()
    for r in feedings:
        t = _parse_time_str(r["start_time"])
        parts = [FEEDING_TYPE_LABEL.get(r["feeding_type"], r["feeding_type"])]
        if r["amount"]:
            parts.append(f"{int(r['amount'])}ml" if r["feeding_type"] != "solid" else f"{int(r['amount'])}g")
        if r["side"] and r["feeding_type"] == "breast":
            parts.append(SIDE_LABEL.get(r["side"], r["side"]))
        records.append({
            "type": "feeding",
            "type_label": "喂奶",
            "time": t.strftime("%H:%M") if t else "",
            "sort_time": t or datetime.datetime.min,
            "title": "喂奶",
            "summary": " · ".join(parts) if len(parts) > 1 else (parts[0] if parts else "记录"),
            "id": r["id"],
        })

    sleeps = db.execute(
        """SELECT id, start_time, duration_minutes, is_nap, note
           FROM sleep_records WHERE baby_id = ? ORDER BY start_time DESC LIMIT ?""",
        (baby_id, limit),
    ).fetchall()
    for r in sleeps:
        t = _parse_time_str(r["start_time"])
        summary_parts = []
        dur = _format_duration(r["duration_minutes"])
        if dur:
            summary_parts.append(dur)
        if r["is_nap"] == 1:
            summary_parts.append("小憩")
        elif r["is_nap"] == 0:
            summary_parts.append("夜间睡眠")
        records.append({
            "type": "sleep",
            "type_label": "睡眠",
            "time": t.strftime("%H:%M") if t else "",
            "sort_time": t or datetime.datetime.min,
            "title": "睡眠",
            "summary": " · ".join(summary_parts) if summary_parts else "记录",
            "id": r["id"],
        })

    diapers = db.execute(
        """SELECT id, change_time, diaper_type, color, note
           FROM diaper_records WHERE baby_id = ? ORDER BY change_time DESC LIMIT ?""",
        (baby_id, limit),
    ).fetchall()
    for r in diapers:
        t = _parse_time_str(r["change_time"])
        summary = DIAPER_TYPE_LABEL.get(r["diaper_type"], r["diaper_type"] or "更换尿布")
        if r["color"]:
            summary += f" · {r['color']}"
        records.append({
            "type": "diaper",
            "type_label": "尿布",
            "time": t.strftime("%H:%M") if t else "",
            "sort_time": t or datetime.datetime.min,
            "title": "尿布",
            "summary": summary,
            "id": r["id"],
        })

    growths = db.execute(
        """SELECT id, record_date, weight, height, head_circumference
           FROM growth_records WHERE baby_id = ? ORDER BY record_date DESC LIMIT ?""",
        (baby_id, limit),
    ).fetchall()
    for r in growths:
        t = _parse_time_str(r["record_date"])
        parts = []
        if r["weight"]:
            parts.append(f"{r['weight']}kg")
        if r["height"]:
            parts.append(f"{r['height']}cm")
        records.append({
            "type": "growth",
            "type_label": "成长",
            "time": t.strftime("%m-%d") if t else "",
            "sort_time": t or datetime.datetime.min,
            "title": "成长",
            "summary": " · ".join(parts) if parts else "记录",
            "id": r["id"],
        })

    records.sort(key=lambda x: x["sort_time"], reverse=True)
    # 去掉 sort_time 后再返回，保持前端字段干净
    return [{k: v for k, v in rec.items() if k != "sort_time"} for rec in records[:limit]]


def _get_who_percentile(sex, age_in_days, weight=None, height=None, bmi=None, head_circumference=None):
    """计算 WHO 百分位（使用 growth_utils 插值 + 分档）"""
    db = get_db()
    result = {}

    table_map = {
        "weight": "who_weight_percentiles",
        "height": "who_height_percentiles",
        "bmi": "who_bmi_percentiles",
        "head_circumference": "who_head_percentiles",
    }
    field_map = {"weight": weight, "height": height, "bmi": bmi, "head_circumference": head_circumference}

    for metric, value in field_map.items():
        if value is None:
            continue
        rows = db.execute(
            f"SELECT age_in_days, p3, p15, p50, p85, p97 FROM {table_map[metric]} WHERE sex = ? ORDER BY age_in_days",
            (sex,),
        ).fetchall()
        ref = growth_utils.interpolate_reference(rows, age_in_days)
        if ref:
            result[metric] = {
                "value": value,
                "classification": growth_utils.classify_percentile(value, ref),
                "z_score": growth_utils.z_score_approx(value, ref),
                "reference": ref,
            }
    return result


def _generate_pattern_note(peak_hours, avg_interval, avg_duration):
    """生成睡眠模式解读"""
    notes = []
    if peak_hours:
        hours_str = "、".join([f"{h[0]}点" for h in peak_hours[:2]])
        notes.append(f"宝宝通常在{hours_str}入睡")
    if avg_interval:
        if avg_interval < 2:
            notes.append("睡眠间隔较短，可能需要增加活动量")
        elif avg_interval > 4:
            notes.append("睡眠间隔较长，宝宝精力充沛")
    if avg_duration:
        if avg_duration < 30:
            notes.append("单次睡眠较短，建议培养自主入睡")
        elif avg_duration > 120:
            notes.append("单次睡眠充足，睡眠质量良好")
    return "；".join(notes) if notes else "继续记录更多数据以获得更准确的分析"


# ==================== 路由 ====================


@bp.route("/api/babies/<int:baby_id>/dashboard", methods=["GET"])
def get_dashboard(baby_id):
    """获取仪表盘统计数据（合并版：包含今日统计、间隔分析、AI洞察）"""
    db = get_db()
    today = datetime.date.today().strftime("%Y-%m-%d")

    try:
        baby = db.execute(
            "SELECT * FROM babies WHERE id = ?", (baby_id,)
        ).fetchone()

        feeding = db.execute(
            """SELECT COUNT(*) as count, COALESCE(SUM(amount), 0) as total
               FROM feeding_records WHERE baby_id = ? AND date(start_time) = ?""",
            (baby_id, today),
        ).fetchone()

        sleep = db.execute(
            """SELECT COALESCE(SUM(duration_minutes), 0) as total_minutes
               FROM sleep_records WHERE baby_id = ? AND date(start_time) = ?""",
            (baby_id, today),
        ).fetchone()

        diaper = db.execute(
            "SELECT COUNT(*) as count FROM diaper_records WHERE baby_id = ? AND date(change_time) = ?",
            (baby_id, today),
        ).fetchone()

        latest_growth = db.execute(
            "SELECT * FROM growth_records WHERE baby_id = ? ORDER BY record_date DESC LIMIT 1",
            (baby_id,),
        ).fetchone()

        latest_milestones = db.execute(
            "SELECT * FROM milestones WHERE baby_id = ? ORDER BY achieved_date DESC LIMIT 3",
            (baby_id,),
        ).fetchall()

        week_ago = (datetime.date.today() - datetime.timedelta(days=6)).strftime("%Y-%m-%d")
        feeding_trend = db.execute(
            """SELECT date(start_time) as date, COUNT(*) as count, COALESCE(SUM(amount), 0) as total
               FROM feeding_records WHERE baby_id = ? AND date(start_time) >= ?
               GROUP BY date(start_time) ORDER BY date""",
            (baby_id, week_ago),
        ).fetchall()

        feeding_interval_data = _calc_feeding_interval(db, baby_id)
        diaper_interval_data = _calc_diaper_interval(db, baby_id)
        ai_insights = _get_dashboard_ai_insights(db, baby_id)

        # 即将到来的用药提醒（可选表，不存在时返回空列表）
        upcoming_medications = []
        try:
            upcoming_medications = db.execute(
                """SELECT * FROM medication_reminders
                   WHERE baby_id = ? AND is_active = 1
                   ORDER BY next_dose_time ASC LIMIT 5""",
                (baby_id,),
            ).fetchall()
        except Exception:
            pass

        # 即将到来的疫苗（可选表，不存在时返回空列表）
        upcoming_vaccines = []
        try:
            upcoming_vaccines = db.execute(
                """SELECT * FROM vaccines
                   WHERE baby_id = ? AND status = 'pending'
                   AND scheduled_date >= ?
                   ORDER BY scheduled_date ASC LIMIT 5""",
                (baby_id, today),
            ).fetchall()
        except Exception:
            pass

        data = {
            "baby_profile": _format_baby_profile(baby, latest_growth),
            "today_feeding": {"count": feeding["count"] if feeding else 0, "total_amount": feeding["total"] if feeding else 0},
            "today_sleep": {"total_minutes": sleep["total_minutes"] if sleep else 0},
            "today_diaper": {"count": diaper["count"] if diaper else 0},
            "latest_growth": row_to_dict(latest_growth),
            "latest_milestones": rows_to_list(latest_milestones),
            "recent_records": _get_recent_records(db, baby_id, limit=6),
            "feeding_trend": rows_to_list(feeding_trend),
            "feeding_interval": feeding_interval_data,
            "diaper_interval": diaper_interval_data,
            "ai_insights": ai_insights,
            "upcoming_medications": rows_to_list(upcoming_medications),
            "upcoming_vaccines": rows_to_list(upcoming_vaccines),
        }
        return jsonify({"success": True, "data": data})
    except Exception as e:
        logger.error("Dashboard error: %s", e, exc_info=True)
        return jsonify({"success": False, "message": "数据加载失败，请稍后重试"}), 500


@bp.route("/api/babies/<int:baby_id>/feeding-interval", methods=["GET"])
def get_feeding_interval(baby_id):
    """分析喂奶间隔规律，预测下次喂奶时间"""
    db = get_db()
    week_ago = (datetime.date.today() - datetime.timedelta(days=7)).strftime("%Y-%m-%d")

    # 复用 _calc_feeding_interval 计算间隔数据（P2-1: 消除重复代码）
    interval_data = _calc_feeding_interval(db, baby_id)

    # 获取每日统计
    daily_counts = db.execute(
        """SELECT date(start_time) as date, COUNT(*) as count, COALESCE(SUM(amount), 0) as total
           FROM feeding_records WHERE baby_id = ? AND date(start_time) >= ?
           GROUP BY date(start_time) ORDER BY date""",
        (baby_id, week_ago),
    ).fetchall()

    # 获取总记录数
    total = db.execute(
        "SELECT COUNT(*) FROM feeding_records WHERE baby_id = ? AND date(start_time) >= ?",
        (baby_id, week_ago),
    ).fetchone()[0]

    return jsonify({
        "success": True,
        "data": {
            **interval_data,
            "total_records_7d": total,
            "daily_counts": rows_to_list(daily_counts),
        },
    })


@bp.route("/api/babies/<int:baby_id>/timeline", methods=["GET"])
def get_timeline(baby_id):
    """获取宝宝时间线（聚合所有事件按时间倒序）"""
    db = get_db()
    date_from = request.args.get("from", "")
    date_to = request.args.get("to", "")
    limit, err = _get_int_arg("limit", 50, minimum=1, maximum=500)
    if err:
        return err

    events = []

    # 查询各表事件（P2-2: 分别查询后合并排序，避免复杂 UNION）
    table_configs = [
        ("feeding", "start_time"),
        ("sleep", "start_time"),
        ("diaper", "change_time"),
        ("growth", "record_date"),
        ("milestone", "achieved_date"),
    ]

    for table, time_col in table_configs:
        if table == "feeding":
            q = ("SELECT id, 'feeding' as type, start_time as time, feeding_type as subtype, "
                 "amount, side, note FROM feeding_records WHERE baby_id = ?")
            params = [baby_id]
            if date_from:
                q += " AND date(start_time) >= ?"
                params.append(date_from)
            if date_to:
                q += " AND date(start_time) <= ?"
                params.append(date_to)
            rows = db.execute(q, params).fetchall()
            for f in rows:
                events.append({
                    "id": f["id"], "type": "feeding", "time": f["time"],
                    "subtype": f["subtype"], "amount": f["amount"],
                    "side": f["side"], "note": f["note"],
                })
        elif table == "sleep":
            q = ("SELECT id, 'sleep' as type, start_time as time, end_time, "
                 "duration_minutes, is_nap, sleep_quality, note FROM sleep_records WHERE baby_id = ?")
            params = [baby_id]
            if date_from:
                q += " AND date(start_time) >= ?"
                params.append(date_from)
            if date_to:
                q += " AND date(start_time) <= ?"
                params.append(date_to)
            rows = db.execute(q, params).fetchall()
            for s in rows:
                events.append({
                    "id": s["id"], "type": "sleep", "time": s["time"],
                    "end_time": s["end_time"], "duration_minutes": s["duration_minutes"],
                    "is_nap": s["is_nap"], "sleep_quality": s["sleep_quality"], "note": s["note"],
                })
        elif table == "diaper":
            q = ("SELECT id, 'diaper' as type, change_time as time, diaper_type, color, note "
                 "FROM diaper_records WHERE baby_id = ?")
            params = [baby_id]
            if date_from:
                q += " AND date(change_time) >= ?"
                params.append(date_from)
            if date_to:
                q += " AND date(change_time) <= ?"
                params.append(date_to)
            rows = db.execute(q, params).fetchall()
            for d in rows:
                events.append({
                    "id": d["id"], "type": "diaper", "time": d["time"],
                    "diaper_type": d["diaper_type"], "color": d["color"], "note": d["note"],
                })
        elif table == "growth":
            q = ("SELECT id, 'growth' as type, record_date as time, height, weight, "
                 "head_circumference, bmi, note FROM growth_records WHERE baby_id = ?")
            params = [baby_id]
            if date_from:
                q += " AND record_date >= ?"
                params.append(date_from)
            if date_to:
                q += " AND record_date <= ?"
                params.append(date_to)
            rows = db.execute(q, params).fetchall()
            for g in rows:
                events.append({
                    "id": g["id"], "type": "growth", "time": g["time"],
                    "height": g["height"], "weight": g["weight"],
                    "head_circumference": g["head_circumference"], "bmi": g["bmi"], "note": g["note"],
                })
        elif table == "milestone":
            q = ("SELECT id, 'milestone' as type, achieved_date as time, title, description, category "
                 "FROM milestones WHERE baby_id = ?")
            params = [baby_id]
            if date_from:
                q += " AND achieved_date >= ?"
                params.append(date_from)
            if date_to:
                q += " AND achieved_date <= ?"
                params.append(date_to)
            rows = db.execute(q, params).fetchall()
            for m in rows:
                events.append({
                    "id": m["id"], "type": "milestone", "time": m["time"],
                    "title": m["title"], "description": m["description"], "category": m["category"],
                })

    events.sort(key=lambda x: x["time"], reverse=True)
    events = events[:limit]
    return jsonify({"success": True, "data": events})


@bp.route("/api/babies/<int:baby_id>/percentiles", methods=["GET"])
def get_percentiles(baby_id):
    """获取宝宝最新 WHO 生长百分位（早产儿自动使用纠正月龄）"""
    db = get_db()
    baby = db.execute("SELECT * FROM babies WHERE id = ?", (baby_id,)).fetchone()
    if not baby:
        return jsonify({"success": False, "message": "宝宝不存在"}), 404

    baby_d = row_to_dict(baby)
    sex = baby_d["gender"] if baby_d["gender"] in ("boy", "girl") else "boy"
    today = datetime.date.today()

    age_in_days_actual = growth_utils.calc_age_days(baby_d["birthday"], today)
    basis_date, is_corrected = growth_utils.effective_age_basis(
        baby_d["birthday"], baby_d.get("due_date"), age_in_days_actual,
    )
    age_in_days = max((today - basis_date).days, 0)

    latest = db.execute(
        "SELECT * FROM growth_records WHERE baby_id = ? ORDER BY record_date DESC LIMIT 1",
        (baby_id,),
    ).fetchone()

    result = {"age_in_days": age_in_days, "age_corrected": is_corrected, "sex": sex, "percentiles": {}}

    if latest:
        w = latest["weight"] if latest["weight"] else None
        h = latest["height"] if latest["height"] else None
        b = latest["bmi"] if latest["bmi"] else None
        hc = latest["head_circumference"] if latest["head_circumference"] else None
        result["percentiles"] = _get_who_percentile(sex, age_in_days, weight=w, height=h, bmi=b, head_circumference=hc)
        result["latest"] = row_to_dict(latest)

    return jsonify({"success": True, "data": result})


@bp.route("/api/who-reference", methods=["GET"])
def get_who_reference():
    """获取 WHO 标准参考数据（用于画图）"""
    sex = request.args.get("sex", "boy")
    metric = request.args.get("metric", "weight")
    table_map = {
        "weight": "who_weight_percentiles",
        "height": "who_height_percentiles",
        "head_circumference": "who_head_percentiles",
        "bmi": "who_bmi_percentiles",
    }
    table = table_map.get(metric, "who_weight_percentiles")

    rows = get_db().execute(
        f"SELECT age_in_days, p3, p15, p50, p85, p97 FROM {table} WHERE sex = ? ORDER BY age_in_days",
        (sex,),
    ).fetchall()

    return jsonify({"success": True, "data": rows_to_list(rows)})


@bp.route("/api/babies/<int:baby_id>/sleep-analysis", methods=["GET"])
def get_sleep_analysis(baby_id):
    """获取睡眠深度分析"""
    db = get_db()
    today = datetime.date.today()

    # 支持自定义天数范围
    days, err = _get_int_arg("days", 7, minimum=1, maximum=90)
    if err:
        return err

    start_date = (today - datetime.timedelta(days=days - 1)).strftime("%Y-%m-%d")

    # 每日睡眠统计
    rows = db.execute(
        """SELECT date(start_time) as date, SUM(duration_minutes) as total,
                  SUM(CASE WHEN is_nap = 1 THEN duration_minutes ELSE 0 END) as nap_total,
                  SUM(CASE WHEN is_nap = 0 THEN duration_minutes ELSE 0 END) as night_total,
                  COUNT(*) as sessions,
                  AVG(sleep_quality) as avg_quality
           FROM sleep_records
           WHERE baby_id = ? AND date(start_time) >= ?
           GROUP BY date(start_time)
           ORDER BY date""",
        (baby_id, start_date),
    ).fetchall()

    # 平均值统计
    stats = db.execute(
        """SELECT AVG(duration_minutes) as avg_duration,
                  AVG(CASE WHEN is_nap = 1 THEN duration_minutes ELSE NULL END) as avg_nap,
                  AVG(CASE WHEN is_nap = 0 THEN duration_minutes ELSE NULL END) as avg_night,
                  SUM(duration_minutes) as sum_total,
                  COUNT(*) as total_sessions
           FROM sleep_records WHERE baby_id = ? AND date(start_time) >= ?""",
        (baby_id, start_date),
    ).fetchone()

    # 睡眠质量分布
    quality_dist = db.execute(
        """SELECT sleep_quality, COUNT(*) as count
           FROM sleep_records
           WHERE baby_id = ? AND date(start_time) >= ? AND sleep_quality IS NOT NULL
           GROUP BY sleep_quality""",
        (baby_id, start_date),
    ).fetchall()

    # 入睡时间分析（夜间睡眠）
    bedtime_stats = db.execute(
        """SELECT AVG(CAST(strftime('%H', start_time) AS INTEGER) * 60 + CAST(strftime('%M', start_time) AS INTEGER)) as avg_bedtime_minutes
           FROM sleep_records
           WHERE baby_id = ? AND date(start_time) >= ? AND is_nap = 0 AND start_time IS NOT NULL""",
        (baby_id, start_date),
    ).fetchone()

    # 睡眠规律性（入睡时间的标准差）
    bedtime_variance = None
    if bedtime_stats and bedtime_stats["avg_bedtime_minutes"]:
        bedtime_rows = db.execute(
            """SELECT CAST(strftime('%H', start_time) AS INTEGER) * 60 + CAST(strftime('%M', start_time) AS INTEGER) as bedtime_minutes
               FROM sleep_records
               WHERE baby_id = ? AND date(start_time) >= ? AND is_nap = 0 AND start_time IS NOT NULL""",
            (baby_id, start_date),
        ).fetchall()
        if bedtime_rows and len(bedtime_rows) > 1:
            avg_bt = bedtime_stats["avg_bedtime_minutes"]
            variance = sum((r["bedtime_minutes"] - avg_bt) ** 2 for r in bedtime_rows) / len(bedtime_rows)
            bedtime_variance = round(variance ** 0.5, 1)  # 标准差（分钟）

    # 构建质量分布字典
    quality_map = {"good": 0, "normal": 0, "poor": 0}
    for q in quality_dist:
        if q["sleep_quality"] in quality_map:
            quality_map[q["sleep_quality"]] = q["count"]

    # 平均入睡时间
    avg_bedtime_str = ""
    if bedtime_stats and bedtime_stats["avg_bedtime_minutes"]:
        bt_min = int(bedtime_stats["avg_bedtime_minutes"])
        avg_bedtime_str = f"{bt_min // 60:02d}:{bt_min % 60:02d}"

    return jsonify({
        "success": True,
        "data": {
            "daily": rows_to_list(rows),
            "averages": {
                "avg_duration": round(stats["avg_duration"] or 0, 1),
                "avg_nap": round(stats["avg_nap"] or 0, 1),
                "avg_night": round(stats["avg_night"] or 0, 1),
                "sum_total": stats["sum_total"] or 0,
                "total_sessions": stats["total_sessions"] or 0,
            },
            "quality_distribution": quality_map,
            "avg_bedtime": avg_bedtime_str,
            "bedtime_variance": bedtime_variance,
            "days": days,
        },
    })


@bp.route("/api/babies/<int:baby_id>/pattern/<string:pattern_type>", methods=["GET"])
def get_pattern_data(baby_id, pattern_type):
    """获取喂养/睡眠24小时节律数据"""
    days, err = _get_int_arg("days", 7, minimum=1, maximum=365)
    if err:
        return err
    db = get_db()

    if pattern_type == "feeding":
        start_date = (datetime.date.today() - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
        records = db.execute(
            """SELECT start_time, feeding_type, amount FROM feeding_records
               WHERE baby_id = ? AND date(start_time) >= ?""",
            (baby_id, start_date),
        ).fetchall()
        hourly = {h: {"count": 0, "total_amount": 0} for h in range(24)}
        for r in records:
            dt = _parse_datetime(r["start_time"])
            if dt is None:
                continue
            hour = dt.hour
            hourly[hour]["count"] += 1
            hourly[hour]["total_amount"] += (r["amount"] or 0)
        data = []
        for h in range(24):
            data.append({
                "hour": h,
                "count": round(hourly[h]["count"] / days, 1),
                "amount": round(hourly[h]["total_amount"] / days, 1),
            })
        return jsonify({"success": True, "data": data, "type": "feeding"})

    elif pattern_type == "sleep":
        start_date = (datetime.date.today() - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
        records = db.execute(
            """SELECT start_time, end_time, duration_minutes, is_nap FROM sleep_records
               WHERE baby_id = ? AND date(start_time) >= ?""",
            (baby_id, start_date),
        ).fetchall()
        hourly = {h: {"nap": 0, "night": 0, "count": 0} for h in range(24)}
        for r in records:
            dt = _parse_datetime(r["start_time"])
            if dt is None:
                continue
            start_hour = dt.hour
            duration = r["duration_minutes"] or 0
            is_nap = r["is_nap"]
            if is_nap:
                hourly[start_hour]["nap"] += duration
            else:
                hourly[start_hour]["night"] += duration
            hourly[start_hour]["count"] += 1
        data = []
        for h in range(24):
            data.append({
                "hour": h,
                "nap": round(hourly[h]["nap"] / days, 1),
                "night": round(hourly[h]["night"] / days, 1),
                "total": round((hourly[h]["nap"] + hourly[h]["night"]) / days, 1),
            })
        return jsonify({"success": True, "data": data, "type": "sleep"})

    return jsonify({"success": False, "message": "未知类型"}), 400


@bp.route("/api/babies/<int:baby_id>/sleep-prediction", methods=["GET"])
def get_sleep_prediction(baby_id):
    """基于历史数据预测下次睡眠时间"""
    db = get_db()
    today = datetime.date.today()
    start_date = (today - datetime.timedelta(days=13)).strftime("%Y-%m-%d")
    records = db.execute(
        """SELECT start_time, end_time, duration_minutes, is_nap FROM sleep_records
           WHERE baby_id = ? AND date(start_time) >= ?
           ORDER BY start_time DESC""",
        (baby_id, start_date),
    ).fetchall()

    if not records:
        return jsonify({"success": True, "data": None, "message": "暂无足够数据进行预测"})

    sleep_starts = []
    for r in records:
        st = _parse_datetime(r["start_time"])
        if st is not None:
            sleep_starts.append(st)

    intervals = []
    for i in range(len(sleep_starts) - 1):
        diff = (sleep_starts[i] - sleep_starts[i + 1]).total_seconds() / 3600
        if 0.5 < diff < 12:
            intervals.append(diff)

    durations = [r["duration_minutes"] for r in records if r["duration_minutes"] and r["duration_minutes"] > 0]
    avg_duration = sum(durations) / len(durations) if durations else 0

    hour_counts = {}
    for st in sleep_starts:
        h = st.hour
        hour_counts[h] = hour_counts.get(h, 0) + 1

    peak_hours = sorted(hour_counts.items(), key=lambda x: x[1], reverse=True)[:3]
    last_sleep = sleep_starts[0] if sleep_starts else None
    last_duration = records[0]["duration_minutes"] if records else 0

    prediction = None
    if intervals and last_sleep:
        avg_interval = sum(intervals) / len(intervals)
        if last_duration > 0:
            last_end = last_sleep + datetime.timedelta(minutes=last_duration)
            next_sleep = last_end + datetime.timedelta(hours=avg_interval)
        else:
            next_sleep = last_sleep + datetime.timedelta(hours=avg_interval)

        next_hour = next_sleep.hour
        closest_peak = min(peak_hours, key=lambda x: abs(x[0] - next_hour))
        if abs(closest_peak[0] - next_hour) > 2:
            next_sleep = next_sleep.replace(hour=closest_peak[0], minute=0)

        prediction = {
            "next_sleep_time": next_sleep.strftime("%H:%M"),
            "confidence": min(90, max(30, int(100 - len(intervals) * 2))),
            "avg_interval_hours": round(avg_interval, 1),
            "avg_duration_minutes": round(avg_duration, 0),
            "peak_sleep_hours": [h[0] for h in peak_hours],
            "last_sleep": last_sleep.strftime("%m-%d %H:%M") if last_sleep else None,
            "pattern_note": _generate_pattern_note(peak_hours, avg_interval, avg_duration),
        }

    return jsonify({"success": True, "data": prediction})
