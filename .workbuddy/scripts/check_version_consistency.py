#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""「设置页显示的应用版本」与「manifest 真值」一致性门禁。

背景（2026-10-05，用户截图里设置页显示「育儿宝 v0.0.1」）：
  `app/backend/constants.py` 曾硬编码 `APP_VERSION = "...", "0.0.1"`，
  而 `manifest` 写的是 `version=0.0.2`。注释还写着"发布新版本时两处一起改"——
  结果两处立刻走岔：**无论装的是哪个版本，系统诊断里都恒显示 v0.0.1**。
  用户按版本号排查问题（"我装的是不是最新版"）会被直接带偏。

判据（任一不满足即判红）：
  ① constants.py 里**不再出现硬编码的版本号字面量**
     （只允许通过 manifest 读取 / 环境变量注入）
  ② 通过 test_client 打 `/api/storage/app-info`，拿到的 version
     **必须等于 manifest 的 version**（真实取值对账，不是"不是占位串就算过"）
  ③ manifest 里必须真有非空的 version（否则②会拿空串比空串假绿）
  ④ 负向对照保障：把 manifest 临时改成另一个值，接口必须跟着变
     （证明 version 真的来自 manifest，而不是恰好又写死了一份）

跑法：
  C:\\Users\\X\\.workbuddy\\binaries\\python\\envs\\babycare\\Scripts\\python.exe \\
      .workbuddy/scripts/check_version_consistency.py
退出码：0 = 一致；1 = 有漂移。
"""
import io
import os
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
BACKEND = os.path.join(ROOT, "app", "backend")
CONSTANTS = os.path.join(BACKEND, "constants.py")
MANIFEST = os.path.join(ROOT, "manifest")


def read_manifest_version(path):
    with io.open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if line.startswith("version="):
                return line.split("=", 1)[1].strip()
    return ""


def probe_api_version():
    """在子进程里起 test_client 打接口，返回 (version, app_name)。"""
    import subprocess
    code = r'''
import os, sys, tempfile, types
sys.path.insert(0, %r)
T = tempfile.mkdtemp(prefix="babycare_verchk_")
os.environ["BABYCARE_DATA_DIR"] = T
os.environ["BABYCARE_DB_PATH"] = os.path.join(T, "babycare.db")
os.environ["BABYCARE_LOG_DIR"] = os.path.join(T, "logs")
os.environ["BABYCARE_DEV_AUTH"] = "1"
# 子进程也要能读到仓库 manifest（源码树布局）
os.environ.pop("BABYCARE_APP_VERSION", None)
_f = types.ModuleType("fcntl")
for _n in ("LOCK_EX","LOCK_SH","LOCK_NB","LOCK_UN"): setattr(_f,_n,0)
_f.flock = lambda *a,**k: None
_f.lockf = lambda *a,**k: None
sys.modules["fcntl"] = _f
import server
server.init_db()
app = server.app
app.config["PROPAGATE_EXCEPTIONS"] = False
c = app.test_client()
with c.session_transaction() as s:
    s["authenticated"] = True; s["is_admin"] = True; s["user_id"] = 1
r = c.get("/api/storage/app-info").get_json() or {}
print("VER=" + str(r.get("version")))
print("NAME=" + str(r.get("app_name")))
''' % BACKEND
    env = dict(os.environ)
    env.pop("BABYCARE_APP_VERSION", None)
    p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env, timeout=180)
    out = p.stdout or ""
    d = dict(re.findall(r"^([A-Z]+)=(.*)$", out, re.M))
    return d.get("VER", ""), d.get("NAME", "")


def main():
    bad = []
    print("=== 版本号一致性门禁 ===")
    if not os.path.exists(MANIFEST):
        print("[FAIL] 找不到 manifest:", MANIFEST)
        sys.exit(1)

    mv = read_manifest_version(MANIFEST)
    print("manifest version        =", repr(mv))
    if not mv:
        print("[FAIL] manifest 里没有非空 version —— 拿空串比空串会假绿，直接判红")
        sys.exit(1)

    # ---- ① 静态：constants.py 不许再写死版本号 ----
    src = io.open(CONSTANTS, "r", encoding="utf-8").read()
    # 允许出现 "0.0.1" 字样，但必须不是 APP_VERSION 的字面量赋值
    hard = re.findall(r'APP_VERSION\s*=\s*["\']([\d][^"\']*)["\']', src)
    ok_static = not hard
    print("  [%s] constants.py 无硬编码 APP_VERSION 字面量%s"
          % ("ok" if ok_static else "FAIL",
             "" if ok_static else "  ← 发现: %s" % hard))
    if not ok_static:
        bad.append("constants.py 仍硬编码 APP_VERSION: %s" % hard)
    if "_read_manifest_version" not in src:
        print("  [FAIL] constants.py 里没有从 manifest 读取版本的逻辑")
        bad.append("constants.py 缺 _read_manifest_version")
    else:
        print("  [ok]   constants.py 通过 manifest 读取版本")

    # ---- ② 动态：接口下发的 version == manifest ----
    api_v, api_name = probe_api_version()
    print("接口下发 version        =", repr(api_v))
    print("接口下发 app_name       =", repr(api_name))
    match = (api_v == mv)
    print("  [%s] 接口 version == manifest version" % ("ok" if match else "FAIL"))
    if not match:
        bad.append("接口 version(%r) != manifest(%r)" % (api_v, mv))

    # ---- ④ 负向对照：改 manifest，接口必须跟着变 ----
    tmp_manifest = MANIFEST + ".verchk"
    shutil.copy2(MANIFEST, tmp_manifest)
    try:
        body = io.open(MANIFEST, "r", encoding="utf-8", errors="replace").read()
        mutated = re.sub(r"^version=.*$", "version=9.9.9-gateprobe", body, count=1, flags=re.M)
        io.open(MANIFEST, "w", encoding="utf-8").write(mutated)
        api_v2, _ = probe_api_version()
        follows = (api_v2 == "9.9.9-gateprobe")
        print("  [%s] 负向对照：manifest 改成 9.9.9-gateprobe ⇒ 接口跟着变（实得 %r）"
              % ("ok" if follows else "FAIL", api_v2))
        if not follows:
            bad.append("接口 version 没有跟随 manifest —— 可能还是写死的（负向对照不转红）")
    finally:
        shutil.move(tmp_manifest, MANIFEST)
    # 还原确认
    restored = read_manifest_version(MANIFEST)
    if restored != mv:
        print("  [FAIL] manifest 未正确还原！实得 %r" % restored)
        bad.append("manifest 还原失败")

    print()
    if bad:
        print("版本号漂移 %d 处：" % len(bad))
        for b in bad:
            print("  - " + b)
        sys.exit(1)
    print("一致：设置页显示的应用版本与 manifest 真值相同，且确实来自 manifest")
    sys.exit(0)


if __name__ == "__main__":
    main()
