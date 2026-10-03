#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""CSS 同名选择器重复定义门禁。

为什么需要它
------------
本项目 main.css 是单文件、已 2600+ 行。同名选择器在**同一上下文**里被定义两次时：

  * 靠后的那条会静默覆盖靠前的那条（同特异性取后者）；
  * 但**只覆盖双方都声明了的属性** —— 靠前那条独有的属性会"漏"进去，
    于是出现"样式看起来对、其实混了两条规则"的诡异结果；
  * 如果新写的一条在实际顺序上更靠前，作者完全不会察觉自己被覆盖了。

2026-10-03 真实踩过：新增的 `.settings-group-title`（带 `::before` 色条、
`font-size:12.5px`）被后文的同名老规则盖掉，色条还外溢到了推送通知卡上。

判定口径
--------
* **同一上下文内**重复才算错：顶层 vs 顶层；同一个 `@media (...)` 块内 vs 它自己。
  跨上下文重复是合法的响应式/主题覆盖（例如顶层一条 + `@media` 里一条）。
* `@keyframes` / `@font-face` 内部的规则（`0%`、`from`、`to` 等）跳过。
* 逗号分隔的选择器组拆开逐个比较（`.a, .b {}` 与 `.b {}` 视为 `.b` 重复）。
* 选择器做空白归一化后再比较。
* 已知的历史重复写在 `check_css_dupes.baseline.txt` 里，只对**新增**重复报错，
  避免"常驻红灯淹没真错误"。

用法
----
    python check_css_dupes.py [css文件路径]

不带参数默认检查 `app/frontend/css/main.css`（相对仓库根）。
返回码：0 = 无新增重复；1 = 有新增重复或基线文件缺失。
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT = os.path.join(ROOT, "app", "frontend", "css", "main.css")
BASELINE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "check_css_dupes.baseline.txt")

SKIP_AT = re.compile(r"@(?:-\w+-)?(?:keyframes|font-face)")


def strip_comments(text):
    """去掉 CSS 注释，避免注释里的花括号干扰扫描。"""
    return re.sub(r"/\*.*?\*/", "", text, flags=re.S)


def scan(text):
    """扫描 CSS，产出 (上下文, 选择器, 行号)。

    关键点：**每个 `{` 都要入栈，每个 `}` 都要出栈**。
    早先只给 at-rule 入栈、却对规则的花括号也出栈，导致
    `@keyframes x { from { } to { } }` 里的 `to` 被误判成顶层选择器；
    更普遍的是 `@media { .a { } .b { } }` 里 `.b` 被误判成顶层
    （媒体块被 `.a` 的 `}` 提前弹掉），从而整片假阳性。
    """
    text = strip_comments(text)
    stack = []      # 每项 ('at', 文本) 或 ('rule', '')
    buf = []
    line = 1
    buf_line = 1
    for ch in text:
        if ch == "\n":
            line += 1
            continue
        if ch == "{":
            raw = " ".join("".join(buf).split())
            buf = []
            buf_line = line
            if not raw:
                stack.append(("rule", ""))
                continue
            if raw.startswith("@"):
                stack.append(("at", raw))
                continue
            ctx = " || ".join(t for k, t in stack if k == "at")
            stack.append(("rule", ""))
            if any(SKIP_AT.match(t) for k, t in stack[:-1] if k == "at"):
                continue
            yield ctx, raw, buf_line
        elif ch == "}":
            if stack:
                stack.pop()
            buf = []
            buf_line = line
        else:
            buf.append(ch)


def split_selectors(raw):
    out = []
    for part in raw.split(","):
        s = " ".join(part.split()).strip()
        if s:
            out.append(s)
    return out


def collect(path):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    seen = {}
    dupes = []
    total = 0
    for ctx, raw, ln in scan(text):
        total += 1
        for sel in split_selectors(raw):
            key = (ctx, sel)
            if key in seen:
                dupes.append((ctx, sel, seen[key], ln))
            else:
                seen[key] = ln
    return total, len(seen), dupes


def key_of(ctx, sel):
    return ("%s|%s" % (ctx, sel)).strip()


def load_baseline():
    if not os.path.isfile(BASELINE):
        return None
    keys = set()
    with open(BASELINE, encoding="utf-8") as fh:
        for ln in fh:
            ln = ln.strip()
            if ln and not ln.startswith("#"):
                keys.add(ln)
    return keys


def main():
    path = next((a for a in sys.argv[1:] if not a.startswith("--")), DEFAULT)
    if not os.path.isfile(path):
        print("[FAIL] 找不到文件：%s" % path)
        return 1

    total, uniq, dupes = collect(path)
    rel = os.path.relpath(path, ROOT) if path.startswith(ROOT) else path
    print("=== CSS 重复定义检查：%s ===" % rel)
    print("规则块 %d 个，唯一 (上下文,选择器) %d 个，重复 %d 处" % (total, uniq, len(dupes)))

    base = load_baseline()

    if "--write-baseline" in sys.argv:
        with open(BASELINE, "w", encoding="utf-8") as fh:
            fh.write("# check_css_dupes 已知历史重复（按「上下文|选择器」记录）\n")
            fh.write("# 只需删行即可让该条重新进入门禁；新增重复会自动报错。\n")
            for ctx, sel, _f, _a in sorted(dupes, key=lambda d: key_of(d[0], d[1])):
                fh.write(key_of(ctx, sel) + "\n")
        print("[OK] 已写入基线：%d 条 → %s"
              % (len(dupes), os.path.relpath(BASELINE, ROOT)))
        return 0

    if base is None:
        print("[FAIL] 缺少基线文件：%s" % os.path.relpath(BASELINE, ROOT))
        print("      历史重复不追责，但必须有基线，否则每次都是常驻红灯。")
        print("      生成方式：python check_css_dupes.py --write-baseline")
        return 1

    new = [d for d in dupes if key_of(d[0], d[1]) not in base]
    if not new:
        print("[PASS] 无新增重复定义（历史 %d 条已登记在基线）" % len(dupes))
        return 0

    print("[FAIL] 发现 %d 处**新增**重复定义：" % len(new))
    for ctx, sel, first, again in new:
        where = "顶层" if not ctx else ctx
        print("  - %s  |  %s" % (where, sel))
        print("      首次 L%d，重复 L%d" % (first, again))
    print()
    print("修法：① 合并成一条并保留两份的全部属性；或")
    print("      ② 若两条意图不同（如只作用于新分组），给新的一条换**更具体的选择器**")
    print("         （如 `.a > .b`），并显式清掉老规则里会漏进来的属性 ——")
    print("         特异性只在「双方都声明该属性」时才有意义，不写照样漏。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
