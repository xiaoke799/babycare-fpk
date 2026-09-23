# -*- coding: utf-8 -*-
"""时间线扩容 —— 前端真实渲染验证（Edge 无头 + 剥前缀的离线预览服务）

为什么需要这一层：verify_timeline_expand.py 验的是**静态一致性**（DOM id 在不在、
typeMap 有没有这个分支、CSS 有没有这个 class）。它证明不了「跑起来真的渲染出东西」——
比如新类型进了 typeMap 但渲染时抛错、或筛选参数没真的发出去，静态检查都是绿的。

本脚本用真实浏览器跑一遍，用探针读 DOM 与网络请求，重点验四件事：
  1. 页面能渲染出条目（不是空壳 —— 空壳往往是 app.js 404，见下面的坑）
  2. 新增类型的图标/标题/明细真的出现在 DOM 里
  3. 类型筛选真把 type 参数发到了接口（行为验证，不是读代码猜）
  4. 「选日期段」能切换出自定义日期输入框，切回去能隐藏

⚠️ 三个已知的「假通过」陷阱，本脚本都做了防：
  - 不剥网关前缀 `/app/babycare-fpk/` → app.js 404 → 页面空壳但看着像「代码没生效」
  - `/api/auth/status` 不给 authenticated:true → 一直卡在验证遮罩上
  - Edge 无头窗口宽度下限约 504px，传更窄会被裁图（这里不量宽度，只读 DOM，规避）
"""
import os
import re
import sys
import json
import time
import shutil
import socket
import tempfile
import subprocess
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

BACKEND = os.path.dirname(os.path.abspath(__file__))
FRONTEND = os.path.abspath(os.path.join(BACKEND, "..", "frontend"))
APP_PREFIX = "/app/babycare-fpk/"
PORT = 8917
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

PASSED, FAILED = [], []


def check(name, cond, detail=None):
    (PASSED if cond else FAILED).append(name)
    line = ("PASS  " if cond else "FAIL  ") + name
    if detail is not None and not cond:
        line += "\n        <- " + str(detail)
    print(line)


# ==================== 假数据：覆盖新增类型 ====================
TIMELINE_FIXTURE = [
    # 喂奶（原有类型，验证没被改坏）
    {"id": 1, "type": "feeding", "time": "2026-09-20 10:00:00", "subtype": "bottle",
     "amount": 120, "side": "both", "note": "早上那顿"},
    {"id": 2, "type": "sleep", "time": "2026-09-20 13:00:00", "end_time": "2026-09-20 14:30:00",
     "duration_minutes": 90, "is_nap": 1, "sleep_quality": "good", "note": ""},
    {"id": 3, "type": "diaper", "time": "2026-09-20 09:00:00", "diaper_type": "wet",
     "color": "yellow", "note": ""},
    # 本次扩容的新类型
    {"id": 4, "type": "pumping", "time": "2026-09-20 06:30:00", "total_amount": 110,
     "left_amount": 60, "right_amount": 50, "duration_minutes": 20, "pump_type": "electric",
     "note": ""},
    {"id": 5, "type": "teething", "time": "2026-09-20 08:00:00", "tooth_name": "下中切牙",
     "position": "下颌", "side": "left", "note": ""},
    {"id": 6, "type": "temperature", "time": "2026-09-19 20:00:00", "temperature": 37.2,
     "measure_method": "ear", "is_fever": 0, "note": ""},
    {"id": 7, "type": "medication", "time": "2026-09-19 20:30:00", "medication_name": "布洛芬混悬液",
     "dosage": "2.5", "dosage_unit": "ml", "note": ""},
    {"id": 8, "type": "vaccine", "time": "2026-09-18 09:00:00", "vaccine_name": "乙肝疫苗",
     "vaccine_type": "free", "dose_number": 2, "hospital": "社区医院", "has_reaction": 0,
     "reaction_detail": "", "note": ""},
    # 2026-09-20 之后的日期用来验「选日期段」能从旧记录里捞出来
    {"id": 9, "type": "checkup", "time": "2026-08-15 10:00:00", "hospital": "市妇幼",
     "doctor": "张医生", "result": "normal", "height": 75.0, "weight": 9.7, "note": ""},
    {"id": 10, "type": "diary", "time": "2026-08-15 21:00:00", "title": "今天很开心",
     "content": "宝宝笑了一下午", "mood": "happy"},
    {"id": 11, "type": "photo", "time": "2026-08-14 16:00:00", "description": "第一次去公园",
     "file_path": "/photos/a.jpg", "is_cover": 0},
]

FAKE = {
    "/api/auth/status": {"success": True, "passwordless": True, "authenticated": True},
    "/api/babies": {"success": True, "data": [{"id": 1, "name": "验证宝宝", "age": "1岁3个月"}]},
}


