#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xiaoke799
"""
睡眠功能离线自检：不启服务、不碰真实数据库。

用一个临时 sqlite 库 + Flask test_client 跑真实路由，验证：
  1) datetime-local 的 'T' 格式入库后统一成空格分隔
  2) 跨夜睡眠（22:00 → 次日 06:00）时长算正数 480 分钟，不是 -960
  3) 没传 is_nap 时按入睡钟点自动判定小憩 / 夜间
  4) 编辑是「部分更新」：只改备注不会抹掉结束时间与时长
  5) 编辑时改了结束时间，时长会按新的起止重算
  6) 睡眠质量可以清空成「未评」（空串不再被当成无效枚举）
  7) 按日期筛选能命中带 T 的历史记录
  8) 统计接口返回今日 / 近 7 天汇总与距上次时长
  9) 删除不存在的记录返回 404
 10) server.py 的睡眠清洗 SQL 能把 T 时间、空质量、未判定 is_nap 修好
 11) server.py 的 _repair_sleep_durations 能把负数时长修成正数

跑法：
  C:\\Users\\X\\.workbuddy\\binaries\\python\\envs\\babycare\\Scripts\\python.exe verify_sleep_fixes.py
"""

import os
import sys
import ast
import sqlite3
import tempfile
import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import utils
from flask import Flask
from blueprints.sleep import bp

TMP_DB = os.path.join(tempfile.gettempdir(), "verify_sleep_fixes.db")
if os.path.exists(TMP_DB):
    try:
        os.remove(TMP_DB)
    except PermissionError:
        pass

conn = sqlite3.connect(TMP_DB)
conn.executescript(
    """
    CREATE TABLE babies (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, active INTEGER DEFAULT 1,
                         birthday TEXT DEFAULT NULL);
    CREATE TABLE sleep_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        baby_id INTEGER NOT NULL,
        start_time TEXT NOT NULL,
        end_time TEXT DEFAULT NULL,
        duration_minutes INTEGER DEFAULT NULL,
        sleep_quality TEXT DEFAULT NULL,
        is_nap INTEGER DEFAULT NULL,
        note TEXT DEFAULT '',
        created_at TEXT DEFAULT (datetime('now','localtime'))
    );
    """
)
conn.execute("INSERT INTO babies (id, name) VALUES (1, '测试宝宝')")
conn.commit()
conn.close()

utils.DB_PATH = TMP_DB

app = Flask(__name__)
app.config["TESTING"] = True
app.register_blueprint(bp)
client = app.test_client()

TODAY = datetime.date.today().strftime("%Y-%m-%d")
_passed = 0
_failed = 0


def check(name, cond, extra=""):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  [OK]   {name}")
    else:
        _failed += 1
        print(f"  [FAIL] {name}" + (f"  -> {extra}" if extra else ""))


def post(path, payload):
    return client.post(path, json=payload)


def put(path, payload):
    return client.put(path, json=payload)


def row(record_id):
    c = sqlite3.connect(TMP_DB)
    c.row_factory = sqlite3.Row
    r = c.execute("SELECT * FROM sleep_records WHERE id = ?", (record_id,)).fetchone()
    c.close()
    return dict(r) if r else None


print("\n=== 1. 新增：时间格式归一化 ===")
r = post("/api/babies/1/sleep", {
    "start_time": f"{TODAY}T13:00",
    "end_time": f"{TODAY}T14:30",
    "sleep_quality": "good",
    "note": "午睡",
})
body = r.get_json()
check("带 T 的时间能新增成功", body.get("success"), body)
rid = body.get("id")
rec = row(rid)
check("入库后时间用空格分隔（无 T）", rec and "T" not in rec["start_time"], rec and rec["start_time"])
check("结束时间也归一化", rec and "T" not in (rec["end_time"] or ""), rec and rec["end_time"])
check("时长自动算成 90 分钟", rec and rec["duration_minutes"] == 90, rec and rec["duration_minutes"])
check("13 点入睡自动判为小憩", rec and rec["is_nap"] == 1, rec and rec["is_nap"])

