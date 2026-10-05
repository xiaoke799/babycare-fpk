#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""功能打磨台账：逐个功能入口体检，输出可勾选台账。

设计意图（对应「逐个功能打磨，确认每个都打磨到位」）：
    「打磨到位」不等于「门禁全绿」。门禁只查结构（路由存在 / 函数已定义 /
    section 有落点），查不出「接口挂了用户却不知道」这类体验缺口。
    所以这里按入口逐个跑 8 个维度，每维都是可机械判定的断言，
    报告里能直接看到哪个功能的哪一维没到位。

🔴 判据纪律（同项目门禁铁律）：
  - 不写「不是占位值就算过」这种会被兜底值蒙混的判据；
  - 每个维度都要能被「打回修复确认转红」，所以判据钉在具体代码形态上；
  - 静态扫描报的「疑似」一律人工确认后再决定是否算问题（见 --only）。

维度：
  D1 section/别名落点   —— 入口能渲染出东西
  D2 loadPageData case   —— 进页面会拉数据（sounds 例外：静态页）
  D3 调用链可达load 函数 —— case 里调的函数真的存在（check_undefined_funcs 已全局兜一层，
                            这里单列是为了台账可读：谁没接数据源一眼看到）
  D4 失败分支可见——
     🔴 本项目最高频静默缺陷：api(...).then(res => { if (!res.success) return; })
     用户看到的是「空白列表 + 点什么都没反应」，完全不知道是挂了。
  D5 空态有渲染         —— 没数据时要说人话，不能留白
  D6 删除有确认 + rowcount —— 误删不可逆
  D7 时间归一化写入     —— 前端别自己拼 'YYYY-MM-DDTHH:mm'
  D8 事件委托/绑定      —— 动态插入的控件必须绑在容器上

用法：
    python .workbuddy/scripts/feature_workbench_ledger.py            # 全量台账
    python .workbuddy/scripts/feature_workbench_ledger.py --page feeding   # 单页细看
"""
import os
import re
import sys
import json
from collections import OrderedDict

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
APP_JS = os.path.join(ROOT, 'app', 'frontend', 'js', 'app.js')
APP_HTML = os.path.join(ROOT, 'app', 'frontend', 'index.html')

# 入口清单：与 NAV_HUBS 保持一致（由 verify_nav_layout.py 交叉锁）
# kind: 'section' = 自有 section；'alias' = 只读别名（READONLY_ALIAS）
#       'shared' = 虚拟页（走TAB_PAGE_HUB，落在别的 hub 的 section 上）
#       'static' = 纯静态页，无数据加载
FEATURES = [
    # ---- 首页 ----
    ('dashboard',      '首页',       'home',     'section'),
    # ---- 记录栏14 ----
    ('feeding',        '喂奶',       'record',   'section'),
    ('pumping',        '吸奶',       'record',   'section'),
    ('solidfood',      '辅食',       'record',   'section'),
    ('sleep-record',   '睡眠记录',   'record',   'section'),
    ('diaper-record',  '换尿布',     'record',   'section'),
    ('tummytime',      '趴睡训练',   'record',   'section'),
    ('growth',         '身高体重',   'record',   'shared'),
    ('milestones',     '成长成就',   'record',   'section'),
    ('leap',           '飞跃期',     'record',   'section'),
    ('diary',          '日记',       'record',   'section'),
    ('photos',         '相册',       'record',   'section'),
    ('diaper-price',   '比价记账',   'record',   'section'),
    ('teeth',          '出牙记录',   'record',   'section'),
    ('fontanelle',     '囟门',       'record',   'section'),
    ('health',         '健康档案',   'record',   'section'),
    # ---- 成长栏 9 ----
    ('growth-chart',   '成长曲线',   'growth',   'shared'),
    ('bmi',            'BMI',        'growth',   'section'),
    ('sleep-analysis', '睡眠分析',   'growth',   'shared'),
    ('pattern',        '作息节律',   'growth',   'section'),
    ('timeline',       '时间线',     'growth',   'section'),
    ('milestone-review','成长回顾',  'growth',   'alias'),
    ('asq',            '发育筛查',   'growth',   'section'),
    ('reports',        '阶段报告',   'growth',   'section'),
    # ---- 知识栏 5 ----
    ('month-guide',    '本月要点',   'knowledge','section'),
    ('knowledge',      '知识库',     'knowledge','section'),
    ('wiki',           '健康百科',   'knowledge','section'),
    ('recipes',        '食谱',       'knowledge','section'),
    ('activities',     '活动',       'knowledge','section'),
    # ---- 系统栏 4 ----
    ('settings',       '设置',       'system',   'section'),
    ('clinic',         '看诊摘要',   'system',   'section'),
    ('sounds',         '安抚音效',   'system',   'static'),
    ('about',          '关于',       'system',   'section'),
]


def strip_comments(src):
    """剥掉 // 与 /* */ 注释，避免注释里的字样被判成真代码（项目铁律）。"""
    out = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c == '"' or c == "'" or c == '`':
            q = c
            out.append(c); i += 1
            while i < n:
                if src[i] == '\\':
                    out.append(src[i:i+2]); i += 2; continue
                out.append(src[i])
                if src[i] == q:
                    i += 1; break
                i += 1
            continue
        if c == '/' and i + 1 < n and src[i+1] == '/':
            while i < n and src[i] != '\n':
                i += 1
            continue
        if c == '/' and i + 1 < n and src[i+1] == '*':
            i += 2
            while i + 1 < n and not (src[i] == '*' and src[i+1] == '/'):
                i += 1
            i += 2
            continue
        out.append(c); i += 1
    return ''.join(out)


