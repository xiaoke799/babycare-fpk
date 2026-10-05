#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xiaoke799
"""
数据分析路由 Blueprint。
提供仪表盘、时间线、WHO 百分位、睡眠分析、喂奶间隔分析等功能。
"""

import datetime
import sqlite3
from flask import Blueprint, request, jsonify

from utils import get_db, row_to_dict, rows_to_list, _get_int_arg
from logger import get_logger
import growth_utils
import vaccine_utils
# 睡眠达标判定复用 ai_engine 的一套口径（SLEEP_GUIDELINES + ±2 小时容差），
# 不在这里另立第二份参考值 —— 否则「睡眠分析」和「AI 睡眠洞察」会给出互相打架的结论。
from ai_engine import SLEEP_GUIDELINES, get_age_months

bp = Blueprint("analytics", __name__)
logger = get_logger("analytics")

# 睡眠质量分数 → 中文标签。与上面 SQL 里的 CASE 映射一一对应（好=3 一般=2 差=1），
# 改一处必须改另一处。分数是 AVG 出来的，可能是小数，用 round 归到最近档。
SLEEP_QUALITY_LABELS = {3: "好", 2: "一般", 1: "差"}



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


# 性别标签：库里真实存的是 boy / girl / other（前端新增宝宝下拉、babies.py INSERT
# 用的都是这三个值），其余写法是历史数据兼容——键必须包含 boy/girl/other，
# 否则首页档案卡会直接显示英文 "boy" / "girl" / "other"。
GENDER_LABEL = {
    "boy": "男宝",
    "girl": "女宝",
    "other": "其他",
    "male": "男宝",
    "female": "女宝",
    "男": "男宝",
    "女": "女宝",
}
FEEDING_TYPE_LABEL = {"breast": "母乳", "bottle": "奶瓶", "solid": "辅食"}
DIAPER_TYPE_LABEL = {"wet": "尿湿", "dirty": "大便", "both": "大小便", "dry": "干爽"}
DIAPER_COLOR_LABEL = {"black": "黑色", "brown": "棕色", "green": "绿色", "yellow": "黄色", "other": "其他"}
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
            summary += f" · {DIAPER_COLOR_LABEL.get(r['color'], r['color'])}"
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


@bp.route("/api/babies/<int:baby_id>/today-summary", methods=["GET"])
def today_summary(baby_id):
    """今日汇总：喂奶次数/奶量、尿布、睡眠分钟、最近身高体重。"""
    today = datetime.date.today().isoformat()
    db = get_db()
    if not db.execute("SELECT id FROM babies WHERE id = ?", (baby_id,)).fetchone():
        return jsonify({"success": False, "message": "宝宝不存在"}), 404

    def _one(sql, args=()):
        row = db.execute(sql, args).fetchone()
        return row[0] if row and row[0] is not None else 0

    growth = db.execute(
        "SELECT height, weight, bmi, head_circumference, record_date FROM growth_records "
        "WHERE baby_id=? ORDER BY record_date DESC, id DESC LIMIT 1",
        (baby_id,),
    ).fetchone()
    return jsonify({
        "success": True,
        "data": {
            "baby_id": baby_id,
            "date": today,
            "feeding_count": _one(
                "SELECT COUNT(*) FROM feeding_records WHERE baby_id=? AND substr(start_time,1,10)=?",
                (baby_id, today)),
            "feeding_ml": _one(
                "SELECT COALESCE(SUM(amount),0) FROM feeding_records WHERE baby_id=? AND substr(start_time,1,10)=?",
                (baby_id, today)),
            "diaper_count": _one(
                "SELECT COUNT(*) FROM diaper_records WHERE baby_id=? AND substr(change_time,1,10)=?",
                (baby_id, today)),
            "sleep_minutes": _one(
                "SELECT COALESCE(SUM(duration_minutes),0) FROM sleep_records WHERE baby_id=? AND substr(start_time,1,10)=?",
                (baby_id, today)),
            "latest_growth": dict(growth) if growth else None,
        },
    })


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

        # 即将到来的疫苗。vaccines 是早期表（写入只进 vaccine_details），
        # 统一走 vaccine_utils 合并两张表后再筛 —— 只读老表这里是恒空的。
        upcoming_vaccines = []
        try:
            upcoming_vaccines = sorted(
                (
                    v
                    for v in vaccine_utils.merged_vaccinations(db, baby_id)
                    if (v.get("status") or "") == "pending"
                    and (v.get("scheduled_date") or "")[:10] >= today
                ),
                key=lambda v: v.get("scheduled_date") or "",
            )[:5]
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


