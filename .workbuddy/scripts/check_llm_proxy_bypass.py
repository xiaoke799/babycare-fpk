#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""🔴 LLM 出网门禁：本机 provider 必须在设了代理的机器上**还能连通**。

背景（2026-10-05 实测）：
  裸 `urllib.request.urlopen()` 的默认 opener 会走 `getproxies()`，也就是
  **HTTP_PROXY / http_proxy** 环境变量。飞牛 NAS 这类 Linux 主机上这道变量
  很常见、而 NO_PROXY 常常是空的 ⇒ 连 `127.0.0.1` 都被送去代理。
  直击本项目：Ollama / LM Studio / 自定义本地网关本来就**只能**配 localhost
  （ai_engine 里两处模型发现都硬白名单 `localhost|127.0.0.1|::1`），
  机器一设代理就全部调不通，症状是「配置看着没问题、就是一直走规则引擎」，
  而且 trace 日志里只有一句无上下文的 URLError，根本看不出是代理干的。

  实测对照（monkeypatch getproxies 还原 Linux 行为）：
    裸 urlopen     打 127.0.0.1 → 本机服务收到 **0**（请求被代理吞了）
    urlopen_bound 打 127.0.0.1 → 本机服务收到 **1**（200）

判据：
  ① 静态：6 个出网点全部走 `utils.urlopen_bound`（漏一处就是漏一个 provider
     分支，而漏的那条恰恰是你最不可能手抖的地方）
  ② 纯函数：`is_local_host()` 的边界值必须判对（172.16 算本地、172.32 不算，
     这一步判错会**同时**误伤和漏放，是最容易写错的地方）
  ③ 动态：起本机假 provider + 不可达代理，断言 `urlopen_bound` 真的能连通
  ④ 反向对照：同一条件下裸 `urlopen` 必须**不通** —— 否则说明第 ③ 条测的是
     空气（代理压根没生效），门禁等于没写

⚠ 跑法必须用项目 venv（utils.py 依赖 flask）：
  C:\\Users\\X\\.workbuddy\\binaries\\python\\envs\\babycare\\Scripts\\python.exe \\
      .workbuddy/scripts/check_llm_proxy_bypass.py
