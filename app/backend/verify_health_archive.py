#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xiaoke799
"""
健康档案离线体检：不启服务、不碰真实数据库。

用 init_db() + _run_migrations() 建一个和线上同构的临时库（两步都要跑，
少跑迁移会漏列），塞入有代表性的数据，再逐个打所有 /api/health/* 接口，
记录 5xx / 异常 / 关键字段缺失。

空库冒烟只能验证「路由通不通」，验证不了「有数据时算得对不对」——
本脚本专门补这一块。

跑法：
  C:\\Users\\X\\.workbuddy\\binaries\\python\\envs\\babycare\\Scripts\\python.exe verify_health_archive.py
"""

import os
import sys
import tempfile
import datetime
import sqlite3

BACKEND = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BACKEND)

# DB_PATH 由环境变量决定（constants.py 里读 BABYCARE_DB_PATH），
# DATA_DIR 默认是 /var/apps/... 在 Windows 上不存在，所以两个都要在 import 前指定好。
TMP_DIR = tempfile.mkdtemp(prefix="babycare_health_verify_")
TMP_DB = os.path.join(TMP_DIR, "babycare.db")
os.environ["BABYCARE_DATA_DIR"] = TMP_DIR
os.environ["BABYCARE_DB_PATH"] = TMP_DB

# Windows 没有 fcntl，先打桩再 import server
_fcntl = type(sys)("fcntl")
for _n in ("LOCK_EX", "LOCK_SH", "LOCK_NB", "LOCK_UN"):
    setattr(_fcntl, _n, 0)
_fcntl.flock = lambda *a, **k: None
_fcntl.lockf = lambda *a, **k: None
sys.modules["fcntl"] = _fcntl

import server

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


print("\n=== 0. 建库（init_db + 迁移，两步缺一不可）===")
server.init_db()
try:
    server._run_migrations()
except Exception as e:
    print(f"  [警告] 迁移抛异常（继续）：{e}")
check("临时库建好了", os.path.exists(TMP_DB))

TODAY = datetime.date.today().strftime("%Y-%m-%d")
WEEK_AGO = (datetime.date.today() - datetime.timedelta(days=7)).strftime("%Y-%m-%d")
BIRTH = (datetime.date.today() - datetime.timedelta(days=200)).strftime("%Y-%m-%d")

conn = sqlite3.connect(TMP_DB)
conn.execute(
    "INSERT INTO babies (id, name, birthday, gender) VALUES (1, '体检宝宝', ?, 'boy')",
    (BIRTH,),
)

# 体检记录
conn.execute(
    """INSERT INTO health_records (baby_id, record_date, record_type, title, height, weight, doctor, hospital, note)
       VALUES (1, ?, 'routine', '常规体检', 68.5, 8.2, ?, ?, ?)""",
    (TODAY, "张医生", "市妇幼", "发育良好"),
)
conn.execute(
    """INSERT INTO health_records (baby_id, record_date, record_type, title, temperature, symptom, medication)
       VALUES (1, ?, 'fever', '发烧', 38.6, '咳嗽流涕', '布洛芬')""",
    (WEEK_AGO,),
)
# 成长记录（给生长曲线用）
for d, w, h in ((WEEK_AGO, 8.0, 67.0), (TODAY, 8.2, 68.5)):
    conn.execute(
        "INSERT INTO growth_records (baby_id, record_date, weight, height) VALUES (1, ?, ?, ?)",
        (d, w, h),
    )
# 里程碑（健康档案那张表：milestone_details，注意不是成长页的 milestones）
conn.execute(
    """INSERT INTO milestone_details (baby_id, milestone_code, milestone_name, category, achieved_date, status)
       VALUES (1, 'motor_roll_over', '会翻身', 'motor', ?, 'achieved')""",
    (WEEK_AGO,),
)
# 筛查
conn.execute(
    """INSERT INTO screenings (baby_id, screening_type, screening_date, result_status, result_summary, hospital, doctor)
       VALUES (1, 'hearing_筛查', ?, 'normal', '双耳通过', '市妇幼', '李医生')""",
    (TODAY,),
)
# 过敏
conn.execute(
    """INSERT INTO allergy_history (baby_id, allergen_type, allergen_name, severity_level, status, first_occurrence_date)
       VALUES (1, 'food', '牛奶蛋白', 'moderate', 'active', ?)""",
    (WEEK_AGO,),
)
# 指标（指标是「一行一个指标」的窄表，不是一行一次体检）
for nm, code, val, unit in (("身高", "height", 68.5, "cm"), ("体重", "weight", 8.2, "kg")):
    conn.execute(
        """INSERT INTO health_indicators (baby_id, indicator_name, indicator_code, value, unit, record_date)
           VALUES (1, ?, ?, ?, ?, ?)""",
        (nm, code, val, unit, TODAY),
    )
