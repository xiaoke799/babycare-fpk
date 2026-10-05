# -*- coding: utf-8 -*-
"""换尿布 / 趴睡训练 / 出牙记录 三页的离线渲染验证（Edge 无头 + 剥前缀的假 API）

为什么单独一个脚本：这几页的缺陷都属于「**看起来正常、实际是错的**」那一类，
静态检查（DOM id 在不在、函数被不被调用）全都发现不了，必须在真浏览器里点一遍：

换尿布：
  1. 皮肤状况标注**不再新增记录**（这是本脚本最关键的一条）。
     以前点一下就是 POST 一条 `diaper_type='wet'`，连点三次凭空多三条小便。
     判据用**行为**而不是 HTTP 方法：点完看列表条数有没有变、标注有没有落到那一条上。
  2. 日期档位真的生效（切到「全部」能看到窗口外的记录）。
  3. 「加载更多」能翻页，且翻完自动隐藏。
  4. 皮肤选项补齐「重度红臀」（后端枚举一直有，UI 之前缺）。

趴睡训练：
  5. 计时不足 1 分钟不再记成 0 —— 判据是「今日分钟」会不会从 3 变成 4。
     如果还按 Math.floor(秒/60) 记 0，今日时长不会变，缺陷就藏住了。
  6. 离开页面自动暂停计时器。
  7. 提示卡不再出现医学断言（"预防扁头综合征"），且有免责声明。

出牙记录（2026-10-05 补）：
  8. 点牙位图能真的新增（页面曾调 /api/babies/<id>/teeth，而后端只有 /teething，
     被当死接口删掉后整页失效：列表空、点击静默无反应、删除假装成功）。
  9. 删除走 /api/teething/<id> 且删完标记回落。

⚠️ 假数据的日期按「相对今天」生成：写死绝对日期会随时间腐坏。
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
PORT = 8921
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

PASSED, FAILED = [], []


def check(name, cond, detail=None):
    (PASSED if cond else FAILED).append(name)
    line = ("PASS  " if cond else "FAIL  ") + name
    if detail is not None and not cond:
        line += "\n        <- " + str(detail)
    print(line)


# ==================== 相对今天造数据 ====================
TODAY = datetime.date.today()


def day(offset):
    return (TODAY - datetime.timedelta(days=offset)).strftime("%Y-%m-%d")


D_TODAY, D_OLD = day(0), day(9)

# 内存里的假库：会被 PUT/POST 真实改动，用来观察"有没有偷偷新建记录"
STATE = {
    "diaper": [
        {"id": 301, "baby_id": 1, "change_time": D_TODAY + " 09:10:00", "diaper_type": "wet",
         "color": None, "skin_condition": "normal", "brand": None, "note": ""},
        {"id": 302, "baby_id": 1, "change_time": D_TODAY + " 11:20:00", "diaper_type": "dirty",
         "color": "brown", "skin_condition": "normal", "brand": None, "note": ""},
        {"id": 303, "baby_id": 1, "change_time": D_TODAY + " 13:05:00", "diaper_type": "both",
         "color": "yellow", "skin_condition": None, "brand": None, "note": ""},
        {"id": 201, "baby_id": 1, "change_time": D_OLD + " 08:00:00", "diaper_type": "wet",
         "color": None, "skin_condition": "normal", "brand": None, "note": ""},
        {"id": 202, "baby_id": 1, "change_time": D_OLD + " 09:30:00", "diaper_type": "wet",
         "color": None, "skin_condition": "normal", "brand": None, "note": ""},
    ],
    "tummytime": [
        {"id": 401, "baby_id": 1, "start_time": D_TODAY + " 10:00:00",
         "end_time": D_TODAY + " 10:03:00", "duration_minutes": 3,
         "milestone": "保持1分钟", "note": ""},
    ],
    # 出牙记录：预置 1 条（code 61 = 左上中切牙），点牙位图加一颗 → 变 2 条，删掉 → 回 1 条。
    "teething": [
        {"id": 501, "baby_id": 1, "tooth_code": "61", "tooth_name": "中切牙",
         "erupt_date": D_OLD, "position": "", "side": "", "note": ""},
    ],
}

PAGE_SIZE = 3          # 故意调小，好在一屏里验「加载更多」
DIAPER_COUNT_CALLS = []   # 记录前端拉列表时带的参数，用来确认筛选真的发出去了


def diaper_page(params):
    rows = list(STATE["diaper"])
    start = (params.get("start") or [""])[0]
    if start:
        rows = [r for r in rows if r["change_time"][:10] >= start]
    rows.sort(key=lambda r: r["change_time"], reverse=True)
    try:
        limit = int((params.get("limit") or ["500"])[0])
    except ValueError:
        limit = 500
    # 故意把单页压到 3 条：前端请求的是 limit=100，服务端少给几条并 has_more=true
    # 是完全合法的响应，正好能在几屏之内把「加载更多」这条路径跑完。
    limit = min(limit, PAGE_SIZE)
    try:
        offset = int((params.get("offset") or ["0"])[0])
    except ValueError:
        offset = 0
    page = rows[offset:offset + limit]
    return {"success": True, "data": page, "total": len(rows),
            "limit": limit, "offset": offset,
            "has_more": offset + len(page) < len(rows)}


FAKE = {
    "/api/auth/status": {"success": True, "passwordless": True, "authenticated": True},
    "/api/babies": {"success": True, "data": [{"id": 1, "name": "验证宝宝", "age": "1岁3个月"}]},
    "/api/storage/app-info": {"success": True, "version": "0.0.2"},
}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=FRONTEND, **kw)

    def _json(self, obj, code=200):
        raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _body(self):
        try:
            return json.loads((self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}"))
        except Exception:
            return {}

    def do_GET(self):
        u = urlparse(self.path)
        path, params = u.path, parse_qs(u.query)
        if path.startswith(APP_PREFIX):
            path = "/" + path[len(APP_PREFIX):]
        if path.startswith("/api/"):
            m = re.match(r"^/api/babies/\d+/diaper$", path)
            if m:
                DIAPER_COUNT_CALLS.append({k: v[0] for k, v in params.items()})
                return self._json(diaper_page(params))
            if re.match(r"^/api/babies/\d+/tummytime$", path):
                rows = sorted(STATE["tummytime"],
                              key=lambda r: r["start_time"], reverse=True)
                return self._json({"success": True, "data": rows})
            # 单条查询：编辑回填要用。漏了这个接口的话，编辑会拿到空数组，
            # 表单全空 —— 症状和「编辑功能坏了」一模一样，测的人会一头雾水。
            m1 = re.match(r"^/api/babies/\d+/tummytime/(\d+)$", path)
            if m1:
                rid = int(m1.group(1))
                for r in STATE["tummytime"]:
                    if r["id"] == rid:
                        return self._json({"success": True, "data": dict(r)})
                return self._json({"success": False, "message": "记录不存在"}, 404)
            # 出牙记录：出牙页（page-teeth 牙位图）读的就是这条。
            # ⚠️ 这里必须真的返回 tooth_code —— 页面靠它判断哪颗牙已萌出；
            #    少了它就等于「一颗都没长」，而断言恰好会红（这正是我们要的）。
            if re.match(r"^/api/babies/\d+/teething$", path):
                rows = sorted(STATE["teething"], key=lambda r: r["erupt_date"], reverse=True)
                return self._json({"success": True, "data": rows})
            if path == "/api/babies/1/dashboard":
                return self._json({"success": True, "data": {
                    "baby_profile": {"name": "验证宝宝", "age": "1岁3个月"},
                    "today_feeding": {"count": 0, "total_amount": 0},
                    "today_sleep": {"total_minutes": 0}, "today_diaper": {"count": 0},
                    "latest_growth": None, "latest_milestones": [],
                    "recent_records": []}})
            return self._json(FAKE.get(path, {"success": True, "data": []}))
        self.path = path
        super().do_GET()

    def do_POST(self):
        u = urlparse(self.path)
        path = u.path
        if path.startswith(APP_PREFIX):
            path = "/" + path[len(APP_PREFIX):]
        body = self._body()
        if re.match(r"^/api/babies/\d+/tummytime$", path):
            nid = max([r["id"] for r in STATE["tummytime"]] or [400]) + 1
            STATE["tummytime"].append({
                "id": nid, "baby_id": 1,
                "start_time": body.get("start_time", ""),
                "end_time": body.get("end_time", ""),
                "duration_minutes": int(body.get("duration") or body.get("duration_minutes") or 0),
                "milestone": body.get("milestone", ""), "note": body.get("note", ""),
            })
            return self._json({"success": True, "message": "已记录"})
        # ⚠️ 换尿布的 POST 必须**真的建记录**。早先这里直接返回 success，
        #    于是「点皮肤标注后列表条数不变」那条断言在旧 bug（POST 建记录）下
        #    也会通过 —— 假后端把缺陷藏住了。负向对照时才发现，已补。
        if re.match(r"^/api/babies/\d+/diaper$", path):
            nid = max([r["id"] for r in STATE["diaper"]] or [300]) + 1
            STATE["diaper"].append({
                "id": nid, "baby_id": 1,
                "change_time": body.get("change_time") or (D_TODAY + " 23:00:00"),
                "diaper_type": body.get("diaper_type") or "wet",
                "color": None, "skin_condition": body.get("skin_condition") or None,
                "brand": None, "note": "",
            })
            return self._json({"success": True, "message": "已添加"})
        # 出牙：必须真的入库并把带 id 的记录回给前端 —— 前端拿这个 id 去删。
        if re.match(r"^/api/babies/\d+/teething$", path):
            nid = max([r["id"] for r in STATE["teething"]] or [500]) + 1
            row = {"id": nid, "baby_id": 1,
                   "tooth_code": body.get("tooth_code", ""),
                   "tooth_name": body.get("tooth_name", ""),
                   "erupt_date": body.get("erupt_date", ""),
                   "position": "", "side": "", "note": body.get("note", "")}
            STATE["teething"].append(row)
            return self._json({"success": True, "message": "出牙记录已添加", "data": row})
        return self._json({"success": True})

    def do_DELETE(self):
        u = urlparse(self.path)
        path = u.path
        if path.startswith(APP_PREFIX):
            path = "/" + path[len(APP_PREFIX):]
        if path.startswith("/api/"):
            m = re.match(r"^/api/teething/(\d+)$", path)
            if m:
                rid = int(m.group(1))
                for r in STATE["teething"]:
                    if r["id"] == rid:
                        STATE["teething"].remove(r)
                        return self._json({"success": True, "message": "已删除"})
                # 复刻真实后端：删不存在的 id 必须 404（假成功会让「删除没生效」测不出来）
                return self._json({"success": False, "message": "记录不存在"}, 404)
            return self._json({"success": True})
        self.send_error(404)

    def do_PUT(self):
        u = urlparse(self.path)
        path = u.path
        if path.startswith(APP_PREFIX):
            path = "/" + path[len(APP_PREFIX):]
        body = self._body()
        m = re.match(r"^/api/babies/\d+/diaper/(\d+)$", path)
        if m:
            rid = int(m.group(1))
            for r in STATE["diaper"]:
                if r["id"] == rid:
                    r.update({
                        "change_time": body.get("change_time", r["change_time"]),
                        "diaper_type": body.get("diaper_type", r["diaper_type"]),
                        "color": body.get("color") or None,
                        "skin_condition": body.get("skin_condition") or None,
                        "brand": body.get("brand") or None,
                        "note": body.get("note", ""),
                    })
                    return self._json({"success": True, "message": "已更新"})
            return self._json({"success": False, "message": "记录不存在"}, 404)
        m = re.match(r"^/api/babies/\d+/tummytime/(\d+)$", path)
        if m:
            rid = int(m.group(1))
            for r in STATE["tummytime"]:
                if r["id"] == rid:
                    # ⚠️ 必须复刻真实后端 _parse_tummytime_payload 的行为：
                    #    收不到 end_time 就置空。这样前端漏回传 end_time 时本测试才会真的红，
                    #    否则一个「什么都能过」的假后端会把数据丢失这个缺陷藏住。
                    r["start_time"] = body.get("start_time", r["start_time"])
                    r["end_time"] = body.get("end_time") or None
                    r["duration_minutes"] = int(body.get("duration")
                                                or body.get("duration_minutes") or 0)
                    r["milestone"] = body.get("milestone", "")
                    r["note"] = body.get("note", "")
                    return self._json({"success": True})
            return self._json({"success": False, "message": "记录不存在"}, 404)
        return self._json({"success": True})

    def log_message(self, *a):
        pass


PROBE = """<script>
setTimeout(async function(){
  var log = [];
  function wait(ms){ return new Promise(function(r){ setTimeout(r, ms); }); }
  function txt(id){ var e = document.getElementById(id); return e ? e.textContent : 'NULL'; }
  function items(sel){ return document.querySelectorAll(sel).length; }
  function toastText(){ var t = document.getElementById('toast'); return t ? t.textContent : 'NULL'; }

  try {
    // ==================== 换尿布 ====================
    switchPage('diaper-record');
    await wait(800);
    log.push('diaperItems=' + items('#diaperList .care-item'));
    log.push('diaperToday=' + txt('diaperTodayCount'));
    log.push('diaperWet=' + txt('diaperTodayWet'));
    log.push('lastTime=' + txt('diaperLastTime'));
    log.push('rangeTabs=' + document.querySelectorAll('#diaperRangeTabs .tab-btn').length);
    log.push('skinBtns=' + Array.prototype.map.call(
      document.querySelectorAll('#skinConditionBar .skin-btn'),
      function(b){ return b.textContent.trim(); }).join('/'));
    log.push('loadMoreVisible=' + (getComputedStyle(document.getElementById('diaperLoadMoreWrap')).display !== 'none'));

    // 点「重度红臀」——关键：列表条数不能变（以前会凭空多一条小便）
    var before = items('#diaperList .care-item');
    var severe = document.querySelector('#skinConditionBar [data-skin="severe_rash"]');
    log.push('severeBtnFound=' + (!!severe));
    if (severe) severe.dispatchEvent(new MouseEvent('click', {bubbles:true, cancelable:true, view:window}));
    await wait(900);
    log.push('itemsAfterSkin=' + items('#diaperList .care-item'));
    log.push('itemsBeforeSkin=' + before);
    log.push('skinToast=' + toastText());
    log.push('skinBadgeShown=' + items('#diaperList .care-skin'));
    log.push('todayAfterSkin=' + txt('diaperTodayCount'));

    // ⚠️ 判「有没有偷偷建记录」不能只看列表条数：列表一页只放 3 条，
    //    多出来的那条会被挤到第二页，第一屏看着一模一样 ⇒ 怎么改都通过。
    //    这里读后端返回的 total（不受分页影响）—— 注意别去数 data.length，
    //    那个同样被页大小夹住（我就踩过一次）。
    var totalAfterSkin = 0;
    await api('/api/babies/1/diaper?limit=200').then(function(r){ totalAfterSkin = r.total; });
    log.push('diaperTotalAfterSkin=' + totalAfterSkin);

    // 切到「全部」：应能看到窗口外的旧记录
    var allTab = document.querySelector('#diaperRangeTabs [data-range="0"]');
    if (allTab) allTab.dispatchEvent(new MouseEvent('click', {bubbles:true, cancelable:true, view:window}));
    await wait(800);
    log.push('itemsAll=' + items('#diaperList .care-item'));
    log.push('loadMoreVisibleAll=' + (getComputedStyle(document.getElementById('diaperLoadMoreWrap')).display !== 'none'));

    // 加载更多
    document.getElementById('diaperLoadMoreBtn').dispatchEvent(
      new MouseEvent('click', {bubbles:true, cancelable:true, view:window}));
    await wait(800);
    log.push('itemsAfterMore=' + items('#diaperList .care-item'));
    log.push('loadMoreHiddenAfter=' + (getComputedStyle(document.getElementById('diaperLoadMoreWrap')).display === 'none'));

    // ==================== 趴睡训练 ====================
    switchPage('tummytime');
    await wait(900);
    log.push('tummyTodayMin=' + txt('statTodayMin'));
    log.push('infoCard=' + (document.querySelector('.tummytime-info-card')
      ? document.querySelector('.tummytime-info-card').textContent.replace(/\\s+/g, ' ').slice(0, 120) : 'NULL'));
    log.push('hasDisclaimer=' + !!document.querySelector('.tummytime-info-note'));

    // 计时 45 秒后停止：今日分钟应从 3 变成 4（旧实现会存 0 分钟 → 还是 3）
    tummyTimerSeconds = 45;
    tummyTimerRunning = false;
    stopTummyTimer();
    await wait(1000);
    log.push('tummyTodayMinAfter45s=' + txt('statTodayMin'));

    // 离开页面应自动暂停
    tummyTimerRunning = true;
    tummyTimerSeconds = 12;
    switchPage('dashboard');
    await wait(500);
    log.push('timerRunningAfterLeave=' + tummyTimerRunning);
    log.push('leaveToast=' + toastText());

    // ==================== 记录可编辑 + 列表打磨 ====================
    switchPage('tummytime');
    await wait(800);
    log.push('tummyCards=' + items('#tummytimeList .tummytime-item-card'));
    log.push('tummyEditBtns=' + items('#tummytimeList .tummytime-edit'));
    log.push('tummyItemTime=' + (document.querySelector('.tummytime-item-date')
      ? document.querySelector('.tummytime-item-date').textContent : 'NULL'));
    log.push('tummyRangeTabs=' + document.querySelectorAll('#tummytimeRangeTabs .tab-btn').length);
    log.push('tummyMilestoneHasEmoji=' + (document.querySelector('.tummytime-item-milestone')
      ? (document.querySelector('.tummytime-item-milestone').textContent.indexOf('\\u{1F3C6}') >= 0) : 'n/a'));
    log.push('skinDots=' + items('#skinConditionBar .skin-dot'));

    // 点编辑 → 弹窗回填。**指定 id=401 那条**：此时列表里还有前面 45 秒那条新记录，
    // 用「第一个编辑按钮」会点到新记录上，回填值就成了 1，测不到真正的回填。
    var editBtn = Array.prototype.filter.call(
      document.querySelectorAll('#tummytimeList .tummytime-edit'),
      function(b){ return (b.getAttribute('onclick') || '').indexOf('(401)') >= 0; })[0];
    log.push('editBtn401Found=' + (!!editBtn));
    if (editBtn) editBtn.dispatchEvent(new MouseEvent('click', {bubbles:true, cancelable:true, view:window}));
    await wait(800);
    log.push('editModalTitle=' + txt('tummytimeModalTitle'));
    log.push('editDurationValue=' + ((document.getElementById('tummytimeDuration')||{}).value || 'NULL'));
    log.push('editModalShown=' + (document.getElementById('tummytimeModal').className.indexOf('show') >= 0));

    // 改时长后提交 → 应走 PUT 且今日分钟变成新值
    document.getElementById('tummytimeDuration').value = '7';
    submitTummytimeRecord();
    await wait(1000);
    log.push('tummyTodayMinAfterEdit=' + txt('statTodayMin'));
    log.push('editToast=' + toastText());
    // 关键：编辑**不能**把原来的 end_time 抹掉（假后端复刻了「收不到就置空」）
    // ⚠️ 必须**按 id 找 401**：列表按时间倒序，[0] 是前面 45 秒那条新记录，
    //    断言它的话怎么改都通过 —— 这就是「断言打在错对象上」的假绿。
    var r401 = (App.tummytimeRecords || []).filter(function(r){ return r.id === 401; })[0];
    log.push('r401Found=' + (!!r401));
    log.push('endTimeAfterEdit=' + (r401 ? (r401.end_time || 'NULL') : 'NOT_FOUND'));

    // 列表日期档位：切到「全部」
    var tAll = document.querySelector('#tummytimeRangeTabs [data-range="0"]');
    if (tAll) tAll.dispatchEvent(new MouseEvent('click', {bubbles:true, cancelable:true, view:window}));
    await wait(400);
    log.push('tummyItemsAll=' + items('#tummytimeList .tummytime-item-card'));
    // ==================== 出牙记录（牙位图） ====================
    // 这一页曾经**整页失效**：前端调 /api/babies/1/teeth，后端只有 /teething，
    // 症状是「列表永远空白 + 点牙位图静默无反应」——所以断言必须落在行为上：
    // 点一颗牙 → 萌出标记 +1、列表 +1 张卡；再删掉 → 回落。
    // 若 URL 又变回指向不存在的接口，这两条会当场变红。
    switchPage('teeth');
    await wait(1000);
    log.push('toothTotal=' + items('#page-teeth .tooth-item'));
    log.push('eruptedBefore=' + items('#page-teeth .tooth-item.erupted'));
    log.push('teethCardsBefore=' + items('#teethList .teeth-item-card'));

    window.prompt = function(){ return '2026-10-05'; };
    var t51 = document.querySelector('#teethUpperRight .tooth-item[onclick*="51"]');
    log.push('tooth51Found=' + (!!t51));
    if (t51) t51.dispatchEvent(new MouseEvent('click', {bubbles:true, cancelable:true, view:window}));
    await wait(1500);
    log.push('eruptedAfterAdd=' + items('#page-teeth .tooth-item.erupted'));
    log.push('teethCardsAfterAdd=' + items('#teethList .teeth-item-card'));
    log.push('teethAddToast=' + toastText());

    // 删除刚加的那条（列表按萌出日期倒序，第一条就是它）
    window.confirm = function(){ return true; };
    var delBtn = document.querySelector('#teethList .teeth-delete');
    log.push('teethDelBtnFound=' + (!!delBtn));
    if (delBtn) delBtn.dispatchEvent(new MouseEvent('click', {bubbles:true, cancelable:true, view:window}));
    await wait(1500);
    log.push('eruptedAfterDel=' + items('#page-teeth .tooth-item.erupted'));
    log.push('teethCardsAfterDel=' + items('#teethList .teeth-item-card'));
    log.push('teethDelToast=' + toastText());
  } catch (e) {
    log.push('EXCEPTION=' + e.message);
  }
  log.push('jsErrors=' + (window.__errs ? window.__errs.length : 'n/a')
    + (window.__errs && window.__errs.length ? ':' + window.__errs.join('|') : ''));
  var d = document.createElement('div'); d.id='DIAG'; d.textContent = log.join(' ;; ');
  document.body.appendChild(d);
}, 1000);
</script>
</body>"""


def check_probe_syntax():
    m = re.search(r"<script>(.*)</script>", PROBE, re.S)
    if not m:
        check("能抽出探针脚本", False, "PROBE 里没有 <script> 块")
        return
    node = shutil.which("node")
    if not node:
        for cand in (r"C:\Users\X\.workbuddy\binaries\node\versions\22.22.2-5\node.exe",
                     r"C:\Program Files\nodejs\node.exe"):
            if os.path.exists(cand):
                node = cand
                break
    if not node:
        print("提示：没找到 node，跳过探针语法自检")
        return
    tmp = os.path.join(tempfile.gettempdir(), "probe_syntax_check2.js")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(m.group(1))
    try:
        r = subprocess.run([node, "--check", tmp], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=60)
        check("探针脚本语法检查通过（括号没配平会让整段探针静默不跑）",
              r.returncode == 0, (r.stderr or "").strip()[:300])
    finally:
        try:
            os.remove(tmp)
        except OSError:
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
        print("找不到 Edge：%s" % EDGE)
        return 2
    if not free_port(PORT):
        print("端口 %d 被占用" % PORT)
        return 2

    check_probe_syntax()

    with open(os.path.join(FRONTEND, "index.html"), "r", encoding="utf-8") as f:
        html = f.read()
    stamp = str(int(time.time()))
    variant = html.replace('href="/app/babycare-fpk/css/main.css"',
                           'href="/app/babycare-fpk/css/main.css?v=%s"' % stamp)
    variant = variant.replace('src="/app/babycare-fpk/js/app.js"',
                              'src="/app/babycare-fpk/js/app.js?v=%s"' % stamp)
    variant = variant.replace("</head>", """<script>
