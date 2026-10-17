#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""看诊摘要的「打印范围 + 页面边界」静态校验。

背景（2026-10-17 用户提出）：
  「我觉得你把这个随访放在这个档案里面一起打印是不是有点不太合理？」

  第一轮理解成「打印时别把随访带出去」，做了打印隔离；用户随即澄清
  「你把这个做的这个一起是不是不太合理」—— 指的是**位置**：
  随访规划和看诊摘要本来就不该长在同一页。所以两件事都做了。

  看诊摘要页（#page-clinic）原来有三块：摘要本体（动态渲染）、健康随访规划、
  自定义倒计时。后两块是静态 DOM 直接挂在 section 里，所以永远跟在报告下面：
    · 健康随访规划：家长自己排的长期日程，带「高危早产儿」勾选与预约日期；
    · 自定义倒计时：生日 / 旅行这类私人事件。
  语义上，摘要 = 带去医院给医生看的**回顾**报告，随访 = 家长自己的**未来**日程，
  两个方向相反。

  处理：
  1. 随访规划搬到「健康档案 → 随访」Tab（第 12 个 Tab，紧跟「体检」），
     首页那个「儿保复诊 · N 天后」的 chip 改为跳 `switchPage('followup')`；
  2. 自定义倒计时搬到记录栏新开的「工具」组（`#page-countdowns`），
     同组还收了原本塞在系统栏的安抚音效 —— 两个都是「用的小功能」，
     既不是记录也不是报表，也不是设置；
  3. 打印用**白名单**：`#page-clinic > :not(.clinic-summary)` 一律不打印。
     这样以后 clinic 页再加块，默认不进打印件。

判据（19 条）：
  1. print 媒体块里存在 clinic 页的白名单规则，且是 display:none !important
  2. 白名单只放行 .clinic-summary
  3. 打印出处行 .clinic-print-foot 仍在渲染（「数据截止到哪天」的交代不能丢）
  4. 打印页头 .clinic-print-head 仍标 no-print
  5. 「打印报告」按钮所在卡片仍标 no-print
  6. 看诊摘要页里已经没有随访卡
  7. 看诊摘要页里已经没有倒计时卡
  8. #page-countdowns 存在，表单与列表都在
  9. 随访卡在健康档案的 #tab-followup 里
 10. 健康档案有「随访」Tab 按钮
 11. loadHealthTabData 认 followup（不认就永远停在「加载中...」）
 12. HEALTH_TAB_PAGES 登记了 followup 别名（chip 与相关链接靠它跳）
 13. 首页随访 chip 跳 followup
 14. case 'clinic' 不调 loadFollowupPlan
 15. case 'clinic' 不调 loadCountdowns
 16. loadPageData 有 case 'countdowns'
 17. 首页倒计时 chip 跳 countdowns
 18~19. 负向对照 T1~T8（改坏一处，确认对应判据精确转红）

用法：
    python .workbuddy/scripts/verify_print_scope.py
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


def strip_comments(s):
    return re.sub(r"/\*[\s\S]*?\*/", "", s)


def at_media_blocks(css, head):
    """取所有 @media <head> 的块体（按大括号配平，能处理嵌套）。"""
    out = []
    for m in re.finditer(re.escape(head) + r"\s*\{", css):
        i = m.end() - 1
        depth = 0
        for j in range(i, len(css)):
            if css[j] == "{":
                depth += 1
            elif css[j] == "}":
                depth -= 1
                if depth == 0:
                    out.append(css[i + 1:j])
                    break
    return out


