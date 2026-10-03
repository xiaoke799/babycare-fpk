#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证监护人同意机制。

《个人信息保护法》第 31 条：处理不满十四周岁未成年人个人信息须取得监护人同意。
本脚本验的是「这个同意不能被绕过、不能被伪造、可以撤回」。

断言：
  A. 首次访问 need_consent=true；同意后变 false
  B. **没勾选监护人确认 → 400**（不能因为前端漏传就默认通过）
  C. 记录里有协议版本号与时间（可举证）
  D. 撤回后 need_consent 重新变 true
  E. 协议版本变更后自动要求重新同意
  F. 协议 URL 由后端生成且非空（前端不硬编码）
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

tmpdir = tempfile.mkdtemp(prefix="babycare_consent_")
os.environ["BABYCARE_DATA_DIR"] = tmpdir
os.environ["BABYCARE_DB_PATH"] = os.path.join(tmpdir, "babycare.db")
os.environ["BABYCARE_CONFIG_DIR"] = tmpdir

import server  # noqa: E402

app = server.app
app.config["TESTING"] = True
server.init_db()

fails: list[str] = []


def chk(ok: bool, label: str, extra: str = "") -> None:
    print(("  [OK]   " if ok else "  [FAIL] ") + label + (f"  {extra}" if extra else ""))
    if not ok:
        fails.append(label)


c = app.test_client()
# 与其它 verify 脚本一致：本地测试直接置会话标志（生产由 fnOS 网关鉴权）
with c.session_transaction() as s:
    s["authenticated"] = True

print("=== A. 首次访问需要同意 ===")
r = c.get("/api/consent/required").get_json()
d = (r or {}).get("data") or {}
chk(r and r.get("success"), "接口可用")
chk(d.get("need_consent") is True, "首次 need_consent=true", f"实际 {d.get('need_consent')}")
chk(bool(d.get("terms_version")) and bool(d.get("privacy_version")),
    "带协议版本号", f"{d.get('terms_version')} / {d.get('privacy_version')}")
chk(bool(d.get("terms_url")) and bool(d.get("privacy_url")),
    "协议 URL 由后端生成（非空，前端不硬编码）",
    f"{d.get('terms_url')}")

print("\n=== B. 未勾选监护人不得通过 ===")
r = c.post("/api/consent", json={})
chk(r.status_code == 400, "空 body → 400", f"实际 {r.status_code}")
r = c.post("/api/consent", json={"is_guardian": False})
chk(r.status_code == 400, "is_guardian=false → 400", f"实际 {r.status_code}")
r = c.get("/api/consent/required").get_json()
chk((r or {}).get("data", {}).get("need_consent") is True,
    "被拒后仍需同意（没有落库）")

print("\n=== C. 正常同意 ===")
r = c.post("/api/consent", json={"is_guardian": True, "app_version": "0.0.1"})
chk(r.status_code == 200 and (r.get_json() or {}).get("success"),
    "勾选后记录成功", f"实际 {r.status_code}")
r = c.get("/api/consent/required").get_json()
d = (r or {}).get("data") or {}
chk(d.get("need_consent") is False, "同意后不再重复征求", f"实际 {d.get('need_consent')}")
chk(bool(d.get("agreed_at")), "记录了同意时间（可举证）", f"{d.get('agreed_at')}")

with app.app_context():
    from utils import get_db
    row = dict(get_db().execute(
        "SELECT * FROM consent_records ORDER BY id DESC LIMIT 1").fetchone())
chk(row.get("is_guardian") == 1, "落库标记为监护人同意")
chk(row.get("terms_version") and row.get("privacy_version"),
    "落库带协议版本号（政策改版后可举证当时同意的是哪版）",
    f"{row.get('terms_version')} / {row.get('privacy_version')}")

print("\n=== D. 撤回同意 ===")
r = c.delete("/api/consent")
chk(r.status_code == 200 and (r.get_json() or {}).get("success"), "撤回成功")
r = c.get("/api/consent/required").get_json()
chk((r or {}).get("data", {}).get("need_consent") is True,
    "撤回后重新需要同意")

print("\n=== E. 协议改版后自动重新征求 ===")
c.post("/api/consent", json={"is_guardian": True})
r = c.get("/api/consent/required").get_json()
chk((r or {}).get("data", {}).get("need_consent") is False, "重新同意后恢复正常")
import blueprints.consent as consent_mod
old = consent_mod.TERMS_VERSION
try:
    consent_mod.TERMS_VERSION = old + "-test"
    r = c.get("/api/consent/required").get_json()
    chk((r or {}).get("data", {}).get("need_consent") is True,
        "协议版本一改，立刻重新征求同意")
finally:
    consent_mod.TERMS_VERSION = old

print()
if fails:
    print("失败 %d 项" % len(fails))
    sys.exit(1)
print("通过")
