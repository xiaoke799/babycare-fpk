#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""开包验货：核对 fpk 清单、打包卫生、以及新前端改动是否进包。

退出码：有失败项时返回 1（门禁 _run_gates.py 按 returncode 判红绿）。
"""
import hashlib
import io
import sys
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
for k in ['appname=babycare-fpk', 'platform=all',
          'os_min_version=1.1.3100', 'install_dep_apps=python312',
          'desktop_applaunchname=babycare-fpk.main']:
    chk('manifest 含 ' + k, ''.join(k.split()) in mf_flat)

# 版本号**别写死**：这里原来是 `version=0.0.1`，一发版（0.0.2）就当场转红 ——
# 跟「别把今天是几号写进断言」是同一个坑。改成两条不变的判据：
#   ① 包内 version 形如 x.y.z；
#   ② 包内 version 必须等于仓库 manifest 的 version（包和仓库对得上）。
# 这样发版只要改 manifest 一处，门禁不用跟着改，而「包是旧包」照样拦得住。
import os as _os
import re as _re
_pkg_ver = ''
for _ln in mf.splitlines():
    if _ln.strip().startswith('version'):
        _pkg_ver = _ln.partition('=')[2].strip()
        break
_ws_manifest = _os.path.join(
    _os.path.dirname(_os.path.abspath(__file__)), '..', '..', 'manifest')
_ws_ver = ''
try:
    for _ln in open(_ws_manifest, encoding='utf-8'):
        if _ln.strip().startswith('version'):
            _ws_ver = _ln.partition('=')[2].strip()
            break
except OSError:
    pass
chk('包内 version 已声明且形如 x.y.z', bool(_re.fullmatch(r'\d+\.\d+\.\d+', _pkg_ver)))
chk('包内 version(%s) 与仓库 manifest(%s) 一致' % (_pkg_ver, _ws_ver),
    bool(_pkg_ver) and _pkg_ver == _ws_ver)

# ---- 回归门禁：manifest 值不能被 INI 注释符 `;` 截断（2026-09-19 事故）----
# fnpack 打包时把行内 `;` 当注释，desc 里写 `style="width:100%;border-radius..."`
# 会被砍成半个 `<img src="..." style="width:100%`，应用中心解析失败 → 简介图不显示。
mf_fields = {}
for _ln in mf.splitlines():
    if not _ln.strip() or _ln.lstrip().startswith((';', '#')):
        continue
    if '=' in _ln:
        _k, _, _v = _ln.partition('=')
        mf_fields.setdefault(_k.strip(), _v)
pkg_desc = mf_fields.get('desc', '')
# 2026-09-19 复测结论：desc 里的 <img> 是能被渲染的，但**详情页简介框高度有限、会把长图裁掉**
# （电脑端只露顶条、手机端几乎不可见），所以简介改为纯文字，图片移到 README 展示。
chk('包内 desc 已改为纯文字（不含 <img>，避免被详情页简介框裁切）',
    pkg_desc.count('<img') == 0)
chk('包内 desc 标签闭合（"<" 与 ">" 数量一致）',
    pkg_desc.count('<') == pkg_desc.count('>') and pkg_desc.count('<') > 0)
chk('包内 desc 含项目主页链接（gitee / github 任一）',
    'gitee.com/xiaoke799/babycare-fpk' in pkg_desc
    or 'github.com/xiaoke799/babycare-fpk' in pkg_desc)
chk('包内 manifest 所有字段值都不含 ";"（否则会被当行内注释截断）',
    not any(';' in _v for _v in mf_fields.values()))
chk('包内 manifest 无游离注释行（截断残渣）',
    not any(_ln.lstrip().startswith(';') for _ln in mf.splitlines()))

inn = outer.extractfile('app.tgz').read()
it = tarfile.open(fileobj=io.BytesIO(inn), mode='r:*')
print('内层条目:', len(it.getnames()))

html = None
js = None
storage_py = None
constants_py = None
health_py = None
development_py = None
vaccines_py = None
pycache_files = []
data_files = []
verify_files = []
for m in it.getmembers():
    if not m.isfile():
        continue
    f = it.extractfile(m)
    data = f.read()
    # 打包卫生：下面三类不该进包（fnpack 直接打 app/ 全量、不读 .gitignore）
    if '__pycache__' in m.name or m.name.endswith('.pyc'):
        pycache_files.append(m.name)
    if m.name.lstrip('./').startswith('data/'):
        data_files.append(m.name)
    _base = m.name.rsplit('/', 1)[-1]
    if _base.startswith('verify_') and _base.endswith('.py'):
        verify_files.append(m.name)
    if m.name.endswith('frontend/index.html'):
        html = data.decode('utf-8')
    if m.name.endswith('frontend/js/app.js'):
        js = data.decode('utf-8')
    if m.name.endswith('backend/blueprints/storage.py'):
        storage_py = data.decode('utf-8')
    if m.name.endswith('backend/constants.py'):
        constants_py = data.decode('utf-8')
    if m.name.endswith('backend/blueprints/health.py'):
        health_py = data.decode('utf-8')
    if m.name.endswith('backend/blueprints/development.py'):
        development_py = data.decode('utf-8')
    if m.name.endswith('backend/blueprints/vaccines.py'):
        vaccines_py = data.decode('utf-8')

print()
print('=== 打包卫生（这些不该进包）===')
# 背景：fnpack 直接打 app/ 全量、不读 .gitignore，__pycache__ / 开发数据 / 测试脚本
# 会被一起打进正式件（曾达 55% 是垃圾，还夹带真实 db 与 secret_key）。
# 内层脚本一律由 python 解释器调用（cmd/main 用 $PYTHON_CMD start_server.py），
# 故不再要求内层 shebang 脚本带执行位（旧断言是过时误判，2026-10-03 移除）。
chk('包内无 __pycache__ / *.pyc 编译缓存（发现 %d 个）' % len(pycache_files),
    not pycache_files)
chk('包内无 data/ 开发数据与密钥（发现 %d 个）' % len(data_files),
    not data_files)
chk('包内无 verify_*.py 测试脚本（发现 %d 个）' % len(verify_files),
    not verify_files)

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
chk("app.js 的 TAB_PAGE_HUB 覆盖睡眠分析与成长曲线两个虚拟页",
    bool(js) and "TAB_PAGE_HUB = { 'sleep-analysis': 'growth-hub', 'growth-chart': 'growth-hub' }" in js)
chk('app.js 已移除重复初始化 initDataManagement-2',
    bool(js) and "'initDataManagement-2'" not in js)
chk('app.js 进入设置页会刷新存储数据',
    bool(js) and "case 'settings':" in js and 'loadStorageOverview(false)' in js)

print()
print('=== 本轮（导航分层 + 系统诊断集成）===')
# 系统诊断板块
for eid in ['appInfoGrid', 'refreshAppInfoBtn', 'logFileList', 'logContent',
            'errorStats', 'errorList', 'loadErrorsBtn', 'clearOldErrorsBtn',
            'loadMoreErrorsBtn', 'errorLevelFilter']:
    chk('index.html 含 #%s' % eid, bool(html) and ('id="%s"' % eid) in html)
chk('异常级别筛选用大写 value（与后端口径一致）',
    bool(html) and '<option value="ERROR">错误</option>' in html)
# 导航分层
def _hub_block(src, name):
    """截出 NAV_HUBS 里某个 hub 的段落（到下一个 hub: 为止）。"""
    i = src.index("hub: '%s'" % name)
    nxt = src.find("hub: '", i + 5)
    return src[i: nxt if nxt != -1 else len(src)]


js_rec = js_gro = ''
if js:
    js_rec = _hub_block(js, 'record')
    js_gro = _hub_block(js, 'growth')
# 2026-09-19 定稿：记录栏=只管往里填数据，成长栏=只管看结果；
# 一个功能横跨两边就拆两半（录的归记录栏、看的归成长栏），各栏各一个 page id。
chk('成长栏：含「成长回顾」(milestone-review)',
    "page: 'milestone-review', label: '成长回顾'" in js_gro)
chk('成长栏：含「成长曲线」(growth-chart)',
    "page: 'growth-chart', label: '成长曲线'" in js_gro)
chk('成长栏：含「时间线」(timeline)',
    "page: 'timeline', label: '时间线'" in js_gro)
chk('记录栏：含「成长成就」录入入口 (milestones)',
    "page: 'milestones', label: '成长成就'" in js_rec)
chk('记录栏：含「身高体重」录入入口 (growth)',
    "page: 'growth', label: '身高体重'" in js_rec)
chk('记录栏：已移出 timeline', bool(js_rec) and "page: 'timeline'" not in js_rec)


def _page_ids(block):
    """NAV_HUBS 某栏里所有 page id。"""
    return set(p.split("'")[0] for p in block.split("page: '")[1:])


_dup = sorted(_page_ids(js_rec) & _page_ids(js_gro)) if js else []
chk('两栏不共用任何 page id（共用即形态分不开 = 假拆分）%s'
    % ('' if not _dup else '，重复：' + '、'.join(_dup)), not _dup)
chk('成长曲线走别名直接落 who-chart（不是默认的录入 Tab）',
    bool(js) and "tab: 'who-chart'" in js and "case 'growth-chart':" in js)
chk('已删的 firsts 页面没有复活', bool(js) and "page: 'firsts'" not in js)
chk('系统栏：安抚音效已归入', bool(js) and "page: 'sounds', label: '安抚音效'" in js)
chk('「第一次」页面已从包内移除', bool(html) and 'id="page-firsts"' not in html)
chk('「第一次」相关 JS 已清干净',
    bool(js) and 'loadFirstsRecords' not in js and 'initFirstsPage' not in js)
chk('里程碑页已改名「成长成就」', bool(html) and '成长成就' in html)
chk('里程碑表单含「标记为第一次」', bool(html) and 'id="milestoneIsFirst"' in html)
chk('健康档案 Tab 改名「发育里程碑」',
    bool(html) and 'data-tab="milestone">发育里程碑</button>' in html)
chk('诊断 JS 已进包（loadAppInfo / loadErrorList）',
    bool(js) and 'function loadAppInfo(' in js and 'function loadErrorList(' in js)
# 后端
chk('包内 storage.py 含 /api/storage/app-info',
    bool(storage_py) and '/api/storage/app-info' in storage_py)
chk('包内 constants.py 含 APP_VERSION',
    bool(constants_py) and 'APP_VERSION' in constants_py)

# ---- 本轮：删除接口「假成功」守卫（2026-09-19）----
print()
print('=== 本轮（删除接口假成功守卫）===')
chk('包内 health.py 已补 rowcount 守卫',
    bool(health_py) and health_py.count('cur.rowcount == 0') >= 5)
chk('包内 development.py 已补 rowcount 守卫',
    bool(development_py) and development_py.count('cur.rowcount == 0') >= 3)
chk('包内 vaccines.py 已补 rowcount 守卫',
    bool(vaccines_py) and vaccines_py.count('cur.rowcount == 0') >= 2)
chk('包内 health.py 趴卧删除已用上 helper 的返回值',
    bool(health_py) and '_delete_tummytime_row(record_id, baby_id) == 0' in health_py)
chk('app.js 删除失败会透出后端 message',
    bool(js) and "showToast.error((res && res.message) || '删除失败')" in js)

print()
print('验货通过' if ok else '验货存在失败项')
sys.exit(0 if ok else 1)
