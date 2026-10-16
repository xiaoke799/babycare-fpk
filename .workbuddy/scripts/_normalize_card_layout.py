# -*- coding: utf-8 -*-
"""块级卡片排版归一：padding / border-radius / margin-bottom 批量定点替换。
每处替换打印核对；任何一处没命中立即报错退出，防静默漏改。
"""
import sys

PATH = r"D:\育儿板块\babycare-fpk\app\frontend\css\main.css"
with open(PATH, encoding="utf-8") as f:
    lines = f.readlines()

def find_rule_block(start_line):
    """从 1-based start_line（选择器行）起，返回规则块末行（含闭括号）的 1-based 行号"""
    i = start_line - 1
    depth = 0
    opened = False
    while i < len(lines):
        depth += lines[i].count("{") - lines[i].count("}")
        if "{" in lines[i]:
            opened = True
        if opened and depth <= 0:
            return i + 1
        i += 1
    raise RuntimeError(f"L{start_line}: 规则块未闭合")

def replace_in_block(sel, start_line, old, new, expect=1):
    """在 start_line 起的规则块内把 `old;` 替换为 `new;`，命中数必须等于 expect"""
    end = find_rule_block(start_line)
    hit = 0
    for i in range(start_line - 1, end):
        if lines[i].strip() == old + ";":
            indent = lines[i][: len(lines[i]) - len(lines[i].lstrip())]
            lines[i] = indent + new + ";\n"
            hit += 1
    if hit != expect:
        print(f"FAIL L{start_line} {sel}: 期望 {expect} 处 '{old}'，实际命中 {hit}")
        sys.exit(1)
    print(f"OK   L{start_line:6d} {sel:34s} {old}  ->  {new}")

# (选择器, 规则起始行, 旧值, 新值)
TASKS = [
    # A. padding 14px -> 16px
    (".stat-card", 428, "padding: 14px", "padding: 16px"),
    (".milestone-card", 4029, "padding: 14px", "padding: 16px"),
    (".wiki-card", 5016, "padding: 14px", "padding: 16px"),
    (".recipe-card", 5485, "padding: 14px", "padding: 16px"),
    (".sf-stat-card", 6572, "padding: 14px", "padding: 16px"),
    (".gp-compare-panel", 7019, "padding: 14px", "padding: 16px"),
    (".recipe-card", 7935, "padding: 14px", "padding: 16px"),
    (".activity-card", 8532, "padding: 14px", "padding: 16px"),
    (".teeth-item-card", 9379, "padding: 14px", "padding: 16px"),
    (".health-item-card", 9466, "padding: 14px", "padding: 16px"),
    (".health-overview-card", 9603, "padding: 14px", "padding: 16px"),
    (".checkup-card", 9816, "padding: 14px", "padding: 16px"),
    (".tummytime-item-card", 10810, "padding: 14px", "padding: 16px"),
    (".allergy-item-card", 10963, "padding: 14px", "padding: 16px"),
    (".diaper-price-item-card", 11080, "padding: 14px", "padding: 16px"),
    (".med-reminder-item-card", 11197, "padding: 14px", "padding: 16px"),
    (".report-stat-card", 11509, "padding: 14px", "padding: 16px"),
    (".ledger-trend-card", 12540, "padding: 14px", "padding: 16px"),
    # B. padding 14px 16px -> 16px
    (".sleep-goal-card", 4148, "padding: 14px 16px", "padding: 16px"),
    (".sleep-weekly-summary", 4357, "padding: 14px 16px", "padding: 16px"),
    (".sleep-reference-card", 4457, "padding: 14px 16px", "padding: 16px"),
    (".vaccine-card", 10243, "padding: 14px 16px", "padding: 16px"),
    (".screening-card", 10394, "padding: 14px 16px", "padding: 16px"),
    (".allergy-card", 10461, "padding: 14px 16px", "padding: 16px"),
    (".feeding-card", 10503, "padding: 14px 16px", "padding: 16px"),
    (".aiw-card", 14993, "padding: 14px 16px", "padding: 16px"),
    # C. padding 12px -> 16px（主卡）
    (".medication-card", 5195, "padding: 12px", "padding: 16px"),
    (".tummy-time-card", 5269, "padding: 12px", "padding: 16px"),
    (".allergy-card", 5340, "padding: 12px", "padding: 16px"),
    (".diaper-card", 5431, "padding: 12px", "padding: 16px"),
    (".activity-recommend-card", 8454, "padding: 12px", "padding: 16px"),
    (".report-summary-card", 10629, "padding: 12px", "padding: 16px"),
    # D. padding 20px -> 16px（计时器/步骤大卡对齐）
    (".sleep-timer-card", 4399, "padding: 20px", "padding: 16px"),
    (".tummytime-timer-card", 10746, "padding: 20px", "padding: 16px"),
    (".cooking-step-card", 8058, "padding: 20px", "padding: 16px"),
    # E. 写死圆角 -> var(--radius)
    (".dashboard-growth-card", 3838, "border-radius: 12px", "border-radius: var(--radius)"),
    (".dashboard-reminders", 3884, "border-radius: 12px", "border-radius: var(--radius)"),
    (".ai-insight-card", 7817, "border-radius: 12px", "border-radius: var(--radius)"),
    (".screening-summary-card", 10384, "border-radius: 8px", "border-radius: var(--radius)"),
    (".report-summary-card", 11467, "border-radius: 12px", "border-radius: var(--radius)"),
    (".cooking-step-card", 8058, "border-radius: 12px", "border-radius: var(--radius)"),
    (".activity-recommend-card", 8454, "border-radius: 10px", "border-radius: var(--radius)"),
    (".ledger-summary-card", 12692, "border-radius: 14px", "border-radius: var(--radius)"),
    (".ledger-trend-card", 12758, "border-radius: 14px", "border-radius: var(--radius)"),
    (".ai-analysis-panel", 7666, "border-radius: 16px", "border-radius: var(--radius)"),
    # F. 小统计卡圆角 -> var(--radius-sm)
    (".activity-stat-card", 8833, "border-radius: 8px", "border-radius: var(--radius-sm)"),
    (".report-stat-card", 11509, "border-radius: 12px", "border-radius: var(--radius-sm)"),
    # G. margin-bottom -> 16px（页面级区块卡）
    (".aiw-card", 14993, "margin-bottom: 14px", "margin-bottom: 16px"),
    (".mg-card", 14595, "margin-bottom: 14px", "margin-bottom: 16px"),
    (".ledger-summary-card", 12692, "margin-bottom: 12px", "margin-bottom: 16px"),
    (".ledger-trend-card", 12758, "margin-bottom: 12px", "margin-bottom: 16px"),
    # H. aiw-header 垂直对齐与其他页统一
    (".aiw-header", 14979, "align-items: baseline", "align-items: center"),
]

for sel, ln, old, new in TASKS:
    replace_in_block(sel, ln, old, new)

with open(PATH, "w", encoding="utf-8", newline="") as f:
    f.writelines(lines)

print(f"\n共 {len(TASKS)} 处替换完成，已写回。")