window.__errs = [];
window.addEventListener('error', function(e){ window.__errs.push(String(e.message)); });
</script>
</head>""", 1)
    variant = variant.replace("</body>", PROBE, 1)

    vp = os.path.join(FRONTEND, "_tmp_record_probe.html")
    with open(vp, "w", encoding="utf-8") as f:
        f.write(variant)

    # 页面用前端写死的 PAGE_SIZE=100 请求，假库按 3 条一页返回，
    # 靠 has_more 驱动「加载更多」按钮的显隐。
    httpd = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    profile = tempfile.mkdtemp(prefix="edge_rec_")
    url = "http://127.0.0.1:%d%s_tmp_record_probe.html" % (PORT, APP_PREFIX)
    try:
        out = subprocess.run(
            [EDGE, "--headless=new", "--disable-gpu", "--no-first-run",
             "--no-default-browser-check", "--hide-scrollbars", "--mute-audio",
             "--user-data-dir=" + profile, "--virtual-time-budget=30000",
             "--window-size=1280,1800", "--dump-dom", url],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
        ).stdout or ""
        m = re.search(r'<div id="DIAG">(.*?)</div>', out, re.S)
        diag = {}
        if not m:
            check("页面跑出 DIAG 探针结果", False,
                  "没抓到 DIAG。前 300 字：%s" % out[:300].replace("\n", " "))
        else:
            check("页面跑出 DIAG 探针结果", True)
            for part in m.group(1).split(";;"):
                if "=" in part:
                    k, v = part.split("=", 1)
                    diag[k.strip()] = v.strip()

        def d(k):
            return diag.get(k, "")

        # ---- 换尿布 ----
        check("换尿布列表渲染出来了", d("diaperItems") == "3", {"items": d("diaperItems")})
        check("今日统计正确（3 次 / 小便 2 次含「都有」）",
              d("diaperToday") == "3" and d("diaperWet") == "2",
              {"today": d("diaperToday"), "wet": d("diaperWet")})
        check("「上次换尿布」有值（单独取全局最近一条，不跟筛选联动）",
              d("lastTime") not in ("", "--", "NULL"), d("lastTime"))
        check("日期档位有 4 个（今日/近3天/近7天/全部）", d("rangeTabs") == "4", d("rangeTabs"))
        check("皮肤选项含「重度红臀」（后端枚举一直支持，UI 之前缺）",
              "重度红臀" in d("skinBtns"), d("skinBtns"))

        check("点皮肤标注后**记录总数不变**（绕开分页直查后端，才不会被页大小挡住）",
              d("diaperTotalAfterSkin") == "5", d("diaperTotalAfterSkin"))
        check("点皮肤标注后当前页条数也不变",
              d("itemsAfterSkin") == d("itemsBeforeSkin"),
              {"before": d("itemsBeforeSkin"), "after": d("itemsAfterSkin")})
        check("点皮肤标注后有「已标注」提示",
              "已标注" in d("skinToast"), d("skinToast"))
        check("标注确实显示在那条记录上", d("skinBadgeShown") != "0", d("skinBadgeShown"))
        check("标注不改变今日次数（没被当成新的一次换尿布）",
              d("todayAfterSkin") == "3", d("todayAfterSkin"))

        check("切「全部」后第一页按页大小显示（3 条）", d("itemsAll") == "3", d("itemsAll"))
        check("第一页没翻完时「加载更多」可见", d("loadMoreVisibleAll") == "true",
              d("loadMoreVisibleAll"))
        check("加载更多后条数增加", d("itemsAfterMore") == "5", d("itemsAfterMore"))
        check("翻完自动隐藏「加载更多」", d("loadMoreHiddenAfter") == "true",
              d("loadMoreHiddenAfter"))

        # 筛选是否生效，用「前端实际发出的请求参数」判定 —— 单页只有 3 条时，
        # DOM 条数区分不出「筛了」和「没筛」（两种情况都是 3）。
        starts = [c.get("start", "") for c in DIAPER_COUNT_CALLS]
        check("默认档位带 start 参数（今日窗口）",
              any(s == D_TODAY for s in starts), {"发出过的 start": starts})
        check("切「全部」后请求不再带 start（等于不限日期）",
              any(c.get("start", "") == "" for c in DIAPER_COUNT_CALLS),
              {"发出过的 start": starts})
        check("翻页请求带了 offset", any(c.get("offset", "0") != "0" for c in DIAPER_COUNT_CALLS),
              {"offset": [c.get("offset") for c in DIAPER_COUNT_CALLS]})

        # ---- 趴睡训练 ----
        check("提示卡不再出现医学断言（扁头综合征等）",
              "扁头综合征" not in d("infoCard") and "大运动发育" not in d("infoCard"),
              d("infoCard")[:80])
        check("提示卡有免责声明", d("hasDisclaimer") == "true", d("hasDisclaimer"))
        check("计时 45 秒后今日时长 +1（不再被记成 0 分钟）",
              d("tummyTodayMin") == "3" and d("tummyTodayMinAfter45s") == "4",
              {"before": d("tummyTodayMin"), "after": d("tummyTodayMinAfter45s")})
        check("离开页面自动暂停计时器",
              d("timerRunningAfterLeave") == "false", d("timerRunningAfterLeave"))
        check("离开页面有「已暂停」提示", "暂停" in d("leaveToast"), d("leaveToast"))

        # ---- 记录可编辑 + 列表打磨 ----
        check("每条趴睡记录都有编辑按钮（以前只能删、不能改）",
              d("tummyEditBtns").isdigit() and d("tummyEditBtns") == d("tummyCards")
              and int(d("tummyEditBtns")) > 0,
              {"edit": d("tummyEditBtns"), "cards": d("tummyCards")})
        check("记录时间用友好格式（今天/昨天/带年份），不是原始时间戳",
              d("tummyItemTime").startswith("今天 ") or d("tummyItemTime").startswith("昨天 "),
              d("tummyItemTime"))
        check("趴睡列表也有日期档位", d("tummyRangeTabs") == "3", d("tummyRangeTabs"))
        check("里程碑行不再带 🏆 emoji", d("tummyMilestoneHasEmoji") == "false",
              d("tummyMilestoneHasEmoji"))
        check("皮肤状况用色点而不是表情（4 个圆点）", d("skinDots") == "4", d("skinDots"))

        check("点编辑打开弹窗且标题变成「编辑」",
              d("editModalShown") == "true" and "编辑" in d("editModalTitle"),
              {"shown": d("editModalShown"), "title": d("editModalTitle")})
        check("编辑时表单回填了原时长", d("editDurationValue") == "3", d("editDurationValue"))
        check("编辑保存后今日时长按新值重算（走了 PUT 而不是新增一条）",
              d("tummyTodayMinAfterEdit") == "8",
              {"实际": d("tummyTodayMinAfterEdit"), "说明": "45秒那条记 1 分钟 + 改成 7 分钟 = 8"})
        check("编辑提示是「已更新」", "已更新" in d("editToast"), d("editToast"))
        check("被编辑的那条记录还在（按 id 找，不是列表第一条）",
              d("r401Found") == "true", d("r401Found"))
        check("编辑不会抹掉原来的 end_time（假后端复刻了「收不到就置空」）",
              d("endTimeAfterEdit") not in ("NULL", "NOT_FOUND", ""), d("endTimeAfterEdit"))
        # ---- 出牙记录（牙位图）----
        check("出牙页牙位图渲染出 20 颗乳牙", d("toothTotal") == "20", d("toothTotal"))
        check("预置出牙记录被高亮（GET /teething 真的走通了）",
              d("eruptedBefore") == "1" and d("teethCardsBefore") == "1",
              {"erupted": d("eruptedBefore"), "cards": d("teethCardsBefore")})
        check("点牙位图新增出牙 → 萌出标记 +1（页面调错接口时这里是 1，不会变成 2）",
              d("eruptedAfterAdd") == "2",
              {"before": d("eruptedBefore"), "after": d("eruptedAfterAdd"),
               "toast": d("teethAddToast")})
        check("新增后列表真的多一张卡", d("teethCardsAfterAdd") == "2", d("teethCardsAfterAdd"))
        check("新增有成功提示（以前是静默失败，连 toast 都没有）",
              "添加" in d("teethAddToast"), d("teethAddToast"))
        check("删除出牙记录 → 萌出标记回落（DELETE 打到 /api/teething/<id>）",
              d("eruptedAfterDel") == "1",
              {"after": d("eruptedAfterDel"), "toast": d("teethDelToast")})
        check("删除后列表回到 1 张卡", d("teethCardsAfterDel") == "1", d("teethCardsAfterDel"))

        check("JS 无运行时报错", d("jsErrors") == "0", d("jsErrors"))
    finally:
        httpd.shutdown()
        try:
            os.remove(vp)
        except OSError:
            pass
        shutil.rmtree(profile, ignore_errors=True)

    print("\n" + "=" * 60)
    print("通过 %d 项，失败 %d 项" % (len(PASSED), len(FAILED)))
    if FAILED:
        print("失败明细：")
        for n in FAILED:
            print("  - " + n)
    print("=" * 60)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
