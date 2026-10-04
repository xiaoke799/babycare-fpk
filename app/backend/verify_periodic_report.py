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
    # ⚠ 必须把"未来的日子"夹到今天：monday+1/+2 可能晚于今天（今天 10/05 是周一，
    #   monday == today，+1/+2 直接是明天/后天）。这种未来行的喂奶数月报"本月至今"
    #   数不到，跟按整月取证时对不上（E 段假红就是这么来的）。夹到今天后条数和奶量都不变。
    def clamp(day):
        return min(day, today)

    feed(clamp(monday), 120)
    feed(clamp(monday + datetime.timedelta(days=1)), 150)
    feed(clamp(monday + datetime.timedelta(days=2)), 100)
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

print("\n=== E. 月报口径（不依赖今天是周几、几号）===")
# ⚠ 旧版写死了「本月 0 条 / 上月 4 条」——那只在"本周一落进上个月"的那几天成立。
#   10-05 是周一 ⇒ monday == today，本周一到周三那 3 次喂奶整个落进 10 月，断言当场翻面。
#   月报要验的是**自然月分桶**，正确姿势：按库里真实行的月份取证，跟报告对账，
#   再钉两条月边界记录（上月最后一天 / 今天）保证两个桶都不空。
rm = c.get(f"{URL}?period=month&date={today.isoformat()}").get_json()
dm = (rm or {}).get("data") or {}
chk(dm.get("start_date") == today.replace(day=1).isoformat(), "月报从 1 号开始",
    f"实际 {dm.get('start_date')}")

first_of_this = today.replace(day=1)                                   # 本月 1 号
last_of_prev = first_of_this - datetime.timedelta(days=1)              # 上月最后一天


def seed_feed(day):
    with app.app_context():
        conn = get_db()
        conn.execute("INSERT INTO feeding_records (baby_id, start_time, feeding_type, amount)"
                     " VALUES (?,?,?,?)", (bid, f"{day.isoformat()} 10:00:00", "bottle", 50))
        conn.commit()


def count_month(year_month):
    """喂奶行按 start_time 前 7 位归月（'YYYY-MM-DD HH:MM:SS' ⇒ substr(1,7)='YYYY-MM'）。"""
    with app.app_context():
        conn = get_db()
        return conn.execute("SELECT COUNT(*) AS n FROM feeding_records"
                            " WHERE baby_id=? AND substr(start_time,1,7)=?",
                            (bid, year_month)).fetchone()["n"]


# 月边界各补一条：这两条落在哪个月是确定的，不随"今天周几"变
seed_feed(last_of_prev)
seed_feed(today)

rm2 = c.get(f"{URL}?period=month&date={today.isoformat()}").get_json()
dm2 = (rm2 or {}).get("data") or {}
cur_m2 = (dm2.get("current") or {})
prev_m2 = (dm2.get("previous") or {})

cur_key = today.strftime("%Y-%m")
prev_key = last_of_prev.strftime("%Y-%m")
n_cur = count_month(cur_key)
n_prev = count_month(prev_key)

chk(n_cur > 0 and n_prev > 0,
    "本月和上月都确实有喂奶行（否则下面对账没意义）",
    f"{cur_key}={n_cur} {prev_key}={n_prev}")
chk(cur_m2.get("feeding_count") == n_cur,
    f"本月 {cur_key} 条数对得上（没串上月、没漏）",
    f"报告 {cur_m2.get('feeding_count')} vs 库 {n_cur}")
chk(prev_m2.get("feeding_count") == n_prev,
    f"上月 {prev_key} 条数对得上",
    f"报告 {prev_m2.get('feeding_count')} vs 库 {n_prev}")
chk(cur_m2.get("feeding_count", 0) + prev_m2.get("feeding_count", 0) == n_cur + n_prev,
    "两段相加 == 两个月总行数（不漏桶、不重复计）")

print()
if fails:
    print("失败 %d 项" % len(fails))
    sys.exit(1)
print("通过")
