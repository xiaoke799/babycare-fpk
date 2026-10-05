#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""导航分层与「系统诊断」板块的静态校验。

背景（2026-09-19 用户定的规则，当日迭代两轮到定稿）：
  1. 记录栏只放「往里填数据」的录入入口；成长栏只放「看结果」的报表与回顾视图。
     **一个功能横跨两边时拆两半**：录的那半归记录栏，看的那半归成长栏，各栏各一个入口。
        · 身高体重(录)          ↔ 成长曲线(看)
        · 睡眠记录(录)          ↔ 睡眠分析(看)
        · 成长成就(录 milestones) ↔ 成长回顾(看 milestone-review，复用同一个 section)
  2. **「带＋添加按钮 ≠ 归记录栏」这条启发式已废弃**（同日上午定、下午推翻）。
     判定标准是「这块是填表，还是看结果」，不是「有没有添加按钮」。
     出牙/囟门/飞跃期这几个页面主体就是填表（图表只是顺带看一眼），所以整个留在记录栏。
  3. 两栏内部按小组收纳（记录栏 6 组 / 成长栏 4 组），宫格渲染时插 `.hub-group-title`。
  4. 应用信息 / 存储 / 数据库健康 / 日志 / 异常记录，统一收在
     设置页的「系统诊断」一个板块里，不散落别处。

这些约定靠人眼很容易在后续迭代中失守，固化成脚本。

用法：
    python .workbuddy/scripts/verify_nav_layout.py