def timeline_payload(params):
    """按 from/to/type/limit 真筛一遍，让「筛选真的生效」这件事可被断言"""
    rows = list(TIMELINE_FIXTURE)
    d_from = (params.get("from") or [""])[0]
    d_to = (params.get("to") or [""])[0]
    types = (params.get("type") or ["all"])[0]
    try:
        limit = int((params.get("limit") or ["50"])[0])
    except ValueError:
        limit = 50
    if types and types != "all":
        wanted = {t for t in types.split(",") if t}
        rows = [r for r in rows if r["type"] in wanted]
    if d_from:
        rows = [r for r in rows if r["time"][:10] >= d_from]
    if d_to:
        rows = [r for r in rows if r["time"][:10] <= d_to]
    rows.sort(key=lambda r: r["time"], reverse=True)
    return {"success": True, "data": rows[:limit]}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=FRONTEND, **kw)

    def _send_json(self, obj):
        raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        u = urlparse(self.path)
        path, params = u.path, parse_qs(u.query)
        # 网关前缀必须剥掉，否则 app.js/css 404 → 页面空壳（假通过）
        if path.startswith(APP_PREFIX):
            path = "/" + path[len(APP_PREFIX):]
        if path.startswith("/api/"):
            if re.match(r"^/api/babies/\d+/timeline$", path):
                return self._send_json(timeline_payload(params))
            return self._send_json(
                FAKE.get(path, {"success": True, "data": [], "total": 0}))
        self.path = path
        super().do_GET()

    def log_message(self, *a):
        pass


def free_port(p):
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", p))
        return True
    except OSError:
        return False
    finally:
        s.close()


