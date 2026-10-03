#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""打包卫生：把不该进 fpk 的三类文件移出 app/，打完包再移回。

背景（2026-10-03 审查发现）
------------------------
fnpack **没有排除机制**，它把 app/ 整个目录打包、**不读 .gitignore**。于是
下面三类"被忽略但物理还在"的文件会被塞进正式件（实测曾占内层 55%）：

  - app/backend/__pycache__/     365 个 .pyc（8.4MB，312+313 两套）
  - app/data/                    真实 babycare.db + 备份库 + 日志 + secret_key
  - app/backend/verify_*.py      测试脚本（32 个）

运行时数据目录走 TRIM_PKGVAR（见 cmd/main），包内 data/ 不会被读，
故这几类是**打包污染/隐私泄露**（解包即可看到作者真实数据与密钥），不是数据串号。

用法（插进打包标准链）
--------------------
    python .workbuddy/scripts/app_hygiene.py --check      # 打包前守卫：不干净则 exit 1
    python .workbuddy/scripts/app_hygiene.py --stage-out  # 移出三类到 .workbuddy/pack_stash/
    #   ... fnpack build / 归位 / fix_fpk_exec_bits / verify_fpk_contents ...
    python .workbuddy/scripts/app_hygiene.py --stage-in   # 从暂存移回

无参数时等价于 --check。
"""
import argparse
import glob
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
APP = os.path.join(ROOT, "app")
STASH = os.path.join(ROOT, ".workbuddy", "pack_stash")
MANIFEST = os.path.join(STASH, "manifest.json")


def _targets():
    """要移出的目标：[(stash 内扁平名, app 内绝对路径)]。

    ⚠ __pycache__ 不止 backend 顶层一个：blueprints/ notifier/ seeds/ 以及
    vendor/** 下还有 20 个（vendor 的依赖缓存最多，约占 300 个 pyc）。
    必须递归找全，否则会漏进正式件（2026-10-03 首次清理就漏了，包内仍剩 311 个）。
    """
    out = []
    for dirpath, dirnames, _files in os.walk(APP):
        if os.path.basename(dirpath) == "__pycache__":
            rel = os.path.relpath(dirpath, APP).replace(os.sep, "__")
            out.append(("pycache__" + rel, dirpath))
            dirnames[:] = []  # __pycache__ 内不再有嵌套
    data = os.path.join(APP, "data")
    if os.path.isdir(data):
        out.append(("app__data", data))
    for p in sorted(glob.glob(os.path.join(APP, "backend", "verify_*.py"))):
        out.append(("verify__" + os.path.basename(p), p))
    return sorted(out, key=lambda x: x[1])


def check(informational=False):
    t = _targets()
    if not t:
        print("[OK] app/ 干净：无 __pycache__ / data / verify_*.py")
        return 0
    tag = "[INFO]" if informational else "[FAIL]"
    print("%s app/ 下有 %d 项会被 fnpack 打进正式件：" % (tag, len(t)))
    for _name, p in t:
        print("   - " + os.path.relpath(p, ROOT))
    if informational:
        # 无参数调用（如被 _run_gates.py 扫到）只提示、不判失败：
        # 开发状态下 app/ 本就有 __pycache__，若这里返回非零会让回归门禁长期误红。
        print("（硬守卫请用 --check；清理用 --stage-out）")
        return 0
    print("打包前请先跑：python .workbuddy/scripts/app_hygiene.py --stage-out")
    return 1


def stage_out():
    if os.path.exists(MANIFEST):
        print("[ERROR] 已有未恢复的暂存（%s）。请先 --stage-in 再重新 --stage-out。"
              % os.path.relpath(MANIFEST, ROOT))
        return 1
    t = _targets()
    if not t:
        print("[SKIP] app/ 已干净，无需移出。")
        return 0
    os.makedirs(STASH, exist_ok=True)
    moved = []
    for name, p in t:
        dst = os.path.join(STASH, name)
        shutil.move(p, dst)
        moved.append({"name": name, "orig": os.path.relpath(p, ROOT)})
        print("  移出 " + os.path.relpath(p, ROOT))
    with open(MANIFEST, "w", encoding="utf-8") as f:
        json.dump(moved, f, ensure_ascii=False, indent=2)
    print("[OK] 已移出 %d 项到 %s" % (len(moved), os.path.relpath(STASH, ROOT)))
    return 0


def stage_in():
    if not os.path.exists(MANIFEST):
        print("[SKIP] 没有待恢复的暂存。")
        return 0
    with open(MANIFEST, encoding="utf-8") as f:
        moved = json.load(f)
    n = 0
    for item in moved:
        src = os.path.join(STASH, item["name"])
        dst = os.path.join(ROOT, item["orig"])
        if not os.path.exists(src):
            print("[WARN] 暂存缺失，跳过：%s" % item["name"])
            continue
        if os.path.exists(dst):
            print("[WARN] 目标已存在，跳过：%s" % item["orig"])
            continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.move(src, dst)
        n += 1
        print("  恢复 " + item["orig"])
    os.remove(MANIFEST)
    try:
        if not os.listdir(STASH):
            os.rmdir(STASH)
    except OSError:
        pass
    print("[OK] 已恢复 %d 项" % n)
    return 0


def main():
    ap = argparse.ArgumentParser(description="打包卫生：移出/恢复 app/ 下不该进包的文件")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--check", action="store_true", help="硬守卫：app/ 不干净则 exit 1（打包前用）")
    g.add_argument("--stage-out", action="store_true", help="把不该进包的移出到 .workbuddy/pack_stash/")
    g.add_argument("--stage-in", action="store_true", help="从暂存恢复回 app/")
    a = ap.parse_args()
    if a.stage_out:
        return stage_out()
    if a.stage_in:
        return stage_in()
    if a.check:
        return check()
    # 无参数 = 信息模式（只提示、恒 exit 0）
    return check(informational=True)


if __name__ == "__main__":
    sys.exit(main())
