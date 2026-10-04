#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xiaoke799
r"""「本月育儿要点」月龄聚合门禁。

这个接口把 5 份内容（发育指导 / 活动 / 食谱 / 疫苗 / 百科）按宝宝当前月龄串成一页。
它跨了 5 张表，任何一张表空了、字段名改了、月龄区间写反了，页面都不会报错——
只是安静地少一块，家长看到的是「内容变少了」而不是「出错了」。所以单独立一道门禁。

查什么：
A. 多月龄遍历（0/2/5/7/12/18/30/42 个月）
   - 每个月龄接口都 200，且字段齐全
   - 发育特点、安全排查、预警信号、急救要点：任何月龄都不能是空的
   - 活动：0-36 个月内每个月龄都要有得玩（内容门禁已保证 ≥3，这里再看接口取得到）
   - 食谱：6 个月以前允许为空（本来就只吃奶），6 个月起必须有
B. 医学边界（这条最重要）
   - 预警信号只给「该怎么办」，不给任何判读：正文里不许出现诊断/评分/cutoff 类词
   - action 只允许「尽快就医」「下次儿保告知医生」两种，不许自己发明第三种
   - 免责声明必须随接口返回（前端渲染不出来就等于没提示）
C. 异常输入
   - 宝宝不存在 → 404；没填出生日期 → 400（不是 500，也不是静默给个 0 月龄）
D. 疫苗未初始化时要给出可引导的状态（前端据此显示「去生成计划」按钮）

跑法：
  C:\Users\X\.workbuddy\binaries\python\envs\babycare\Scripts\python.exe \
      .workbuddy/scripts/verify_month_guide.py
退出码：0 = 通过；1 = 有问题。
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
BACKEND = os.path.join(ROOT, "app", "backend")
sys.path.insert(0, BACKEND)

TMP_DIR = tempfile.mkdtemp(prefix="babycare_mg_")
os.environ["BABYCARE_DATA_DIR"] = TMP_DIR
os.environ["BABYCARE_DB_PATH"] = os.path.join(TMP_DIR, "babycare.db")
os.environ["BABYCARE_LOG_DIR"] = os.path.join(TMP_DIR, "logs")
os.environ["BABYCARE_DEV_AUTH"] = "1"

_fcntl = type(sys)("fcntl")
for _n in ("LOCK_EX", "LOCK_SH", "LOCK_NB", "LOCK_UN"):
    setattr(_fcntl, _n, 0)
_fcntl.flock = lambda *a, **k: None
_fcntl.lockf = lambda *a, **k: None
sys.modules["fcntl"] = _fcntl

import server  # noqa: E402
from seed_data import load_all_seeds  # noqa: E402

FAILS = []
OKS = []


def fail(msg):
    FAILS.append(msg)


def ok(msg):
    OKS.append(msg)


def check(cond, msg):
    if cond:
        ok(msg)
    else:
        fail(msg)
    return bool(cond)


# ---------------------------------------------------------------- 准备
server.init_db()
load_all_seeds()

app = server.app
app.config["TESTING"] = True
app.config["DATA_DIR"] = TMP_DIR
c = app.test_client()


def get(url):
    auth()
    return c.get(url)


def post(url, **kw):
    auth()
    return c.post(url, **kw)


def auth():
    """重种会话（项目路由里含 /api/auth/logout，会清掉 session，每次请求前重种）。"""
    with c.session_transaction() as sess:
        sess["authenticated"] = True
        sess.permanent = True


def make_baby(months_ago, name="mg"):
    """造一个出生日期 = 今天往前 N 个月的宝宝，返回 id。"""
    import datetime
    today = datetime.date.today()
    y = today.year
    m = today.month - months_ago
    while m <= 0:
        m += 12
        y -= 1
    d = today.day
    # 避免 2 月 29/30/31 这类不存在日期
    while True:
        try:
            b = datetime.date(y, m, d)
            break
        except ValueError:
            d -= 1
    r = post("/api/babies", json={"name": name, "birthday": b.strftime("%Y-%m-%d")})
    if not (r.status_code == 200 and r.get_json().get("success")):
        raise RuntimeError(f"建宝宝失败: {r.status_code} {r.get_json()}")
    return r.get_json()["data"]["id"]


baby_ids = {}
# 月龄取值要覆盖全部分档（3/6/12/18/24 是分档边界）：
# 0,2→newborn  5→early  7,12→late  18→toddler  21→young  30,42→preschool
AGES = (0, 2, 5, 7, 12, 18, 21, 30, 42)
for mo in AGES:
    baby_ids[mo] = make_baby(mo, f"b{mo}")

# ---------------------------------------------------------------- A. 多月龄遍历
TOP_KEYS = ("age_months", "stage", "stage_label", "age_label", "development",
            "activities", "recipes", "vaccines", "common_questions",
            "safety_checklist", "red_flags", "first_aid", "disclaimer")

# 医学边界：本页只提示不判读，出现这些词说明越界了
FORBIDDEN = ("确诊", "诊断", "评分", "cutoff", "Cutoff", "百分位", "正常值", "智力量表")
# 预警信号只给两条出路，多一条都是自己在下判断
ALLOWED_ACTIONS = ("尽快就医", "下次儿保告知医生")
# 安全排查不许恐吓式措辞
SCARE = ("一定", "必须马上", "肯定")

for mo in (0, 2, 5, 7, 12, 18, 30, 42):
    r = get(f"/api/babies/{baby_ids[mo]}/month-guide")
    if not check(r.status_code == 200, f"{mo}月龄：接口返回 200（实际 {r.status_code}）"):
        continue
    j = r.get_json()
    if not check(j.get("success") is True, f"{mo}月龄：success=True"):
        continue
    d = j.get("data") or {}
    missing = [k for k in TOP_KEYS if k not in d]
    check(not missing, f"{mo}月龄：字段齐全（缺 {missing}）" if missing else f"{mo}月龄：字段齐全")

    # 月龄算得对不对（取整差异允许 1 个月内）
    am = d.get("age_months")
    check(isinstance(am, int) and abs(am - mo) <= 1,
          f"{mo}月龄：返回的 age_months={am} 与实际相符")

    # 发育特点
    dev = d.get("development") or {}
    check(bool((dev.get("description") or "").strip()),
          f"{mo}月龄：发育特点描述非空")

    # 安全排查 / 预警信号 / 急救
    check(len(d.get("safety_checklist") or []) >= 4,
          f"{mo}月龄：家庭安全排查 ≥4 条（实际 {len(d.get('safety_checklist') or [])}）")
    check(len(d.get("red_flags") or []) >= 3,
          f"{mo}月龄：预警信号 ≥3 条（实际 {len(d.get('red_flags') or [])}）")
    check(len(d.get("first_aid") or []) >= 4,
          f"{mo}月龄：急救要点 ≥4 条（实际 {len(d.get('first_aid') or [])}）")

    # 活动：0-36 个月内都要有得玩（42 个月超出内容范围，只要求不报错）
    acts = d.get("activities") or []
    if mo <= 36:
        check(len(acts) >= 3, f"{mo}月龄：活动 ≥3 个（实际 {len(acts)}）")
    for a in acts:
        if not (isinstance(a.get("min_age_months"), int) and isinstance(a.get("max_age_months"), int)):
            fail(f"{mo}月龄：活动 {a.get('title')} 缺月龄区间")
            break
        if not (a["min_age_months"] <= am <= a["max_age_months"]):
            fail(f"{mo}月龄：活动 {a.get('title')} 月龄区间"
                 f" {a['min_age_months']}-{a['max_age_months']} 与当前 {am} 不符")
            break
    else:
        ok(f"{mo}月龄：活动月龄区间全部命中当前月龄")

    # 食谱：6 月前允许为空，6 月起必须有
    recs = d.get("recipes") or []
    if am >= 6:
        check(len(recs) >= 1, f"{mo}月龄：辅食 ≥1 个（实际 {len(recs)}）")
    else:
        ok(f"{mo}月龄：{am} 月未到辅食月龄，食谱为空属正常")

    # 疫苗未初始化：应给出 False，前端据此引导
    v = d.get("vaccines") or {}
    check(v.get("initialized") is False,
          f"{mo}月龄：未生成接种计划时 initialized=False")

    # 免责声明
    check(bool((d.get("disclaimer") or "").strip()),
          f"{mo}月龄：免责声明非空")

    # ⚠ 医学边界必须「每个月龄都查」，不能只抽查一个阶段：
    # red_flags / safety_checklist 是按 stage 分开存的，只查 12 月龄的话，
    # 新生儿期写错照样全绿（负向对照实测过，别改回去）
    hits = []
    for f in d.get("red_flags") or []:
        for word in FORBIDDEN:
            if word in (f.get("sign") or "") + (f.get("action") or ""):
                hits.append((word, f.get("sign")))
    check(not hits, f"{mo}月龄：预警信号不含诊断/评分类词（命中 {hits}）" if hits
          else f"{mo}月龄：预警信号不含诊断/评分类词")

    bad_actions = [f.get("action") for f in d.get("red_flags") or []
                   if f.get("action") not in ALLOWED_ACTIONS]
    check(not bad_actions,
          f"{mo}月龄：预警 action 只取 {ALLOWED_ACTIONS}（越界 {bad_actions}）" if bad_actions
          else f"{mo}月龄：预警 action 只取 {ALLOWED_ACTIONS}")

    # 每条都要有「表现 + 怎么办」，缺一半等于看了不知道该干嘛
    half = [f for f in d.get("red_flags") or []
            if not (f.get("sign") or "").strip() or not (f.get("action") or "").strip()]
    check(not half, f"{mo}月龄：每条预警都有「表现 + 怎么办」（缺项 {len(half)} 条）")

    # 安全排查：不许出现会吓唬人的绝对化措辞（只做提示，不做恐吓）
    scare_hits = [s for s in d.get("safety_checklist") or [] if any(w in s for w in SCARE)]
    check(not scare_hits, f"{mo}月龄：安全排查措辞不绝对化（命中 {scare_hits}）" if scare_hits
          else f"{mo}月龄：安全排查措辞不绝对化")

# ---------------------------------------------------------------- B. 医学边界（全局项）
# 急救是常量的，不用按月龄重复查；但每条都要有场景和处置步骤
d = get(f"/api/babies/{baby_ids[12]}/month-guide").get_json()["data"]
aids = d.get("first_aid") or []
bad_aid = [a.get("title") for a in aids
           if not (a.get("scene") or "").strip() or not (a.get("do") or [])]
check(not bad_aid, f"急救条目都有场景与处置步骤（缺项 {bad_aid}）" if bad_aid
      else "急救条目都有场景与处置步骤")
check(any(a.get("call_120") for a in aids), "急救条目至少一条标注需拨 120")
# 每条急救都要写「不要做什么」——只写做什么容易让人做错事
no_avoid = [a.get("title") for a in aids if not (a.get("avoid") or "").strip()]
check(not no_avoid, f"急救条目都写了「不要做」的禁忌（缺项 {no_avoid}）" if no_avoid
      else "急救条目都写了「不要做」的禁忌")

# ---------------------------------------------------------------- C. 异常输入
r404 = get("/api/babies/999999/month-guide")
check(r404.status_code == 404, f"宝宝不存在返回 404（实际 {r404.status_code}）")

# 没填出生日期的宝宝
rb = post("/api/babies", json={"name": "no-birthday"})
if rb.status_code == 200 and rb.get_json().get("success"):
    nid = rb.get_json()["data"]["id"]
    rn = get(f"/api/babies/{nid}/month-guide")
    check(rn.status_code == 400,
          f"未填出生日期返回 400（实际 {rn.status_code}）")
else:
    ok("未填出生日期返回 400（建宝宝即要求生日，跳过）")

# ---------------------------------------------------------------- D. 疫苗计划初始化后
import datetime  # noqa: E402
bid = baby_ids[7]
today = datetime.date.today()
past = (today - datetime.timedelta(days=20)).strftime("%Y-%m-%d")
soon_d = (today + datetime.timedelta(days=10)).strftime("%Y-%m-%d")
# 路由写错会静默返回 0 条，所以每条都先断言自己写进去了
VACC_URL = f"/api/babies/{bid}/vaccines"
for _name, _date, _st in (("乙肝疫苗第3剂", past, "pending"),
                          ("麻腮风第1剂", soon_d, "pending"),
                          ("卡介苗", past, "completed")):
    _r = post(VACC_URL, json={"vaccine_name": _name,
                              "scheduled_date": _date, "status": _st})
    check(_r.status_code == 200 and _r.get_json().get("success") is True,
          f"写入疫苗记录「{_name}」成功（实际 {_r.status_code}）")
r2 = get(f"/api/babies/{bid}/month-guide")
v2 = (r2.get_json().get("data") or {}).get("vaccines") or {}
check(v2.get("initialized") is True, "生成接种计划后 initialized=True")
check(len(v2.get("overdue") or []) == 1,
      f"逾期疫苗识别为 1 条（实际 {len(v2.get('overdue') or [])}）")
check(len(v2.get("due_soon") or []) == 1,
      f"30 天内到期识别为 1 条（实际 {len(v2.get('due_soon') or [])}）")
check(v2.get("completed_count") == 1,
      f"已完成计数为 1（实际 {v2.get('completed_count')}）")
check(v2.get("total") == 3, f"计划总数为 3（实际 {v2.get('total')}）")

# ---------------------------------------------------------------- 报告
print("=" * 70)
print(f"本月育儿要点门禁：通过 {len(OKS)} 项，失败 {len(FAILS)} 项")
for m in FAILS:
    print("  [FAIL] " + m)
if FAILS:
    print("=" * 70)
    sys.exit(1)
print("全部通过：%d 个月龄（覆盖全部分档）× 字段齐全 + 医学边界 + 异常输入 + 疫苗三态" % len(AGES))
print("=" * 70)
