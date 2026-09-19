#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""导航分层与「系统诊断」板块的静态校验。

背景（2026-09-19 用户定的规则）：
  1. 记录栏只放「要你填表」的记录功能；成长栏只放「看」的报表。
     凡是录入选成长栏，或报表混进记录栏，都算越界。
  2. 应用信息 / 存储 / 数据库健康 / 日志 / 异常记录，统一收在
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
storage = read(STORAGE_PATH)
constants = read(CONSTANTS_PATH)


def hub_block(name):
    """截出 NAV_HUBS 里某个 hub 的 items 段（到下一个 hub: 为止）。"""
    start = js.index("hub: '%s'" % name)
    nxt = js.find("hub: '", start + 5)
    return js[start: nxt if nxt != -1 else len(js)]


def pages_in(block):
    return re.findall(r"page:\s*'([a-z0-9\-]+)'", block)


print("=== 一、记录栏：只放记录（要填表的）===")
rec = hub_block("record")
rec_pages = pages_in(rec)
chk("记录栏含「成长成就」(milestones)", "milestones" in rec_pages)
chk("记录栏含身高体重的「录」入口 (growth)", "growth" in rec_pages)
chk("记录栏不再是安抚音效的入口 (sounds)", "sounds" not in rec_pages)
for p in ["feeding", "pumping", "sleep-record", "diaper-record", "solidfood",
          "tummytime", "diaper-price", "diary", "photos", "leap",
          "fontanelle", "teeth", "health", "timeline"]:
    chk("记录栏保留 %s" % p, p in rec_pages)

print()
print("=== 二、成长栏：只放报表（看结果的）===")
gro = hub_block("growth")
gro_pages = pages_in(gro)
for p in ["growth", "sleep-analysis", "bmi", "asq", "pattern", "reports"]:
    chk("成长栏保留报表 %s" % p, p in gro_pages)
for p in ["milestones", "firsts"]:
    chk("成长栏不再有记录功能 %s" % p, p not in gro_pages)

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
# 从「系统诊断」那张 settings-card 的开头切起，直到下一张卡（推送通知）为止。
_diag_anchor = html.index("系统诊断")
diag_start = html.rindex('<div class="settings-card">', 0, _diag_anchor)
diag_block = html[diag_start:html.index("<!-- 推送通知设置 -->", diag_start)]
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
print("通过" if ok else "存在失败项")
sys.exit(0 if ok else 1)