# ==================== 时间线数据源 ====================
# 结构：(类型标识, 表名, 时间列, [(输出字段, SQL 表达式)], 附加 WHERE)
#
# ⚠️ 三条铁律，改这里之前先读：
#   1. 时间列必须是「事件发生时间」（start_time / measure_time / xxx_date），
#      **绝不能图省事用 created_at** —— 那是入库时间，补录一条上个月的记录会直接
#      显示成今天，整条时间线就错位了。
#   2. 输出字段名要和前端 app.js 的 typeMap / getTimelineDetail 对得上。
#      改名不会报错，只会让那个类型的明细**静默空掉**。
#   3. 列名必须与 server.py 建表语句逐字一致；写错的后果是这类查空（也是静默的）。
#      所以下面每张表都单独 try 包住，失败会写 warning 日志，不影响其它类型。
_TIMELINE_SOURCES = [
    # ---- 原有 5 类：字段名保持不变，前端已在用 ----
    ("feeding", "feeding_records", "start_time",
     [("subtype", "feeding_type"), ("amount", "amount"), ("side", "side"), ("note", "note")], ""),
    ("sleep", "sleep_records", "start_time",
     [("end_time", "end_time"), ("duration_minutes", "duration_minutes"),
      ("is_nap", "is_nap"), ("sleep_quality", "sleep_quality"), ("note", "note")], ""),
    ("diaper", "diaper_records", "change_time",
     [("diaper_type", "diaper_type"), ("color", "color"), ("note", "note")], ""),
    ("growth", "growth_records", "record_date",
     [("height", "height"), ("weight", "weight"),
      ("head_circumference", "head_circumference"), ("bmi", "bmi"), ("note", "note")], ""),
    ("milestone", "milestones", "achieved_date",
     [("title", "title"), ("description", "description"), ("category", "category")], ""),
    # ---- 本次扩容：记录栏其余入口 ----
    ("pumping", "pumping_records", "pump_time",
     [("total_amount", "total_amount"), ("left_amount", "left_amount"), ("right_amount", "right_amount"),
      ("duration_minutes", "duration_minutes"), ("pump_type", "pump_type"), ("note", "note")], ""),
    ("solidfood", "solid_food_records", "first_try_date",
     [("food_name", "food_name"), ("food_category", "food_category"), ("amount", "amount"),
      ("reaction", "reaction"), ("note", "note")], ""),
    ("tummy", "tummy_time_records", "start_time",
     [("duration_minutes", "duration_minutes"), ("milestone", "milestone"), ("note", "note")], ""),
    ("teething", "teething_records", "erupt_date",
     [("tooth_code", "tooth_code"),
      ("tooth_name", "tooth_name"), ("position", "position"), ("side", "side"), ("note", "note")], ""),
    ("fontanelle", "fontanelle_records", "check_date",
     [("anterior_size", "anterior_size"), ("anterior_status", "anterior_status"),
      ("posterior_status", "posterior_status"), ("note", "note")], ""),
    ("leap", "leap_records", "start_date",
     [("leap_number", "leap_number"), ("end_date", "end_date"),
      ("is_completed", "is_completed"), ("note", "note")], ""),
    ("diary", "diary_entries", "entry_date",
     [("title", "title"), ("content", "content"), ("mood", "mood")], ""),
    ("photo", "photos", "photo_date",
     [("description", "description"), ("file_path", "file_path"), ("is_cover", "is_cover")], ""),
    # ---- 健康档案里的几类，之前只在各自 Tab 里看得到 ----
    ("temperature", "temperature_records", "measure_time",
     [("temperature", "temperature"), ("measure_method", "measure_method"),
      ("is_fever", "is_fever"), ("note", "note")], ""),
    ("medication", "medication_records", "measure_time",
     [("medication_name", "medication_name"), ("dosage", "dosage"),
      ("dosage_unit", "dosage_unit"), ("note", "note")], ""),
    ("vaccine", "vaccine_details", "actual_date",
     [("vaccine_name", "vaccine_name"), ("vaccine_type", "vaccine_type"),
      ("dose_number", "dose_number"), ("hospital", "hospital"),
      ("has_reaction", "has_reaction"), ("reaction_detail", "reaction_detail"), ("note", "note")],
     # 只收「已接种」的：没打的只有 scheduled_date，挂到时间线上会让人误以为打过了
     "actual_date IS NOT NULL AND actual_date != ''"),
    ("allergy", "allergy_tests", "test_date",
     [("food_name", "food_name"), ("day_number", "day_number"), ("has_reaction", "has_reaction"),
      ("reaction_detail", "reaction_detail"), ("status", "status"), ("note", "note")], ""),
    ("checkup", "checkup_records", "checkup_date",
     [("hospital", "hospital"), ("doctor", "doctor"), ("result", "result"),
      ("height", "height"), ("weight", "weight"), ("head_circumference", "head_circumference"),
      ("note", "note")], ""),
]


