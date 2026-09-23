# -*- coding: utf-8 -*-
"""时间线 + 首页「看某一天」—— 前端真实渲染验证（Edge 无头 + 剥前缀的离线预览服务）

为什么需要这一层：verify_timeline_expand.py 验的是**静态一致性**（DOM id 在不在、
类型表有没有这个分支、CSS 有没有这个 class）。它证明不了「跑起来真的渲染出东西」——
比如新类型进了类型表但渲染时抛错、或筛选参数没真的发出去，静态检查都是绿的。

本脚本用真实浏览器跑一遍，用探针读 DOM 与网络请求，验四件事：
  1. 时间线能渲染出条目（不是空壳 —— 空壳往往是 app.js 404，见下面的坑）
  2. 18 类的图标/标题/明细真的出现在 DOM 里，日期档与「选日期段」真的生效
  3. 类型筛选真把 type 参数发到了接口（行为验证，不是读代码猜）
  4. 首页日期条：点「昨天」整页切到回看态（文案/区块显隐/概览数字），点回「今天」复原

⚠️ 三个已知的「假通过」陷阱，本脚本都做了防：
  - 不剥网关前缀 `/app/babycare-fpk/` → app.js 404 → 页面空壳但看着像「代码没生效」
  - `/api/auth/status` 不给 authenticated:true → 一直卡在验证遮罩上
  - Edge 无头窗口宽度下限约 504px，传更窄会被裁图（这里不量宽度，只读 DOM，规避）

⚠️ 假数据里的日期一律按「相对今天」生成。写绝对日期（2026-09-20 之类）当时能跑通，
   过几天就自己变红 —— 而「近7天」这类档位是跟着真实日期走的，测试会随时间腐坏。
"""
import os
import re
import sys
import json
import time
import shutil
import socket
import datetime
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


# ==================== 假数据：覆盖全部 18 类 ====================
TODAY = datetime.date.today()


def day(offset):
    return (TODAY - datetime.timedelta(days=offset)).strftime("%Y-%m-%d")


D_TODAY, D_YEST, D_D2 = day(0), day(1), day(2)
D_OLD, D_OLD2 = day(30), day(31)


def ts(d, hhmm):
    return "%s %s:00" % (d, hhmm)


_ev_id = [0]


def _ev(typ, t, **kw):
    _ev_id[0] += 1
    row = {"id": _ev_id[0], "type": typ, "time": t}
    row.update(kw)
    return row


# 今天 12 类 / 昨天 5 类 / 前天 1 类 / 一个月前 3 类 —— 合起来正好 18 类、5 个日期组
TIMELINE_FIXTURE = [
    _ev("feeding", ts(D_TODAY, "10:00"), subtype="bottle", amount=120, side="both", note="早上那顿"),
    _ev("sleep", ts(D_TODAY, "13:00"), end_time=ts(D_TODAY, "14:30"),
        duration_minutes=90, is_nap=1, sleep_quality="good", note=""),
    _ev("diaper", ts(D_TODAY, "09:00"), diaper_type="wet", color="yellow", note=""),
    _ev("pumping", ts(D_TODAY, "06:30"), total_amount=110, left_amount=60,
        right_amount=50, duration_minutes=20, pump_type="electric", note=""),
    _ev("teething", ts(D_TODAY, "08:00"), tooth_name="下中切牙", position="下颌", side="left", note=""),
    _ev("growth", ts(D_TODAY, "07:00"), height=75.5, weight=9.8, head_circumference=45.0,
        bmi=17.2, note=""),
    _ev("milestone", ts(D_TODAY, "16:00"), title="会翻身", description="第一次自己翻身", category="motor"),
    _ev("solidfood", ts(D_TODAY, "11:30"), food_name="西兰花", food_category="vegetable",
        amount="2勺", reaction="none", note=""),
    _ev("tummy", ts(D_TODAY, "15:00"), duration_minutes=10, milestone="抬头稳", note=""),
    _ev("fontanelle", ts(D_TODAY, "17:00"), anterior_size=1.5, anterior_status="closing",
        posterior_status="closed", note=""),
    _ev("leap", ts(D_TODAY, "18:00"), leap_number=5, end_date=D_TODAY, is_completed=1, note=""),
    _ev("allergy", ts(D_TODAY, "19:00"), food_name="鸡蛋", day_number=2, has_reaction=1,
        reaction_detail="轻微皮疹", status="failed", note=""),
    _ev("feeding", ts(D_YEST, "10:00"), subtype="bottle", amount=150, side="", note=""),
    _ev("sleep", ts(D_YEST, "13:00"), end_time=ts(D_YEST, "15:00"),
        duration_minutes=120, is_nap=1, sleep_quality="good", note=""),
    _ev("diaper", ts(D_YEST, "09:00"), diaper_type="dirty", color="brown", note=""),
    _ev("temperature", ts(D_YEST, "20:00"), temperature=37.2, measure_method="ear",
        is_fever=0, note=""),
    _ev("medication", ts(D_YEST, "20:30"), medication_name="布洛芬混悬液", dosage="2.5",
        dosage_unit="ml", note=""),
    _ev("vaccine", ts(D_D2, "09:00"), vaccine_name="乙肝疫苗", vaccine_type="free",
        dose_number=2, hospital="社区医院", has_reaction=0, reaction_detail="", note=""),
    _ev("checkup", ts(D_OLD, "10:00"), hospital="市妇幼", doctor="张医生", result="normal",
        height=75.0, weight=9.7, head_circumference=44.8, note=""),
    _ev("diary", ts(D_OLD, "21:00"), title="今天很开心", content="宝宝笑了一下午", mood="happy"),
    _ev("photo", ts(D_OLD2, "16:00"), description="第一次去公园", file_path="/photos/a.jpg", is_cover=0),
]

