#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证删除安全码接口的权限门禁（GET 状态接口也要管理员）。

背景：设置/清除安全码的 PUT / DELETE 都有 @require_admin，唯独 GET 状态接口
没有（只回一个 enabled 布尔，看起来无害）。但集成 Token（Home Assistant 等）
是「已认证但非管理员」，不该知道本机有没有设安全码，故补齐并要求这里守着。

三条断言：
  A. 管理员（session 登录）GET 仍为 200 —— 否则设置页白屏（不能为了安全把正常用户挡了）
  B. 未登录 401/403
  C. 集成 Token 403 —— C 走真实 Token（generate_api_token + Authorization 头），
     命中的是 server.require_login 的生产分支，不是打桩出来的假用户。

负向对照（改守卫时务必做一次）：
  把 settings.py 里 GET 的 @require_admin 去掉 → C 会退化成 200，本脚本报 FAIL。
"""
import os
import sys
import tempfile
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if "fcntl" not in sys.modules:
    m = types.ModuleType("fcntl")
    m.LOCK_EX = 2
    m.LOCK_NB = 4
    m.LOCK_UN = 8
    m.flock = lambda *a, **k: None
    sys.modules["fcntl"] = m

tmpdir = tempfile.mkdtemp(prefix="babycare_sc_")
os.environ["BABYCARE_DATA_DIR"] = tmpdir
os.environ["BABYCARE_DB_PATH"] = os.path.join(tmpdir, "babycare.db")
os.environ["BABYCARE_CONFIG_DIR"] = tmpdir

import server  # noqa: E402

app = server.app
app.config["TESTING"] = True
server.init_db()
c = app.test_client()

fails = []
URL = "/api/settings/safety-code"


def chk(label, ok, extra=""):
    print(("  [OK]   " if ok else "  [FAIL] ") + label + (("  " + extra) if extra else ""))
    if not ok:
        fails.append(label)


print("=== A. 管理员（session 登录）===")
with c.session_transaction() as s:
    s["authenticated"] = True
r = c.get(URL)
chk("GET 安全码状态 200（设置页不白屏）", r.status_code == 200, "实际 %d" % r.status_code)
try:
    chk("返回体带 enabled 字段", "enabled" in (r.get_json() or {}).get("data", {}))
except Exception as e:  # noqa
    chk("返回体可解析为 JSON", False, repr(e))

print("\n=== B. 未登录 ===")
with c.session_transaction() as s:
    s.clear()
r2 = c.get(URL)
chk("未登录被拦（401/403）", r2.status_code in (401, 403), "实际 %d" % r2.status_code)

print("\n=== C. 集成 Token（已认证但非管理员）===")
from api_token import generate_api_token  # noqa: E402

with app.app_context():
    tok = generate_api_token()
r3 = c.get(URL, headers={"Authorization": "Bearer " + tok})
chk("集成用户被拦（403）", r3.status_code == 403, "实际 %d" % r3.status_code)

print()
if fails:
    print("失败 %d 项" % len(fails))
    sys.exit(1)
print("通过")