@bp.route("/api/babies/<int:baby_id>/timeline", methods=["GET"])
def get_timeline(baby_id):
    """获取宝宝时间线（聚合所有事件按时间倒序）"""
    db = get_db()
    date_from = request.args.get("from", "")
    date_to = request.args.get("to", "")
    # 服务端按类型筛：前端以前是「拉回来再本地筛」，而 limit 是在服务端截断的，
    # 于是高频类型（喂奶）会把低频类型挤出 limit 之外，选「出牙」结果是空的。
    # 现在把类型下推到 SQL 之前，只有被选中的表参与查询和 limit 竞争。
    type_arg = (request.args.get("type") or "").strip()
    wanted = None
    if type_arg and type_arg != "all":
        wanted = {t for t in type_arg.split(",") if t}
    limit, err = _get_int_arg("limit", 50, minimum=1, maximum=2000)
    if err:
        return err

    events = []

    # 逐表查询后合并排序（不写 UNION：各家列不一样，UNION 反而更难维护）
    for type_key, table, time_col, columns, extra_where in _TIMELINE_SOURCES:
        if wanted is not None and type_key not in wanted:
            continue

        select_sql = "".join(", {} AS {}".format(expr, alias) for alias, expr in columns)
        q = "SELECT id, {} AS time{} FROM {} WHERE baby_id = ?".format(time_col, select_sql, table)
        params = [baby_id]
        if extra_where:
            q += " AND " + extra_where
        if date_from:
            # 统一用 date() 比较：库里既有 'YYYY-MM-DD'（日期列）也有
            # 'YYYY-MM-DD HH:MM:SS'（时间列），date() 两种都能吃，不用分支处理。
            q += " AND date({}) >= ?".format(time_col)
            params.append(date_from)
        if date_to:
            q += " AND date({}) <= ?".format(time_col)
            params.append(date_to)

        # 单表失败不能让整条时间线挂掉：一张表列名/表名对不上就跳过它，其余照常。
        try:
            rows = db.execute(q, params).fetchall()
        except Exception as e:
            logger.warning("时间线跳过数据源 %s(%s)：%s", type_key, table, e)
            continue

        for r in rows:
            if not r["time"]:
                # 时间列为空的历史脏数据：挂不上时间轴，跳过（否则排序时会 TypeError）
                continue
            ev = {"id": r["id"], "type": type_key, "time": r["time"]}
            for alias, _expr in columns:
                ev[alias] = r[alias]
            events.append(ev)

    events.sort(key=lambda x: x["time"], reverse=True)
    events = events[:limit]
    return jsonify({"success": True, "data": events})


