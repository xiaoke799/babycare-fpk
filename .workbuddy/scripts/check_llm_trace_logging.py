#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI 调用链路日志门禁：确认"每一跳都能被日志定位"这件事没被改回去。

背景（2026-10-05）：用户症状「配置好了却一直走规则引擎」，但当时链路上有 7 处
**静默** return False —— 失败只在日志里留一行、甚至什么都不留，谁也没法从现场
判断断在哪一步。加了带 trace id 的逐跳日志后，一次失败能直接看出断点。
本门禁锁的就是"这条链路还留着可定位的日志"。

判据（任一不满足即判红）：
  ① ai_engine 里有 trace 基础设施：_new_trace_id / llm_trace
  ② 前置门槛（provider 空、未同意外发）**都**留了日志（历史静默点）
  ③ 底层 HTTP 的失败分支都留了日志：HTTPError / URLError / 超时
  ④ ai_client 的重试层拦截（provider 空 / 未同意）留了日志
  ⑤ trace id 能跨重试复用（call_llm 接受 trace_id 参数；ai_client 往下传）
  ⑥ 蓝图在结论处输出汇总行（「本次问答结论」）
  ⑦ 动态验证：真造一次失败，日志里必须出现该 trace 的多跳记录

跑法：
  C:\\Users\\X\\.workbuddy\\binaries\\python\\envs\\babycare\\Scripts\\python.exe \\
      .workbuddy/scripts/check_llm_trace_logging.py
退出码：0 = 完好；1 = 有静默点被改回。
"""
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
BACKEND = os.path.join(ROOT, "app", "backend")

ENGINE = os.path.join(BACKEND, "ai_engine.py")
CLIENT = os.path.join(BACKEND, "ai_client.py")
TOOLING = os.path.join(BACKEND, "ai_engine_tooling.py")
BLUEPRINT = os.path.join(BACKEND, "blueprints", "ai.py")

PASSED, FAILED = [], []


def chk(name, cond, detail=None):
    (PASSED if cond else FAILED).append(name)
    print(("  [ok]   " if cond else "  [FAIL] ") + name +
          ("" if cond else "   <- %s" % (detail,)))


def src(p):
    return io.open(p, "r", encoding="utf-8").read()


def strip_comments(code: str) -> str:
    """剥掉注释与字符串字面量里的说明文字，只留"真代码"。

    ⚠ 不剥的话会假绿：把某行的日志删掉后，同一个词往往还留在**注释/docstring**里
    （本门禁第一次写就踩了：删掉 _trace 调用后 "provider 为空" 仍能从
    docstring 里匹配到 ⇒ 负向对照不转红）。断言必须打在真代码上。
    做法：去掉三引号块 + 井号注释，并把字符串字面量压成空串占位，
    这样「函数里调没调用 _trace」才是真的可判。
    """
    out = []
    i = 0
    n = len(code)
    while i < n:
        ch = code[i]
        # 三引号块
        if code.startswith('"""', i) or code.startswith("'''", i):
            q = code[i:i + 3]
            j = code.find(q, i + 3)
            j = n if j < 0 else j + 3
            out.append(" " * (j - i))
            i = j
            continue
        # 井号注释
        if ch == "#":
            j = code.find("\n", i)
            j = n if j < 0 else j
            out.append(" " * (j - i))
            i = j
            continue
        # 字符串字面量（单行）→ 压成 ''
        if ch in ("'", '"'):
            j = i + 1
            while j < n:
                if code[j] == "\\":
                    j += 2
                    continue
                if code[j] == ch:
                    break
                if code[j] == "\n":
                    break
                j += 1
            out.append("''")
            i = min(j + 1, n)
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def body_of(module_src: str, func_def_prefix: str) -> str:
    """取某函数的函数体（用**原始**源码切，切完再剥注释）。"""
    if func_def_prefix not in module_src:
        return ""
    part = module_src.split(func_def_prefix, 1)[1]
    part = part.split("\ndef ", 1)[0]
    return part


def branch_of(func_body: str, guard: str, stops) -> str:
    """取「某个 if 守卫」**它自己那一支**的代码片段。

    ⚠ 别写成 `body.split(guard, 1)[-1][:400]` —— 那会**吃掉后面的兄弟分支**，
    于是兄弟分支里的日志会被算到本分支头上 ⇒ 负向对照不转红（本门禁踩过两次：
    删掉 provider 空分支的 _trace 后，切片越界落到 consent 分支的 _trace 上，
    仍然 31/31 全绿）。所以必须显式给「本分支结束」的记号，在记号处截断；
    找不到记号就返回空串 —— 宁可判红，也不放过。
    """
    if not guard or guard not in func_body:
        return ""
    tail = func_body.split(guard, 1)[1]
    cuts = [tail.find(s) for s in stops]
    cuts = [c for c in cuts if c >= 0]
    return tail[:min(cuts)] if cuts else ""