退出码 0 = 全部通过，1 = 有失败项。
"""
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
JS_PATH = os.path.join(ROOT, "app", "frontend", "js", "app.js")
HTML_PATH = os.path.join(ROOT, "app", "frontend", "index.html")
CSS_PATH = os.path.join(ROOT, "app", "frontend", "css", "main.css")
STORAGE_PATH = os.path.join(ROOT, "app", "backend", "blueprints", "storage.py")
CONSTANTS_PATH = os.path.join(ROOT, "app", "backend", "constants.py")

ok = True


def chk(label, cond):
    global ok
    print(("  [OK]   " if cond else "  [FAIL] ") + label)
    if not cond:
        ok = False


def read(path):
    with io.open(path, "r", encoding="utf-8") as f:
        return f.read()


js = read(JS_PATH)
html = read(HTML_PATH)
css = read(CSS_PATH)
storage = read(STORAGE_PATH)
constants = read(CONSTANTS_PATH)


def hub_block(name):
    """截出 NAV_HUBS 里某个 hub 的段（到下一个 hub: 为止）。"""
    start = js.index("hub: '%s'" % name)
    nxt = js.find("hub: '", start + 5)
    return js[start: nxt if nxt != -1 else len(js)]


def pages_in(block):
    return re.findall(r"page:\s*'([a-z0-9\-]+)'", block)


def titles_in(block):
    return re.findall(r"title:\s*'([^']*)'", block)


print("=== 一、记录栏：只放往里填数据的录入入口 ===")
rec = hub_block("record")
rec_pages = pages_in(rec)
chk("记录栏含身高体重的「录」入口 (growth)", "growth" in rec_pages)
chk("记录栏不含成长栏的曲线形态 (growth-chart)", "growth-chart" not in rec_pages)
chk("记录栏不再是安抚音效的入口 (sounds)", "sounds" not in rec_pages)
# 成长成就拆两半：带表单的「录」这半归记录栏，只读的「回顾」那半在成长栏
chk("记录栏含「成长成就」录入入口 (milestones)", "milestones" in rec_pages)
chk("记录栏不含只读回顾页 (milestone-review)", "milestone-review" not in rec_pages)
chk("记录栏已无「时间线」(timeline)", "timeline" not in rec_pages)
for p in ["feeding", "pumping", "sleep-record", "diaper-record", "solidfood",
          "tummytime", "diaper-price", "diary", "photos", "leap",
          "fontanelle", "teeth", "health"]:
    chk("记录栏保留 %s" % p, p in rec_pages)

print()
print("=== 二、成长栏：只放看结果的报表与回顾视图 ===")
gro = hub_block("growth")
gro_pages = pages_in(gro)
for p in ["growth-chart", "milestone-review", "timeline", "sleep-analysis", "bmi",
          "asq", "pattern", "reports"]:
    chk("成长栏含 %s" % p, p in gro_pages)
chk("成长栏不含录入形态的成长成就 (milestones)",
    "milestones" not in gro_pages)
chk("成长栏不含录入形态的身高体重 (growth)",
    "growth" not in gro_pages)
chk("成长栏没有混进逐条录入页",
    not any(p in gro_pages for p in ["feeding", "diaper-record", "sleep-record",
                                     "fontanelle", "teeth"]))
chk("已删的 firsts 页面没有复活（两栏都无）",
    "firsts" not in gro_pages and "firsts" not in rec_pages)

# 一个 page id 出现在两栏 = 两栏进的是同一个页面，形态根本分不开（假拆分）。
# 真拆分的做法是给「看」的那半单独一个 page id，再登记进
# GROWTH_TAB_PAGES / READONLY_ALIAS。这条断言就是防假拆分复发的。
_dup = sorted(set(rec_pages) & set(gro_pages))
chk("两栏不共用任何 page id（共用即为假拆分）%s"
    % ("" if not _dup else "，重复：" + "、".join(_dup)), not _dup)

print()
print("=== 二之二、三对拆分各自的落点 ===")
chk("身高体重(录) → page-growth 落 records Tab",
    re.search(r"case 'growth':[\s\S]{0,400}activateGrowthTab\('records'\)", js) is not None)
chk("成长曲线(看) → growth-chart 别名落 who-chart Tab",
    re.search(r"'growth-chart':\s*\{\s*host:\s*'growth',\s*tab:\s*'who-chart'\s*\}", js) is not None)
chk("睡眠分析(看) → sleep-analysis 别名落同名 Tab",
    re.search(r"'sleep-analysis':\s*\{\s*host:\s*'growth',\s*tab:\s*'sleep-analysis'\s*\}", js) is not None)
chk("成长成就(录) / 成长回顾(看) → 同一 section 靠 only-view 切形态",
    re.search(r"'milestone-review':\s*\{\s*host:\s*'milestones',\s*cls:\s*'only-view'\s*\}", js)
    is not None)
chk("三个「看」的别名都登记了返回目标（回成长宫格）",
    all(("'%s': 'growth-hub'" % a) in js
        for a in ["sleep-analysis", "growth-chart"]))
chk("切宝宝能刷新成长曲线（loadPageData 认 growth-chart）",
    "case 'growth-chart':" in js)
chk("别名页会重算底部 tab 高亮（别沿用宿主算出来的）",
    "const aliasHub = HUB_OF[page]" in js)
# 用户 2026-09-19 明确「这三个不管」：页面里虽有图，但主体是填表/档案，
# 不给它们加成长栏只读入口。以后别手滑加进去。
for p in ["fontanelle", "tummytime", "health"]:
    chk("有意不拆的 %s 仍只在记录栏" % p,
        p in rec_pages and p not in gro_pages)

print()
print("=== 三、系统栏：工具与设置 ===")
sysb = hub_block("system")
sys_pages = pages_in(sysb)
chk("系统栏含 设置/看诊摘要/安抚音效",
    all(p in sys_pages for p in ["settings", "clinic", "sounds"]))

print()
print("=== 四、「第一次」独立页面已彻底并入里程碑 ===")
chk("HTML 无 #page-firsts", 'id="page-firsts"' not in html)
chk("HTML 无 #firstsModal", 'id="firstsModal"' not in html)
chk("HTML 无 firstsList", 'firstsList' not in html)
for name in ["initFirstsPage", "loadFirstsRecords", "renderFirstsList",
             "showAddFirstsModal", "submitFirstsRecord", "deleteFirstsRecord"]:
    chk("JS 已删除 %s" % name, name not in js)
chk("PAGE_LINKS 里已无 firsts 键", not re.search(r"\bfirsts:\s*\[", js))
chk("loadPageData 里已无 case 'firsts'", "case 'firsts'" not in js)

print()
print("=== 五、里程碑页承接「第一次」能力 ===")
chk("里程碑页有快捷模板按钮",
    html.count('class="milestone-template-btn"') >= 10)
chk("里程碑表单有「标记为第一次」勾选框", 'id="milestoneIsFirst"' in html)
chk("提交时带上 is_first", "is_first: document.getElementById('milestoneIsFirst')" in js)
chk("模板按钮绑定到 openAddForm", ".milestone-template-btn" in js and "openAddForm" in js)
chk("里程碑页已改名为「成长成就」", "成长成就" in html)

print()
print("=== 六、两套里程碑名字区分开 ===")
chk("健康档案 Tab 叫「发育里程碑」",
    'data-tab="milestone">发育里程碑</button>' in html)
chk("健康档案里程碑仍用独立接口（milestone_details）",
    "/api/health/milestones/" in js)

print()
print("=== 七、设置页「系统诊断」集成度 ===")
# 从「系统诊断」那张卡的开头切起，到下一张卡为止。
# 用卡片标题做锚点、用「下一张 settings-card 开头」定边界 ——
# 不依赖卡片上的修饰 class（如 settings-card--diag）或注释文字，避免改版面就误伤门禁。
# 注意：卡片内部还有 settings-card-head / -head-text / -desc 等同前缀的 div，
# 所以这里必须用「class 值到 settings-card 为止」的精确匹配，不能用字符串前缀查找。
_diag_anchor = html.index('settings-card-title">系统诊断<')
_card_opens = [m.start() for m in re.finditer(r'<div class="settings-card(?=[\s"])', html)]
diag_start = max(o for o in _card_opens if o < _diag_anchor)
_after = [o for o in _card_opens if o > _diag_anchor]
diag_block = html[diag_start:(_after[0] if _after else len(html))]
for eid in ["appInfoGrid", "refreshAppInfoBtn", "storageOverview", "storageGrid",
            "storageHealth", "checkDbHealthBtn", "optimizeDbBtn", "cleanOrphanBtn",
            "logFileList", "refreshLogBtn", "logTypeSelect", "logLinesSelect",
            "loadLogBtn", "cleanLogsBtn", "logContent",
            "errorStats", "errorList", "errorLevelFilter", "errorDaysFilter",
            "loadErrorsBtn", "clearOldErrorsBtn", "loadMoreErrorsBtn", "refreshErrorsBtn"]:
    chk("系统诊断含 #%s" % eid, ('id="%s"' % eid) in diag_block)
chk("日志/异常块没有跑到看诊摘要页",
    'logFileList' not in html[html.index('id="page-clinic"'):html.index('id="page-teeth"')])

print()
print("=== 八、诊断相关 JS 接线 ===")
for fn in ["loadAppInfo", "loadLogFiles", "loadLogContent",
           "loadErrorPanel", "loadErrorStats", "loadErrorList",
           "showErrorDetail", "clearOldErrors"]:
    chk("已定义 %s" % fn, ("function %s(" % fn) in js)
chk("initDataManagement 绑定了诊断按钮",
    "refreshAppInfoBtn" in js and "loadLogBtn" in js and "clearOldErrorsBtn" in js)
chk("进入设置页会刷新诊断数据",
    re.search(r"case 'settings':[\s\S]{0,900}loadAppInfo\(\)", js) is not None)
chk("诊断接口路径用的是后端已有的路由",
    "/api/admin/logs/stats" in js and "/api/admin/logs/errors" in js
    and "/api/admin/logs/files/content" in js and "/api/storage/app-info" in js)

print()
print("=== 九、后端接口齐备 ===")
chk("storage.py 有 /api/storage/app-info", "/api/storage/app-info" in storage)
chk("app-info 需要管理员权限", re.search(
    r'/api/storage/app-info[\s\S]{0,220}?@require_admin', storage) is not None)
chk("constants.py 有 APP_VERSION", "APP_VERSION" in constants)
for r in ["/api/admin/logs/stats", "/api/admin/logs/errors", "/api/admin/logs/trend",
          "/api/admin/logs/detail/", "/api/admin/logs/clear",
          "/api/admin/logs/files", "/api/admin/logs/files/content",
          "/api/admin/logs/files/clear"]:
    chk("admin_logs 路由存在 %s" % r, r in read(os.path.join(
        ROOT, "app", "backend", "blueprints", "admin_logs.py")))

print()
print("=== 十、两栏分组与「只读别名」机制 ===")
chk("NAV_HUBS 改用 groups 收纳（不再是平铺 items）", "groups: [" in js)
chk("旧的平铺 items 遍历已清除", "(g.items || []).forEach" not in js)
chk("hubItems() 摊平 groups，HUB_OF/PAGE_LABELS 共用一份",
    "function hubItems(" in js
    and "hubItems(g).forEach(it => { HUB_OF" in js
    and "hubItems(g).forEach(it => { PAGE_LABELS" in js)
chk("宫格渲染会插分组标题（JS + CSS 都在）",
    "hub-group-title" in js and ".hub-group-title {" in css)
chk("组名与图标同行（.hub-group flex，不做跨整行标题）",
    "hub-group-items" in js and ".hub-group-items {" in css)
# 桌面端真正要防的是「图标被按整屏宽度拉伸」：
#   - 图标容器 .hub-group-items 必须是 flex 且图标定宽（一旦写网格列就会被拉宽）
#   - 分组容器 .hub-grid 允许分两栏并排（把整页宽度用起来），但列数不得超过 2
# 旧判据是「hub-grid 出现 grid-template-columns 就报错」，会把上面这种
# 「分组两栏 + 图标仍定宽」的合理布局误判成回归（2026-09-29 实测）。
# 判「允许」而不是判「禁止」：写成负向前瞻 `(?!repeat\(\s*[12]\s*,)` 会被正则回溯绕过
# （`\s*` 少吞一个空格，前瞻就在空白处成立，于是合法写法也被判违规）。
_HUB_GRID_BLOCKS = re.findall(r"\.hub-grid\s*\{([^}]*)\}", css)
_HUB_GRID_OK = re.compile(r"grid-template-columns\s*:\s*repeat\(\s*[12]\s*,")
_HUB_GRID_BAD = [
    b for b in _HUB_GRID_BLOCKS
    if "grid-template-columns" in b and not _HUB_GRID_OK.search(b)]
chk("桌面端图标没被按整屏均分（图标容器 flex 定宽，分组最多两栏）",
    not _HUB_GRID_BAD
    and not re.search(r"\.hub-group-items\s*\{[^}]*grid-template-columns", css)
    and re.search(r"\.hub-group-items\s+\.nav-item\s*\{\s*width:\s*\d+px", css)
    is not None)
chk("记录栏 6 个分组标题",
    titles_in(rec) == ['喂养', '睡眠', '日常', '发育', '生活', '健康'])
chk("成长栏 4 个分组标题",
    titles_in(gro) == ['生长', '睡眠', '回顾', '发育'])
chk("只读别名表存在", "const READONLY_ALIAS =" in js)
chk("成长回顾复用 page-milestones 的只读形态",
    re.search(r"READONLY_ALIAS = \{\s*'milestone-review':\s*\{\s*host:\s*'milestones'", js)
    is not None)
chk("milestone-review 没有另建 section（否则撞 DOM id）",
    'id="page-milestone-review"' not in html)
chk("switchPage 用别名换算 section id",
    "const sectionId = alias ? alias.host : page" in js)
chk("switchPage 按进来的入口切形态 class",
    "pageEl.classList.toggle(cfg.cls" in js)
chk("loadPageData 认只读别名页", "case 'milestone-review':" in js)
chk("renderMilestones 有只读分支（不绑编辑点击）",
    "if (review) return;" in js and "isMilestoneReview()" in js)
chk("只读形态 CSS 存在", "#page-milestones.only-view" in css)
chk("里程碑页标题有 id，供两种形态切文案", 'id="milestonesPageTitle"' in html)

print()
print("=== 十一、每个入口都有落点（防死入口白屏）===")
# NAV_HUBS 里每个 item 的 page，要么有 #page-xxx，要么在
# GROWTH_TAB_PAGES / HEALTH_TAB_PAGES / READONLY_ALIAS 里。
# 少一个就会点下去白屏 —— 这是本项目最容易复发的一类 bug。
_nav_start = js.index("const NAV_HUBS = [")
_nav_end = js.index("\n];", _nav_start)
nav_block = js[_nav_start:_nav_end]
html_pages = set(re.findall(r'id="page-([a-z0-9\-]+)"', html))
alias_keys = set(re.findall(r"'([a-z0-9\-]+)':\s*\{\s*host:", js))
nav_entries = re.findall(r"\{ page: '([a-z0-9\-]+)', label: '([^']*)'", nav_block)
missing = []
for p, lbl in nav_entries:
    if p in html_pages or p in alias_keys:
        continue
    if p not in missing:
        missing.append("%s(%s)" % (p, lbl))
chk("全部 %d 个入口都有 section 或别名落点%s"
    % (len(nav_entries), "" if not missing else "，缺：" + "、".join(missing)),
    not missing)
chk("别名页都没有独立 section（会撞 DOM id）",
    all(('id="page-%s"' % a) not in html for a in alias_keys))
chk("switchPage / renderRelatedLinks 都做了别名换算",
    "const sectionId = alias ? alias.host : page" in js
    and "const hostPage = alias ? alias.host : page" in js)

print()
print("=== 十二、「相关功能」链接的完整性 ===")
# PAGE_LINKS 引用的 id 必须都能取到中文名，否则按钮上会直接显示英文 page id。
# 2026-09-19 深审抓到：健康档案页的相关功能冒出了 temperature / med-reminder / vaccines
# —— 它们已并入 Tab、不在 NAV_HUBS 里，PAGE_LABELS 取不到 key 就 fallback 成 id 本身。
_pl = js[js.index('const PAGE_LINKS'):]
_pl = _pl[:_pl.index('\n};')]
_targets = set(re.findall(r"'([a-z0-9\-]+)'", _pl))


def page_links_of(page_id):
    """取 PAGE_LINKS 里某一项配置的链接文本（没配返回空串）。"""
    m = re.search(r"^\s*'?%s'?:\s*\[([^\]]*)\]" % re.escape(page_id), _pl, re.M)
    return m.group(1) if m else ''

_nav_ids = set(re.findall(r"page: '([a-z0-9\-]+)'", nav_block))
_alias_ids = set(re.findall(r"'([a-z0-9\-]+)':\s*\{\s*host:", js))
_lb = js[js.index('const PAGE_LABELS'):]
_lb = _lb[:_lb.index('function renderRelatedLinks')]
_manual_ids = set(re.findall(r"'?([a-z0-9\-]+)'?:\s*'[^']+'", _lb))
_known = _nav_ids | _alias_ids | _manual_ids
_missing = sorted(t for t in _targets if t not in _known)
chk("PAGE_LINKS 引用的 id 都有中文名（否则按钮显示英文 id）%s"
    % ("" if not _missing else "，缺：" + "、".join(_missing)), not _missing)
chk("renderRelatedLinks 认全三种别名表（漏认会残留上一个页面的链接）",
    "READONLY_ALIAS[page] || GROWTH_TAB_PAGES[page] || HEALTH_TAB_PAGES[page]" in js)
chk("别名页优先用自己的链接配置（否则会显示指向自己的按钮）",
    "PAGE_LINKS[page] || PAGE_LINKS[hostPage]" in js)
chk("拆开的两半互相指路（录↔看）",
    "milestone-review" in page_links_of("milestones")
    and "milestones" in page_links_of("milestone-review")
    and "growth-chart" in page_links_of("growth")
    and "growth" in page_links_of("growth-chart"))

# ==================== 十三、合规入口：协议卡片必须真的在设置页里 ====================
# 背景（2026-10-05 实踩，代价是合规要件当场失效而所有门禁全绿）：
# 「隐私与协议」这张卡片被写在了 `</html>` **之后**。浏览器容错会把它并回 <body>，
# 但**不会并进任何 .page** ⇒ 设置页里根本没有这个入口，用户找不到《用户协议》。
# 当时没有任何门禁能发现它：check_dom_ids 只看 id 在不在（在）、
# verify_nav_layout 只查导航落点、真机渲染只覆盖换尿布/趴睡/出牙。
# 同一次还有两个连带问题，一起锁住：
#   · 卡片头用的 `.settings-card-head-icon` 在 CSS 里**根本没有定义** ⇒ 图标裸奔；
#   · `bindLegalSettings()` 只在 DOMContentLoaded + setTimeout(0) 里调一次，
#     那时 /api/consent/required 还没回来 ⇒ href 恒为 "#"、版本号恒为 "—"。
# ⚠ 先剥注释再判，并且取**最后一个** </html>：注释里写一句「结束标签之后」
# 就能把朴素实现带偏（本次实测：我自己的注释里出现过 `</html>` 字样，
# 断言拿第一个当分界，于是把正常内容全报成了「游离在文档外」的假红）。
_html_nc = re.sub(r"<!--.*?-->", "", html, flags=re.S)
_idx = _html_nc.rfind("</html>")
_after_html = _html_nc[_idx + len("</html>"):] if _idx >= 0 else _html_nc
_stray = re.findall(r"<[a-zA-Z][^>]*>", _after_html)
chk("</html> 之后没有元素标签（写在那里等于游离在 .page 之外，页面里根本看不到）%s"
    % ("" if not _stray else "，发现：" + " ".join(_stray[:3])), not _stray)

_m = re.search(r'<section class="page" id="page-settings">(.*?)</section>', html, re.S)
_settings_html = _m.group(1) if _m else ""
for _id in ("settingsLegalLink", "settingsPrivacyLink", "withdrawConsentBtn"):
    chk("设置页里有 #%s（合规入口要能被用户找到）" % _id,
        ('id="%s"' % _id) in _settings_html)
chk("协议入口只在设置页出现一次（重复 id 会让绑定只生效一半）",
    html.count('id="settingsLegalLink"') == 1 and html.count('id="withdrawConsentBtn"') == 1)
chk("设置页文案里有「隐私与协议」卡片标题", "隐私与协议" in _settings_html)

# 卡片头图标类必须在 CSS 里有定义（凭空造类名 = 图标裸奔且没人会发现）
_icon_classes = set(re.findall(r'<span class="(settings-card-[a-z\-]*icon[a-z\-]*)"', html))
_icon_missing = sorted(c for c in _icon_classes if ("." + c) not in css)
chk("卡片头图标类在 CSS 里有定义%s"
    % ("" if not _icon_missing else "，未定义：" + "、".join(_icon_missing)), not _icon_missing)

# 协议链接与版本号必须在拿到 /api/consent/required 之后回填
_init = js[js.index("async function initConsentGate"):]
_init = _init[:_init.index("\n}\n")]
chk("initConsentGate 拿到协商信息后调 bindLegalSettings()（否则链接恒为 #、版本恒为 —）",
    "_consentInfo = res.data" in _init and "bindLegalSettings()" in _init)
chk("bindLegalSettings 有兜底 URL（后端没起来也不能留一个死的 #）",
    "App.apiBase + '/legal/terms.html'" in js and "App.apiBase + '/legal/privacy.html'" in js)

print()
print("通过" if ok else "存在失败项")
sys.exit(0 if ok else 1)