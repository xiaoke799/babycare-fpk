#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xiaoke799
r"""记录模块「新建 → 回读 → 逐字段比对」门禁。

为什么要这个脚本：
  2026-10-04 在「记录者字段」那次改造里，辅食记录的 INSERT 写成了
      INSERT INTO solid_food_records (..., food_name, ..., author)
      VALUES (?, ?, ...)", (baby_id, author) + tuple(values)
  —— author 排在第 2 位，而列清单里第 2 位是 food_name，于是 None 被写进
  NOT NULL 的 food_name，**新增辅食从此条条失败**，长达多个版本无人察觉。
  静态检查全都抓不到：占位符数量是对的（ check_sql_placeholder 通过），
  全路由冒烟也不会报错（400 不算 5xx）。只有「存进去、再读出来、一个字段一个字段对」
  才能当场把它拧出来。

判据：
  ① 每个模块用一组**互不相同的特征值**提交（姓名里带 RT-标记 之类的记号）；
  ② 从列表接口里按记号找回那一行；
  ③ 逐个字段比对返回值与提交值 —— 值跑到别的列去了（列错位）或者压根没落库都算失败；
  ④ 时间/日期字段额外要求已经是规范化写法，和 verify_time_normalization 互为补充
     （那个是塞脏值验证收口，这个是验证特征值真的落在自己那一列）。

跑法：
  C:\Users\X\.workbuddy\binaries\python\envs\babycare\Scripts\python.exe \
      .workbuddy/scripts/verify_crud_roundtrip.py
退出码：0 = 全部通过；1 = 有字段对不上或没读回来。
"""
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
BACKEND = os.path.join(ROOT, "app", "backend")
sys.path.insert(0, BACKEND)

TMP_DIR = tempfile.mkdtemp(prefix="babycare_rt_")
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

MARK = "RT回读"          # 每条记录都带这个记号，回读时靠它定位
TODAY = "2026-09-16"

