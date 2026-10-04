#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xiaoke799
r"""时间格式归一化门禁：给每个记录接口塞「非标准格式的时间」，回库检查有没有原样落进去。

背景（为什么说这是静默缺陷）：
  库内时间统一约定 'YYYY-MM-DD HH:MM:SS'，纯日期列统一 'YYYY-MM-DD'。
  一旦某个模块把前端 datetime-local 给的 '2026-09-16T14:30' 原样写库：
    - 不报错、界面提示「已保存」；
    - 但 ORDER BY <时间列> 时 'T'(0x54) > ' '(0x20)，同一天带 T 的记录会被排到半天之后，
      列表顶部显示的「最近一次」是错的；
    - 按日期区间筛选（measure_time <= '2026-09-16 23:59:59'）时，带 T 的行被整条漏掉。
  这类问题在有 nonce 校验的模块里已修（喂养/睡眠/尿布/排卵），本脚本覆盖其余模块。

判据：
  ① 每个用例新建**独立宝宝**，提交脏值后扫描全库该宝宝的所有字符串列；
     凡是包含测试日期却又不是规范格式的，即为缺陷（精确到 表.列 = 值）。
  ② 同时要求「确实入库了」—— 接口若返回 4xx 拒收，也算缺陷
     （前端 datetime-local 给的就是 T 格式，拒收等于把正常请求打成 400）。
  ③ 内置**正向对照**：已知修好的模块（喂养/睡眠）必须判通过，
     否则说明扫描本身没扫到东西，结果不可信（防假绿）。

跑法：
  C:\Users\X\.workbuddy\binaries\python\envs\babycare\Scripts\python.exe \
      .workbuddy/scripts/verify_time_normalization.py
退出码：0 = 全部通过；1 = 有缺陷或对照组失守。
"""
import os
import re
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
BACKEND = os.path.join(ROOT, "app", "backend")
sys.path.insert(0, BACKEND)

TMP_DIR = tempfile.mkdtemp(prefix="babycare_timenorm_")
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

DB_PATH = os.environ["BABYCARE_DB_PATH"]
client = app.test_client()

# 测试用日期：故意带两种「前端真会给」的脏写法
DT_VARIANTS = [
    ("datetime-local(T)", "2026-09-16T14:30"),          # datetime-local 无秒
    ("斜杠+空格", "2026/09/16 14:30"),                   # 某些输入法/老手机
]
DATE_VARIANTS = [
    ("斜杠日期", "2026/09/16"),
]

CANON_DT = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
CANON_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# 用例：(显示名, 方法, URL模板, 生成body(v) -> dict, 期望语义)
CASES = [
    # ---------- 时间语义（datetime） ----------
    ("体温", "POST", "/api/babies/{bid}/temperatures",
     lambda v: {"temperature": 36.5, "measure_method": "ear", "measure_time": v}, "datetime"),
    ("用药记录", "POST", "/api/babies/{bid}/medications",
     lambda v: {"medication_name": "测试药", "dosage": "1", "measure_time": v,
                "next_dose_interval": 6}, "datetime"),
    ("趴卧训练", "POST", "/api/babies/{bid}/tummy-time",
     lambda v: {"start_time": v, "end_time": "2026-09-16 15:00:00",
                "duration_minutes": 30}, "datetime"),
    ("用药提醒", "POST", "/api/babies/{bid}/medication-reminders",
     lambda v: {"medication_name": "测试药", "reminder_time": v,
                "frequency": "每日一次"}, "datetime"),
    # ---------- 纯日期语义 ----------
    ("过敏测试", "POST", "/api/babies/{bid}/allergy-tests",
     lambda v: {"food_name": "鸡蛋", "test_date": v, "day_number": 1}, "date"),
    ("心情日记", "POST", "/api/babies/{bid}/diary",
     lambda v: {"title": "测试", "content": "内容", "entry_date": v}, "date"),
    ("出牙记录", "POST", "/api/babies/{bid}/teething",
     lambda v: {"tooth_name": "下中切牙", "erupt_date": v}, "date"),
    ("辅食记录", "POST", "/api/babies/{bid}/solid-food",
     lambda v: {"food_name": "米糊", "first_try_date": v}, "date"),
    ("囟门检查", "POST", "/api/babies/{bid}/fontanelle",
     lambda v: {"check_date": v, "anterior_status": "open"}, "date"),
    ("飞跃期", "POST", "/api/babies/{bid}/leaps",
     lambda v: {"leap_number": 1, "start_date": v}, "date"),
    ("体检记录", "POST", "/api/babies/{bid}/checkups",
     lambda v: {"checkup_date": v, "height": 50, "weight": 4}, "date"),
    ("里程碑", "POST", "/api/babies/{bid}/milestones",
     lambda v: {"title": "抬头", "achieved_date": v}, "date"),
    ("支出记录", "POST", "/api/expenses",
     lambda v: {"category": "diaper", "amount": 99, "item_name": "纸尿裤",
                "expense_date": v}, "date"),
    # ---------- 正向对照：这两处的归一化已修好，必须判 PASS ----------
    ("【对照·已修】喂养", "POST", "/api/babies/{bid}/feeding",
     lambda v: {"feeding_type": "breast", "start_time": v}, "datetime"),
    ("【对照·已修】睡眠", "POST", "/api/babies/{bid}/sleep",
     lambda v: {"start_time": v, "end_time": "2026-09-16 16:00:00"}, "datetime"),
]


