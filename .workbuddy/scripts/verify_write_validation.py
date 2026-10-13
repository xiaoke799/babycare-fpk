#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""写接口「校验收口」门禁 —— 脏数据必须被拒绝，假成功必须不存在。

为什么需要这个脚本（2026-10-12 全面审计的真实缺陷）：
  审计发现 17 组写入侧缺陷，全部**没有门禁覆盖**：
    P0  疫苗两条 PUT：UPDATE 命中 0 行也回 200「已更新」（假成功），
        且不查记录是否存在；同批 4 条 PUT 同样假成功。
    P0  两个批量入口（统一 batch-records + legacy diaper/batch）校验系统性
        松于单条路由：尿布颜色 "purple"、奶量 -999、note 十万字、
        sleep 时长 "abc" 全部 200 入库，而单条路由同参数必 400。
    P0  用药 next_dose_interval 无上限：99999999 让 timedelta 溢出年份
        >9999，用户手误被伪装成 500 服务端故障。
    P1  growth/vaccine/baby 生日的日期不过 validate_date，「8月20号」
        原样入库，之后 clinic-summary 每月 strptime 500。
    P1  体检记录数值零校验，且把脏值自动同步进 growth_records（WHO 曲线）。
    P1  里程碑非空 list body 绕过 `if not data` 守卫 → AttributeError 500。
    P2  admin 清日志 days 传字符串 → 500 且泄露内部 TypeError 文案。
  这些缺陷的共同点：**读代码看不出来，只有真发请求才能拧出来**。

判据（每个都有正向 + 反向两面）：
  A. PUT 不存在的 id 一律 404（与 DELETE 侧 85 项守卫同一标准）
  B. 脏数据写入一律 400（日期/枚举/范围/类型/长度）
  C. 批量入口与单条路由**同参数差分**：单条 400 的，批量也不许 200 入库
  D. PUT 部分字段更新不许把没传的字段抹成 NULL（NOT NULL 撞 400「数据冲突」）
  E. 正向对照：干净数据全部正常入库（证明不是「一律 400」的假门禁）

跑法：
  C:\Users\X\.workbuddy\binaries\python\envs\babycare\Scripts\python.exe \
      .workbuddy/scripts/verify_write_validation.py