CANON_DT = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
CANON_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# 每个模块：(显示名, 列表URL模板, 提交体, 期望回放字段名集合)
# 期望集合里的时间列会被额外校验格式；其余列做等值比对。
CASES = [
    ("喂养", "/api/babies/{bid}/feeding",
     {"feeding_type": "bottle", "start_time": "2026-09-16 14:30:00",
      "amount": 120, "note": MARK + "喂养"},
     {"feeding_type": "bottle", "amount": 120, "note": MARK + "喂养"},
     {"start_time": "datetime"}),

    ("吸奶", "/api/babies/{bid}/pumping",
     {"pump_time": "2026-09-16 14:30:00", "duration_minutes": 15, "total_amount": 80},
     {"duration_minutes": 15, "total_amount": 80},
     {"pump_time": "datetime"}),

    ("睡眠", "/api/babies/{bid}/sleep",
     {"start_time": "2026-09-16 22:00:00", "end_time": "2026-09-17 06:00:00",
      "sleep_quality": "good"},
     {"sleep_quality": "good"},
     {"start_time": "datetime", "end_time": "datetime"}),

    ("换尿布", "/api/babies/{bid}/diaper",
     {"change_time": "2026-09-16 14:30:00", "diaper_type": "both", "note": MARK + "尿布"},
     {"diaper_type": "both", "note": MARK + "尿布"},
     {"change_time": "datetime"}),

    ("成长", "/api/babies/{bid}/growth",
     {"record_date": TODAY, "height": 66.6, "weight": 7.7},
     {"height": 66.6, "weight": 7.7},
     {"record_date": "date"}),

    ("体温", "/api/babies/{bid}/temperatures",
     {"temperature": 37.4, "measure_method": "armpit", "measure_time": "2026-09-16 14:30:00",
      "note": MARK + "体温"},
     {"temperature": 37.4, "measure_method": "armpit", "note": MARK + "体温"},
     {"measure_time": "datetime"}),

    ("用药记录", "/api/babies/{bid}/medications",
     {"medication_name": MARK + "退烧药", "dosage": "2", "measure_time": "2026-09-16 14:30:00",
      "note": MARK + "用药"},
     {"medication_name": MARK + "退烧药", "dosage": "2", "note": MARK + "用药"},
     {"measure_time": "datetime"}),

    ("趴卧训练", "/api/babies/{bid}/tummy-time",
     {"start_time": "2026-09-16 14:30:00", "duration_minutes": 12, "note": MARK + "趴卧"},
     {"duration_minutes": 12, "note": MARK + "趴卧"},
     {"start_time": "datetime"}),

    ("辅食", "/api/babies/{bid}/solid-food",
     {"food_name": MARK + "南瓜泥", "food_category": "vegetable", "first_try_date": TODAY,
      "amount": "20g", "reaction": "none", "note": MARK + "辅食"},
     {"food_name": MARK + "南瓜泥", "food_category": "vegetable", "amount": "20g",
      "reaction": "none", "note": MARK + "辅食"},
     {"first_try_date": "date"}),

    ("出牙", "/api/babies/{bid}/teething",
     {"tooth_name": MARK + "上门牙", "erupt_date": TODAY},
     {"tooth_name": MARK + "上门牙"},
     {"erupt_date": "date"}),

    ("囟门", "/api/babies/{bid}/fontanelle",
     {"check_date": TODAY, "anterior_status": "open", "posterior_status": "closed"},
     {"anterior_status": "open", "posterior_status": "closed"},
     {"check_date": "date"}),

    ("飞跃期", "/api/babies/{bid}/leaps",
     {"leap_number": 3, "start_date": TODAY, "note": MARK + "飞跃"},
     {"leap_number": 3, "note": MARK + "飞跃"},
     {"start_date": "date"}),

    ("体检", "/api/babies/{bid}/checkups",
     {"checkup_date": TODAY, "height": 66.6, "weight": 7.7, "hospital": MARK + "妇幼"},
     {"height": 66.6, "weight": 7.7, "hospital": MARK + "妇幼"},
     {"checkup_date": "date"}),

    ("里程碑", "/api/babies/{bid}/milestones",
     {"title": MARK + "独坐", "achieved_date": TODAY, "category": "motor"},
     {"title": MARK + "独坐", "category": "motor"},
     {"achieved_date": "date"}),

    ("过敏测试", "/api/babies/{bid}/allergy-tests",
     {"food_name": MARK + "鸡蛋", "test_date": TODAY, "day_number": 2},
     {"food_name": MARK + "鸡蛋", "day_number": 2},
     {"test_date": "date"}),

    ("心情日记", "/api/babies/{bid}/diary",
     {"title": MARK + "日记", "content": MARK + "正文", "entry_date": TODAY},
     {"title": MARK + "日记", "content": MARK + "正文"},
     {"entry_date": "date"}),

    ("用药提醒", "/api/babies/{bid}/medication-reminders",
     {"medication_name": MARK + "维D", "reminder_time": "2026-09-16 09:00:00",
      "frequency": "每日一次", "note": MARK + "提醒"},
     {"medication_name": MARK + "维D", "frequency": "每日一次", "note": MARK + "提醒"},
     {"reminder_time": "datetime"}),

    ("支出记账", "/api/expenses",
     {"category": "diaper", "amount": 128.5, "item_name": MARK + "纸尿裤",
      "expense_date": TODAY, "note": MARK + "比价"},
     {"category": "diaper", "amount": 128.5, "item_name": MARK + "纸尿裤",
      "note": MARK + "比价"},
     {"expense_date": "date"}),

    ("疫苗明细", "/api/health/vaccines/{bid}",
     {"vaccine_name": MARK + "乙肝疫苗", "vaccine_type": "free", "dose_number": 2,
      "scheduled_date": TODAY, "actual_date": TODAY, "status": "completed",
      "manufacturer": MARK + "厂家", "note": MARK + "疫苗"},
     {"vaccine_name": MARK + "乙肝疫苗", "vaccine_type": "free", "dose_number": 2,
      "status": "completed", "manufacturer": MARK + "厂家", "note": MARK + "疫苗"},
     {"scheduled_date": "date", "actual_date": "date"}),

    ("用品比价", "/api/product-prices",
     {"brand": MARK + "品牌", "category": "diaper", "price": 1.65, "package_size": 40,
      "unit": "片"},
     {"brand": MARK + "品牌", "category": "diaper", "price": 1.65, "package_size": 40},
     {}),
]


def auth():
    with client.session_transaction() as sess:
        sess["authenticated"] = True
        sess.permanent = True


def new_baby(name):
    auth()
    r = client.post("/api/babies", json={"name": name, "birthday": "2026-01-15"})
    if r.status_code != 200:
        return None
    payload = r.get_json() or {}
    return payload.get("id") or payload.get("data", {}).get("id")


def pick_rows(payload):
    """从各种返回形态里掏出行列表。"""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("data", "records", "items", "list", "results"):
            v = payload.get(key)
            if isinstance(v, list):
                return v
            if isinstance(v, dict):
                for k2 in ("records", "items", "list"):
                    if isinstance(v.get(k2), list):
                        return v[k2]
    return []


def row_hits_mark(row):
    return any(isinstance(v, str) and MARK in v for v in row.values())


