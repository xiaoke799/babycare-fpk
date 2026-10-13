#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xiaoke799
"""健康档案写入端校验门禁。

体检 / 指标 / 提醒 / 里程碑 / 筛查 / 过敏 / 喂养摘要 七类写入此前没有任何后端校验，
脏数据直接落库：体温填 999、筛查日期写成 2026-13-40、结果判定存成前端认不出的野串、
baby_id 瞎填也照样 201。这里逐条动态验证「脏值进不去 / 干净值照常存 / 不存在的宝宝 404」。

用法：python app/backend/verify_health_records.py
退出码 0=通过，1=不通过，2=跑不起来（环境/建表问题）。
"""

import os
import sys
import tempfile
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail and not ok else ""))
    return ok


def detail(cond, yes, no):
    """cond 成立返回 yes，否则返回 no"""
    return yes if cond else no


def main():
    # 🔴 隔离必须走环境变量，且必须在 import server 之前：
    # constants.DB_PATH / server.DB_PATH 都是 import 时从 BABYCARE_DB_PATH 定的，
    # 运行时再改 app.config 屁用没有（get_db() 只认模块级 DB_PATH）。
    # 本门禁曾用 app.config「假隔离」，实际一直在打默认库
    # （Windows 上 cwd 在 D 盘 ⇒ D:\var\apps\babycare-fpk\var\babycare.db），
    # 各轮测试数据全堆在那儿，还让兜底逻辑落到同一个宝宝身上撞 UNIQUE。
    tmpdir = tempfile.mkdtemp(prefix="verify_health_")
    os.environ["BABYCARE_DATA_DIR"] = tmpdir
    os.environ["BABYCARE_DB_PATH"] = os.path.join(tmpdir, "verify.db")
    os.environ["BABYCARE_LOG_DIR"] = os.path.join(tmpdir, "logs")

    import server  # noqa: E402
    from utils import get_db
    import constants

    # 自检：真隔离了才配往下跑。曾用 app.config 假隔离（get_db 只认 import 时
    # 从环境变量定下的模块级 DB_PATH），悄悄把各轮测试数据写进默认库。这里
    # 直接核对 DB_PATH 落在临时目录里，不满足就红，别让假隔离复活。
    if not os.path.abspath(constants.DB_PATH).startswith(os.path.abspath(tmpdir) + os.sep):
        print("隔离失效：DB_PATH=%s 不在临时目录 %s 下" % (constants.DB_PATH, tmpdir))
        return 2

    server.app.config["TESTING"] = True

    with server.app.app_context():
        server.init_db()

    client = server.app.test_client()
    # verify 里不能用 /api/auth/status 判断登录态，必须自己种一个 session。
    # user_context 认的是 session['authenticated']，其它键（user_id/is_admin…）都看着呢。
    with client.session_transaction() as sess:
        sess["authenticated"] = True
        sess["user_id"] = 1
        sess["username"] = "verify"
        sess["is_admin"] = True

    today = date.today().strftime("%Y-%m-%d")

    def mk_baby(name="宝宝"):
        # gender 必须是 boy/girl/other（与前端下拉一致）。这里曾写 "male"，
        # 在 add_baby 接入共享校验前能蒙混入库，之后每次都被 400 打掉、
        # 走兜底逻辑落到库里第一个宝宝身上——各轮测试数据互相打架。
        resp = client.post("/api/babies", json={
            "name": name, "gender": "boy",
            "birthday": "2025-01-01",
        })
        if resp.status_code >= 400:
            # 已有同名宝宝时接口可能返回 400，直接捞 id
            listing = client.get("/api/babies")
            data = listing.get_json() or {}
            items = data.get("babies") or data.get("data") or []
            return items[0]["id"] if items else None
        body = resp.get_json() or {}
        return ((body.get("data") or {}).get("id")
                or (body.get("baby") or {}).get("id") or body.get("id"))

    baby = mk_baby()
    if not baby:
        print("无法创建测试宝宝，后续用例跳过")
        return 2

    # 实际写入落库后按 id 增量核对，不靠 baby_id 反查（全局表会漏）
    def max_id(table):
        with server.app.app_context():
            db = get_db()
            row = db.execute(f"SELECT MAX(id) AS m FROM {table}").fetchone()
            return row["m"] if row and row["m"] is not None else 0

    def rows_since(table, since):
        with server.app.app_context():
            db = get_db()
            rows = db.execute(
                f"SELECT * FROM {table} WHERE id > ? ORDER BY id", (since,)
            ).fetchall()
            return [dict(r) for r in rows]

    print("\n=== A. 正常值必须写得进去（正向对照） ===")
    before = max_id("health_records")
    r = client.post(f"/api/health/records/{baby}", json={
        "record_date": today, "record_type": "routine", "title": "门禁体检",
        "height": 68.5, "weight": 8.2, "temperature": 36.8, "age_months": 12,
        "next_visit_date": today, "hospital": "市妇幼",
    })
    check("体检·正常值写入成功", r.status_code in (200, 201), f"实际 {r.status_code} {r.get_json()}")
    inserted = rows_since("health_records", before)
    check("体检·确实落库一行", len(inserted) == 1, f"实际 {len(inserted)}")
    if inserted:
        row = inserted[0]
        check("体检·身高按数值归一", abs(float(row["height"]) - 68.5) < 0.01,
              repr(row["height"]))
        check("体检·日期格式归一", row["record_date"] == today, repr(row["record_date"]))

    before = max_id("screenings")
    r = client.post(f"/api/health/screenings/{baby}", json={
        "screening_type": "vision_筛查", "screening_date": today,
        "result_status": "normal", "age_months": 12,
    })
    check("筛查·正常值写入成功", r.status_code in (200, 201), f"实际 {r.status_code} {r.get_json()}")

    before = max_id("allergy_history")
    r = client.post(f"/api/health/allergies/{baby}", json={
        "allergen_type": "food", "allergen_name": "鸡蛋",
        "severity_level": "mild", "status": "active",
        "first_occurrence_date": today,
    })
    check("过敏·正常值写入成功", r.status_code in (200, 201), f"实际 {r.status_code} {r.get_json()}")

    before = max_id("feeding_summary")
    r = client.post(f"/api/health/feeding_summary/{baby}", json={
        "record_month": today[:7], "feeding_type": "breastmilk",
        "avg_daily_ml": 800, "feeding_frequency": 8,
    })
    check("喂养摘要·正常值写入成功", r.status_code in (200, 201), f"实际 {r.status_code} {r.get_json()}")

    before = max_id("health_reminders")
    r = client.post(f"/api/health/reminders/{baby}", json={
        "reminder_type": "vaccine", "title": "疫苗提醒", "due_date": today,
    })
    check("提醒·正常值写入成功", r.status_code in (200, 201), f"实际 {r.status_code} {r.get_json()}")

    before = max_id("milestone_details")
    r = client.post(f"/api/health/milestones/{baby}", json={
        "milestone_name": "抬头", "category": "motor",
        "status": "achieved", "achieved_date": today, "expected_age_months": 3,
    })
    check("里程碑·正常值写入成功", r.status_code in (200, 201), f"实际 {r.status_code} {r.get_json()}")

    print("\n=== B. 脏值必须被拒（400），不能落库 ===")
    dirty_cases = [
        ("体检·体温 999 超区间拒写",
         ("POST", f"/api/health/records/{baby}"),
         {"record_date": today, "title": "脏体温", "temperature": 999},
         "health_records"),
        ("体检·日期写成 2026-13-40 拒写",
         ("POST", f"/api/health/records/{baby}"),
         {"record_date": "2026-13-40", "title": "脏日期"},
         "health_records"),
        ("体检·身高填文字拒写",
         ("POST", f"/api/health/records/{baby}"),
         {"record_date": today, "title": "脏身高", "height": "一米二"},
         "health_records"),
        ("体检·记录类型野串拒写",
         ("POST", f"/api/health/records/{baby}"),
         {"record_date": today, "title": "脏类型", "record_type": "supercheck"},
         "health_records"),
        ("筛查·日期格式错拒写",
         ("POST", f"/api/health/screenings/{baby}"),
         {"screening_type": "vision_筛查", "screening_date": "2026/10/01"},
         "screenings"),
        ("筛查·结果判定野串拒写",
         ("POST", f"/api/health/screenings/{baby}"),
         {"screening_type": "vision_筛查", "screening_date": today,
          "result_status": "pass"},
         "screenings"),
        ("过敏·严重程度野串拒写",
         ("POST", f"/api/health/allergies/{baby}"),
         {"allergen_type": "food", "allergen_name": "脏严重度",
          "severity_level": "verybad"},
         "allergy_history"),
        ("过敏·日期格式错拒写",
         ("POST", f"/api/health/allergies/{baby}"),
         {"allergen_type": "food", "allergen_name": "脏日期",
          "first_occurrence_date": "昨天"},
         "allergy_history"),
        ("喂养·月份写成 2026/10 拒写",
         ("POST", f"/api/health/feeding_summary/{baby}"),
         {"record_month": "2026/10"},
         "feeding_summary"),
        ("喂养·奶量 99999 拒写",
         ("POST", f"/api/health/feeding_summary/{baby}"),
         {"record_month": today[:7], "avg_daily_ml": 99999},
         "feeding_summary"),
        ("提醒·到期日期格式错拒写",
         ("POST", f"/api/health/reminders/{baby}"),
         {"title": "脏提醒", "due_date": "2026-13-01"},
         "health_reminders"),
        ("提醒·类型野串拒写",
         ("POST", f"/api/health/reminders/{baby}"),
         {"title": "脏提醒2", "due_date": today, "reminder_type": "wedding"},
         "health_reminders"),
        ("里程碑·月龄 9999 拒写",
         ("POST", f"/api/health/milestones/{baby}"),
         {"milestone_name": "脏里程碑", "actual_age_months": 9999},
         "milestone_details"),
        ("里程碑·日期格式错拒写",
         ("POST", f"/api/health/milestones/{baby}"),
         {"milestone_name": "脏里程碑2", "achieved_date": "10-04-2026"},
         "milestone_details"),
    ]
    for name, (method, url), payload, table in dirty_cases:
        before = max_id(table)
        resp = client.open(url, method=method, json=payload)
        after = rows_since(table, before)
        ok = resp.status_code == 400 and not after
        check(name, ok,
              detail(ok, "", f"状态码 {resp.status_code}，落库 {len(after)} 行"))

    print("\n=== C. 关联指标（体检内嵌）必须校验 ===")
    before = max_id("health_records")
    r = client.post(f"/api/health/records/{baby}", json={
        "record_date": today, "title": "带指标",
        "indicators": [{"indicator_name": "血红蛋白", "value": "abc"}],
    })
    check("指标值填文字 → 400 而不是 500", r.status_code == 400,
          f"实际 {r.status_code} {r.get_json()}")
    check("指标脏值不落主记录", not rows_since("health_records", before))

    r = client.post(f"/api/health/records/{baby}", json={
        "record_date": today, "title": "带指标2",
        "indicators": [{"indicator_name": "血红蛋白", "value": 120,
                        "result_status": "wild"}],
    })
    check("指标判定野串 → 400", r.status_code == 400,
          f"实际 {r.status_code} {r.get_json()}")

    # 指标表要单独取快照：两张表的 id 不同步，拿 health_records 的 MAX(id) 去量
    # indicator 表会漏，用例会假绿成「指标没存上」
    before = max_id("health_indicators")
    r = client.post(f"/api/health/records/{baby}", json={
        "record_date": today, "title": "带正常指标",
        "indicators": [{"indicator_name": "血红蛋白", "value": 120,
                        "result_status": "normal"}],
    })
    ok = r.status_code in (200, 201) and bool(rows_since("health_indicators", before))
    check("指标正常值照常存", ok, f"实际 {r.status_code}")

    print("\n=== D. 不存在的宝宝必须 404 ===")
    ghost = 999999
    cases_404 = [
        ("体检·幽灵 baby_id 404", "POST", f"/api/health/records/{ghost}"),
        ("筛查·幽灵 baby_id 404", "POST", f"/api/health/screenings/{ghost}"),
        ("过敏·幽灵 baby_id 404", "POST", f"/api/health/allergies/{ghost}"),
        ("喂养·幽灵 baby_id 404", "POST", f"/api/health/feeding_summary/{ghost}"),
        ("提醒·幽灵 baby_id 404", "POST", f"/api/health/reminders/{ghost}"),
        ("里程碑·幽灵 baby_id 404", "POST", f"/api/health/milestones/{ghost}"),
    ]
    for name, method, url in cases_404:
        resp = client.open(url, method=method,
                           json={"record_date": today, "title": "幽灵",
                                 "screening_type": "vision_筛查",
                                 "screening_date": today,
                                 "allergen_name": "幽灵",
                                 "record_month": today[:7],
                                 "milestone_name": "幽灵"})
        check(name, resp.status_code == 404, f"实际 {resp.status_code}")

    print("\n=== E. PUT 分支同样过闸（改体检） ===")
    listing = client.get(f"/api/health/records/{baby}")
    data = listing.get_json() or {}
    recs = data.get("records") or data.get("data") or []
    if recs:
        rid = recs[0]["id"]
        r = client.put(f"/api/health/records/detail/{rid}", json={
            "record_date": "2026-99-99", "height": 999,
        })
        check("PUT·脏日期/脏身高 400", r.status_code == 400,
              f"实际 {r.status_code} {r.get_json()}")
        r = client.put(f"/api/health/records/detail/{rid}",
                       json={"record_date": today, "height": 70.0})
        check("PUT·正常值 200", r.status_code == 200, f"实际 {r.status_code}")
    else:
        check("PUT·前置条件（存在体检记录）", False, "列表捞不到记录，用例跳过")

    print("\n=== F. 缺失必填项要人话而不是 500 ===")
    before = max_id("health_records")
    r = client.post(f"/api/health/records/{baby}", json={"title": "没日期"})
    ok = r.status_code == 400 and not rows_since("health_records", before)
    check("体检缺 record_date → 400 不落库", ok, f"实际 {r.status_code}")

    before = max_id("health_reminders")
    r = client.post(f"/api/health/reminders/{baby}", json={"title": "没到期日期"})
    ok = r.status_code == 400 and not rows_since("health_reminders", before)
    check("提醒缺 due_date → 400 不落库", ok, f"实际 {r.status_code}")

    print("\n=== G. 免责提示接口 ===")
    r = client.get("/api/health/notice")
    body = r.get_json() or {}
    n = body.get("notice") or {}
    ok = (r.status_code == 200 and body.get("success")
          and n.get("title") and n.get("body") and n.get("advice")
          and ("就医" in (n.get("advice") or "") or "就诊" in (n.get("advice") or ""))
          and "医院" in (n.get("body") or ""))
    check("/api/health/notice 返回完整免责文案", ok, str(body)[:120])
    check("提示覆盖主要场景",
          all(k in (n.get("scopes") or []) for k in ("体检", "筛查", "过敏")),
          str(n.get("scopes")))

    print("\n=== G2. 前后端字段必须对得上（防静默空渲染）===")
    import blueprints.health_records as _hr_mod
    HEALTH_NOTICE_KEYS = set(_hr_mod.HEALTH_NOTICE.keys())
    import re as _re
    js_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "frontend", "js", "app.js")
    try:
        js_src = open(js_path, encoding="utf-8").read()
    except OSError:
        js_src = ""
    # 只扫 loadHealthNotice 函数体：`xxx.className` 这类无关代码里的 n.xxx 会误伤
    body = ""
    m = _re.search(r"function loadHealthNotice\(\)\s*\{(.*?)\n\}", js_src, _re.S)
    if m:
        body = m.group(1)
    used = set(_re.findall(r"\bn\.([a-z_]+)", body))
    provided = set(HEALTH_NOTICE_KEYS)
    missing = sorted(used - provided)
    check("前端用到的提示字段后端都给了", not missing, f"缺 {missing}")
    check("前端确实在渲染提示字段", bool(used), "没匹配到任何 n.xxx，用例可能失效")

    print("\n=== G3. PUT 收口：不存在的 id 必须 404，指标归属认记录自己 ===")
    # ① 改不存在的主记录。此前 UPDATE 命中 0 行也返回 200「记录已更新」——
    #    前端以为改成功、刷新还是原样，和 DELETE 那边同一类假成功。
    r = client.put("/api/health/records/detail/999999999", json={"title": "改不存在的"})
    check("改不存在的体检记录 → 404", r.status_code == 404, f"实际 {r.status_code}")
    for path, payload, label in (
        ("/api/health/reminders/detail/999999999", {"title": "x"}, "提醒"),
        ("/api/health/milestones/detail/999999999", {"milestone_name": "x"}, "里程碑"),
        ("/api/health/screenings/detail/999999999", {"result_summary": "x"}, "筛查"),
        ("/api/health/allergies/detail/999999999", {"allergen_name": "x"}, "过敏"),
        ("/api/health/feeding_summary/detail/999999999", {"concerns": "x"}, "喂养摘要"),
    ):
        rr = client.put(path, json=payload)
        check(f"改不存在的{label} → 404", rr.status_code == 404, f"实际 {rr.status_code}")

    # ② 指标归属：指标原来用请求体里的 baby_id 重写，客户端不传就是 NULL，
    #    而指标列表是按 baby_id 过滤的 ⇒ 改一次指标，指标就从列表里凭空消失。
    #    现在必须认「记录自己属于哪个宝宝」。
    r = client.post(f"/api/health/records/{baby}", json={
        "record_date": today, "title": "指标归属用例",
        "indicators": [{"indicator_name": "血红蛋白", "value": 120, "unit": "g/L",
                        "result_status": "normal"}],
    })
    rid = (r.get_json() or {}).get("id")
    check("建带指标的体检记录成功", r.status_code in (200, 201) and bool(rid),
          f"实际 {r.status_code} {r.get_json()}")
    if rid:
        rr = client.put(f"/api/health/records/detail/{rid}", json={
            "note": "改过指标",
            "indicators": [{"indicator_name": "血红蛋白", "value": 118, "unit": "g/L",
                            "result_status": "normal"}],
        })
        check("PUT 重写指标成功", rr.status_code in (200, 201), f"实际 {rr.status_code}")
        rows = rows_since("health_indicators", 0)
        mine = [x for x in rows if x["record_id"] == rid]
        check("重写后指标还在", len(mine) == 1, f"实际 {len(mine)} 行")
        check("指标 baby_id 取自记录本身（不是请求体）",
              bool(mine) and mine[0]["baby_id"] == baby,
              f"实际 {mine[0]['baby_id'] if mine else None} 期望 {baby}")
        # 指标列表按 baby_id 过滤 —— 归属错了这里就会 0 条
        rl = client.get(f"/api/health/indicators/{baby}")
        listed = (rl.get_json() or {}).get("data") or []
        check("改完指标后仍出现在指标列表里",
              any(x.get("indicator_name") == "血红蛋白" for x in listed),
              f"实际 {len(listed)} 条")

    print("\n=== H. 负向对照：把守卫撤掉，脚本必须转红 ===")
    # 这里不真改代码（会污染工作区），而是校验「脏值能落库」时上面的用例确实会 FAIL：
    # 若脏数据本来就被拒，说明校验没生效，用例失去意义。
    import blueprints.health_records as hr
    saved = hr._check_baby_exists
    hr._check_baby_exists = lambda db, bid: (True, None)
    resp = client.post(f"/api/health/screenings/{ghost}", json={
        "screening_type": "vision_筛查", "screening_date": today,
    })
    # 撤掉守卫后不再是 404（这里会退化成外键冲突的 400），
    # 说明 D 段的 404 确实来自 _check_baby_exists，不是别的环节顺手拦的。
    check("撤掉宝宝校验后：不再返回 404（证明 D 段有牙）",
          resp.status_code != 404, f"实际 {resp.status_code}")
    hr._check_baby_exists = saved

    resp2 = client.post(f"/api/health/screenings/{baby}", json={
        "screening_type": "vision_筛查", "screening_date": "2026-13-40",
    })
    check("恢复守卫后同样请求被拒（对照组复核）",
          resp2.status_code == 400, f"实际 {resp2.status_code}")

    try:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)
    except Exception:
        pass

    failed = [x for x in RESULTS if not x[1]]
    total = len(RESULTS)
    print(f"\n{'=' * 52}\n健康档案校验：{total - len(failed)}/{total} 通过")
    if failed:
        for name, _, detail_msg in failed:
            print(f"  FAIL: {name} {detail_msg}")
        print("结论：健康档案写入端仍存在放行脏数据的路径")
        return 1
    print("结论：健康档案写入端校验齐全")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:  # pragma: no cover
        import traceback
        traceback.print_exc()
        print(f"门禁跑不起来：{exc}")
        sys.exit(2)
