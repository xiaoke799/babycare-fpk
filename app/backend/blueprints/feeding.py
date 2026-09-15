#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xiaoke799
"""
喂奶与吸奶记录路由 Blueprint。
提供喂奶记录增删、吸奶记录管理、统计等功能。

时间格式约定：库里一律存 'YYYY-MM-DD HH:MM:SS'。
前端 datetime-local 给 'YYYY-MM-DDTHH:MM'，快捷按钮给 'YYYY-MM-DD HH:MM:SS'，
两种混存会让按日期范围筛选漏记录（'T' 比 ' ' 大，带 T 的下午记录会被
'当天 23:59:59' 挡在门外）。所以入参统一走 validate_datetime 归一化，
查询统一用 substr(start_time,1,10) 比日期，不依赖具体分隔符。
"""

import datetime
from flask import Blueprint, request, jsonify

from utils import get_db, json_body
from validators import (
    validate_amount, validate_duration, validate_enum,
    validate_text_length, validate_datetime, validate_seconds,
    VALID_FEEDING_TYPES, VALID_FEEDING_SIDES, NOTE_MAX_LENGTH,
)

bp = Blueprint("feeding", __name__)

# 平均间隔只统计 12 小时内的相邻记录，隔夜的空档不算进「喂奶节奏」
_MAX_GAP_MINUTES = 12 * 60


def _parse_dt(value):
    """解析库里的时间字符串，兼容 'YYYY-MM-DD HH:MM:SS' / '... HH:MM' / 带 T 的格式"""
    if not value:
        return None
    text = str(value).replace("T", " ").strip()[:19]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _clean_feeding_payload(data, existing=None):
    """归一化喂奶入参。返回 (clean_dict, error_message)。

    existing 非空时表示「编辑」：本次没传的字段沿用原值。
    这一点很关键——之前编辑只改 5 个字段，左右侧计时时长被静默清空。
    """
    src = existing or {}
    out = {}

    def pick(key, default=None):
        """取值：本次显式传了就算数（传空 = 清空），没传才沿用原值。

        注意别写成 `data.get(key) or src.get(key)`：那样「把奶量清空成 null」
        会被当成没传，编辑时清不掉旧值。
        """
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
    if end_time and end_time < start_time:
        return None, f"结束时间（{end_time}）早于开始时间（{start_time}），请确认。"
    out["end_time"] = end_time

    ok, feeding_type, err = validate_enum(pick("feeding_type"), VALID_FEEDING_TYPES, "喂养类型")
    if not ok:
        return None, err
    if not feeding_type:
        return None, "请选择喂养类型"
    out["feeding_type"] = feeding_type

    ok, amount, err = validate_amount(pick("amount"), "奶量")
    if not ok:
        return None, err
    out["amount"] = amount if amount else None          # 0ml 当成「没填」，不写脏 0

    ok, side, err = validate_enum(pick("side"), VALID_FEEDING_SIDES, "哺乳侧")
    if not ok:
        return None, err
    out["side"] = side or None                          # 空串 → NULL

    # 备注允许主动清空，所以不能用 pick（空串会被当成「没传」）
    ok, note, err = validate_text_length(data.get("note", src.get("note")), NOTE_MAX_LENGTH, "备注")
    if not ok:
        return None, err
    out["note"] = note if note is not None else ""

    ok, left_dur, err = validate_seconds(pick("left_duration"), "左侧哺乳时长")
    if not ok:
        return None, err
    ok, right_dur, err = validate_seconds(pick("right_duration"), "右侧哺乳时长")
    if not ok:
        return None, err
    out["left_duration"] = left_dur
    out["right_duration"] = right_dur

    # 只计了左右侧时长、没填结束时间 → 补一个结束时间，这样单次总时长不会丢
    if not out["end_time"] and (left_dur or right_dur):
        total_sec = (left_dur or 0) + (right_dur or 0)
        start_dt = datetime.datetime.strptime(start_time, "%Y-%m-%d %H:%M:%S")
        out["end_time"] = (start_dt + datetime.timedelta(seconds=total_sec)).strftime("%Y-%m-%d %H:%M:%S")

    return out, None


