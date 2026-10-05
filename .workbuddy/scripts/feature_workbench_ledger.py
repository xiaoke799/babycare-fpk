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
  D9 无数据不编结论     —— 🔴 本轮新增，见下

🔴 D9 是从一次「负向对照转绿」里长出来的：
  pattern 页后端**恒返回 24 条**（时段 0~23），没记录时是 24 个 0。
  原代码照样渲染统计卡和洞察，于是 7 天完全没喂奶记录的宝宝，
  页面会自信地写「宝宝喂奶节律良好，白天喂奶充足」+「高峰时段 0:00」。
  这比留白危险得多 —— 留白用户知道没数据，编结论用户会当成诊断依据。
  D4 抓不到它（那不是静默 return，是**成功渲染了错误内容**），
  D5 抓不到它（有渲染，只是渲染错了）。所以必须单列一维。

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

# 🔴 D5 空态白名单 —— 每一项都必须写清「为什么不适用」，不许裸写白名单。
#⚠ 新增条目前先问「这条错判会误伤谁」：判据报错比没判据更贵，
#    它会逼着去改本来正确的代码。
D5_NA = {
    # 静态页：白噪音音频、定时器选项全在 index.html 里，压根不拉数据，
    # loadPageData 里就是 `case 'sounds': break;`。要求它渲染空态是荒唐的。
    'sounds': '静态页，无接口调用（case 里就是 break）',

    # 表单页：loadSettings 只把后端设置回填到<select>，失败时表单保持
    # HTML 里写好的默认值（kg/cm/°C/20条）—— 这正是期望的降级行为，
    # 不是「静默失败」。改成弹红色报错反而会让用户以为设置坏了。
    'settings': '表单页，失败时保持 HTML 默认值即正确降级',

    # 关于页只取一个版本号，函数注释已写明「失败则回退到静态版本」，
    # 且先写死了 '版本 0.0.1' 再异步覆盖，用户永远看得到一个版本号。
    'about': '仅版本号，代码内已显式写死静态兜底',
}

# 🔴 D9 不适用名单：同样必须写理由。
#    这些页面要么没有「计算型结论」，要么本就该显示 0。
D9_NA = {
    # 结论是静态文案（"数据仅供本地保存"之类），不来自数据计算
    'about': '静态文案，无数据计算',
    'settings': '纯表单回填',
    'sounds': '静态页',
    #🔴 这个是被 D9 判据误伤后人工核实确认的：
    #    renderSleepGoal 里 `ref.status === 'none'` 就整张卡 display:none，
    #    守卫写在 Math.max 之前，逻辑完全正确。但它是后端算好的 status 字段，
    #    关键词穷举（hasData / length===0 / isEmpty）认不出这种语义等价形态。
    #    ⇒ 与其扩关键词表（下一个函数又是别的写法），不如显式豁免并写明理由。
    #    判据报错比没判据更贵：它会逼着去改本来正确的代码。
    'sleep-analysis': 'renderSleepGoal 由 ref.status===\'none\' 守卫（整卡隐藏），已人工核实',
}


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


