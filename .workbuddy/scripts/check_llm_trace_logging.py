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
import json
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

    # ③ 底层 HTTP 三个失败分支 —— 判据点：共用 helper（2026-10-10 起）
    # 背景：这段错误处理原先在 _call_openai_compat / _call_ollama /
    # _round_openai_compat / _round_ollama 里各写一遍（4 份）。只给其中两份
    # 补东西就酿成过「工具分支不记原因 ⇒ 界面只回笼统调用失败」的故障。
    # 现在收敛成 ai_engine._post_chat_json 一份 —— **判据强度不变，判据点移到这里**，
    # 并额外钉死「四条路径都必须经由它」，否则又会出现没留痕的分支。
    _helper = body_of(eng_raw, "def _post_chat_json(")
    _hcode = strip_comments(_helper)
    chk("共用出网 helper 存在（四条路径的唯一出网点）", bool(_helper.strip()))
    chk("helper：发起请求前留日志（带地址与模型）",
        "llm_trace(" in _hcode and "url[:120]" in _hcode)
    chk("helper：HTTPError 分支留日志（含状态码与响应体）",
        "HTTPError" in _hcode and "llm_trace(" in strip_comments(
            _helper.split("HTTPError", 1)[-1][:400])
        and "he.code" in _hcode)
    chk("helper：URLError 分支留日志",
        "URLError" in _hcode and "llm_trace(" in strip_comments(
            _helper.split("URLError", 1)[-1][:400]))
    chk("helper：超时/连接异常分支留日志",
        "TimeoutError" in _hcode and "llm_trace(" in strip_comments(
            _helper.split("TimeoutError", 1)[-1][:400]))
    chk("helper：兜底 Exception 分支也留日志",
        "except Exception as exc" in _hcode and "llm_trace(" in _hcode)
    for fn in ("_call_openai_compat", "_call_ollama"):
        _b = strip_comments(body_of(eng_raw, "def %s(" % fn))
        chk("%s 经由共用 helper 出网（否则这条路没留痕）" % fn,
            "_post_chat_json(" in _b, "没走 helper")

    # ③b 工具调用分支（ai_engine_tooling.py）
    # 背景：带 tools 时走的是另一条路，原先**完全没有 trace**。用户 NAS 日志里那两行
    #   LLM 调用失败: HTTP Error 404: Not Found
    #   LLM 请求失败: HTTP Error 404: Not Found
    # 就是从这出的，既没地址也没状态码含义。
    # 现在它的 HTTP 也走共用 helper，于是「这条路也得留痕」由
    # ①helper 自身的 trace + ②下面的「经由 helper」共同保证。
    tool_raw = src(TOOLING)
    for fn in ("_round_openai_compat", "_round_ollama"):
        raw = body_of(tool_raw, "def %s(" % fn)
        body = strip_comments(raw)
        chk("%s：走 trace_id 参数" % fn,
            "trace_id" in raw.split(") ->")[0] or "trace_id=None" in raw.split("\n", 1)[0])
        chk("%s：经由共用 helper 出网（错误分支的留痕由 helper 保证）" % fn,
            "_post_chat_json(" in body and "trace_id" in body, "没走 helper")
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
        # 🔴 必须先把请求体读掉再回应：Windows 上服务端带着未读数据关闭 socket
        # 会回 RST 而非 FIN，客户端可能连已到达的响应一起作废，报
        # RemoteDisconnected / WinError 10053——探针就永远拿不到 404，
        # 「404 带上状态码与响应体」这条断言时灵时不灵（实测 20 连发 12 次丢响应）。
        # 加上读 body 后 20/20 稳定拿到 404。mock_llm_server 本来就读了，别退回去。
        _n = int(self.headers.get('Content-Length') or 0)
        if _n:
            self.rfile.read(_n)
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

    # ================================================================
    # ⑧ 配置运行时收口（2026-10-05，对照 dsh resolveAdapterOptions）
    #
    # 背景：configure_llm() 被前端 POST 的**裸 dict** 调用，没有任何 schema，
    # 内部 `for k, v in config.items(): if k in LLM` 只是照单收下。
    # 实测能落库并一路进到发给厂商的 payload 里的东西：
    #   temperature="abc" / max_tokens=-5 / timeout="x" / model="乱写的"
    # 而界面一律显示"配置已更新"—— 与 404 那次同型：配置存了，但坏了没人知道。
    #
    # 判据：静态看收口函数与调用点都在；动态拿脏值真打一遍 configure_llm，
    # 断言**落库的是安全值**（不是"接口没报错"——那恒为真，是假绿）。
    # ================================================================
    engine_raw = src(ENGINE)
    engine_nc = strip_comments(engine_raw)

    chk("配置收口：存在 _coerce_llm_config_value 归一函数",
        "def _coerce_llm_config_value(" in engine_nc, "函数不见了")
    chk("配置收口：存在 _resolve_model_choice 模型存在性校验",
        "def _resolve_model_choice(" in engine_nc, "函数不见了")
    chk("配置收口：数值字段有显式区间表",
        "_LLM_INT_RANGES" in engine_nc and "_LLM_FLOAT_RANGES" in engine_nc,
        "区间表不见了 ⇒ 上界可能被改成无限制")
    # ⚠ 下面三条判**原始文本**、不走 strip_comments：
    #   这几行行尾带中文注释（'timeout': (1, 600),   # 上限 600s：…），
    #   strip_comments 会把整行剥掉 ⇒ 用 nc 判会**恒红**，那是假红不是真问题。
    #   （教训与 MEMORY 那条一致：断言要匹配"真实的代码形态"，
    #     而不是假设源码长得比自己写的整洁。）
    chk("配置收口：timeout 上界被钉住（600s）",
        "'timeout': (1, 600)" in engine_raw, "timeout 上界被改了")
    chk("配置收口：temperature 上界被钉住（2.0）",
        "'temperature': (0.0, 2.0)" in engine_raw, "temperature 区间被改了")
    chk("配置收口：bool 被显式排除（Python 里 True==1）",
        "isinstance(value, bool)" in engine_nc, "bool 会被当成数字收口")
    chk("配置收口：NaN/Inf 被挡住（否则 NaN 会被发给厂商）",
        "NaN/Inf" in engine_raw or "isfinite" in engine_nc, "NaN 漏过去了")
    chk("配置收口：configure_llm 调用了数值归一",
        "_coerce_llm_config_value(k, v)" in engine_nc,
        "configure_llm 没调用归一函数 ⇒ 脏值仍会原样落库")
    chk("配置收口：configure_llm 调用了模型存在性校验",
        "_resolve_model_choice(" in engine_nc, "模型名存在性校验没接上")
    chk("配置收口：改过的项要留日志（否则用户只看到'配置已更新'）",
        "LLM 配置收口" in engine_raw and "logger.warning" in engine_nc,
        "收口没有日志 ⇒ 又变成静默归一")

    # ---- ⑧b 动态：拿脏值真打 configure_llm，断言落库值 ----
    probe3 = r'''
import io, json, os, sys, tempfile, types
fc = types.ModuleType('fcntl')
for n in ('LOCK_EX','LOCK_SH','LOCK_NB','LOCK_UN'): setattr(fc, n, 0)
fc.flock = lambda *a, **k: None
fc.lockf = lambda *a, **k: None
sys.modules['fcntl'] = fc
T = tempfile.mkdtemp()
os.environ.update(BABYCARE_DATA_DIR=T, BABYCARE_DB_PATH=os.path.join(T,'b.db'),
                  BABYCARE_LOG_DIR=os.path.join(T,'logs'), BABYCARE_DEV_AUTH='1')
sys.path.insert(0, %r)
import ai_engine as E
out = {}

# A 越界 / 负数
E.configure_llm({'provider':'deepseek','model':'deepseek-flash',
                 'temperature':99, 'max_tokens':-5, 'timeout':99999})
out['A'] = {k: E.LLM.get(k) for k in ('temperature','max_tokens','timeout')}

# B 脏类型
E.configure_llm({'provider':'deepseek','model':'deepseek-flash',
                 'temperature':'abc', 'timeout':'x'})
out['B'] = {k: E.LLM.get(k) for k in ('temperature','timeout')}

# C 模型名不存在 -> 回落默认
E.configure_llm({'provider':'deepseek','model':'no-such-model-xyz'})
out['C'] = E.LLM.get('model')

# D 开了视觉但该厂商没有视觉模型 -> 自动关掉
E.configure_llm({'provider':'deepseek','model':'deepseek-flash',
                 'vision_enabled':True, 'vision_model':'fake-vision-x'})
out['D'] = {k: E.LLM.get(k) for k in ('vision_enabled','vision_model')}

# E 合法值必须**原样保留**（收口不能越权改正常配置）
E.configure_llm({'provider':'deepseek','model':'deepseek-v4-pro',
                 'temperature':0.35, 'max_tokens':2048, 'timeout':45})
out['E'] = {k: E.LLM.get(k) for k in ('temperature','max_tokens','timeout','model')}

# F 用户自填模型必须被接受（收口不能把功能关了）
E.LLM['custom_models'] = {}
E.configure_llm({'provider':'deepseek','model':'deepseek-flash',
                 'custom_models':['my-own-model-x']})
''' % BACKEND

    probe3 += (
        "out['F'] = E.get_provider_templates()['deepseek']['models']\n"
        "print('GATEJSON' + json.dumps(out, ensure_ascii=False, default=str))\n"
    )
    env = dict(os.environ)
    env.update({"BABYCARE_DEV_AUTH": "1", "PYTHONIOENCODING": "utf-8"})
    p3 = subprocess.run([sys.executable, "-c", probe3], capture_output=True, text=True,
                        encoding="utf-8", errors="replace", env=env, timeout=200)
    o3 = p3.stdout or ""
    res3 = {}
    if "GATEJSON" in o3:
        # ⚠ 不能用 split("\n",1)[0] 取 JSON：探针 stdout 后面常还粘着别的输出
        #   （logging 的 WARNING 会走 stderr，但某些库会写 stdout），
        #   切第一行会把 JSON 拦腰截断 ⇒ 明明跑通了却判"探针没跑通"（假红）。
        #   用 raw_decode：它按 JSON 自身的结束位置吃掉，尾巴爱带多少带多少。
        tail = o3.split("GATEJSON", 1)[1].lstrip()
        dec = json.JSONDecoder()
        try:
            res3, _end = dec.raw_decode(tail)
        except Exception as _e:
            res3 = {}
            chk("配置收口：动态探针输出可解析", False,
                "JSON 解析失败(%s)；输出=%r" % (_e, tail[:300]))
    if res3 and "配置收口：动态探针输出可解析" not in [n for n in FAILED]:
        chk("配置收口：动态探针输出可解析", True)
    if not res3:
        chk("配置收口：动态探针能跑通", False, "输出=%r / 错误=%r" % (o3[-300:], (p3.stderr or "")[-300:]))
    else:
        chk("配置收口：动态探针能跑通", True)
        a = res3.get("A", {})
        chk("配置收口：temperature 越界被钳到上界",
            isinstance(a.get("temperature"), (int, float)) and a["temperature"] <= 2.0,
            "落库=%r" % (a.get("temperature"),))
        chk("配置收口：max_tokens 负数被归零",
            a.get("max_tokens") == 0, "落库=%r" % (a.get("max_tokens"),))
        chk("配置收口：timeout 越界被钳到 600",
            a.get("timeout") == 600, "落库=%r" % (a.get("timeout"),))
        b = res3.get("B", {})
        chk("配置收口：temperature='abc' 回落成数字",
            isinstance(b.get("temperature"), (int, float)), "落库=%r" % (b.get("temperature"),))
        chk("配置收口：timeout='x' 回落成数字",
            isinstance(b.get("timeout"), int) and not isinstance(b.get("timeout"), bool),
            "落库=%r" % (b.get("timeout"),))
        chk("配置收口：非法模型名回落成默认模型",
            res3.get("C") == "deepseek-flash", "落库=%r" % (res3.get("C"),))
        d = res3.get("D", {})
        chk("配置收口：厂商无视觉模型时自动关掉 vision_enabled",
            d.get("vision_enabled") is False and not (d.get("vision_model") or ""),
            "落库=%r" % (d,))
        e = res3.get("E", {})
        chk("配置收口：合法值**原样保留**（收口不许越权改正常配置）",
            e.get("temperature") == 0.35 and e.get("max_tokens") == 2048
            and e.get("timeout") == 45 and e.get("model") == "deepseek-v4-pro",
            "落库=%r" % (e,))
        f = res3.get("F") or []
        chk("配置收口：用户自填模型仍出现在清单里（收口没把功能关掉）",
            "my-own-model-x" in f, "清单=%r" % (f,))

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
