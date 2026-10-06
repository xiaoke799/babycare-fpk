#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
D11：逐页删除守卫 —— 「这一页的删除入口是否真的有防误删确认」。

为什么要有这一维（全局 audit_deletes 不够）：
  全局审查是「所有 delete*/remove* 函数逐个判」，它只能保证
  **被它扫到的函数**有守卫。漏掉的情形有三种，逐页视角能补上：
    1. 删除入口藏在 **HTML 内联 onclick** 里（`onclick="deleteFeeding(3)"`），
       而入口函数是模板字符串里的匿名逻辑 ⇒ 全局按函数名扫不到。
    2. 页面调用的是**通用删除器**（`deleteRecord('feeding', id)`），
       按类型分发到各条deleteXxx —— 全局看是「有守卫」，
       但**具体某一页的某类型**可能走了没守卫的分支。
    3. 页面渲染出的删除按钮**指向一个不存在的函数**（改名后忘了改模板）
       ⇒ 全局扫描完全看不到，点下去才报错。

判据设计（每条都必须能双向对照，否则不写进来）：
  - 只认**页面真实渲染出的删除入口**（模板字符串里的 onclick/按钮），
    不按「这个页面应该有删除功能」去猜。
  - 内联 onclick 的函数名必须**能在 js 里找到定义**，否则报 GAP
    （这条专治第 3 类，改名后模板没跟着改）。
  - 删除函数必须落在「有守卫」集合里；守卫形态认全：
    confirm( / safetyCodeModal / prompt( / showModal，且确认后判 .success。

已知边界（不硬补，硬补会引假阳性）：
  - 删除入口由数据驱动的通用组件统一渲染时，只能按「模板里出现的
    onclick 名」判定，无法区分同一按钮在不同页面走不同分支的情况。
  - 通过事件委托（addEventListener + dataset）在运行时绑定的删除，
    不在模板字符串里，静态扫不到。这类由 verify_list_error_render.py
    的动态探针覆盖。

退出码：0 = 无 GAP；1 = 有 GAP（打印明细）。
"""
import ast
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
JS = os.path.join(ROOT, 'app', 'frontend', 'js', 'app.js')
HTML = os.path.join(ROOT, 'app', 'frontend', 'index.html')
LEDGER = os.path.join(ROOT, '.workbuddy', 'scripts',
                      'feature_workbench_ledger.py')

# 只动内存 / 未提交表单，不丢数据 ⇒ 不需要确认
HARMLESS = {'removeLoadingMessage', 'removeBatchRow', 'removeCustomModel'}
# 非删除语义的动词（去掉即"删除"语义但不删数据）
NOT_DELETE = {
    'removeEventListener', 'removeAttribute', 'removeClass', 'removeItem',
    'removeActive', 'removeLoading', 'removeSelection',
}


def strip_comments(js):
    """只删注释，保留字符串原文，位置与原文严格对齐。"""
    out = list(js)
    i, n = 0, len(js)
    prev_sig = ''
    while i < n:
        c = js[i]
        if c in ' \t\r\n':
            out[i] = c
            i += 1
            continue
        if c in '"\'`':
            if prev_sig in ('', '(', '[', '{', ',', ':', '=', ';', '!', '&',
                            '|', '?', '+', '-', '*', '%', '<', '>', '~',
                            '^') or not prev_sig:
                q, i = c, i + 1
                while i < n:
                    if js[i] == '\\':
                        i += 2
                        continue
                    if js[i] == q:
                        i += 1
                        break
                    i += 1
                prev_sig = '"'
                continue
            prev_sig = c
            i += 1
            continue
        if c == '/' and i + 1 < n and js[i + 1] == '/':
            while i < n and js[i] != '\n':
                out[i] = ' '
                i += 1
            continue
        if c == '/' and i + 1 < n and js[i + 1] == '*':
            j = i + 2
            while j < n and not (js[j] == '*' and j + 1 < n
                                 and js[j + 1] == '/'):
                if js[j] != '\n':
                    out[j] = ' '
                j += 1
            for k in range(i, min(j + 2, n)):
                if js[k] != '\n':
                    out[k] = ' '
            i = j + 2
            continue
        prev_sig = c
        i += 1
    return ''.join(out)


def func_body(js, name):
    """取顶层 function name(...) {...} 的函数体。"""
    m = re.search(r'^(?:async\s+)?function\s+' + re.escape(name) +
                  r'\s*\([^)]*\)[^:\n]*:\n', js, re.M)
    if not m:
        m = re.search(r'^(?:async\s+)?function\s+' + re.escape(name) +
                      r'\s*\([^)]*\)\s*\{', js, re.M)
        if not m:
            return None
        start = js.index('{', m.start())
    else:
        start = m.end()
    depth, i = 0, start
    while i < len(js):
        if js[i] == '{':
            depth += 1
        elif js[i] == '}':
            depth -= 1
            if depth == 0:
                return js[start + 1:i]
        i += 1
    return None


def guarded(js, name, depth=0):
    """
    判定删除函数 name 是否有防误删守卫。返回 (是否通过, 说明)。
    沿 perform* 调用跟一层（真实删除常在被转发的那个函数里）。
    """
    if depth > 2:
        return True, '跳转过深，按通过处理'
    body = func_body(js, name)
    if body is None:
        return None, '函数体取不到'
    if name in HARMLESS:
        return True, '只动内存/未提交表单'
    if not re.search(r"method\s*:\s*['\"]DELETE['\"]", body):
        for sub in re.findall(r'\b(perform[A-Z][A-Za-z0-9_]*)\s*\(', body):
            if func_body(js, sub):
                ok, why = guarded(js, sub, depth + 1)
                if ok is None:
                    return None, why
                if not ok:
                    return False, '真实删除在 %s：%s' % (sub, why)
                return True, '真实删除在 %s：%s' % (sub, why)
        return True, '不发 DELETE 请求（内存/未提交）'
    if re.search(r'safetyCodeModal|prompt\(', body):
        return True, '有安全码（强于 confirm）'
    if re.search(r'confirm\(', body):
        i = body.find('confirm(')
        judged = judges_success(body[i:])
        return judged, ('confirm + 判success' if judged
                       else '有 confirm 但没判 res.success')
    return False, '删数据但无任何确认'


def judges_success(seg):
    """
    判断代码片段里是否**判了请求结果的 success**。

    ⚠ **不能只搜 `.success`**（负向对照栽过的地方）：
      `showToast.success('已删除')` 里也含 `.success`，
      子串匹配会把「删完无条件弹成功提示」判成「判了 success」⇒ 假绿。
      请求结果对象在项目里统一是回调的 `res`/`result`/`resp`，
      所以只认这几个标识符上的 `.success`。
      （`showToast.success` / `toast.success` 这类带前缀调用，
        前缀不在白名单里，自然被排除。）
    """
    return bool(re.search(
        r'\b(?:res|result|resp|response|r)\s*\.\s*success\b', seg))


def load_features():
    """从台账脚本里读 FEATURES（单一真值源，不重复维护页面清单）。"""
    src = open(LEDGER, encoding='utf-8').read()
    m = re.search(r'^FEATURES\s*=\s*\[(.*?)^\]', src, re.M | re.S)
    if not m:
        sys.exit('D11 FAIL: 读不到台账 FEATURES 清单（判据失效）')
    out = []
    for line in m.group(1).split('\n'):
        mm = re.match(r"\s*\('([\w-]+)',\s*'([^']*)',", line)
        if mm:
            out.append((mm.group(1), mm.group(2)))
    if not out:
        sys.exit('D11 FAIL: FEATURES 解析出 0 项（判据失效）')
    return out


def page_delete_entries(js, page):
    """
    从页面相关代码里挖删除入口。
    范围：页面入口函数体 + 该页的渲染函数（沿调用链下钻 2 层）。

    ⚠ 入口定位**直接复用台账的 entry_function()**，不在这里重写。
      第一版自己按 `'feeding': function` 之类的模式找页面，抽出 0 长度
      ⇒ 报告「0 个删除函数均有守卫」—— 典型的假绿。
      entry_function 已处理 case 穿透/ case 之间夹注释等坑，
      页面入口的唯一定义处就在它那里（单一真值源）。
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location('_ledger', LEDGER)
    led = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(led)
    except Exception as e:                # 台账脚本自身坏了 ⇒ 判据失效
        sys.exit('D11 FAIL: 台账脚本加载失败（%s）—— 判据失效' % e)
    fn, blk = led.entry_function(js, page)

    text = []
    seen = set()

    def walk(fname, depth):
        # 下钻 3 层：`loadFeeding → renderFeedingList → renderFeedingItems`，
        # 删除按钮在第 3 层。限 2 层时 feeding/diaper/photos/health 四页
        # 抽到 0 个 onclick，看着像「这些页没有删除入口」——
        # 实际是**没下钻到**，又是一次假绿。
        if depth > 3 or fname in seen or not fname:
            return
        seen.add(fname)
        b = func_body(js, fname)
        if b is None:
            return
        text.append(b)
        for sub in callees(b):
            walk(sub, depth + 1)

    walk(fn, 0)
    if blk:
        text.append(blk)
    return '\n'.join(text)


PREFIXES = ('load', 'render', 'init', 'draw', 'show')
CB_TAIL = (',', ')', ']', '}', ';')


def callees(body):
    """
    取函数体里调用的（load|render|init|draw|show）* 函数名。

    ⚠ **必须同时认「裸引用传参」**（本判据栽过的地方）：
      `items.map(renderFeedingItems).join('')` —— 这里 renderFeedingItems
      后面**没有左括号**，是当函数值传给 map 的。
      第一版只找 `名字(` ⇒ 喂养页下钻停在 renderFeedingList，
      删除按钮（就在 renderFeedingItems 里）整段没进样本，
      于是「喂养页没有删除入口」—— 假绿。
      对照：D10 的踩坑是同一类（漏一种写法就少抽一层链条）。

    裸引用的判据：名字后面跟 , ) ] } ; 或空白（而不是 `(` 或 `.`），
    且它前面不是 `.`（排除 obj.renderX 的属性访问）。
    """
    out = set()
    pat = r'(?<![.\w$])((?:' + '|'.join(PREFIXES) + r')[A-Z]\w*)'
    for m in re.finditer(pat, body):
        name = m.group(1)
        tail = body[m.end():]
        nxt = tail.lstrip()
        # 直接调用 fn(...)
        if nxt.startswith('('):
            out.add(name)
            continue
        # 声明处/对象属性/关键字参数名，一律不算调用
        if not nxt or nxt[0] in CB_TAIL:
            # 裸引用传参：往前看是不是紧跟在 ( , 之后（参数位置）
            prev = body[:m.start()].rstrip()
            if prev and prev[-1] in ('(', ','):
                out.add(name)
    return out


DISPATCHER = {'deleteRecord'}


def guarded_dispatch(js, name, page_body, depth=0):
    """
    通用删除器（deleteRecord('feeding', id)）的守卫判定。

    ⚠ 守卫判在**函数级**，不逐分支（这是本判据栽过的地方）：
      deleteRecord 的 confirm 在函数第一行（对所有类型统一生效），
      而各类型分支只负责「删完刷新哪一页」。
      第一版按 `type === 'growth'` 切片后再找 confirm ⇒ 切不到开头
      ⇒ 报「分支内无确认」，是**假阳性**（实际 confirm 就在第 2 行）。
      逐分支判守卫只在「分支各自发 DELETE 请求」时才有意义，
      而那属于别的函数，不是这个分发器。

    分支切片仍要看一眼，但用途是「该类型确实被处理」，
    没命中分支只记 note，不判红。
    """
    if depth > 2:
        return True, ['跳转过深，按通过处理']
    body = func_body(js, name)
    if body is None:
        return None, ['函数体取不到']

    # 1) 函数级守卫（真正决定用户安不安全的就是这一层）
    notes = []
    has_req = bool(re.search(r"method\s*:\s*['\"]DELETE['\"]", body))
    if has_req:
        if re.search(r'safetyCodeModal|prompt\(', body):
            notes.append('函数级：安全码')
        elif re.search(r'confirm\(', body):
            i = body.find('confirm(')
            judged = judges_success(body[i:])
            notes.append('函数级：confirm + 判success' if judged
                         else '函数级：confirm 但没判 success')
            if not judged:
                return False, notes
        else:
            return False, ['函数级：删数据但无任何确认']
    else:
        notes.append('函数级：不发 DELETE 请求')

    # 2) 本页用到的类型是否真被处理（只记 note，不判红）
    types = set()
    for m in re.finditer(
            r'on\w+\s*=\s*["\'][^"\']*?' + re.escape(name) +
            r'\(\s*["\'](\w+)["\']', page_body, re.S):
        types.add(m.group(1))
    for t in sorted(types):
        pat = None
        for p in (r"type\s*===?\s*['\"]" + t + r"['\"]",
                  r"['\"]" + t + r"['\"]\s*===?\s*type",
                  r"case\s+['\"]" + t + r"['\"]\s*:"):
            if re.search(p, body):
                pat = p
                break
        notes.append('%s: %s' % (t, '已处理' if pat else '**未在分发器里出现**'))
    return True, notes


def main():
    if not os.path.exists(JS):
        sys.exit('D11 FAIL: 前端 js 不存在 %s' % JS)
    raw = open(JS, encoding='utf-8').read()
    js = strip_comments(raw)
    features = load_features()

    defined = set(re.findall(r'^(?:async\s+)?function\s+(\w+)\s*\(', js, re.M))
    gaps, checked = [], 0
    for page, label in features:
        body = page_delete_entries(js, page)
        if not body:
            continue
        # 挖页面里的删除入口：onclick="deleteX(...)" 或 onXXX="deleteX("
        calls = set()
        for m in re.finditer(
                r'on\w+\s*=\s*["\']([^"\']*?)["\']', body, re.S):
            for fn in re.findall(r'\b((?:delete|remove)[A-Za-z0-9_]*)\s*\(',
                                 m.group(1)):
                if fn not in NOT_DELETE:
                    calls.add(fn)
        if not calls:
            continue
        for fn in sorted(calls):
            checked += 1
            if fn not in defined:
                gaps.append((page, label, fn, '模板里的删除函数在 js 中不存在'))
                continue
            if fn in DISPATCHER:
                ok, notes = guarded_dispatch(js, fn, body)
                if ok:
                    continue
                for n in (notes if isinstance(notes, list) else [notes]):
                    head, _, tail = n.partition('：')
                    gaps.append((page, label, '%s[%s]' % (fn, head), tail))
                continue
            ok, why = guarded(js, fn)
            if ok is None:
                gaps.append((page, label, fn, why))
            elif not ok:
                gaps.append((page, label, fn, why))

    if gaps:
        print('D11 命中 %d 处删除守卫缺口（共核 %d 个页面删除入口）：'
              % (len(gaps), checked))
        for g in gaps:
            print('  %-16s %-8s %s —— %s' % g)
        return 1
    print('D11 OK: %d 个入口的 %d 个删除函数均有防误删守卫'
          % (len(features), checked))
    return 0


if __name__ == '__main__':
    # 判据脚本自身先过语法自检 —— 崩了却输出"全绿"是最难查的一类
    ast.parse(open(os.path.abspath(__file__), encoding='utf-8').read())
    sys.exit(main())