def run_checks(html, css, js):
    results = []

    def chk(label, cond):
        results.append((label, bool(cond)))

    css_nc = strip_comments(css)
    html_nc = re.sub(r"<!--[\s\S]*?-->", "", html)

    # 只看含 #page-clinic 的那个 print 块（文件里还有一段历史遗留的 @media print）
    _print_blocks = [b for b in at_media_blocks(css_nc, "@media print") if "#page-clinic" in b]
    chk("存在针对 #page-clinic 的 print 样式块", bool(_print_blocks))

    # 白名单规则：选择器 + 声明一起取
    _rules = []
    for b in _print_blocks:
        for m in re.finditer(r"([^{}]+)\{([^}]*)\}", b):
            _rules.append((m.group(1).strip(), m.group(2)))

    _white = [(sel, decl) for sel, decl in _rules
              if re.search(r"#page-clinic\s*>\s*:not\(\s*\.clinic-summary\s*\)", sel)]
    chk("print 里有 clinic 页白名单（#page-clinic > :not(.clinic-summary)）", bool(_white))
    chk("白名单是 display:none !important（少 !important 会被页级 display 规则压回去）",
        bool(_white) and all("display" in d and "none" in d and "!important" in d
                             for _, d in _white))

    # 白名单不能把随访 / 倒计时也放进来（写成 :not(.a):not(.b) 就等于放行它们）
    _leak = [sel for sel, _ in _white if re.search(r"followup|countdown", sel)]
    chk("白名单没有放行随访卡 / 倒计时卡%s"
        % ("" if not _leak else "，混进：" + "、".join(_leak)), not _leak)

    # 出处行与页头
    chk("打印出处行 .clinic-print-foot 仍在渲染（交代数据截止日期）",
        "clinic-print-foot" in js)
    chk("打印页头 .clinic-print-head 仍标 no-print（它是屏幕上的说明，不该印）",
        re.search(r'class="clinic-print-head\s+no-print"', html_nc) is not None)
    chk("「打印报告」按钮所在卡片仍标 no-print",
        re.search(r'class="clinic-card\s+no-print"[\s\S]{0,400}?window\.print\(\)', js) is not None)

    # 看诊摘要页里不该再有随访卡 —— 2026-10-17 已搬到「健康档案 → 随访」Tab。
    # 这条同时守住「搬回去」这个回退：白白把两块私用内容重新塞进给医生的档案页。
    # （倒计时还没搬，只靠白名单挡在打印件外，所以它必须仍在 clinic 页 ——
    #   哪天搬走了这条会红，提醒把判据一起改，而不是让判定悄悄失去意义。）
    _clinic = ""
    m = re.search(r'<section class="[^"]*\bpage\b[^"]*"\s+id="page-clinic">([\s\S]*?)</section>', html_nc)
    if m:
        _clinic = m.group(1)
    chk("看诊摘要页里已经没有随访卡（已搬到健康档案 → 随访 Tab）",
        'id="followupCard"' not in _clinic)
    chk("看诊摘要页里已经没有倒计时（已搬到记录栏「工具 → 事件倒计时」）",
        'class="countdown-card"' not in _clinic)
    chk("倒计时页 #page-countdowns 存在，表单与列表都在",
        'id="page-countdowns"' in html_nc
        and all(('id="%s"' % i) in html_nc for i in ["cdTitle", "cdDate", "cdAddBtn", "cdList"]))
    chk("随访卡在健康档案的 #tab-followup 里",
        re.search(r'id="tab-followup">[\s\S]*?id="followupCard"', html_nc) is not None)

    # ---- 搬家的接线：按钮 / 数据分发 / 别名页 / 首页入口，少一环就是「点了没反应」----
    chk("健康档案有「随访」Tab 按钮",
        re.search(r'class="health-tab[^"]*"\s+data-tab="followup"', html_nc) is not None)
    chk("loadHealthTabData 认 followup（不认就永远停在「加载中...」）",
        re.search(r"case 'followup':\s*loadFollowupPlan\(\)", js) is not None)
    chk("HEALTH_TAB_PAGES 登记了 followup 别名（首页 chip 与相关链接靠它跳）",
        re.search(r"'followup':\s*\{\s*host:\s*'health',\s*tab:\s*'followup'\s*\}", js) is not None)
    chk("首页随访 chip 跳 followup 而不是旧的 clinic",
        "is-followup\" onclick=\"switchPage('followup')\"" in js)
    # 判据要抓「clinic 那支不再加载随访」，不能去数 loadFollowupPlan() 出现几次 ——
    # 它本来就有 5 处调用（定义 / 保存后重载 / 清空后重载 / 早产儿开关 / Tab 分发），
    # 数次数既脆弱又证明不了任何事（第一版就是这么写错的）。
    _clinic_case = ""
    _mc = re.search(r"case 'clinic':([\s\S]{0,300}?)break;", js)
    if _mc:
        _clinic_case = _mc.group(1)
    chk("看诊摘要页不再加载随访数据（case 'clinic' 里不调 loadFollowupPlan）",
        bool(_clinic_case) and "loadFollowupPlan" not in _clinic_case)
    # 倒计时同理：搬走了就不该在 clinic 那支里刷数据（看不到的 DOM 白跑一次请求）
    chk("看诊摘要页不再加载倒计时（case 'clinic' 里不调 loadCountdowns）",
        bool(_clinic_case) and "loadCountdowns" not in _clinic_case)
    chk("事件倒计时有自己的 loadPageData 分支",
        re.search(r"case 'countdowns':\s*loadCountdowns\(\)", js) is not None)
    chk("首页倒计时 chip 跳 countdowns（不再跳看诊摘要）",
        "onclick=\"switchPage('countdowns')\"" in js)

    return results


