#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xiaoke799
"""路由覆盖差集：找出「后端有、但任何自检脚本都没碰过」的接口。

用途：既有项目里没被自检覆盖的接口 = 未验证功能的高发区。
本项目的用法是拿它排优先级：先补「有 UI、用户天天点」的那些（删除、设置、备份）。

用法：
  python .workbuddy/scripts/route_coverage.py
退出码始终为 0（这是分析工具，不是门禁）。

注意：脚本里写的是**字面量 id**（/api/babies/1/feeding），路由是 <int:baby_id>，
所以归一化必须把「纯数字路径段」也换成 *，否则会把已覆盖的路由误报成未覆盖
（2026-09-19 踩过：没归一时未覆盖数虚高到 159，归一后是 85）。
"""
import glob
import io
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)                       # .workbuddy
ROOT = os.path.dirname(APP)                       # babycare-fpk
BE = os.path.join(ROOT, "app", "backend")

VERIFY_DIRS = [
    os.path.join(ROOT, ".workbuddy", "scripts"),
    BE,
    os.path.join(os.path.dirname(ROOT), ".workbuddy", "scripts"),  # 工作区级脚本
]


def norm(url):
    url = re.sub(r"<[^>]+>", "*", url)
    url = re.sub(r"\{[^}]*\}", "*", url)
    url = url.split("?")[0]
    url = re.sub(r"/\d+(?=/|$)", "/*", url)
    if len(url) > 1 and url.endswith("/"):
        url = url[:-1]
    return url


routes = {}
for p in glob.glob(os.path.join(BE, "**", "*.py"), recursive=True):
    if os.sep + "vendor" + os.sep in p or os.sep + "__pycache__" + os.sep in p:
        continue
    src = io.open(p, encoding="utf-8", errors="replace").read()
    for m in re.finditer(r"@\w+\.route\(\s*[\"'](/[^\"']*)[\"']([^)]*)\)", src):
        url = norm(m.group(1))
        meths = re.findall(r"[\"'](GET|POST|PUT|DELETE|PATCH)[\"']", m.group(2))
        routes.setdefault(url, set()).update(meths or {"GET"})

covered = set()
files = 0
for d in VERIFY_DIRS:
    for p in glob.glob(os.path.join(d, "verify_*.*")):
        files += 1
        src = io.open(p, encoding="utf-8", errors="replace").read()
        for m in re.finditer(r"[\"'](/api/[^\"'\s]*)", src):
            covered.add(norm(m.group(1)))
        for m in re.finditer(r"[\"'](/api/[^\"']*)[\"']\s*\+", src):
            covered.add(norm(m.group(1)) + "*")

print("后端路由数:", len(routes))
print("自检脚本文件数:", files, " 其中出现的 URL 数:", len(covered))


def hit(url):
    if url in covered:
        return True
    return any(c.endswith("*") and url.startswith(c[:-1]) for c in covered)


never = sorted(u for u in routes if not hit(u))
print()
print("从未被任何自检脚本触及的路由（%d 个）:" % len(never))
for u in never:
    print("   %-6s %s" % ("/".join(sorted(routes[u])), u))
