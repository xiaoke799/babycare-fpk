#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI 链路端到端门禁 —— 用本地「假厂商」把整条链路真跑一遍。

为什么不用真厂商
----------------
AI 的缺陷（错误分类、超时、空响应、工具循环、max_tokens、缓存/熔断语义）都发生在
「真的发出去一个 HTTP 请求」之后。不外网、不要真 Key 就只能读代码 ——
而 2026-10-10 的故障（工具分支漏记失败原因 ⇒ 界面只回笼统「调用失败」）
恰恰是读代码看不出来的那类。

本门禁起一个本地 OpenAI 兼容服务（.workbuddy/scripts/mock_llm_server.py），
按场景返回正常/401/404/429/500/超时/空响应/工具调用/合法 JSON，
再把两条链路（对话页 + AI 帮写）都真跑一遍。

覆盖：
  A. 两条对话链路（带工具 / 不带工具）+ 工具调用闭环
  B. AI 帮写：成功提取 / 不可解析 / 401 / 超时 —— 失败必须带**真实原因**
  C. 错误分类不串味：401 / 404 / 429 / 500 / 空响应 的原因必须互不相同
  D. 陈旧原因不串台：先 401 再空响应，展示的必须是后者的原因
  E. 写路径确实在韧性层内（连续失败会被熔断短路，说明共用同一个熔断器）
  F. max_tokens 按调用方意图发出（extract 500 / full-diary 2000）
  G. 结构断言：HTTP 错误处理只有一处（4 份重复过，是本次故障的根因类别）

跑法：
  C:\\Users\\X\\.workbuddy\\binaries\\python\\envs\\babycare\\Scripts\\python.exe \\
      .workbuddy/scripts/verify_ai_chain_e2e.py
