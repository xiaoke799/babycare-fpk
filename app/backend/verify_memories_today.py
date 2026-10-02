#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证「那年今天」接口。

判据（对应真实使用场景）：
  A. 往年同月同日的记录能命中，且 years_ago 算对
  B. 今年同月同日的记录**不算**回忆（那是今天，不是回忆）
  C. 同月不同日 / 同日不同月的都不命中
  D. 不止日记：喂养、里程碑等不同数据源都要能命中
  E. date 参数：传哪天就找那天的往年（首页回看某天时用）
  F. 边界：宝宝不存在 404、日期格式错 400、无往年记录返回空列表而不是报错

时间列两种格式都造一份（'YYYY-MM-DD' 与 'YYYY-MM-DD HH:MM:SS'），
确保 strftime 对日期列和时间列都能成立。
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

tmpdir = tempfile.mkdtemp(prefix="babycare_mem_")
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


with app.app_context():
    db = get_db()
    cur = db.execute(
        "INSERT INTO babies (name, birthday, gender) VALUES (?, ?, ?)",
        ("回忆测试宝", "2020-01-01", "other"),
    )
    db.commit()
    baby_id = cur.lastrowid

today = datetime.date.today()
md = today.strftime("%m-%d")
y1 = today.year - 1          # 去年
y3 = today.year - 3          # 三年前


def same_md(year, suffix=""):
    """同月同日的某个年份，suffix 用来造 HH:MM:SS 格式。"""
    return "%04d-%s%s" % (year, md, suffix)


with app.app_context():
    db = get_db()
    # 去年同月同日 —— 喂养（时间列是 HH:MM:SS 格式）
    db.execute(
        "INSERT INTO feeding_records (baby_id, start_time, feeding_type, amount) VALUES (?,?,?,?)",
        (baby_id, same_md(y1, " 08:30:00"), "bottle", 120),
    )
    # 三年前同月同日 —— 里程碑（日期列是 YYYY-MM-DD 格式）
    db.execute(
        "INSERT INTO milestones (baby_id, title, achieved_date, is_first) VALUES (?,?,?,?)",
        (baby_id, "第一次走路", same_md(y3), 1),
    )
    # 今年同月同日 —— 不该算回忆
    db.execute(
        "INSERT INTO feeding_records (baby_id, start_time, feeding_type, amount) VALUES (?,?,?,?)",
        (baby_id, same_md(today.year, " 09:00:00"), "bottle", 100),
    )
    # 去年但不同月同日 —— 不该命中
    other = (today.replace(month=1) if today.month != 1 else today.replace(month=2))
    db.execute(
        "INSERT INTO feeding_records (baby_id, start_time, feeding_type, amount) VALUES (?,?,?,?)",
        (baby_id, "%04d-%02d-%02d 10:00:00" % (y1, other.month, other.day), "bottle", 90),
    )
    db.commit()

c = app.test_client()
with c.session_transaction() as s:
    s["authenticated"] = True

URL = "/api/babies/%d/memories/today" % baby_id

print("=== A. 往年同月同日命中 ===")
r = c.get(URL)
data = (r.get_json() or {}).get("data") or []
chk("接口 200", r.status_code == 200, "实际 %d" % r.status_code)
chk("命中 2 条（去年喂养 + 三年前里程碑）", len(data) == 2, "实际 %d 条" % len(data))
by_type = {d["type"]: d for d in data}
chk("喂养记录命中", "feeding" in by_type)
chk("里程碑命中（不止日记）", "milestone" in by_type)
chk("去年那条 years_ago=1",
    by_type.get("feeding", {}).get("years_ago") == 1,
    "实际 %s" % by_type.get("feeding", {}).get("years_ago"))
chk("三年前那条 years_ago=3",
    by_type.get("milestone", {}).get("years_ago") == 3,
    "实际 %s" % by_type.get("milestone", {}).get("years_ago"))

print("\n=== B/C. 不该命中的不出现 ===")
years = {str(d["time"])[:4] for d in data}
chk("今年的记录不算回忆", str(today.year) not in years, "命中年份 %s" % sorted(years))
chk("同月同日以外的记录没进来", all(str(d["time"])[5:10] == md for d in data))

print("\n=== E. date 参数（首页回看某天）===")
anchor = "%04d-%s" % (y1, md)   # 以去年那一天为锚点
r2 = c.get(URL + "?date=" + anchor)
d2 = (r2.get_json() or {}).get("data") or []
# 锚点是 y1 年，比它更早的只有 y3 那条里程碑
chk("传 date 后只返回更早年份", len(d2) == 1 and d2[0]["type"] == "milestone",
    "实际 %d 条 %s" % (len(d2), [x["type"] for x in d2]))

print("\n=== F. 边界 ===")
chk("宝宝不存在 404", c.get("/api/babies/999999/memories/today").status_code == 404)
chk("日期格式错 400", c.get(URL + "?date=not-a-date").status_code == 400)
r4 = c.get(URL + "?date=2019-01-01")     # 宝宝还没出生的年份
chk("没有往年记录时返回空列表且不报错",
    r4.status_code == 200 and (r4.get_json() or {}).get("data") == [])

print()
if fails:
    print("失败 %d 项" % len(fails))
    sys.exit(1)
print("通过")
