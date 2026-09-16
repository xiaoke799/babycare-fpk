#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xiaoke799
"""
睡眠记录路由 Blueprint。

时间格式约定与喂奶一致：库里一律 'YYYY-MM-DD HH:MM:SS'。
前端 datetime-local 给 'YYYY-MM-DDTHH:MM'，快捷按钮给 'YYYY-MM-DD HH:MM:SS'，
混存会让按日期范围筛选漏记录（'T' > ' '）。入参走 validate_datetime 归一化，
查询用 substr(start_time,1,10) 比日期、排序用 replace(start_time,'T',' ')'。
"""

import datetime
from flask import Blueprint, request, jsonify

from utils import get_db, json_body
from validators import (
    validate_enum, validate_text_length, validate_datetime, parse_datetime,
    VALID_SLEEP_QUALITY, NOTE_MAX_LENGTH,
)

bp = Blueprint("sleep", __name__)

# 自动判定小憩的时间区间：6:00–18:00 之间入睡算白天小睡
NAP_START_HOUR = 6
NAP_END_HOUR = 18


def _calc_duration(start_dt, end_dt):
    """算睡眠时长（分钟），正确处理跨夜。

    夜里 22:00 睡到次日 06:00，直接相减是 -960 分钟，会把"今日总睡眠"统计拉成负数。
    这里发现 end <= start 就按跨天算（与 ai_tools._sleep_minutes 同一口径）。
    """
    if not start_dt or not end_dt:
        return None
    try:
        s = datetime.datetime.strptime(start_dt[:19], "%Y-%m-%d %H:%M:%S")
        e = datetime.datetime.strptime(end_dt[:19], "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return None
    if e == s:
        return 0
    if e < s:
        e += datetime.timedelta(days=1)
    return int((e - s).total_seconds() // 60)


def _guess_is_nap(start_dt):
    """没显式给 is_nap 时按入睡钟点猜：白天 6:00–18:00 入睡算小憩"""
    if not start_dt:
        return None
    try:
        hour = datetime.datetime.strptime(start_dt[:19], "%Y-%m-%d %H:%M:%S").hour
    except (ValueError, TypeError):
        return None
    return 1 if NAP_START_HOUR <= hour < NAP_END_HOUR else 0


def _clean_sleep_payload(data, existing=None):
    """归一化睡眠入参。返回 (clean_dict, error_message)。

    existing 非空表示「编辑」：本次没传的字段沿用原值，
    避免改个备注就把结束时间、时长、质量抹成 NULL。
    """
    src = existing or {}
    out = {}

    def pick(key, default=None):
        if key in data:
            v = data[key]
            return None if v in (None, "") else v
        return src.get(key, default)

    ok, start_time, err = validate_datetime(pick("start_time"), "开始时间")
    if not ok:
        return None, err
    if not start_time:
        return None, "请填写开始时间"
    out["start_time"] = start_time

    ok, end_time, err = validate_datetime(pick("end_time"), "结束时间")
    if not ok:
        return None, err
    out["end_time"] = end_time

    # 时长：传了就用传的；没传、或改了起止时间，就按新的起止重算（含跨夜）。
    # 编辑场景只改结束时间却不重算，会让时长停留在旧值。
    duration = pick("duration_minutes")
    times_changed = ("start_time" in data) or ("end_time" in data)
    if (duration is None or times_changed) and end_time:
        computed = _calc_duration(start_time, end_time)
        if computed is not None:
            duration = computed
    if duration is not None:
        try:
            duration = int(round(float(duration)))
        except (TypeError, ValueError):
            return None, "睡眠时长必须为数字"
        if duration < 0:
            return None, "睡眠时长不能为负数，请检查结束时间是否早于开始时间"
        if duration > 24 * 60:
            return None, "单次睡眠时长不能超过 24 小时"
    out["duration_minutes"] = duration

    ok, quality, err = validate_enum(pick("sleep_quality"), VALID_SLEEP_QUALITY, "睡眠质量")
    if not ok:
        return None, err
    out["sleep_quality"] = quality

    # is_nap：显式给就用（0/1），没给按入睡时间猜
    is_nap = pick("is_nap")
    if is_nap is None:
        is_nap = _guess_is_nap(start_time)
    else:
        try:
            is_nap = 1 if int(is_nap) else 0
        except (TypeError, ValueError):
            is_nap = _guess_is_nap(start_time)
    out["is_nap"] = is_nap

    ok, note, err = validate_text_length(data.get("note", src.get("note")), NOTE_MAX_LENGTH, "备注")
    if not ok:
        return None, err
    out["note"] = note if note is not None else ""

    return out, None


@bp.route("/api/babies/<int:baby_id>/sleep", methods=["POST"])
def add_sleep_record(baby_id):
    """添加睡眠记录"""
    clean, err = _clean_sleep_payload(json_body())
    if err:
        return jsonify({"success": False, "message": err}), 400

    db = get_db()
    cur = db.execute(
        """INSERT INTO sleep_records (baby_id, start_time, end_time, duration_minutes, sleep_quality, is_nap, note)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (baby_id, clean["start_time"], clean["end_time"], clean["duration_minutes"],
         clean["sleep_quality"], clean["is_nap"], clean["note"]),
    )
    db.commit()
    return jsonify({"success": True, "message": "记录已添加", "id": cur.lastrowid})


@bp.route("/api/babies/<int:baby_id>/sleep", methods=["GET"])
def list_sleep_records(baby_id):
    """获取睡眠记录列表（支持 start/end 按日期筛选）"""
    db = get_db()
    start = (request.args.get("start") or "").strip()
    end = (request.args.get("end") or "").strip()

    query = "SELECT * FROM sleep_records WHERE baby_id = ?"
    params = [baby_id]
    if start:
        query += " AND substr(start_time, 1, 10) >= ?"
        params.append(start[:10])
    if end:
        query += " AND substr(start_time, 1, 10) <= ?"
        params.append(end[:10])

    query += " ORDER BY replace(start_time, 'T', ' ') DESC"

    try:
        limit = int(request.args.get("limit", 100))
    except (TypeError, ValueError):
        limit = 100
    limit = max(1, min(limit, 500))
    query += " LIMIT ?"
    params.append(limit)

    rows = db.execute(query, params).fetchall()
    return jsonify({"success": True, "data": [dict(r) for r in rows]})


@bp.route("/api/sleep/<int:record_id>", methods=["DELETE"])
def delete_sleep_record(record_id):
    """删除睡眠记录"""
    db = get_db()
    cur = db.execute("DELETE FROM sleep_records WHERE id = ?", (record_id,))
    db.commit()
    if cur.rowcount == 0:
        return jsonify({"success": False, "message": "记录不存在"}), 404
    return jsonify({"success": True, "message": "已删除"})


@bp.route("/api/babies/<int:baby_id>/sleep/<int:record_id>", methods=["GET"])
def get_sleep_record(baby_id, record_id):
    """获取单条睡眠记录"""
    db = get_db()
    row = db.execute(
        "SELECT * FROM sleep_records WHERE id = ? AND baby_id = ?",
        (record_id, baby_id)
    ).fetchone()
    if not row:
        return jsonify({"success": False, "message": "记录不存在"}), 404
    return jsonify({"success": True, "data": dict(row)})


@bp.route("/api/babies/<int:baby_id>/sleep/<int:record_id>", methods=["PUT"])
def update_sleep_record(baby_id, record_id):
    """更新睡眠记录（部分更新：没传的字段保持原值）"""
    data = json_body()
    db = get_db()
    row = db.execute(
        "SELECT * FROM sleep_records WHERE id = ? AND baby_id = ?",
        (record_id, baby_id)
    ).fetchone()
    if not row:
        return jsonify({"success": False, "message": "记录不存在"}), 404

    clean, err = _clean_sleep_payload(data, existing=dict(row))
    if err:
        return jsonify({"success": False, "message": err}), 400

    db.execute(
        """UPDATE sleep_records SET start_time=?, end_time=?, duration_minutes=?, sleep_quality=?, is_nap=?, note=?
           WHERE id=? AND baby_id=?""",
        (clean["start_time"], clean["end_time"], clean["duration_minutes"],
         clean["sleep_quality"], clean["is_nap"], clean["note"], record_id, baby_id)
    )
    db.commit()
    return jsonify({"success": True, "message": "已更新", "data": clean})


@bp.route("/api/babies/<int:baby_id>/sleep/stats", methods=["GET"])
def get_sleep_stats(baby_id):
    """获取睡眠统计（今日 / 近 7 天），避免前端拉全量自己算"""
    db = get_db()
    today = datetime.date.today().strftime("%Y-%m-%d")
    week_start = (datetime.date.today() - datetime.timedelta(days=6)).strftime("%Y-%m-%d")

    def _agg(since):
        rows = db.execute(
            """SELECT COUNT(*) AS count,
                      COALESCE(SUM(duration_minutes), 0) AS total,
                      COALESCE(SUM(CASE WHEN is_nap = 1 THEN duration_minutes ELSE 0 END), 0) AS nap,
                      COALESCE(SUM(CASE WHEN is_nap = 0 THEN duration_minutes ELSE 0 END), 0) AS night
               FROM sleep_records WHERE baby_id = ? AND substr(start_time, 1, 10) >= ?""",
            (baby_id, since),
        ).fetchone()
        return {
            "count": rows["count"],
            "total_minutes": rows["total"],
            "nap_minutes": rows["nap"],
            "night_minutes": rows["night"],
        }

    last_row = db.execute(
        "SELECT start_time FROM sleep_records WHERE baby_id = ? "
        "ORDER BY replace(start_time, 'T', ' ') DESC LIMIT 1",
        (baby_id,),
    ).fetchone()
    last_time = last_row["start_time"] if last_row else None
    minutes_since_last = None
    if last_time:
        last_dt = parse_datetime(last_time)
        if last_dt:
            # 负数 = 记了未来的时间，前端显示成 '--'，这里不偷偷抹平
            minutes_since_last = int((datetime.datetime.now() - last_dt).total_seconds() // 60)

    # 近 7 天平均：只统计有记录的天，没记录的天不算进去
    days_with_record = db.execute(
        "SELECT COUNT(DISTINCT substr(start_time, 1, 10)) FROM sleep_records "
        "WHERE baby_id = ? AND substr(start_time, 1, 10) >= ?",
        (baby_id, week_start),
    ).fetchone()[0]
    week = _agg(week_start)
    week["days_with_record"] = days_with_record
    week["avg_minutes"] = round(week["total_minutes"] / days_with_record) if days_with_record else 0

    return jsonify({"success": True, "data": {
        "today": _agg(today),
        "week": week,
        "last_time": last_time,
        "minutes_since_last": minutes_since_last,
    }})