def new_baby(name):
    with client.session_transaction() as sess:
        sess["authenticated"] = True
        sess.permanent = True
    r = client.post("/api/babies", json={"name": name, "birthday": "2026-01-15"})
    if r.status_code != 200:
        return None, f"建宝宝失败 {r.status_code}: {r.get_data(as_text=True)[:100]}"
    return (r.get_json() or {}).get("id") or (r.get_json() or {}).get("data", {}).get("id"), None


def _all_tables(con):
    return [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]


def snapshot_ids():
    """记录每张表现在的最大 id，用来事后精确定位「这次请求新写进去的行」。

    早先是按 baby_id 找行的：支出记账这类**全局表**没有 baby_id，
    接口明明返回 200「已记账」，扫描器却报告「未入库」—— 假报警会反过来掩盖真缺陷，
    所以改成 id 增量对照，不挑表结构。
    """
    con = sqlite3.connect(DB_PATH)
    snap = {}
    for t in _all_tables(con):
        cols = [c[1] for c in con.execute(f"PRAGMA table_info('{t}')")]
        key = "id" if "id" in cols else "rowid"
        try:
            snap[t] = (key, con.execute(f"SELECT MAX({key}) FROM '{t}'").fetchone()[0] or 0)
        except sqlite3.Error:
            pass
    con.close()
    return snap


def scan_new_rows(snap):
    """找出快照之后新增的行里，所有「含测试日期」的字符串值，返回 [(表, 列, 值)]。"""
    con = sqlite3.connect(DB_PATH)
    found = []
    for t, (key, max_id) in snap.items():
        cols = [c[1] for c in con.execute(f"PRAGMA table_info('{t}')")]
        try:
            rows = con.execute(f"SELECT * FROM '{t}' WHERE {key} > ?", (max_id,)).fetchall()
        except sqlite3.Error:
            continue
        for row in rows:
            for k, v in zip(cols, row):
                if isinstance(v, str) and ("2026-09-16" in v or "2026/09/16" in v):
                    found.append((t, k, v))
    con.close()
    return found


def auth():
    """每个请求前重种会话（本项目的路由里含 /api/auth/logout，会清掉 session）。"""
    with client.session_transaction() as sess:
        sess["authenticated"] = True
        sess.permanent = True


def _rows_in_tables_simple(snap, baby_id):
    """按 id 增量取出刚刚新增的行，返回 [(表名, 列名->值的字典)]。"""
    con = sqlite3.connect(DB_PATH)
    out = []
    for t, (key, max_id) in snap.items():
        cols = [c[1] for c in con.execute(f"PRAGMA table_info('{t}')")]
        try:
            rows = con.execute(f"SELECT * FROM '{t}' WHERE {key} > ?", (max_id,)).fetchall()
        except sqlite3.Error:
            continue
        for r in rows:
            d = dict(zip(cols, r))
            if d.get("baby_id") not in (None, baby_id):
                continue
            out.append((t, d))
    con.close()
    return out


def _dirty_value(v):
    """把规范写法改写成老版本真的会在库里出现的脏样子。"""
    if CANON_DT.match(v):
        # 一半做成 19 位（带秒，前端快捷按钮给的字符串被直接塞进 datetime-local 时会有）；
        # 一半做成 16 位（datetime-local 原样落库，历史上最常见的形态）
        return v.replace(" ", "T") if int(v[14:16]) % 2 else v.replace(" ", "T")[:16]
    if CANON_DATE.match(v):
        return v.replace("-", "/")
    return None


