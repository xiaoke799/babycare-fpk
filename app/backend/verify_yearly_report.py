#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证年度成长报告。

判据：
  A. 各类计数与库里真实数据一致（喂奶/睡眠/尿布/日记/照片/里程碑/疫苗/体检）
  B. 身高体重取的是「这一年内最早一次 → 最近一次」，不是全年平均值
  C. 只统计指定年份：跨年的记录不被算进来
  D. 未配置 AI 模型时 summary 为 null，但数据照常返回（报告不能因为 AI 挂了就没有）
  E. 宝宝不存在 404；年份非法 400
"""
import datetime
import os
import sys
import tempfile
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if "fcntl" not in sys.modules:
    m = types.ModuleType("fcntl")
    m.LOCK_EX = 2
    m.LOCK_NB = 4
    m.LOCK_UN = 8
    m.flock = lambda *a, **k: None
    sys.modules["fcntl"] = m

tmpdir = tempfile.mkdtemp(prefix="babycare_yr_")
os.environ["BABYCARE_DATA_DIR"] = tmpdir
os.environ["BABYCARE_DB_PATH"] = os.path.join(tmpdir, "babycare.db")
os.environ["BABYCARE_CONFIG_DIR"] = tmpdir

import server  # noqa: E402

app = server.app
app.config["TESTING"] = True
server.init_db()

from utils import get_db  # noqa: E402

fails = []


def chk(label, ok, extra=""):
    print(("  [OK]   " if ok else "  [FAIL] ") + label + (("  " + extra) if extra else ""))
    if not ok:
        fails.append(label)


YEAR = 2025

with app.app_context():
    db = get_db()
    cur = db.execute(
        "INSERT INTO babies (name, birthday, gender) VALUES (?, ?, ?)",
        ("年度报告宝", "2023-01-01", "other"),
    )
    db.commit()
    baby_id = cur.lastrowid

with app.app_context():
    db = get_db()
    # 年内：3 次喂奶共 300ml、2 次睡眠共 180 分钟、2 次尿布、2 篇日记、1 张照片
    for i in range(3):
        db.execute(
            "INSERT INTO feeding_records (baby_id, start_time, feeding_type, amount) VALUES (?,?,?,?)",
            (baby_id, "2025-03-0%d 08:00:00" % (i + 1), "bottle", 100),
        )
    db.execute(
        "INSERT INTO sleep_records (baby_id, start_time, end_time, duration_minutes) VALUES (?,?,?,?)",
        (baby_id, "2025-04-01 12:00:00", "2025-04-01 13:30:00", 90),
    )
    db.execute(
        "INSERT INTO sleep_records (baby_id, start_time, end_time, duration_minutes) VALUES (?,?,?,?)",
        (baby_id, "2025-04-02 12:00:00", "2025-04-02 13:30:00", 90),
    )
    for i in range(2):
        db.execute(
            "INSERT INTO diaper_records (baby_id, change_time, diaper_type) VALUES (?,?,?)",
            (baby_id, "2025-05-0%d 10:00:00" % (i + 1), "wet"),
        )
    for i in range(2):
        db.execute(
            "INSERT INTO diary_entries (baby_id, title, content, entry_date) VALUES (?,?,?,?)",
            (baby_id, "日记%d" % i, "内容", "2025-06-0%d" % (i + 1)),
        )
    db.execute(
        "INSERT INTO photos (baby_id, photo_date, file_path) VALUES (?,?,?)",
        (baby_id, "2025-07-01", "x.jpg"),
    )
    db.execute(
        "INSERT INTO milestones (baby_id, title, achieved_date, is_first) VALUES (?,?,?,?)",
        (baby_id, "会叫妈妈", "2025-08-01", 1),
    )
    # 生长：年内两次测量（50→60cm, 4→6kg）
    db.execute(
        "INSERT INTO growth_records (baby_id, record_date, height, weight) VALUES (?,?,?,?)",
        (baby_id, "2025-01-10", 50, 4),
    )
    db.execute(
        "INSERT INTO growth_records (baby_id, record_date, height, weight) VALUES (?,?,?,?)",
        (baby_id, "2025-12-20", 60, 6),
    )
    # 跨年：2024 年的喂奶不该被算进 2025
    db.execute(
        "INSERT INTO feeding_records (baby_id, start_time, feeding_type, amount) VALUES (?,?,?,?)",
        (baby_id, "2024-12-31 08:00:00", "bottle", 999),
    )
    db.commit()

c = app.test_client()
with c.session_transaction() as s:
    s["authenticated"] = True

URL = "/api/babies/%d/yearly-report" % baby_id

print("=== A. 各类计数 ===")
r = c.get(URL + "?year=%d" % YEAR)
js = r.get_json() or {}
d = js.get("data") or {}
chk("接口 200", r.status_code == 200, "实际 %d" % r.status_code)
chk("喂奶 3 次", d.get("feeding_count") == 3, "实际 %s" % d.get("feeding_count"))
chk("奶量合计 300ml", d.get("feeding_ml") == 300, "实际 %s" % d.get("feeding_ml"))
chk("睡眠 2 次", d.get("sleep_count") == 2, "实际 %s" % d.get("sleep_count"))
chk("睡眠 3.0 小时", d.get("sleep_hours") == 3.0, "实际 %s" % d.get("sleep_hours"))
chk("尿布 2 次", d.get("diaper_count") == 2, "实际 %s" % d.get("diaper_count"))
chk("日记 2 篇", d.get("diary_count") == 2, "实际 %s" % d.get("diary_count"))
chk("照片 1 张", d.get("photo_count") == 1, "实际 %s" % d.get("photo_count"))
chk("里程碑 1 条", d.get("milestone_count") == 1, "实际 %s" % d.get("milestone_count"))
chk("里程碑标题带出来", d.get("milestone_titles") == ["会叫妈妈"],
    "实际 %s" % d.get("milestone_titles"))

print("\n=== B. 身高体重是「年初 → 年末」===")
chk("身高 50 → 60", d.get("height_from") == 50 and d.get("height_to") == 60,
    "实际 %s → %s" % (d.get("height_from"), d.get("height_to")))
chk("体重 4 → 6", d.get("weight_from") == 4 and d.get("weight_to") == 6,
    "实际 %s → %s" % (d.get("weight_from"), d.get("weight_to")))

print("\n=== C. 只统计指定年份 ===")
r2 = c.get(URL + "?year=2024")
d2 = (r2.get_json() or {}).get("data") or {}
chk("2024 年只有那 1 次跨年喂奶", d2.get("feeding_count") == 1,
    "实际 %s" % d2.get("feeding_count"))
chk("2024 年没有日记", d2.get("diary_count") == 0, "实际 %s" % d2.get("diary_count"))

print("\n=== D. 未配置 AI 时只给数据 ===")
r3 = c.get(URL + "?year=%d&summary=1" % YEAR)
j3 = r3.get_json() or {}
chk("接口仍 200", r3.status_code == 200, "实际 %d" % r3.status_code)
chk("数据照常返回", (j3.get("data") or {}).get("diary_count") == 2)
chk("summary 为 null 而不是报错", j3.get("summary") is None,
    "实际 %r" % (j3.get("summary"),))

print("\n=== E. 边界 ===")
chk("宝宝不存在 404", c.get("/api/babies/999999/yearly-report").status_code == 404)
chk("年份非法 400", c.get(URL + "?year=abc").status_code == 400)

print()
if fails:
    print("失败 %d 项" % len(fails))
    sys.exit(1)
print("通过")