def main():
    print("=== AI 调用链路日志门禁 ===")
    eng_raw, cli_raw, bp_raw = src(ENGINE), src(CLIENT), src(BLUEPRINT)
    # 断言一律打在"剥掉注释后的真代码"上，否则删了日志、注释还在 ⇒ 假绿
    eng, cli, bp = strip_comments(eng_raw), strip_comments(cli_raw), strip_comments(bp_raw)

    # ① trace 基础设施
    chk("ai_engine 有 _new_trace_id()", "def _new_trace_id" in eng)
    chk("ai_engine 有 llm_trace()", "def llm_trace" in eng)
    _it = body_of(eng_raw, "def llm_trace(")
    chk("trace 内容过脱敏（_sanitize_llm_error）",
        "_sanitize_llm_error" in strip_comments(_it))

    # ② call_llm 的前置门槛留日志（判真代码里有没有 llm_trace 调用）
    call_body = body_of(eng_raw, "def call_llm(")
    call_code = strip_comments(call_body)
    chk("call_llm：provider 为空时留日志",
        call_code.count("llm_trace(") >= 1 and "return False, None" in call_code)
    # 该分支的具体日志：切片必须**只落在本分支内**（遇到下一个门槛就截断）
    seg = branch_of(call_body, "if not LLM['provider']:", ("if LLM.get('provider')",))
    chk("call_llm：provider 为空那一支确实写了日志",
        "llm_trace(" in strip_comments(seg), "切片=%r" % seg[:80])
    seg2 = branch_of(call_body, "if LLM.get('provider') not in", ("try:",))
    chk("call_llm：未同意外发那一支确实写了日志",
        "llm_trace(" in strip_comments(seg2), "切片=%r" % seg2[:80])

    # ③ 底层 HTTP 三个失败分支
    for fn in ("_call_openai_compat", "_call_ollama"):
        raw = body_of(eng_raw, "def %s(" % fn)
        body = strip_comments(raw)
        chk("%s：HTTPError 分支留日志" % fn,
            "HTTPError" in body and "llm_trace(" in strip_comments(
                raw.split("HTTPError", 1)[-1][:400]))
        chk("%s：URLError 分支留日志" % fn,
            "URLError" in body and "llm_trace(" in strip_comments(
                raw.split("URLError", 1)[-1][:400]))
        chk("%s：超时/连接异常分支留日志" % fn,
            "TimeoutError" in body and "llm_trace(" in strip_comments(
                raw.split("TimeoutError", 1)[-1][:400]))
        chk("%s：发起请求前留日志（带地址与模型）" % fn,
            "llm_trace(" in body and "url[:120]" in body)

    # ③b 工具调用分支（ai_engine_tooling.py）——2026-10-05 补
    # 背景：带 tools 时走的是另一条路，原先**完全没有 trace**。用户 NAS 日志里那两行
    #   LLM 调用失败: HTTP Error 404: Not Found
    #   LLM 请求失败: HTTP Error 404: Not Found
    # 就是从这出的，既没地址也没状态码含义。本组锁的就是"这条路也得留痕"。
    tool_raw = src(TOOLING)
    chk("ai_engine_tooling 有 urllib.error 导入（判 HTTPError 靠它）",
        "import urllib.error" in strip_comments(tool_raw))
    for fn in ("_round_openai_compat", "_round_ollama"):
        raw = body_of(tool_raw, "def %s(" % fn)
        body = strip_comments(raw)
        chk("%s：走 trace_id 参数" % fn,
            "trace_id" in raw.split(") ->")[0] or "trace_id=None" in raw.split("\n", 1)[0])
        chk("%s：HTTPError 分支留日志（含状态码与响应体）" % fn,
            "HTTPError" in body and "llm_trace(" in strip_comments(
                raw.split("except urllib.error.HTTPError", 1)[-1][:500]))
        chk("%s：发起请求前留日志（带地址）" % fn,
            "llm_trace(" in body and "url[:120]" in body)
    _ctl = body_of(tool_raw, "def call_llm_with_tools(")
    chk("工具分支入口留日志（工具数/模型/轮次）",
        "llm_trace(" in strip_comments(_ctl))
    chk("工具分支 provider 为空那一支留日志",
        "llm_trace(" in strip_comments(branch_of(_ctl, "if not provider:", ("for _round in",))))
    chk("工具分支失败那支留日志（说明不再重试）",
        "llm_trace(" in strip_comments(branch_of(_ctl, "if not ok:", ("if tool_calls:",))))
    chk("ai_client 把 trace_id 传进了 call_llm_with_tools",
        re.search(r"call_llm_with_tools\([^)]*trace_id\s*=\s*trace_id", cli_raw, re.S) is not None)

    # ④ ai_client 重试层拦截（判真代码，不看注释）
    chk("ai_client 有 _trace() 助手", "def _trace(" in cli)
    retry_raw = body_of(cli_raw, "def _call_llm_with_retry(")
    retry_code = strip_comments(retry_raw)
    # 切片同样只落在本分支内（兄弟分支的 _trace 不算数）
    seg_p = branch_of(retry_raw, "if not provider:", ("if provider not in",))
    chk("重试层：provider 为空那一支确实写了日志",
        "_trace(" in strip_comments(seg_p), "切片=%r" % seg_p[:80])
    seg_c = branch_of(retry_raw, "if provider not in", ("_trace('③'", "ok, answer ="))
    chk("重试层：未同意外发那一支确实写了日志",
        "_trace(" in strip_comments(seg_c), "切片=%r" % seg_c[:80])
    chk("ai_client 缓存命中留日志", "_trace(" in strip_comments(
        body_of(cli_raw, "def call_llm_with_resilience(").split("cached is not None", 1)[-1][:300]))
    chk("ai_client 熔断跳过留日志", "_trace(" in strip_comments(
        body_of(cli_raw, "def call_llm_with_resilience(").split("can_execute()", 1)[-1][:300]))

    # ⑤ trace id 跨重试复用
    chk("call_llm 接受 trace_id 参数", "def call_llm(" in eng and "trace_id" in
        body_of(eng_raw, "def call_llm(").split("\n", 1)[0])
    chk("韧性层入口接受 trace_id",
        "trace_id" in body_of(cli_raw, "def call_llm_with_resilience(").split('"""')[0]
        or "trace_id" in body_of(cli_raw, "def call_llm_with_resilience(").split(") ->")[0])
    chk("重试函数接受并下传 trace_id",
        "trace_id" in body_of(cli_raw, "def _call_llm_with_retry(").split(") ->")[0]
        and "trace_id=trace_id" in cli)

    # ⑥ 蓝图结论行
    # ⚠ 这里要判的是"真代码里调了 llm_trace"，不能只判字符串（字符串剥掉后没了）。
    #   已知结论行/同步日志都写在函数体内，取该函数体判 llm_trace( 调用即可。
    bp_ai_chat = body_of(bp_raw, "def ai_chat(")
    chk("蓝图 ai_chat 里调用了 llm_trace（结论行）",
        "llm_trace(" in strip_comments(bp_ai_chat))
    chk("蓝图结论行文案存在（原始源码）", "本次问答结论" in bp_raw)
    chk("配置同步处调用了 llm_trace",
        "llm_trace(" in strip_comments(body_of(bp_raw, "def _ensure_llm_applied(")))

    # ⑦ 动态：真造一次失败，看日志
    print()
    print("  —— 动态验证（造一次真实失败，检查日志多跳留痕）——")
    import subprocess
    import tempfile
    probe = r'''
import io, logging, os, sys, tempfile, types
sys.path.insert(0, %r)
T = tempfile.mkdtemp(prefix="bc_tracegate_")
os.environ.update({"BABYCARE_DATA_DIR": T, "BABYCARE_DB_PATH": os.path.join(T,"b.db"),
                   "BABYCARE_LOG_DIR": os.path.join(T,"logs"), "BABYCARE_DEV_AUTH":"1"})
_f = types.ModuleType("fcntl")
for _n in ("LOCK_EX","LOCK_SH","LOCK_NB","LOCK_UN"): setattr(_f,_n,0)
_f.flock=lambda *a,**k:None; _f.lockf=lambda *a,**k:None
sys.modules["fcntl"]=_f
buf = io.StringIO()
h = logging.StreamHandler(buf); h.setLevel(logging.INFO)
logging.getLogger().addHandler(h); logging.getLogger().setLevel(logging.INFO)
import ai_engine, ai_client
from ai_engine import LLM, configure_llm
configure_llm({'provider':'deepseek','api_key':'sk-gate-trace-0001',
               'model':'deepseek-v4-flash','consent_cloud':True,
               'api_url':'http://127.0.0.1:9/v1/chat/completions','timeout':2})
LLM['api_key'] = ai_engine.encrypt_api_key('sk-gate-trace-0001')
LLM['consent_cloud'] = True
ai_client.reset_circuit_breaker()
ai_client.call_llm_with_resilience("门禁探针")
print("LOGSTART")
print(buf.getvalue())
''' % BACKEND
    env = dict(os.environ)
    env.pop("BABYCARE_APP_VERSION", None)
    p = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env, timeout=200)
    out = p.stdout or ""
    log = out.split("LOGSTART", 1)[1] if "LOGSTART" in out else ""
    lines = [ln for ln in log.splitlines() if "[llm#" in ln]

    ids = re.findall(r"\[llm#([0-9a-f]+)\]", log)
    chk("失败调用产生了链路日志", len(lines) >= 3, "只有 %d 行" % len(lines))
    chk("同一趟调用的多跳共用同一个 trace id",
        len(ids) > 0 and max(ids.count(i) for i in set(ids)) >= 3,
        "trace ids=%s" % ids[:6])
    chk("日志能指出发起请求的地址", any("⑤ POST" in ln for ln in lines))
    chk("日志能指出失败定位（超时/网络/HTTP）",
        any(("⑥" in ln) and any(k in ln for k in ("超时", "网络不可达", "HTTP 错误")) for ln in lines))
    chk("日志给出最终失败原因", any("✗ 失败" in ln for ln in lines))
    chk("日志不含明文密钥", "sk-gate-trace-0001" not in log,
        "凭据泄漏进日志了！")

    # ⑦b 动态验证**工具调用分支**（用户 NAS 日志暴露的那条路）
    # 用真起一个返 404 的本地端点，比对"能否从日志看出 404 的地址与响应体"。
    probe2 = r'''
import io, json, logging, os, sys, tempfile, threading, types
import http.server
sys.path.insert(0, %r)
T = tempfile.mkdtemp(prefix="bc_toolgate_")
os.environ.update({"BABYCARE_DATA_DIR": T, "BABYCARE_DB_PATH": os.path.join(T,"b.db"),
                   "BABYCARE_LOG_DIR": os.path.join(T,"logs"), "BABYCARE_DEV_AUTH":"1"})
_f = types.ModuleType("fcntl")
for _n in ("LOCK_EX","LOCK_SH","LOCK_NB","LOCK_UN"): setattr(_f,_n,0)
_f.flock=lambda *a,**k:None; _f.lockf=lambda *a,**k:None
sys.modules["fcntl"]=_f
class H(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        self.send_response(404); self.send_header("Content-Type","application/json")
        self.end_headers()
        self.wfile.write(b'{"error":{"message":"The model `no-such-model` does not exist"}}')
    def log_message(self,*a): pass
srv = http.server.HTTPServer(("127.0.0.1",0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
url = "http://127.0.0.1:%%d/v1/chat/completions" %% srv.server_address[1]
buf = io.StringIO()
h = logging.StreamHandler(buf); h.setLevel(logging.INFO)
logging.getLogger().addHandler(h); logging.getLogger().setLevel(logging.INFO)
import ai_engine, ai_client
from ai_engine import LLM, configure_llm
configure_llm({'provider':'deepseek','api_key':'sk-gate-tool-7777',
               'model':'no-such-model','consent_cloud':True,
               'api_url':url,'timeout':3})
LLM['api_key'] = ai_engine.encrypt_api_key('sk-gate-tool-7777')
LLM['consent_cloud'] = True
ai_client.reset_circuit_breaker()
ai_client.call_llm_with_resilience(
    "门禁工具分支探针",
    tools=[{"type":"function","function":{"name":"probe","parameters":{"type":"object","properties":{}}}}],
    execute_tool_fn=lambda *a, **k: None)
print("LOGSTART")
print(buf.getvalue())
srv.shutdown()
''' % BACKEND
    p2 = subprocess.run([sys.executable, "-c", probe2], capture_output=True, text=True,
                        encoding="utf-8", errors="replace", env=env, timeout=200)
    out2 = p2.stdout or ""
    log2 = out2.split("LOGSTART", 1)[1] if "LOGSTART" in out2 else ""
    l2 = [ln for ln in log2.splitlines() if "[llm#" in ln]
    ids2 = re.findall(r"\[llm#([0-9a-f]+)\]", log2)
    chk("工具分支：产生了链路日志", len(l2) >= 3, "只有 %d 行；输出=%r" % (len(l2), (log2 or out2)[-300:]))
    chk("工具分支：多跳共用同一 trace id",
        len(ids2) > 0 and max(ids2.count(i) for i in set(ids2)) >= 3, "ids=%s" % ids2[:6])
    chk("工具分支：日志点出了请求地址",
        any("工具分支 POST" in ln or "工具分支(Ollama)" in ln for ln in l2),
        "\n".join(l2)[:300])
    chk("工具分支：404 带上了状态码与响应体（本轮修复重点）",
        any(("status=404" in ln and "does not exist" in ln) for ln in l2),
        "\n".join(l2)[:400])
    chk("工具分支：日志不含明文密钥", "sk-gate-tool-7777" not in log2, "凭据泄漏进日志了！")

    print()
    print("=" * 62)
    print("通过 %d 项，失败 %d 项" % (len(PASSED), len(FAILED)))
    if FAILED:
        for n in FAILED:
            print("  - " + n)
    print("=" * 62)
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()