def check_legacy_cleanup():
    """存量脏数据：先造出来，跑一次启动清洗，再看有没有全洗干净。

    写入端收口只管得住**以后**的记录。用户在旧版本里已经存下的记录仍是混杂格式，
    列表排序和日期筛选照样是错的 —— 必须靠 server._normalize_record_timestamps()
    这类启动清洗兜底。这里验证清洗覆盖到了每一张表。
    """
    baby_id, err = new_baby("清洗校验专用")
    if err:
        return [("清洗检查", err)]

    seeded = []  # [(表名, id列名值, {(列, 脏值)})]
    for name, method, url_tpl, body_fn, kind in CASES:
        if name.startswith("【对照"):
            continue
        auth()
        bid = None if "{bid}" not in url_tpl else baby_id
        snap = snapshot_ids()
        body = body_fn("2026-09-16" if kind == "date" else "2026-09-16 14:30:00")
        r = client.open(url_tpl.format(bid=bid), method=method, json=body)
        if r.status_code >= 400:
            continue
        for t, d in _rows_in_tables_simple(snap, baby_id or -1):
            dirties = {}
            for col, val in d.items():
                # 审计列由 SQLite 生成（本来就是规范格式），脏化它是自伤，跳过
                if col in ("created_at", "updated_at"):
                    continue
                if not isinstance(val, str):
                    continue
                dv = _dirty_value(val)
                if dv:
                    dirties[col] = dv
            if dirties:
                seeded.append((t, d.get("id"), dirties))

    # 用 SQL 把行改成脏值（绕开接口校验，模拟历史库里已经落地的老数据）
    con = sqlite3.connect(DB_PATH)
    for t, row_id, dirties in seeded:
        sets = ", ".join(f"{c} = ?" for c in dirties)
        con.execute(f"UPDATE '{t}' SET {sets} WHERE id = ?", (*dirties.values(), row_id))
    con.commit()

    if not seeded:
        con.close()
        return [("清洗检查", "没种出任何脏数据，检查本身失效（假绿）")]

    before = len(seeded)
    server._normalize_record_timestamps()

    problems = []
    for t, row_id, dirties in seeded:
        row = con.execute(f"SELECT * FROM '{t}' WHERE id = ?", (row_id,)).fetchone()
        if row is None:
            continue
        cols = [c[1] for c in con.execute(f"PRAGMA table_info('{t}')")]
        d = dict(zip(cols, row))
        for col in dirties:
            v = d.get(col)
            if not isinstance(v, str) or not v:
                continue
            if not (CANON_DT.match(v) or CANON_DATE.match(v)):
                problems.append(f"{t}.{col} 清洗后仍是 {v!r}")
    con.close()

    if problems:
        print(f"  [FAIL] 种下 {before} 行脏数据，清洗后仍有 {len(problems)} 列不合规")
        return [("存量清洗", p) for p in problems]
    print(f"  [OK]   种下 {before} 行脏数据，启动清洗后全部恢复规范格式")
    return []


def main():
    failures = []
    print("=" * 78)
    print("时间格式归一化检查（塞脏值 → 回库验证是否规范化）")
    print("=" * 78)

    for name, method, url_tpl, body_fn, kind in CASES:
        variants = DT_VARIANTS if kind == "datetime" else DATE_VARIANTS
        for vlabel, dirty in variants:
            baby_id, err = new_baby(f"时间校验-{name}-{vlabel}")
            if err:
                failures.append((f"{name}/{vlabel}", err))
                print(f"  [FAIL] {name:16s} {vlabel:16s} {err}")
                continue

            with client.session_transaction() as sess:
                sess["authenticated"] = True
                sess.permanent = True
            snap = snapshot_ids()
            r = client.open(url_tpl.format(bid=baby_id), method=method, json=body_fn(dirty))
            code = r.status_code

            rows = scan_new_rows(snap)
            bad = []
            canon_ok = False
            canon_re = CANON_DT if kind == "datetime" else CANON_DATE
            for t, c, v in rows:
                if canon_re.match(v):
                    canon_ok = True
                else:
                    bad.append(f"{t}.{c} = {v!r}")

            label = f"{name}/{vlabel}"
            if bad:
                msg = "原样入库：" + "；".join(bad[:3])
                failures.append((label, msg))
                print(f"  [FAIL] {label:34s} {msg}")
            elif not rows:
                snippet = (r.get_data(as_text=True) or "")[:90].replace("\n", " ")
                msg = f"未入库 http={code} {snippet}"
                failures.append((label, msg))
                print(f"  [FAIL] {label:34s} {msg}")
            elif not canon_ok:
                msg = "入库了但没有规范形式的日期/时间列"
                failures.append((label, msg))
                print(f"  [FAIL] {label:34s} {msg}")
            else:
                print(f"  [OK]   {label:34s} http={code} → 已规范")

    print("=" * 78)
    control = [f for f in failures if f[0].startswith("【对照")]
    real = [f for f in failures if not f[0].startswith("【对照")]
    if control:
        print(f"⚠ 正向对照失守 {len(control)} 条 —— 扫描器可能没扫到数据，以下结论不可信：")
        for lab, _ in control:
            print("     " + lab)
    print(f"用例总数（含 {len(control)} 条对照）：维护清单 {len(CASES)*2} 项，实际失败 {len(failures)} 项")

    print("-" * 78)
    print("存量脏数据清洗检查（模拟老库里已经存在的混杂格式）")
    clean_fail = check_legacy_cleanup()

    if failures:
        print("写入端失败明细：")
        for lab, msg in failures:
            print(f"  - {lab}: {msg}")
    if clean_fail:
        print("清洗失败明细：")
        for lab, msg in clean_fail:
            print(f"  - {lab}: {msg}")
    if failures or clean_fail:
        return 1
    print("=" * 78)
    print("全部通过：新写入的时间都规范，存量脏数据也能被清洗干净")
    return 0


if __name__ == "__main__":
    sys.exit(main())
