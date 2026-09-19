#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""开包验货：核对 fpk 清单、执行位、以及新前端改动是否进包。"""
import hashlib
import io
import tarfile

FPK = 'babycare-fpk.fpk'
ok = True


def chk(label, cond):
    global ok
    print(('  [OK]   ' if cond else '  [FAIL] ') + label)
    if not cond:
        ok = False


print('md5:', hashlib.md5(open(FPK, 'rb').read()).hexdigest())

outer = tarfile.open(FPK, 'r:*')
print('外层条目:', len(outer.getnames()))

bad = []
for m in outer.getmembers():
    if m.name.startswith('cmd/') and (m.mode & 0o111) == 0:
        bad.append((m.name, oct(m.mode)))
print('cmd 缺执行位:', bad if bad else '无')
chk('外层 cmd 脚本都有执行位', not bad)

mf = outer.extractfile('manifest').read().decode('utf-8')
# fnpack 打包时会把 manifest 重排成 `key = value`（等号两侧补空格），
# 因此用「去空白后包含」来判断，避免误报。
mf_flat = ''.join(mf.split())
for k in ['appname=babycare-fpk', 'version=0.0.1', 'platform=all',
          'os_min_version=1.1.3100', 'install_dep_apps=python312',
          'desktop_applaunchname=babycare-fpk.main']:
    chk('manifest 含 ' + k, ''.join(k.split()) in mf_flat)

inn = outer.extractfile('app.tgz').read()
it = tarfile.open(fileobj=io.BytesIO(inn), mode='r:*')
print('内层条目:', len(it.getnames()))

noexec = []
html = None
js = None
for m in it.getmembers():
    if not m.isfile():
        continue
    f = it.extractfile(m)
    data = f.read()
    if data[:2] == b'#!' and (m.mode & 0o111) == 0:
        noexec.append(m.name)
    if m.name.endswith('frontend/index.html'):
        html = data.decode('utf-8')
    if m.name.endswith('frontend/js/app.js'):
        js = data.decode('utf-8')

print('带 shebang 但缺执行位:', noexec if noexec else '无')
chk('内层带 shebang 脚本都有执行位', not noexec)

print()
print('=== 包内前端内容核对 ===')
chk('index.html 含「系统诊断」', bool(html) and '系统诊断' in html)
chk('index.html 含 storageOverview', bool(html) and 'storageOverview' in html)
chk('index.html 含 backupHistory', bool(html) and 'backupHistory' in html)
clin = ''
if html and 'id="page-clinic"' in html:
    clin = html[html.index('id="page-clinic"'):]
    clin = clin[:clin.index('</section>')]
chk('看诊摘要页已瘦身（只剩 clinicSummary）',
    'clinicSummary' in clin and 'storageOverview' not in clin and 'backupHistory' not in clin)
chk("app.js 含 TAB_PAGE_HUB = { 'sleep-analysis': 'growth-hub' }",
    bool(js) and "TAB_PAGE_HUB = { 'sleep-analysis': 'growth-hub' }" in js)
chk('app.js 已移除重复初始化 initDataManagement-2',
    bool(js) and "'initDataManagement-2'" not in js)
chk('app.js 进入设置页会刷新存储数据',
    bool(js) and "case 'settings':" in js and 'loadStorageOverview(false)' in js)

print()
print('验货通过' if ok else '验货存在失败项')