def selftest(html, css, js):
    print()
    print("=== 负向对照（改坏一处，确认对应判据转红）===")
    bad_ok = True

    def expect_red(name, new_html, new_css, must_fail_label, new_js=None):
        nonlocal bad_ok
        res = run_checks(new_html, new_css, js if new_js is None else new_js)
        hit = [lbl for lbl, ok in res if not ok]
        want = [lbl for lbl in hit if must_fail_label in lbl]
        flag = bool(want)
        print(("  [OK]   " if flag else "  [FAIL] ") + name)
        if not flag:
            bad_ok = False
            print("         期望转红的判据：" + must_fail_label)
            print("         实际转红：" + ("、".join(hit) if hit else "（无）"))

    # T1 整条白名单删掉（回到「只隐藏 .no-print」的原状，随访会被一起打印）
    c1 = re.sub(r"#page-clinic\s*>\s*:not\(\s*\.clinic-summary\s*\)\s*\{[^}]*\}", "", css)
    expect_red("T1 删掉 clinic 页白名单", html, c1, "print 里有 clinic 页白名单")

    # T2 白名单写成只隐藏页头（等于摘要之外的块全部照打）
    c2 = css.replace("#page-clinic > :not(.clinic-summary)", "#page-clinic > .clinic-print-head")
    expect_red("T2 白名单改成只隐藏打印页头", html, c2, "print 里有 clinic 页白名单")

    # T3 把随访卡也放行（:not(.a):not(.b) 的写法最容易被误加）
    c3 = css.replace("#page-clinic > :not(.clinic-summary)",
                     "#page-clinic > :not(.clinic-summary):not(.followup-card)")
    expect_red("T3 白名单放行随访卡", html, c3, "白名单没有放行随访卡")

    # T4 去掉 !important（会被 #page-clinic{display:block !important} 压回去）
    c4 = re.sub(r"(#page-clinic\s*>\s*:not\(\s*\.clinic-summary\s*\)\s*\{[^}]*?)\s*!important",
                r"\1", css)
    expect_red("T4 去掉白名单的 !important", html, c4, "白名单是 display:none !important")

    # T5 把随访卡塞回看诊摘要页（回退到「档案里夹着家长日程」的旧样子）
    h5 = html.replace(
        '<div class="clinic-summary" id="clinicSummary">',
        '<div class="followup-card" id="followupCard"></div>\n'
        '                <div class="clinic-summary" id="clinicSummary">', 1)
    expect_red("T5 把随访卡塞回看诊摘要页", h5, css, "看诊摘要页里已经没有随访卡")

    # T6 打印页头丢了 no-print
    h6 = html.replace('class="clinic-print-head no-print"', 'class="clinic-print-head"')
    expect_red("T6 打印页头丢了 no-print", h6, css, "打印页头 .clinic-print-head 仍标 no-print")

    # T7 看诊摘要页又去加载随访数据（搬走了却还在刷）
    js7 = js.replace("case 'clinic':\n            loadClinicSummary();",
                     "case 'clinic':\n            loadFollowupPlan();\n            loadClinicSummary();")
    expect_red("T7 看诊摘要页又加载随访数据", html, css,
               "看诊摘要页不再加载随访数据", js7)

    # T8 把倒计时卡塞回看诊摘要页
    h8 = html.replace(
        '<div class="clinic-summary" id="clinicSummary">',
        '<div class="countdown-card"></div>\n'
        '                <div class="clinic-summary" id="clinicSummary">', 1)
    expect_red("T8 把倒计时卡塞回看诊摘要页", h8, css, "看诊摘要页里已经没有倒计时")

    return bad_ok


def main():
    html = read(HTML_PATH)
    css = read(CSS_PATH)
    js = read(JS_PATH)

    print("=== 一、打印件只留摘要本体 ===")
    results = run_checks(html, css, js)

    ok = True
    for lbl, passed in results:
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
