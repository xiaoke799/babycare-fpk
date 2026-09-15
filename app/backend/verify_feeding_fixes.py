#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xiaoke799
"""
喂奶功能离线自检：不启服务、不碰真实数据库。

用一个临时 sqlite 库 + Flask test_client 跑真实路由，验证：
  1) 时间格式统一（datetime-local 的 T 格式入库后变成空格分隔）
  2) 0ml / 空哺乳侧 不写脏值
  3) 左右侧计时秒数能存、结束时间能自动补
  4) 编辑是「部分更新」，不会抹掉左右侧时长
  5) 编辑时可以把奶量/哺乳侧清空
  6) 按日期筛选能命中带 T 的历史记录
  7) 统计接口返回汇总与平均间隔

跑法：
  python C:\\Users\\X\\.workbuddy\\binaries\\python\\envs\\babycare\\Scripts\\python.exe verify_feeding_fixes.py
"""

import os
import sys
import sqlite3
import tempfile
import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import utils
from flask import Flask
from blueprints.feeding import bp

TMP_DB = os.path.join(tempfile.gettempdir(), "verify_feeding_fixes.db")
if os.path.exists(TMP_DB):
    os.remove(TMP_DB)

conn = sqlite3.connect(TMP_DB)
conn.executescript(
    """
    CREATE TABLE babies (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, active INTEGER DEFAULT 1);
    CREATE TABLE feeding_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        baby_id INTEGER NOT NULL,
        start_time TEXT NOT NULL,
        end_time TEXT DEFAULT NULL,
        feeding_type TEXT NOT NULL,
        amount REAL DEFAULT NULL,
        side TEXT DEFAULT NULL,
        note TEXT DEFAULT '',
        left_duration INTEGER DEFAULT NULL,
        right_duration INTEGER DEFAULT NULL,
        created_at TEXT DEFAULT (datetime('now','localtime'))
    );
    CREATE TABLE pumping_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        baby_id INTEGER NOT NULL,
        pump_time TEXT NOT NULL,
        duration_minutes INTEGER DEFAULT NULL,
        left_amount REAL DEFAULT NULL,
        right_amount REAL DEFAULT NULL,
        total_amount REAL DEFAULT NULL,
        pump_type TEXT DEFAULT NULL,
        side TEXT DEFAULT NULL,
        left_duration INTEGER DEFAULT NULL,
        right_duration INTEGER DEFAULT NULL,
        note TEXT DEFAULT ''
    );
    """
)
conn.execute("INSERT INTO babies (id, name) VALUES (1, '测试宝宝')")
conn.commit()
conn.close()

# get_db() 读的是 utils 模块里的 DB_PATH，这里指向临时库
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
        print(f"  [FAIL] {name} {extra}")


def post(payload):
    return client.post("/api/babies/1/feeding", json=payload)


def raw(record_id):
    c = sqlite3.connect(TMP_DB)
    c.row_factory = sqlite3.Row
    row = c.execute("SELECT * FROM feeding_records WHERE id = ?", (record_id,)).fetchone()
    c.close()
    return dict(row) if row else None


print("\n=== 1. 时间格式归一化 ===")
r = post({"start_time": "2026-09-16T10:30", "feeding_type": "bottle", "amount": 120})
check("T 格式能提交成功", r.get_json().get("success"), r.get_json())
rec_id = r.get_json().get("id")
row = raw(rec_id)
check("入库时间是空格分隔", row["start_time"] == "2026-09-16 10:30:00", row["start_time"])

print("\n=== 2. 不写脏值 ===")
r = post({"start_time": f"{TODAY} 08:00:00", "feeding_type": "breast", "amount": 0, "side": ""})
row = raw(r.get_json().get("id"))
check("amount=0 存成 NULL", row["amount"] is None, row["amount"])
check("side='' 存成 NULL", row["side"] is None, row["side"])

print("\n=== 3. 左右侧计时 + 结束时间自动补 ===")
r = post({
    "start_time": f"{TODAY} 09:00:00",
    "feeding_type": "breast",
    "side": "both",
    "left_duration": 600,
    "right_duration": 540,
})
row = raw(r.get_json().get("id"))
check("左时长入库", row["left_duration"] == 600, row["left_duration"])
check("右时长入库", row["right_duration"] == 540, row["right_duration"])
check("结束时间自动补（+19 分钟）", row["end_time"] == f"{TODAY} 09:19:00", row["end_time"])