@bp.route("/api/babies/<int:baby_id>/feeding", methods=["POST"])
def add_feeding_record(baby_id):
    """添加喂奶记录"""
    clean, err = _clean_feeding_payload(json_body())
    if err:
        return jsonify({"success": False, "message": err}), 400

    db = get_db()
    cur = db.execute(
        """INSERT INTO feeding_records (baby_id, start_time, end_time, feeding_type, amount, side, note, left_duration, right_duration)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (baby_id, clean["start_time"], clean["end_time"], clean["feeding_type"], clean["amount"],
         clean["side"], clean["note"], clean["left_duration"], clean["right_duration"]),
    )
    db.commit()
    return jsonify({"success": True, "message": "记录已添加", "id": cur.lastrowid, "data": clean})


@bp.route("/api/babies/<int:baby_id>/feeding", methods=["GET"])
def list_feeding_records(baby_id):
    """获取喂奶记录列表（支持 start/end 按日期筛选，limit 控制条数）"""
    db = get_db()
    start = (request.args.get("start") or "").strip()
    end = (request.args.get("end") or "").strip()

    query = "SELECT * FROM feeding_records WHERE baby_id = ?"
    params = [baby_id]
    if start:
        query += " AND substr(start_time, 1, 10) >= ?"
        params.append(start[:10])
    if end:
        query += " AND substr(start_time, 1, 10) <= ?"
        params.append(end[:10])

    # 排序前把 'T' 归一化成空格，避免两种格式混存时排在后面的记录抢到前面
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


@bp.route("/api/babies/<int:baby_id>/feedings", methods=["GET"])
def list_feeding_records_plural(baby_id):
    """获取喂奶记录列表（复数形式，兼容前端）"""
    return list_feeding_records(baby_id)


@bp.route("/api/feeding/<int:record_id>", methods=["DELETE"])
def delete_feeding_record(record_id):
    """删除喂奶记录"""
    db = get_db()
    cur = db.execute("DELETE FROM feeding_records WHERE id = ?", (record_id,))
    db.commit()
    if cur.rowcount == 0:
        return jsonify({"success": False, "message": "记录不存在"}), 404
    return jsonify({"success": True, "message": "已删除"})


@bp.route("/api/babies/<int:baby_id>/feeding/<int:record_id>", methods=["GET"])
def get_feeding_record(baby_id, record_id):
    """获取单条喂奶记录"""
    db = get_db()
    row = db.execute(
        "SELECT * FROM feeding_records WHERE id = ? AND baby_id = ?",
        (record_id, baby_id)
    ).fetchone()
    if not row:
        return jsonify({"success": False, "message": "记录不存在"}), 404
    return jsonify({"success": True, "data": dict(row)})


@bp.route("/api/babies/<int:baby_id>/feeding/<int:record_id>", methods=["PUT"])
def update_feeding_record(baby_id, record_id):
    """更新喂奶记录（部分更新：没传的字段保持原值）"""
    data = json_body()
    db = get_db()
    row = db.execute(
        "SELECT * FROM feeding_records WHERE id = ? AND baby_id = ?",
        (record_id, baby_id)
    ).fetchone()
    if not row:
        return jsonify({"success": False, "message": "记录不存在"}), 404

    clean, err = _clean_feeding_payload(data, existing=dict(row))
    if err:
        return jsonify({"success": False, "message": err}), 400

    db.execute(
        """UPDATE feeding_records
           SET start_time=?, end_time=?, feeding_type=?, amount=?, side=?, note=?,
               left_duration=?, right_duration=?
           WHERE id=? AND baby_id=?""",
        (clean["start_time"], clean["end_time"], clean["feeding_type"], clean["amount"],
         clean["side"], clean["note"], clean["left_duration"], clean["right_duration"],
         record_id, baby_id),
    )
    db.commit()
    return jsonify({"success": True, "message": "已更新", "data": clean})


@bp.route("/api/babies/<int:baby_id>/feeding/stats", methods=["GET"])
def get_feeding_stats(baby_id):
    """获取喂奶统计。

    兼容约定：today / week 仍是「按类型分组」的字典（老前端按 Object.keys 遍历），
    汇总值放在 today_total / week_total，避免把汇总 key 混进类型字典里。
    """
    db = get_db()
    today = datetime.date.today().strftime("%Y-%m-%d")
    week_start = (datetime.date.today() - datetime.timedelta(days=6)).strftime("%Y-%m-%d")

    def _aggregate(since_date):
        rows = db.execute(
            """SELECT feeding_type,
                      COUNT(*) AS count,
                      COALESCE(SUM(amount), 0) AS amount,
                      COALESCE(SUM(COALESCE(left_duration, 0) + COALESCE(right_duration, 0)), 0) AS seconds
               FROM feeding_records
               WHERE baby_id = ? AND substr(start_time, 1, 10) >= ?
               GROUP BY feeding_type""",
            (baby_id, since_date),
        ).fetchall()
        by_type = {
            r["feeding_type"]: {"count": r["count"], "amount": r["amount"], "seconds": r["seconds"]}
            for r in rows
        }
        total = {
            "count": sum(v["count"] for v in by_type.values()),
            "amount": round(sum(v["amount"] for v in by_type.values()), 1),
            "breast_seconds": by_type.get("breast", {}).get("seconds", 0),
        }
        return by_type, total

    today_by_type, today_total = _aggregate(today)
    week_by_type, week_total = _aggregate(week_start)

    # 上次喂奶 + 距今天数
    last_row = db.execute(
        "SELECT start_time FROM feeding_records WHERE baby_id = ? "
        "ORDER BY replace(start_time, 'T', ' ') DESC LIMIT 1",
        (baby_id,),
    ).fetchone()
    last_time = last_row["start_time"] if last_row else None
    minutes_since_last = None
    if last_time:
        last_dt = _parse_dt(last_time)
        if last_dt:
            # 负数 = 记了未来的时间，前端会显示成 '--'，不在这里偷偷抹平
            minutes_since_last = int((datetime.datetime.now() - last_dt).total_seconds() // 60)

    # 平均喂奶间隔（近 7 天，只算 12 小时内的相邻间隔）
    recent = db.execute(
        "SELECT start_time FROM feeding_records WHERE baby_id = ? AND substr(start_time, 1, 10) >= ? "
        "ORDER BY replace(start_time, 'T', ' ')",
        (baby_id, week_start),
    ).fetchall()
    stamps = []
    for r in recent:
        dt = _parse_dt(r["start_time"])
        if dt:
            stamps.append(dt)
    gaps = []
    for prev, nxt in zip(stamps, stamps[1:]):
        gap = (nxt - prev).total_seconds() / 60
        if 0 < gap <= _MAX_GAP_MINUTES:
            gaps.append(gap)
    avg_interval = int(round(sum(gaps) / len(gaps))) if gaps else None

    # 预计下次喂奶时间（有平均间隔才给，避免拿一个拍脑袋的数误导人）
    next_expected = None
    if last_time and avg_interval:
        last_dt = _parse_dt(last_time)
        if last_dt:
            next_expected = (last_dt + datetime.timedelta(minutes=avg_interval)).strftime("%Y-%m-%d %H:%M:%S")

    return jsonify({"success": True, "data": {
        "today": today_by_type,
        "today_total": today_total,
        "week": week_by_type,
        "week_total": week_total,
        "last_time": last_time,
        "minutes_since_last": minutes_since_last,
        "avg_interval_minutes": avg_interval,
        "next_expected": next_expected,
    }})


@bp.route("/api/babies/<int:baby_id>/pumping", methods=["POST"])
def add_pumping_record(baby_id):
    """添加吸奶记录"""
    data = json_body()
    if not data.get("pump_time"):
        return jsonify({"success": False, "message": "请填写吸奶时间"}), 400

    ok, pump_time, err = validate_datetime(data.get("pump_time"), "吸奶时间")
    if not ok:
        return jsonify({"success": False, "message": err}), 400

    # 校验数值字段
    ok, left, err = validate_amount(data.get("left_amount"), "左侧奶量")
    if not ok:
        return jsonify({"success": False, "message": err}), 400

    ok, right, err = validate_amount(data.get("right_amount"), "右侧奶量")
    if not ok:
        return jsonify({"success": False, "message": err}), 400

    ok, total, err = validate_amount(data.get("total_amount"), "总奶量")
    if not ok:
        return jsonify({"success": False, "message": err}), 400

    ok, duration, err = validate_duration(data.get("duration_minutes"), "吸奶时长")
    if not ok:
        return jsonify({"success": False, "message": err}), 400

    # 左右侧计时时长（秒），由前端计时器提供，可独立/双侧同时
    ok, left_dur, err = validate_seconds(data.get("left_duration"), "左侧时长")
    if not ok:
        return jsonify({"success": False, "message": err}), 400
    ok, right_dur, err = validate_seconds(data.get("right_duration"), "右侧时长")
    if not ok:
        return jsonify({"success": False, "message": err}), 400

    # 计算总奶量
    if total is None and (left is not None or right is not None):
        total = (left or 0) + (right or 0)

    # 仅计时未填总时长时，用左右侧秒数之和折算总分钟数
    if duration is None and (left_dur or right_dur):
        duration = round(((left_dur or 0) + (right_dur or 0)) / 60)

    # 校验文本长度
    ok, note, err = validate_text_length(data.get("note"), NOTE_MAX_LENGTH, "备注")
    if not ok:
        return jsonify({"success": False, "message": err}), 400

    side = data.get("side") or None

    db = get_db()
    db.execute(
        """INSERT INTO pumping_records (baby_id, pump_time, duration_minutes, left_amount, right_amount, total_amount, pump_type, side, left_duration, right_duration, note)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (baby_id, pump_time, duration,
         left, right, total, data.get("pump_type"), side,
         left_dur, right_dur, note),
    )
    db.commit()
    return jsonify({"success": True, "message": "记录已添加"})


