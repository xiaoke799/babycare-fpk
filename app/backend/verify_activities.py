#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""活动游戏模块门禁：分类可达性 + 月龄覆盖 + 参数真生效。

起因（2026-10-05）：活动页有三类「界面看不出异常」的问题，
本脚本把它们变成会红的断言。

1. **分类可达性**——分类按钮原来硬编码在 index.html，于是
   后端有的分类（nature / creative / music）没按钮 ⇒ 用户永远筛不到；
   前端有的按钮（art）后端没数据 ⇒ 点了必然空白。
   修法是按钮改由 /api/activities/categories 动态渲染，所以这里断言
   「种子里的每个分类都能从该接口取到」。
2. **月龄覆盖**——原来全部活动 max_age_months ≤ 36，
   宝宝满3 岁后整页空白、推荐接口回 0 条，而引导体系覆盖到 42 月。
3. **参数真生效**——limit / sort 原来后端根本不认，
   前端写了会被静默忽略（接口 200 但条数/顺序没变）。

跑法：
  C:\\Users\\X\\.workbuddy\\binaries\\python\\envs\\babycare\\Scripts\\python.exe \\
      app/backend/verify_activities.py
"""
import json
import os
import sys
import tempfile
import types

BACKEND = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BACKEND)
SEEDS = os.path.join(BACKEND, "seeds")
FRONTEND = os.path.join(os.path.dirname(BACKEND), "frontend")

TMP = tempfile.mkdtemp(prefix="babycare_act_")
os.environ["BABYCARE_DATA_DIR"] = TMP
os.environ["BABYCARE_DB_PATH"] = os.path.join(TMP, "babycare.db")
os.environ["BABYCARE_LOG_DIR"] = os.path.join(TMP, "logs")
os.environ["BABYCARE_DEV_AUTH"] = "1"

if "fcntl" not in sys.modules:
    m = types.ModuleType("fcntl")
    m.LOCK_EX, m.LOCK_NB, m.LOCK_UN = 2, 4, 8
    m.flock = lambda *a, **k: None
    m.lockf = lambda *a, **k: None
    sys.modules["fcntl"] = m

import server  # noqa: E402

app = server.app
app.config["TESTING"] = True
server.init_db()
client = app.test_client()
with client.session_transaction() as s:
    s["authenticated"] = True

_pass = 0
_fail = 0


def chk(ok, label, extra=""):
    global _pass, _fail
    print(("  [OK]   " if ok else "  [FAIL] ") + label + (f"  {extra}" if extra else ""))
    if ok:
        _pass += 1
    else:
        _fail += 1


def get(url):
    r = client.get(url)
    return r.status_code, ((r.get_json() or {}).get("data"))


print("=== 1. 分类可达性（前端按钮是动态渲染的，源头就是这个接口）===")
seed = json.load(open(os.path.join(SEEDS, "baby_activities.json"), encoding="utf-8"))
seed_cats = {x.get("category") for x in seed if x.get("category")}

code, cats = get("/api/activities/categories")
chk(code == 200, "分类接口回 200", f"实际 {code}")
api_cats = {c["key"] for c in (cats or [])}
chk(api_cats == seed_cats,
    "接口分类与种子分类完全一致",
    f"仅种子有 {sorted(seed_cats - api_cats)}；仅接口有 {sorted(api_cats - seed_cats)}"
    if api_cats != seed_cats else f"{len(api_cats)} 个分类")

# 前端不能再有硬编码的分类按钮（那正是这次缺陷的根源）
html = open(os.path.join(FRONTEND, "index.html"), encoding="utf-8").read()
i = html.find('id="activityCatBar"')
seg = html[i:i + 400] if i >= 0 else ""
chk('data-cat="all"' in seg, "分类条容器存在且只保留「全部」")
chk(not any(f'data-cat="{c}"' in seg for c in seed_cats - {"all"}),
    "index.html 里不再硬编码各分类按钮（避免再次两边走岔）")

print("\n=== 2. 每个分类都能真的筛出东西（不能点了就空白）===")
for c in sorted(seed_cats):
    code, rows = get(f"/api/activities?category={c}")
    chk(code == 200 and rows, f"分类 {c} 可筛出内容", f"{len(rows or [])} 条")

print("\n=== 3. 月龄覆盖（0-42 月，与引导体系口径一致）===")
for months in (0, 6, 12, 18, 24, 30, 36, 40, 42):
    code, rows = get(f"/api/activities?age_months={months}")
    chk(bool(rows), f"{months} 月龄有可选活动", f"{len(rows or [])} 条")
    code2, cats2 = get(f"/api/activities/categories?age_months={months}")
    empty = [c["key"] for c in (cats2 or []) if c["count"] == 0]
    chk(not empty, f"{months} 月龄下没有空分类（否则按钮点了就空白）", f"空的 {empty}")

# 3 岁以上：不能只补到 42 月就完事，种子声称覆盖到 60 月
for months in (48, 60):
    code, rows = get(f"/api/activities?age_months={months}")
    chk(bool(rows), f"{months} 月龄仍有内容（3 岁+ 不空白）", f"{len(rows or [])} 条")

print("\n=== 4. limit / sort 真生效（前端写了后端不认会被静默忽略）===")
code, rows = get("/api/activities?limit=3")
chk(len(rows or []) == 3, "limit=3 真的只回 3 条", f"实际 {len(rows or [])}")
code, rows = get("/api/activities?limit=999999")
chk(len(rows or []) <= 200, "limit 越界被钳到 200", f"实际 {len(rows or [])}")
code, rows = get("/api/activities?sort=popular")
chk(bool(rows), "sort=popular 能返回数据", f"{len(rows or [])} 条")
if rows:
    chk("played_count" in rows[0], "popular 排序带 played_count 字段")
else:
    chk(False, "popular 排序带 played_count 字段", "没数据没法验")

print("\n=== 5. 空态文案（超范围时要说人话）===")
js = open(os.path.join(FRONTEND, "js", "app.js"), encoding="utf-8").read()
chk("renderActivityList(activities, ageMonths)" in js,
    "renderActivityList 收了月龄参数（要区分「超范围」与「筛空了」）")
chk("活动库暂时没有适合" in js, "超范围时给出说明文案（不是干巴巴一句「暂无匹配」）")
chk("fallback=1" in js, "前端兜底请求带 fallback=1")
# 上一轮的兜底写法是 sort=popular / 不传 age_months ⇒ 3 岁用户会拿到
# 0-3 月的「黑白卡追视」。看着有内容，其实完全不适合 —— 比空白更糟。
chk("sort=popular&baby_id=" not in js.split("function renderActivityList")[-1][:2000],
    "兜底不再用 sort=popular（那会给超龄宝宝新生儿内容）")
chk("activityAgeRangeText" in js and "activity-age-gap" in
    open(os.path.join(FRONTEND, "css", "main.css"), encoding="utf-8").read(),
    "兜底卡片标出「适合几岁」（如实告知不是精确匹配）")

print("\n=== 6. 兜底给的是「月龄最近」而不是「排序最前」（2026-10-05 修）===")
# 8 岁（96 月）远超库上限 60
code, rows = get("/api/activities?fallback=1&limit=6&age_months=96")
chk(bool(rows), "超范围时兜底有内容", f"{len(rows or [])} 条")
if rows:
    # 关键：不能给 0-3 月的内容。给的话「看着有内容，其实完全不适合」。
    too_young = [r for r in rows if (r.get("min_age_months") or 0) <= 6]
    chk(not too_young, "兜底不给新生儿内容（3 岁用户拿到黑白卡追视 = 没兜）",
        f"混入 {[r.get('title') for r in too_young]}")
    # 应给距离最近的一档，这里 60 月（5 岁）比 6 月近得多
    nearest = max((r.get("min_age_months") or 0) for r in rows)
    chk(nearest >= 36, "兜底给的是较年长的一档", f"最大起始月龄 {nearest}")
    chk(all("age_gap" in r for r in rows), "每条都带 age_gap（差多少月龄）")
    gaps = {r.get("age_gap") for r in rows}
    chk(gaps == {36}, "age_gap 算对了（96 - 60 = 36）", f"实际 {gaps}")

# 区间内有内容时不该触发兜底
code, rows = get("/api/activities?fallback=1&limit=6&age_months=12")
chk(bool(rows) and "age_gap" not in rows[0],
    "区间内有内容时不触发兜底（不硬塞 age_gap）")

# 不传 fallback 就不兜底（别让其他调用点被动拿到近似内容）
code, rows = get("/api/activities?limit=6&age_months=96")
chk(not rows, "不传 fallback 就不兜底（96 月龄仍为 0 条）", f"实际 {len(rows or [])}")

print()
print(f"通过 {_pass} 项，失败 {_fail} 项")
sys.exit(1 if _fail else 0)
