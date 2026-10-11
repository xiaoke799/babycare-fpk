#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""门禁：规则引擎与 AI 对话必须**分开**。

用户要求（2026-10-11 原话）：
  「规则引擎：就做旁边那种点击回复就行了，因为它也只能死板地回复……
    填了 API，过后我只是在聊天输入那里，如果不填的话，那个功能就不给用。
    不要把两个功能混淆了」

落成的契约：
  · 聊天输入（/api/ai/chat）= 纯 AI。没配好 → 不给答案（answer=None）+ 明确原因 + 去设置。
  · 点击回复（/api/ai/quick-answer）= 纯规则引擎，不需要 API，永远可用。
  · 任何情况下 /api/ai/chat 都**不许**返回规则引擎的话术。

最锋利的一条断言在 A2：拿一个「规则引擎一定能答」的问题去问聊天接口，
并断言 answer 仍是 None —— 只要有人把规则引擎兜底加回去，它立刻转红。

跑法：
  C:\\Users\\X\\.workbuddy\\binaries\\python\\envs\\babycare\\Scripts\\python.exe \\
      .workbuddy/scripts/verify_ai_rule_separation.py
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
BACKEND = os.path.join(ROOT, "app", "backend")
sys.path.insert(0, BACKEND)
sys.path.insert(0, HERE)

TMP = tempfile.mkdtemp(prefix="babycare_sep_")
os.environ.update({
    "BABYCARE_DATA_DIR": TMP,
    "BABYCARE_DB_PATH": os.path.join(TMP, "babycare.db"),
    "BABYCARE_LOG_DIR": os.path.join(TMP, "logs"),
    "BABYCARE_DEV_AUTH": "1",
})
_fcntl = type(sys)("fcntl")
for _n in ("LOCK_EX", "LOCK_SH", "LOCK_NB", "LOCK_UN"):
    setattr(_fcntl, _n, 0)
_fcntl.flock = lambda *a, **k: None
_fcntl.lockf = lambda *a, **k: None
sys.modules["fcntl"] = _fcntl

import mock_llm_server as mk      # noqa: E402
import server                     # noqa: E402
import ai_client                  # noqa: E402

PASS, FAIL = [], []


def ok(m):
    PASS.append(m)
    print("  [OK]   " + m)


def bad(m, detail=""):
    FAIL.append(m)
    print("  [FAIL] " + m + ("  实际 " + str(detail) if detail is not None else ""))


srv, PORT = mk.start_mock()
server.init_db()
c = server.app.test_client()
with c.session_transaction() as s:
    s["authenticated"] = True
    s["is_admin"] = True
    s["user_id"] = 1
BID = (c.post("/api/babies", json={"name": "分离", "birthday": "2026-04-01"})
       .get_json() or {}).get("data", {}).get("id")

# 这个问题的规则引擎一定有现成话术；用它才能证明"聊天没有偷偷用规则引擎"
RULE_ANSWERABLE = "睡眠不好怎么办？"


def chat(msg=RULE_ANSWERABLE, baby_id=None):
    return c.post("/api/ai/chat", json={"message": msg, "baby_id": baby_id}).get_json() or {}


def quick():
    return c.post("/api/ai/quick-answer",
                  json={"message": RULE_ANSWERABLE, "baby_id": BID}).get_json() or {}


def status():
    return (c.get("/api/ai/llm/status").get_json() or {}).get("status") or {}


print("=== A. 未配置大模型：聊天不给用，点击回复照常可用 ===")
r = chat(baby_id=BID)
if r.get("llm_available") is False and r.get("need_config") is True:
    ok("聊天接口明确报「不可用」并标 need_config（前端据此禁用输入框）")
else:
    bad("聊天接口没报不可用", {"available": r.get("llm_available"),
                               "need_config": r.get("need_config")})
if r.get("answer") is None and r.get("llm_used") is False:
    ok("🔴 规则引擎一定能答的问题，聊天接口仍然不给答案（没有偷偷兜底）")
else:
    bad("🔴 聊天接口拿规则引擎顶了一条答案 —— 这正是要消掉的混淆",
        {"answer": str(r.get("answer"))[:60], "used": r.get("llm_used")})
if (r.get("fallback_reason") or "").strip():
    ok("给出了可执行的原因（去哪儿配）")
else:
    bad("没说为什么不可用", r.get("fallback_reason"))
if r.get("llm_model") in ("", None):
    ok("不标模型名（没答出来就不许标）")
else:
    bad("没答出来却标了模型名", r.get("llm_model"))

q = quick()
if q.get("success") and (q.get("answer") or "").strip() and q.get("source") == "rule":
    ok("点击回复走本地规则引擎、未配置也能用")
else:
    bad("点击回复不可用（它本该不依赖大模型）", q)
if (q.get("answer") or "") != (r.get("answer") or ""):
    ok("两条路的输出确实不是同一个东西（说明已分开）")

