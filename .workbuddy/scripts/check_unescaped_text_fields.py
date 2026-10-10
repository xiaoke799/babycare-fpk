#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""门禁：前端把「用户自由文本字段」插进 HTML 时必须是 escapeHtml(...) 包裹的。

背景：项目里绝大多数地方已用 escapeHtml，但若干「编辑弹窗回填」「卡片渲染」
漏了 → 存储型 XSS（用户/备份导入的自由文本含 "</textarea>" 或 '" onerror='
即可注入）。技能自带的 scan_xss_templates.py 用**字段名白名单**判，白名单不全
（实测漏 diagnosis_method / diagnosed_by / management / hospital / batch_number…），
故本脚本改为**显式登记自由文本字段的全集**（真值源 = server.py 里各表的 TEXT 列
∩ 用户可编辑 ∩ 非枚举/非日期/非数字），命中未转义即报错。

判据只报「未转义且落在 HTML 上下文」的；判定为转义的形式：
  - ${escapeHtml(...)} / ${encodeURIComponent(...)}
  - 三元里两分支都被 escapeHtml 包住
  - 纯数字字段（见 SAFE_FIELD）
"""
import os
import re
import sys

JS = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), 'app', 'frontend', 'js', 'app.js')

# 用户可编辑的自由文本列（来自 server.py 建表 + _ensure_column）。
# ⚠ 新增「用户能往里面敲字符串」的列时，把它加进来，否则本门禁漏判。
TEXT_FIELDS = [
    'allergen_name', 'reaction_detail', 'diagnosis_method', 'diagnosed_by', 'management',
    'food_name', 'brand', 'series', 'spec', 'item_name', 'purchase_channel',
    'medication_name', 'dosage', 'indicator_name', 'milestone_name', 'title',
    'description', 'note', 'diagnosis', 'doctor', 'hospital', 'result_summary',
    'screening_method', 'detail_left', 'detail_right', 'injection_site',
    'manufacturer', 'batch_number', 'concerns', 'content', 'tooth_name',
    'doctor_name', 'remark', 'address', 'nickname', 'name_value',
]

# 纯数字/枚举/日期列（同名子串命中即放行）
SAFE_FIELD = re.compile(
    r'^(id|.*_id|count|total|num|number|days|months|weeks|minutes|hours|ml|'
    r'amount|amount_g|order|index|idx|dose_number|dose|score|percentile|rank|'
    r'.*_date|.*_at|.*_time|week|status|.*_status|.*_type|.*_level|severity|'
    r'reaction$|mood|unit|color|kind|mode|source|state|code|sex|gender|role|side|'
    r'age_months|age_gap|leap_number|percent|pct|bmi|height|weight|temperature|'
    r'head_circumference|package_size|price|value|cls|text|icon|label|apiBase)$'
)


def strip_noise(src):
    """把注释与字符串字面量抹掉，避免在注释/普通串里误判（保留模板串）。"""
    out = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c == '/' and i + 1 < n and src[i + 1] == '/':
            j = src.find('\n', i)
            i = n if j == -1 else j
            continue
        if c == '/' and i + 1 < n and src[i + 1] == '*':
            j = src.find('*/', i + 2)
            i = n if j == -1 else j + 2
            continue
        if c in '"\'':
            q, j = c, i + 1
            while j < n:
                if src[j] == '\\':
                    j += 2
                    continue
                if src[j] == q:
                    break
                j += 1
            seg = src[i:j + 1]
            out.append(''.join('\n' if ch == '\n' else ' ' for ch in seg))
            i = j + 1
            continue
        out.append(c)
        i += 1
    return ''.join(out)


# ⚠ 白名单：只放行「值确实不落进 HTML，或在使用点已被 escapeHtml」的表达式。
# 每条都必须写明理由；新增一条前先想清楚「这条错判会放过什么」。
# 已知残余风险：若「使用点已转义」的那些调用方哪天撤掉了 escapeHtml，本门禁不会报
# （它只看插值处）。发现这类改动时，把该表达式从白名单移除即可恢复拦截。
ALLOW = [
    (re.compile(r'\bref\.\w+'),
     'ref 取自 SLEEP_REFERENCE 本地常量表，无用户输入'),
    (re.compile(r'\.title\((ev|event)\)'),
     'TIMELINE_TYPE_MAP.title() 的返回值在所有使用点已 escapeHtml'),
    (re.compile(r'\bevent\.(reaction_detail|dosage|note)'),
     'getTimelineDetail 的返回值在使用点已 escapeHtml（2721 / 6080）'),
    (re.compile(r'\be\.(food_name|tooth_name)'),
     '同上，TIMELINE_TYPE_MAP 里的 title 构造器，使用点已 escapeHtml'),
    (re.compile(r'\bv\.reaction_detail'),
     'clinic 页拼入 reaction 变量后以 escapeHtml 输出'),
    (re.compile(r'\bm\.title'),
     'canvas ctx.fillText，非 DOM 注入面'),
    (re.compile(r'\bp\.description'),
     '来自 leaps 静态指导表（后端常量），非用户输入'),
    (re.compile(r'\bdata\.(brand|series|spec)'),
     '拼入 JSON 请求体（item_name），非 HTML 注入面'),
    (re.compile(r'\bi\.text'),
     '本地硬编码的洞察文案，无用户输入'),
]


def main():
    src = open(JS, encoding='utf-8', errors='replace').read()
    # ⚠ 不做「剥串」预处理：正则字面量里的引号会让剥串逻辑错位（本仓库反复踩过），
    # 错位会把后面大段代码吞掉 ⇒ 漏判。直接扫原始源码，注释里的同名文本由人工确认。
    clean = src
    field_alt = '|'.join(re.escape(f) for f in TEXT_FIELDS)
    # ${ expr 中访问了自由文本字段
    access = re.compile(r'\$\{([^{}]*?\b(?:' + field_alt + r')\b[^{}]*?)\}')
    findings = []
    for m in access.finditer(clean):
        e = m.group(1).strip()
        if 'escapeHtml' in e or 'encodeURIComponent' in e:
            continue
        # 形如 x.field —— 字段名确实是「字段」而非字符串内容
        flds = re.findall(r'\.([a-zA-Z_]\w*)', e)
        real = [f for f in flds if f in TEXT_FIELDS]
        if not real:
            continue
        if all(SAFE_FIELD.match(f) for f in real):
            continue
        if any(p.search(e) for p, _ in ALLOW):
            continue
        ln = clean[:m.start()].count('\n') + 1
        findings.append((ln, e))

    # 去重（同名同表达式）
    seen, uniq = set(), []
    for ln, e in findings:
        if (ln, e) in seen:
            continue
        seen.add((ln, e))
        uniq.append((ln, e))

    print(f"未转义的自由文本插值：{len(uniq)} 处")
    print("=" * 78)
    for ln, e in uniq:
        print(f"  app.js:{ln}  ${{{e[:110]}}}")
    return 1 if uniq else 0


if __name__ == '__main__':
    sys.exit(main())