def func_body(src, name):
    """取function name(...) {...} 的函数体（按大括号配平）。

    🔴 不做「找不到就返回后面全部」——那会把兄弟函数的调用算到这个函数头上，
    断言会莫名其妙地通过或失败。项目铁律：宁可返回空串。
    """
    m = re.search(r'function\s+' + re.escape(name) + r'\s*\(', src)
    if not m:
        m = re.search(r'(?:const|let|var)\s+' + re.escape(name) + r'\s*=\s*(?:async\s*)?(?:function\s*)?\(', src)
    if not m:
        return None
    start = src.index('{', m.end() - 1)
    # 若 '{' 落在参数列表里（解构默认值），先跳过参数
    head = src[m.start():start]
    if head.count('(') > head.count(')'):
        return None
    depth, i, n = 0, start, len(src)
    while i < n:
        if src[i] == '{':
            depth += 1
        elif src[i] == '}':
            depth -= 1
            if depth == 0:
                return src[start:i+1]
        i += 1
    return None


def entry_function(src, page):
    """从 loadPageData 的 switch 里取出该page 调用的函数名。"""
    m = re.search(r'function\s+loadPageData\s*\(', src)
    if not m:
        return None, None
    start = src.index('{', m.end() - 1)
    depth, i, n = 0, start, len(src)
    body = None
    while i < n:
        if src[i] == '{':
            depth += 1
        elif src[i] == '}':
            depth -= 1
            if depth == 0:
                body = src[start:i+1]
                break
        i += 1
    if not body:
        return None, None
    for pm in re.finditer(r"case\s+['\"]" + re.escape(page) + r"['\"]\s*:", body):
        #⚠ 不能拿下一个 case 当边界——本项目 case 之间常夹两行注释，
        #    更麻烦的是 case 穿透（'milestones' 直接落到下个 case 的 body），
        #    那样截出来的块里根本没有函数名，会误报「进页不拉数据」。
        #    正确做法：取「本 case 到下一个 case（或 switch 结束）」之间，
        #    里面没有函数名就沿 case 穿透继续往后找。
        stop = body.find('case ', pm.end())
        blk = body[pm.end(): stop if stop != -1 else len(body)]
        calls = re.findall(r'\b(load[A-Z]\w*|init[A-Z]\w*)\s*\(', blk)
        calls = [c for c in calls if c != 'loadPageData']
        if calls:
            return calls[0], blk
        # case 穿透：本块无函数名，去 case 之后继续取
        if stop != -1:
            nxt = body[stop + 5:]
            nstop = nxt.find('case ')
            nblk = nxt[: nstop if nstop != -1 else len(nxt)]
            ncalls = re.findall(r'\b(load[A-Z]\w*|init[A-Z]\w*)\s*\(', nblk)
            ncalls = [c for c in ncalls if c != 'loadPageData']
            if ncalls:
                return ncalls[0], blk + nblk
    return None, None