@bp.route("/api/babies/<int:baby_id>/pumping", methods=["GET"])
def list_pumping_records(baby_id):
    """获取吸奶记录列表"""
    db = get_db()
    rows = db.execute(
        "SELECT * FROM pumping_records WHERE baby_id = ? "
        "ORDER BY replace(pump_time, 'T', ' ') DESC LIMIT 100",
        (baby_id,)
    ).fetchall()
    return jsonify({"success": True, "data": [dict(r) for r in rows]})


@bp.route("/api/pumping/<int:record_id>", methods=["DELETE"])
def delete_pumping_record(record_id):
    """删除吸奶记录"""
    db = get_db()
    cur = db.execute("DELETE FROM pumping_records WHERE id = ?", (record_id,))
    db.commit()
    if cur.rowcount == 0:
        return jsonify({"success": False, "message": "记录不存在"}), 404
    return jsonify({"success": True, "message": "已删除"})


@bp.route("/api/babies/<int:baby_id>/pumping/<int:record_id>", methods=["GET"])
def get_pumping_record(baby_id, record_id):
    """获取单条吸奶记录（供编辑回显）"""
    db = get_db()
    row = db.execute(
        "SELECT * FROM pumping_records WHERE id = ? AND baby_id = ?",
        (record_id, baby_id),
    ).fetchone()
    if not row:
        return jsonify({"success": False, "message": "记录不存在"}), 404
    return jsonify({"success": True, "data": dict(row)})