@bp.route("/api/babies/<int:baby_id>/memories/today", methods=["GET"])
def get_memories_today(baby_id):
    """「那年今天」：往年同月同日发生过什么。

    与时间线共用一套数据源（_TIMELINE_SOURCES，18 类），只是把过滤条件
    从「某段日期」换成「月-日 相同且年份更早」。所以不只是日记，
    喂养/睡眠/里程碑/出牙/照片全都能命中——比只翻日记更有看头。

    参数：
      date  —— 可选，指定「哪一天的往年」（YYYY-MM-DD），默认今天。
               首页选了某一天回看时，就能顺带看「那年的这一天」。
      limit —— 可选，默认 30，上限 200。

    库里时间列既有 'YYYY-MM-DD'（日期列）也有 'YYYY-MM-DD HH:MM:SS'（时间列），
    strftime 两种都能吃，不必分支处理。
    """
    db = get_db()
    baby = db.execute("SELECT id FROM babies WHERE id = ?", (baby_id,)).fetchone()
    if not baby:
        return jsonify({"success": False, "message": "宝宝不存在"}), 404

    limit, err = _get_int_arg("limit", 30, minimum=1, maximum=200)
    if err:
        return err

    date_arg = (request.args.get("date") or "").strip()
    if date_arg:
        try:
            anchor = datetime.datetime.strptime(date_arg[:10], "%Y-%m-%d").date()
        except ValueError:
            return jsonify({"success": False, "message": "日期格式不正确"}), 400
    else:
        anchor = datetime.date.today()

    md = anchor.strftime("%m-%d")
    anchor_year = anchor.year

    events = []
    for type_key, table, time_col, columns, extra_where in _TIMELINE_SOURCES:
        select_sql = "".join(", {} AS {}".format(expr, alias) for alias, expr in columns)
        q = (
            "SELECT id, {t} AS time{cols} FROM {tbl} WHERE baby_id = ? "
            "AND strftime('%m-%d', {t}) = ? "
            "AND CAST(strftime('%Y', {t}) AS INTEGER) < ?"
        ).format(t=time_col, cols=select_sql, tbl=table)
        params = [baby_id, md, anchor_year]
        if extra_where:
            q += " AND " + extra_where

        # 单表失败不能让整块回忆挂掉（列名/表名对不上就跳过它）
        try:
            rows = db.execute(q, params).fetchall()
        except Exception as e:
            logger.warning("那年今天跳过数据源 %s(%s)：%s", type_key, table, e)
            continue

        for r in rows:
            if not r["time"]:
                continue  # 时间列为空的历史脏数据，算不出「几年前的今天」
            try:
                year = int(str(r["time"])[:4])
            except ValueError:
                continue
            ev = {
                "id": r["id"],
                "type": type_key,
                "time": r["time"],
                "years_ago": anchor_year - year,
            }
            for alias, _expr in columns:
                ev[alias] = r[alias]
            events.append(ev)

    events.sort(key=lambda x: x["time"], reverse=True)
    events = events[:limit]
    return jsonify({
        "success": True,
        "data": events,
        "date": anchor.isoformat(),
        "month_day": md,
        "count": len(events),
    })


def _count_in_year(db, table, time_col, year, baby_id, extra_where=""):
    """按年计数。表/列不存在就当 0 —— 年度报告是锦上添花，不能因为一张表缺列整个挂掉。"""
    try:
        q = ("SELECT COUNT(*) AS c FROM {} WHERE baby_id = ? "
             "AND CAST(strftime('%Y', {}) AS INTEGER) = ?").format(table, time_col)
        if extra_where:
            q += " AND " + extra_where
        row = db.execute(q, (baby_id, year)).fetchone()
        return row["c"] or 0
    except Exception as e:
        logger.warning("年度统计跳过 %s：%s", table, e)
        return 0


