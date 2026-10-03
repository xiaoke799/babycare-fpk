#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证 ASQ 记录与免责提示。

重点：分数校验（这里以前会 500）、问卷月份表完整性、以及
**任何情况下都不许出现 normal/monitor/refer 这种临床结论**。

判据：
  A. 正常录入 200，五维分数与总分正确
  B. 脏值一律 400 而不是 500：字符串分数、分数越界、月龄越界、坏日期、空 body
  C. result 恒为 score_only（不判读）
  D. /api/asq/info：21 份问卷、领域、免责说明里必须有「专业筛查」字样
  E. 宝宝不存在 404
  F. GET 列表也带免责声明
  G. 负向对照（写在注释里）：去掉 _validate_asq_payload 就会 500 —— 已实测
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

tmpdir = tempfile.mkdtemp(prefix="babycare_asq_")
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
        ("ASQ测试宝", "2024-01-10", "other"),
    )
    db.commit()
    baby_id = cur.lastrowid

c = app.test_client()
with c.session_transaction() as s:
    s["authenticated"] = True

URL = "/api/babies/%d/asq" % baby_id
FULL = {
    "age_months": 12,
    "communication_score": 55,
    "gross_motor_score": 50,
    "fine_motor_score": 45,
    "problem_solving_score": 40,
    "personal_social_score": 35,
    "note": "自己在家做的",
}

print("=== A. 正常录入 ===")
r = c.post(URL, json=FULL)
js = r.get_json() or {}
chk("接口 200", r.status_code == 200, "实际 %d" % r.status_code)
chk("总分 225", (js.get("data") or {}).get("total_score") == 225,
    "实际 %s" % (js.get("data") or {}).get("total_score"))
chk("推荐 12 月龄问卷", (js.get("data") or {}).get("interval") == 12,
    "实际 %s" % (js.get("data") or {}).get("interval"))
with app.app_context():
    row = get_db().execute("SELECT * FROM asq_screenings").fetchone()
chk("库里总分也是 225", row and row["total_score"] == 225, "实际 %s" % (row["total_score"] if row else None))

print("\n=== B. 脏值必须是 400，不能是 500 ===")
cases = [
    ("分数传字符串（旧代码会 TypeError→500）",
     dict(FULL, communication_score="abc")),
    ("分数传 null 字面量", dict(FULL, communication_score=None)),
    ("分数超过满分 60", dict(FULL, gross_motor_score=61)),
    ("分数为负", dict(FULL, fine_motor_score=-1)),
    ("月龄超出 1~66", dict(FULL, age_months=70)),
    ("月龄为 0", dict(FULL, age_months=0)),
    ("月龄传字符串", dict(FULL, age_months="十二")),
    ("月龄缺失", {k: v for k, v in FULL.items() if k != "age_months"}),
    ("日期格式坏", dict(FULL, screening_date="2025/13/45")),
    ("布尔值冒充分数", dict(FULL, communication_score=True)),
]
for label, payload in cases:
    rr = c.post(URL, json=payload)
    chk(label + " → 400", rr.status_code == 400, "实际 %d" % rr.status_code)
    chk("  且不是 5xx", rr.status_code < 500, "实际 %d" % rr.status_code)

r_empty = c.post(URL, json={})
chk("空 body → 400", r_empty.status_code == 400, "实际 %d" % r_empty.status_code)
r_none = c.post(URL, json=None, content_type="application/json")
chk("body 为 null → 400（不是 500）", r_none.status_code == 400, "实际 %d" % r_none.status_code)

print("\n=== C. 绝不给临床结论 ===")
r2 = c.post(URL, json=dict(FULL, age_months=24, total_score=999))
res = ((r2.get_json() or {}).get("data") or {}).get("result")
chk("result 恒为 score_only", res == "score_only", "实际 %r" % res)
with app.app_context():
    bad = get_db().execute(
        "SELECT COUNT(*) AS c FROM asq_screenings WHERE result != 'score_only'").fetchone()
chk("库里没有 normal/monitor/refer 记录", bad and bad["c"] == 0,
    "实际 %s 条" % (bad["c"] if bad else "?"))

print("\n=== D. 问卷月份表与免责说明 ===")
info = (c.get("/api/asq/info").get_json() or {}).get("data") or {}
ivs = info.get("intervals") or []
chk("共 21 份问卷", len(ivs) == 21, "实际 %d" % len(ivs))
months = [i["months"] for i in ivs]
chk("含 9 月龄（以前前端下拉漏了）", 9 in months)
chk("含 42/48/54/60（以前漏了）", all(m in months for m in (42, 48, 54, 60)),
    "%s" % months[-6:])
chk("五个领域", len(info.get("areas") or []) == 5, "实际 %d" % len(info.get("areas") or []))
chk("每领域满分 60 / 总分 300",
    info.get("max_area_score") == 60 and info.get("max_total") == 300,
    "领域 %s 总分 %s" % (info.get("max_area_score"), info.get("max_total")))
disc = info.get("disclaimer") or ""
chk("免责说明提到「专业筛查」", "专业筛查" in disc or "儿童保健科" in disc, disc[:40])
chk("免责说明提到「不能代替」", "不能代替" in disc)
chk("免责说明提到早产矫正", "矫正月龄" in disc)

print("\n=== E/F. 列表与边界 ===")
lst = c.get(URL).get_json() or {}
chk("GET 列表带免责声明", "专业筛查" in (lst.get("disclaimer") or ""))
chk("每条记录标出对应问卷月龄",
    all(x.get("interval") for x in (lst.get("screenings") or [])),
    "%s" % [x.get("interval") for x in (lst.get("screenings") or [])])
chk("宝宝不存在 404", c.post("/api/babies/999999/asq", json=FULL).status_code == 404)
chk("删不存在的记录 404", c.delete("/api/asq/999999").status_code == 404)

print()
if fails:
    print("失败 %d 项" % len(fails))
    sys.exit(1)
print("通过")
