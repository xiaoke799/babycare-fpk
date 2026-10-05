#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""动态探针：验证「列表接口失败时用户真的看得到提示」。

为什么必须动态验证（项目铁律「全绿≠没问题」）：
    静态判据只能证明代码里"写了 renderListError"，证明不了
    ① 它真的被调用② 渲染出的 DOM 真的有那句文案
    ③ 容器 id 写错/escapeHtml 未定义/CSS 类没定义时修复看着做了实际没生效。
    三者任一都会让修复"看着做了、实际没生效"。

做法：复制前端到临时目录 → 把 api() 桩成统一失败 →
      注入脚本进页面触发 loadPageData → Edge 无头 --dump-dom →
      在真实 DOM 里找失败文案。

⚠ Edge 无头在 Windows 上 --dump-dom 要靠 shell 重定向才拿得到 stdout，
   这里用subprocess 把 stdout 写文件，不用 capture_output（它拿不到）。
⚠ 进页前必须给 App.currentBaby 赋值，否则 load* 第一行就 return，
   探针会误判成"修复没生效"——这是假红，不是真问题。
"""
import os
import re
import sys
import shutil
import json
import tempfile
import subprocess
import http.server
import socketserver
import threading

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
FRONT = os.path.join(ROOT, 'app', 'frontend')
EDGE = r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
OUT = os.path.join(ROOT, 'outputs')

# (page id, 容器 id, 期望文案, 触发方式)
# trigger='page' 走 loadPageData；'health-tab' 走健康档案的 loadHealthTabData
CASES = [
    ('feeding', 'feedingList', '喂奶记录加载失败', 'page'),
    ('sleep-record', 'sleepList', '睡眠记录加载失败', 'page'),
    ('milestones', 'milestoneList', '成长记录加载失败', 'page'),
    ('health', 'temperatureList', '体温记录加载失败', 'health-tab:temperature'),
    ('health', 'growthTable', '生长数据加载失败', 'health-tab:growth'),
]

FAILURES = []


def check(ok, name, detail=''):
    print('  [%s] %s%s' % ('ok' if ok else 'FAIL', name,
                           '' if ok else '<-- ' + str(detail)))
    if not ok:
        FAILURES.append(name)
    return ok


def build_site(workdir, page, container, text, trigger='page'):
    dst = os.path.join(workdir, 'site')
    if os.path.exists(dst):
        shutil.rmtree(dst)
    # 🔴 必须复刻前端在真实网关下的目录形状：index.html 里引用的是
    #    /app/babycare-fpk/js/app.js（网关前缀路径，见 fnOS 统一网关）。
    #    第一版把前端直接铺在服务根下 ⇒ app.js 404 ⇒ 页面加载的是"没 JS 的壳"，
    #    探针读到的「暂无X记录」全是 HTML 静态文案，看着像修复没生效，实则
    #    探针环境从一开始就是坏的。⇒ 桩自检（STUB-OK）就是为这类假红设的。
    site_dir = os.path.join(dst, 'app', 'babycare-fpk')
    os.makedirs(site_dir, exist_ok=True)
    for name in ('index.html', 'js', 'css', 'images', 'legal'):
        src = os.path.join(FRONT, name)
        if not os.path.exists(src):
            continue
        tgt = os.path.join(site_dir, name)
        if os.path.isdir(src):
            shutil.copytree(src, tgt)
        else:
            shutil.copy2(src, tgt)
    site_root = site_dir

    js_path = os.path.join(site_root, 'js', 'app.js')
    js = open(js_path, encoding='utf-8').read()
    html_path = os.path.join(site_root, 'index.html')
    html = open(html_path, encoding='utf-8').read()
    # 🔴 桩的挂法（第一版栽在这）：不要给真 api 改名。
    #    app.js 内部是**裸调用** api(...)，改名会让每处都抛 ReferenceError，
    #    load* 第一行就中断 ⇒ 容器保持 HTML 里的静态默认文案「暂无X记录」，
    #    探针会误报"修复没生效"——那是假红，根因是桩没接管。
    #    正确做法：末尾覆盖 window.api。浏览器全局作用域里
    #    `function api(){}` 本身就是 window.api 这个属性，末尾赋值会覆盖它。
    js += """
// ===== 探针桩：所有 api 调用统一失败 =====
window.api = function (url, opts) {
  return Promise.resolve({ success: false, error: 'probe-forced-failure' });
};
if (typeof App !== 'undefined' && App) { App.currentBaby = 1; }
"""
    open(js_path, 'w', encoding='utf-8').write(js)

    # 健康档案的 Tab 不走 loadPageData，走 loadHealthTabData
    fire = ("loadHealthTabData(%s);" % json.dumps(trigger.split(':', 1)[1])
            if trigger.startswith('health-tab:')
            else "loadPageData(%s);" % json.dumps(page))
    probe = """
