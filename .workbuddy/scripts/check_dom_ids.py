#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""扫描 app.js 里引用但 index.html 中不存在的 DOM id。

只报「缺失」，并且和 git HEAD 版本对比，标出哪些是本次改动新引入的
（历史遗留的缺失不计入新债）。动态注入的节点（弹窗里的 editXxx 等）必然缺失，
所以结果需要人工判断，重点是 NEW。
"""
import io
import re
import subprocess
import sys

ROOT = 'D:/育儿板块/babycare-fpk'
JS = ROOT + '/app/frontend/js/app.js'
HTML = ROOT + '/app/frontend/index.html'


def read(path):
    return io.open(path, encoding='utf-8').read()


def js_ids(src, safe_only=False):
    """返回 JS 里引用的 id。

    safe_only=False -> 所有引用（默认）
    safe_only=True  -> 只返回「没有用 ?. 保护」的引用，即节点缺失时会真抛错的那些
    """
    pat = re.compile(
        r"getElementById\(\s*['\"]([A-Za-z0-9_\-]+)['\"]\s*\)(\?\.)?")
    out = set()
    for m in pat.finditer(src):
        guarded = m.group(2) is not None
        if safe_only and guarded:
            continue
        out.add(m.group(1))
    return out


def html_ids(src):
    pat = re.compile(r'\bid\s*=\s*["\']([A-Za-z0-9_\-]+)["\']')
    return set(pat.findall(src))


def git_show(path):
    out = subprocess.run(['git', 'show', 'HEAD:' + path], cwd=ROOT,
                         capture_output=True)
    if out.returncode != 0:
        return None
    return out.stdout.decode('utf-8', 'replace')


def fn_sentinel_protected(src, existing):
    """函数级哨兵：函数开头 `if (!document.getElementById('X')) return;` 且 X 确实不存在。

    函数一开头就退出，函数体内**所有**引用都不会执行，等效于整体保护 ——
    不必要求每个 id 各自带 `?.`。（2026-09-29 实测：initAllergyTest 用
    allergyTestModal 当哨兵，护住 allergyTestForm / allergyDay / allergyStatus
    等 5 个节点；只认「同一 id 的哨兵」会把它们全误报成会抛错。）

    只看函数体前 800 字符，避免把函数深处的无关判空也算成哨兵。
    """
    protected = set()
    starts = [m.start() for m in re.finditer(
        r"(?m)^(?:async\s+)?function\s+[A-Za-z_$][\w$]*", src)]
    if not starts:
        return protected
    for a, b in zip(starts, starts[1:] + [len(src)]):
        head = src[a:min(b, a + 800)]
        sids = re.findall(
            r"if\s*\(\s*!\s*document\.getElementById\(\s*['\"]([A-Za-z0-9_\-]+)['\"]\s*\)\s*\)\s*return",
            head)
        # 哨兵节点必须确实缺失，函数才会真的提前 return
        if not any(s not in existing for s in sids):
            continue
        protected |= set(re.findall(
            r"getElementById\(\s*['\"]([A-Za-z0-9_\-]+)['\"]", src[a:b]))
    return protected


def dynamic_ids(src):
    """JS 里动态生成的节点 id（弹窗、列表项等的模板字符串）。

    这些 id 在 index.html 里必然找不到，但运行时由 showModal/render 插进 DOM，
    属于**合法存在**。不把它们算作「已定义」，弹窗类代码会被成片误报。
    （2026-09-29 实测：quickCustomAmount / amountLabel 就是这类，
    且调用处还带 ?. 与 if 判空，本来就抛不了错。）
    """
    return set(re.findall(r'id\s*=\s*["\']([A-Za-z0-9_\-]+)["\']', src))


def guarded_by_variable(src, ident):
    """先用变量接住再判空，也算保护。

    写法如 `const b = getElementById('x'); if (b) {...}` —— 比 `?.` 更彻底
    （判空后才用，缺失时整段跳过）。只认 `?.` 会把这种写法误报成风险。
    """
    m = re.search(
        r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*document\.getElementById\(\s*['\"]"
        + re.escape(ident) + r"['\"]\s*\)", src)
    if not m:
        return False
    var = re.escape(m.group(1))
    return any(re.search(p, src) for p in (
        r"if\s*\(\s*!\s*" + var + r"\s*\)",
        r"if\s*\(\s*" + var + r"\s*\)",
        var + r"\s*\?\.",
        var + r"\s*&&",
    ))


def guarded_by_sentinel(src, ident):
    """函数开头写 `if (!document.getElementById('x')) return;` 也算保护。

    只认 `?.` 会把这种写法误报成风险，而它其实比 `?.` 更彻底（直接跳过整个函数）。
    """
    return re.search(
        r"if\s*\(\s*!\s*document\.getElementById\(\s*['\"]" + re.escape(ident)
        + r"['\"]\s*\)\s*\)\s*return", src) is not None


def main():
    js, html = read(JS), read(HTML)
    used, have = js_ids(js), html_ids(html)
    # 只关心「没加 ?. 保护、节点缺失就抛 TypeError」的引用
    risky = js_ids(js, safe_only=True)
    # 用变量接住再 if 判断（如 const b = getElementById('x'); if (b) ...）也算保护
    risky = {i for i in risky if not guarded_by_sentinel(js, i)}
    risky = {i for i in risky if not guarded_by_variable(js, i)}
    risky -= fn_sentinel_protected(js, have)
    injected = dynamic_ids(js)
    missing_now = sorted(risky - have - injected)

    old_js = git_show('app/frontend/js/app.js')
    old_html = git_show('app/frontend/index.html')
    if old_js is None or old_html is None:
        print('无法取到 HEAD 版本，只输出当前缺失：')
        for i in missing_now:
            print('  MISSING', i)
        return 0
    # 旧版本用同一套口径算，否则「旧版本来就报、现在修好了」会被误当成新债
    old_risky = js_ids(old_js, safe_only=True)
    old_risky = {i for i in old_risky if not guarded_by_sentinel(old_js, i)}
    old_risky = {i for i in old_risky if not guarded_by_variable(old_js, i)}
    old_risky -= fn_sentinel_protected(old_js, html_ids(old_html))
    missing_old = set(old_risky - html_ids(old_html) - dynamic_ids(old_js))
    new_missing = [i for i in missing_now if i not in missing_old]

    print('JS 引用 id 数：%d（其中无保护的：%d），HTML 定义 id 数：%d，JS 动态注入：%d'
          % (len(used), len(risky), len(have), len(injected)))
    print('会真抛错的缺失：%d（其中本次新增：%d）' % (len(missing_now), len(new_missing)))
    if new_missing:
        print('\n>>> NEW（本次改动引入，必须处理）：')
        for i in new_missing:
            print('   ', i)
    else:
        print('\n>>> NEW：无')
    print('\n历史既有缺失（本次不管）：')
    print('   ' + (', '.join(i for i in missing_now if i in missing_old) or '无'))
    return 1 if new_missing else 0


if __name__ == '__main__':
    sys.exit(main())
