#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""小萌对话「确认卡」浏览器级端到端门禁（Edge 无头 + 假 API + 注入探针）。

背景（2026-10-17 用户拍板）：
  「AI 帮写」独立页面撤除后，记录唯一入口 = 小萌对话框说一句 → AI 识别
  并弹「要记录这条吗」确认卡 → 点「记下来」才写入。这条路径横跨
  sendMsg / extract-auto / AIW_TYPES.save / 确认卡渲染四段，静态判据
  （verify_ai_writer_ui 的 S11/S12/E2b/E8/E9）证明的是「各段都接着」，
  但「真点一下到底弹不弹卡、点不了会不会写、点记下来写对没写对」只有
  真浏览器能证明。2026-10-17 首跑即抓到「不了」判据本身的时序错位
  （断言把第二轮合法写入算进第一轮），说明这类时序必须由 DOM 是/否
  两条路径合起来判。

覆盖（12 项断言）：
  1. 小萌浮窗能打开、输入框/发送按钮在位
  2. 第一轮「不了」：确认卡出现、有「不了」按钮、点后卡片移除
  3. 「不了」不产生任何写入（两轮合计必须恰好 1 条 POST）
  4. 第二轮「记下来」：卡上有标题/类型/奶量、有「记下来」按钮
  5. 点后出现「已记录」回执
  6. 后端收到的 POST 体正确（feeding_type / amount / start_time）
  7. 页面全程无 JS 报错

依赖：Windows + Edge（同 verify_record_pages 的环境要求）。
跑法（项目 venv）：
  C:\\Users\\X\\.workbuddy\\binaries\\python\\envs\\babycare\\Scripts\\python.exe \\
      .workbuddy/scripts/verify_chat_record_card_e2e.py
