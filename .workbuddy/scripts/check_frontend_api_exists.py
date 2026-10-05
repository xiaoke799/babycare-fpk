#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xiaoke799
r"""前端 api() 调的每个接口，后端必须真的有这条路由。

为什么要有这个脚本（2026-10-05 实踩，代价是一整个页面报废）：
  「出牙记录」页（page-teeth）调的是 /api/babies/<id>/teeth，而写 baby_teeth 表的
  这三个接口在 7e2ba58 被当成「前端从未接入的死接口」删掉了 —— 删除时看的注释
  写反了：真正没被接入的是另一套 /teething，被删的才是页面唯一在用的数据层。
  结果：进页面列表永远空白、点牙位图**静默无反应**（前端只判 res.success、没有
  else）、删除只是本地数组少一条。它躲过了当时全部 49 道门禁，因为：
    - 静态检查只看 DOM id / 函数是否定义 —— 这两个都齐全；
    - 全路由冒烟只算 5xx —— 404 不算；
    - 记录页离线渲染只覆盖换尿布/趴睡两页。
  唯一能提前发现的角度就是：**拿前端真的会调的那些 URL 去 url_map 里问一句在不在**。

判据：
  ① 从 app.js 抓出所有 api(...) 的第一个参数，把模板变量还原成 *；
  ② 归一化成 /api/xxx/* 之后，必须能在 Flask url_map 里找到对应规则；
  ③ 抓到的 URL 少于 MIN_EXPECTED 直接判 FAIL —— 防止扫描器自己写废了还报「0 条」假绿。

跑法（必须用项目 venv，要 import server）：
  C:\Users\X\.workbuddy\binaries\python\envs\babycare\Scripts\python.exe \
      .workbuddy/scripts/check_frontend_api_exists.py
退出码：0 = 全部对得上；1 = 有前端调用找不到后端路由（或扫描器失效）。
"""
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
BACKEND = os.path.join(ROOT, "app", "backend")
JS_PATH = os.path.join(ROOT, "app", "frontend", "js", "app.js")
sys.path.insert(0, BACKEND)

# 只要拿 url_map，不要碰真实数据目录
_TMP = tempfile.mkdtemp(prefix="babycare_apiexist_")
os.environ["BABYCARE_DATA_DIR"] = _TMP
os.environ["BABYCARE_DB_PATH"] = os.path.join(_TMP, "babycare.db")
os.environ["BABYCARE_LOG_DIR"] = os.path.join(_TMP, "logs")
os.environ["BABYCARE_DEV_AUTH"] = "1"

_fcntl = type(sys)("fcntl")
for _n in ("LOCK_EX", "LOCK_SH", "LOCK_NB", "LOCK_UN"):
    setattr(_fcntl, _n, 0)
_fcntl.flock = lambda *a, **k: None
_fcntl.lockf = lambda *a, **k: None
sys.modules["fcntl"] = _fcntl

import server  # noqa: E402

MIN_EXPECTED = 50          # 实测 ~140 条；低于这个数说明抓取逻辑失效了


def args_of_call(src, open_paren):
    """从 '(' 的后一位扫到配对的 ')'(跳过字符串/注释)，返回括号内原文。

    与 check_api_call_shape.py 同一套局部括号匹配：不做整文件预处理，
    避免模板串里的嵌套引号把状态机带跑偏。
    """
    i = open_paren + 1
    depth = 1
    n = len(src)
    while i < n:
        ch = src[i]
        if ch in "'\"`":
            q = ch
            i += 1
            while i < n:
                if src[i] == "\\":
                    i += 2
                    continue
                if src[i] == q:
                    break
                i += 1
            i += 1
            continue
        if ch == "/" and i + 1 < n and src[i + 1] == "/":
            while i < n and src[i] != "\n":
                i += 1
            continue
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
            if depth == 0:
                return src[open_paren + 1:i]
        i += 1
    return None


def first_arg(args):
    """取顶层第一个逗号之前的原文。"""
    depth = 0
    i = 0
    n = len(args)
    while i < n:
        ch = args[i]
        if ch in "'\"`":
            q = ch
            i += 1
            while i < n:
                if args[i] == "\\":
                    i += 2
                    continue
                if args[i] == q:
                    break
                i += 1
            i += 1
            continue
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == "," and depth == 0:
            return args[:i]
        i += 1
    return args