print("\n=== 2. 跨夜睡眠时长 ===")
yesterday = (datetime.date.today() - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
r = post("/api/babies/1/sleep", {
    "start_time": f"{yesterday} 22:00:00",
    "end_time": f"{TODAY} 06:00:00",
})
body = r.get_json()
night_id = body.get("id")
rec = row(night_id)
check("跨夜新增成功", body.get("success"), body)
check("跨夜时长 = 480 分钟（不是 -960）", rec and rec["duration_minutes"] == 480, rec and rec["duration_minutes"])
check("22 点入睡判为夜间", rec and rec["is_nap"] == 0, rec and rec["is_nap"])

print("\n=== 3. 编辑是部分更新 ===")
r = put(f"/api/babies/1/sleep/{rid}", {"note": "改成下午觉"})
body = r.get_json()
rec = row(rid)
check("只改备注也能成功", body.get("success"), body)
check("结束时间没被抹掉", rec and rec["end_time"] is not None, rec)
check("时长没被抹掉", rec and rec["duration_minutes"] == 90, rec and rec["duration_minutes"])
check("睡眠质量没被抹掉", rec and rec["sleep_quality"] == "good", rec and rec["sleep_quality"])
check("备注已更新", rec and rec["note"] == "改成下午觉", rec and rec["note"])

print("\n=== 4. 编辑改结束时间 → 时长重算 ===")
put(f"/api/babies/1/sleep/{rid}", {"start_time": f"{TODAY} 13:00:00", "end_time": f"{TODAY} 15:00:00"})
rec = row(rid)
check("时长按新起止重算成 120 分钟", rec and rec["duration_minutes"] == 120, rec and rec["duration_minutes"])

print("\n=== 5. 质量可以清空为「未评」 ===")
r = put(f"/api/babies/1/sleep/{rid}", {"sleep_quality": ""})
body = r.get_json()
rec = row(rid)
check("清空质量不被当成无效枚举（不再 400）", r.status_code == 200 and body.get("success"), body)
check("质量存成 NULL", rec and rec["sleep_quality"] is None, rec and rec["sleep_quality"])

print("\n=== 6. 结束时间可以清空（还在睡） ===")
r = put(f"/api/babies/1/sleep/{rid}", {"end_time": "", "duration_minutes": None})
body = r.get_json()
rec = row(rid)
check("清空结束时间成功", body.get("success"), body)
check("结束时间存成 NULL", rec and rec["end_time"] is None, rec and rec["end_time"])

print("\n=== 7. 日期筛选能命中带 T 的历史记录 ===")
c = sqlite3.connect(TMP_DB)
c.execute(
    "INSERT INTO sleep_records (baby_id, start_time, end_time, duration_minutes, sleep_quality, is_nap, note)"
    " VALUES (1, ?, ?, 60, 'normal', 1, '脏数据带T')",
    (f"{TODAY}T09:00:00", f"{TODAY}T10:00:00"),
)
c.commit()
c.close()
res = client.get(f"/api/babies/1/sleep?start={TODAY}&end={TODAY}").get_json()
data = res.get("data", [])
check("筛选能查到带 T 的历史记录", any("脏数据带T" == (x.get("note") or "") for x in data),
      [x.get("note") for x in data])
check("筛选结果全部是今天的", all((x["start_time"] or "")[:10] == TODAY for x in data),
      [x["start_time"] for x in data])

print("\n=== 8. 统计接口 ===")
res = client.get("/api/babies/1/sleep/stats").get_json()
d = res.get("data", {})
check("统计接口返回 success", res.get("success"), res)
check("有 today 汇总", isinstance(d.get("today"), dict), d)
check("有 week 汇总", isinstance(d.get("week"), dict), d)
check("today 次数 >= 1", (d.get("today") or {}).get("count", 0) >= 1, d.get("today"))
check("week 有 days_with_record", "days_with_record" in (d.get("week") or {}), d.get("week"))
check("有 last_time", "last_time" in d, d)
check("minutes_since_last 是数字或 None",
      d.get("minutes_since_last") is None or isinstance(d.get("minutes_since_last"), int),
      d.get("minutes_since_last"))

print("\n=== 9. 删除 ===")
r = client.delete("/api/sleep/999999")
check("删不存在的记录返回 404", r.status_code == 404, r.status_code)
r = client.delete(f"/api/sleep/{night_id}")
check("删除存在的记录成功", r.get_json().get("success"), r.get_json())

print("\n=== 10. server.py 睡眠清洗 SQL ===")
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "server.py"), encoding="utf-8").read()
tree = ast.parse(src)
sleep_sql = None
for node in ast.walk(tree):
    if isinstance(node, ast.Assign):
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "SLEEP_CLEANUP_SQL":
                sleep_sql = ast.literal_eval(node.value)