def main():
    if not os.path.exists(EDGE):
        print("找不到 Edge：%s —— 无法做渲染验证" % EDGE)
        return 2
    if not free_port(PORT):
        print("端口 %d 被占用，先释放再跑" % PORT)
        return 2

    with open(os.path.join(FRONTEND, "index.html"), "r", encoding="utf-8") as f:
        html = f.read()

    # 默认页由 class="page active" 决定（初始化不调 switchPage），把时间线换成默认页
    variant = html.replace('<section class="page active" id="page-dashboard">',
                           '<section class="page" id="page-dashboard">')
    variant = variant.replace('<section class="page" id="page-timeline">',
                              '<section class="page active" id="page-timeline">', 1)

    stamp = str(int(time.time()))
    variant = variant.replace('href="/app/babycare-fpk/css/main.css"',
                              'href="/app/babycare-fpk/css/main.css?v=%s"' % stamp)
    variant = variant.replace('src="/app/babycare-fpk/js/app.js"',
                              'src="/app/babycare-fpk/js/app.js?v=%s"' % stamp)

    # 探针 1：在 app.js 之前抓 JS 报错 + 记录时间线请求的真实 URL
    probe_head = """<script>
window.__errs = [];
window.addEventListener('error', function(e){ window.__errs.push(String(e.message)); });
window.__tlUrls = [];
(function(){ var f = window.fetch;
  window.fetch = function(u){ try { if (String(u).indexOf('/timeline') >= 0) window.__tlUrls.push(String(u)); } catch(e){}
    return f.apply(this, arguments); }; })();
</script>
</head>"""
    variant = variant.replace("</head>", probe_head, 1)

    # 探针 2：跑完交互后把结果写进 #DIAG，供 --dump-dom 读取
    probe_body = """<script>
setTimeout(async function(){
  var log = [];
  function wait(ms){ return new Promise(function(r){ setTimeout(r, ms); }); }
  try {
    if (typeof switchPage === 'function') switchPage('timeline');
    await wait(700);
    log.push('typeOptions=' + document.getElementById('timelineType').options.length);
    var items = document.querySelectorAll('#timelineList .timeline-item');
    log.push('items=' + items.length);
    var icons = Array.prototype.map.call(
      document.querySelectorAll('#timelineList .timeline-icon'),
      function(e){ return e.textContent.trim(); });
    log.push('icons=' + icons.join(''));
    var cls = {};
    Array.prototype.forEach.call(document.querySelectorAll('#timelineList .timeline-icon'),
      function(el){ Array.prototype.forEach.call(el.classList, function(c){
        if (c !== 'timeline-icon') cls[c] = 1; }); });
    log.push('iconClasses=' + Object.keys(cls).sort().join(','));
    log.push('titles=' + Array.prototype.map.call(
      document.querySelectorAll('#timelineList .timeline-title'),
      function(e){ return e.textContent; }).slice(0, 5).join('/'));
    log.push('details=' + Array.prototype.map.call(
      document.querySelectorAll('#timelineList .timeline-detail'),
      function(e){ return e.textContent; }).slice(0, 4).join('/'));
    log.push('dateGroups=' + document.querySelectorAll('#timelineList .timeline-date-group').length);

    // 切「全部」：8 月的旧记录只有放开日期才能看见（默认近7天会把它筛掉，这是对的）
    var days = document.getElementById('timelineDays');
    days.value = '0';
    days.dispatchEvent(new Event('change'));
    await wait(700);
    log.push('allItems=' + document.querySelectorAll('#timelineList .timeline-item').length);
    log.push('allGroups=' + document.querySelectorAll('#timelineList .timeline-date-group').length);
    log.push('allIcons=' + Array.prototype.map.call(
      document.querySelectorAll('#timelineList .timeline-icon'),
      function(e){ return e.textContent.trim(); }).join(''));
    var allCls = {};
    Array.prototype.forEach.call(document.querySelectorAll('#timelineList .timeline-icon'),
      function(el){ Array.prototype.forEach.call(el.classList, function(c){
        if (c !== 'timeline-icon') allCls[c] = 1; }); });
    log.push('allClasses=' + Object.keys(allCls).sort().join(','));
    log.push('allDetails=' + Array.prototype.map.call(
      document.querySelectorAll('#timelineList .timeline-detail'),
      function(e){ return e.textContent; }).join('|'));
    log.push('allUrl=' + (window.__tlUrls[window.__tlUrls.length-1] || 'n/a'));

    days.value = 'custom';
    days.dispatchEvent(new Event('change'));
    await wait(200);
    log.push('customDisplay=' + getComputedStyle(document.getElementById('timelineCustomRange')).display);
    log.push('fromDefault=' + document.getElementById('timelineFrom').value);
    log.push('toDefault=' + document.getElementById('timelineTo').value);

    document.getElementById('timelineFrom').value = '2026-08-01';
    document.getElementById('timelineTo').value = '2026-08-31';
    document.getElementById('timelineApplyBtn').click();
    await wait(700);
    log.push('rangeItems=' + document.querySelectorAll('#timelineList .timeline-item').length);
    log.push('rangeUrl=' + (window.__tlUrls[window.__tlUrls.length-1] || 'n/a'));

    days.value = '7';
    days.dispatchEvent(new Event('change'));
    await wait(200);
    log.push('customDisplayBack=' + getComputedStyle(document.getElementById('timelineCustomRange')).display);

    var ts = document.getElementById('timelineType');
    ts.value = 'teething';
    ts.dispatchEvent(new Event('change'));
    await wait(700);
    log.push('filteredItems=' + document.querySelectorAll('#timelineList .timeline-item').length);
    log.push('filteredIcons=' + Array.prototype.map.call(
      document.querySelectorAll('#timelineList .timeline-icon'),
      function(e){ return e.textContent.trim(); }).join(''));
    log.push('filterUrl=' + (window.__tlUrls[window.__tlUrls.length-1] || 'n/a'));
  } catch (e) {
    log.push('EXCEPTION=' + e.message);
  }
  log.push('jsErrors=' + window.__errs.length + (window.__errs.length ? ':' + window.__errs.join('|') : ''));
  var d = document.createElement('div');
  d.id = 'DIAG';
  d.textContent = log.join(' ;; ');
  document.body.appendChild(d);
}, 1000);
</script>
</body>"""
    variant = variant.replace("</body>", probe_body, 1)

    variant_path = os.path.join(FRONTEND, "_tmp_timeline_probe.html")
    with open(variant_path, "w", encoding="utf-8") as f:
        f.write(variant)

    httpd = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()

    profile = tempfile.mkdtemp(prefix="edge_tl_")
    url = "http://127.0.0.1:%d%s_tmp_timeline_probe.html" % (PORT, APP_PREFIX)
    try:
        out = subprocess.run(
            [EDGE, "--headless=new", "--disable-gpu", "--no-first-run",
             "--no-default-browser-check", "--hide-scrollbars", "--mute-audio",
             "--user-data-dir=" + profile, "--virtual-time-budget=9000",
             "--window-size=1280,1400", "--dump-dom", url],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        ).stdout or ""

        m = re.search(r'<div id="DIAG">(.*?)</div>', out, re.S)
        if not m:
            check("页面跑出 DIAG 探针结果", False,
                  "没抓到 DIAG。前 400 字：%s" % out[:400].replace("\n", " "))
            diag = {}
        else:
            check("页面跑出 DIAG 探针结果", True)
            diag = {}
            for part in m.group(1).split(";;"):
                if "=" in part:
                    k, v = part.split("=", 1)
                    diag[k.strip()] = v.strip()

        def d(key):
            return diag.get(key, "")

        # ---- 断言 ----
        check("时间线渲染出了条目（不是空壳）",
              d("items").isdigit() and int(d("items")) > 0, {"items": d("items")})
        check("JS 无运行时报错", d("jsErrors") == "0", d("jsErrors"))
        check("类型下拉有 19 个选项（全部 + 18 类）", d("typeOptions") == "19", d("typeOptions"))

        # 默认档是「近7天」（今天 2026-09-23 → 窗口自 09-17 起）：8 月那三条不该出现。
        # 窗口内落到 09-18 / 09-19 / 09-20 三个日期，故 8 条 3 组。
        # 固定断言条数，能同时证明日期筛选真的在起作用，而不是把 fixture 全倒出来。
        check("默认近7天档只显示窗口内的 8 条", d("items") == "8", {"items": d("items")})
        check("近7天档按日期分成 3 组（09-18/19/20）",
              d("dateGroups") == "3", {"dateGroups": d("dateGroups")})

        # 「全部」档：8 月的旧记录必须能捞回来（这是「看一个月前」的关键路径）
        # 11 条落到 5 个日期：09-20、09-19、09-18、08-15、08-14
        check("切「全部」后显示全部 11 条（含 8 月旧记录）",
              d("allItems") == "11", {"allItems": d("allItems")})
        check("切「全部」后分成 5 个日期组",
              d("allGroups") == "5", {"allGroups": d("allGroups")})
        check("「全部」档不带 from 参数（不限日期）",
              "from=" not in d("allUrl"), d("allUrl"))

        # 11 类图标的字符与配色 class 都要真的落到 DOM 上
        ALL_ICONS = ["喂", "睡", "布", "吸", "牙", "温", "药", "苗", "检", "记", "图"]
        ALL_CLASSES = ["feeding", "sleep", "diaper", "pumping", "teething", "temperature",
                       "medication", "vaccine", "checkup", "diary", "photo"]
        missing_icon = [c for c in ALL_ICONS if c not in d("allIcons")]
        missing_cls = [c for c in ALL_CLASSES if c not in d("allClasses")]
        check("全部 11 类图标都渲染出来", not missing_icon,
              {"缺": missing_icon, "实得": d("allIcons")})
        check("全部 11 类配色 class 都落到 DOM", not missing_cls,
              {"缺": missing_cls, "实得": d("allClasses")})

        check("标题文案按类型生成（不是清一色「记录」）",
              "出牙" in d("titles") and ("吸奶" in d("titles") or "体温测量" in d("titles")),
              d("titles"))
        # 明细含各类型的数值/单位：体温 37.2、吸奶 110ml、体检医院名 —— 取全量明细判
        check("明细文案渲染出来（体温 37.2 在）", "37.2" in d("allDetails"), d("allDetails")[:160])
        check("明细文案渲染出来（吸奶 110ml 在）", "110" in d("allDetails"), d("allDetails")[:160])
        check("明细文案渲染出来（体检医院名在）", "市妇幼" in d("allDetails"), d("allDetails")[:160])

        # 日期段：显隐 + 默认值 + 真的把 from/to 发出去
        check("选「选日期段」时自定义输入框显示出来", d("customDisplay") == "flex",
              d("customDisplay"))
        check("自定义日期段默认填了起始日期", bool(d("fromDefault")), d("fromDefault"))
        check("点「查看」后按日期段取到 8 月的旧记录",
              d("rangeItems") == "3", {"rangeItems": d("rangeItems"), "url": d("rangeUrl")})
        check("from/to 真的发到了接口上",
              "from=2026-08-01" in d("rangeUrl") and "to=2026-08-31" in d("rangeUrl"),
              d("rangeUrl"))
        check("切回「近7天」时自定义输入框收起", d("customDisplayBack") == "none",
              d("customDisplayBack"))

        # 类型筛选：行为验证 —— 参数发出去 + 结果只剩该类型
        check("类型筛选把 type 参数发到接口（服务端筛选生效）",
              "type=teething" in d("filterUrl"), d("filterUrl"))
        check("筛「出牙」后列表只剩出牙那一条",
              d("filteredItems") == "1" and d("filteredIcons") == "牙",
              {"items": d("filteredItems"), "icons": d("filteredIcons")})
        check("筛选请求带上了 limit=2000",
              "limit=2000" in d("filterUrl"), d("filterUrl"))
    finally:
        httpd.shutdown()
        try:
            os.remove(variant_path)
        except OSError:
            pass
        shutil.rmtree(profile, ignore_errors=True)

    print("\n" + "=" * 60)
    print("通过 %d 项，失败 %d 项" % (len(PASSED), len(FAILED)))
    if FAILED:
        print("失败明细：")
        for name in FAILED:
            print("  - " + name)
    print("=" * 60)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