退出码：0 = 全部通过；1 = 有失败。
"""
import io
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
BACKEND = os.path.join(ROOT, "app", "backend")
sys.path.insert(0, BACKEND)

PY = sys.executable
_fails = []
_passes = [0]


def chk(cond, name, detail=""):
    if cond:
        _passes.append(1)
        print("  [ok]   %s" % name)
    else:
        _fails.append(name)
        print("  [FAIL] %s   <- %s" % (name, detail))


# ==================== ① 静态：出网点是否都换了入口 ====================
print("=" * 66)
print("第一段：LLM 出网点是否都走 utils.urlopen_bound")
print("=" * 66)

TARGETS = ["ai_engine.py", "ai_engine_tooling.py"]

# 只统计「真的会发出 LLM 请求」的那几处（模型发现接口也算，它们打的就是本地 provider）
SITES = [
    ("ai_engine.py", "call_llm 的 Ollama 分支"),
    ("ai_engine.py", "call_llm 的 OpenAI 兼容分支"),
    ("ai_engine.py", "Ollama 模型发现 /api/tags"),
    ("ai_engine.py", "LM Studio 模型发现 /v1/models"),
]

total_bare = 0
total_bound = 0
_src_cache = {}
_bare_lines = []
for fname, where in SITES:
    if fname not in _src_cache:
        _src_cache[fname] = io.open(os.path.join(BACKEND, fname), "r", encoding="utf-8").read()
    src = _src_cache[fname]
    bound = len(re.findall(r"urlopen_bound\(", src))
    total_bound += bound
    chk(bound > 0, "%s / %s 已改用 urlopen_bound" % (fname, where), "还是裸 urlopen")

# ---- 工具分支：出网点判定 ----
# 2026-10-10 起，四条路径的 HTTP 往返收敛到 ai_engine._post_chat_json 一份
# （此前 4 份各写一遍，只给其中 2 份补 _set_last_llm_error 就酿成过故障）。
# 【判据强度不变】仍是「工具分支出网必须走 urlopen_bound、本机地址不走代理」，
# 只是判据点从「每个函数各自调」移到「经由共用 helper」。
# ⚠ 位置不对只会告诉你「这里不是判据」，不会告诉你判据坏了 —— 所以这里改判据点，
#   而不是删掉这条断言。
_tol_path = os.path.join(BACKEND, "ai_engine_tooling.py")
if "ai_engine_tooling.py" not in _src_cache:
    _src_cache["ai_engine_tooling.py"] = io.open(_tol_path, "r", encoding="utf-8").read()
_tol_src = _src_cache["ai_engine_tooling.py"]
_eng_src = _src_cache["ai_engine.py"]


def _fn_src(src, name):
    i = src.find("def %s(" % name)
    if i == -1:
        return ""
    j = src.find("\ndef ", i + 1)
    return src[i:j if j != -1 else len(src)]


_helper_src = _fn_src(_eng_src, "_post_chat_json")
# ⚠ 本文件的 chk 是 chk(cond, name, detail) —— 与 check_llm_trace_logging.py 的
#   chk(name, cond) **参数顺序相反**。照那边抄过来会变成「条件位收到一个非空字符串」
#   ⇒ 断言恒真、看着很凶却什么都没测。这里曾真的踩过，靠负向对照才发现。
chk(bool(_helper_src),
    "共用出网 helper _post_chat_json 存在（四条路径的唯一出网点）",
    "找不到，说明出网又被拆开了")
chk("urlopen_bound(" in _helper_src,
    "共用 helper 走 urlopen_bound（本机地址不走代理）",
    "helper 里没用 urlopen_bound")
for _fn in ("_round_ollama", "_round_openai_compat"):
    chk("_post_chat_json(" in _fn_src(_tol_src, _fn),
        "工具分支 %s 经由共用 helper 出网" % _fn,
        "没走共用 helper —— HTTP 与错误处理又会分叉")

# 🔴 计数与明细**必须按文件扫一次**，不能塞在 SITES 循环里 —— SITES 里有
#    4 项都指向 ai_engine.py，逐项累加会把同一行数 4 遍（实测报「8 处」、
#    实际只有 2 处行号，明细里 ai_engine.py:1846 出现 4 次就是这么来的）。
#    这种虚高会让判据看起来很凶、却又说不清哪一行，最后没人敢信它。
for fname in dict.fromkeys(f[0] for f in SITES):
    src = _src_cache[fname]
    bare = len(re.findall(r"urlopen\((?!_bound)", src))
    total_bare += bare
    for i, line in enumerate(src.splitlines(), 1):
        if "urlopen(" in line and "urlopen_bound(" not in line:
            _bare_lines.append("%s:%d" % (fname, i))

# 🔴 修完之后**一处裸 urlopen 都不该有**。这里的反向计数是防「改了名但是
#    换个名字继续裸着」：如果有人把 urlopen_bound 改名/删掉，total_bare 就 > 0。
chk(total_bare == 0, "LLM 出网点已无裸 urlopen（一漏就是漏一个 provider 分支）",
    "还有 %d 处：%s" % (total_bare, ", ".join(_bare_lines) or "（明细为空，看正则是不是写废了）"))
chk(total_bound >= 6, "urlopen_bound 覆盖到全部 6 个出网点（不能少于 6）",
    "实际只有 %d 处" % total_bound)

utils_src = io.open(os.path.join(BACKEND, "utils.py"), encoding="utf-8").read()
chk("_opener_direct" in utils_src, "utils 里有直连 opener（空代理表）")
chk("_opener_default" in utils_src, "utils 里有默认 opener（外部仍走系统代理）")

# ==================== ② 纯函数：is_local_host 边界值 ====================
print()
print("=" * 66)
print("第二段：is_local_host 边界值")
print("=" * 66)

import utils  # noqa: E402

# (host, 期望)
BOUNDARY = [
    ("localhost", True),
    ("127.0.0.1", True),
    ("127.9.9.9", True),          # 整个 127/8 都是回环
    ("[::1]", True),
    ("::1", True),
    ("192.168.1.5", True),        # 家用/NAS 内网
    ("10.0.0.1", True),           # 内网
    ("172.16.0.1", True),         # 私有网段下界
    ("172.31.255.255", True),     # 私有网段上界
    # ↓ 以下一律 False：判成 True 会把外部请求静默改成直连（绕过用户代理）
    ("172.32.0.1", False),        # 刚过上界
    ("172.15.0.1", False),        # 差一点到下界
    ("8.8.8.8", False),
    ("api.deepseek.com", False),
    ("", False),
]
for host, want in BOUNDARY:
    got = utils.is_local_host(host)
    chk(got == want, "is_local_host(%r) == %s" % (host, want), "实际 %s" % got)

chk("no_proxy" in io.open(os.path.join(BACKEND, "utils.py"), encoding="utf-8").read(),
    "NO_PROXY 里显式声明的域名也算本地（用户自己配的例外要认）")

# ==================== ③ 动态 + ④ 反向对照 ====================
print()
print("=" * 66)
print("第三段：真起本机 provider + 不可达代理（子进程）")
print("=" * 66)

PROBE = r'''
# -*- coding: utf-8 -*-
import http.server, json, os, sys, threading, urllib.request
sys.path.insert(0, r"__BACKEND__")
import utils

PORT = int(sys.argv[1])
MODE = sys.argv[2]
UNREACHABLE = "http://127.0.0.1:" + str(PORT + 1)

hits = [0]
class H(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def do_POST(self):
        hits[0] += 1
        b = b'{"choices":[{"message":{"content":"ok"}}]}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)
    def log_message(self, *a): pass

srv = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()

# 🔴 Windows 上 getproxies() 走**注册表**，设环境变量根本读不到 ⇒ 必须 monkeypatch
#    才能还原 Linux NAS 上「HTTP_PROXY 生效」的真实行为。
urllib.request.getproxies = lambda: {"http": UNREACHABLE}

req = urllib.request.Request("http://127.0.0.1:" + str(PORT) + "/v1/chat",
                             data=b'{"model":"x"}',
                             headers={"Content-Type": "application/json"})
hits[0] = 0
try:
    if MODE == "bound":
        utils._opener_direct.cache_clear()
        utils._opener_default.cache_clear()
        with utils.urlopen_bound(req, timeout=8) as r:
            r.read()
            code = r.status
    else:
        with urllib.request.urlopen(req, timeout=8) as r:
            r.read()
            code = r.status
except Exception as e:
    code = "EXC:%s" % type(e).__name__
srv.shutdown(); srv.server_close()
print(json.dumps({"hits": hits[0], "code": str(code)}))
'''

import json  # noqa: E402

PORT = 18310
results = {}
for mode in ("bound", "plain"):
    src = PROBE.replace("__BACKEND__", BACKEND)
    p = os.path.join(HERE, "_probe_proxy_%s.py" % mode)
    io.open(p, "w", encoding="utf-8").write(src)
    try:
        r = subprocess.run([PY, p, str(PORT), mode], capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           cwd=ROOT, timeout=120)
        tail = (r.stdout or "")
        # 🔴 取 JSON 用 raw_decode：stdout 尾部常粘日志，split("\n",1)[0] 会拦腰截断
        dec = json.JSONDecoder()
        payload, _end = dec.raw_decode(tail[tail.index("{"):])
        results[mode] = payload
    except Exception as e:
        results[mode] = {"hits": -1, "code": "PROBE_FAILED:%s" % type(e).__name__}
    finally:
        try:
            os.remove(p)
        except OSError:
            pass

bound = results.get("bound", {})
plain = results.get("plain", {})

chk(bound.get("hits") == 1,
    "🔴 代理开着时，urlopen_bound 仍能打到本机 provider（本地 Ollama 不该被代理吞）",
    {"本机收到": bound.get("hits"), "code": bound.get("code")})
chk(bound.get("code") == "200",
    "urlopen_bound 真的收到了 200（不是靠异常蒙过去的）", bound.get("code"))

# 🔴 反向对照：这条**必须**是不通。它红了说明代理没生效、第③条测的是空气
chk(plain.get("hits") == 0,
    "🔴 反向对照：同样条件下裸 urlopen 打不通（证明代理确实生效、③测的不是空气）",
    {"本机收到": plain.get("hits"), "code": plain.get("code")})

# ==================== 汇总 ====================
print()
print("=" * 66)
print("通过 %d 项，失败 %d 项" % (len(_passes), len(_fails)))
print("=" * 66)
if _fails:
    for n in _fails:
        print("  FAIL  %s" % n)
    print("\n提示：跑这个门禁必须用项目 venv（utils.py 依赖 flask）：")
    print("  C:\\Users\\X\\.workbuddy\\binaries\\python\\envs\\babycare\\Scripts\\python.exe")
    print("      .workbuddy\\scripts\\check_llm_proxy_bypass.py")
    sys.exit(1)
print("全部通过。")