def url_of_arg(arg):
    """把参数原文压成一条路径：

    'literal' / `template` 里的字符保留（${...} 变 *），引号外的拼接部分变 *，
    这样 '/api/babies/' + id + '/teeth' 也能还原成 /api/babies/*/teeth。
    """
    out = []
    i = 0
    n = len(arg)
    while i < n:
        ch = arg[i]
        if ch in "'\"`":
            q = ch
            i += 1
            while i < n:
                if arg[i] == "\\":
                    out.append(arg[i:i + 2])
                    i += 2
                    continue
                if arg[i] == q:
                    break
                if q == "`" and arg[i] == "$" and arg[i + 1:i + 2] == "{":
                    depth = 1
                    i += 2
                    while i < n and depth:
                        if arg[i] == "{":
                            depth += 1
                        elif arg[i] == "}":
                            depth -= 1
                        i += 1
                    out.append("*")
                    continue
                out.append(arg[i])
                i += 1
            i += 1
            continue
        out.append("*")
        i += 1
    joined = "".join(out)
    idx = joined.find("/api/")
    if idx < 0:
        return None
    path = joined[idx:]
    # 去掉查询串与哈希，占位符/数字段统一成 *
    path = path.split("?")[0].split("#")[0]
    path = re.sub(r"/\d+(?=/|$)", "/*", path)
    path = re.sub(r"\*+", "*", path)      # 连续占位符合并成一个
    path = re.sub(r"/+", "/", path)
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    return path


def norm_rule(rule):
    rule = re.sub(r"<[^>]+>", "*", rule)
    rule = re.sub(r"/\d+(?=/|$)", "/*", rule)
    if len(rule) > 1 and rule.endswith("/"):
        rule = rule[:-1]
    return rule


js = open(JS_PATH, "r", encoding="utf-8").read()

# 路由按「段」比对：路由里的占位符段（*）可以匹配前端的任意一段。
# 只做整串相等是不够的 —— 后端 /api/babies/<id>/pattern/<string:pattern_type>
# 归一成 /api/babies/*/pattern/*，前端调的是 /api/babies/1/pattern/sleep，
# 整串比会误报成「后端没这条路由」。
backend_segs = [tuple(norm_rule(r.rule).split("/")) for r in server.app.url_map.iter_rules()]


def route_exists(path):
    segs = tuple(path.split("/"))
    for rule in backend_segs:
        if len(rule) != len(segs):
            continue
        if all(a == "*" or a == b for a, b in zip(rule, segs)):
            return True
    return False


def is_fully_dynamic(path):
    """'/api/*/*' 这种：除了 /api 一句静态信息都没有，静态检查无能为力，跳过。"""
    return all(s == "*" for s in path.split("/")[2:])


calls = []          # (行号, 归一化路径, 原始源码行)
skipped = 0
for m in re.finditer(r"(?<![\w.])api\s*\(", js):
    line_start = js.rfind("\n", 0, m.start()) + 1
    if js[line_start:m.start()].strip().startswith("//"):
        continue
    op = js.index("(", m.start())
    args = args_of_call(js, op)
    if args is None:
        continue
    path = url_of_arg(first_arg(args))
    line_no = js[:m.start()].count("\n") + 1
    if path is None or is_fully_dynamic(path):
        skipped += 1
        continue
    calls.append((line_no, path, js.splitlines()[line_no - 1].strip()))

print("=== 前端 api() 调用 → 后端路由存在性检查 ===")
print("后端路由: %d 条" % len(backend_segs))
print("前端 api() 调用（可解析出路径）: %d 条，另有 %d 条路径全由变量拼成（跳过）"
      % (len(calls), skipped))

missing = [(ln, p, src) for (ln, p, src) in calls if not route_exists(p)]
uniq_missing = sorted({p for (_, p, _) in missing})
print("对不上的路径: %d 条" % len(uniq_missing))
for p in uniq_missing:
    lines = [ln for (ln, pp, _) in calls if pp == p]
    print("  [FAIL] %s   调用行 %s" % (p, lines))
    for (ln, pp, src) in calls:
        if pp == p:
            print("          %d: %s" % (ln, src[:150]))

if len(calls) < MIN_EXPECTED:
    print()
    print("[FAIL] 只解析出 %d 条调用（预期 ≥ %d）—— 扫描器自己失效了，"
          "这种「0 条失败」不算通过" % (len(calls), MIN_EXPECTED))
    sys.exit(1)

if uniq_missing:
    print()
    print("修法二选一：① 把前端 URL 改成后端真实存在的路由；")
    print("          ② 后端补上这条路由（补之前先 grep 确认它是不是被谁当死接口删过）。")
    sys.exit(1)

print("全部对得上：前端调用的每个接口后端都有")
sys.exit(0)
