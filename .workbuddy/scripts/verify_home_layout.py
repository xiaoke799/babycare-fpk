#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""首页行布局 + 宫格分组形态的静态校验。

背景（2026-10-17 用户反馈「视觉上感觉很乱，但说不清哪儿乱」）：
  拆下来是 7 处，其中 4 处是布局结构造成的，全部改在 2026-10-17：
   ① 桌面端快捷按钮 `flex-direction:row` + 每块 76px 宽 → 「喂奶」被压成
      「喂」在上「奶」在下（图标 42 + 间隙 12 + 左右内边距 32 已经超过 76px）。
   ② 首页左右两栏各自纵向堆叠 → 左栏高约 250px、右栏约 400px，
      左下空出 150px 一大片（行高只由高的一侧决定）。
   ③ 宫格「组名占左侧 54px 固定列 + 图标在右」→ 组内只有 1~3 个图标时
      右侧空一大片，组名那列又白占宽度。
   ④ CSS 栅格写死列数 + 加项 = 孤行。
  另外发现一个此前没人注意的 bug：手机端用 `display:contents` 摊平两栏 +
  靠 `order` 拼阅读序，但 order 名单里**漏了搜索条**（默认 order:0），
  于是手机端第一块是搜索框、问候语被挤到它下面。

这些都属于「功能全好、但看起来不对」的静默视觉问题：接口能通、门禁全绿、
点哪都能点，只有人眼看截图才发现。所以固化成静态判据。

判据分六节：
  一、首页不靠 order 拼手机阅读序
  二、块序 = DOM 顺序（含「哪两块并排」）
  三、快捷按钮不竖排
  四、并排两块的高度可比
  五、宫格不套卡片、不写死列数
  六、首页关键 DOM id 都在

⚠ 新增/移动首页区块时，第二节的块序表**要一起改**，这是有意的成本：
   块序被打乱正是本脚本要防的事，不能让它悄悄变。

用法：
    python .workbuddy/scripts/verify_home_layout.py