def audit_deletes(js_nc):
    """D6：全量审查所有 delete*/remove* 函数有���有防误删确认。

    🔴 判据踩过的三个坑（都会造成误判/漏判，都写在这里）：
1. **只认confirm( 会把更强的防护判成"无确认"**：
       deleteBaby 走的是安全码弹窗（safetyCodeModal + prompt 兜底），
       比 confirm 严格得多，第一版把它误报成 ⚠失败当成功。
       ⇒ 确认形态要认全：confirm( / showModal / prompt / safetyCodeModal。
2. **"会发 DELETE 请求"不等于"会丢数据"**：
       removeLoadingMessage 只 el.remove() 删一个 DOM 节点（AI 加载动画）、
       removeBatchRow 只 splice 一行**尚未提交的表单**。这两处加确认弹窗
       只会让用户多按一次键，判据必须先确认它到底删不删数据。
    3. **静态扫描结果要人工确认**（项目铁律）：本函数报出的每一项，
       都要读一眼真实代码再决定改不改，别照单全收。
    """
    # 只删内存/未提交数据、不发持久化删除 ⇒ 不需要确认弹窗
    #（removeCustomModel 也归这类：只动内存数组 + 下拉框，刷新即恢复。
    #  它的真实缺口是**文案误导**「已移除」，已改成说清是临时移除。）
    HARMLESS = {'removeLoadingMessage', 'removeBatchRow', 'removeCustomModel'}
    # 有比confirm 更强的防护（安全码）
    STRONG = {'deleteBaby'}

    out = []
    # 🔴 别只按函数名找：真正发 DELETE 请求的常常是**被调用的那个**函数。
    #    deleteBaby 本体只负责弹安全码框，真正删的是 performDeleteBaby
    #    （名字不叫 delete*/remove*）⇒ 第一版把它判成「不发 DELETE 请求」，
    #    成了假绿。⇒ 把 delete*/remove* 里调到的 perform* /真正发请求的函数
    #    也一并纳入审查。
    seeds = set(re.findall(r'^(?:async\s+)?function\s+((?:delete|remove)[A-Za-z0-9_]*)\s*\(', js_nc, re.M))
    for seed in list(seeds):
        b0 = func_body(js_nc, seed) or ''
        for sub in re.findall(r'\b(perform[A-Z][A-Za-z0-9_]*)\s*\(', b0):
            if func_body(js_nc, sub):
                seeds.add(sub)

    for m in re.finditer(r'^(?:async\s+)?function\s+((?:delete|remove)[A-Za-z0-9_]*|perform[A-Z][A-Za-z0-9_]*)\s*\(',
                         js_nc, re.M):
        name = m.group(1)
        if name not in seeds:
            continue
        body = func_body(js_nc, name)
        if not body:
            out.append((name, 'MISS', '函数体取不到'))
            continue
        if name in HARMLESS:
            out.append((name, 'n/a', '只动内存/未提交表单，不丢数据'))
            continue
        # 发 DELETE 请求？（本体没有就沿 perform* 调用的判定走）
        if not re.search(r"method\s*:\s*['\"]DELETE['\"]", body):
            subs = [s for s in re.findall(r'\b(perform[A-Z][A-Za-z0-9_]*)\s*\(', body)
                    if func_body(js_nc, s)]
            if subs:
                sb = func_body(js_nc, subs[0]) or ''
                if re.search(r"method\s*:\s*['\"]DELETE['\"]", sb):
                    strong = bool(re.search(r'safety_code|safetyCodeModal|prompt\(', sb))
                    judged = bool(re.search(r'\.success', sb))
                    if strong and judged:
                        out.append((name, 'ok', '真实删除在 %s：有安全码 + 判 success' % subs[0]))
                    elif judged:
                        out.append((name, 'ok', '真实删除在 %s：判 success' % subs[0]))
                    else:
                        out.append((name, 'WARN', '真实删除在 %s，但没判 success' % subs[0]))
                    continue
            out.append((name, 'n/a', '不发 DELETE 请求'))
            continue
        if name in STRONG or re.search(r'safetyCodeModal|prompt\(', body):
            out.append((name, 'ok', '有安全码（比 confirm 更强）'))
            continue
        if re.search(r'confirm\(', body):
            # 确认之后有没有判 success（否则「删失败」会被当成「删成功」）
            i = body.find('confirm(')
            tail = body[i:]
            judged = bool(re.search(r'\.success', tail))
            out.append((name, 'ok' if judged else 'WARN',
                        'confirm + 判success' if judged else '有 confirm 但没判 success（失败可能当成功）'))
            continue
        out.append((name, 'GAP', '删数据的操作**无任何确认**'))
    return out


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

        # ---------- D5 空态 ----------
        # ⚠ 下面要用 body，必须在这里重新取（它被原D5 判据用，这里补回赋值）
        if fn:
            body = func_body(js_nc, fn) or ''
        else:
            body = blk or ''
        #🔴 第一版只 grep 入口函数本体，于是 33 个入口里 18 个报 '?'，
        #    人工核实后发现 **多数是误判**：空态写在子渲染函数里。
        #    loadActivities → renderActivityList、loadRecipes → renderRecipeList、
        #    loadTummytimePage → renderTummytimeList，三处都有完整空态，
        #    而且 activities 还细分了「月龄超范围」和「分类筛空」两种，比通用空态更好。
        #    ⇒ 判据必须和 D4 一样沿调用链下钻 2 层，只看本体必然假红。
        #⚠ 但也有真缺失的：reports 的 4 个 load*Report 全部
        #    `if (!res.success) return`，失败时页面留白/留着上一个 tab 的数字；
        #    pattern 页后端恒返回 24 个时段（没记录时是 24 个 0），
        #    不判全零就会输出「节律良好，白天喂奶充足」的**凭空结论**。
        d5_txt = (body or '') + (blk or '')
        d5_chain = []          # 收集沿链看到的所有函数体，供下面分类
        seen5 = set()
        def walk5(fname, depth):
            if depth > 2 or fname in seen5 or not fname:
                return
            seen5.add(fname)
            b = func_body(js_nc, fname)
            if b is None:
                return
            d5_chain.append(b)
            for sub in set(re.findall(r'\b((?:load|render|init|draw)\w*)\s*\(', b)):
                walk5(sub, depth + 1)
        walk5(fn, 0)
        d5_all_txt = d5_txt + '\n' + '\n'.join(d5_chain)

        has_empty = re.search(r'empty|暂无|没有记录|还没有|无数据', d5_all_txt, re.I)
        # ⚠ D5 只管「无数据时说不人话」。失败可见性是 D4 的职责，
        #    在这里再判一遍会导致 21 个页面报同一个问题（D4 已全绿），
        #    逼着去改已经修好的地方 —— 判据报错比没判据更贵。
        if page in D5_NA:
            r['d5'] = 'n/a'
            r['d5why'] = D5_NA[page]
        elif has_empty:
            r['d5'] = 'ok'
        else:
            r['d5'] = 'GAP'
            r['gaps'].append('D5 无空态渲染')

        # ---------- D9 无数据不编结论 ----------
        # 🔴 这一维是被「负向对照转绿」逼出来的，不是想出来的：
        #    把 pattern 的全零守卫短路掉，台账依然全绿 —— 判据抓不到，
        #    就意味着这次修的 bug 下次还会回来。
        #    D4 判的是「失败时说不说」，D5 判的是「有没有渲染」，
        #    而「数据全为 0 却输出良好结论」两者都不沾。
        # 判法：页面里出现「计算型结论文案」（节律/良好/充足/建议…）
        #       时，同函数内必须存在「全零/无数据」的前置守卫。
        d9_hits = []
        if page not in D9_NA:
            seen9 = set()
            def walk9(fname, depth):
                if depth > 2 or fname in seen9 or not fname:
                    return
                seen9.add(fname)
                b = func_body(js_nc, fname)
                if b is None:
                    return
                # 计算型结论 + 有求和/峰值逻辑，却没有空数据守卫
                #
                # 🔴 判据位置踩的坑（负向对照抓出来的，必须记住）：
                #    第一版只判断「函数体里有没有 hasData 这个词」。
                #    于是把 `if (!hasData) {...return;}` 改成 `if (false) {...}`
                #    之后判据纹丝不动全绿 —— 因为**变量声明还在**：
                #        const hasData = data.some(...)
                #    关键词搜得到，守卫却已经不生效了。
                #    ⇒ 只认关键词 = 判据坏了。
                #    现在改成**切函数体、找守卫的位置**：守卫必须出现在
                #    渲染结论之前，且形如 `if (<条件>) { ...return... }`。
                #    判据自己要先过负向对照，否则不敢信（全绿 ≠ 没问题）。
                has_conclusion = re.search(r'(良好|充足|频繁|建议|分析)', b) and \
                    re.search(r'\.reduce\(|\.some\(|Math\.(max|min)', b)
                if has_conclusion:
                    # 结论渲染语句的位置（第一个 innerHTML 赋值 / 结论变量）
                    concl = re.search(r'innerHTML|const insight|const st\s*=', b)
                    concl_at = concl.start() if concl else len(b)
                    # 形如 if (xx) { ...return...; } 的守卫，取其**条件表达式**与起始位置
                    guards = []
                    for gm in re.finditer(r'\bif\s*\(([^)]*)\)\s*\{', b):
                        seg = b[gm.start(): gm.start() + 400]
                        if 'return' in seg:
                            guards.append((gm.start(), gm.group(1)))
                    # 守卫必须在结论渲染**之前**，且条件里带数据判据
                    #
                    # 🔴 判据位置踩的坑（第二次负向对照才抓到）：
                    #    第一版是 `emptyish.search(b[g:g+200])` —— 扫的是
                    #    「守卫后面200 字符」，而不是**条件本身**。
                    #    于是把 `if (!hasData)` 改成 `if (1 === 2)` 之后，
                    #    后面的 `const hasData = data.some(...)` 声明还在窗口里，
                    #    判据照样给 ok ⇒ 判据是坏的，不是代码是坏的。
                    #    现在只判条件表达式本身。
                    emptyish = re.compile(r'hasData|render\w*Empty|records\.length\s*===?\s*0|'
                                          r'!\w+\.length|\.length\s*===?\s*0|isEmpty|'
                                          r'status\s*===\s*.none.|no data', re.I)
                    before = [(g, c) for g, c in guards if g < concl_at]
                    guard_ok = bool(before) and any(emptyish.search(c) for _, c in before)
                    if not guard_ok:
                        d9_hits.append(fname)
                for sub in set(re.findall(r'\b((?:load|render|init|draw)\w*)\s*\(', b)):
                    walk9(sub, depth + 1)
            walk9(fn, 0)
        r['d9'] = 'GAP' if d9_hits else 'ok'
        r['d9fns'] = d9_hits
        if d9_hits:
            r['gaps'].append('D9 输出计算型结论但无空数据守卫：%s' % ','.join(sorted(set(d9_hits))))

        # ---------- D6 删除守卫（全量审 delete*/remove* 函数，不是逐页） ----------
        #🔴 第一版这里写的是 r['d6']='n/a'（等于没判）。而这一维查出来的东西
        #    最有价值：19+ 个删除函数里有一个**完全没有确认**（家庭成员标签，
        #    界面就是个 × 按钮，点一下直接删），和其余 14 处不一致。
        r['d6'] = 'ok'

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
    print('%-16s %-10s %-5s %-4s %-4s %-4s %-4s %-4s' % ('入口', '栏目', '落点', 'D2', 'D3', 'D4', 'D5', 'D9'))
    print('-' * 78)
    for r in rows:
        print('%-16s %-10s %-5s %-4s %-4s %-4s %-4s %-4s' % (
            '%s/%s' % (r['page'], r['label']), r['hub'], r['d1'], r['d2'], r['d3'],
            r.get('d4', '-'), r.get('d5', '-'), r.get('d9', '-')))

    # ---------- D6 全局审查（先算，才能并入总数） ----------
    deletes = audit_deletes(js_nc)
    n_gap = sum(1 for _, s, _ in deletes if s == 'GAP')
    n_warn = sum(1 for _, s, _ in deletes if s == 'WARN')

    ngap = sum(len(r['gaps']) for r in rows)
    print('-' * 78)
    print('未到位项合计：%d' % (ngap + n_gap + n_warn))
    for r in rows:
        if r['gaps']:
            print('  [%s] %s' % (r['page'], '；'.join(r['gaps'])))

    if d4_all:
        print()
        print('### D4 静默失败明细（api 失败但用户看不到任何提示）')
        for page, label, fname, snippet in d4_all:
            print('  %-16s %-14s %s' % (page, fname, snippet[:90]))

    print()
    print('=' * 78)
    print('D6 删除守卫 · 全量 %d 个 delete*/remove* 函数' % len(deletes))
    print('=' * 78)
    print('%-28s %-6s %s' % ('函数', '判定', '说明'))
    print('-' * 78)
    for name, st, why in deletes:
        print('%-28s %-6s %s' % (name, st, why))
    print('-' * 78)
    print('需修：GAP %d 项（无确认）、WARN %d 项（失败当成功）' % (n_gap, n_warn))
    for name, st, why in deletes:
        if st in ('GAP', 'WARN'):
            print('  D6 [%s] %s —— %s' % (st, name, why))

    if only is None:
        os.makedirs(os.path.join(ROOT, 'outputs'), exist_ok=True)
        p = os.path.join(ROOT, 'outputs', '功能打磨台账.json')
        with open(p, 'w', encoding='utf-8') as f:
            json.dump([dict(r) for r in rows], f, ensure_ascii=False, indent=2)
        print('\n台账已存：%s' % p)

    return 0


if __name__ == '__main__':
    sys.exit(main())