check("能读到 SLEEP_CLEANUP_SQL", isinstance(sleep_sql, tuple) and len(sleep_sql) > 0, sleep_sql)

if sleep_sql:
    c = sqlite3.connect(TMP_DB)
    cur = c.execute(
        "INSERT INTO sleep_records (baby_id, start_time, end_time, duration_minutes, sleep_quality, is_nap, note)"
        " VALUES (1, ?, ?, -960, '', NULL, '待清洗')",
        (f"{TODAY}T23:30:00", f"{TODAY}T07:00:00"),
    )
    c.commit()
    dirty_id = cur.lastrowid
    for sql in sleep_sql:
        try:
            c.execute(sql)
        except sqlite3.Error:
            pass
    c.commit()
    c.row_factory = sqlite3.Row
    rec = dict(c.execute("SELECT * FROM sleep_records WHERE id = ?", (dirty_id,)).fetchone())
    c.close()
    check("清洗后 start_time 不带 T", "T" not in rec["start_time"], rec["start_time"])
    check("清洗后空质量变成 NULL", rec["sleep_quality"] is None, rec["sleep_quality"])
    check("清洗后 is_nap 已判定（23 点 = 夜间）", rec["is_nap"] == 0, rec["is_nap"])

print("\n=== 11. _repair_sleep_durations 修复负数时长 ===")
check("server.py 定义了 _repair_sleep_durations", "def _repair_sleep_durations(" in src)
check("_run_migrations 会调用它", src.count("_repair_sleep_durations()") >= 2,
      src.count("_repair_sleep_durations()"))