st = status()
if st.get("chat_usable") is False and st.get("chat_need_config") is True \
        and (st.get("chat_unusable_reason") or "").strip():
    ok("状态接口给出 chat_usable / need_config / 原因（前端只问这一处）")
else:
    bad("状态接口缺 chat_usable 相关字段", {k: st.get(k) for k in
                                        ("chat_usable", "chat_need_config",
                                         "chat_unusable_reason")})

print("\n=== B. 配置好之后：聊天走 AI，点击回复仍走本地 ===")
c.post("/api/ai/llm/configure", json={
    "provider": "deepseek", "api_key": "sk-mock-1", "model": "deepseek-flash",
    "api_url": f"http://127.0.0.1:{PORT}/v1/chat/completions",
    "consent_cloud": True, "timeout": 3})
ai_client.reset_circuit_breaker()
ai_client.clear_ai_cache()
if status().get("chat_usable") is True:
    ok("配好后 chat_usable 变 True（输入框可用了）")
else:
    bad("配好后仍报不可用", status())
r2 = chat(baby_id=BID)
if r2.get("llm_used") is True and (r2.get("answer") or "").strip():
    ok("聊天由大模型回答")
else:
    bad("配好后聊天仍没答上来", {"used": r2.get("llm_used"),
                                 "reason": str(r2.get("fallback_reason"))[:80]})
q2 = quick()
if q2.get("source") == "rule" and (q2.get("answer") or "").strip():
    ok("配好之后点击回复**仍然**走本地规则引擎（没被 AI 顶掉）")
else:
    bad("点击回复被改成走 AI 了（那还是混在一起）", q2)

print("\n=== C. 前端契约 ===")
js = open(os.path.join(ROOT, "app", "frontend", "js", "app.js"), encoding="utf-8").read()
html = open(os.path.join(ROOT, "app", "frontend", "index.html"), encoding="utf-8").read()


def strip_line_comments(text):
    """去掉整行 JS 注释再判。

    ⚠ 项目原则：断言要打在"真代码"上。否则会出现两种错——
      ①删了代码、注释还在 ⇒ 假绿；②注释里提到某句话 ⇒ 假红（本文件就踩过：
        注释里引用了那句要删掉的文案，断言直接报红）。
    只去「整行以 // 开头」的注释，不做行尾截断 —— 行尾截断会误伤 URL 里的 //。
    """
    return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("//"))


js_code = strip_line_comments(js)
if "规则引擎（本地，无需联网）" not in js_code:
    ok("「回答模型」下拉里不再有规则引擎这一项")
else:
    bad('下拉里还留着「规则引擎」这个「模型」选项')
if "status.chat_usable" in js_code and "applyChatAvailability" in js_code:
    ok("前端按后端 chat_usable 禁用输入框（不自己拼条件）")
else:
    bad("前端没接 chat_usable")
if "由本地规则引擎回答" not in js_code:
    ok("前端不再说「由本地规则引擎回答」（剥掉整行注释后仍不出现）")
else:
    bad("前端还在说「由本地规则引擎回答」")
if "/api/ai/quick-answer" in js_code:
    ok("点击回复接的是专用的规则引擎端点")
else:
    bad("点击回复没接 /api/ai/quick-answer")
if "使用规则引擎" in html:
    bad('页面上还写着「使用规则引擎」')
else:
    ok("页面文案不再宣称未配置时用规则引擎回答")
if "/api/ai/quick-answer" in open(os.path.join(BACKEND, "blueprints", "ai.py"),
                                  encoding="utf-8").read():
    ok("后端提供 /api/ai/quick-answer")
else:
    bad("后端没有 /api/ai/quick-answer")

print("\n=== D. 畸形 body 不许打出 5xx（这条是冒烟门禁抓出来的真缺陷）===")
# 背景：这两个端点原先用 request.get_json()。**没带 Content-Type** 时它会抛 415，
# 被兜底 except 接住后变成 500 —— 前端偶尔漏设 Content-Type 就会看到服务器错误。
# 正确写法是 utils.json_body()（silent=True + 只认 dict）。冒烟门禁抓到了它，
# 这里补一条定点断言，免得只靠全路由穷举才发现。
for _path in ("/api/ai/chat", "/api/ai/quick-answer"):
    _bad = []
    for _raw in (None, "null", "[]", "{}", '"str"'):
        _kw = {} if _raw is None else {"data": _raw, "content_type": "application/json"}
        _rr = c.post(_path, **_kw)
        if _rr.status_code >= 500:
            _bad.append((_raw, _rr.status_code))
    if not _bad:
        ok(f"{_path} 对畸形/空 body 一律不 5xx（没带 Content-Type 也是 4xx）")
    else:
        bad(f"{_path} 对畸形 body 打出了 5xx", _bad)

srv.shutdown()
print("\n" + "=" * 46)
print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
print("=" * 46)
if FAIL:
    for f in FAIL:
        print("  - " + f)
    sys.exit(1)
print("规则引擎与 AI 对话已分开。")
sys.exit(0)