ALL_TYPES = sorted({e["type"] for e in TIMELINE_FIXTURE})
# 时间线类目的单字图标（与 app.js TIMELINE_TYPE_MAP 对齐）
ICON_BY_TYPE = {
    "feeding": "喂", "sleep": "睡", "diaper": "布", "pumping": "吸", "teething": "牙",
    "temperature": "温", "medication": "药", "vaccine": "苗", "checkup": "检", "diary": "记",
    "photo": "图", "growth": "长", "milestone": "★", "solidfood": "辅", "tummy": "趴",
    "fontanelle": "囟", "leap": "跃", "allergy": "敏",
}

FAKE = {
    "/api/auth/status": {"success": True, "passwordless": True, "authenticated": True},
    "/api/babies": {"success": True, "data": [{"id": 1, "name": "验证宝宝", "age": "1岁3个月"}]},
    "/api/babies/1/dashboard": {"success": True, "data": {
        "baby_profile": {"name": "验证宝宝", "age": "1岁3个月", "gender": "男", "weight": 9.8},
        "today_feeding": {"count": 2, "total_amount": 270},
        "today_sleep": {"total_minutes": 210},
        "today_diaper": {"count": 2},
        "latest_growth": {"record_date": D_TODAY, "height": 75.5, "weight": 9.8,
                          "bmi": 17.2, "head_circumference": 45.0},
        "latest_milestones": [],
        "recent_records": [
            {"type": "feeding", "type_label": "喂奶", "time": "10:00", "title": "喂奶",
             "summary": "配方奶 · 120ml", "id": 1},
        ],
    }},
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


# ==================== 探针：一次加载，先跑时间线，再切回首页跑日期条 ====================
PROBE_BODY = """<script>
setTimeout(async function(){
  var log = [];
  function wait(ms){ return new Promise(function(r){ setTimeout(r, ms); }); }
  function txt(id){ var e = document.getElementById(id); return e ? e.textContent : 'NULL'; }
  function disp(id){ var e = document.getElementById(id); return e ? getComputedStyle(e).display : 'NULL'; }
  function icons(sel){ return Array.prototype.map.call(document.querySelectorAll(sel),
      function(e){ return e.textContent.trim(); }).join(''); }
  function classes(sel){
    var s = {}; Array.prototype.forEach.call(document.querySelectorAll(sel), function(el){
      Array.prototype.forEach.call(el.classList, function(c){
        if (c !== 'timeline-icon') s[c] = 1; }); });
    return Object.keys(s).sort().join(','); }
  function lastUrl(){ return window.__tlUrls[window.__tlUrls.length-1] || 'n/a'; }

  try {
    // ---------- 一、时间线 ----------
    switchPage('timeline');
    await wait(700);
    log.push('typeOptions=' + document.getElementById('timelineType').options.length);
    log.push('items=' + document.querySelectorAll('#timelineList .timeline-item').length);
    log.push('dateGroups=' + document.querySelectorAll('#timelineList .timeline-date-group').length);

    var days = document.getElementById('timelineDays');
    days.value = '0';
    days.dispatchEvent(new Event('change'));
    await wait(700);
    log.push('allItems=' + document.querySelectorAll('#timelineList .timeline-item').length);
    log.push('allGroups=' + document.querySelectorAll('#timelineList .timeline-date-group').length);
    log.push('allIcons=' + icons('#timelineList .timeline-icon'));
    log.push('allClasses=' + classes('#timelineList .timeline-icon'));
    log.push('allDetails=' + Array.prototype.map.call(
      document.querySelectorAll('#timelineList .timeline-detail'),
      function(e){ return e.textContent; }).join('|'));
    log.push('allUrl=' + lastUrl());
    log.push('titles=' + Array.prototype.map.call(
      document.querySelectorAll('#timelineList .timeline-title'),
      function(e){ return e.textContent; }).slice(0, 8).join('/'));

    days.value = 'custom';
    days.dispatchEvent(new Event('change'));
    await wait(200);
    log.push('customDisplay=' + disp('timelineCustomRange'));
    document.getElementById('timelineFrom').value = window.__oldDay2;
    document.getElementById('timelineTo').value = window.__oldDay;
    document.getElementById('timelineApplyBtn').click();
    await wait(700);
    log.push('rangeItems=' + document.querySelectorAll('#timelineList .timeline-item').length);
    log.push('rangeUrl=' + lastUrl());

    days.value = '7';
    days.dispatchEvent(new Event('change'));
    await wait(200);
    log.push('customDisplayBack=' + disp('timelineCustomRange'));

    var ts = document.getElementById('timelineType');
    ts.value = 'teething';
    ts.dispatchEvent(new Event('change'));
    await wait(700);
    log.push('filteredItems=' + document.querySelectorAll('#timelineList .timeline-item').length);
    log.push('filteredIcons=' + icons('#timelineList .timeline-icon'));
    log.push('filterUrl=' + lastUrl());

    // ---------- 二、首页「看某一天」 ----------
    switchPage('dashboard');
    await wait(800);
    log.push('homeTodayLabel=' + txt('homeGreetingLabel'));
    log.push('homeTodayStatsTitle=' + txt('homeStatsTitle'));
    log.push('homeTodayRecentTitle=' + txt('homeRecentTitle'));
    log.push('homeTodayQA=' + disp('homeQuickActions'));
    log.push('homeTodayBody=' + disp('homeBodySection'));
    log.push('homeTodayGreet=' + document.querySelector('.home-greeting-title').textContent);

    document.querySelector('#homeDateBar [data-home-offset="1"]').click();
    await wait(800);
    log.push('homeYestLabel=' + txt('homeGreetingLabel'));
    log.push('homeYestStatsTitle=' + txt('homeStatsTitle'));
    log.push('homeYestRecentTitle=' + txt('homeRecentTitle'));
    log.push('homeYestGreet=' + document.querySelector('.home-greeting-title').textContent);
    log.push('homeYestQA=' + disp('homeQuickActions'));
    log.push('homeYestBody=' + disp('homeBodySection'));
    log.push('homeYestFeed=' + txt('homeFeedingCount'));
    log.push('homeYestSleep=' + txt('homeSleepTime'));
    log.push('homeYestDiaper=' + txt('homeDiaperCount'));
    log.push('homeYestItems=' + document.querySelectorAll('#homeRecentList .timeline-item').length);
    log.push('homeYestIcons=' + icons('#homeRecentList .timeline-icon'));
    log.push('homeYestTitles=' + Array.prototype.map.call(
      document.querySelectorAll('#homeRecentList .timeline-title'),
      function(e){ return e.textContent; }).join('/'));
    log.push('homeYestInput=' + (document.getElementById('homeDateInput')||{}).value);
    log.push('homeYestUrl=' + lastUrl());

    var input = document.getElementById('homeDateInput');
    input.value = window.__oldDay;
    input.dispatchEvent(new Event('change'));
    await wait(800);
    log.push('homeOldItems=' + document.querySelectorAll('#homeRecentList .timeline-item').length);
    log.push('homeOldStatsTitle=' + txt('homeStatsTitle'));
    log.push('homeOldUrl=' + lastUrl());

    document.querySelector('#homeDateBar [data-home-offset="0"]').click();
    await wait(800);
    log.push('homeBackStatsTitle=' + txt('homeStatsTitle'));
    log.push('homeBackRecentTitle=' + txt('homeRecentTitle'));
    log.push('homeBackLabel=' + txt('homeGreetingLabel'));
    log.push('homeBackQA=' + disp('homeQuickActions'));
    log.push('homeBackBody=' + disp('homeBodySection'));
    log.push('homeBackUrl=' + lastUrl());
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


def main():
    if not os.path.exists(EDGE):
        print("找不到 Edge：%s —— 无法做渲染验证" % EDGE)
        return 2
    if not free_port(PORT):
        print("端口 %d 被占用，先释放再跑" % PORT)
        return 2

    with open(os.path.join(FRONTEND, "index.html"), "r", encoding="utf-8") as f:
        html = f.read()

    stamp = str(int(time.time()))
    variant = html.replace('href="/app/babycare-fpk/css/main.css"',
                           'href="/app/babycare-fpk/css/main.css?v=%s"' % stamp)
    variant = variant.replace('src="/app/babycare-fpk/js/app.js"',
                              'src="/app/babycare-fpk/js/app.js?v=%s"' % stamp)

    # 探针 1：在 app.js 之前抓 JS 报错 + 记录请求过的 timeline URL + 注入日期基线
    probe_head = """<script>
window.__errs = [];
window.addEventListener('error', function(e){ window.__errs.push(String(e.message)); });
window.__tlUrls = [];
window.__oldDay = %s;
window.__oldDay2 = %s;
(function(){ var f = window.fetch;
  window.fetch = function(u){ try { if (String(u).indexOf('/timeline') >= 0) window.__tlUrls.push(String(u)); } catch(e){}
    return f.apply(this, arguments); }; })();
</script>
</head>""" % (json.dumps(D_OLD), json.dumps(D_OLD2))
    variant = variant.replace("</head>", probe_head, 1)
    variant = variant.replace("</body>", PROBE_BODY, 1)

    variant_path = os.path.join(FRONTEND, "_tmp_timeline_probe.html")
    with open(variant_path, "w", encoding="utf-8") as f:
        f.write(variant)

    httpd = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    profile = tempfile.mkdtemp(prefix="edge_tl_")
    url = "http://127.0.0.1:%d%s_tmp_timeline_probe.html" % (PORT, APP_PREFIX)
    try:
        out = subprocess.run(
            [EDGE, "--headless=new", "--disable-gpu", "--no-first-run",
             "--no-default-browser-check", "--hide-scrollbars", "--mute-audio",
             "--user-data-dir=" + profile, "--virtual-time-budget=30000",
             "--window-size=1280,1600", "--dump-dom", url],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
        ).stdout or ""

        m = re.search(r'<div id="DIAG">(.*?)</div>', out, re.S)
        diag = {}
        if not m:
            check("页面跑出 DIAG 探针结果", False,
                  "没抓到 DIAG。前 400 字：%s" % out[:400].replace("\n", " "))
        else:
            check("页面跑出 DIAG 探针结果", True)
            for part in m.group(1).split(";;"):
                if "=" in part:
                    k, v = part.split("=", 1)
                    diag[k.strip()] = v.strip()

        def d(key):
            return diag.get(key, "")

        n_all = len(TIMELINE_FIXTURE)
        n_week = len([e for e in TIMELINE_FIXTURE if e["time"][:10] >= day(6)])
        n_groups_all = len({e["time"][:10] for e in TIMELINE_FIXTURE})
        n_groups_week = len({e["time"][:10] for e in TIMELINE_FIXTURE if e["time"][:10] >= day(6)})
        n_old = len([e for e in TIMELINE_FIXTURE if day(31) <= e["time"][:10] <= day(30)])

        # ---------- 一、时间线 ----------
        check("时间线渲染出了条目（不是空壳）",
              d("items").isdigit() and int(d("items")) > 0, {"items": d("items")})
        check("JS 无运行时报错", d("jsErrors") == "0", d("jsErrors"))
        check("类型下拉有 19 个选项（全部 + 18 类）", d("typeOptions") == "19", d("typeOptions"))
        check("默认近7天档条数正确（%d 条）" % n_week, d("items") == str(n_week), {"items": d("items")})
        check("近7天档按日期分成 %d 组" % n_groups_week,
              d("dateGroups") == str(n_groups_week), {"dateGroups": d("dateGroups")})

        check("切「全部」后条数正确（%d 条）" % n_all, d("allItems") == str(n_all),
              {"allItems": d("allItems")})
        check("切「全部」后分成 %d 个日期组" % n_groups_all,
              d("allGroups") == str(n_groups_all), {"allGroups": d("allGroups")})
        check("「全部」档不带 from 参数（不限日期）", "from=" not in d("allUrl"), d("allUrl"))

        missing_icon = [t for t in ALL_TYPES if ICON_BY_TYPE[t] not in d("allIcons")]
        missing_cls = [t for t in ALL_TYPES if t not in d("allClasses")]
        check("全部 %d 类图标都渲染出来" % len(ALL_TYPES), not missing_icon,
              {"缺": missing_icon, "实得": d("allIcons")})
        check("全部 %d 类配色 class 都落到 DOM" % len(ALL_TYPES), not missing_cls,
              {"缺": missing_cls, "实得": d("allClasses")})

        # titles 取的是「全部」档里最新的 8 条（按时间倒序），所以断言要挑这段时间靠前的类型。
        # 用「囟门检查 / 过敏测试」这种只有新类型才会生成的文案，能证明不是清一色兜底「记录」。
        check("标题文案按类型生成（不是清一色「记录」）",
              "囟门检查" in d("titles") and "过敏测试" in d("titles"), d("titles"))
        check("明细含数值（体温 37.2）", "37.2" in d("allDetails"), d("allDetails")[:160])
        check("明细含数值（吸奶 110ml）", "110" in d("allDetails"), d("allDetails")[:160])
        check("明细含文本（体检医院名）", "市妇幼" in d("allDetails"), d("allDetails")[:160])

        check("选「选日期段」时自定义输入框显示出来", d("customDisplay") == "flex",
              d("customDisplay"))
        check("按日期段取到一个月前的旧记录（%d 条）" % n_old,
              d("rangeItems") == str(n_old), {"rangeItems": d("rangeItems"), "url": d("rangeUrl")})
        check("from/to 真的发到了接口上",
              ("from=" + day(31)) in d("rangeUrl") and ("to=" + day(30)) in d("rangeUrl"),
              d("rangeUrl"))
        check("切回「近7天」时自定义输入框收起", d("customDisplayBack") == "none",
              d("customDisplayBack"))

        check("类型筛选把 type 参数发到接口（服务端筛选生效）",
              "type=teething" in d("filterUrl"), d("filterUrl"))
        check("筛「出牙」后列表只剩出牙那一条",
              d("filteredItems") == "1" and d("filteredIcons") == "牙",
              {"items": d("filteredItems"), "icons": d("filteredIcons")})
        check("筛选请求带上了 limit=2000", "limit=2000" in d("filterUrl"), d("filterUrl"))

        # ---------- 二、首页「看某一天」 ----------
        check("首页初始态：概览标题是「今日概览」", d("homeTodayStatsTitle") == "今日概览",
              d("homeTodayStatsTitle"))
        check("首页初始态：列表标题是「最近记录」", d("homeTodayRecentTitle") == "最近记录",
              d("homeTodayRecentTitle"))
        check("首页初始态：快捷记录可见（没被回看态误伤）", d("homeTodayQA") != "none",
              d("homeTodayQA"))
        check("首页初始态：身体数据块可见", d("homeTodayBody") != "none", d("homeTodayBody"))
        check("首页初始态：标题是「…的今日」",
              d("homeTodayGreet").endswith("的今日"), d("homeTodayGreet"))

        check("点「昨天」后问候语变为「回看记录」", d("homeYestLabel") == "回看记录",
              d("homeYestLabel"))
        check("点「昨天」后概览标题带日期且不再是「今日」",
              "概览" in d("homeYestStatsTitle") and "今日" not in d("homeYestStatsTitle"),
              d("homeYestStatsTitle"))
        # 昨天这种「就近的一天」用口语词更顺眼，所以标题是「…的昨天」而不是具体日期
        check("点「昨天」后大标题变成「…的昨天」（不再是「的今日」）",
              d("homeYestGreet").endswith("的昨天"), d("homeYestGreet"))
        check("点「昨天」后列表标题变「那天的记录」", d("homeYestRecentTitle") == "那天的记录",
              d("homeYestRecentTitle"))
        check("回看态收起快捷记录（避免在历史数据下误点录入）", d("homeYestQA") == "none",
              d("homeYestQA"))
        check("回看态收起「最新测量」块", d("homeYestBody") == "none", d("homeYestBody"))

        # 昨天的概览数字：喂奶 1 次 / 睡眠 120 分 -> 2h / 尿布 1 次
        check("回看态概览：喂奶次数按那天算", d("homeYestFeed") == "1", d("homeYestFeed"))
        check("回看态概览：睡眠时长按那天算（120 分 -> 2h）", d("homeYestSleep") == "2h",
              d("homeYestSleep"))
        check("回看态概览：尿布次数按那天算", d("homeYestDiaper") == "1", d("homeYestDiaper"))
        check("回看态按那天取数（from=to=昨天）",
              ("from=" + D_YEST) in d("homeYestUrl") and ("to=" + D_YEST) in d("homeYestUrl"),
              d("homeYestUrl"))
        check("回看态列出那天的 5 条记录", d("homeYestItems") == "5", d("homeYestItems"))
        check("回看态列表用的是时间线同一套图标",
              "温" in d("homeYestIcons") and "药" in d("homeYestIcons"), d("homeYestIcons"))
        check("回看态条目文案正确（用药名）", "布洛芬" in d("homeYestTitles"), d("homeYestTitles"))
        check("回看态日期输入框同步为所选日期", d("homeYestInput") == D_YEST, d("homeYestInput"))

        # 手选的是「30 天前」那一天（from=to=同日），那天有体检 + 日记 2 条；
        # 照片在 31 天前，属于另一天，不该被算进来 —— 顺带验了单日取数没有多捞。
        check("手选一个月前那天取到那天的 2 条（不多捞邻日）", d("homeOldItems") == "2",
              {"items": d("homeOldItems"), "url": d("homeOldUrl")})
        check("手选日期时概览标题同步带日期",
              "概览" in d("homeOldStatsTitle") and "今日" not in d("homeOldStatsTitle"),
              d("homeOldStatsTitle"))
        check("手选日期按 from=to=那天取数",
              ("from=" + D_OLD) in d("homeOldUrl") and ("to=" + D_OLD) in d("homeOldUrl"),
              d("homeOldUrl"))

        check("点回「今天」后概览标题复原", d("homeBackStatsTitle") == "今日概览",
              d("homeBackStatsTitle"))
        check("点回「今天」后列表标题复原", d("homeBackRecentTitle") == "最近记录",
              d("homeBackRecentTitle"))
        check("点回「今天」后问候语复原（非「回看记录」）", d("homeBackLabel") != "回看记录",
              d("homeBackLabel"))
        check("点回「今天」后快捷记录恢复可见", d("homeBackQA") != "none", d("homeBackQA"))
        check("点回「今天」后身体数据块恢复可见", d("homeBackBody") != "none", d("homeBackBody"))
        check("点回「今天」不按日期取数（回到 dashboard 路径）",
              ("from=" + D_TODAY) not in d("homeBackUrl"), d("homeBackUrl"))
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