print("\n=== 4. 编辑是部分更新，不抹掉左右时长 ===")
edit_id = r.get_json().get("id")
r = client.put(f"/api/babies/1/feeding/{edit_id}", json={"note": "只改备注"})
check("PUT 成功", r.get_json().get("success"), r.get_json())
row = raw(edit_id)
check("左时长保留", row["left_duration"] == 600, row["left_duration"])
check("右时长保留", row["right_duration"] == 540, row["right_duration"])
check("结束时间保留", row["end_time"] == f"{TODAY} 09:19:00", row["end_time"])
check("起始时间保留", row["start_time"] == f"{TODAY} 09:00:00", row["start_time"])
check("备注已更新", row["note"] == "只改备注", row["note"])

print("\n=== 5. 编辑时能清空奶量/哺乳侧 ===")
r = client.put(f"/api/babies/1/feeding/{edit_id}", json={"amount": None, "side": ""})
row = raw(edit_id)
check("奶量清空成 NULL", row["amount"] is None, row["amount"])
check("哺乳侧清空成 NULL", row["side"] is None, row["side"])

print("\n=== 6. 混合喂养类型可用 ===")
r = post({"start_time": f"{TODAY} 12:00:00", "feeding_type": "mixed", "amount": 60, "side": "left"})
check("mixed 能提交", r.get_json().get("success"), r.get_json())

print("\n=== 7. 日期筛选能命中带 T 的历史记录 ===")
c = sqlite3.connect(TMP_DB)
# 模拟历史脏数据：下午的记录带 T，正是会被旧查询漏掉的那种
c.execute(
    "INSERT INTO feeding_records (baby_id, start_time, feeding_type) VALUES (1, ?, 'bottle')",
    (f"{TODAY}T15:30",),
)
c.commit()
c.close()
r = client.get(f"/api/babies/1/feeding?start={TODAY}&end={TODAY}")
data = r.get_json().get("data", [])
hit = [x for x in data if str(x["start_time"]).startswith(f"{TODAY}T")]
check("带 T 的下午记录能被筛到", len(hit) == 1, f"命中 {len(hit)} 条")

print("\n=== 8. 列表按时间倒序（混格式也正确） ===")
times = [x["start_time"] for x in data]
check("倒序排列", times == sorted(times, reverse=True), times[:3])

print("\n=== 9. 统计接口 ===")
r = client.get("/api/babies/1/feeding/stats")
d = r.get_json().get("data", {})
check("today 是按类型分组", isinstance(d.get("today"), dict) and "bottle" in d.get("today", {}), d.get("today"))
check("today_total.count 存在", isinstance(d.get("today_total", {}).get("count"), int), d.get("today_total"))
check("亲喂时长已汇总", d.get("today_total", {}).get("breast_seconds", 0) >= 1140, d.get("today_total"))
check("last_time 有值", bool(d.get("last_time")), d.get("last_time"))
check("minutes_since_last 是数字", isinstance(d.get("minutes_since_last"), (int, float)), d.get("minutes_since_last"))
check("avg_interval 要么是 null 要么是正整数",
      d.get("avg_interval_minutes") is None or (isinstance(d.get("avg_interval_minutes"), int) and d["avg_interval_minutes"] > 0),
      d.get("avg_interval_minutes"))

print("\n=== 10. 参数校验 ===")
check("结束早于开始被拒",
      client.post("/api/babies/1/feeding", json={
          "start_time": f"{TODAY} 10:00:00", "end_time": f"{TODAY} 09:00:00", "feeding_type": "bottle"
      }).status_code == 400)
check("缺开始时间被拒",
      client.post("/api/babies/1/feeding", json={"feeding_type": "bottle"}).status_code == 400)
check("非法类型被拒",
      client.post("/api/babies/1/feeding", json={
          "start_time": f"{TODAY} 10:00:00", "feeding_type": "beer"
      }).status_code == 400)
