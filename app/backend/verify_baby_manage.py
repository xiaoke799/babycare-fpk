#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""宝宝资料管理冒烟：创建 → 修改 → 删除（临时库，不碰正式数据）。

跑法：
  $MIMO_PYTHON app/backend/verify_baby_manage.py
"""
import os
import sys
import tempfile

BACKEND = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BACKEND)

TMP_DIR = tempfile.mkdtemp(prefix="babycare_baby_manage_")
TMP_DB = os.path.join(TMP_DIR, "babycare.db")
os.environ["BABYCARE_DATA_DIR"] = TMP_DIR
os.environ["BABYCARE_DB_PATH"] = TMP_DB
os.environ["BABYCARE_LOG_DIR"] = os.path.join(TMP_DIR, "logs")
os.environ["BABYCARE_DEV_AUTH"] = "1"

_fcntl = type(sys)("fcntl")
for _n in ("LOCK_EX", "LOCK_SH", "LOCK_NB", "LOCK_UN"):
    setattr(_fcntl, _n, 0)
_fcntl.flock = lambda *a, **k: None
_fcntl.lockf = lambda *a, **k: None
sys.modules["fcntl"] = _fcntl

import server  # noqa: E402

_passed = 0
_failed = 0


def check(name, cond, extra=""):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  [OK]   {name}")
    else:
        _failed += 1
        print(f"  [FAIL] {name}" + (f"  -> {extra}" if extra else ""))


print("\n=== 0. 建库 ===")
server.init_db()
try:
    server._run_migrations()
except Exception as e:
    print(f"  [警告] 迁移抛异常（继续）：{e}")
check("临时库建好了", os.path.exists(TMP_DB))

app = server.app
app.config["TESTING"] = True
client = app.test_client()
with client.session_transaction() as sess:
    sess["authenticated"] = True  # 会话登录恒为管理员

print("\n=== 1. 创建 ===")
r = client.post("/api/babies", json={
    "name": "冒烟宝",
    "birthday": "2024-06-01",
    "gender": "girl",
    "due_date": "2024-08-01",
})
j = r.get_json() or {}
check("POST 创建成功", r.status_code == 200 and j.get("success"), f"{r.status_code} {j}")
baby_id = (j.get("data") or {}).get("id")
check("返回了 id", isinstance(baby_id, int), str(baby_id))

print("\n=== 2. 修改 ===")
r = client.put(f"/api/babies/{baby_id}", json={"name": "冒烟宝改", "gender": "boy"})
j = r.get_json() or {}
data = j.get("data") or {}
check("PUT 成功", r.status_code == 200 and j.get("success"), f"{r.status_code} {j}")
check("昵称已改", data.get("name") == "冒烟宝改", str(data.get("name")))
check("性别已改", data.get("gender") == "boy", str(data.get("gender")))
check("未传字段保持不变", data.get("birthday") == "2024-06-01", str(data.get("birthday")))
check("年龄字段仍在", "age" in data)

r = client.put(f"/api/babies/{baby_id}", json={"due_date": "2025-01-01"})
check("预产期晚于生日被拒", r.status_code == 400, str(r.get_json()))

r = client.put(f"/api/babies/{baby_id}", json={})
check("空 body 被拒", r.status_code == 400, str(r.get_json()))

r = client.put(f"/api/babies/{baby_id}", json={"name": "   "})
check("空白昵称被拒", r.status_code == 400, str(r.get_json()))

r = client.put("/api/babies/999999", json={"name": "x"})
check("改不存在的宝宝 404", r.status_code == 404, str(r.get_json()))

print("\n=== 3. 删除（安全码）===")
# 未设安全码时禁止删除
r = client.delete(f"/api/babies/{baby_id}", json={"safety_code": "1234"})
check("未设安全码时禁止删除", r.status_code == 403 and (r.get_json() or {}).get("needSafetyCode"),
      f"{r.status_code} {r.get_json()}")

# 设置安全码
r = client.put("/api/settings/safety-code", json={"code": "2580"})
check("设置安全码成功", r.status_code == 200 and (r.get_json() or {}).get("success"), str(r.get_json()))

r = client.get("/api/settings/safety-code")
check("状态为已启用", (r.get_json() or {}).get("data", {}).get("enabled") is True, str(r.get_json()))

# 空码 / 错码
r = client.delete(f"/api/babies/{baby_id}", json={"safety_code": ""})
check("空安全码被拒", r.status_code == 403, str(r.get_json()))

r = client.delete(f"/api/babies/{baby_id}", json={"safety_code": "0000"})
check("错误安全码被拒", r.status_code == 403 and "安全码" in ((r.get_json() or {}).get("message") or ""),
      str(r.get_json()))

r = client.delete("/api/babies/999999", json={"safety_code": "2580"})
check("正确码 + 不存在 id 仍 404", r.status_code == 404, str(r.get_json()))

r = client.delete(f"/api/babies/{baby_id}", json={"safety_code": "2580"})
j = r.get_json() or {}
check("正确安全码 DELETE 成功", r.status_code == 200 and j.get("success"), f"{r.status_code} {j}")

r = client.get(f"/api/babies/{baby_id}")
check("删除后 GET 404", r.status_code == 404, str(r.get_json()))

# 修改安全码
r = client.put("/api/settings/safety-code", json={"code": "abcd"})
check("修改安全码成功", r.status_code == 200, str(r.get_json()))
r = client.put("/api/settings/safety-code", json={"code": "ab"})
check("过短安全码被拒", r.status_code == 400, str(r.get_json()))

print(f"\n=== 结果：{_passed} 过 / {_failed} 挂 ===")
sys.exit(1 if _failed else 0)
