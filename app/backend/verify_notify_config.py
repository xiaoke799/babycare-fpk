#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""推送配置的「前后端字段对齐」静态检查。

背景：后端 `notifications.CONFIG_KEYS` 有 20 项配置，前端设置页只暴露了其中一部分。
2026-09-19 发现 `vaccine_reminder_days` / `pushplus_topic` / `title_prefix`
三项后端一直支持、设置页却改不了（用户看不见也改不动）。
这类「配置项存在但没有入口」的问题人眼很难发现，固化成脚本。

另有一类反向问题：前端提交了后端不认的 key → 后端静默忽略，用户以为存上了。

用法：
    python app/backend/verify_notify_config.py
退出码 0 = 全部通过，1 = 有失败项。
"""
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(os.path.dirname(HERE))
JS_PATH = os.path.join(PROJ, "app", "frontend", "js", "app.js")
HTML_PATH = os.path.join(PROJ, "app", "frontend", "index.html")
NBPY_PATH = os.path.join(HERE, "blueprints", "notifications.py")

ok = True


def chk(label, cond):
    global ok
    print(("  [OK]   " if cond else "  [FAIL] ") + label)
    if not cond:
        ok = False


def read(p):
    with io.open(p, "r", encoding="utf-8") as f:
        return f.read()


js = read(JS_PATH)
html = read(HTML_PATH)
nbpy = read(NBPY_PATH)

# ---- 后端认的 key ----
m = re.search(r"CONFIG_KEYS\s*=\s*\((.*?)\)", nbpy, re.S)
config_keys = set(re.findall(r'"([a-z_0-9]+)"', m.group(1))) if m else set()

# ---- 前端 save：key -> element id ----
m = re.search(r"function saveNotificationConfig\s*\([^)]*\)\s*\{", js)
i = m.start()
depth, k = 0, js.index("{", i)
while k < len(js):
    if js[k] == "{":
        depth += 1
    elif js[k] == "}":
        depth -= 1
        if depth == 0:
            break
    k += 1
save_body = js[i:k + 1]
# 只在 `const data = { ... };` 这个对象字面量里找，否则正则会跨过
# `body: JSON.stringify(data)` 后面的 `.then()` 块，把里头的 getElementById 也算进来（假阳性）。
_data_block = re.search(r"const data\s*=\s*\{(.*?)\n\s*\};", save_body, re.S)
save_scope = _data_block.group(1) if _data_block else save_body
# 值可能被 parseInt/parseFloat 包着（如 vaccine_reminder_days: parseInt(document.getElementById(...))），
# 所以中间用 [^,}]*? 跳过前缀 —— 只写 \s* 会漏掉这几项，误报成"没有入口"。
save_pairs = re.findall(
    r"([a-z_0-9]+):[^,}]*?document\.getElementById\('([A-Za-z0-9_]+)'\)", save_scope)

# ---- 前端 load：后端 key -> element id ----
m = re.search(r"function loadNotificationConfig\s*\([^)]*\)\s*\{", js)
i = m.start()
depth, k = 0, js.index("{", i)
while k < len(js):
    if js[k] == "{":
        depth += 1
    elif js[k] == "}":
        depth -= 1
        if depth == 0:
            break
    k += 1
load_body = js[i:k + 1]
load_pairs = re.findall(r"set(?:Val|Check)\('([A-Za-z0-9_]+)',\s*cfg\.([a-z_0-9]+)\)", load_body)
load_by_key = {ck: eid for eid, ck in load_pairs}

# ---- HTML 里定义的 id ----
html_ids = set(re.findall(r'id="([A-Za-z0-9_]+)"', html))

# 有意不暴露给用户的技术参数（后端可改、但没必要做界面）
INTERNAL_ONLY = {"reminder_check_interval", "notify_timeout"}

print("=== 一、前端提交的 key 后端都认 ===")
saved_keys = {k_ for k_, _ in save_pairs}
unknown = sorted(saved_keys - config_keys)
chk("saveNotificationConfig 提交的 %d 个 key 全在 CONFIG_KEYS 里%s"
    % (len(saved_keys), "" if not unknown else "，多的：" + "、".join(unknown)),
    not unknown)

print()
print("=== 二、前端取的 DOM id 都真实存在 ===")
# save_pairs 是 (key, eid)、load_pairs 是 (eid, key)，取 id 的位置不同，别混着解
used_ids = {eid for _, eid in save_pairs} | {eid for eid, _ in load_pairs}
missing_el = sorted(used_ids - html_ids)
chk("提交/回填用到的 %d 个元素 id 都在 index.html 里%s"
    % (len(used_ids), "" if not missing_el else "，缺：" + "、".join(missing_el)),
    not missing_el)

print()
print("=== 三、读写对称（能改的也要能看到当前值）===")
only_save = sorted(saved_keys - set(load_by_key))
chk("每个能提交的 key 都会回填到界面%s"
    % ("" if not only_save else "，只写不读：" + "、".join(only_save)),
    not only_save)

print()
print("=== 四、后端配置项都有入口（或有意的白名单）===")
no_ui = sorted(config_keys - saved_keys - INTERNAL_ONLY)
chk("CONFIG_KEYS 里没有「能存但改不了」的项%s"
    % ("" if not no_ui else "，未暴露：" + "、".join(no_ui)),
    not no_ui)
print("      （有意不暴露的技术参数：%s）" % "、".join(sorted(INTERNAL_ONLY)))

print()
print("=== 五、数值型配置的范围与后端一致 ===")
_int_block = re.search(r"_INT_RANGES\s*=\s*\{(.*?)\}", nbpy, re.S).group(1)
int_ranges = {k: (lo, hi) for k, lo, hi in
              re.findall(r'"([a-z_0-9]+)":\s*\((\d+),\s*(\d+)\)', _int_block)}
for key, eid in [('vaccine_reminder_days', 'notifyVaccineDays')]:
    if key in int_ranges:
        lo, hi = int_ranges[key]
        mm = re.search(r'id="%s"[^>]*?min="(\d+)"[^>]*?max="(\d+)"' % eid, html)
        chk("%s 的 min/max(%s/%s) 与后端 (%s,%s) 一致"
            % (key, mm.group(1) if mm else '?', mm.group(2) if mm else '?', lo, hi),
            bool(mm) and mm.group(1) == lo and mm.group(2) == hi)

print()
print("通过" if ok else "存在失败项")
sys.exit(0 if ok else 1)
