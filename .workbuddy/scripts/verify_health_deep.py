#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
健康档案「深度」体检：比 verify_health_archive.py 更进一步。

verify_health_archive.py 只保证「接口不崩、概览算得出来」；
这个脚本专测真正会坑到用户的东西：

  A. 六类资源的 CRUD 全链路（建 → 查得回来 → 改一个字段别的不能丢 → 真删掉）
  B. 筛选参数是不是真生效（前端下拉框点了有没有反应）
  C. 统计口径（趋势/百分位/增速/时间线）
  D. 边界与脏值（空串、超长、负数值）
  E. 跨宝宝隔离（A 宝宝不该看到 B 宝宝的数据）

不启服务、不碰真实库。跑法：
  C:\\Users\\X\\.workbuddy\\binaries\\python\\envs\\babycare\\Scripts\\python.exe .workbuddy/scripts/verify_health_deep.py
"""

import os
import sys
import tempfile
import datetime
import sqlite3

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BACKEND = os.path.join(ROOT, "app", "backend")
sys.path.insert(0, BACKEND)

TMP_DIR = tempfile.mkdtemp(prefix="babycare_health_deep_")
TMP_DB = os.path.join(TMP_DIR, "babycare.db")
os.environ["BABYCARE_DATA_DIR"] = TMP_DIR
os.environ["BABYCARE_DB_PATH"] = TMP_DB

_fcntl = type(sys)("fcntl")
for _n in ("LOCK_EX", "LOCK_SH", "LOCK_NB", "LOCK_UN"):
    setattr(_fcntl, _n, 0)
_fcntl.flock = lambda *a, **k: None
_fcntl.lockf = lambda *a, **k: None
sys.modules["fcntl"] = _fcntl

import server  # noqa: E402

_passed = 0
_failed = 0
_failures = []


def check(name, cond, extra=""):
    global _passed, _failed
    if cond:
        _passed += 1
        print("  [OK]   " + name)
    else:
        _failed += 1
        _failures.append(name)
        print("  [FAIL] " + name + (("  -> " + str(extra)) if extra else ""))


def section(t):
    print("\n=== " + t + " ===")


print("\n=== 0. 建库 ===")
server.init_db()
try:
    server._run_migrations()
except Exception as e:
    print("  [警告] 迁移抛异常（继续）：%s" % e)
check("临时库建好了", os.path.exists(TMP_DB))

TODAY = datetime.date.today().strftime("%Y-%m-%d")
D1 = (datetime.date.today() - datetime.timedelta(days=60)).strftime("%Y-%m-%d")
D2 = (datetime.date.today() - datetime.timedelta(days=30)).strftime("%Y-%m-%d")
BIRTH = (datetime.date.today() - datetime.timedelta(days=400)).strftime("%Y-%m-%d")

conn = sqlite3.connect(TMP_DB)
conn.execute("INSERT INTO babies (id, name, birthday, gender) VALUES (1, '甲', ?, 'boy')", (BIRTH,))
conn.execute("INSERT INTO babies (id, name, birthday, gender) VALUES (2, '乙', ?, 'girl')", (BIRTH,))
# 生长记录：三个时间点，用来测趋势与增速
for d, w, h in ((D1, 7.0, 64.0), (D2, 7.8, 66.5), (TODAY, 8.4, 68.0)):
    conn.execute(
        "INSERT INTO growth_records (baby_id, record_date, weight, height) VALUES (1, ?, ?, ?)",
        (d, w, h))
conn.commit()
conn.close()

app = server.app
app.config["TESTING"] = True
client = app.test_client()
BABY, OTHER = 1, 2

with client.session_transaction() as sess:
    sess["authenticated"] = True


def jpost(url, payload):
    return client.post(url, json=payload)


def jput(url, payload):
    return client.put(url, json=payload)


def data_of(resp):
    body = resp.get_json()
    return (body or {}).get("data") or []


# ---------------------------------------------------------------- A. CRUD
section("A1. 体检记录：建 → 查 → 部分改 → 删")
r = jpost(f"/api/health/records/{BABY}", {
    "record_date": TODAY, "title": "一岁体检", "record_type": "routine",
    "height": 75.5, "weight": 9.6, "doctor": "王医生", "note": "一切正常"})
check("POST 成功", r.get_json().get("success"), r.get_json())
new_id = (r.get_json() or {}).get("id")
check("POST 返回了 id（前端要靠它刷新）", bool(new_id), new_id)

recs = data_of(client.get(f"/api/health/records/{BABY}"))
mine = [x for x in recs if x.get("title") == "一岁体检"]
check("GET 能查回新建的记录", len(mine) == 1, [x.get("title") for x in recs])
if mine:
    m = mine[0]
    check("身高存对了（没被转成字符串/截断）", abs(float(m.get("height") or 0) - 75.5) < 0.01, m.get("height"))
    check("体重存对了", abs(float(m.get("weight") or 0) - 9.6) < 0.01, m.get("weight"))
    rid = m.get("id")
    u = jput(f"/api/health/records/detail/{rid}", {"note": "只改备注"})
    check("PUT 成功", u.get_json().get("success"))
    after = [x for x in data_of(client.get(f"/api/health/records/{BABY}")) if x.get("id") == rid]
    if after:
        a = after[0]
        check("备注已更新", a.get("note") == "只改备注", a.get("note"))
        check("身高没被抹掉", abs(float(a.get("height") or 0) - 75.5) < 0.01, a.get("height"))
        check("医生没被抹掉", a.get("doctor") == "王医生", a.get("doctor"))
    d = client.delete(f"/api/health/records/detail/{rid}")
    check("DELETE 成功", d.get_json().get("success"))
    gone = [x for x in data_of(client.get(f"/api/health/records/{BABY}")) if x.get("id") == rid]
    check("删完真的查不到了", len(gone) == 0)

section("A2. 提醒：建 → 查 → 标记完成 → 删")
r = jpost(f"/api/health/reminders/{BABY}", {
    "title": "补维生素D", "reminder_type": "medication", "due_date": TODAY})
check("POST 成功", r.get_json().get("success"), r.get_json())
rem = [x for x in data_of(client.get(f"/api/health/reminders/{BABY}")) if x.get("title") == "补维生素D"]
check("GET 能查回", len(rem) == 1)
if rem:
    rid = rem[0]["id"]
    u = jput(f"/api/health/reminders/detail/{rid}", {"is_completed": True})
    check("标记完成成功", u.get_json().get("success"))
    allrem = data_of(client.get(f"/api/health/reminders/{BABY}?completed=true"))
    done = [x for x in allrem if x.get("id") == rid]
    check("完成状态已落库", done and done[0].get("is_completed") in (1, True), done)
    check("完成日期已写入", done and bool(done[0].get("completed_date")), done and done[0].get("completed_date"))
    client.delete(f"/api/health/reminders/detail/{rid}")

section("A3. 里程碑：建 → 查 → 改 → 删")
r = jpost(f"/api/health/milestones/{BABY}", {
    "milestone_name": "独站", "category": "motor", "status": "pending"})
check("POST 成功", r.get_json().get("success"), r.get_json())
ms = [x for x in data_of(client.get(f"/api/health/milestones/{BABY}")) if x.get("milestone_name") == "独站"]
check("GET 能查回", len(ms) == 1)
if ms:
    mid = ms[0]["id"]
    jput(f"/api/health/milestones/detail/{mid}", {"status": "achieved", "achieved_date": TODAY})
    now = [x for x in data_of(client.get(f"/api/health/milestones/{BABY}")) if x.get("id") == mid]
    check("状态改为已达成", now and now[0].get("status") == "achieved", now and now[0].get("status"))
    check("达成日期已写入", now and now[0].get("achieved_date") == TODAY, now and now[0].get("achieved_date"))
    check("分类没被抹掉", now and now[0].get("category") == "motor", now and now[0].get("category"))
    client.delete(f"/api/health/milestones/detail/{mid}")

section("A4. 筛查：建 → 查 → 改 → 删")
r = jpost(f"/api/health/screenings/{BABY}", {
    "screening_type": "vision_筛查", "screening_date": TODAY,
    "result_status": "normal", "hospital": "市妇幼"})
check("POST 成功", r.get_json().get("success"), r.get_json())
sc = data_of(client.get(f"/api/health/screenings/{BABY}"))
check("GET 能查回", len(sc) >= 1)
if sc:
    sid = sc[-1]["id"]
    jput(f"/api/health/screenings/detail/{sid}", {"result_summary": "双眼正常"})
    now = [x for x in data_of(client.get(f"/api/health/screenings/{BABY}")) if x.get("id") == sid]
    check("结论已更新", now and now[0].get("result_summary") == "双眼正常")
    check("医院没被抹掉", now and now[0].get("hospital") == "市妇幼", now and now[0].get("hospital"))
    client.delete(f"/api/health/screenings/detail/{sid}")

section("A5. 过敏：建 → 查 → 改 → 删")
r = jpost(f"/api/health/allergies/{BABY}", {
    "allergen_name": "花生", "allergen_type": "food",
    "severity_level": "severe", "status": "active"})
check("POST 成功", r.get_json().get("success"), r.get_json())
al = [x for x in data_of(client.get(f"/api/health/allergies/{BABY}")) if x.get("allergen_name") == "花生"]
check("GET 能查回", len(al) == 1)
if al:
    aid = al[0]["id"]
    jput(f"/api/health/allergies/detail/{aid}", {"status": "resolved"})
    now = [x for x in data_of(client.get(f"/api/health/allergies/{BABY}?status=resolved")) if x.get("id") == aid]
    check("按 status 能筛到已缓解的", len(now) == 1, len(now))
    check("名称没被抹掉", now and now[0].get("allergen_name") == "花生")
    client.delete(f"/api/health/allergies/detail/{aid}")

section("A6. 喂养摘要：建 → 查 → 改 → 删")
r = jpost(f"/api/health/feeding_summary/{BABY}", {
    "record_month": TODAY[:7], "feeding_type": "mixed",
    "avg_daily_ml": 800, "feeding_frequency": 7})
check("POST 成功", r.get_json().get("success"), r.get_json())
fs = data_of(client.get(f"/api/health/feeding_summary/{BABY}"))
check("GET 能查回", len(fs) >= 1)
if fs:
    fid = fs[0]["id"]
    jput(f"/api/health/feeding_summary/detail/{fid}", {"avg_daily_ml": 850})
    now = [x for x in data_of(client.get(f"/api/health/feeding_summary/{BABY}")) if x.get("id") == fid]
    check("奶量已更新", now and now[0].get("avg_daily_ml") == 850, now and now[0].get("avg_daily_ml"))
    check("次数没被抹掉", now and now[0].get("feeding_frequency") == 7, now and now[0].get("feeding_frequency"))
    client.delete(f"/api/health/feeding_summary/detail/{fid}")

# ---------------------------------------------------------------- B. 筛选
section("B. 筛选参数是不是真生效")
jpost(f"/api/health/allergies/{BABY}", {"allergen_name": "尘螨", "allergen_type": "environmental"})
food = data_of(client.get(f"/api/health/allergies/{BABY}?type=food"))
env = data_of(client.get(f"/api/health/allergies/{BABY}?type=environmental"))
check("type=food 只回食物类", all(x.get("allergen_type") == "food" for x in food), [x.get("allergen_type") for x in food])
check("type=environmental 只回环境类",
      all(x.get("allergen_type") == "environmental" for x in env), [x.get("allergen_type") for x in env])
check("两个筛选结果不重叠", not ({x["id"] for x in food} & {x["id"] for x in env}))

jpost(f"/api/health/reminders/{BABY}", {"title": "已完成的提醒", "due_date": TODAY})
pend = data_of(client.get(f"/api/health/reminders/{BABY}"))
jput(f"/api/health/reminders/detail/{pend[0]['id']}", {"is_completed": True})
only_pending = data_of(client.get(f"/api/health/reminders/{BABY}"))
with_done = data_of(client.get(f"/api/health/reminders/{BABY}?completed=true"))
check("默认不返回已完成的提醒",
      all(not x.get("is_completed") for x in only_pending), [x.get("title") for x in only_pending])
check("completed=true 时才带出已完成的", len(with_done) >= len(only_pending), (len(with_done), len(only_pending)))

# ---------------------------------------------------------------- C. 统计口径
section("C. 统计口径")
# 时间线吃的是「体检+疫苗+里程碑+筛查」，前面几段为了测删除都清掉了，
# 这里补一条不删的，否则时间线必然为空，测不出真东西。
jpost(f"/api/health/records/{BABY}", {
    "record_date": D2, "title": "半岁体检", "record_type": "routine",
    "height": 67.0, "weight": 7.8})
tr = client.get(f"/api/health/trend/{BABY}")
check("趋势接口不 5xx", tr.status_code < 500, tr.status_code)
tb = tr.get_json() or {}
trd = tb.get("data") or tb.get("trend") or []
if isinstance(trd, list) and trd:
    dates = [str(x.get("record_date") or x.get("date") or "") for x in trd]
    check("趋势按时间升序（画折线图不能是乱序）", dates == sorted(dates), dates)
    check("趋势点数 >= 3（三个生长记录）", len(trd) >= 3, len(trd))
else:
    check("趋势返回了数据", False, tb)

pc = client.get(f"/api/health/growth/percentile/{BABY}")
pb = pc.get_json() or {}
check("百分位接口不 5xx", pc.status_code < 500, pc.status_code)
if pb.get("success"):
    def walk_num(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if isinstance(v, (int, float)) and "percentile" in k.lower():
                    yield k, v
                yield from walk_num(v)
        elif isinstance(o, list):
            for v in o:
                yield from walk_num(v)
    bad = [(k, v) for k, v in walk_num(pb) if v < 0 or v > 100]
    check("百分位都落在 0~100", not bad, bad)
else:
    check("百分位接口返回 success", False, pb)

vc = client.get(f"/api/health/growth/velocity/{BABY}")
check("增速接口不 5xx", vc.status_code < 500, vc.status_code)

tl = client.get(f"/api/health/timeline/{BABY}")
check("时间线接口不 5xx", tl.status_code < 500, tl.status_code)
tlb = tl.get_json() or {}
tld = tlb.get("data") or tlb.get("timeline") or []
if isinstance(tld, list) and tld:
    dts = [str(x.get("date") or x.get("record_date") or "") for x in tld]
    check("时间线按日期降序（最近在前）", dts == sorted(dts, reverse=True), dts)
else:
    check("时间线返回了数据", False, tlb)

# ---------------------------------------------------------------- D. 脏值
section("D. 边界与脏值")
cases = [
    ("体检缺标题", f"/api/health/records/{BABY}", {"record_date": TODAY}),
    ("体检缺日期", f"/api/health/records/{BABY}", {"title": "没日期"}),
    ("过敏缺名称", f"/api/health/allergies/{BABY}", {"allergen_type": "food"}),
    ("提醒缺标题", f"/api/health/reminders/{BABY}", {"reminder_type": "custom"}),
    ("里程碑缺名称", f"/api/health/milestones/{BABY}", {"category": "motor"}),
    ("筛查缺类型", f"/api/health/screenings/{BABY}", {"screening_date": TODAY}),
]
for name, url, payload in cases:
    rr = jpost(url, payload)
    body = rr.get_json() or {}
    check(f"{name} → 400 且不落库", rr.status_code == 400 and not body.get("success"),
          (rr.status_code, body.get("success")))

rr = jpost(f"/api/health/records/{BABY}", {"record_date": TODAY, "title": "超长" * 200, "height": -5})
check("超长标题 + 负身高不 5xx", rr.status_code < 500, rr.status_code)

# ---------------------------------------------------------------- E. 跨宝宝
section("E. 跨宝宝隔离")
jpost(f"/api/health/allergies/{BABY}", {"allergen_name": "甲的过敏", "allergen_type": "food"})
other = data_of(client.get(f"/api/health/allergies/{OTHER}"))
check("乙宝宝看不到甲的过敏记录",
      all(x.get("allergen_name") != "甲的过敏" for x in other), [x.get("allergen_name") for x in other])
jpost(f"/api/health/records/{OTHER}", {"record_date": TODAY, "title": "乙的体检"})
mine = data_of(client.get(f"/api/health/records/{BABY}"))
check("甲宝宝看不到乙的体检记录",
      all(x.get("title") != "乙的体检" for x in mine), [x.get("title") for x in mine])

print("\n" + "=" * 46)
print("通过 %d 项，失败 %d 项" % (_passed, _failed))
if _failures:
    print("失败项：")
    for f in _failures:
        print("  - " + f)
print("=" * 46)
sys.exit(1 if _failed else 0)
