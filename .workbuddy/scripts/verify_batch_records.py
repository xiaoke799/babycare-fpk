#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xiaoke799
r"""批量记录「点了提交就真存进去」门禁。

为什么要这个脚本
----------------
2026-10-06 补上批量记录入口（batchRecordBtn）后，这个功能才第一次真正可达。
可达之前它一直"存在但没人点得动"，于是下面这条缺陷一直藏着：

    前端 createEmptyRecord('sleep') 生成 start_time = "2026-10-06 14:30"
    （datetime-local 的语义，不带秒），而后端 add_batch_records 里
        datetime.datetime.strptime(start, '%Y-%m-%d %H:%M:%S')
    硬按"带秒"解析 ⇒ ValueError ⇒ 该条被 except 吞进 errors，added 不增加。

    更糟的是接口照样返回
        {'success': True, 'message': '成功添加 0 条记录', 'errors': [...]}
    前端 submitBatchRecords 只看 res.success，于是 showToast('成功添加 0 条记录')
    然后关掉弹窗、刷新仪表盘 —— 用户以为提交了，其实一条都没进库，
    而且 errors 里的真原因前端根本没展示。

判据（只判"用户视角的真假"，不判实现细节）
------------------------------------------
  ① 四种类型各提交 1 条，用**前端真实会发出的时间写法**（无秒）；
  ② 每一条都必须真的落库（回读列表里能找到）；
  ③ 接口不许出现「success=True 却一条没存进去」—— 这叫假成功；
  ④ 同一次提交里若存在失败行，前端必须拿得到可展示的原因（errors 非空）。

跑法：
  C:\Users\X\.workbuddy\binaries\python\envs\babycare\Scripts\python.exe \
      .workbuddy/scripts/verify_batch_records.py
退出码：0 = 全部通过；1 = 有类型没存进去，或出现了假成功。
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
BACKEND = os.path.join(ROOT, "app", "backend")
sys.path.insert(0, BACKEND)

TMP_DIR = tempfile.mkdtemp(prefix="babycare_batch_")
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

app = server.app
app.config["PROPAGATE_EXCEPTIONS"] = False
server.init_db()
client = app.test_client()
with client.session_transaction() as sess:
    sess["authenticated"] = True   # 走本地会话登录（source=session，是真人不被集成守卫挡）

PASS, FAIL = [], []


def ok(msg):
    PASS.append(msg)
    print("  [OK]   " + msg)


def bad(msg, detail=""):
    FAIL.append(msg)
    print("  [FAIL] " + msg + (("  实际 " + str(detail)) if detail else ""))


# ---------------------------------------------------------------- 建宝宝
r = client.post("/api/babies", json={"name": "批量测试宝", "birthday": "2026-01-01",
                                     "gender": "other"})
if not (r.get_json() or {}).get("success"):
    print("建宝宝失败，无法继续：", r.get_json())
    sys.exit(1)
BID = r.get_json()["data"]["id"]
print("测试宝宝 id =", BID)


def make_record(kind):
    """完全照前端 createEmptyRecord 的输出格式造一条（关键：时间不带秒）。"""
    date_str, time_str = "2026-10-06", "14:30"
    if kind == "feeding":
        return {"type": "feeding", "start_time": f"{date_str} {time_str}",
                "feeding_type": "breast", "amount": "", "side": "left", "note": "BF"}
    if kind == "sleep":
        return {"type": "sleep", "start_time": f"{date_str} {time_str}",
                "end_time": "", "duration_minutes": "", "is_nap": None, "note": "BS"}
    if kind == "diaper":
        return {"type": "diaper", "change_time": f"{date_str} {time_str}",
                "diaper_type": "wet", "color": "", "note": "BD"}
    if kind == "growth":
        # 数值留空发 null（与前端 growthForm 的 `.value || null` 一致）。
        # 旧批量入口把 "" 原样插进 REAL 列（SQLite 动态类型存成文本 ""），
        # 看着能存其实存了一堆非数值，WHO 百分位查询全乱；统一走单条校验后
        # "" 会被 validate_height 挡下——这正是收紧的目的，别用 fixture 迁就旧行为。
        return {"type": "growth", "record_date": date_str,
                "height": None, "weight": None, "head_circumference": None, "bmi": None,
                "note": "BG"}
    raise ValueError(kind)


# 回读用的列表接口与"找得到自己那条"的判定字段
READBACK = {
    "feeding": (f"/api/babies/{BID}/feedings", "note", "BF"),
    "sleep":   (f"/api/babies/{BID}/sleep", "note", "BS"),
    "diaper":  (f"/api/babies/{BID}/diapers", "note", "BD"),
    "growth":  (f"/api/babies/{BID}/growth", "note", "BG"),
}


def find_in(url, field, mark):
    r = client.get(url)
    j = r.get_json() or {}
    rows = j.get("data") or []
    if isinstance(rows, dict):
        rows = rows.get("records") or rows.get("items") or []
    for row in rows:
        if isinstance(row, dict) and row.get(field) == mark:
            return True
    return False


print("\n=== A. 四种类型各提交 1 条，必须真的落库 ===")
for kind in ("feeding", "sleep", "diaper", "growth"):
    rec = make_record(kind)
    r = client.post(f"/api/babies/{BID}/batch-records", json={"records": [rec]})
    j = r.get_json() or {}
    added = j.get("count", j.get("imported", 0))
    errors = j.get("errors") or []
    url, field, mark = READBACK[kind]

    if added == 1 and find_in(url, field, mark):
        ok(f"{kind}：落库并回读成功")
        continue

    # 落库失败：把后端藏起来的真原因挖出来说明白
    reason = errors[0].get("message") if errors else "(后端没给 errors)"
    bad(f"{kind}：提交后没落库", f"success={j.get('success')} count={added} errors={reason}")

print("\n=== B. 不许「success=True 却一条没存进去」===")
rec = make_record("sleep")
r = client.post(f"/api/babies/{BID}/batch-records", json={"records": [rec]})
j = r.get_json() or {}
added = j.get("count", j.get("imported", 0))
errors = j.get("errors") or []
if added == 0 and j.get("success") is True:
    bad("接口返回 success=True 但实际 0 条入库（前端只会弹「成功添加 0 条记录」）",
        f"message={j.get('message')!r} errors={errors}")
elif added == 1:
    ok("sleep 不再假成功：要么真存进去，要么明确报错")
else:
    ok(f"sleep 未入库且未谎报成功（success={j.get('success')}）")

print("\n=== C. 失败行必须带可展示的原因 ===")
bad_rec = {"type": "sleep", "start_time": "不是时间", "note": "BX"}
r = client.post(f"/api/babies/{BID}/batch-records", json={"records": [bad_rec]})
j = r.get_json() or {}
errors = j.get("errors") or []
if errors and any(e.get("message") for e in errors):
    ok(f"非法时间能拿到原因：{errors[0]['message'][:60]}")
else:
    bad("非法时间没有返回可展示的原因（用户只能看到干巴巴的失败）", j)

print("\n=== D. legacy 入口 /diaper/batch 必须与统一接口同口径 ===")
# 前端已改走 /batch-records，但外部集成可能仍在调 /diaper/batch。
# 两个入口是「同一件事」，不能一个严一个松：旧实现 item 不校验（非对象直接 500）、
# 时间硬存原串、且一条都没进去也回 success=True。
r = client.post(f"/api/babies/{BID}/diaper/batch",
                json={"records": [{"diaper_type": "wet",
                                   "change_time": "2026-10-06 14:30", "note": "BDL"}]})
j = r.get_json() or {}
if j.get("success") and j.get("count") == 1 and find_in(f"/api/babies/{BID}/diapers", "note", "BDL"):
    ok("legacy 入口：无秒时间能落库（已被 validate_datetime 收口）")
else:
    bad("legacy 入口：无秒时间没能落库", j)

r = client.post(f"/api/babies/{BID}/diaper/batch",
                json={"records": [{"diaper_type": ""},
                                  {"diaper_type": "wet", "change_time": "不是时间"}]})
j = r.get_json() or {}
if j.get("success") is True and j.get("count") == 0:
    bad("legacy 入口：success=True 却 0 条入库（假成功）", j)
else:
    ok(f"legacy 入口：不谎报成功（success={j.get('success')} count={j.get('count')}）")

r = client.post(f"/api/babies/{BID}/diaper/batch", json={"records": [1, 2]})
if r.status_code >= 500:
    bad("legacy 入口：畸形记录项导致 5xx", r.status_code)
else:
    ok(f"legacy 入口：畸形记录项不 5xx（{r.status_code}）")

print(f"\n通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
if FAIL:
    print("\n失败明细：")
    for f in FAIL:
        print("  - " + f)
    sys.exit(1)
print("全部通过：批量记录真正存得进去，且不再假成功。")
sys.exit(0)
