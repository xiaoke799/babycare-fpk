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
    missing_now = sorted(risky - have)

    old_js = git_show('app/frontend/js/app.js')
    old_html = git_show('app/frontend/index.html')
    if old_js is None or old_html is None:
        print('无法取到 HEAD 版本，只输出当前缺失：')
        for i in missing_now:
            print('  MISSING', i)
        return 0
    missing_old = set(js_ids(old_js, safe_only=True) - html_ids(old_html))
    new_missing = [i for i in missing_now if i not in missing_old]

    print('JS 引用 id 数：%d（其中无 ?. 保护的：%d），HTML 定义 id 数：%d'
          % (len(used), len(risky), len(have)))
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
