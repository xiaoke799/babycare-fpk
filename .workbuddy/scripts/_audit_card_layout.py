# -*- coding: utf-8 -*-
"""侦察 main.css 卡片类排版值分布（只读分析，不改文件）"""
import re
from collections import Counter

PATH = r"D:\育儿板块\babycare-fpk\app\frontend\css\main.css"
lines = open(PATH, encoding="utf-8").readlines()

pad_c, rad_c, mb_c = Counter(), Counter(), Counter()
cur = None
depth = 0
for ln in lines:
    s = ln.strip()
    if "{" in ln:
        cur = s.split("{")[0].strip()
        depth += ln.count("{")
    depth -= ln.count("}")
    if depth < 0:
        depth = 0
    if not cur or cur.startswith("@") or depth < 1:
        continue
    if not re.search(r"card", cur):
        continue
    p = re.match(r"padding:\s*([^;]+);", s)
    r = re.match(r"border-radius:\s*([^;]+);", s)
    m = re.match(r"margin-bottom:\s*([^;]+);", s)
    sel = cur.split(",")[0].strip()
    if p:
        pad_c[p.group(1).strip()] += 1
    if r:
        rad_c[r.group(1).strip()] += 1
    if m:
        mb_c[m.group(1).strip()] += 1

print("=== 卡片类 padding（规则数）===")
for v, c in pad_c.most_common(16):
    print(f"{c:3d}  {v}")
print("=== 卡片类 border-radius ===")
for v, c in rad_c.most_common(16):
    print(f"{c:3d}  {v}")
print("=== 卡片类 margin-bottom ===")
for v, c in mb_c.most_common(16):
    print(f"{c:3d}  {v}")