退出码：0 全过；1 有失败；2 环境缺失（找不到 Edge）。
"""
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FRONTEND = os.path.join(ROOT, "app", "frontend")
APP_PREFIX = "/app/babycare-fpk/"
PORT_CANDIDATES = (8866, 8867, 8868)
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PROBE_FILE = "_tmp_chat_probe.html"

SAVED = []          # 记录 feeding POST 的 body
EXTRACT_CALLS = []  # extract-auto 收到的 input


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=FRONTEND, **kw)

    def _send(self, obj, code=200):
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

    def _strip(self, path):
        return "/" + path[len(APP_PREFIX):] if path.startswith(APP_PREFIX) else path

    def do_GET(self):
        path = self._strip(urlparse(self.path).path)
        if path.startswith("/api/"):
            if path == "/api/auth/status":
                return self._send({"success": True, "passwordless": True, "authenticated": True})
            if path == "/api/babies":
                return self._send({"success": True, "data": [
                    {"id": 1, "name": "验证宝宝", "age": "1岁3个月", "birthday": "2025-07-01"}]})
            if path == "/api/ai/llm/status":
                return self._send({"success": True, "status": {
                    "provider": "deepseek", "model": "mock-model",
                    "api_url": "http://127.0.0.1/mock",
                    "ai_record_authorized": True, "vision_enabled": False,
                    "system_prompt": "", "consent_cloud": True, "has_api_key": True}})
            if path == "/api/storage/app-info":
                return self._send({"success": True, "version": "0.0.5"})
            if path.endswith("/dashboard"):
                return self._send({"success": True, "data": {
                    "baby_profile": {"name": "验证宝宝", "age": "1岁3个月"},
                    "today_feeding": {"count": 0, "total_amount": 0},
                    "today_sleep": {"total_minutes": 0}, "today_diaper": {"count": 0},
                    "latest_growth": None, "latest_milestones": [], "recent_records": []}})
            return self._send({"success": True, "data": []})
        self.path = path
        super().do_GET()

    def do_POST(self):
        path = self._strip(urlparse(self.path).path)
        body = self._body()
        if path == "/api/ai/chat":
            return self._send({"success": True, "llm_used": True,
                               "answer": "收到，帮你整理这条记录。", "llm_model": "mock-model"})
        if path == "/api/ai/write/extract-auto":
            EXTRACT_CALLS.append(body.get("input", ""))
            return self._send({"success": True, "type": "feeding", "record": {
                "feeding_type": "bottle", "amount": 120,
                "start_time": "2026-10-17 08:00:00", "note": ""},
                "message": "识别到一条记录"})
        if re.match(r"^/api/babies/1/feeding$", path):
            SAVED.append(body)
            return self._send({"success": True, "message": "记录已添加"})
        return self._send({"success": True})

    def log_message(self, *a):
        pass


PROBE = """<script>
window.__errs = [];
window.addEventListener('error', function(e){ window.__errs.push(String(e.message)); });
(async function(){
  var log = [];
  function wait(ms){ return new Promise(function(r){ setTimeout(r, ms); }); }
  try {
    var avatar = document.getElementById('xiaomengAvatar');
    log.push('avatar=' + (avatar ? 1 : 0));
    if (avatar) avatar.click();
    await wait(900);
    var inp = document.getElementById('xiaomengInput');
    var send = document.getElementById('xiaomengSend');
    log.push('input=' + (inp ? 1 : 0));
    log.push('send=' + (send ? 1 : 0));
    if (inp && send) {
      // ---- 第一轮：点「不了」→ 卡移除、不写入 ----
      inp.value = '刚喝了120ml奶粉';
      send.click();
      var card1 = null;
      for (var i = 0; i < 40; i++) { await wait(250); card1 = document.querySelector('.chat-record-card'); if (card1) break; }
      log.push('card1=' + (card1 ? 1 : 0));
      if (card1) {
        var no = card1.querySelector('[data-act="no"]');
        log.push('noBtn=' + (no ? 1 : 0));
        if (no) {
          no.click();
          await wait(400);
          log.push('card1Removed=' + (document.querySelector('.chat-record-card') ? 0 : 1));
        }
      }
      await wait(900);
      // ---- 第二轮：点「记下来」→ 落库 ----
      inp.value = '刚喝了120ml奶粉';
      send.click();
      var card = null;
      for (var k = 0; k < 40; k++) { await wait(250); card = document.querySelector('.chat-record-card'); if (card) break; }
      log.push('card2=' + (card ? 1 : 0));
      if (card) {
        log.push('cardText=' + card.textContent.replace(/\\s+/g, ' ').slice(0, 120));
        var yes = card.querySelector('[data-act="yes"]');
        log.push('yesBtn=' + (yes ? 1 : 0));
        if (yes) {
          yes.click();
          var got = '';
          for (var j = 0; j < 60; j++) { await wait(250); var d = card.querySelector('.chat-record-done'); if (d) { got = d.textContent; break; } }
          log.push('done=' + got.slice(0, 60));
        }
      }
    }
  } catch (e) { log.push('EXC=' + e.message); }
  log.push('jsErrors=' + window.__errs.length + (window.__errs.length ? ':' + window.__errs.join('|').slice(0, 200) : ''));
  var d = document.createElement('div'); d.id = 'DIAG'; d.textContent = log.join(' ;; ');
  document.body.appendChild(d);
})();
</script>"""


def _free_port(p):
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
        print("找不到 Edge：%s（本门禁需要 Windows 本机的 Edge）" % EDGE)
        return 2
    port = next((p for p in PORT_CANDIDATES if _free_port(p)), None)
    if port is None:
        print("端口全被占用：%s" % (PORT_CANDIDATES,))
        return 2
    # 清历史残留（上次进程被杀可能留下）
    stale = os.path.join(FRONTEND, PROBE_FILE)
    if os.path.exists(stale):
        try:
            os.remove(stale)
        except OSError:
            pass

    with open(os.path.join(FRONTEND, "index.html"), "r", encoding="utf-8") as f:
        html = f.read()
    stamp = str(int(time.time()))
    variant = html.replace('href="/app/babycare-fpk/css/main.css"',
                           'href="/app/babycare-fpk/css/main.css?v=%s"' % stamp)
    variant = variant.replace('src="/app/babycare-fpk/js/app.js"',
                              'src="/app/babycare-fpk/js/app.js?v=%s"' % stamp)
    variant = variant.replace("</body>", PROBE + "</body>", 1)
    vp = os.path.join(FRONTEND, PROBE_FILE)
    with open(vp, "w", encoding="utf-8") as f:
        f.write(variant)

    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    profile = tempfile.mkdtemp(prefix="edge_chat_")
    url = "http://127.0.0.1:%d%s%s" % (port, APP_PREFIX, PROBE_FILE)
    out = ""
    try:
        out = subprocess.run(
            [EDGE, "--headless=new", "--disable-gpu", "--no-first-run",
             "--no-default-browser-check", "--hide-scrollbars", "--mute-audio",
             "--user-data-dir=" + profile, "--virtual-time-budget=60000",
             "--window-size=500,900", "--dump-dom", url],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=200,
        ).stdout or ""
    finally:
        httpd.shutdown()
        try:
            os.remove(vp)
        except OSError:
            pass
        shutil.rmtree(profile, ignore_errors=True)

    m = re.search(r'<div id="DIAG">(.*?)</div>', out, re.S)
    if not m:
        print("没抓到 DIAG（页面没跑起来？）。前 500 字：\n" + out[:500])
        return 1
    diag = {}
    for part in m.group(1).split(";;"):
        if "=" in part:
            k, v = part.split("=", 1)
            diag[k.strip()] = v.strip()

    print("=== 探针结果 ===")
    for k in ("avatar", "input", "send", "card1", "noBtn", "card1Removed",
              "card2", "cardText", "yesBtn", "done", "jsErrors", "EXC"):
        if k in diag:
            print("  %-12s = %s" % (k, diag[k]))
    print()
    print("=== 后端收到 ===")
    print("  extract-auto input:", EXTRACT_CALLS)
    print("  feeding POST body:", SAVED)
    print()

    fails = []
    def bad(name, detail=""):
        fails.append(name + ("  <- " + str(detail) if detail else ""))

    if diag.get("avatar") != "1":
        bad("页面里找不到小萌浮窗入口（xiaomengAvatar）")
    if diag.get("input") != "1" or diag.get("send") != "1":
        bad("小萌输入框/发送按钮不可用", (diag.get("input"), diag.get("send")))
    # 第一轮：「不了」
    if diag.get("card1") != "1":
        bad("第一轮确认卡没有出现（说了话却没有可确认的卡）")
    if diag.get("noBtn") != "1":
        bad("确认卡没有「不了」按钮")
    if diag.get("card1Removed") != "1":
        bad("点「不了」后卡片没有移除")
    if len(SAVED) > 1:
        bad("「不了」路径也产生了写入（合计 %d 条，应 1 条）" % len(SAVED), SAVED)
    if len(EXTRACT_CALLS) < 2:
        bad("extract-auto 只收到 %d 次原话（应 2 次）" % len(EXTRACT_CALLS), EXTRACT_CALLS)
    # 第二轮：「记下来」
    if diag.get("card2") != "1":
        bad("第二轮确认卡没有出现")
    if "要记录这条吗" not in diag.get("cardText", ""):
        bad("确认卡标题不对", diag.get("cardText", "")[:80])
    if "喂奶" not in diag.get("cardText", ""):
        bad("确认卡没显示类型「喂奶」", diag.get("cardText", "")[:80])
    if "120" not in diag.get("cardText", ""):
        bad("确认卡没显示奶量 120", diag.get("cardText", "")[:80])
    if diag.get("yesBtn") != "1":
        bad("确认卡没有「记下来」按钮")
    if "已记录" not in diag.get("done", ""):
        bad("点「记下来」后没有「已记录」回执", diag.get("done", ""))
    if len(SAVED) != 1:
        bad("feeding 落库请求应恰好 1 条，实为 %d" % len(SAVED), SAVED)
    else:
        b = SAVED[0]
        if b.get("feeding_type") != "bottle":
            bad("落库 feeding_type 不对", b.get("feeding_type"))
        if b.get("amount") != 120:
            bad("落库 amount 不对", b.get("amount"))
        if b.get("start_time") != "2026-10-17 08:00:00":
            bad("落库 start_time 不对", b.get("start_time"))
    if diag.get("jsErrors") != "0":
        bad("页面有 JS 报错", diag.get("jsErrors"))
    if "EXC" in diag:
        bad("探针执行异常", diag.get("EXC"))

    print("=" * 56)
    if fails:
        print("失败 %d 项：" % len(fails))
        for f in fails:
            print("  - " + f)
        return 1
    print("对话确认卡真机 E2E 通过（12 项断言，含「不了不写入」）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