check("乱写时间格式被拒",
      client.post("/api/babies/1/feeding", json={
          "start_time": "昨天下午", "feeding_type": "bottle"
      }).status_code == 400)
check("编辑不存在的记录返回 404",
      client.put("/api/babies/1/feeding/99999", json={"note": "x"}).status_code == 404)

print("\n=== 11. 喂奶提醒调度器（之前查的是不存在的表，一直是死的） ===")
try:
    from notifier.reminder_scheduler import ReminderScheduler

    sent = []

    class FakeNotifier:
        def send_notification(self, title, body, **kwargs):
            sent.append((title, body))

    c = sqlite3.connect(TMP_DB)
    c.row_factory = sqlite3.Row
    # 单独用 2 号宝宝：1 号宝宝的测试数据里混着「未来时间」的记录，
    # 正好顺便验证未来记录不会把提醒卡死
    c.execute("INSERT INTO babies (id, name, active) VALUES (2, '提醒宝宝', 1)")
    four_hours_ago = (datetime.datetime.now() - datetime.timedelta(hours=4)).strftime("%Y-%m-%d %H:%M:%S")
    c.execute(
        "INSERT INTO feeding_records (baby_id, start_time, feeding_type) VALUES (2, ?, 'breast')",
        (four_hours_ago,),
    )
    c.commit()

    sched = ReminderScheduler(
        FakeNotifier(),
        lambda: c,
        {"reminder_enabled": True, "feeding_reminder_enabled": True, "feeding_reminder_interval": 3},
    )
    sched._check_feeding_reminder(datetime.datetime.now())
    check("超过 3 小时会发提醒", len(sent) == 1 and "喂奶" in sent[0][0], sent)

    # 刚喂过（间隔 30 分钟）不应该提醒
    sent.clear()
    c.execute("UPDATE feeding_records SET start_time = ? WHERE baby_id = 2",
              ((datetime.datetime.now() - datetime.timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S"),))
    c.commit()
    sched._last_reminders.clear()
    sched._check_feeding_reminder(datetime.datetime.now())
    check("刚喂过不提醒", len(sent) == 0, sent)
    c.close()
except Exception as exc:  # noqa: BLE001
    check("提醒调度器可正常执行", False, f"异常：{exc}")

print("\n=== 12. 历史数据清洗语句（直接读 server.py 里的同一份常量） ===")
try:
    import ast

    server_src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "server.py"),
                      encoding="utf-8").read()
    tree = ast.parse(server_src)
    cleanup_sql = None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "FEEDING_CLEANUP_SQL":
                    cleanup_sql = ast.literal_eval(node.value)
    check("能读到 FEEDING_CLEANUP_SQL", isinstance(cleanup_sql, tuple) and len(cleanup_sql) > 0, cleanup_sql)

    if cleanup_sql:
        c = sqlite3.connect(TMP_DB)
        # 塞一条 T 格式 + 一条 amount=0 的脏数据
        c.execute("INSERT INTO feeding_records (baby_id, start_time, feeding_type, amount) "
                  "VALUES (1, ?, 'bottle', 0)", (f"{TODAY}T20:00",))
        c.commit()
        first = sum(c.execute(sql).rowcount or 0 for sql in cleanup_sql)
        c.commit()
        second = sum(c.execute(sql).rowcount or 0 for sql in cleanup_sql)
        c.commit()
        dirty = c.execute("SELECT COUNT(*) FROM feeding_records WHERE start_time LIKE '%T%'").fetchone()[0]
        zero = c.execute("SELECT COUNT(*) FROM feeding_records WHERE amount = 0").fetchone()[0]
        c.close()
        check("清洗语句有改动数据", first > 0, first)
        check("第二次执行幂等（0 行）", second == 0, second)
        check("洗完没有带 T 的时间", dirty == 0, dirty)
        check("洗完没有 0ml 脏值", zero == 0, zero)
except Exception as exc:  # noqa: BLE001
    check("清洗语句校验可执行", False, f"异常：{exc}")

print(f"\n===== 结果：通过 {_passed} 项，失败 {_failed} 项 =====")
try:
    os.remove(TMP_DB)
except OSError:
    pass  # Windows 上 Flask 的连接可能还没释放，临时库留着无害
sys.exit(1 if _failed else 0)