# 「前端真实提交形状」：完全复刻 submitXxx() 里那份 body，包括用户把可选字段留空的情形。
# 只断言 2xx —— 加校验/加归一化最容易顺手把「合法但字段留空」的请求打成 400：
# 用药提醒的 reminder_time 列是 NOT NULL，而前端只提交 next_dose_time，
# 归一化时把空值变成 None 写进去 ⇒ 用户「不填时间反而保存不了」（2026-10-04 真踩过）。
FRONTEND_SHAPES = [
    ("用药提醒·前端形状（时间留空）", "/api/babies/{bid}/medication-reminders",
     {"medication_name": MARK + "维D", "dosage": "1粒", "frequency": "每日一次",
      "next_dose_time": "", "note": ""}),
    ("用药提醒·前端形状（填了时间）", "/api/babies/{bid}/medication-reminders",
     {"medication_name": MARK + "维D", "dosage": "1粒", "frequency": "每日一次",
      "next_dose_time": "2026-09-16T09:00", "note": ""}),
    ("过敏测试·前端形状（不带日期）", "/api/babies/{bid}/allergy-tests",
     {"food_name": MARK + "牛奶", "day_number": 1, "status": "testing", "note": ""}),
    ("体温·前端形状（不带测量时间）", "/api/babies/{bid}/temperatures",
     {"temperature": 36.8, "measure_method": "ear", "note": ""}),
    ("辅食·前端形状（只填必填）", "/api/babies/{bid}/solid-food",
     {"food_name": MARK + "苹果泥", "first_try_date": TODAY}),
    ("日记·前端形状（不带作者）", "/api/babies/{bid}/diary",
     {"title": MARK + "随手", "content": "", "entry_date": TODAY}),
    ("支出·前端形状（不带日期）", "/api/expenses",
     {"category": "toy", "amount": 39, "item_name": MARK + "摇铃"}),
]


def check_frontend_shapes():
    """按前端真实提交形状逐个打一遍，只要求不报错。"""
    problems = []
    for label, url_tpl, body in FRONTEND_SHAPES:
        baby_id = new_baby(f"{MARK}-形状")
        auth()
        r = client.post(url_tpl.format(bid=baby_id), json=body)
        if r.status_code >= 400:
            snippet = (r.get_data(as_text=True) or "")[:90].replace("\n", " ")
            problems.append((label, f"{r.status_code} {snippet}"))
            print(f"  [FAIL] {label:28s} {r.status_code} {snippet[:60]}")
        else:
            print(f"  [OK]   {label:28s} {r.status_code}")
    return problems


def main():
    failures = []
    print("=" * 78)
    print("记录模块「新建 → 回读 → 逐字段比对」")
    print("=" * 78)

    for idx, (name, list_tpl, body, expect_map, time_fields) in enumerate(CASES):
        baby_id = new_baby(f"{MARK}-{name}")
        if not baby_id:
            failures.append((name, "建测试宝宝失败"))
            print(f"  [FAIL] {name:10s} 建测试宝宝失败")
            continue

        auth()
        url = list_tpl.format(bid=baby_id)
        r = client.post(url, json=body)
        if r.status_code >= 400:
            snippet = (r.get_data(as_text=True) or "")[:90].replace("\n", " ")
            failures.append((name, f"新建失败 {r.status_code} {snippet}"))
            print(f"  [FAIL] {name:10s} 新建失败 {r.status_code} {snippet}")
            continue

        auth()
        lr = client.get(url)
        rows = pick_rows(lr.get_json() or {})
        rows = [row for row in rows if isinstance(row, dict)]
        # 每个用例都用新建的宝宝，列表里通常就只有这一条。
        # 但「睡眠/成长/囟门」这类没有备注字段的模块没有记号可认，所以：
        # 先按记号找；找不到就退化为「唯一的那行」；再多也只挑匹配度最高的那一行。
        hits = [row for row in rows if row_hits_mark(row)]
        if not hits:
            if len(rows) == 1:
                hits = rows
            elif rows:
                def score(row):
                    return sum(1 for k, v in expect_map.items() if row.get(k) == v)
                hits = [max(rows, key=score)]
        if not hits:
            failures.append((name, f"列表里没找到带记号的行（共 {len(rows)} 行）"))
            print(f"  [FAIL] {name:10s} 回读不到（列表 {len(rows)} 行）")
            continue

        row = hits[0]
        problems = []

        for key, want in expect_map.items():
            got = row.get(key, "«字段不存在»")
            if isinstance(want, float) and isinstance(got, (int, float)):
                if abs(float(got) - want) > 1e-6:
                    problems.append(f"{key} 期望 {want!r} 实际 {got!r}")
            elif got != want:
                problems.append(f"{key} 期望 {want!r} 实际 {got!r}")

        for key, kind in time_fields.items():
            got = row.get(key, "«字段不存在»")
            pat = CANON_DT if kind == "datetime" else CANON_DATE
            if not (isinstance(got, str) and pat.match(got)):
                problems.append(f"{key} 时间格式应为{kind}规范 实际 {got!r}")

        if problems:
            failures.append((name, "；".join(problems)))
            print(f"  [FAIL] {name:10s} " + "；".join(problems)[:120])
        else:
            checked = len(expect_map) + len(time_fields)
            print(f"  [OK]   {name:10s} {checked} 个字段全部对得上")

    print("-" * 78)
    print("前端真实提交形状检查（可选字段留空也必须能存）")
    shape_fail = check_frontend_shapes()

    print("=" * 78)
    bad = failures + shape_fail
    if bad:
        print(f"共失败 {len(bad)} 项：")
        for name, msg in bad:
            print(f"  - {name}: {msg}")
        return 1
    print(f"全部通过：{len(CASES)} 个模块字段对得上，"
          f"{len(FRONTEND_SHAPES)} 种前端提交形状都能存")
    return 0


if __name__ == "__main__":
    sys.exit(main())