退出码：0 全过；1 有失败。
"""
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
BACKEND = os.path.join(ROOT, "app", "backend")
sys.path.insert(0, BACKEND)
sys.path.insert(0, HERE)

TMP = tempfile.mkdtemp(prefix="babycare_aichain_")
os.environ.update({
    "BABYCARE_DATA_DIR": TMP,
    "BABYCARE_DB_PATH": os.path.join(TMP, "babycare.db"),
    "BABYCARE_LOG_DIR": os.path.join(TMP, "logs"),
    "BABYCARE_DEV_AUTH": "1",
})

import mock_llm_server as mk      # noqa: E402
import server                     # noqa: E402
import ai_client                  # noqa: E402
import ai_engine                  # noqa: E402

PASS, FAIL = [], []


def ok(msg):
    PASS.append(msg)
    print("  [OK]   " + msg)


def bad(msg, detail=""):
    FAIL.append(msg)
    print("  [FAIL] " + msg + (("  实际 " + str(detail)) if detail is not None else ""))


srv, PORT = mk.start_mock()
URL = f"http://127.0.0.1:{PORT}/v1/chat/completions"

server.init_db()
client = server.app.test_client()
with client.session_transaction() as sess:
    sess["authenticated"] = True
    sess["is_admin"] = True
    sess["user_id"] = 1

r = client.post("/api/babies", json={"name": "AI链路", "birthday": "2026-04-01"})
BID = (r.get_json() or {}).get("data", {}).get("id")

r = client.post("/api/ai/llm/configure", json={
    "provider": "deepseek", "api_key": "sk-mock-1234567890", "model": "deepseek-flash",
    "api_url": URL, "consent_cloud": True, "timeout": 3, "custom_models": [],
})
if not (r.get_json() or {}).get("success"):
    print("配置假厂商失败，无法继续：", r.get_json())
    sys.exit(1)


def sc(mode):
    """切到某个场景：设假厂商模式 + 复位熔断 + 清缓存。

    清缓存是必须的：同一提示词刚成功过一次就会被缓存命中，
    401/500 场景会直接返回上一次的正确答案，把断言全变成假绿。
    """
    mk.set_mode(mode)
    ai_client.reset_circuit_breaker()
    ai_client.clear_ai_cache()


def chat(mode, tools=True, msg="今天该注意什么"):
    sc(mode)
    return client.post("/api/ai/chat", json={"message": msg, "baby_id": BID,
                                             "enable_tools": tools}).get_json() or {}


def write(mode, ep="/api/ai/write/extract-feeding", body=None):
    sc(mode)
    return client.post(ep, json=body or {"input": "今天喂了180ml母乳"}).get_json() or {}


print("=== A. 两条对话链路 + 工具闭环 ===")
r_off = chat(None, tools=False)
ok("不带工具：拿到大模型回答") if r_off.get("llm_used") and (r_off.get("answer") or "").strip() \
    else bad("不带工具：没拿到回答", r_off.get("fallback_reason"))
r_on = chat(None, tools=True)
ok("带工具：拿到大模型回答") if r_on.get("llm_used") and (r_on.get("answer") or "").strip() \
    else bad("带工具：没拿到回答", r_on.get("fallback_reason"))
r_tool = chat("tool-1", tools=True, msg="宝宝多大了")
if r_tool.get("llm_used") and len(r_tool.get("tool_events") or []) >= 1:
    ok("工具调用闭环：最终回答来自模型且记录了工具事件")
else:
    bad("工具调用闭环没跑通", {"used": r_tool.get("llm_used"),
                               "events": len(r_tool.get("tool_events") or [])})

print("\n=== B. AI 帮写：成功 / 不可解析 / 真实故障 ===")
w_ok = write("json-ok")
rec = w_ok.get("record") or {}
if w_ok.get("success") and rec.get("amount") == 180:
    ok("成功提取：字段真的解析出来了（amount=180）")
else:
    bad("成功提取失败", w_ok)

w_bad = write("json-bad")
msg_bad = str(w_bad.get("message") or "")
if "无法识别" in msg_bad and "大模型调用失败" not in msg_bad:
    ok("不可解析：只说『无法识别』，不把故障措辞塞进来（语义没串味）")
else:
    bad("不可解析时的文案不对", msg_bad)

for mode, key in (("err401", "HTTP 401"), ("err500", "HTTP 500")):
    m = str(write(mode).get("message") or "")
    if "大模型调用失败" in m and key in m:
        ok(f"AI 帮写 {mode}：把真实原因带给了用户（含 {key}）")
    else:
        bad(f"AI 帮写 {mode}：没带真实原因", m[:120])

m_to = str(write("timeout").get("message") or "")
if "大模型调用失败" in m_to and ("超时" in m_to or "不可达" in m_to):
    ok("AI 帮写超时：把真实原因带给了用户")
else:
    bad("AI 帮写超时：没带真实原因", m_to[:120])

print("\n=== C. 错误分类不串味 ===")
reasons = {}
for mode in ("err401", "err404", "err429", "err500", "empty"):
    reasons[mode] = str(chat(mode).get("fallback_reason") or "")
uniq = len({v for v in reasons.values()})
if uniq == len(reasons):
    ok(f"5 种故障给出 {uniq} 种互不相同的原因")
else:
    bad("不同故障的原因串味了", reasons)
for mode, key in (("err401", "401"), ("err404", "404"), ("err429", "429")):
    ok(f"{mode} 的原因里带状态码 {key}") if key in reasons[mode] \
        else bad(f"{mode} 的原因里没有状态码", reasons[mode][:90])

print("\n=== D. 陈旧原因不串台 ===")
# 两条路都要测：带工具那条对「200 但正文为空」有显式设置，
# 不带工具那条依赖 _post_chat_json 在每次尝试前清空 —— 少测一条就漏一半。
for _tools in (False, True):
    chat("err401", tools=_tools)                 # 先制造一次 401，把错误变量写上
    d = str(chat("empty", tools=_tools).get("fallback_reason") or "")
    if "可用内容" in d and "401" not in d:
        ok(f"空响应的原因不会被上一次的 401 顶替（tools={_tools}）")
    else:
        bad(f"展示的是上一次的旧原因，用户会照着错误方向查（tools={_tools}）", d[:120])

print("\n=== E. 写路径确实在韧性层内 ===")
# 判据用「熔断打开后还发不发请求」—— 这能直接测出写路径在不在韧性层里：
# 若它在层外，熔断器根本管不到它，请求数会继续涨。
# ⚠ 这里**不能**用 write()（它会 reset 熔断），否则自己把自己的前提清掉。
mk.set_mode("err500")
ai_client.reset_circuit_breaker()
ai_client.clear_ai_cache()
for _ in range(6):
    client.post("/api/ai/write/extract-feeding", json={"input": "今天喂了180ml母乳"})
st = (ai_client.get_ai_client_stats().get("circuit_breaker") or {}).get("state")
n_before = len(mk.seen())
last = client.post("/api/ai/write/extract-feeding", json={"input": "今天喂了180ml母乳"}).get_json() or {}
n_after = len(mk.seen())

if st == "open" and n_after == n_before:
    ok("写路径受熔断约束：熔断打开后不再发出任何请求（说明与对话页共用同一熔断器）")
else:
    bad("写路径绕过了韧性层", {"熔断状态": st, "熔断前请求数": n_before, "之后": n_after})

lm = str(last.get("message") or "")
if "熔断" in lm:
    ok("熔断时写路径如实说『已熔断』，而不是把上一条旧错误当成这次的原因")
else:
    bad("熔断时写了误导性原因（把上一次的错误当成这次）", lm[:130])
ai_client.reset_circuit_breaker()

print("\n=== F. max_tokens 按调用方意图发出 ===")
expected = {"/api/ai/write/extract-feeding": 500, "/api/ai/write/full-diary": 2000}
for ep, want in expected.items():
    sc("json-ok")
    mk.reset()
    body = {"input": "今天喂了180ml母乳"} if "extract" in ep else {"input": "今天很开心", "baby_id": BID}
    client.post(ep, json=body)
    got = [s.get("max_tokens") for s in mk.seen()]
    if got and all(g == want for g in got):
        ok(f"{ep.split('/')[-1]} 发出 max_tokens={want}")
    else:
        bad(f"{ep.split('/')[-1]} 的 max_tokens 不是调用方要的值", {"期望": want, "实际": got})

print("\n=== G. 结构断言：HTTP 错误处理只有一处 ===")
src_engine = open(os.path.join(BACKEND, "ai_engine.py"), encoding="utf-8").read()
src_tooling = open(os.path.join(BACKEND, "ai_engine_tooling.py"), encoding="utf-8").read()
n_http_err = src_engine.count("_set_last_llm_error('HTTP %s: %s' % (he.code")
if n_http_err == 1:
    ok("ai_engine 里 HTTP 错误分类只剩 1 份（4 份重复是本次故障的根因类别）")
else:
    bad("HTTP 错误分类又出现了多份", {"份数": n_http_err})
if src_tooling.count("urlopen_bound") == 0:
    ok("ai_engine_tooling 不再自己发 HTTP（已改用共用 helper）")
else:
    bad("工具分支又自己发 HTTP 了", src_tooling.count("urlopen_bound"))
if "_post_chat_json" in src_engine and src_tooling.count("_post_chat_json") >= 2:
    ok("工具分支的两轮调用都走共用 helper")
else:
    bad("工具分支没有全部走共用 helper")

print("\n=== H. ollama 路径同样按调用方意图发 max_tokens ===")
# F 段只覆盖了 openai 兼容路径。2026-10-12 审计发现：_call_ollama 的签名
# 收了 max_tokens，函数体却根本没用 —— ai_writer 要的 500/2000 在 ollama
# 路径上一律被丢弃。这类「参数写了没用」的缺陷读代码看不出来，
# 必须真发一次请求、看假厂商到底收到了什么。
r = client.post("/api/ai/llm/configure", json={
    "provider": "ollama", "api_key": "", "model": "qwen2.5:7b",
    "api_url": URL, "consent_cloud": False, "timeout": 3, "custom_models": [],
})
if not (r.get_json() or {}).get("success"):
    bad("切到 ollama 假厂商失败", r.get_json())
else:
    sc("json-ok")
    mk.reset()
    client.post("/api/ai/write/extract-feeding", json={"input": "今天喂了180ml母乳"})
    got_np = [s.get("num_predict") for s in mk.seen()]
    if got_np and all(v == 500 for v in got_np):
        ok("ollama 路径：options.num_predict=500 按调用方意图发出")
    else:
        bad("ollama 路径的 num_predict 不是调用方要的值（max_tokens 被丢弃？）",
            {"期望": 500, "实际": got_np})
    # 结构断言兜底：_call_ollama 必须经由 _resolve_max_tokens 取值
    m = re.search(r"def _call_ollama\(.*?\n(.*?)\n\ndef ", src_engine, re.S)
    body = m.group(1) if m else ""
    if "num_predict" in body and "_resolve_max_tokens" in body:
        ok("_call_ollama 函数体里真的有 num_predict 且走 _resolve_max_tokens")
    else:
        bad("_call_ollama 又没有把 max_tokens 接进 payload", body[:200])

srv.shutdown()

print("\n" + "=" * 46)
print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
print("=" * 46)
if FAIL:
    print("失败明细：")
    for f in FAIL:
        print("  - " + f)
    sys.exit(1)
print("AI 链路端到端通过。")
sys.exit(0)