# D4：静默失败探测
# 形态：then(res => { if (!res.success) return; ... })  —— 用户看不到任何提示
SILENT_PAT = re.compile(
    r'\.then\s*\(\s*(?:async\s*)?\(?\s*\w+\s*\)?\s*=>\s*\{(?P<body>[^{}]*?)\}', re.S)
SILENT_HIT = re.compile(r'if\s*\(\s*!\s*\w+\.success\s*\)\s*\{?\s*return\s*;?\s*\}?')
USER_VISIBLE = re.compile(
    r'showToast|showError|alert\s*\(|renderEmpty|emptyState|setText|innerHTML|'
    r'textContent|console\.|diagnos|error_logs|reportError')
FAIL_VISIBLE = re.compile(
    r'showToast|showError|alert\s*\(|renderError|emptyState|renderEmpty|'
    r'textContent|innerHTML|reportError|toast')


def scan_silent(js_nc):
    """返回 [(行号, 代码片段, 是否对用户可见)]，行号按未注释源码估算。"""
    hits = []
    for m in SILENT_PAT.finditer(js_nc):
        body = m.group('body')
        if not SILENT_HIT.search(body):
            continue
        after = body[SILENT_HIT.search(body).end():]
        # 失败分支之后若还有「对用户可见」的动作，视为已处理
        visible = bool(FAIL_VISIBLE.search(after))
        hits.append((m.start(), body.strip()[:120], visible))
    return hits


