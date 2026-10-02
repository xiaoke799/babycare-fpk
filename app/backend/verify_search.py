#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证全局搜索接口。

判据：
  A. 日记正文能搜到（这是最常被找的东西）
  B. 不止日记：里程碑、用药、过敏、体检各自能命中
  C. snippet 里带着关键字（前端直接展示，不能是整条长文）
  D. 通配符安全：搜 "%" 不该命中全部记录（LIKE 的 % 被转义）
  E. 边界：空关键字 400、宝宝不存在 404、搜不到返回空列表不报错
  F. 结果按时间倒序
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

tmpdir = tempfile.mkdtemp(prefix="babycare_srch_")
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
        ("搜索测试宝", "2022-05-01", "other"),
    )
    db.commit()
    baby_id = cur.lastrowid

with app.app_context():
    db = get_db()
    # 日记：正文里埋关键字（标题不含）
    db.execute(
        "INSERT INTO diary_entries (baby_id, title, content, entry_date) VALUES (?,?,?,?)",
        (baby_id, "普通的一天", "今天带宝宝去公园玩，第一次坐了秋千。", "2025-04-02"),
    )
    # 里程碑
    db.execute(
        "INSERT INTO milestones (baby_id, title, description, achieved_date, is_first) VALUES (?,?,?,?,?)",
        (baby_id, "学会走路", "自己走了五步", "2024-03-10", 1),
    )
    # 用药
    db.execute(
        "INSERT INTO medication_records (baby_id, medication_name, dosage, measure_time, note) "
        "VALUES (?,?,?,?,?)",
        (baby_id, "布洛芬混悬液", "5ml", "2025-06-01 20:00:00", "发烧 38.5 度"),
    )
    # 过敏
    db.execute(
        "INSERT INTO allergy_history (baby_id, allergen_type, allergen_name, reaction_detail, "
        "first_occurrence_date, status) VALUES (?,?,?,?,?,?)",
        (baby_id, "food", "鸡蛋", "起红疹", "2025-02-11", "active"),
    )
    # 体检
    db.execute(
        "INSERT INTO checkup_records (baby_id, checkup_date, hospital, doctor, result, advice, note) "
        "VALUES (?,?,?,?,?,?,?)",
        (baby_id, "2025-08-08", "市妇幼", "张医生", "发育正常", "多晒太阳", "下次三个月后复查"),
    )
    # 一条含字面 % 的记录，用于验通配符转义
    db.execute(
        "INSERT INTO diary_entries (baby_id, title, content, entry_date) VALUES (?,?,?,?)",
        (baby_id, "体检单", "血红蛋白 120%，医生说正常", "2025-09-09"),
    )
    db.commit()

c = app.test_client()
with c.session_transaction() as s:
    s["authenticated"] = True

URL = "/api/babies/%d/search" % baby_id


def search(q):
    r = c.get(URL + "?q=" + q)
    js = r.get_json() or {}
    return r.status_code, js.get("data") or [], js


print("=== A. 日记正文能搜到 ===")
code, data, _ = search("公园")
chk("接口 200", code == 200, "实际 %d" % code)
chk("命中日记", any(d["type"] == "diary" and "公园" in d["snippet"] for d in data),
    "结果 %s" % [(d["type"], d["snippet"][:20]) for d in data])

print("\n=== B. 不止日记 ===")
for kw, want in [("走路", "milestone"), ("布洛芬", "medication"),
                 ("鸡蛋", "allergy"), ("市妇幼", "checkup")]:
    _c, d2, _ = search(kw)
    chk("搜「%s」命中 %s" % (kw, want), any(x["type"] == want for x in d2),
        "实际 %s" % [x["type"] for x in d2])

print("\n=== C. snippet 带关键字且是片段 ===")
_c, d3, _ = search("秋千")
chk("snippet 含关键字", any("秋千" in x["snippet"] for x in d3))
chk("snippet 不是整条长文（<80 字）", all(len(x["snippet"]) < 80 for x in d3),
    "最长 %d 字" % max((len(x["snippet"]) for x in d3), default=0))

print("\n=== D. 通配符安全 ===")
_c, d4, _ = search("%25")      # URL 编码后的 '%'
chk("搜字面 % 只命中含 % 的记录", 0 < len(d4) < 6, "命中 %d 条" % len(d4))
chk("命中内容确实含 %", all("%" in (x["snippet"] + x["title"]) for x in d4))

print("\n=== E. 边界 ===")
chk("空关键字 400", c.get(URL + "?q=").status_code == 400)
chk("宝宝不存在 404", c.get("/api/babies/999999/search?q=x").status_code == 404)
_c5, d5, _ = search("绝对不存在的关键字xyz")
chk("搜不到返回空列表不报错", _c5 == 200 and d5 == [])

print("\n=== F. 时间倒序 ===")
_c6, d6, _ = search("的")
times = [x["time"] for x in d6 if x["time"]]
chk("结果按时间倒序", times == sorted(times, reverse=True), "%s" % times[:5])

print()
if fails:
    print("失败 %d 项" % len(fails))
    sys.exit(1)
print("通过")
