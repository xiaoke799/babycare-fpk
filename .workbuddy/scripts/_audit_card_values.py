# -*- coding: utf-8 -*-
"""块级卡片 padding / margin-bottom 分裂清单（只读分析，放宽筛选）"""
import re

PATH = r"D:\育儿板块\babycare-fpk\app\frontend\css\main.css"
lines = open(PATH, encoding="utf-8").readlines()

rules = []
cur, depth, buf, start = None, 0, [], 0
for i, ln in enumerate(lines, 1):
    s = ln.strip()
    if depth == 0 and "{" in ln and not s.startswith("/*"):
        cur = s.split("{")[0].strip()
        depth = ln.count("{")
        buf = [ln.split("{", 1)[1]] if "{" in ln.split("{", 1)[1] else []
        start = i
        rest = ln.split("{", 1)[1]
        if "}" in rest:
            rules.append((cur, start, rest.split("}")[0]))
            cur, depth = None, 0
        else:
            buf = [rest]
    elif depth > 0:
        depth += ln.count("{") - ln.count("}")
        buf.append(ln)
        if depth <= 0:
            rules.append((cur, start, "\n".join(buf)))
            cur, depth = None, 0

groups = {}
for sel, no, body in rules:
    if not sel or sel.startswith("@"):
        continue
    base = sel.split(",")[0].strip().split()[-1].split(">")[-1].split(":")[0].lstrip(".").lstrip("#")
    # 块级卡片：类名以 card/panel/box 结尾，或已知大卡
    if not re.search(r"(card|panel|-box|summary|overview|reminders|backup-item|aiw-card)$", base):
        continue
    # 排除卡片内部小元素
    if re.search(r"(title|desc|icon|head|header|badge|tag|hint|value|num|label|text|btn|item|row|chip|bar|track|fill|dot|avatar|preview|img|img)$", base) and "card" not in base.split("-")[-1]:
        if not base.endswith("card"):
            continue
    has_card_look = re.search(r"background|box-shadow|border", body)
    if not has_card_look:
        continue
    p = re.search(r"padding:\s*([^;]+);", body)
    m = re.search(r"margin-bottom:\s*([^;]+);", body)
    r = re.search(r"border-radius:\s*([^;]+);", body)
    key = p.group(1).strip() if p else "(无padding)"
    groups.setdefault(key, []).append(f"{sel.split(',')[0].strip()[:52]} L{no}" + (f" [r={r.group(1).strip()}]" if r else "") + (f" [mb={m.group(1).strip()}]" if m else ""))

for key in sorted(groups, key=lambda k: -len(groups[k])):
    print(f"--- padding = {key}  ({len(groups[key])} 处) ---")
    for n in groups[key]:
        print("   ", n)
