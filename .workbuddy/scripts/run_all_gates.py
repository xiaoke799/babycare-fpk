#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批量跑项目门禁，流式打印 + 落盘。

扫描时**显式跳过自己**（否则它会把自己也跑一遍，递归下去）。

要点（都是踩过的）：
- **流式打印**：以前的版本只在最后落盘，跑 30 分钟期间完全看不到进度，
  卡住时只能靠 tasklist 猜在跑哪一条。
- **每条约 600s 上限**：`smoke_all_routes` 这种全路由穷举本来就要 3~5 分钟，
  上限给 300s 会把它长期卡在超时线上（时灵时不灵的门禁比没有门禁更贵）。
  它已经做过一轮瘦身（GET 不再重复跑 4 种 body），但仍留足余量。
- 扫 `app/backend/verify_*.py` 与 `.workbuddy/scripts/*.py`（跳过 `_` 开头与自身）。
- ⚠ 文件名不能用 `_` 开头：`.gitignore` 有 `_*.py`，会进不了版本库。

跑法：
  C:\\Users\\X\\.workbuddy\\binaries\\python\\envs\\babycare\\Scripts\\python.exe \\
      .workbuddy/scripts/run_all_gates.py
退出码：0 = 全绿；1 = 有红。
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PY = sys.executable
PER_GATE_TIMEOUT = 600

TARGETS = []
backend = os.path.join(ROOT, "app", "backend")
for name in sorted(os.listdir(backend)):
    if name.startswith("verify_") and name.endswith(".py"):
        TARGETS.append(("backend/" + name, backend, os.path.join(backend, name)))

scripts = os.path.join(ROOT, ".workbuddy", "scripts")
for name in sorted(os.listdir(scripts)):
    if name.endswith(".py") and not name.startswith("_") and name != "run_all_gates.py":
        TARGETS.append(("scripts/" + name, ROOT, os.path.join(scripts, name)))

print("=" * 78)
print(f"共 {len(TARGETS)} 条门禁，每条上限 {PER_GATE_TIMEOUT}s")
print("=" * 78)

lines = []
results = []
for idx, (label, cwd, path) in enumerate(TARGETS, 1):
    try:
        p = subprocess.run([PY, path], cwd=cwd, capture_output=True, text=True,
                           timeout=PER_GATE_TIMEOUT, encoding="utf-8", errors="replace")
        code = p.returncode
        out = (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        code, out = -99, "TIMEOUT"
    except Exception as e:
        code, out = -98, repr(e)
    flag = "PASS" if code == 0 else "FAIL"
    # 流式：每条立刻打一行，跑的过程中就能看出卡在哪
    print(f"[{flag}] rc={code}  {label}   ({idx}/{len(TARGETS)})", flush=True)
    if code != 0:
        tail = [ln for ln in out.splitlines() if ln.strip()][-8:]
        for ln in tail:
            print("        | " + ln[:200], flush=True)
    results.append((label, code))
    lines.append(f"[{flag}] rc={code}  {label}")

bad = [lab for lab, code in results if code != 0]
text = "\n".join(lines + ["=" * 78,
                          f"total={len(results)} pass={len(results) - len(bad)} fail={len(bad)}",
                          "FAILS: " + (", ".join(bad) if bad else "none")])
print("\n" + text, flush=True)
with open(os.path.join(ROOT, ".workbuddy", "tmp", "gates_now.txt"), "w",
          encoding="utf-8") as f:
    f.write(text)
sys.exit(1 if bad else 0)