@bp.route("/api/babies/<int:baby_id>/yearly-report", methods=["GET"])
def get_yearly_report(baby_id):
    """年度成长报告：把一年的记录汇成一份能回头看的东西。

    数据攒久了是需要回望的——这也是本地私有存储真正的价值所在。
    统计全部来自库里真实数据；AI 总结可选（summary=1），
    未配置模型或调用失败时只给数据，不影响报告本身。

    参数：
      year    —— 可选，默认今年
      summary —— 可选，1 表示附带 AI 文字回顾
    """
    db = get_db()
    baby = db.execute("SELECT * FROM babies WHERE id = ?", (baby_id,)).fetchone()
    if not baby:
        return jsonify({"success": False, "message": "宝宝不存在"}), 404

    year, err = _get_int_arg("year", datetime.date.today().year,
                             minimum=1970, maximum=datetime.date.today().year)
    if err:
        return err
    want_summary = (request.args.get("summary") or "") == "1"

    y = str(year)
    next_y = str(year + 1)

    def _range_where(time_col):
        """时间列既有日期也有时间戳，统一用 date() 比较"""
        return "date({}) >= ? AND date({}) < ?".format(time_col, time_col)

    def _sum(table, col, time_col):
        try:
            q = ("SELECT SUM({}) AS s FROM {} WHERE baby_id = ? AND {}").format(
                col, table, _range_where(time_col))
            row = db.execute(q, (baby_id, y + "-01-01", next_y + "-01-01")).fetchone()
            return row["s"] or 0
        except Exception as e:
            logger.warning("年度求和跳过 %s.%s：%s", table, col, e)
            return 0

    feeding_count = _count_in_year(db, "feeding_records", "start_time", year, baby_id)
    feeding_ml = round(float(_sum("feeding_records", "amount", "start_time") or 0))
    sleep_count = _count_in_year(db, "sleep_records", "start_time", year, baby_id)
    sleep_minutes = _sum("sleep_records", "duration_minutes", "start_time")
    sleep_hours = round(float(sleep_minutes or 0) / 60, 1)
    diaper_count = _count_in_year(db, "diaper_records", "change_time", year, baby_id)
    diary_count = _count_in_year(db, "diary_entries", "entry_date", year, baby_id)
    photo_count = _count_in_year(db, "photos", "photo_date", year, baby_id)
    milestone_count = _count_in_year(db, "milestones", "achieved_date", year, baby_id)
    vaccine_count = _count_in_year(db, "vaccine_details", "actual_date", year, baby_id)
    checkup_count = _count_in_year(db, "checkup_records", "checkup_date", year, baby_id)
    teething_count = _count_in_year(db, "teething_records", "erupt_date", year, baby_id)

    # 年初/年末的身高体重：一年里最早与最近的一次测量
    height_from = height_to = weight_from = weight_to = None
    try:
        rows = db.execute(
            "SELECT height, weight FROM growth_records WHERE baby_id = ? AND {} "
            "ORDER BY date(record_date) ASC".format(_range_where("record_date")),
            (baby_id, y + "-01-01", next_y + "-01-01"),
        ).fetchall()
        if rows:
            height_from = rows[0]["height"]
            weight_from = rows[0]["weight"]
            height_to = rows[-1]["height"]
            weight_to = rows[-1]["weight"]
    except Exception as e:
        logger.warning("年度生长数据读取失败：%s", e)

    milestone_titles = []
    try:
        mrows = db.execute(
            "SELECT title, achieved_date FROM milestones WHERE baby_id = ? AND {} "
            "ORDER BY date(achieved_date) ASC".format(_range_where("achieved_date")),
            (baby_id, y + "-01-01", next_y + "-01-01"),
        ).fetchall()
        milestone_titles = [r["title"] for r in mrows if r["title"]]
    except Exception as e:
        logger.warning("年度里程碑读取失败：%s", e)

    data = {
        "year": year,
        "baby_name": baby["name"],
        "diary_count": diary_count,
        "photo_count": photo_count,
        "feeding_count": feeding_count,
        "feeding_ml": feeding_ml,
        "sleep_count": sleep_count,
        "sleep_hours": sleep_hours,
        "diaper_count": diaper_count,
        "milestone_count": milestone_count,
        "milestone_titles": milestone_titles,
        "vaccine_count": vaccine_count,
        "checkup_count": checkup_count,
        "teething_count": teething_count,
        "height_from": height_from,
        "height_to": height_to,
        "weight_from": weight_from,
        "weight_to": weight_to,
    }

    summary = None
    if want_summary:
        try:
            from ai_writer import generate_yearly_summary
            from ai_engine import get_age_months
            ok, text = generate_yearly_summary(
                {"name": baby["name"],
                 "age_months": get_age_months(baby["birthday"], baby["due_date"] if "due_date" in baby.keys() else None)},
                data,
            )
            if ok:
                summary = text
        except Exception as e:
            logger.warning("年度 AI 总结生成失败（报告仍返回数据）：%s", e)

    return jsonify({"success": True, "data": data, "summary": summary})


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


# 达标判定容差（小时）：日均与建议值相差不超过 2 小时都算正常。
# 与 ai_engine.analyze_sleep 的判定完全一致，改这里要同步改那边。
SLEEP_TOLERANCE_HOURS = 2