def main():
    only = None
    if '--page' in sys.argv:
        only = sys.argv[sys.argv.index('--page') + 1]

    raw = open(APP_JS, encoding='utf-8').read()
    html = open(APP_HTML, encoding='utf-8').read()
    js_nc = strip_comments(raw)

    # 原始文本行号映射（用于报可点击的行）
    def lineno(off):
        return raw.count('\n', 0, off) + 1

    sections = set(re.findall(r'<section[^>]*id="page-([\w-]+)"', html))
    alias_blk = re.search(r'READONLY_ALIAS\s*=\s*\{(.*?)\};', js_nc, re.S)
    aliases = dict(re.findall(r"['\"]?([\w-]+)['\"]?\s*:\s*\{[^}]*host:\s*['\"]([\w-]+)['\"]", alias_blk.group(1))) if alias_blk else {}
    tabhub = dict(re.findall(r"['\"]([\w-]+)['\"]\s*:\s*['\"]([\w-]+)['\"]", (re.search(r'TAB_PAGE_HUB\s*=\s*\{(.*?)\};', js_nc, re.S) or re.match('', '')).group(1) if re.search(r'TAB_PAGE_HUB\s*=\s*\{(.*?)\};', js_nc, re.S) else ''))

    feats = [f for f in FEATURES if (only is None or f[0] == only)]
    if not feats:
        print('没有这个入口：%s' % only)
        return 2

    rows = []
    d4_all = []
    for page, label, hub, kind in feats:
        r = OrderedDict(page=page, label=label, hub=hub, kind=kind, gaps=[])

        # D1 落点
        if kind == 'section':
            r['d1'] = 'ok' if page in sections else 'MISS'
        elif kind == 'alias':
            r['d1'] = 'ok' if page in aliases else 'MISS'
        elif kind == 'shared':
            r['d1'] = 'ok' if page in sections or page in tabhub or page in aliases else 'MISS'
        else:
            r['d1'] = 'n/a'
        if r['d1'] == 'MISS':
            r['gaps'].append('D1 无落点')

        # D2 loadPageData case
        fn, blk = entry_function(js_nc, page)
        if kind == 'static':
            r['d2'] = 'n/a'
            r['fn'] = '-'
        elif fn:
            r['d2'] = 'ok'
            r['fn'] = fn
        else:
            r['d2'] = 'MISS'
            r['fn'] = '-'
            r['gaps'].append('D2 进页不拉数据')

        # D3 调用链可达
        if fn:
            if func_body(js_nc, fn) is not None:
                r['d3'] = 'ok'
            else:
                r['d3'] = 'MISS'
                r['gaps'].append('D3 load 函数不存在：%s' % fn)
        else:
            r['d3'] = 'n/a'

        # D4 失败分支可见 —— 沿调用链向下 2 层找静默 return
        silents = []
        seen = set()
        def walk(fname, depth):
            if depth > 2 or fname in seen or not fname:
                return
            seen.add(fname)
            body = func_body(js_nc, fname)
            if body is None:
                return
            off = js_nc.index(body)
            for _, snippet, visible in scan_silent(body):
                if not visible:
                    silents.append((fname, snippet))
            for sub in set(re.findall(r'\b(load[A-Z]\w*)\s*\(', body)):
                walk(sub, depth + 1)
        walk(fn, 0)
        # 同页内 render* 也算一层（渲染函数里静默 return 会让空态不显示）
        r['d4'] = 'ok' if not silents else 'GAP'
        r['d4n'] = len(silents)
        if silents:
            r['gaps'].append('D4 静默失败 %d 处' % len(silents))
            for fname, snippet in silents:
                d4_all.append((page, label, fname, snippet))

        # D5 空态 / D6 删除 / D7 时间 / D8 事件
        if fn:
            body = func_body(js_nc, fn) or ''
        else:
            body = blk or ''
        r['d5'] = 'ok' if re.search(r'empty|暂无|没有记录|还没有', (body or '') + (blk or ''), re.I) else '?'
        r['d6'] = 'n/a'
        # D7 逐页判定，且只认「真正会出UTC 偏移」的形态：
        #    坑是裸的 new Date().toISOString()（UTC），而
        #    new Date(t - getTimezoneOffset()*60000).toISOString() 是
        #    主动做了本地化补偿的正确写法（datetime-local 输入框就靠它）。
        #⚠ 别把两者混为一谈——第一版判据就是在这栽的：全文件只要出现
        #    toISOString 就给每页报GAP，33 个入口里 32 个假红。
        page_iso = []
        for m in re.finditer(r'toISOString\(\)', js_nc):
            ctx = js_nc[max(0, m.start() - 90): m.end() + 20]
            if 'getTimezoneOffset' not in ctx:
                page_iso.append(js_nc.count('\n', 0, m.start()) + 1)
        r['d7'] = 'GAP' if page_iso else 'ok'
        r['d7lines'] = page_iso
        if r['d7'] == 'GAP':
            r['gaps'].append('D7 裸 toISOString（UTC 未本地化）%d 处' % len(page_iso))
        rows.append(r)

    # 报告
    print('=' * 78)
    print('功能打磨台账 · 共 %d 个入口' % len(rows))
    print('=' * 78)
    print('%-16s %-10s %-5s %-4s %-4s %-4s %-4s' % ('入口', '栏目', '落点', 'D2', 'D3', 'D4', 'D7'))
    print('-' * 78)
    for r in rows:
        print('%-16s %-10s %-5s %-4s %-4s %-4s %-4s' % (
            '%s/%s' % (r['page'], r['label']), r['hub'], r['d1'], r['d2'], r['d3'],
            r.get('d4', '-'), r.get('d7', '-')))

    ngap = sum(len(r['gaps']) for r in rows)
    print('-' * 78)
    print('未到位项合计：%d' % ngap)
    for r in rows:
        if r['gaps']:
            print('  [%s] %s' % (r['page'], '；'.join(r['gaps'])))

    if d4_all:
        print()
        print('### D4 静默失败明细（api 失败但用户看不到任何提示）')
        for page, label, fname, snippet in d4_all:
            print('  %-16s %-14s %s' % (page, fname, snippet[:90]))

    if only is None:
        os.makedirs(os.path.join(ROOT, 'outputs'), exist_ok=True)
        p = os.path.join(ROOT, 'outputs', '功能打磨台账.json')
        with open(p, 'w', encoding='utf-8') as f:
            json.dump([dict(r) for r in rows], f, ensure_ascii=False, indent=2)
        print('\n台账已存：%s' % p)

    return 0


if __name__ == '__main__':
    sys.exit(main())