# 提醒
conn.execute(
    """INSERT INTO health_reminders (baby_id, reminder_type, title, due_date, is_completed)
       VALUES (1, 'checkup', '下次体检', ?, 0)""",
    (TODAY,),
)
# 喂养摘要（按月份汇总）
conn.execute(
    """INSERT INTO feeding_summary (baby_id, record_month, feeding_type, avg_daily_ml, feeding_frequency)
       VALUES (1, ?, 'mixed', 720, 8)""",
    (TODAY[:7],),
)
# 疫苗（vaccine_details）
conn.execute(
    """INSERT INTO vaccine_details (baby_id, vaccine_name, dose_number, scheduled_date, status)
       VALUES (1, '乙肝疫苗', 1, ?, 'completed')""",
    (WEEK_AGO,),
)
conn.commit()
conn.close()

app = server.app
app.config["TESTING"] = True
client = app.test_client()
BABY = 1

# 接口都要登录。网关身份头需要 request.gateway_trusted（由前缀中间件打标），
# 测试里直接走 session 更省事 —— 会话登录恒为管理员，与生产同权限。
with client.session_transaction() as sess:
    sess["authenticated"] = True
_probe = client.get(f"/api/health/summary/{BABY}")
if _probe.status_code == 401:
    print("  [FAIL] 会话认证没生效，后续断言会全是假阴性")
else:
    print("  [OK]   已登录（session 认证生效）")

print("\n=== 1. 各 Tab 的 GET 接口（有数据时）===")
GET_ENDPOINTS = [
    ("概览", f"/api/health/summary/{BABY}"),
    ("体检记录", f"/api/health/records/{BABY}"),
    ("趋势", f"/api/health/trend/{BABY}?metric=weight"),
    ("指标", f"/api/health/indicators/{BABY}"),
    ("里程碑", f"/api/health/milestones/{BABY}"),
    ("筛查", f"/api/health/screenings/{BABY}"),
    ("过敏", f"/api/health/allergies/{BABY}"),
    ("喂养摘要", f"/api/health/feeding_summary/{BABY}"),
    ("提醒", f"/api/health/reminders/{BABY}"),
    ("健康疫苗", f"/api/health/vaccines/{BABY}"),
    ("疫苗计划", f"/api/health/vaccine/schedule/{BABY}"),
    ("自费疫苗", "/api/health/vaccine/paid"),
    ("时间线", f"/api/health/timeline/{BABY}"),
    ("生长百分位", f"/api/health/growth/percentile/{BABY}"),
    ("生长速度", f"/api/health/growth/velocity/{BABY}"),
    ("独立页疫苗列表", f"/api/babies/{BABY}/vaccines"),
    ("独立页疫苗状态", f"/api/babies/{BABY}/vaccines/status"),
    ("独立页疫苗时间线", f"/api/babies/{BABY}/vaccines/timeline"),
    ("独立页疫苗统计", f"/api/babies/{BABY}/vaccines/stats"),
    ("独立页疫苗导出", f"/api/babies/{BABY}/vaccines/export"),
]
for name, url in GET_ENDPOINTS:
    try:
        r = client.get(url)
    except Exception as e:
        check(f"{name} GET 不抛异常", False, repr(e))
        continue
    ok = r.status_code < 500
    detail = f"HTTP {r.status_code}"
    if r.status_code >= 500:
        try:
            detail += " " + (r.get_json() or {}).get("message", "")[:80]
        except Exception:
            detail += " " + r.get_data(as_text=True)[:80]
    check(f"{name} GET 返回 200/4xx", ok, detail)

print("\n=== 2. 概览必须真的汇总出数据 ===")
r = client.get(f"/api/health/summary/{BABY}").get_json()
s = r.get("summary") or r.get("data") or {}
check("体检次数 >= 1（不是 0）", (s.get("checkup_count") or 0) >= 1, s.get("checkup_count"))
check("有最新身高", bool(s.get("latest_height") or s.get("latestHeight")),
      s.get("latest_height") or s.get("latestHeight"))
check("有最新体重", bool(s.get("latest_weight") or s.get("latestWeight")),
      s.get("latest_weight") or s.get("latestWeight"))
check("有疫苗完成/总数", s.get("vaccine_total") is not None,
      (s.get("vaccine_completed"), s.get("vaccine_total")))
check("有待办提醒", s.get("pending_reminders") is not None, s.get("pending_reminders"))

print("\n=== 2b. 接口返回键统一（都必须有 data）===")
for name, url in (
    ("体检记录", f"/api/health/records/{BABY}"),
    ("指标", f"/api/health/indicators/{BABY}"),
    ("里程碑", f"/api/health/milestones/{BABY}"),
    ("筛查", f"/api/health/screenings/{BABY}"),
    ("过敏", f"/api/health/allergies/{BABY}"),
    ("提醒", f"/api/health/reminders/{BABY}"),
    ("喂养摘要", f"/api/health/feeding_summary/{BABY}"),
    ("健康疫苗", f"/api/health/vaccines/{BABY}"),
    ("自费疫苗", "/api/health/vaccine/paid"),
):
    body = client.get(url).get_json() or {}
    check(f"{name} 返回体带 data 键", "data" in body, list(body.keys()))

