#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证入站 Webhook 的鉴权与输入约束（对外开放入口，必须真打一遍）。

重点验四件容易被「看起来能跑」掩盖的事：
  1. 没 token / 错 token 一律 401；**集成 Token（bcb_）不能当 webhook token 用**
  2. 只能新增记录，没有任何删除 / 改设置的入口
  3. 字段白名单：调用方传不认识的字段会被忽略（碰不到列名 = 没有注入面）
  4. 数值有范围钳制，脏值进不来；超限流返回 429

另：每次调用（含失败）都要落 webhook_logs 审计。
"""
from __future__ import annotations

import os
import sys
import tempfile
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if "fcntl" not in sys.modules:
    m = types.ModuleType("fcntl")
    m.LOCK_EX, m.LOCK_NB, m.LOCK_UN = 2, 4, 8
    m.flock = lambda *a, **k: None
    sys.modules["fcntl"] = m

tmpdir = tempfile.mkdtemp(prefix="babycare_webhook_")
os.environ["BABYCARE_DATA_DIR"] = tmpdir
os.environ["BABYCARE_DB_PATH"] = os.path.join(tmpdir, "babycare.db")
os.environ["BABYCARE_CONFIG_DIR"] = tmpdir

import server  # noqa: E402

app = server.app
app.config["TESTING"] = True
server.init_db()

from api_token import generate_api_token, generate_webhook_token  # noqa: E402
from utils import get_db  # noqa: E402

fails: list[str] = []


def chk(ok: bool, label: str, extra: str = "") -> None:
    print(("  [OK]   " if ok else "  [FAIL] ") + label + (f"  {extra}" if extra else ""))
    if not ok:
        fails.append(label)


with app.app_context():
    db = get_db()
    db.execute("INSERT INTO babies (name, birthday, gender) VALUES (?,?,?)",
               ("小甲", "2026-01-01", "boy"))
    BID = db.execute("SELECT last_insert_rowid() AS i FROM babies").fetchone()["i"]
    WEBHOOK = generate_webhook_token()
    INTEGRATION = generate_api_token()

c = app.test_client()
H = {"Authorization": "Bearer " + WEBHOOK}
URL = "/api/webhook/log"


def post(payload, headers=H):
    return c.post(URL, json=payload, headers=headers)


print("=== 1. 鉴权（对外开放入口最要紧的一关）===")
r = post({"event": "feeding", "baby_id": BID}, headers={})
chk(r.status_code == 401, "不带 token → 401", f"实际 {r.status_code}")
r = post({"event": "feeding", "baby_id": BID},
         headers={"Authorization": "Bearer bcw_完全是错的"})
chk(r.status_code == 401, "错误 token → 401", f"实际 {r.status_code}")
r = post({"event": "feeding", "baby_id": BID},
         headers={"Authorization": "Bearer " + INTEGRATION})
chk(r.status_code == 401,
    "集成 Token(bcb_) 不能当 webhook token 用 → 401", f"实际 {r.status_code}")
r = post({"event": "feeding", "baby_id": BID, "feeding_type": "breast"},
         headers={"X-BabyCare-Token": WEBHOOK})
chk(r.status_code == 201,
    "也支持 X-BabyCare-Token 头（智能按钮常配不了请求头）", f"实际 {r.status_code}")

print("\n=== 2. 正常写入 ===")
r = post({"event": "feeding", "baby_id": BID, "feeding_type": "bottle",
          "amount": 120, "start_time": "2026-10-02T08:30", "author": "妈妈"})
chk(r.status_code == 201, "喂奶记录写入 201", f"实际 {r.status_code}")
rid = ((r.get_json() or {}).get("data") or {}).get("id")
with app.app_context():
    row = get_db().execute("SELECT * FROM feeding_records WHERE id = ?", (rid,)).fetchone()
    d = dict(row)
chk(d.get("amount") == 120, "奶量落库正确", f"实际 {d.get('amount')}")
chk(d.get("start_time") == "2026-10-02 08:30:00",
    "时间被归一化成 'YYYY-MM-DD HH:MM:SS'", f"实际 {d.get('start_time')}")
chk(d.get("author") == "妈妈", "记录者落库正确", f"实际 {d.get('author')}")

r = post({"event": "diaper", "baby_id": BID, "diaper_type": "wet"})
chk(r.status_code == 201, "尿布记录写入 201", f"实际 {r.status_code}")
r = post({"event": "sleep", "baby_id": BID, "start_time": "2026-10-02 13:00:00",
          "end_time": "2026-10-02 15:00:00"})
chk(r.status_code == 201, "睡眠记录写入 201", f"实际 {r.status_code}")
r = post({"event": "temperature", "baby_id": BID, "temperature": 37.2})
chk(r.status_code == 201, "体温记录写入 201", f"实际 {r.status_code}")

print("\n=== 3. 输入约束 ===")
r = post({"event": "nuke", "baby_id": BID})
chk(r.status_code == 400, "未知事件类型 → 400", f"实际 {r.status_code}")
r = post({"event": "feeding", "baby_id": 99999, "feeding_type": "bottle"})
chk(r.status_code == 404, "宝宝不存在 → 404", f"实际 {r.status_code}")
r = post({"event": "feeding", "baby_id": BID})
chk(r.status_code == 400, "缺必填字段 → 400", f"实际 {r.status_code}")
r = post({"event": "feeding", "baby_id": "不是数字", "feeding_type": "bottle"})
chk(r.status_code == 400, "baby_id 非法 → 400", f"实际 {r.status_code}")

# 字段白名单：多传一个不认识的字段，不能写进库里
r = post({"event": "feeding", "baby_id": BID, "feeding_type": "bottle",
          "is_admin": 1, "baby_id_hack": "x"})
chk(r.status_code == 201, "带未知字段仍能写（未知字段被忽略）", f"实际 {r.status_code}")
with app.app_context():
    cols = {row[1] for row in get_db().execute("PRAGMA table_info(feeding_records)").fetchall()}
chk("is_admin" not in cols, "表里根本没有可被注入的列（白名单是硬约束）")

print("\n=== 4. 数值钳制 ===")
r = post({"event": "temperature", "baby_id": BID, "temperature": 99})
chk(r.status_code == 201, "离谱体温不报错（钳制而非拒绝）", f"实际 {r.status_code}")
rid2 = ((r.get_json() or {}).get("data") or {}).get("id")
with app.app_context():
    v = get_db().execute("SELECT temperature FROM temperature_records WHERE id = ?",
                         (rid2,)).fetchone()[0]
chk(v == 45, "99℃ 被钳到上限 45", f"实际 {v}")

print("\n=== 5. 限流 ===")
codes = [post({"event": "diaper", "baby_id": BID, "diaper_type": "wet"}).status_code
         for _ in range(35)]
chk(429 in codes, "连续高频触发限流 429", f"状态码集合 {sorted(set(codes))}")

print("\n=== 6. 审计 ===")
with app.app_context():
    total = get_db().execute("SELECT COUNT(*) AS c FROM webhook_logs").fetchone()["c"]
    bad = get_db().execute(
        "SELECT COUNT(*) AS c FROM webhook_logs WHERE ok = 0").fetchone()["c"]
chk(total > 0, f"审计表有 {total} 条记录（含失败）")
chk(bad > 0, f"失败调用也落审计（{bad} 条）")

# ---------------------------------------------------------------------------
print("\n=== 7. 审计可查（曾经只写不读：按钮按了没反应时无从下手）===")
with app.app_context():
    sess = None
from utils import row_to_dict  # noqa: E402

with c.session_transaction() as s:
    s["authenticated"] = True

r = c.get("/api/admin/logs/webhooks?limit=50&days=7")
chk(r.status_code == 200, "查询审计回200", f"实际 {r.status_code}")
body = r.get_json() or {}
chk(body.get("success") is True, "响应带 success")
rows = body.get("data") or []
chk(isinstance(rows, list) and len(rows) > 0,
    f"查得到记录（{len(rows)} 条）", "查不到 = 又变回只写不读")
chk((body.get("total") or 0) > 0, "total 计数 > 0", f"实际 {body.get('total')}")
chk((body.get("failed") or 0) > 0,
    "failed 计数 > 0（失败的也得能查出来）", f"实际 {body.get('failed')}")
if rows:
    chk("created_at" in rows[0] and "ok" in rows[0] and "reason" in rows[0],
        "记录含created_at / ok / reason 三要素")
    # 反向：一条记录都不该出现 webhook token 明文
    with app.app_context():
        leaked = get_db().execute(
            "SELECT COUNT(*) AS c FROM webhook_logs WHERE reason LIKE '%bcw_%'"
        ).fetchone()["c"]
chk(leaked == 0, "审计表里没有 webhook token 明文", f"实际 {leaked} 条")

r = c.get("/api/admin/logs/webhooks?days=9999")
chk(r.status_code == 200, "越界 days 不报错（被钳制）", f"实际 {r.status_code}")
chk((r.get_json() or {}).get("success") is True, "越界 days 仍返回数据")

# ⚠ 上面两条对「钳制」本身是**假绿**：钳没钳响应都长一样，看不出来。
# 所以补一条可观测的判据 —— 塞一条 200 天前的记录：
#   days 钳到 90 时它不该出现（这是「钳制生效」的证据）；
#   若哪天有人把 min(max(...), 90) 删了，days=9999 会把这条老记录捞出来，脚本转红。
with app.app_context():
    get_db().execute(
        "INSERT INTO webhook_logs (created_at, event, baby_id, ok, reason, remote_addr)"
        " VALUES (?,?,?,?,?,?)",
        ("2000-01-01 00:00:00", "oldmarker", BID, 0, "老记录探针", "1.1.1.1"))
    get_db().commit()
r = c.get("/api/admin/logs/webhooks?days=9999&limit=200")
_ids = [x.get("event") for x in ((r.get_json() or {}).get("data") or [])]
chk("oldmarker" not in _ids,
    "days 被钳到 90 天（200 天前的记录不会被 days=9999 捞出来）",
    f"捞出来了 = 钳制没了")

# limit 同理：要能看出被钳到 200
r = c.get("/api/admin/logs/webhooks?limit=100000")
_n = len(((r.get_json() or {}).get("data") or []))
chk(_n <= 200, f"limit 被钳到 200（实际返回 {_n} 条）", "超过 200 就是钳制没了")

r = c.get("/api/admin/logs/webhooks?offset=-50&limit=10")
chk(r.status_code == 200 and (r.get_json() or {}).get("success") is True,
    "负数 offset 被兜住（不当成 SQL 错误）", f"实际 {r.status_code}")

# 清理接口：默认留 30 天，删不存在的也要正常回
r = c.post("/api/admin/logs/webhooks/clear", json={"days": 30})
chk(r.status_code == 200, "清理审计回 200", f"实际 {r.status_code}")
chk(isinstance((r.get_json() or {}).get("deleted"), int),
    "清理返回删除条数（数字型）")
r = c.post("/api/admin/logs/webhooks/clear", json={})
chk(r.status_code == 200, "不传 days 也不报错（有默认值）", f"实际 {r.status_code}")

print()
if fails:
    print("失败 %d 项" % len(fails))
    sys.exit(1)
print("通过")
