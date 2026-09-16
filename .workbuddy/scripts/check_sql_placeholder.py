#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全仓扫 INSERT 语句的「列数 vs 占位符数」是否匹配。

起因：health_records.py 的 health_record_create 里 24 列只写了 23 个 `?`，
导致「添加体检记录」这个核心功能一直报「数据库操作失败」。
这类错误只有在真的 POST 一次时才会暴露（GET 冒烟测不出来），
而同一个文件里还有十几个 INSERT，很可能不止一处。

难点：VALUES 的值元组里含 data.get(...) 这类带括号的调用，
用正则切会碎，所以这里用括号配平来定位。

跑法：
  python .workbuddy/scripts/check_sql_placeholder.py
"""
import os
import re
import sys
import io

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def match_paren(s, start):
    """返回与 s[start]=='(' 配对的 ')' 下标，找不到返回 -1"""
    depth = 0
    for i in range(start, len(s)):
        if s[i] == '(':
            depth += 1
        elif s[i] == ')':
            depth -= 1
            if depth == 0:
                return i
    return -1


def strip_sql_comments(sql):
    """去掉 -- 行注释，避免注释里的逗号/问号干扰计数"""
    out = []
    for line in sql.split('\n'):
        i = line.find('--')
        out.append(line if i < 0 else line[:i])
    return '\n'.join(out)


def scan_file(path):
    src = io.open(path, encoding='utf-8').read()
    issues = []
    # 只找 "INSERT INTO xxx (...)" 这种显式列清单的
    for m in re.finditer(r'INSERT\s+(?:OR\s+\w+\s+)?INTO\s+\w+\s*\(', src, re.I):
        col_open = m.end() - 1
        col_close = match_paren(src, col_open)
        if col_close < 0:
            continue
        cols_sql = strip_sql_comments(src[col_open + 1:col_close])
        cols = [c.strip() for c in cols_sql.split(',') if c.strip()]
        if not cols:
            continue
        # 列清单后面必须紧跟 VALUES
        rest = src[col_close + 1:col_close + 200].lstrip()
        if not rest.upper().startswith('VALUES'):
            continue
        val_open = col_close + 1 + (len(src[col_close + 1:col_close + 200])
                                    - len(src[col_close + 1:col_close + 200].lstrip()))
        val_open = src.index('VALUES', col_close) + len('VALUES')
        val_open = src.index('(', val_open)
        val_close = match_paren(src, val_open)
        if val_close < 0:
            continue
        vals_sql = strip_sql_comments(src[val_open + 1:val_close])
        n_ph = vals_sql.count('?')
        if n_ph == len(cols):
            continue
        # 值里混了字面量（如 VALUES (1, ?, 'routine', 68.5)）是合法写法，
        # 这种情况没法靠计数判断，跳过以免刷屏误报。
        pieces = [p.strip() for p in vals_sql.split(',') if p.strip()]
        if not all(p == '?' for p in pieces):
            continue
        line_no = src[:col_open].count('\n') + 1
        issues.append({
            'line': line_no,
            'table': m.group(0).split()[-2] if len(m.group(0).split()) >= 2 else '?',
            'cols': len(cols),
            'ph': n_ph,
        })
    return issues


def main():
    total = 0
    bad = 0
    hits = []
    for base, dirs, files in os.walk(os.path.join(ROOT, 'app')):
        dirs[:] = [d for d in dirs if d not in ('__pycache__', 'node_modules', '.git')]
        for fn in files:
            if not fn.endswith('.py'):
                continue
            p = os.path.join(base, fn)
            try:
                iss = scan_file(p)
            except Exception as e:
                print('  [跳过] %s: %s' % (p, e))
                continue
            total += 1
            if iss:
                bad += 1
                for it in iss:
                    hits.append((os.path.relpath(p, ROOT), it))
    print('扫描 %d 个 py 文件' % total)
    if not hits:
        print('>>> 未发现列数与占位符不匹配的 INSERT')
        return 0
    print('>>> 发现 %d 处不匹配：' % len(hits))
    for rel, it in hits:
        print('   %s:%d  %s  列=%d 占位符=%d' % (rel, it['line'], it['table'], it['cols'], it['ph']))
    return 1


if __name__ == '__main__':
    sys.exit(main())
