#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""打包前静态检查 manifest：防「值被 INI 注释符截断」。

背景（2026-09-19 实测事故）：
    manifest 是 INI 格式，fnpack 打包时会重排成 `key = value`。
    它把**行内 `;` 当注释符** —— 值里出现第一个 `;` 起，后面全部被丢弃。
    当时 desc 里写了 `style="width:100%;border-radius:14px;margin:10px 0;"`
    结果包内 desc 被砍成 `<img src="..." style="width:100%`（半个没闭合的标签），
    应用中心详情页 HTML 解析失败 → 把标签当纯文本显示，简介图就"加载不出来"了。

用法：
    python .workbuddy/scripts/check_manifest_safe.py [manifest 路径]
退出码 0 = 通过，1 = 有问题。
"""
import os
import sys

MF = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    os.path.dirname(os.path.abspath(__file__)), '..', '..', 'manifest')

# 值里出现即会被当成注释起点的字符（`;` 已实测；`#` 同属 INI 注释符，先按警告处理）
FATAL_CHARS = {';': 'INI 行内注释符，实测会截断值'}
WARN_CHARS = {'#': 'INI 注释符，fnpack 若也按行内注释处理会截断值'}

problems = []
warnings = []
entries = []

with open(MF, encoding='utf-8') as fh:
    for lineno, raw in enumerate(fh, 1):
        line = raw.rstrip('\n').rstrip('\r')
        if not line.strip() or line.lstrip().startswith((';', '#')):
            continue
        if '=' not in line:
            problems.append('第 %d 行不是 key=value：%s' % (lineno, line[:60]))
            continue
        key, _, value = line.partition('=')
        key = key.strip()
        entries.append((lineno, key, value))

for lineno, key, value in entries:
    for ch, why in FATAL_CHARS.items():
        if ch in value:
            pos = value.index(ch)
            problems.append(
                '第 %d 行字段 %s 的值里有 %r（%s）；'
                '值会从该处被截断，实际生效内容以 …%s 结尾'
                % (lineno, key, ch, why, value[max(0, pos - 30):pos]))
    for ch, why in WARN_CHARS.items():
        if ch in value:
            warnings.append('第 %d 行字段 %s 的值里有 %r（%s）' % (lineno, key, ch, why))

# desc 专项：标签必须闭合，否则渲染端会退化成纯文本
desc = dict((k, v) for _, k, v in entries).get('desc', '')
if desc:
    if desc.count('<') != desc.count('>'):
        problems.append(
            'desc 里 "<" 有 %d 个、">" 有 %d 个 —— 标签不闭合/被截断，'
            '应用中心会把标签当纯文本显示' % (desc.count('<'), desc.count('>')))
    if desc.count('"') % 2 != 0:
        problems.append('desc 里双引号个数是奇数（%d），属性没闭合' % desc.count('"'))
    if '<img' in desc and not desc.rstrip().endswith('>'):
        problems.append('desc 结尾不是 ">"，最后一个标签被截断')

print('检查文件：%s' % os.path.abspath(MF))
print('字段数：%d' % len(entries))
for lineno, key, value in entries:
    flag = '!!' if any(c in value for c in FATAL_CHARS) else '  '
    print('  %s %-20s = %s' % (flag, key, value[:90]))

print()
for w in warnings:
    print('[WARN] ' + w)
for p in problems:
    print('[FAIL] ' + p)

if problems:
    print('\nmanifest 检查不通过（%d 项）' % len(problems))
    sys.exit(1)
print('\nmanifest 检查通过' + ('（有 %d 项警告）' % len(warnings) if warnings else ''))