@bp.route("/api/babies/<int:baby_id>/pumping/<int:record_id>", methods=["PUT"])
def update_pumping_record(baby_id, record_id):
    """更新吸奶记录"""
    data = json_body()
    db = get_db()
    row = db.execute(
        "SELECT * FROM pumping_records WHERE id = ? AND baby_id = ?",
        (record_id, baby_id),
    ).fetchone()
    if not row:
        return jsonify({"success": False, "message": "记录不存在"}), 404
    old = dict(row)

    ok, pump_time, err = validate_datetime(data.get("pump_time", old.get("pump_time")), "吸奶时间")
    if not ok:
        return jsonify({"success": False, "message": err}), 400

    ok, left, err = validate_amount(data.get("left_amount", old.get("left_amount")), "左侧奶量")
    if not ok:
        return jsonify({"success": False, "message": err}), 400
    ok, right, err = validate_amount(data.get("right_amount", old.get("right_amount")), "右侧奶量")
    if not ok:
        return jsonify({"success": False, "message": err}), 400
    ok, total, err = validate_amount(data.get("total_amount", old.get("total_amount")), "总奶量")
    if not ok:
        return jsonify({"success": False, "message": err}), 400
    ok, duration, err = validate_duration(data.get("duration_minutes", old.get("duration_minutes")), "吸奶时长")
    if not ok:
        return jsonify({"success": False, "message": err}), 400
    ok, left_dur, err = validate_seconds(data.get("left_duration", old.get("left_duration")), "左侧时长")
    if not ok:
        return jsonify({"success": False, "message": err}), 400
    ok, right_dur, err = validate_seconds(data.get("right_duration", old.get("right_duration")), "右侧时长")
    if not ok:
        return jsonify({"success": False, "message": err}), 400

    if total is None and (left is not None or right is not None):
        total = (left or 0) + (right or 0)
    if duration is None and (left_dur or right_dur):
        duration = round(((left_dur or 0) + (right_dur or 0)) / 60)

    ok, note, err = validate_text_length(data.get("note", old.get("note")), NOTE_MAX_LENGTH, "备注")
    if not ok:
        return jsonify({"success": False, "message": err}), 400

    db.execute(
        """UPDATE pumping_records
           SET pump_time=?, pump_type=?, side=?, duration_minutes=?, left_amount=?, right_amount=?,
               total_amount=?, left_duration=?, right_duration=?, note=?
           WHERE id=? AND baby_id=?""",
        (pump_time, data.get("pump_type", old.get("pump_type")),
         data.get("side", old.get("side")) or None, duration,
         left, right, total, left_dur, right_dur, note, record_id, baby_id),
    )
    db.commit()
    return jsonify({"success": True, "message": "已更新"})