退出码 0 = 全过，1 = 有失败项。
"""
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HTML_PATH = os.path.join(ROOT, "app", "frontend", "index.html")
CSS_PATH = os.path.join(ROOT, "app", "frontend", "css", "main.css")
JS_PATH = os.path.join(ROOT, "app", "frontend", "js", "app.js")


def read(path):
    with io.open(path, "r", encoding="utf-8") as f:
        return f.read()


def strip_css_comments(s):
    return re.sub(r"/\*[\s\S]*?\*/", "", s)


def strip_html_comments(s):
    return re.sub(r"<!--[\s\S]*?-->", "", s)


def dashboard_html(html):
    m = re.search(r'<section class="[^"]*\bpage\b[^"]*"\s+id="page-dashboard">([\s\S]*?)</section>', html)
    return m.group(1) if m else ""


def css_block(pattern, css):
    """取某个选择器块的声明体（只取第一个匹配）。"""
    m = re.search(pattern, css, re.M)
    return m.group(1) if m else None


def row_ranges(dhtml):
    """返回每个 .home-row 的 [start, end) 区间。

    用「下一个 home-row 起点」当上一个的结束边界，最后一个到容器末尾 ——
    比平衡标签扫描简单，且 .home-row 不允许嵌套（嵌套会让并排语义失效），
    真嵌套了下面的块序/同排断言会一起报红。
    """
    starts = [m.start() for m in re.finditer(r'<div class="home-row">', dhtml)]
    out = []
    for i, s in enumerate(starts):
        e = starts[i + 1] if i + 1 < len(starts) else len(dhtml)
        out.append((s, e))
    return out


def in_same_row(ranges, a, b):
    for s, e in ranges:
        if s <= a < e and s <= b < e:
            return True
    return False


def run_checks(html, css, js):
    """返回 [(label, ok), ...]。拆成函数是为了 selftest 能对变形文本复跑。"""
    results = []

    def chk(label, cond):
        results.append((label, bool(cond)))

    html_nc = strip_html_comments(html)
    css_nc = strip_css_comments(css)
    dhtml = dashboard_html(html_nc)

    # ---------- 一、首页不靠 order 拼手机阅读序 ----------
    chk("首页无 .home-col 双栏容器（HTML）",
        "home-col" not in dhtml)
    chk("CSS 已无 .home-col 选择器",
        re.search(r"^\s*\.home-col\b", css_nc, re.M) is None)
    # order 只在 #page-dashboard 段内禁；设置页用的是同一手法但名单完整，不在此列
    _dash_rules = re.findall(r"#page-dashboard[^{]*\{[^}]*\}", css_nc)
    chk("首页 CSS 无 order 重排（块序靠 DOM，不靠 order）",
        not any("order:" in b for b in _dash_rules))
    chk("首页容器是 flex 纵向流（手机与桌面同一套块序）",
        re.search(r"#page-dashboard\.page\.active\s*\{[^}]*display:\s*flex[^}]*flex-direction:\s*column",
                  css_nc) is not None)

    # ---------- 二、块序 = DOM 顺序 ----------
    ORDER = [
        ("home-header", "问候头部"),
        ("home-toolbar", "搜索+日期"),
        ("home-quick-actions", "快捷记录"),
        ("home-memories", "那年今天"),
        ("homeStatsTitle", "今日概览"),
        ("homeMilkCard", "今日奶量"),
        ("homeBodySection", "身体数据"),
        ("homeCountdowns", "倒计时"),
        ("home-recent", "最近记录"),
    ]
    pos = {}
    missing_anchor = []
    for token, label in ORDER:
        idx = -1
        for probe in ('id="%s"' % token, token):
            j = dhtml.find(probe)
            if j >= 0 and (idx < 0 or j < idx):
                idx = j
        if idx < 0:
            missing_anchor.append(label)
        else:
            pos[label] = idx
    chk("首页 9 个区块都在%s" % ("" if not missing_anchor else "，缺：" + "、".join(missing_anchor)),
        not missing_anchor)
    if not missing_anchor:
        seq = [pos[label] for _, label in ORDER]
        bad = [ORDER[i][1] for i in range(1, len(seq)) if seq[i] < seq[i - 1]]
        chk("块序与阅读顺序一致（header→搜索日期→快捷→那年今天→概览→奶量→身体→倒计时→最近）%s"
            % ("" if not bad else "，错位：" + "、".join(bad)),
            not bad)
    else:
        chk("块序与阅读顺序一致（区块缺失，无法判序）", False)

    _rr = row_ranges(dhtml)
    chk("首页有两处并排行（概览+奶量 / 身体+倒计时）", len(_rr) == 2)
    chk("今日概览与今日奶量在同一行",
        len(_rr) == 2 and in_same_row(_rr, dhtml.find('id="homeStatsTitle"'),
                                      dhtml.find('id="homeMilkCard"')))
    chk("身体数据与倒计时在同一行",
        len(_rr) == 2 and in_same_row(_rr, dhtml.find('id="homeBodySection"'),
                                      dhtml.find('id="homeCountdowns"')))

    # ---------- 三、快捷按钮不竖排 ----------
    # 竖排的真因：桌面按钮宽度 < 图标 + 间隙 + 左右内边距，文字被压成逐字换行。
    # 判据抓的是根因（最小列宽），不是症状（flex-direction）—— 只要宽度够，row 方向是对的。
    #
    # ⚠ 取「所有」带 #page-dashboard 前缀的 .home-quick-actions 块，不能只取第一个：
    # 平板断点（768~1023）里也有一条同名规则，只写 gap、没有 grid-template-columns，
    # 而它排在桌面断点前面 —— 第一版用 re.search 取首个，拿到的正是这条，
    # 于是判据恒红（T1 也「转红」，等于什么也没验证）。
    _base_qa = css_block(r"^\.home-quick-actions\s*\{([^}]*)\}", css_nc) or ""
    _dash_qa_blocks = re.findall(r"#page-dashboard\s+\.home-quick-actions\s*\{([^}]*)\}", css_nc)
    _mins = [int(m) for b in _dash_qa_blocks for m in re.findall(r"minmax\(\s*(\d+)px", b)]
    chk("桌面快捷按钮最小列宽 ≥ 120px（低于这个「图标+间隙+内边距」就把文字挤成竖排）",
        bool(_mins) and min(_mins) >= 120)
    chk("快捷区用 auto-fit（写死列数会在加按钮时留下孤行）",
        "auto-fit" in _base_qa)
    chk("桌面快捷按钮文字不换行（white-space: nowrap）",
        "nowrap" in (css_block(r"#page-dashboard\s+\.home-quick-label\s*\{([^}]*)\}", css_nc) or ""))

    # ---------- 四、并排两块的高度可比 ----------
    chk("倒计时卡有标题行（并排时与「身体数据」的标题对齐，否则两块顶边差 ~30px）",
        "content:" in (css_block(r"\.home-countdowns::before\s*\{([^}]*)\}", css_nc) or ""))
    chk("奶量卡是纵向 flex（会被 JS 设成 display:flex，写 block 会丢掉垂直居中）",
        "flex-direction: column" in (css_block(r"^\.home-milk-card\s*\{([^}]*)\}", css_nc) or ""))
    # 只在 renderHomeMilkTarget 体内判 —— 文件里还有别的 `card.style.display = 'block'`
    # （另一张卡），全文件搜会误伤，判据就恒红。
    _mt_fn = ""
    if "function renderHomeMilkTarget" in js:
        _mt_fn = js[js.index("function renderHomeMilkTarget"):]
        _end = _mt_fn.find("\n}\n")
        if _end > 0:
            _mt_fn = _mt_fn[:_end]
    chk("奶量卡的显示态写的是 flex 不是 block（内联样式会盖掉 CSS 的 display:flex）",
        "card.style.display = 'flex'" in _mt_fn and "card.style.display = 'block'" not in _mt_fn)

    # ---------- 五、宫格不套卡片、不写死列数 ----------
    # 套卡片会让 1~2 个图标的组在卡内留一大块空白，边框还把它框起来，
    # 读起来就是「这张卡没排满」——比原来的「行尾空洞」更显眼（同一天试过又退回）。
    _hg = css_block(r"^\.hub-group\s*\{([^}]*)\}", css_nc) or ""
    chk("宫格组不画卡片底（不设 background / border）",
        "background" not in _hg and "border" not in _hg.split("border-radius")[0])
    _hgi = css_block(r"^\.hub-group-items\s*\{([^}]*)\}", css_nc) or ""
    chk("宫格图标容器是 flex（写网格列会把图标按整屏宽度均分）",
        "display: flex" in _hgi and "grid-template-columns" not in _hgi)
    chk("宫格图标定宽（宽度随内容走，不被容器拉伸）",
        re.search(r"\.hub-group-items\s+\.nav-item\s*\{[^}]*width:\s*\d+px", css_nc) is not None)
    _desk_grids = re.findall(r"^\s+\.hub-grid\s*\{([^}]*)\}", css_nc, re.M)
    chk("桌面宫格保持两栏（单列长条会把 6 组拉得很长）",
        any("grid-template-columns: repeat(2" in b for b in _desk_grids))

    # ---------- 六、首页关键 DOM id 都在 ----------
    IDS = ["homeGreetingLabel", "homeBabyName", "homeBabyCard", "homeBabyInitial",
           "homeProfileName", "homeBabyMeta", "homeQuickActions", "batchRecordBtn",
           "homeSearchBar", "homeSearchInput", "homeSearchBtn", "homeDateBar",
           "homeDateInput", "homeMemories", "memoriesTitle", "memoriesSub",
           "memoriesList", "homeStatsTitle", "homeFeedingCount", "homeSleepTime",
           "homeDiaperCount", "homeMilkCard", "milkTargetEditBtn", "homeMilkPercent",
           "homeMilkBar", "homeMilkNow", "homeMilkTarget", "homeMilkDetail",
           "homeBodySection", "homeBodyCard", "homeLatestHeight", "homeLatestWeight",
           "homeLatestBmi", "homeLatestHead", "homeBodyDate", "homeBodyRecordBtn",
           "homeCountdowns", "homeRecentTitle", "homeRecentList"]
    _lost = [i for i in IDS if ('id="%s"' % i) not in dhtml]
    chk("首页 39 个 DOM id 全部保留%s" % ("" if not _lost else "，缺：" + "、".join(_lost)),
        not _lost)

    # JS 里对首页块的引用仍能对上（重排 DOM 时最容易漏搬）
    chk("JS 仍按 id 操作首页各块（homeMilkCard / homeCountdowns / homeRecentList）",
        all(x in js for x in ["getElementById('homeMilkCard')",
                              "getElementById('homeCountdowns')",
                              "getElementById('homeRecentList')"]))

    return results


def selftest(html, css, js):
    """负向对照：故意改坏一处，确认对应对判据会转红。

    只证明「判据能看见这类破坏」，不代表覆盖全部改坏方式。
    """
    print()
    print("=== 负向对照（改坏一处，确认对应判据转红）===")
    bad_ok = True

    def expect_red(name, new_html, new_css, must_fail_label):
        nonlocal bad_ok
        res = run_checks(new_html, new_css, js)
        hit = [lbl for lbl, ok in res if not ok]
        want = [lbl for lbl in hit if must_fail_label in lbl]
        flag = bool(want)
        print(("  [OK]   " if flag else "  [FAIL] ") + name)
        if not flag:
            bad_ok = False
            print("         期望转红的判据：" + must_fail_label)
            print("         实际转红：" + ("、".join(hit) if hit else "（无）"))

    # T1 桌面最小列宽退回 76px（「喂奶」竖排的原始写法）
    c1 = re.sub(r"(#page-dashboard\s+\.home-quick-actions\s*\{[^}]*minmax\(\s*)120",
                r"\g<1>76", css)
    expect_red("T1 桌面最小列宽改回 76px", html, c1, "桌面快捷按钮最小列宽")

    # T2 首页加回 order 重排
    c2 = re.sub(r"(#page-dashboard\.page\.active\s*\{[^}]*flex-direction:\s*column)",
                r"\1; order: 1", css, count=1)
    expect_red("T2 首页加回 order 重排", html, c2, "首页 CSS 无 order 重排")

    # T3 去掉一处 home-row（概览与奶量不再同排）
    h3 = html.replace('<div class="home-row">', '<div class="home-row-x">', 1)
    expect_red("T3 去掉一处并排行", h3, css, "首页有两处并排行")

    # T4 宫格组加回卡片底
    c4 = re.sub(r"(^\.hub-group\s*\{)",
                r"\1\n    background: var(--card-bg);", css, count=1, flags=re.M)
    expect_red("T4 宫格组加回卡片底", html, c4, "宫格组不画卡片底")

    # T5 倒计时卡去掉 ::before 标题
    c5 = re.sub(r"\.home-countdowns::before\s*\{[^}]*\}", "", css)
    expect_red("T5 倒计时卡去掉标题行", html, c5, "倒计时卡有标题行")

    # T6 换块序（把「最近记录」整块搬到快捷前面）
    _i = html.find("<!-- 最近记录")
    _j = html.find("</section>", _i) if _i >= 0 else -1
    h6 = html
    if _i >= 0 and _j > _i:
        _recent = html[_i:_j]
        h6 = html[:_i] + html[_j:]
        _qa = h6.find('<div class="home-quick-actions"')
        h6 = h6[:_qa] + _recent + h6[_qa:]
    expect_red("T6 打乱块序（最近记录搬到最前）", h6, css, "块序与阅读顺序一致")

    return bad_ok


def main():
    html = read(HTML_PATH)
    css = read(CSS_PATH)
    js = read(JS_PATH)

    results = run_checks(html, css, js)
    # 分节条数必须与 run_checks 里 chk 的发出顺序一致；多出来的会落到最后一段
    sections = [
        ("=== 一、首页不靠 order 拼手机阅读序 ===", 4),
        ("=== 二、块序 = DOM 顺序 ===", 5),
        ("=== 三、快捷按钮不竖排 ===", 3),
        ("=== 四、并排两块的高度可比 ===", 3),
        ("=== 五、宫格不套卡片、不写死列数 ===", 4),
        ("=== 六、首页关键 DOM id 都在 ===", 2),
    ]
    idx = 0
    ok = True
    for title, n in sections:
        print()
        print(title)
        for lbl, passed in results[idx:idx + n]:
            print(("  [OK]   " if passed else "  [FAIL] ") + lbl)
            if not passed:
                ok = False
        idx += n
    if idx < len(results):
        print()
        for lbl, passed in results[idx:]:
            print(("  [OK]   " if passed else "  [FAIL] ") + lbl)
            if not passed:
                ok = False

    if not selftest(html, css, js):
        ok = False

    print()
    print("通过" if ok else "存在失败项")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