退出码：0 = 全部通过；1 = 有失败。
"""
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
BACKEND = os.path.join(ROOT, "app", "backend")
sys.path.insert(0, BACKEND)

TMP_DIR = tempfile.mkdtemp(prefix="babycare_wv_")
os.environ["BABYCARE_DATA_DIR"] = TMP_DIR
os.environ["BABYCARE_DB_PATH"] = os.path.join(TMP_DIR, "babycare.db")
os.environ["BABYCARE_LOG_DIR"] = os.path.join(TMP_DIR, "logs")
os.environ["BABYCARE_DEV_AUTH"] = "1"

# Windows 没有 fcntl，server/backup 的 flock 需要桩掉（与其他门禁同一做法）
_fcntl = type(sys)("fcntl")
for _n in ("LOCK_EX", "LOCK_SH", "LOCK_NB", "LOCK_UN"):
    setattr(_fcntl, _n, 0)
_fcntl.flock = lambda *a, **k: None
_fntl_lockf = lambda *a, **k: None
_fcntl.lockf = _fntl_lockf
sys.modules["fcntl"] = _fcntl

import server  # noqa: E402

app = server.app
app.config["PROPAGATE_EXCEPTIONS"] = False
server.init_db()
client = app.test_client()
with client.session_transaction() as sess:
    sess["authenticated"] = True
    sess["is_admin"] = True
    sess["user_id"] = 1

PASS, FAIL = [], []


def ok(msg):
    PASS.append(msg)
    print("  [OK]   " + msg)


def bad(msg, detail=None):
    FAIL.append(msg)
    print("  [FAIL] " + msg + (("   实际 " + str(detail)) if detail is not None else ""))


def post(url, body):
    return client.post(url, json=body)


def put(url, body):
    return client.put(url, json=body)


def jr(resp):
    try:
        return resp.get_json() or {}
    except Exception:
        return {}


# ---------- 准备两个宝宝 + 各类干净记录（供 PUT/差分用） ----------
BID = (jr(post("/api/babies", {"name": "门禁宝宝", "birthday": "2025-06-01"}))
       .get("data", {}).get("id"))
if not BID:
    print("建宝宝失败，无法继续")
    sys.exit(1)

# 疫苗（两条写路径各建一条）
v1 = jr(post(f"/api/babies/{BID}/vaccines",
             {"vaccine_name": "乙肝疫苗", "scheduled_date": "2025-06-01",
              "dose_number": 1, "status": "pending"}))
VID = v1.get("id") or v1.get("data", {}).get("id")
if not VID:
    # 接口不回 id 就从列表里按名称取
    lst = jr(client.get(f"/api/babies/{BID}/vaccines")).get("data") or []
    VID = next((r["id"] for r in lst if r.get("vaccine_name") == "乙肝疫苗"), None)
v2 = jr(post(f"/api/health/vaccines/{BID}",
             {"vaccine_name": "卡介苗", "scheduled_date": "2025-06-01", "dose_number": 1}))
VID2 = v2.get("id") or v2.get("data", {}).get("id")
if not VID2:
    lst = jr(client.get(f"/api/health/vaccines/{BID}")).get("data") or []
    VID2 = next((r["id"] for r in lst if r.get("vaccine_name") == "卡介苗"), None)

# 过敏测试 / 用药提醒 / 里程碑 / 成长（供 PUT 用）
aid = jr(post(f"/api/babies/{BID}/allergy-tests",
              {"food_name": "鸡蛋", "test_date": "2026-09-01"})).get("id")
if not aid:
    lst = jr(client.get(f"/api/babies/{BID}/allergy-tests")).get("data") or []
    aid = lst[0]["id"] if lst else None
mid = jr(post(f"/api/babies/{BID}/medication-reminders",
              {"medication_name": "维生素D", "next_dose_time": "2026-10-01 08:00:00"})).get("id")
if not mid:
    # POST 只回 success，id 要从列表取
    lst = jr(client.get(f"/api/babies/{BID}/medication-reminders")).get("data") or []
    mid = next((r["id"] for r in lst if r.get("medication_name") == "维生素D"), None)
if not mid:
    bad("用药提醒建记录失败（后续 D 段无法测）")
mst = jr(post(f"/api/babies/{BID}/milestones",
              {"title": "会翻身", "achieved_date": "2026-08-01", "category": "motor"}))
MST = mst.get("id") or mst.get("data", {}).get("id")
if not MST:
    lst = jr(client.get(f"/api/babies/{BID}/milestones")).get("data") or []
    MST = next((r["id"] for r in lst if r.get("title") == "会翻身"), None)
gid = jr(post(f"/api/babies/{BID}/growth",
              {"record_date": "2026-09-01", "height": 75, "weight": 9.5})).get("id")
if not gid:
    lst = jr(client.get(f"/api/babies/{BID}/growth")).get("data") or []
    gid = lst[0]["id"] if lst else None

print("=" * 66)
print("A. PUT 不存在的 id 一律 404（假成功清零）")
print("=" * 66)
MISSING = 999999
for name, url in (
    ("疫苗 PUT", f"/api/vaccines/{MISSING}"),
    ("疫苗明细 PUT", f"/api/health/vaccines/detail/{MISSING}"),
    ("过敏测试 PUT", f"/api/allergy-tests/{MISSING}"),
    ("用药提醒 PUT", f"/api/babies/{BID}/medication-reminders/{MISSING}"),
    ("里程碑 PUT", f"/api/babies/{BID}/milestones/{MISSING}"),
    ("成长 PUT", f"/api/babies/{BID}/growth/{MISSING}"),
):
    r = put(url, {"status": "completed", "note": "x"})
    code = r.status_code
    body = jr(r)
    if code == 404 and body.get("success") is False:
        ok(f"{name}：不存在的 id 返回 404")
    else:
        bad(f"{name}：不存在的 id 没有 404（假成功）", {"状态码": code, " body": body})

print()
print("=" * 66)
print("B. 脏数据写入一律 400")
print("=" * 66)
# B1 疫苗：脏日期 / 负剂次 / 非法枚举 / 超长 note
for label, body in (
    ("疫苗脏日期", {"vaccine_name": "甲肝", "scheduled_date": "8月20号"}),
    ("疫苗负剂次", {"vaccine_name": "甲肝", "dose_number": -5}),
    ("疫苗非法状态", {"vaccine_name": "甲肝", "status": "香蕉"}),
    ("疫苗超长备注", {"vaccine_name": "甲肝", "note": "x" * 20001}),
):
    r = post(f"/api/babies/{BID}/vaccines", body)
    if r.status_code == 400:
        ok(f"{label}：400 拒绝")
    else:
        bad(f"{label}：没有 400", {"状态码": r.status_code, "body": jr(r)})

# B2 growth：脏日期
r = post(f"/api/babies/{BID}/growth", {"record_date": "8月20号", "height": 80})
ok("成长脏日期：400 拒绝") if r.status_code == 400 else \
    bad("成长脏日期：没有 400", {"状态码": r.status_code, "body": jr(r)})
r = post(f"/api/babies/{BID}/growth", {"record_date": {"a": 1}, "height": 80})
ok("成长日期传对象：不 500") if r.status_code != 500 else \
    bad("成长日期传对象：500 了", r.status_code)

# B3 宝宝：脏生日 / 昵称非字符串 / 超长昵称 / 性别野串
# 「male」不是笔误：verify_health_records 的 fixture 真发过这个值，add_baby
# 接入共享校验前能蒙混入库，之后每次 400 还把门禁打进兜底逻辑。锁住它。
for label, body in (
    ("宝宝脏生日", {"name": "测试", "birthday": "8月20号"}),
    ("宝宝昵称传数字", {"name": 123, "birthday": "2025-01-01"}),
    ("宝宝超长昵称", {"name": "昵" * 200, "birthday": "2025-01-01"}),
    ("宝宝性别野串", {"name": "测试", "birthday": "2025-01-01", "gender": "male"}),
):
    r = post("/api/babies", body)
    if r.status_code == 400:
        ok(f"{label}：400 拒绝")
    else:
        bad(f"{label}：没有 400", {"状态码": r.status_code, "body": jr(r)})

# B4 用药：间隔超大必须 400（此前 99999999 → timedelta 溢出 → 500）
r = post(f"/api/babies/{BID}/medications",
         {"medication_name": "美林", "next_dose_interval": 99999999})
if r.status_code == 400:
    ok("用药间隔 99999999：400 拒绝（不再 500）")
else:
    bad("用药间隔超大：没有 400", {"状态码": r.status_code, "body": jr(r)})

# B5 体检：脏数值必须 400，且不许同步进 growth_records
n_growth_before = len(jr(client.get(f"/api/babies/{BID}/growth")).get("data") or [])
r = post(f"/api/babies/{BID}/checkups",
         {"checkup_date": "2026-09-10", "height": -5, "weight": "abc", "age_months": 9999})
if r.status_code == 400:
    ok("体检脏数值：400 拒绝")
else:
    bad("体检脏数值：没有 400", {"状态码": r.status_code, "body": jr(r)})
n_growth_after = len(jr(client.get(f"/api/babies/{BID}/growth")).get("data") or [])
ok("体检被拒后没有污染生长曲线") if n_growth_after == n_growth_before else \
    bad("体检脏数值被同步进了 growth_records", {"前": n_growth_before, "后": n_growth_after})

# B6 里程碑：非 dict body 不许 500
r = post(f"/api/babies/{BID}/milestones", [1, 2, 3])
ok("里程碑传数组 body：不 500") if r.status_code != 500 else \
    bad("里程碑传数组 body：500 了", r.status_code)
r = post(f"/api/babies/{BID}/milestones", {"title": {"x": 1}, "achieved_date": "2026-08-01"})
ok("里程碑 title 传对象：不 500") if r.status_code != 500 else \
    bad("里程碑 title 传对象：500 了", r.status_code)
r = post(f"/api/babies/{BID}/milestones", {"title": "t" * 5000, "achieved_date": "2026-08-01"})
ok("里程碑超长标题：400 拒绝") if r.status_code == 400 else \
    bad("里程碑超长标题：没有 400", {"状态码": r.status_code})

# B7 admin 清日志 days：传非数字必须 400 且不泄露内部文案；
#    传数字字符串（"30"）允许 coercem（int() 收敛），不回 500
r = post("/api/admin/logs/clear", {"days": "abc"})
body = jr(r)
leaked = "not supported between" in str(body)
ok("admin days 传 'abc'：400 且不泄露内部报错") if r.status_code == 400 and not leaked else \
    bad("admin days 传 'abc'：状态码/文案不对", {"状态码": r.status_code, "body": body})
r = post("/api/admin/logs/clear", {"days": "30"})
ok("admin days 传 '30'：收敛成 30，不 500") if r.status_code == 200 else \
    bad("admin days 传 '30'：状态码不对", {"状态码": r.status_code, "body": jr(r)})

print()
print("=" * 66)
print("C. 批量入口与单条路由同参差分（单条 400 ⇒ 批量也不许 200 入库）")
print("=" * 66)
DIRTY = "x" * 20001     # 超过 NOTE_MAX_LENGTH=2000

# C1 records 不是 list / 含非 dict 元素：400，不是 500
r = post(f"/api/babies/{BID}/batch-records", {"records": "x"})
ok("批量 records 传字符串：400（不是 500）") if r.status_code == 400 else \
    bad("批量 records 传字符串：状态码不对", {"状态码": r.status_code, "body": jr(r)})
r = post(f"/api/babies/{BID}/batch-records", {"records": [1, 2]})
ok("批量 records 含非对象：400") if r.status_code == 400 else \
    bad("批量 records 含非对象：状态码不对", {"状态码": r.status_code, "body": jr(r)})

# C2 尿布：颜色 purple 单条必 400，批量也必须拒（不进库）
r_single = post(f"/api/babies/{BID}/diaper",
                {"diaper_type": "wet", "change_time": "2026-09-16 10:00:00", "color": "purple"})
single_400 = r_single.status_code == 400
ok("单条尿布颜色 purple：400") if single_400 else \
    bad("单条尿布颜色 purple：没有 400", {"状态码": r_single.status_code})
r_batch = post(f"/api/babies/{BID}/batch-records",
               {"records": [{"type": "diaper", "change_time": "2026-09-16 10:00:00",
                             "diaper_type": "wet", "color": "purple"}]})
batch_body = jr(r_batch)
n_diaper = len(jr(client.get(f"/api/babies/{BID}/diaper")).get("data") or [])
if single_400 and batch_body.get("count") == 0 and n_diaper == 0:
    ok("批量同参：purple 被拒且未入库（与单条一致）")
else:
    bad("批量入口比单条松（purple 进库了？）",
        {"单条状态码": r_single.status_code, "批量": batch_body, "库中尿布数": n_diaper})

# C3 喂奶：amount -999 单条必 400，批量也必须拒
r_single = post(f"/api/babies/{BID}/feeding",
                {"feeding_type": "bottle", "start_time": "2026-09-16 10:00:00", "amount": -999})
single_400f = r_single.status_code == 400
ok("单条喂奶 amount -999：400") if single_400f else \
    bad("单条喂奶 amount -999：没有 400", {"状态码": r_single.status_code})
r_batch = post(f"/api/babies/{BID}/batch-records",
               {"records": [{"type": "feeding", "start_time": "2026-09-16 10:00:00",
                             "feeding_type": "bottle", "amount": -999}]})
batch_body = jr(r_batch)
if single_400f and batch_body.get("count") == 0:
    ok("批量同参：amount -999 被拒且未入库")
else:
    bad("批量入口比单条松（-999 进库了？）", {"批量": batch_body})

# C4 note 超长：单条 400，批量 0 条入库
r_single = post(f"/api/babies/{BID}/diaper",
                {"diaper_type": "wet", "change_time": "2026-09-16 10:00:00", "note": DIRTY})
single_400n = r_single.status_code == 400
ok("单条尿布 note 2万字：400") if single_400n else \
    bad("单条尿布超长 note：没有 400", {"状态码": r_single.status_code})
r_batch = post(f"/api/babies/{BID}/batch-records",
               {"records": [{"type": "diaper", "change_time": "2026-09-16 10:00:00",
                             "diaper_type": "wet", "note": DIRTY}]})
batch_body = jr(r_batch)
if single_400n and batch_body.get("count") == 0:
    ok("批量同参：超长 note 被拒且未入库")
else:
    bad("批量入口比单条松（超长 note 进库了？）", {"批量": batch_body})

# C5 legacy diaper/batch 同样对齐（color purple）
r_legacy = post(f"/api/babies/{BID}/diaper/batch",
                {"records": [{"diaper_type": "wet", "change_time": "2026-09-16 10:00:00",
                              "color": "purple"}]})
legacy_body = jr(r_legacy)
n_diaper2 = len(jr(client.get(f"/api/babies/{BID}/diaper")).get("data") or [])
if legacy_body.get("count") == 0 and n_diaper2 == n_diaper:
    ok("legacy diaper/batch 同参：purple 被拒且未入库")
else:
    bad("legacy 批量入口比单条松", {"返回": legacy_body, "库中尿布数": n_diaper2})

print()
print("=" * 66)
print("D. PUT 部分字段更新不许抹库（没传的字段沿用原值）")
print("=" * 66)
if gid:
    r = put(f"/api/babies/{BID}/growth/{gid}", {"note": "只改备注"})
    row = next((x for x in (jr(client.get(f"/api/babies/{BID}/growth")).get("data") or [])
                if x.get("id") == gid), None)
    if r.status_code == 200 and row and row.get("height") == 75 and row.get("weight") == 9.5:
        ok("成长 PUT 只改备注：身高体重原样保留")
    else:
        bad("成长 PUT 把没传的数值抹了", {"状态码": r.status_code, "行": row})

if mid:
    r = put(f"/api/babies/{BID}/medication-reminders/{mid}", {"note": "只改备注"})
    if r.status_code == 200:
        ok("用药提醒 PUT 只改备注：不再撞「数据冲突」400")
    else:
        bad("用药提醒 PUT 只改备注被拒", {"状态码": r.status_code, "body": jr(r)})

if MST:
    # milestones 表没有 note 列，PUT 只认 title/description/achieved_date/category/is_first
    r = put(f"/api/babies/{BID}/milestones/{MST}", {"description": "只改描述"})
    ok("里程碑 PUT 只改描述：200") if r.status_code == 200 else \
        bad("里程碑 PUT 只改描述被拒", {"状态码": r.status_code, "body": jr(r)})

print()
print("=" * 66)
print("E. 正向对照：干净数据全部正常入库（证明不是一律 400 的假门禁）")
print("=" * 66)
checks = [
    ("疫苗新建", post(f"/api/babies/{BID}/vaccines",
                    {"vaccine_name": "麻疹疫苗", "scheduled_date": "2026-01-01",
                     "dose_number": 1, "status": "completed", "note": "正常"}), 200),
    ("疫苗 PUT", put(f"/api/vaccines/{VID}", {"status": "completed", "note": "已接种"}), 200),
    ("疫苗明细 PUT", put(f"/api/health/vaccines/detail/{VID2}", {"status": "completed"}), 200),
    ("成长新建", post(f"/api/babies/{BID}/growth",
                    {"record_date": "2026-09-20", "height": 76, "weight": 9.8}), 200),
    ("成长 PUT", put(f"/api/babies/{BID}/growth/{gid}", {"height": 76, "weight": 9.6}), 200),
    ("里程碑新建", post(f"/api/babies/{BID}/milestones",
                      {"title": "会爬", "achieved_date": "2026-09-01", "category": "motor"}), 200),
    ("用药新建", post(f"/api/babies/{BID}/medications",
                    {"medication_name": "退烧药", "next_dose_interval": 6}), 200),
    ("批量-喂奶", post(f"/api/babies/{BID}/batch-records",
                     {"records": [{"type": "feeding", "start_time": "2026-09-16 12:00:00",
                                   "feeding_type": "bottle", "amount": 120}]}), 200),
    ("批量-尿布", post(f"/api/babies/{BID}/batch-records",
                     {"records": [{"type": "diaper", "change_time": "2026-09-16 12:30:00",
                                   "diaper_type": "wet", "color": "yellow"}]}), 200),
    ("批量-睡眠", post(f"/api/babies/{BID}/batch-records",
                     {"records": [{"type": "sleep", "start_time": "2026-09-16 13:00:00",
                                   "end_time": "2026-09-16 14:00:00"}]}), 200),
    ("批量-成长", post(f"/api/babies/{BID}/batch-records",
                     {"records": [{"type": "growth", "record_date": "2026-09-25",
                                   "height": 77, "weight": 9.9}]}), 200),
]
for label, resp, want in checks:
    if resp.status_code == want:
        ok(f"{label}：{want}")
    else:
        bad(f"{label}：期望 {want}", {"状态码": resp.status_code, "body": jr(resp)})

# 批量四条都应计数成功
rb = jr(post(f"/api/babies/{BID}/batch-records",
             {"records": [
                 {"type": "feeding", "start_time": "2026-09-26 09:00:00",
                  "feeding_type": "breast"},
                 {"type": "sleep", "start_time": "2026-09-26 10:00:00",
                  "end_time": "2026-09-26 11:00:00"},
                 {"type": "diaper", "change_time": "2026-09-26 11:30:00",
                  "diaper_type": "both", "color": "brown"},
                 {"type": "growth", "record_date": "2026-09-26", "height": 78},
             ]}))
ok("批量四条全部成功") if rb.get("count") == 4 else \
    bad("批量四条没有全部成功", rb)

print()
print("=" * 66)
print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
print("=" * 66)
if FAIL:
    print("失败明细：")
    for f in FAIL:
        print("  - " + f)
    sys.exit(1)
print("写接口校验收口全部通过。")
sys.exit(0)
