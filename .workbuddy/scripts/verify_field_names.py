#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
D10：前端字段名 vs 真实表列。

背景（本判据存在的原因）：
    后端全是 SELECT * + dict(row) ⇒ 接口返回的字段名就是真实列名。
    前端若读了不存在的列（如 r.duration / r.start_date），
    会被 `|| 0` 或 `.filter()` 静默吞成 0，界面却像有数据 ⇒ 极难发现。
    报表页 diaper 趋势图「从来没画出来过」就是这类问题。

判据必须自己先站得住（踩过的坑）：
  1. 第一版用「URL 片段猜表名」的手写映射 ⇒猜错就全盘错。
     改为**从 blueprints/*.py 的 @bp.route 块里直接抽 SELECT ... FROM 的真实表名**。
  2. 第二版用「函数级表推断」—— 一个函数调 16 个 API 就取 16 张表的列并集，
     任何字段都能在并集里找到 ⇒ 判据恒绿（跑出 183 处全假阳性）。
     改为**变量级赋值点推断**：只认 `const X = ...res.data` 这一行往回找到的那次 api。
  3. 第三版找字段时用 `参数名.字段` 正则，但只看紧邻 tail（`.map(r => r.x)` 之后 20 字符），
     漏掉 `.map(r => Number(r.duration_minutes))` 这种被包一层调用的写法。
     改为在**该变量作用域内**全文搜 `变量名.字段`。

退出码：0 = 无错字段；1 = 有错字段（打印明细）。
"""
import ast
import glob
import os
import re
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))          # -> 仓库根 babycare-fpk/
JS_DIR = os.path.join(ROOT, 'app', 'frontend', 'js')
BACKEND = os.path.join(ROOT, 'app', 'backend')
DB = os.path.join(ROOT, 'app', 'data', 'babycare.db')

# JS/浏览器通用成员名：不是表列，别当错字段报
JS_BUILTIN = {
    # 属性访问 / DOM
    'length', 'classList', 'style', 'dataset', 'id', 'name', 'type', 'value',
    'checked', 'disabled', 'selected', 'href', 'src', 'alt', 'title', 'text',
    'innerHTML', 'innerText', 'textContent', 'parentNode', 'parentElement',
    'children', 'childNodes', 'firstChild', 'lastChild', 'nextSibling',
    'previousSibling', 'nodeType', 'nodeName', 'appendChild', 'removeChild',
    'insertBefore', 'setAttribute', 'getAttribute', 'removeAttribute',
    'querySelector', 'querySelectorAll', 'getElementById', 'addEventListener',
    'removeEventListener', 'closest', 'matches', 'contains', 'focus', 'blur',
    'click', 'submit', 'scrollTop', 'scrollLeft', 'scrollHeight', 'scrollWidth',
    'offsetWidth', 'offsetHeight', 'clientWidth', 'clientHeight',
    'getBoundingClientRect', 'getContext', 'toDataURL', 'width', 'height',
    'top', 'left', 'right', 'bottom', 'x', 'y',
    # 数组 / 字符串 / 对象方法
    'map', 'filter', 'forEach', 'reduce', 'find', 'findIndex', 'some', 'every',
    'includes', 'indexOf', 'join', 'slice', 'splice', 'push', 'pop', 'shift',
    'unshift', 'sort', 'reverse', 'concat', 'flat', 'keys', 'values', 'entries',
    'toString', 'trim', 'split', 'replace', 'replaceAll', 'toFixed', 'padStart',
    'padEnd', 'charAt', 'substring', 'substr', 'startsWith', 'endsWith',
    'toUpperCase', 'toLowerCase', 'match', 'test', 'getTime', 'getDate',
    'getHours', 'getMinutes', 'getMonth', 'getFullYear', 'getSeconds',
    'toISOString', 'then', 'catch', 'finally', 'push', 'assign', 'freeze',
    'hasOwnProperty', 'toLocaleDateString', 'toLocaleTimeString',
    # 前端项目自有对象
    'success', 'data', 'message', 'error', 'timestamp', 'items', 'records',
    'total', 'page', 'pageSize', 'limit', 'offset', 'count', 'status', 'code',
    'preventDefault', 'stopPropagation', 'target', 'currentTarget', 'key',
    'detail', 'payload', 'result', 'resolve', 'reject', 'prototype',
}


# ---------------------------------------------------------------- schema
def declared_columns():
    """
    从 server.py 抽「建表 + 补列」声明的列，**并入** schema 真值源。

    为什么必须并（踩过）：
      app/data/babycare.db 是**运行期产物**，只有服务真正启动过才会被
      server.py 的 `_ensure_column()` 补齐列。本机 db 缺
      diaper_records.skin_condition / brand、pumping_records.left_duration /
      right_duration —— 而这些列在后端 INSERT 和前端读取里都真实存在。
      只信 db 文件 ⇒ 6 处真字段被误报成「不存在」，
      而照着判据去改前端「修」它，才是真把功能改坏。
    真值源 = 代码声明 ∪ db 快照（db 只是代码声明的滞后投影）。
    """
    out = {}                      # {表: {列}}
    spath = os.path.join(BACKEND, 'server.py')
    if not os.path.exists(spath):
        return out
    src = open(spath, encoding='utf-8').read()
    for m in re.finditer(r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)\s*\(',
                         src, re.I):
        t = m.group(1)
        depth, j = 0, m.end() - 1
        while j < len(src):
            if src[j] == '(':
                depth += 1
            elif src[j] == ')':
                depth -= 1
                if depth == 0:
                    break
            j += 1
        cols = set()
        for line in src[m.end() - 1:j].split('\n'):
            line = line.strip().rstrip(',')
            if not line or line.startswith('--'):
                continue
            head = re.match(r'["`\[]?(\w+)["`\]]?\s+(?:TEXT|INTEGER|REAL|'
                             r'NUMERIC|BLOB|DEFAULT|PRIMARY|REFERENCES|'
                             r'UNIQUE|CHECK|NOT|COLLATE)', line, re.I)
            if head:
                cols.add(head.group(1))
        if cols:
            out.setdefault(t, set()).update(cols)
    for m in re.finditer(r"_ensure_column\(\s*['\"](\w+)['\"]\s*,\s*"
                         r"['\"](\w+)", src):
        out.setdefault(m.group(1), set()).add(m.group(2))
    return out


def load_schema():
    """真实表列：{表名: {列名}} = db 实际列 ∪ server.py 声明列"""
    out = {}
    if os.path.exists(DB):
        con = sqlite3.connect(DB)
        try:
            for (t,) in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"):
                out[t] = {r[1] for r in
                          con.execute('PRAGMA table_info("%s")' % t)}
        finally:
            con.close()
    for t, cols in declared_columns().items():
        out.setdefault(t, set()).update(cols)
    return out


# ------------------------------------------------- URL -> 表（从后端抽）
# ------------------------------------------------------------ 后端分析
KV_TABLES = {'settings', 'app_settings'}
SCHEMA_CACHE = {}          # 运行时填充：{表名: {列}}，供 helper_fields 取表列


def route_fields(blk, tables, schema, helpers):
    """
    一个路由「真实返回的字段名」集合。

    后端有两种返回形态，混着判就会误豁免（踩过）：
      A. 行数据：`SELECT * FROM t` + dict(row) ⇒ 字段 = t 的列
      B. 聚合：`COUNT(*) AS count` / `jsonify({'total_count': ...})`
         ⇒ 字段是自造的，既不在表里也不能一律豁免
    早先版本对含 COUNT/GROUP BY 的路由整条豁免，结果 `/api/babies` 这种
    纯 `SELECT *` 的行接口也被放过（它块里恰好有别的 COUNT）。
    正确做法：B 类要**抽出它自造的字段名**当白名单，精确比对。
    """
    fields = set()
    fields_seen = set()
    # A. 行数据路由：任一表被 SELECT *
    star = re.findall(r'SELECT\s+(?:\*|[\w,\s.]*?\*)', blk)
    for t in tables:
        if t in schema:
            fields |= schema[t]
    # B. 聚合自造字段：jsonify 字典键 + SQL 别名 + 字典下标赋值
    for m in re.finditer(r"""['"]([a-z_][a-z0-9_]*)['"]\s*:""", blk):
        fields.add(m.group(1))
    for m in re.finditer(r'\bAS\s+([a-z_][a-z0-9_]*)', blk, re.I):
        fields.add(m.group(1).lower())
    for m in re.finditer(r'\bAS\s+"([a-z_][a-z0-9_]*)"', blk, re.I):
        fields.add(m.group(1).lower())
    for m in re.finditer(r"""['"]([a-z_][a-z0-9_]*)['"]\s*:""", blk):
        fields.add(m.group(1))
    # result["weight_velocity"] = ... / d['x'] = ...
    # 踩过：growth-velocity 全靠这种下标赋值造字段，只认 `'k':` 会把
    # weight_velocity / total_days 等 12 个字段全报成「不存在」⇒ 假阳性。
    for m in re.finditer(r"""\[["']([a-z_][a-z0-9_]*)["']\]\s*=[^=]""", blk):
        fields.add(m.group(1))
    for m in re.finditer(r"""\.update\(\{""", blk):
        pass
    # obj.update({...}) / obj.append({...}) 内的键
    for m in re.finditer(r"""\{([^{}]*?)\}""", blk, re.S):
        for km in re.finditer(r"""['"]([a-z_][a-z0-9_]*)['"]\s*:""", m.group(1)):
            fields.add(km.group(1))
    # D. 路由块里调用的**同文件视图/辅助函数**里造的字段。
    #    两层踩坑：
    #    1. 只扫路由块看不到 `/percentiles` 的 `percentile`
    #       （在 _get_who_percentile 里造的）⇒ 假阳性。
    #    2. 只跟 1 层还漏 **路由别名转发**：`/feedings` 的函数体只有一句
    #       `return list_feeding_records(baby_id)`，SQL 和 dict(row) 都在
    #       被转发的那个视图里 ⇒ 字段集只剩 {data, success}，
    #       于是 feeding_type/amount 全被误报。
    #       ⇒ 递归跟随「本文件视图函数调用」，深度限 2 层。
    for fname in re.findall(r'\b_?[a-z][a-z0-9_]*\s*\(', blk):
        fname = fname[:-1].strip()
        if not fname or fname in BUILTIN_CALLS or fname in fields_seen:
            continue
        fh = helpers.get(fname)
        if fh:
            fields_seen.add(fname)
            fields |= helper_fields(fh)
            # 转发：把这个 helper 体内再调用的视图函数也并进来（再一层）
            for inner in re.findall(r'\b([a-z][a-z0-9_]*)\s*\(', fh):
                ih = helpers.get(inner)
                if ih and inner not in fields_seen and inner not in BUILTIN_CALLS:
                    fields_seen.add(inner)
                    fields |= helper_fields(ih)
                    # 第三层：转发视图自己也会 SELECT * FROM某表
                    for t2 in re.findall(r'\bFROM\s+(\w+)', ih):
                        if t2 in schema:
                            fields |= schema[t2]
    return fields


BUILTIN_CALLS = {
    'if', 'for', 'while', 'print', 'len', 'int', 'str', 'float', 'round',
    'max', 'min', 'sum', 'sorted', 'list', 'dict', 'abs', 'all', 'any',
    'jsonify', 'execute', 'fetchall', 'fetchone', 'get_db', 'json_body',
    'request', 'datetime', 'json', 'os', 're', 'time', 'math', 'statistics',
    'isinstance', 'enumerate', 'zip', 'range', 'strftime', 'get', 'getlist',
    'row_to_dict', 'rows_to_list', 'normalize_author', 'escape', 'quote',
    'sha256', 'md5', 'urlencode', 'parse_qs', 'set', 'tuple', 'type',
    'super', 'filter', 'map', 'sorted', 'next', 'iter', 'append',
}


def helper_fields(fbody):
    """辅助函数/模块级常量里造出的字段名。"""
    out = set()
    for m in re.finditer(r"""['"]([a-z_][a-z0-9_]*)['"]\s*:""", fbody):
        out.add(m.group(1))
    for m in re.finditer(r"""\[["']([a-z_][a-z0-9_]*)["']\]\s*=[^=]""", fbody):
        out.add(m.group(1))
    for m in re.finditer(r'\bAS\s+([a-z_][a-z0-9_]*)', fbody, re.I):
        out.add(m.group(1).lower())
    # 字段声明元组：("duration_minutes", "duration_minutes"), ("end_time", ...)
    # 踩过：/timeline 用模块级 _TIMELINE_SOURCES 声明各类型的字段，
    # 只扫路由块 + 函数体都看不到 ⇒ duration_minutes 被误报 3 次。
    for m in re.finditer(r'\(\s*["\']([a-z_][a-z0-9_]*)["\']\s*,\s*'
                         r'["\']([a-z_][a-z0-9_]*)["\']\s*\)', fbody):
        out.add(m.group(1))
        out.add(m.group(2))
    # 返回字典的位置索引键："timestamp": row[2], "level": row[3]
    # 踩过：error_tracker.get_error_detail() 用 row[N] 造 12 个字段，
    # 只认 `["k"] = ` 下标赋值会全漏 ⇒ admin 日志详情的10 个字段被误报。
    for m in re.finditer(r'["\']([a-z_][a-z0-9_]*)["\']\s*:\s*row\s*\[', fbody):
        out.add(m.group(1))
    # 多行 import 里的符号名（from error_tracker import (\n  a,\n  b\n)）
    # ⚠ 这里原来写成 `:\s*row\s*\.\s*\w+`，右侧没有约束 ⇒
    # `(r.get('skin_condition') for r in records …)` 这类被当键抽出，
    # 而 re.finditer 的 \w+ 又只取到 `re` ⇒ 报出根本不存在的字段 `re`
    # （solid_food_records 那条假阳性就是它）。键只认 `row[` 位置索引。
    for m in re.finditer(r'["\']([a-z_][a-z0-9_]*)["\']\s*:\s*row\s*\[',
                         fbody):
        out.add(m.group(1))
    # SELECT * FROM t ⇒ 整表列都是合法字段
    for m in re.finditer(r'\bFROM\s+(\w+)', fbody):
        t = m.group(1)
        if t in SCHEMA_CACHE:
            out |= SCHEMA_CACHE[t]
    return out


def route_shapes(schema, con):
    """
    返回 {URL模板: 允许字段集合}。
      - 表列（SELECT * 路由）
      - + 聚合自造字段（COUNT AS x / jsonify 字典键）
      - + key-value 表的 key 值
    """
    out = {}
    # 先扫一遍确定哪些路由是 key-value 型（settings 契约），供前端写入侧反推用
    kv_routes = {}
    for f in sorted(glob.glob(os.path.join(BACKEND, 'blueprints', '*.py'))):
        src = open(f, encoding='utf-8').read()
        m = re.search(r'Blueprint\([^,]+,[^,]+(?:,\s*url_prefix="([^"]*)")?',
                      src)
        prefix = (m.group(1) or '') if m else ''
        for blk in re.split(r'@bp\.route\(', src)[1:]:
            um = re.match(r'["\']([^"\']*)["\']', blk.split('\n', 1)[0])
            if not um:
                continue
            full = prefix + um.group(1)
            tables = set(re.findall(r'\bFROM\s+(\w+)', blk))
            tables |= set(re.findall(r'\bJOIN\s+(\w+)', blk))
            tables = {t for t in tables
                      if t in schema and not t.startswith('sqlite')}
            segs = [s for s in full.split('/') if s]
            norm = tuple('<v>' if is_var_seg(s) else s for s in segs)
            kv_routes.setdefault(norm, set()).update(
                t for t in tables if t in KV_TABLES)
    kv_url_fragments = {k for k in kv_routes if kv_routes[k]}
    kv_cache = {}

    for f in sorted(glob.glob(os.path.join(BACKEND, 'blueprints', '*.py'))):
        src = open(f, encoding='utf-8').read()
        # 本文件里的模块级辅助函数 + 模块级常量（供路由块回溯字段）
        # 踩过：/timeline 的字段声明在模块级 _TIMELINE_SOURCES 元组里，
        # 只抽 def 的函数体 ⇒ 看不到 ⇒ duration_minutes 被误报 3 次。
        helpers = {}
        for hm in re.finditer(r'^def\s+(\w+)\s*\([^)]*\)[^:\n]*:\n((?:[ \t]+.*\n|\n)*)',
                             src, re.M):
            helpers[hm.group(1)] = hm.group(2)
        # 模块级常量赋值：NAME = [ ... ] / NAME = ( ... )，**按括号配平抽全文**。
        # 踩过：用 `[^\n]*`逐行拼 ⇒ 第一行后遇到缩进续行就停
        #（负向行首断言 `(?![A-Za-z@#\s])` 把缩进行全排除了），
        # 抽出来只有 '_TIMELINE_SOURCES = [' 21 个字符 ⇒ 字段数 0。
        for cm in re.finditer(r'^([A-Z_][A-Z0-9_]*)\s*=\s*[\[\{]', src, re.M):
            start = cm.end() - 1
            openc = cm.group(0)[-1]
            closec = {'[': ']', '{': '}'}[openc]
            depth, j = 0, start
            while j < len(src):
                if src[j] == openc:
                    depth += 1
                elif src[j] == closec:
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            helpers.setdefault(cm.group(1), src[cm.start():j + 1])
        # 跨文件 helper（error_tracker / logger / growth_utils …）
        # 踩过三处，逐条都是"抽不到就静默放过"：
        # 1. /admin/logs/detail 的字段是 error_tracker.get_error_detail() 里
        #    用 `row[0]`…`row[11]` 位置索引造的，既不在路由块也不在本文件。
        # 2. `from error_tracker import (` 是**跨行括号式** import。
        # 3. 直接把整个源码压成一行再 `from\s+(\w+)\s+import\s*\(?([^)\n#]*)`：
        #    压行后 `)` 提前截断 ⇒ 符号名单被截，而 error_tracker 这条 import
        #    被并进上一条 flask 的符号区（`Blueprint, request, jsonify
        #    from error_tracker im...`）⇒ flask.py 在 BACKEND 下不存在 ⇒ 跳过。
        #    正解：**先按 `from` 切块**，每块独立解析。
        ext_fields = set()          # 每个文件重置
        for chunk in re.split(r'(?m)^(?=from\s)', src):
            im = re.match(r'from\s+(\w+)\s+import\s*(\([^)]*\)|[^\n]*)',
                          chunk)
            if not im:
                continue
            mod = im.group(1)
            syms = im.group(2)
            mpath = os.path.join(BACKEND, mod + '.py')
            if not os.path.exists(mpath):
                continue
            msrc = open(mpath, encoding='utf-8').read()
            for sym in re.findall(r'\w+', syms):
                for fm2 in re.finditer(
                        r'^def\s+' + re.escape(sym) + r'\s*\([^)]*\)[^:\n]*:\n'
                        r'((?:[ \t]+.*\n|\n)*)', msrc, re.M):
                    helpers.setdefault(sym, fm2.group(1))
                    # 外部模块 helper 的字段并入 file_wide
                    # （get_error_detail 用 row[N] 位置索引造 12 个字段）
                    ext_fields |= helper_fields(fm2.group(1))
        # 该文件里「模块级常量 + 外部 helper」的字段集合：
        # 对本文件所有路由并入。放宽的代价必须核过：
        # _TIMELINE_SOURCES 的 55 个字段里不含 `duration`（我们注入的错误字段）、
        # 也不含 `type` ⇒ 不会掩盖 sleep/diaper 路由的真 bug。
        # ⚠ ext_fields 必须在**使用之前**初始化。
        #   踩过：初始化写在 import 循环之后 ⇒ Python 认为它是局部名但未赋值
        #   ⇒ `ext_fields |= ...` 走 global 语义静默丢结果
        #   ⇒ /admin/logs/detail 的 10 个字段被误报，且脚本不报错（最难查的一类）。
        file_wide = set()
        for k, v in helpers.items():
            # 模块级常量名判断：**必须先剥掉前导下划线**。
            # 踩过：'_TIMELINE_SOURCES'.isupper() 是 False（Python 认为
            # 整个串没有任何 cased 字符就不算 upper），⇒ file_wide 恒空
            # ⇒ /timeline 的 duration_minutes 一直被误报。
            kn = k.lstrip('_')
            if kn and kn.isupper() and any(c.isalpha() for c in kn):
                file_wide |= helper_fields(v)
        file_wide |= ext_fields
        m = re.search(r'Blueprint\([^,]+,[^,]+(?:,\s*url_prefix="([^"]*)")?',
                      src)
        prefix = (m.group(1) or '') if m else ''
        for blk in re.split(r'@bp\.route\(', src)[1:]:
            um = re.match(r'["\']([^"\']*)["\']', blk.split('\n', 1)[0])
            if not um:
                continue
            full = prefix + um.group(1)
            tables = set(re.findall(r'\bFROM\s+(\w+)', blk))
            tables |= set(re.findall(r'\bJOIN\s+(\w+)', blk))
            tables = {t for t in tables
                      if t in schema and not t.startswith('sqlite')}
            segs = [s for s in full.split('/') if s]
            norm = tuple('<v>' if is_var_seg(s) else s for s in segs)
            fields = route_fields(blk, tables, schema, helpers)
            fields |= file_wide
            for t in tables:
                if t in KV_TABLES:
                    if t not in kv_cache:
                        kv_cache[t] = kv_keys_for(t, con, kv_url_fragments)
                    fields |= kv_cache[t]
            out.setdefault(norm, set()).update(fields)
    return out


def kv_keys_for(tbl, con, kv_urls):
    """
    key-value 型表的「合法字段名」= 表里现存的 key ∪ 前端 PUT 该路由时写入的 key。

    settings 这类表字段名不是列、也不在库里（表可为空），
    契约由前端单方面定义 ⇒ 只能从前端写入侧反推。

    两处收紧，都是防「判据变宽就恒绿」：
      1. 只认**指向 kv 路由的 PUT 调用**，不是前端所有对象字面量。
         早先版用全局 `{ k: v }` 扫描，吸进来 426 个键 —— 里面混着
         feeding_records.amount、health_records.abdomen_result 等表列，
         等于把整个 schema 都变成 settings 的合法字段 ⇒ 真错字段会被吞掉。
      2. 只取该载荷对象里**直接**的一级键，不递归嵌套子对象。
    """
    keys = set()
    try:
        for (k,) in con.execute('SELECT key FROM "%s"' % tbl):
            if k:
                keys.add(k)
    except Exception:
        pass

    for f in sorted(glob.glob(os.path.join(JS_DIR, '*.js'))):
        src = strip_comments(open(f, encoding='utf-8').read())
        for m in re.finditer(r"""\bapi\(\s*([^)]*?)\s*,\s*(\{.*?\})\s*\)""",
                             src, re.S):
            arg, payload = m.group(1), m.group(2)
            # 路由必须**精确等于**某个 kv 路由。
            # 踩过：用 `frag in arg` 子串判定，把
            #   api('/api/babies/1/sleep', { body: ...start_time/end_time... })
            # 也认成 settings 的写入点 ⇒ 收进 sleep 的字段，
            # 同时把 settings 真正的键（weight_unit…）漏掉。
            um = re.search(r'["\'`](/[^"\'`]*)["\'`]', arg)
            if not um:
                continue
            if url_to_route(um.group(1)) not in kv_urls:
                continue
            # 载荷两种写法都要认：
            #   a) body: JSON.stringify(data)  → 变量 data 在调用前定义
            #   b) body: JSON.stringify({ k: v })  → 字面量内联
            bodies = re.findall(r'JSON\.stringify\(\s*([A-Za-z_$][\w$]*|'
                                r'\{[\s\S]*?\})\s*\)', payload)
            for b in bodies:
                if b.startswith('{'):
                    keys |= top_level_keys(b)
                    continue
                # 变量：往回找**同一次调用之前**最近的 `const/let b = {`。
                # 两个坑：
                #   1) for 循环里复用 vm，首轮不满足就 break ⇒ vm 指向错的匹配
                #      （踩过，收进来的是 sleep 的 start_time/end_time）。
                #      必须每次迭代重置，且只在命中时 break。
                #   2) b 常是 data/payload 这类通用名，全文件回溯会命中
                #      上一个函数里的同名定义 ⇒ 只看调用前 600 字符窗口。
                vm = None
                for cand in re.finditer(
                        r'\b(?:const|let|var)\s+' + re.escape(b) +
                        r'\s*=\s*\{', src):
                    if cand.start() < m.start():
                        vm = cand                     # 继续往前找更近的
                if vm is None or m.start() - vm.start() > 600:
                    continue
                keys |= top_level_keys(src[vm.end() - 1: m.start()])
    return keys


def arrow_body(js, pos):
    """
    从 pos（箭头函数 `=>` 之后）起，取该回调体的文本。
      - `{ ... }` 形式：按花括号配平（跳过字符串里的括号）
      - 表达式形式（`r => r.x + 1`）：到逗号 / 分号 / 换行处停，上限 800 字符
    返回 (body_text, end_pos)。
    """
    n = len(js)
    i = pos
    while i < n and js[i] in ' \t\r\n':
        i += 1
    if i >= n:
        return '', pos
    if js[i] == '{':
        depth, j, instr = 0, i, None
        while j < n:
            ch = js[j]
            if instr:
                if ch == '\\':
                    j += 2
                    continue
                if ch == instr:
                    instr = None
            elif ch in '"\'`':
                instr = ch
            elif ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    return js[i:j + 1], j + 1
            j += 1
        return js[i:], n
    # 表达式体
    m = re.search(r'[,;\n]', js[i:i + 800])
    end = i + (m.start() if m else 800)
    return js[i:end], end


def enclosing_function_start(js, pos):
    """
    找包含 pos 的最近一个「顶层函数/箭头函数」定义的起始位置。
    用于把api 归属限制在同一函数体内（防止跨函数误归属）。
    找不到时返回 0（不限制）—— 但宁可漏也不愿错归属，故调用方还要
    结合 fn_end 一起判断；这里简化成：取pos 之前最后一个顶格/两空格缩进的
    function/箭头赋值。
    """
    best = 0
    for m in re.finditer(r'^(function\s+\w+|(?:const|let|var)\s+\w+\s*=\s*'
                         r'(?:async\s*)?(?:function|\())', js, re.M):
        if m.start() <= pos:
            best = m.start()
        else:
            break
    return best


def top_level_keys(obj_src):
    """只取对象字面量**第一层**的键，不递归子对象。"""
    out, depth = set(), 0
    for i, ch in enumerate(obj_src):
        if ch == '{':
            depth += 1
            continue
        if ch == '}':
            depth -= 1
            if depth == 0:
                break
        if depth == 1 and ch == ':':
            m = re.search(r'([A-Za-z_$][\w$]*)\s*$', obj_src[:i])
            if m:
                out.add(m.group(1))
    return out


def route_table_map(schema):
    """
    扫 blueprints/*.py：每个 @bp.route 块里的 SELECT/JOIN 表名。
    返回 {URL模板: {表名}}，URL 模板已把 <int:baby_id> 之类归一为{}。
    """
    out = {}
    for f in sorted(glob.glob(os.path.join(BACKEND, 'blueprints', '*.py'))):
        src = open(f, encoding='utf-8').read()
        m = re.search(r'Blueprint\([^,]+,[^,]+(?:,\s*url_prefix="([^"]*)")?',
                      src)
        prefix = (m.group(1) or '') if m else ''
        for blk in re.split(r'@bp\.route\(', src)[1:]:
            url = blk.split('\n', 1)[0]
            um = re.match(r'["\']([^"\']*)["\']', url)
            if not um:
                continue
            full = prefix + um.group(1)
            tables = set(re.findall(r'\bFROM\s+(\w+)', blk))
            tables |= set(re.findall(r'\bJOIN\s+(\w+)', blk))
            tables = {t for t in tables
                      if t in schema and not t.startswith('sqlite')}
            segs = [s for s in full.split('/') if s]
            norm = tuple('<v>' if is_var_seg(s) else s for s in segs)
            out.setdefault(norm, set()).update(tables)
    return out


def is_var_seg(s):
    """
    判断一个路径段是否是「变量段」。
    必须覆盖前端真实写法，漏一种 ⇒ 那条路由整个匹配不上 ⇒ 静默跳过
    （踩过：`/api/babies/${App.currentBaby}/sleep` 里 `${...}` 没被认成变量，
    norm 变成 ('api','babies','${App.currentBaby}','sleep')，查不到 ⇒ 判据恒绿）。
    """
    if not s:
        return False
    if re.fullmatch(r'\d+|[0-9a-f]{8,}', s):          # 已求值的 id
        return True
    if s.startswith('${') and s.endswith('}'):        # 模板插值
        return True
    if s.startswith('<') and s.endswith('>'):          # Flask 风格
        return True
    return False


def url_to_route(url):
    """
    前端 url -> 归一化路由元组，变量段统一写成 '<v>'。
    例：/api/babies/${App.currentBaby}/sleep -> ('api','babies','<v>','sleep')
    """
    segs = [s for s in url.split('?')[0].split('/') if s]
    return tuple('<v>' if is_var_seg(s) else s for s in segs)


# ------------------------------------------------------------ 前端解析
def api_calls(src):
    """
    找出所有 api('/api/...') 调用点。
    返回 [(call_start, url_literal_or_None)]
    """
    out = []
    for m in re.finditer(r"""\bapi\(\s*(['"`])(/[^'"`]*)\1""", src):
        out.append((m.start(), m.group(2)))
    # 变量拼接的 api(someVar + '/api/...')：只取能静态看出的片段
    for m in re.finditer(r"""\bapi\(\s*([A-Za-z_$][\w$]*)\s*\+\s*(['"`])([^'"`]*)\2""",
                         src):
        out.append((m.start(), m.group(3)))
    return out


def strip_comments(js):
    """
    只删注释，**保留字符串字面量原文**。位置（pos）与原文严格对齐
    （用等长空白替换，不做删除），否则行号定位会漂。

    踩过两个坑：
    1. 第一版把字符串内容一并替换成""，结果 api('/api/...') 的 URL 全被抹掉
       ⇒ api 调用点识别出 0 个 ⇒ 判据恒绿却报「全部通过」。
    2. 第二版把注释里出现的引号（如 `// 原先写 p.percentile ⇒ 显示 '--'`）
       当成字符串开始标记，后面几行注释就再没被剥掉 ⇒ 注释里的字段名
       被当成真代码报错。字符串只在「代码上下文」才成立：
       即上一个有效字符不是标识符/数字/`)`/`]`（那是字符串的后续拼接）。
    """
    out = list(js)
    i, n = 0, len(js)
    prev_sig = ''          # 上一个有效字符（跳过空白后）
    while i < n:
        c = js[i]
        if c in ' \t\r\n':
            out[i] = c
            i += 1
            continue
        # 判定这个引号是「字符串开始」还是「字符串结束/续接」
        if c == '"' or c == "'" or c == '`':
            if prev_sig in ('', '(', '[', '{', ',', ':', '=', ';', '!', '&',
                            '|', '?', '+', '-', '*', '%', '<', '>', '~',
                            '^') or not prev_sig:
                # 进入字符串
                q = c
                i += 1
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
            # 否则是收尾引号（该字符本身保留，prev_sig 不变）
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
            while j < n and not (js[j] == '*' and j + 1 < n and js[j + 1] == '/'):
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


def check(js_path, schema, allowed_by_route, rmap):
    raw = open(js_path, encoding='utf-8').read()
    js = strip_comments(raw)
    line_of = lambda pos: raw.count('\n', 0, pos) + 1

    calls = api_calls(js)
    # 变量 -> 归一化路由（变量级，不是函数级 —— 见文件头踩坑记录）
    assign_re = re.compile(
        r'\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*'
        r'(?:await\s+)?(?:res|resp|result|r)\s*\.\s*(?:data|items|list|records)\b'
        r'|(?:\b([A-Za-z_$][\w$]*)\s*=\s*)'
        r'(?:await\s+)?(?:res|resp|result|r)\s*\.\s*(?:data|items|list|records)\b'
    )   # 注意：只有 group(1)/group(2) 两个捕获组，
        # 中间的 (?:data|items|...) 必须是非捕获组，否则编号会错位。
    var_route = []          # [(变量名, 归一化路由, 赋值点pos)]，**按出现逐条**
    for m in assign_re.finditer(js):
        var = m.group(1) or m.group(2)
        if not var:
            continue
        # 找「**同一个函数体内**、在赋值点之前」最近的那次 api 调用。
        #
        # 踩过两个坑：
        # 1. 只按字符距离（1200）回溯 ⇒ 跨过函数边界。
        #    renderPumpingItems(r) 的参数 r 被算到上一个函数
        #    editFeeding 里的 api('/api/babies/1/feeding/3') 上，
        #    于是 pumping_records 的 8 个字段全被报成「feeding 表里没有」。
        # 2. 用 `var_route.setdefault(var, ...)` ⇒ 同名变量只留**第一次**。
        #    `records` 这个名字在项目里被 20+ 处复用（growth/sleep/feeding/
        #    diaper…），只有第一次进了字典，报表页那三处历史错字段
        #    （r.duration / r.type / r.time）因此全被判成「growth 表」，
        #    而 duration 恰好不在 growth_records 里 ⇒ 又被当成合法字段放过。
        #    ⇒ 变量名不能当唯一键，必须逐处登记。
        fn_start = enclosing_function_start(js, m.start())
        prev = [c for c in calls if fn_start <= c[0] < m.start()]
        if not prev:
            continue
        norm = url_to_route(prev[-1][1])
        if norm is None or norm not in allowed_by_route:
            continue
        var_route.append((var, norm, m.start()))

    if not var_route:
        return []

    hits = []
    for var, norm, pos in var_route:
        cols = allowed_by_route[norm]
        if not cols:
            continue
        # 字段搜索同样限制在**同一函数体内**（≤4000 字符）。
        # 变量作用域溢出到别的函数 ⇒ 拿 A 表的字段名去比B 表的字段集。
        fn_start = enclosing_function_start(js, pos)
        seg = js[pos: pos + 4000]
        nxt = re.search(r'^(?:function\s+\w+|(?:const|let|var)\s+\w+\s*=\s*'
                        r'(?:async\s*)?(?:function|\())', seg, re.M)
        if nxt:
            seg = seg[:nxt.start()]
        # (a) 直接访问数组变量：records.xxx
        for fm in re.finditer(r'\b' + re.escape(var) + r'\s*\.\s*(\w+)', seg):
            fld = fm.group(1)
            if fld in JS_BUILTIN or fld in cols:
                continue
            hits.append((os.path.basename(js_path),
                         line_of(pos + fm.start()),
                         var, fld, '/'+'/'.join(norm)))
        # (b) **回调参数**上的字段：records.map(r => r.xxx)
        #     只做 (a) 的话，真实错字段全是绿的假象 —— 报表页那三处
        #     （r.duration / r.type / r.time）全都写在 map/filter 的回调体里，
        #     而 `records.xxx` 一个都没有。
        #     ⚠ 箭头函数有 4 种写法，全都要认（漏一种就漏一批字段）：
        #         r => …              （无括号单参）
        #         (r) => …            （括号单参）
        #         (r, i) => …         （括号双参，索引参数名也可能被用到）
        #         async r => … / async (r) => …
        #       踩过：正则只写了「括号单参」和「括号双参」两个分支，
        #       `map(r => Number(r.x))` 这种最常见的写法一个都不匹配
        #       ⇒ 负向对照注入 4 处只抓到 1 处（对照位置对了，判据还是漏）。
        cb_re = (r'\b' + re.escape(var) +
                 r'\s*\.\s*(?:map|filter|forEach|some|every|find|findIndex'
                 r'|reduce|flatMap|sort)\s*\(\s*(?:async\s+)?'
                 r'(?:'
                 r'\(\s*([A-Za-z_$][\w$]*)\s*(?:,\s*([A-Za-z_$][\w$]*)\s*)?\)'
                 r'|([A-Za-z_$][\w$]*)\s*(?:,\s*([A-Za-z_$][\w$]*)\s*)?'
                 r')\s*=>')
        for fm in re.finditer(cb_re, seg):
            names = [n for n in fm.groups() if n]
            if not names:
                continue
            # 回调体范围按**箭头函数体配平**截断，不能用固定 1200 字符窗口。
            # 踩过：`records = records.filter(r => r.food_category === category)`
            # 之后 1200 字符内还有另一个 records.map(r => …) 的模板，
            # 固定窗口会跨出当前回调去扫它⇒ 报出 `r.re`（源码里根本没有，
            # 实际是 r.reaction），人工核实时被行号带偏。
            tail, _ = arrow_body(seg, fm.end())
            if not tail:
                continue
            for pname in names:
                if pname in JS_BUILTIN:
                    continue
                for im in re.finditer(r'\b' + re.escape(pname) +
                                      r'\s*\.\s*(\w+)', tail):
                    fld = im.group(1)
                    if fld in JS_BUILTIN or fld in cols:
                        continue
                    abs_pos = pos + fm.end() + im.start()
                    hits.append((os.path.basename(js_path),
                                 line_of(abs_pos),
                                 pname, fld, '/'+'/'.join(norm)))
    return hits


def main():
    if not os.path.isdir(JS_DIR):
        print('D10 FAIL: 前端目录不存在 %s' % JS_DIR)
        return 1
    schema = load_schema()
    if not schema:
        print('D10 FAIL: 读不到数据库 %s（字段名判据无真值源会恒绿）' % DB)
        return 1
    rmap = route_table_map(schema)
    if not rmap:
        print('D10 FAIL: 从后端抽不到 路由->表 映射（判据失效）')
        return 1
    con = sqlite3.connect(DB)
    try:
        SCHEMA_CACHE.clear()
        SCHEMA_CACHE.update(schema)
        allowed = route_shapes(schema, con)
    finally:
        con.close()

    allhits = []
    for p in sorted(glob.glob(os.path.join(JS_DIR, '*.js'))):
        allhits += check(p, schema, allowed, rmap)

    if allhits:
        print('D10 命中 %d 处字段名存疑：' % len(allhits))
        for h in allhits[:60]:
            print('  %s:%s  %s.%s←%s' % h)
        if len(allhits) > 60:
            print('  ... 其余 %d 处省略' % (len(allhits) - 60))
        return 1
    print('D10 OK: 前端读的字段全部存在于真实表列/聚合返回体'
          '（判到 %d 条路由）' % len(allowed))
    return 0


if __name__ == '__main__':
    sys.exit(main())
