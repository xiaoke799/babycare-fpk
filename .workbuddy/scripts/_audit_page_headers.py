# -*- coding: utf-8 -*-
"""对比所有页面级 header / 区块标题的排版一致性（只读分析）"""
import re

PATH = r"D:\育儿板块\babycare-fpk\app\frontend\css\main.css"
css = open(PATH, encoding="utf-8").read()
lines = css.splitlines()

# 页面级 header（排除条目内部 header：item-header/care-item/diary-item/llm-test/storage-health/modal/correlation/photos-header-actions）
page_headers = [
    "hub-header", "settings-header", "diary-header", "knowledge-header",
    "photos-header", "vaccines-header", "dashboard-header", "milestones-header",
    "sleep-analysis-header", "temperature-header", "wiki-header", "pattern-header",
    "medication-header", "tummy-time-header", "allergy-header", "tools-header",
    "quick-actions-header", "asq-header", "teething-header", "checkup-header",
    "solidfood-header", "leap-header", "fontanelle-header", "sounds-header",
    "timeline-header", "comparison-header", "aiw-header",
]

def find_block(cls):
    """返回 .cls { ... } 的声明体（首个匹配）"""
    pat = re.compile(r"^[.#][\w-]*" + re.escape(cls) + r"[\w-]*\s*\{")
    for i, ln in enumerate(lines):
        if pat.match(ln.strip()) and ln.strip().startswith("." + cls) and "{" in ln:
            body = []
            j = i
            depth = 0
            while j < len(lines):
                depth += lines[j].count("{") - lines[j].count("}")
                body.append(lines[j])
                if depth <= 0 and j > i:
                    break
                if depth <= 0 and "{" in lines[j] and "}" in lines[j]:
                    break
                j += 1
            return "\n".join(body)
    return None

print(f"{'header':24s} {'padding':20s} {'margin':18s} {'display':14s} {'align':12s}")
print("-" * 92)
for cls in page_headers:
    block = find_block(cls)
    if not block:
        print(f"{cls:24s} (无独立规则)")
        continue
    pad = re.search(r"padding:\s*([^;]+);", block)
    mar = re.search(r"margin:\s*([^;]+);", block)
    disp = re.search(r"display:\s*([^;]+);", block)
    align = re.search(r"align-items:\s*([^;]+);", block)
    just = re.search(r"justify-content:\s*([^;]+);", block)
    print(f"{cls:24s} {pad.group(1).strip() if pad else '-':20s} {mar.group(1).strip() if mar else '-':18s} {disp.group(1).strip() if disp else '-':14s} {(align.group(1).strip() if align else (just.group(1).strip() if just else '-')):12s}")