<script>
window.addEventListener('load', function () {
  var p = %s, cid = %s;
  var sec = document.getElementById('page-' + p);
  if (sec) sec.classList.add('active');
  try { %s } catch (e) { /* 探针不因异常中断，交给 DOM 判据 */ }
  setTimeout(function () {
    var c = document.getElementById(cid);
    // 自诊断：桩没接管时要能一眼看出来，而不是报成"修复没生效"
    var stubbed = String(window.api).indexOf('probe-forced-failure') >= 0;
    var box = document.createElement('div');
    box.id = 'PROBE_RESULT';
    box.textContent = (stubbed ? 'STUB-OK ' : 'STUB-FAIL ') + (c ? c.textContent : 'NO-CONTAINER');
    document.body.appendChild(box);
    var end = document.createElement('div');
    end.id = 'PROBE_END';
    document.body.appendChild(end);
  }, 1500);
});
</script>
""" % (json.dumps(page), json.dumps(container), fire)
    html = html.replace('</body>', probe + '</body>')
    open(html_path, 'w', encoding='utf-8').write(html)
    # 🔴 返回**上一层** site（即含 app/babycare-fpk/ 的那一层）当服务根。
    #    index.html 里写的是 /app/babycare-fpk/js/app.js（网关前缀绝对路径），
    #    服务根若直接指到 babycare-fpk/，那条请求会 404 ⇒ 页面成"没 JS 的壳"。
    return dst


def dump_dom(site, port, label):
    handler = lambda *a, **kw: http.server.SimpleHTTPRequestHandler(*a, directory=site, **kw)
    # 🔴 URL 必须带网关前缀 /app/babycare-fpk/ —— index.html 与 js/app.js
    #    都挂在这个前缀下（前者决定文档，后者决定静态资源路径）。
    #    漏掉前缀 ⇒ /index.html 404，抓回来的是 Python 的 404 错误页。
    doc = '/app/babycare-fpk/index.html'
    httpd = socketserver.TCPServer(('127.0.0.1', port), handler)
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    try:
        prof = tempfile.mkdtemp(prefix='edgeprof')
        dom = os.path.join(site, '..', 'dom_%s.html' % label)
        with open(dom, 'w', encoding='utf-8') as f:
            subprocess.run([
                EDGE, '--headless', '--disable-gpu', '--no-sandbox',
                '--user-data-dir=' + prof,
                '--virtual-time-budget=5000',
                '--dump-dom', 'http://127.0.0.1:%d%s' % (port, doc),
            ], stdout=f, stderr=subprocess.DEVNULL, timeout=180, cwd=site)
        return dom
    finally:
        httpd.shutdown()


def main():
    print('=' * 72)
    print('动态探针 · 列表失败提示是否真的渲染到 DOM')
    print('=' * 72)
    if not os.path.exists(EDGE):
        print('找不到 Edge：%s' % EDGE)
        return 2

    base = tempfile.mkdtemp(prefix='list_err_probe')
    port = 8793
    all_ok = True
    for i, (page, container, text, trigger) in enumerate(CASES):
        site = build_site(os.path.join(base, page + '-' + container), page, container, text, trigger)
        port += 1
        dom = dump_dom(site, port, page + '-' + container)
        html = open(dom, encoding='utf-8').read()
        # 🔴 不能用 `id="PROBE_RESULT"[^>]*>(.*?)</div>` —— 容器内是 innerHTML，
        #    嵌套标签会让非贪婪匹配在第一个 </div> 处截断，文本只剩个">"。
        #    改用「取该 id 元素到下一个同类 id 之前」的整段，再剥标签。
        m = re.search(r'id="PROBE_RESULT"(.*?)(?=id="PROBE_END"|</body>)', html, re.S)
        got = m.group(1) if m else '<<没抓到 PROBE_RESULT>>'
        # 属性闭合的 '>' 会粘在前导，剥掉再判
        got = re.sub(r'^[^A-Za-z0-9一-鿿]*', '', got).strip()
        got_txt = re.sub(r'<[^>]+>', '', got)
        print('%s / #%s' % (page, container))
        print('    容器文本：%s' % (got_txt[:100] or '（空）'))
        # 桩自检：先确认探针环境成立，否则下面的判据全是假红
        if not got_txt.startswith('STUB-OK'):
            all_ok &= check(False, '探针桩已接管 api（环境问题，不是代码问题）', got_txt[:100])
            continue
        got_txt = got_txt[len('STUB-OK '):]
        all_ok &= check(text in got_txt, '失败文案「%s」出现在 #%s' % (text, container), got_txt[:100])
        # 顺带钉一条反向：不能是「暂无记录」这种把失败伪装成没数据的文案
        all_ok &= check('暂无记录' not in got_txt or text in got_txt,
                        '失败未被伪装成「暂无记录」', got_txt[:100])
    print('-' * 72)
    if FAILURES:
        print('失败 %d 项：%s' % (len(FAILURES), '；'.join(FAILURES)))
        return 1
    print('全部通过：失败提示在真实 DOM 中可见，且与「暂无记录」区分开')
    return 0


if __name__ == '__main__':
    sys.exit(main())
