# -*- coding: utf-8 -*-
"""
D8 判据：动态插入的控件事件必须能绑上。

铁律原文：init 里逐个 getElementById + addEventListener 都绑不上。
原因：getElementById 拿到 null，可选链 `?.` 静默跳过 ⇒ 控件点了没反应，
不报错、不留痕。

判据口径（不判死代码，只判**活代码里绑不上的绑定**）：
  1. 收集「元素 id 的全部产地」：
       静态 index.html 的 id="..." + app.js 里所有字面 id="..."
     （app.js 里的字面 id 覆盖所有 innerHTML 模板串插入的元素，
       跨函数插入的元素也算「有产地」）
  2. 扫每个顶层函数里 getElementById(id)?.addEventListener / .addEventListener
  3. id 在静态 HTML 里 → 安全（页面加载即存在）
     id 全 js 字面出现过 → 安全（某处会动态插入）
     两者都没有 → **绑了个谁都不创建的元素**
  4. 但若该函数整体有哨兵守卫（`if (!document.getElementById('X')) return`
     形式的早期返回，且 X 与目标 id 同族），判为「已知死功能，不判红」

⚠ 三轮踩坑记录（都在这里，别再栽）：
  · 第一轮只查「本函数 innerHTML 插入的 id」⇒ 报 15 处，全是假阳性
    （innerHTML 写 sfModalBody，绑的是 solidFoodForm，各是各的容器）。
    ⇒ 必须区分「本函数插入的」与「静态 HTML 里就有的」，两者混为一谈必炸。
  · 第二轮改用「静态 id ∪ 全 js 字面 id」⇒ 报 3 处 initAllergyTest /
    initBatchRecord / initShareCard。人工核实**三处全是已知死功能**
    （作者自己在代码注释里写明了），其中两处已有 `if (!el) return` 哨兵。
    ⇒ 判据必须排掉「函数级哨兵已拦」的情况，否则会长期红。
  · 第三轮探针自身一度输出「0 处命中」而看似通过，实为 `\\(` 在 raw
    string 里变成匹配字面反斜杠 + K&R 风格 `function f(){` 没被识别
    ⇒ 654 个函数全被判空体。**「0 条」永远先怀疑脚本没跑起来。**

双向对照见 verify_event_bindings.py 的 --selftest。
"""
import os
import re
import sys

FE = r'D:\育儿板块\babycare-fpk\app\frontend'
JS = os.path.join(FE, 'js', 'app.js')
HTML = os.path.join(FE, 'index.html')

ID_RE = re.compile(r'\bid\s*=\s*["\']([\w-]+)["\']')
# getElementById('x')  后面接可选链或直接点号，再 addEventListener
BOUND_RE = re.compile(
    r"getElementById\(\s*['\"]([\w-]+)['\"]\s*\)"
    r"(?:\s*\?\s*\.\s*|\s*)\.\s*addEventListener")
# 哨兵：if (!document.getElementById('x')) return;  /  if (!x) return;
GUARD_RE = re.compile(
    r"if\s*\(\s*!\s*(?:document\s*\.\s*)?(?:getElementById\(\s*['\"]([\w-]+)['\"]"
    r"\s*\)|\w+)\s*\)\s*return")


def func_body(js, name):
    """取顶层 function name(...) {...} 的函数体 + 起始行号。

    ⚠ 必须同时认两种签名（本判据栽过）：
      Python 式 `function f():\n` 与 JS 式 `function f() {\n` 都有，
      只认前者会让 K&R 写法的函数体全判空（假绿）。
    """
    m = re.search(r'^(?:async\s+)?function\s+' + re.escape(name) +
                  r'\s*\([^)]*\)[^:\n]*:\n', js, re.M)
    if not m:
        m = re.search(r'^(?:async\s+)?function\s+' + re.escape(name) +
                      r'\s*\([^)]*\)\s*\{', js, re.M)
        if not m:
            return None, 0
        start = js.index('{', m.start())
    else:
        start = m.end()
    d, i = 0, start
    while i < len(js):
        if js[i] == '{':
            d += 1
        elif js[i] == '}':
            d -= 1
            if d == 0:
                return js[start + 1:i], js[:start].count('\n') + 1
        i += 1
    return None, 0


def scan():
    js = open(JS, encoding='utf-8').read()
    html = open(HTML, encoding='utf-8').read()

    static_ids = set(ID_RE.findall(html))
    js_ids = set(ID_RE.findall(js))
    known = static_ids | js_ids

    defined = sorted(set(re.findall(r'^(?:async\s+)?function\s+(\w+)\s*\(', js, re.M)))
    bound_total, gaps, guarded_skips = 0, [], 0
    for f in defined:
        b, ln = func_body(js, f)
        if not b:
            continue
        # 函数级哨兵：出现即视为「本函数整体被前置条件拦住」
        g = GUARD_RE.findall(b)
        sentinel = g[0] if g and g[0] else None
        for m in BOUND_RE.finditer(b):
            bid = m.group(1)
            bound_total += 1
            if bid in known:
                continue
            line = ln + b[:m.start()].count('\n')
            if sentinel:
                guarded_skips += 1
                gaps.append((line, f, bid, '哨兵守卫后仍绑定（疑似死功能残留）'))
                continue
            gaps.append((line, f, bid, '绑了谁都不创建的元素（静默绑不上）'))

    return {
        'n_func': len(defined), 'n_static': len(static_ids),
        'n_js': len(js_ids), 'n_bound': bound_total,
        'gaps': gaps, 'guarded': guarded_skips,
    }


def main():
    r = scan()
    print('自检：顶层函数 %d 个 | 静态 id %d | js 字面 id %d | 逐 id 绑定 %d 处'
          % (r['n_func'], r['n_static'], r['n_js'], r['n_bound']))
    if r['n_bound'] < 10 or r['n_func'] < 100:
        print('D8 FAIL: 扫描量异常偏小 —— 判据自己没跑起来')
        return 2
    if r['gaps']:
        print('D8 命中 %d 处绑定不上的控件事件：' % len(r['gaps']))
        for line, f, bid, why in r['gaps']:
            print('  L%-6d %-26s #%-22s %s' % (line, f, bid, why))
        return 1
    print('D8 OK: %d 处逐 id 绑定全部有元素产地' % r['n_bound'])
    return 0


if __name__ == '__main__':
    sys.exit(main())