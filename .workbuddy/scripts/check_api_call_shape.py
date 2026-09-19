#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""前端 api() 调用形态检查。

`api(url, options = {})` 只接受**两个**参数。一旦被写成三参
`api(url, 'POST', data)`，第二个参数字符串会被对象展开成 `{0:'P',1:'O',…}`，
**method 和 body 双双丢失、请求退化成 GET**；而写接口只注册了 POST
→ 405，前端却只提示"操作失败"，很难定位。

2026-09-19 实测踩过：活动收藏 `api(url, method, data)` —— 收藏功能从来没成功过。

⚠️ 实现注意：**不要对整份源码做「剥注释/字符串」的预处理**。模板字符串里常含
嵌套引号（api 的 URL 大量是反引号模板串），朴素状态机一旦错位就会把整个文件当成
一个未闭合字符串吞掉 —— 表现为「共 0 处调用」，看着像"没问题"，实则扫描器废了。
这里改成只在 api( 之后做**局部**括号匹配，从根上避开这个坑。

用法：
    python .workbuddy/scripts/check_api_call_shape.py
退出码 0 = 通过，1 = 发现可疑调用。
"""
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(os.path.dirname(HERE))
JS_PATH = os.path.join(PROJ, "app", "frontend", "js", "app.js")
js = io.open(JS_PATH, "r", encoding="utf-8").read()


def args_of_call(src, open_paren):
    """从 '(' 的下一位开始，扫到配对的 ')'，返回括号内的原文。
    扫的时候跳过字符串/模板串（处理转义），但只在这段局部范围内，不会跑飞。"""
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


def top_args_count(s):
    """数顶层逗号 → 参数个数（跳过字符串）。"""
    depth = 0
    count = 1
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if ch in "'\"`":
            q = ch
            i += 1
            while i < n:
                if s[i] == "\\":
                    i += 2
                    continue
                if s[i] == q:
                    break
                i += 1
            i += 1
            continue
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == "," and depth == 0:
            count += 1
        i += 1
    return count


bad = []
total = 0
for m in re.finditer(r"(?<![\w.])api\s*\(", js):
    # 跳过注释行里的示例（行首是 // 的）
    line_start = js.rfind("\n", 0, m.start()) + 1
    if js[line_start:m.start()].strip().startswith("//"):
        continue
    op = js.index("(", m.start())
    args = args_of_call(js, op)
    if args is None:
        continue
    total += 1
    if top_args_count(args) > 2:
        line = js[:m.start()].count("\n") + 1
        bad.append((line, js.splitlines()[line - 1].strip()[:120]))

print("=== api() 调用形态检查（只接受两参：api(url, options)）===")
print("共扫到 %d 处调用，可疑 %d 处" % (total, len(bad)))
for line, txt in bad:
    print("  [FAIL] 第 %d 行 | %s" % (line, txt))

if bad:
    print()
    print("修法：api(url, { method: 'POST', body: JSON.stringify(data) })")
    sys.exit(1)
print("全部符合 api(url, options) 形态")
sys.exit(0)