@bp.route("/api/babies/<int:baby_id>/pumping/stats", methods=["GET"])
def get_pumping_stats(baby_id):
    """获取吸奶统计"""
    db = get_db()
    today = datetime.date.today().strftime("%Y-%m-%d")

    # 今日统计
    today_stat = db.execute(
        """SELECT COUNT(*) as count, COALESCE(SUM(total_amount), 0) as total,
                  COALESCE(SUM(duration_minutes), 0) as duration
           FROM pumping_records WHERE baby_id = ? AND substr(pump_time, 1, 10) = ?""",
        (baby_id, today),
    ).fetchone()

    # 近7天统计
    week_ago = (datetime.date.today() - datetime.timedelta(days=7)).strftime("%Y-%m-%d")
    week_stat = db.execute(
        """SELECT COUNT(*) as count, COALESCE(SUM(total_amount), 0) as total,
                  COALESCE(SUM(duration_minutes), 0) as duration
           FROM pumping_records WHERE baby_id = ? AND substr(pump_time, 1, 10) >= ?""",
        (baby_id, week_ago),
    ).fetchone()

    return jsonify({"success": True, "data": {
        "today": {"count": today_stat["count"], "amount": today_stat["total"], "duration": today_stat["duration"]},
        "week": {"count": week_stat["count"], "amount": week_stat["total"], "duration": week_stat["duration"]},
    }})
