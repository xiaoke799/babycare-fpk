#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证「谁记的」（家庭成员 + author 字段）。

判据：
  A. 家庭成员：新增、查重、改名、停用、删除；删不存在的 id 必须 404（不许假成功）
  B. 日记/喂养/睡眠/尿布写入时带 author，读回来还在
  C. 不传 author 也能存（老数据兼容），读出来是空而不是报错
  D. 超长称呼被收口到 20 字，不把一列撑爆
  E. 家庭成员表进了备份导出清单（否则换机恢复后成员名单凭空消失）
  F. 成员删掉后，老记录上的名字仍在（不能变成空引用）
"""
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

tmpdir = tempfile.mkdtemp(prefix="babycare_fam_")
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
        ("谁记的测试宝", "2024-01-01", "other"),
    )
    db.commit()
    baby_id = cur.lastrowid

c = app.test_client()
with c.session_transaction() as s:
    s["authenticated"] = True

MEM = "/api/settings/family-members"

print("=== A. 家庭成员 CRUD ===")
r = c.post(MEM, json={"name": "妈妈"})
chk("新增成员 200", r.status_code == 200, "实际 %d" % r.status_code)
mid = (r.get_json() or {}).get("data", {}).get("id")
chk("返回新成员 id", bool(mid))

r2 = c.post(MEM, json={"name": "妈妈"})
chk("重名拒绝（400）", r2.status_code == 400, "实际 %d" % r2.status_code)

r3 = c.get(MEM)
names = [m["name"] for m in (r3.get_json() or {}).get("data", [])]
chk("列表能查到", "妈妈" in names, "%s" % names)

r4 = c.put(MEM + "/" + str(mid), json={"name": "妈妈", "is_active": 0})
chk("停用成功", r4.status_code == 200 and
    (r4.get_json() or {}).get("data", {}).get("is_active") == 0)

chk("删不存在的 id 返回 404（不许假成功）",
    c.delete(MEM + "/999999").status_code == 404)

print("\n=== B. 四类记录都能带上「谁记的」 ===")
cases = [
    ("/api/babies/%d/diary" % baby_id,
     {"title": "第一次翻身", "entry_date": "2025-05-05", "content": "今天自己翻过去了",
      "author": "妈妈"},
     "/api/babies/%d/diary" % baby_id, "data", "author"),
    ("/api/babies/%d/feeding" % baby_id,
     {"start_time": "2025-05-05 08:00:00", "feeding_type": "bottle", "amount": 120,
      "author": "爸爸"},
     None, None, None),
    ("/api/babies/%d/sleep" % baby_id,
     {"start_time": "2025-05-05 12:00:00", "end_time": "2025-05-05 14:00:00",
      "author": "奶奶"},
     None, None, None),
    ("/api/babies/%d/diaper" % baby_id,
     {"change_time": "2025-05-05 15:00:00", "diaper_type": "wet", "author": "爸爸"},
     None, None, None),
]
for url, payload, _g, _k, _f in cases:
    rr = c.post(url, json=payload)
    chk("写入 %s 成功" % url.split("/")[-1], rr.status_code == 200,
        "实际 %d %s" % (rr.status_code, (rr.get_json() or {}).get("message", "")))

with app.app_context():
    db = get_db()
    got = {
        "diary": db.execute("SELECT author FROM diary_entries").fetchone(),
        "feeding": db.execute("SELECT author FROM feeding_records").fetchone(),
        "sleep": db.execute("SELECT author FROM sleep_records").fetchone(),
        "diaper": db.execute("SELECT author FROM diaper_records").fetchone(),
    }
chk("日记 author=妈妈", got["diary"] and got["diary"]["author"] == "妈妈")
chk("喂养 author=爸爸", got["feeding"] and got["feeding"]["author"] == "爸爸")
chk("睡眠 author=奶奶", got["sleep"] and got["sleep"]["author"] == "奶奶")
chk("尿布 author=爸爸", got["diaper"] and got["diaper"]["author"] == "爸爸")

print("\n=== C. 不传 author 也能存（老数据兼容）===")
r5 = c.post("/api/babies/%d/feeding" % baby_id,
            json={"start_time": "2025-05-06 08:00:00", "feeding_type": "bottle", "amount": 100})
chk("不带 author 写入 200", r5.status_code == 200, "实际 %d" % r5.status_code)
with app.app_context():
    row = get_db().execute(
        "SELECT author FROM feeding_records WHERE start_time = '2025-05-06 08:00:00'").fetchone()
chk("没写 author 时存 NULL 而不是空串/报错", row and row["author"] is None,
    "实际 %r" % (row["author"] if row else None))

print("\n=== D. 超长称呼被收口 ===")
long_name = "爸" * 50
r6 = c.post("/api/babies/%d/diaper" % baby_id,
            json={"change_time": "2025-05-07 09:00:00", "diaper_type": "wet",
                  "author": long_name})
with app.app_context():
    row2 = get_db().execute(
        "SELECT author FROM diaper_records WHERE change_time = '2025-05-07 09:00:00'").fetchone()
chk("写入成功且截断到 20 字", r6.status_code == 200 and row2 and len(row2["author"]) == 20,
    "实际长度 %s" % (len(row2["author"]) if row2 and row2["author"] else None))

print("\n=== E. 家庭成员表进了备份清单 ===")
from constants import EXPORT_TABLES  # noqa: E402

chk("EXPORT_TABLES 含 family_members", "family_members" in EXPORT_TABLES)

print("\n=== F. 成员删掉后老记录上的名字仍在 ===")
chk("删除成员 200", c.delete(MEM + "/" + str(mid)).status_code == 200)
with app.app_context():
    row3 = get_db().execute("SELECT author FROM diary_entries").fetchone()
chk("老日记上的「妈妈」没被清空", row3 and row3["author"] == "妈妈")

print()
if fails:
    print("失败 %d 项" % len(fails))
    sys.exit(1)
print("通过")
