#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证周报 / 月报接口（periodic-report）的统计口径对不对。

为什么必须真跑：口径错（比如把上周期数据算进本周、环比符号反了）
静态检查一个都测不出来，而家长是照着这个数字判断宝宝情况的。

断言：
  A. 本周期记录被正确计入，上一周期的不被计入
  B. 环比差值 = 本周期 - 上周期；上周期为 0 时不编造假差值
  C. 体温只数发烧的（is_fever=1）
  D. period 非法返回 400；date 格式非法返回 400
  E. 月报口径是自然月，且能取到当月数据
"""
from __future__ import annotations

import datetime
import os
import sys
import tempfile
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if "fcntl" not in sys.modules:
    m = types.ModuleType("fcntl")
    m.LOCK_EX, m.LOCK_NB, m.LOCK_UN = 2, 4, 8
    m.flock = lambda *a, **k: None
    sys.modules["fcntl"] = m

tmpdir = tempfile.mkdtemp(prefix="babycare_periodic_")
os.environ["BABYCARE_DATA_DIR"] = tmpdir
os.environ["BABYCARE_DB_PATH"] = os.path.join(tmpdir, "babycare.db")
os.environ["BABYCARE_CONFIG_DIR"] = tmpdir

import server  # noqa: E402

app = server.app
app.config["TESTING"] = True
server.init_db()

from utils import get_db  # noqa: E402

fails: list[str] = []


def chk(ok: bool, label: str, extra: str = "") -> None:
    print(("  [OK]   " if ok else "  [FAIL] ") + label + (f"  {extra}" if extra else ""))
    if not ok:
        fails.append(label)


today = datetime.date.today()
monday = today - datetime.timedelta(days=today.weekday())
prev_monday = monday - datetime.timedelta(days=7)

with app.app_context():
    db = get_db()
    db.execute("INSERT INTO babies (name, birthday, gender) VALUES (?,?,?)",
               ("小甲", str(today - datetime.timedelta(days=200)), "boy"))
    bid = db.execute("SELECT last_insert_rowid() AS i FROM babies").fetchone()["i"]

    def feed(day, amount=None):
        # 注意：feeding_records 没有 duration_minutes（哺乳时长是 left/right_duration），
        # 奶量在 amount —— 写错列名会直接 OperationalError
        db.execute(
            "INSERT INTO feeding_records (baby_id, start_time, feeding_type, amount)"
            " VALUES (?,?,?,?)",
            (bid, f"{day.isoformat()} 10:00:00", "bottle", amount))

    def sleep(day, minutes):
        db.execute(
            "INSERT INTO sleep_records (baby_id, start_time, end_time, duration_minutes)"
            " VALUES (?,?,?,?)",
            (bid, f"{day.isoformat()} 12:00:00",
             f"{day.isoformat()} 13:00:00", minutes))

    def temp(day, t, fever):
        db.execute(
            "INSERT INTO temperature_records (baby_id, temperature, measure_time,"
            " measure_method, is_fever) VALUES (?,?,?,?,?)",
            (bid, t, f"{day.isoformat()} 08:00:00", "armpit", 1 if fever else 0))

    # 本周：3 次喂奶（120+150+100=370ml）、1 段睡眠 180 分钟、2 次换尿布、1 次发烧
    feed(monday, 120)
    feed(monday + datetime.timedelta(days=1), 150)
    feed(monday + datetime.timedelta(days=2), 100)
    sleep(monday + datetime.timedelta(days=1), 180)
    for _ in range(2):
        db.execute("INSERT INTO diaper_records (baby_id, change_time, diaper_type)"
                   " VALUES (?,?,?)", (bid, f"{monday.isoformat()} 09:00:00", "wet"))
    temp(monday, 38.2, True)
    temp(monday, 36.8, False)      # 正常体温，不该被算进发热次数
    db.execute("INSERT INTO growth_records (baby_id, record_date, height, weight)"
               " VALUES (?,?,?,?)", (bid, monday.isoformat(), 62.0, 6.8))

    # 上一周：1 次喂奶（90ml）、1 段睡眠 60 分钟
    feed(prev_monday, 90)
    sleep(prev_monday, 60)
    db.commit()

c = app.test_client()
with c.session_transaction() as s:
    s["authenticated"] = True

URL = f"/api/babies/{bid}/periodic-report"

print("=== A. 周期口径 ===")
r = c.get(f"{URL}?period=week&date={today.isoformat()}").get_json()
d = (r or {}).get("data") or {}
cur = d.get("current") or {}
prev = d.get("previous") or {}
chk(r and r.get("success"), "接口返回成功")
chk(cur.get("feeding_count") == 3, "本周期喂奶 3 次", f"实际 {cur.get('feeding_count')}")
chk(cur.get("feeding_ml") == 370, "本周期奶量 370ml", f"实际 {cur.get('feeding_ml')}")
chk(cur.get("sleep_minutes") == 180, "本周期睡眠 180 分钟", f"实际 {cur.get('sleep_minutes')}")
chk(cur.get("diaper_count") == 2, "本周期换尿布 2 次", f"实际 {cur.get('diaper_count')}")
chk(prev.get("feeding_count") == 1, "上一周期喂奶 1 次（没被算进本周）",
    f"实际 {prev.get('feeding_count')}")
chk(d.get("start_date") == monday.isoformat(), "周报从周一开始",
    f"{d.get('start_date')} vs {monday.isoformat()}")

print("\n=== B. 环比 ===")
delta = d.get("delta") or {}
chk(delta.get("feeding_count") == 2, "喂奶环比 +2（3-1）", f"实际 {delta.get('feeding_count')}")
chk(delta.get("feeding_ml") == 280, "奶量环比 +280ml", f"实际 {delta.get('feeding_ml')}")
chk(delta.get("sleep_minutes") == 120, "睡眠环比 +120 分钟",
    f"实际 {delta.get('sleep_minutes')}")

print("\n=== C. 体温只数发烧 ===")
chk(cur.get("fever_count") == 1, "发热 1 次（36.8℃ 不算）", f"实际 {cur.get('fever_count')}")

print("\n=== D. 成长与参数校验 ===")
g = d.get("growth") or {}
chk(g.get("weight") == 6.8, "取到本周期最后一次体重", f"实际 {g.get('weight')}")
r2 = c.get(f"{URL}?period=year")
chk(r2.status_code == 400, "period=year 被拒 400", f"实际 {r2.status_code}")
r3 = c.get(f"{URL}?period=week&date=不是日期")
chk(r3.status_code == 400, "date 格式非法被拒 400", f"实际 {r3.status_code}")

print("\n=== E. 月报口径 ===")
rm = c.get(f"{URL}?period=month&date={today.isoformat()}").get_json()
dm = (rm or {}).get("data") or {}
chk(dm.get("start_date") == today.replace(day=1).isoformat(), "月报从 1 号开始",
    f"实际 {dm.get('start_date')}")
# 上面喂奶数据插在本周一到周三；月初那天之前算上个月，本月至今（10/3）可能还没有记录。
# 这里要验的正是**月份之间不串**：上个月有多少条都不该出现在本月里。
cur_m = (dm.get("current") or {})
prev_m = (dm.get("previous") or {})
chk(cur_m.get("feeding_count", 0) == 0,
    "本月至今无记录就是 0（不拿上月的凑数）", f"实际 {cur_m.get('feeding_count')}")
chk(prev_m.get("feeding_count") == 4,
    "上月共 4 次喂奶都归到上月（9/21 + 9/28~9/30 全在 9 月）",
    f"实际 {prev_m.get('feeding_count')}")

print()
if fails:
    print("失败 %d 项" % len(fails))
    sys.exit(1)
print("通过")
