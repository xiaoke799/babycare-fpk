#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证集成 Token（Home Assistant 等）的权限边界。

对外承诺（ha-integration/README.md）：
    集成 Token 可读数据、写喂养/睡眠/尿布/生长/计时记录，
    **不能**改设置、删宝宝或动备份。

此前 `POST /api/babies`（新建宝宝）与 `PUT /api/babies/<id>`（改宝宝资料）
没挂 @require_admin，集成用户能凭空造宝宝、改掉生日/昵称 —— 与承诺不符。

四类断言：
  A. 管理员（session 登录）建 / 改宝宝仍为 200 —— 否则设置页与资料编辑直接 403
  B. 未登录 401
  C. 集成 Token 403 —— Token 走「管理员 HTTP 接口」生成，命中生产分支
  D. 非管理员真人用户（fnOS 网关 X-Trim-Isadmin=false）仍能建 / 改宝宝 200
     —— 防止用成 require_admin 而误伤家庭成员
  E. 集成 Token **仍可写记录类**（睡眠 / 体温）—— 防止为了收敛权限把 HA 正经用途挡掉

守卫用 require_not_integration（判来源）而非 require_admin（判管理员身份）的原因：
fnOS 上家庭非管理员成员也是真人，不该被机器凭证的规则挡在"添加宝宝"外面。

负向对照（改守卫时务必做一次）：
  把 babies.py 里 add_baby / update_baby 的守卫去掉 → C 会退化成 200，本脚本报 FAIL。
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

tmpdir = tempfile.mkdtemp(prefix="babycare_scope_")
os.environ["BABYCARE_DATA_DIR"] = tmpdir
os.environ["BABYCARE_DB_PATH"] = os.path.join(tmpdir, "babycare.db")
os.environ["BABYCARE_CONFIG_DIR"] = tmpdir

import server  # noqa: E402

app = server.app
app.config["TESTING"] = True
server.init_db()
c = app.test_client()

fails = []


def chk(label, ok, extra=""):
    print(("  [OK]   " if ok else "  [FAIL] ") + label + (("  " + extra) if extra else ""))
    if not ok:
        fails.append(label)


def code_of(resp):
    return resp.status_code


# ---------- 准备：管理员建宝宝 + 生成集成 Token ----------
with c.session_transaction() as s:
    s["authenticated"] = True
r = c.post("/api/babies", json={"name": "甲", "birthday": "2026-01-01", "gender": "boy"})
BID = ((r.get_json() or {}).get("data") or {}).get("id")
r_tok = c.post("/api/settings/integration-token")
TOK = (r_tok.get_json() or {}).get("token") or ""

print("=== A. 管理员（session 登录）===")
chk("能新建宝宝", code_of(r) == 200, "实际 %d" % code_of(r))
chk("确实拿到 baby id", bool(BID), "id=%s" % BID)
r_up = c.put(f"/api/babies/{BID}", json={"name": "甲改"})
chk("能改宝宝资料", code_of(r_up) == 200, "实际 %d" % code_of(r_up))
chk("改名生效", ((r_up.get_json() or {}).get("data") or {}).get("name") == "甲改")

# ---------- 关键：清掉管理员 session，后续请求身份只由 Token 决定 ----------
with c.session_transaction() as s:
    s.clear()

print("\n=== B. 未登录 ===")
chk("未登录建宝宝被拦（401）", code_of(c.post("/api/babies", json={"name": "乙"})) == 401)
chk("未登录改宝宝被拦（401）", code_of(c.put(f"/api/babies/{BID}", json={"name": "x"})) == 401)

print("\n=== C. 集成 Token（已认证但非管理员）===")
H = {"Authorization": "Bearer " + TOK}
# 先确认 Token 本身生效，否则下面的 403 是"没认证"而不是"没权限"
chk("Token 生效（读接口 200）", code_of(c.get("/api/babies", headers=H)) == 200)
rc = c.post("/api/babies", json={"name": "乙", "birthday": "2026-02-02", "gender": "girl"},
            headers=H)
chk("不能新建宝宝（403）", code_of(rc) == 403, "实际 %d" % code_of(rc))
ru = c.put(f"/api/babies/{BID}", json={"name": "被集成改了"}, headers=H)
chk("不能改宝宝资料（403）", code_of(ru) == 403, "实际 %d" % code_of(ru))
rd = c.delete(f"/api/babies/{BID}", json={"safety_code": ""}, headers=H)
chk("不能删宝宝（403）", code_of(rd) == 403, "实际 %d" % code_of(rd))

print("\n=== D. 非管理员真人用户（fnOS 网关 X-Trim-Isadmin=false）不受影响 ===")
# 这类用户是家庭成员，不是机器凭证：添加宝宝 / 改资料本就该能用。
# 若这里退化成 403，说明误用了 require_admin 而不是 require_not_integration。
GATEWAY = {"X-Trim-Userid": "u_family", "X-Trim-Username": "家人",
           "X-Trim-Isadmin": "false"}
ENV_GW = {"BABYCARE_GATEWAY_TRUSTED": "1"}
rg = c.post("/api/babies", json={"name": "丙", "birthday": "2026-03-03", "gender": "boy"},
            headers=GATEWAY, environ_base=ENV_GW)
chk("非管理员真人可建宝宝（200）", code_of(rg) == 200, "实际 %d" % code_of(rg))
BID2 = ((rg.get_json() or {}).get("data") or {}).get("id")
rg2 = c.put(f"/api/babies/{BID2}", json={"name": "丙改"},
            headers=GATEWAY, environ_base=ENV_GW)
chk("非管理员真人可改宝宝资料（200）", code_of(rg2) == 200, "实际 %d" % code_of(rg2))

print("\n=== E. 集成 Token 仍可写记录类（不能误伤）===")
rs = c.post(f"/api/babies/{BID}/sleep",
            json={"start_time": "2026-10-02 10:00:00", "end_time": "2026-10-02 11:00:00"},
            headers=H)
chk("可写睡眠记录", code_of(rs) == 200, "实际 %d" % code_of(rs))
rt = c.post(f"/api/babies/{BID}/temperatures",
            json={"temperature": 36.8, "time": "2026-10-02 10:00:00"}, headers=H)
chk("可写体温记录", code_of(rt) == 200, "实际 %d" % code_of(rt))

print()
if fails:
    print("失败 %d 项" % len(fails))
    sys.exit(1)
print("通过")