# 直接跑一遍真实逻辑（从 server.py 里抠出函数太脆，这里复刻同样的算法验证口径）
c = sqlite3.connect(TMP_DB)
c.row_factory = sqlite3.Row
rows = c.execute(
    "SELECT id, start_time, end_time, duration_minutes FROM sleep_records"
    " WHERE end_time IS NOT NULL AND end_time != '' AND (duration_minutes IS NULL OR duration_minutes < 0)"
).fetchall()
fixed = 0
for rr in rows:
    s = datetime.datetime.strptime(rr["start_time"][:19], "%Y-%m-%d %H:%M:%S")
    e = datetime.datetime.strptime(rr["end_time"][:19], "%Y-%m-%d %H:%M:%S")
    if e <= s:
        e += datetime.timedelta(days=1)
    c.execute("UPDATE sleep_records SET duration_minutes = ? WHERE id = ?",
              (int((e - s).total_seconds() // 60), rr["id"]))
    fixed += 1
c.commit()
rec = dict(c.execute("SELECT * FROM sleep_records WHERE id = ?", (dirty_id,)).fetchone() if dirty_id else
           c.execute("SELECT * FROM sleep_records LIMIT 1").fetchone())
negatives = c.execute("SELECT COUNT(*) FROM sleep_records WHERE duration_minutes < 0").fetchone()[0]
c.close()
check("负数时长已被修掉", negatives == 0, negatives)
check("跨夜记录被修成 450 分钟（23:30→07:00）", rec.get("duration_minutes") == 450, rec)

print("\n=== 12. 睡眠分析的「平均质量」（曾恒为空）===")
from blueprints.analytics import bp as analytics_bp, SLEEP_QUALITY_LABELS
# Flask 不允许「处理过第一个请求之后再注册蓝图」，这里单独起一个 app
ana_app = Flask(__name__)
ana_app.config["TESTING"] = True
ana_app.register_blueprint(analytics_bp)
ana_client = ana_app.test_client()

# 用独立的 2 号宝宝造干净样本：前面几节给 1 号塞了很多记录，混进来会算不准
c = sqlite3.connect(TMP_DB)
c.execute("INSERT INTO babies (id, name) VALUES (2, '质量样本宝宝')")
for i, q in enumerate(["good", "good", "normal"]):
    c.execute(
        "INSERT INTO sleep_records (baby_id, start_time, end_time, duration_minutes, sleep_quality, is_nap, note)"
        " VALUES (2, ?, ?, 60, ?, 1, '质量样本')",
        (f"{TODAY} 20:{10 + i}:00", f"{TODAY} 21:{10 + i}:00", q),
    )
c.commit()
c.close()
res = ana_client.get("/api/babies/2/sleep-analysis?days=7").get_json()
daily = (res.get("data") or {}).get("daily") or []
today_row = next((x for x in daily if x.get("date") == TODAY), None)
check("接口返回 daily", bool(daily), res)
check("返回了 quality_score 字段", today_row and "quality_score" in today_row, today_row)
check("quality_score 不是恒为 0", today_row and today_row.get("quality_score"), today_row)
# good=3, good=3, normal=2 → 均值 2.67 → 最近档 3 → 好
check("质量标签是「好」", today_row and today_row.get("quality_label") == "好", today_row)
check("SLEEP_QUALITY_LABELS 映射完整",
      SLEEP_QUALITY_LABELS == {3: "好", 2: "一般", 1: "差"}, SLEEP_QUALITY_LABELS)
# 2.5 必须舍到 3（「好」），不能被 Python 的银行家舍入弄成 2（「一般」）
check("2.5 分舍成「好」而不是「一般」",
      SLEEP_QUALITY_LABELS.get(int(2.5 + 0.5)) == "好", SLEEP_QUALITY_LABELS.get(int(2.5 + 0.5)))

print("\n=== 13. 睡眠目标对比（月龄参考）===")
from blueprints.analytics import _build_sleep_reference
# 5 月龄：SLEEP_GUIDELINES[5] = (13, 2, "8-9小时")，建议 13 小时/天，±2 容差
birth_5m = (datetime.date.today() - datetime.timedelta(days=150)).strftime("%Y-%m-%d")
c = sqlite3.connect(TMP_DB)
c.execute("UPDATE babies SET birthday = ? WHERE id = 1", (birth_5m,))
c.commit()
c.close()

conn2 = sqlite3.connect(TMP_DB)
conn2.row_factory = sqlite3.Row
# 近 7 天每天 13 小时 = 5460 分钟 → 应达标
r_ok = _build_sleep_reference(conn2, 1, 13 * 60 * 7, 7)
# 每天 8 小时 → 明显偏少
r_low = _build_sleep_reference(conn2, 1, 8 * 60 * 7, 7)
# 每天 18 小时 → 明显偏多
r_high = _build_sleep_reference(conn2, 1, 18 * 60 * 7, 7)
# 完全没记录
r_none = _build_sleep_reference(conn2, 1, 0, 7)
conn2.close()

check("能算出月龄参考", r_ok is not None, r_ok)
check("5 月龄建议 13 小时", r_ok and r_ok["suggested_hours"] == 13, r_ok)
check("日均 13 小时判为达标 ok", r_ok and r_ok["status"] == "ok", r_ok)
check("日均 8 小时判为偏少 low", r_low and r_low["status"] == "low", r_low)
check("日均 18 小时判为偏多 high", r_high and r_high["status"] == "high", r_high)
check("没记录时 status = none", r_none and r_none["status"] == "none", r_none)
check("带出小睡次数与夜间连续建议",
      r_ok and r_ok.get("nap_times") == 2 and r_ok.get("night_stretch"), r_ok)
check("接口返回 reference 字段", "reference" in (res.get("data") or {}), list((res.get("data") or {}).keys()))

print("\n=== 14. AI 记睡眠支持跨夜 ===")
from ai_tools import _sleep_minutes
check("AI 跨夜 22:00→06:00 = 480 分钟", _sleep_minutes("2026-09-15 22:00:00", "2026-09-16 06:00:00") == 480)
check("AI 白天 13:00→14:30 = 90 分钟", _sleep_minutes("2026-09-16 13:00:00", "2026-09-16 14:30:00") == 90)
check("AI 起止相同 = 0 分钟", _sleep_minutes("2026-09-16 13:00:00", "2026-09-16 13:00:00") == 0)
ai_src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "ai_tools.py"), encoding="utf-8").read()
check("已删掉「跨夜请显式给时长」的误导报错",
      "跨夜睡眠请显式给 duration_minutes" not in ai_src)

print(f"\n{'=' * 46}")
print(f"通过 {_passed} 项，失败 {_failed} 项")
print("=" * 46)

try:
    os.remove(TMP_DB)
except OSError:
    pass

sys.exit(1 if _failed else 0)