def _build_sleep_reference(db, baby_id, total_minutes, days):
    """按宝宝月龄算出「建议睡多久」和达标情况。

    建议值取自 ai_engine.SLEEP_GUIDELINES（表只到 12 月龄，更大的宝宝沿用 12 月档），
    判定用 ±2 小时容差。没有生日（算不出月龄）时返回 None，前端就不显示这张卡。
    """
    try:
        baby = db.execute("SELECT birthday FROM babies WHERE id = ?", (baby_id,)).fetchone()
    except sqlite3.Error:
        # 历史库可能没有 birthday 列。达标卡片只是锦上添花，
        # 不能因为它把整个睡眠分析接口拖成 500。
        logger.warning("查询宝宝生日失败，跳过睡眠达标参考")
        return None
    if not baby or not baby["birthday"]:
        return None

    age_months = get_age_months(baby["birthday"])
    guide = SLEEP_GUIDELINES.get(min(age_months, 12), SLEEP_GUIDELINES[12])
    suggested_hours = guide[0]

    actual_hours = round((total_minutes or 0) / days / 60, 1) if days else 0.0
    if not total_minutes:
        status = "none"      # 这段时间压根没记录，谈不上达标
    elif actual_hours < suggested_hours - SLEEP_TOLERANCE_HOURS:
        status = "low"
    elif actual_hours > suggested_hours + SLEEP_TOLERANCE_HOURS:
        status = "high"
    else:
        status = "ok"

    return {
        "age_months": age_months,
        "suggested_hours": suggested_hours,
        "actual_hours": actual_hours,
        "status": status,
        "days": days,
        "nap_times": guide[1],          # 白天小睡建议次数
        "night_stretch": guide[2],      # 夜间连续睡眠建议
    }


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
    # 注意 sleep_quality 是文本（good/normal/poor），不能直接 AVG：
    # SQLite 对非数字文本一律按 0 参与聚合，AVG(sleep_quality) 恒等于 0，
    # 前端再拿 0 去查标签表就永远查不到 —— 「睡眠质量」一直显示不出来。
    rows = db.execute(
        """SELECT date(start_time) as date, SUM(duration_minutes) as total,
                  SUM(CASE WHEN is_nap = 1 THEN duration_minutes ELSE 0 END) as nap_total,
                  SUM(CASE WHEN is_nap = 0 THEN duration_minutes ELSE 0 END) as night_total,
                  COUNT(*) as sessions,
                  AVG(CASE sleep_quality WHEN 'good' THEN 3
                                         WHEN 'normal' THEN 2
                                         WHEN 'poor' THEN 1 END) as quality_score
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

    # 质量分数（1-3）翻成中文标签。映射只在这一处维护，前端直接用 label，
    # 免得前后端各存一份分数→文字的对照表、改了一边忘另一边。
    daily = rows_to_list(rows)
    for d in daily:
        score = d.get("quality_score")
        # 别用 round()：Python 是「银行家舍入」，2.5 会舍成 2（偶数），
        # 跟「四舍五入」的直觉不符。这里显式取最近的一档。
        d["quality_label"] = (
            SLEEP_QUALITY_LABELS.get(int(score + 0.5), "") if score is not None else ""
        )

    reference = _build_sleep_reference(db, baby_id, stats["sum_total"] or 0, days)

    return jsonify({
        "success": True,
        "data": {
            "daily": daily,
            "reference": reference,
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


# ==================== 周报 / 月报 ====================
# 年度报告太长，日常其实想看的是「这一周/这个月怎么样，比上一周期怎么样」。
# 之前只有 yearly-report，feeding_summary 表里又只有月度汇总没有月报接口，
# 于是「最近一周」这个问题只能自己翻记录。

@bp.route("/api/babies/<int:baby_id>/periodic-report", methods=["GET"])
def get_periodic_report(baby_id):
    """周报 / 月报：本周期关键指标 + 与上一周期的环比。

    参数：
      period —— week（默认，周一到周日）或 month（自然月）
      date   —— 可选，周期内的任意一天，默认今天
    """
    logger = get_logger(__name__)
    db = get_db()
    baby = db.execute("SELECT * FROM babies WHERE id = ?", (baby_id,)).fetchone()
    if not baby:
        return jsonify({"success": False, "message": "宝宝不存在"}), 404

    period = (request.args.get("period") or "week").strip().lower()
    if period not in ("week", "month"):
        return jsonify({"success": False, "message": "period 只能是 week 或 month"}), 400

    raw_date = (request.args.get("date") or "").strip()
    try:
        ref = datetime.date.fromisoformat(raw_date) if raw_date else datetime.date.today()
    except ValueError:
        return jsonify({"success": False, "message": "date 格式应为 YYYY-MM-DD"}), 400

    if period == "week":
        start = ref - datetime.timedelta(days=ref.weekday())   # 周一为一周之始
        end = start + datetime.timedelta(days=7)
        prev_start = start - datetime.timedelta(days=7)
    else:
        start = ref.replace(day=1)
        end = (start + datetime.timedelta(days=32)).replace(day=1)
        prev_start = (start - datetime.timedelta(days=1)).replace(day=1)
    prev_end = start

    s_cur, e_cur = start.isoformat(), end.isoformat()
    s_prev, e_prev = prev_start.isoformat(), prev_end.isoformat()

    def _scalar(sql, params):
        """单值查询；单表出问题只影响这一项，不拖垮整份报告。"""
        try:
            row = db.execute(sql, params).fetchone()
            return row[0] if row else 0
        except Exception as e:  # noqa: BLE001
            logger.warning("周期报告查询失败 %s：%s", sql[:60], e)
            return 0

    def _bucket(s, e):
        """一个周期内的五项计数。"""
        return {
            "feeding_count": int(_scalar(
                "SELECT COUNT(*) FROM feeding_records WHERE baby_id = ?"
                " AND date(start_time) >= ? AND date(start_time) < ?",
                (baby_id, s, e)) or 0),
            "feeding_ml": round(float(_scalar(
                "SELECT SUM(amount) FROM feeding_records WHERE baby_id = ?"
                " AND date(start_time) >= ? AND date(start_time) < ?",
                (baby_id, s, e)) or 0)),
            "sleep_minutes": int(float(_scalar(
                "SELECT SUM(duration_minutes) FROM sleep_records WHERE baby_id = ?"
                " AND date(start_time) >= ? AND date(start_time) < ?",
                (baby_id, s, e)) or 0)),
            "diaper_count": int(_scalar(
                "SELECT COUNT(*) FROM diaper_records WHERE baby_id = ?"
                " AND date(change_time) >= ? AND date(change_time) < ?",
                (baby_id, s, e)) or 0),
            "fever_count": int(_scalar(
                "SELECT COUNT(*) FROM temperature_records WHERE baby_id = ?"
                " AND is_fever = 1 AND date(measure_time) >= ? AND date(measure_time) < ?",
                (baby_id, s, e)) or 0),
        }

    cur, prev = _bucket(s_cur, e_cur), _bucket(s_prev, e_prev)

    def _growth_at(s, e):
        """周期内最后一次量到的身高体重（没有就返回 None，别拿 0 冒充）。"""
        try:
            row = db.execute(
                "SELECT record_date, height, weight, head_circumference FROM growth_records"
                " WHERE baby_id = ? AND date(record_date) >= ? AND date(record_date) < ?"
                " AND (height IS NOT NULL OR weight IS NOT NULL)"
                " ORDER BY record_date DESC LIMIT 1",
                (baby_id, s, e)).fetchone()
            return row_to_dict(row) if row else None
        except Exception as e:  # noqa: BLE001
            logger.warning("周期报告成长记录查询失败：%s", e)
            return None

    growth_cur = _growth_at(s_cur, e_cur)

    def _delta(key):
        """环比差值：上一周期为 0 时不给出（避免出现 +999 这种假增长）。"""
        a, b = cur.get(key, 0), prev.get(key, 0)
        return None if not b else a - b

    days = (end - start).days
    return jsonify({
        "success": True,
        "data": {
            "period": period,
            "start_date": s_cur,
            "end_date": e_cur,
            "prev_start_date": s_prev,
            "prev_end_date": e_prev,
            "days": days,
            "baby": {"name": baby["name"], "birthday": baby["birthday"]},
            "current": cur,
            "previous": prev,
            "delta": {k: _delta(k) for k in
                      ("feeding_count", "feeding_ml", "sleep_minutes",
                       "diaper_count", "fever_count")},
            "growth": growth_cur,
            "milestone_count": int(_scalar(
                "SELECT COUNT(*) FROM milestones WHERE baby_id = ?"
                " AND date(achieved_date) >= ? AND date(achieved_date) < ?",
                (baby_id, s_cur, e_cur)) or 0),
        },
    })