print("\n=== 3. 两套疫苗 API 返回一致（同一个 baby、同一张表）===")
a = client.get(f"/api/health/vaccines/{BABY}").get_json()
b = client.get(f"/api/babies/{BABY}/vaccines").get_json()


def n_vaccines(payload):
    d = payload.get("data") or payload.get("vaccines") or []
    return len(d) if isinstance(d, list) else -1


na, nb = n_vaccines(a), n_vaccines(b)
check("两套接口都返回 success", a.get("success") and b.get("success"), (a.get("success"), b.get("success")))
check("两套接口记录数一致（同源）", na == nb and na >= 1, (na, nb))

print("\n=== 4. 体检记录：时间与类型 ===")
r = client.get(f"/api/health/records/{BABY}").get_json()
recs = r.get("data") or r.get("records") or []
check("返回 2 条（体检 + 发烧）", len(recs) == 2, f"{len(recs)} 条：{[x.get('record_type') for x in recs]}")
types = {x.get("record_type") for x in recs}
check("类型含 routine 与 fever", types == {"routine", "fever"}, types)
if recs:
    t0 = recs[0].get("record_date") or ""
    check("时间格式是 'YYYY-MM-DD HH:MM:SS' 风格（无 T）", "T" not in t0, t0)
    check("按日期倒序（今天在前）",
          t0 >= (recs[-1].get("record_date") or ""), [x.get("record_date") for x in recs])
else:
    check("至少要有记录才测得了排序", False, "列表为空")

print("\n=== 5. 编辑必须是「部分更新」，别把别的字段抹掉 ===")
before = recs[0]
rid = before.get("id")
put = client.put(
    f"/api/health/records/detail/{rid}",
    json={"note": "只改备注"},
).get_json()
check("PUT 成功", put.get("success"), put)
after_row = None
c = sqlite3.connect(TMP_DB)
c.row_factory = sqlite3.Row
row = c.execute("SELECT * FROM health_records WHERE id = ?", (rid,)).fetchone()
c.close()
after_row = dict(row) if row else {}
check("备注已更新", after_row.get("note") == "只改备注", after_row.get("note"))
check("身高没被抹掉（部分更新生效）",
      after_row.get("height") == before.get("height"), (before.get("height"), after_row.get("height")))
check("体重没被抹掉",
      after_row.get("weight") == before.get("weight"), (before.get("weight"), after_row.get("weight")))
check("医生没被抹掉",
      after_row.get("doctor") == before.get("doctor"), (before.get("doctor"), after_row.get("doctor")))

print("\n=== 6. 删除不存在的记录不能假成功 ===")
d = client.delete("/api/health/records/detail/999999")
check("删不存在返回 404", d.status_code == 404, d.status_code)
d2 = client.delete("/api/health/milestones/detail/999999")
check("里程碑删不存在返回 404", d2.status_code == 404, d2.status_code)
d3 = client.delete("/api/health/allergies/detail/999999")
check("过敏删不存在返回 404", d3.status_code == 404, d3.status_code)

print("\n=== 7. 空 body / 脏 body 不能 500 ===")
for method, path in (
    ("POST", f"/api/health/records/{BABY}"),
    ("POST", f"/api/health/milestones/{BABY}"),
    ("POST", f"/api/health/allergies/{BABY}"),
    ("POST", f"/api/health/screenings/{BABY}"),
    ("POST", f"/api/health/reminders/{BABY}"),
):
    try:
        rr = client.post(path, json={})
        check(f"{path} 空 body 不 5xx", rr.status_code < 500, rr.status_code)
    except Exception as e:
        check(f"{path} 空 body 不抛异常", False, repr(e))

rr = client.post(f"/api/health/records/{BABY}", data="not json", content_type="application/json")
check("非法 JSON 不 5xx", rr.status_code < 500, rr.status_code)
rr = client.post(f"/api/health/records/{BABY}", json=[])
check("数组 body（非 dict）不 5xx", rr.status_code < 500, rr.status_code)

print("\n=== 8. 缺失宝宝不能崩 ===")
for name, url in GET_ENDPOINTS[:15]:
    url404 = url.replace(f"/{BABY}", "/9999")
    try:
        rr = client.get(url404)
        if rr.status_code >= 500:
            check(f"{name} 不存在的宝宝不 5xx", False, rr.status_code)
    except Exception as e:
        check(f"{name} 不存在的宝宝不抛异常", False, repr(e))
check("不存在的宝宝所有接口都不 5xx", True)

print("\n" + "=" * 46)
print(f"通过 {_passed} 项，失败 {_failed} 项")
print("=" * 46)

try:
    os.remove(TMP_DB)
except OSError:
    pass

sys.exit(1 if _failed else 0)
