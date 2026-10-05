#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LLM 配置「保存 → 重启 → 仍然可用」门禁。

背景（2026-10-05，用户症状：「配置好了、测试连接也成功，但小萌一直用规则引擎回答」）：
  磁盘上存的 api_key 是**密文**，而 configure_llm 会对 api_key 再加密一次。
  _ensure_llm_applied 重启后把磁盘值直接喂回去 ⇒ 「密文当明文又加密一遍」
  ⇒ decrypt 得到的是**上一次的密文本身** ⇒ 真实对话带着坏 key 打厂商接口必然 401，
  连续 5 次后熔断，整段时间全走规则引擎。
  而设置页「测试连接」用的是前端刚输入的**明文**，所以照样显示"连接成功" ——
  测试过、但对话永远不生效，用户完全看不出原因。

  同一处还有第二个坑：custom_models 在磁盘上是 {provider: [...]} 的 dict，
  而 configure_llm 的自定义模型分支只认 list ⇒ 重启后自定义模型静默消失。

本门禁用**两个进程**覆盖这条路径（同进程测不出来：内存里那一刻是对的）。

判据：
  A. 同进程：保存后立即解密 == 原文
  B. **新进程**：重启同步后解密 == 原文（这条是 P0 的守门人）
  C. 新进程：自定义模型没丢
  D. 调用失败时 llm_model 标「规则引擎」且带 fallback_reason
     （用本地不可达地址造失败，**不依赖外网**）
  E. 无宝宝 id 分支也要带原因
  F. 前端不靠模型名字符串判断是否回退（必须认 llm_used）
"""
import io
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
BACKEND = os.path.join(ROOT, "app", "backend")
PY = sys.executable
KEY = "sk-gate-roundtrip-0123456789"
CUSTOM = "my-custom-model-xyz"

TMP = tempfile.mkdtemp(prefix="babycare_llmcfg_")
ENV = dict(os.environ)
ENV.update({
    "BABYCARE_DATA_DIR": TMP,
    "BABYCARE_DB_PATH": os.path.join(TMP, "babycare.db"),
    "BABYCARE_LOG_DIR": os.path.join(TMP, "logs"),
    "BABYCARE_DEV_AUTH": "1",
})

PASSED, FAILED = [], []


def chk(name, cond, detail=None):
    (PASSED if cond else FAILED).append(name)
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else "   <- %s" % (detail,)))


BOOT = r'''
import os, sys
sys.path.insert(0, %r)
import server
server.init_db()
app = server.app
app.config["PROPAGATE_EXCEPTIONS"] = False
c = app.test_client()
with c.session_transaction() as s:
    s["authenticated"] = True; s["is_admin"] = True; s["user_id"] = 1
''' % BACKEND


# ---------- A 进程：保存配置 ----------
a = BOOT + r'''
c.post("/api/ai/llm/configure", json={
    "provider": "deepseek", "api_key": %r, "model": "deepseek-v4-flash",
    "api_url": "https://api.deepseek.com/v1/chat/completions",
    "consent_cloud": True, "timeout": 5, "custom_models": [%r],
})
import ai_engine
mem = ai_engine.LLM.get("api_key", "")
print("SAME_PROC_PLAIN=" + (ai_engine.decrypt_api_key(mem) if mem else ""))
print("SAME_PROC_CUSTOM=" + ",".join(sorted((ai_engine.LLM.get("custom_models") or {}).get("deepseek", []))))
''' % (KEY, CUSTOM)

ra = subprocess.run([PY, "-c", a], capture_output=True, text=True, encoding="utf-8",
                    errors="replace", env=ENV, timeout=180)
out_a = ra.stdout or ""
print("=== A 进程（保存配置）===")
print(out_a.strip() or (ra.stderr or "")[-500:])
val = dict(re.findall(r"^([A-Z_]+)=(.*)$", out_a, re.M))
chk("同进程：保存后解密 == 原文", val.get("SAME_PROC_PLAIN") == KEY, val.get("SAME_PROC_PLAIN"))
chk("同进程：自定义模型已存在于内存", CUSTOM in (val.get("SAME_PROC_CUSTOM") or ""),
    val.get("SAME_PROC_CUSTOM"))

# ---------- B 进程：重启后同步 + 失败路径标注 ----------
b = BOOT + r'''
import ai_engine
st = (c.get("/api/ai/llm/status").get_json() or {}).get("status") or {}
mem = ai_engine.LLM.get("api_key", "")
print("NEW_PROC_PLAIN=" + (ai_engine.decrypt_api_key(mem) if mem else ""))
print("NEW_PROC_CUSTOM=" + ",".join(sorted((ai_engine.LLM.get("custom_models") or {}).get("deepseek", []))))
# 再打两次，确认不会每次请求都被重新加密（密文漂移）
for _ in range(2):
    c.get("/api/ai/llm/status")
mem2 = ai_engine.LLM.get("api_key", "")
print("STABLE_PLAIN=" + (ai_engine.decrypt_api_key(mem2) if mem2 else ""))
print("ENABLED=" + str(st.get("enabled")))

# ---- 失败路径：本地不可达地址，不碰外网 ----
bid = c.post("/api/babies", json={"name": "门禁宝宝", "birthday": "2025-06-01"}).get_json()["data"]["id"]
c.post("/api/ai/llm/configure", json={
    "provider": "ollama", "model": "qwen3:8b",
    "api_url": "http://127.0.0.1:9/api/chat",   # 9 端口没人监听，必然连不上
    "timeout": 2, "custom_models": [],
})
r = c.post("/api/ai/chat", json={"message": "今天该注意什么", "baby_id": bid,
                                 "model": "", "enable_tools": True}).get_json() or {}
print("FAIL_LLM_ENABLED=" + str(r.get("llm_enabled")))
print("FAIL_LLM_USED=" + str(r.get("llm_used")))
print("FAIL_LLM_MODEL=" + str(r.get("llm_model")))
print("FAIL_REASON=" + str(r.get("fallback_reason")))
r2 = c.post("/api/ai/chat", json={"message": "你好", "baby_id": None, "model": ""}).get_json() or {}
print("NOBABY_MODEL=" + str(r2.get("llm_model")))
print("NOBABY_USED=" + str(r2.get("llm_used")))
print("NOBABY_REASON=" + str(r2.get("fallback_reason")))

# ---- 「留空 = 不修改」：改个温度再保存，不能把已存的 Key 抹掉 ----
c.post("/api/ai/llm/configure", json={
    "provider": "deepseek", "api_key": %r, "model": "deepseek-v4-flash",
    "api_url": "https://api.deepseek.com/v1/chat/completions",
    "consent_cloud": True, "timeout": 5, "custom_models": [],
})
c.post("/api/ai/llm/configure", json={
    "provider": "deepseek", "api_key": "", "model": "deepseek-v4-flash",   # ← 输入框留空
    "api_url": "https://api.deepseek.com/v1/chat/completions",
    "consent_cloud": True, "temperature": 0.3, "timeout": 5, "custom_models": [],
})
st2 = (c.get("/api/ai/llm/status").get_json() or {}).get("status") or {}
print("BLANK_KEEP_CONFIGURED=" + str(st2.get("api_key_configured")))
print("BLANK_KEEP_PLAIN=" + ai_engine.decrypt_api_key(ai_engine.LLM.get("api_key") or ""))
print("BLANK_KEEP_TEMP=" + str(st2.get("temperature")))

# 换厂商且留空 ⇒ 旧凭据必须清掉（A 厂商的 Key 打 B 厂商必 401，不如逼重填）
c.post("/api/ai/llm/configure", json={
    "provider": "kimi", "api_key": "", "model": "kimi-k3",
    "api_url": "https://api.moonshot.cn/v1/chat/completions",
    "consent_cloud": True, "timeout": 5, "custom_models": [],
})
st3 = (c.get("/api/ai/llm/status").get_json() or {}).get("status") or {}
print("SWITCH_CLEARED=" + str(not st3.get("api_key_configured")))

# ---- 测试连接：输入框留空时用**库里存的 Key**；并校验所选模型是否在账号可用列表里 ----
import json as _json
import urllib.request as _u

_seen = []


class _Resp:
    def __init__(self, payload):
        self._b = _json.dumps(payload).encode("utf-8")

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_urlopen(req, timeout=None):
    url = getattr(req, "full_url", str(req))
    auth = ""
    try:
        for k, v in dict(req.headers).items():
            if k.lower() in ("authorization", "x-api-key"):
                auth = str(v)
    except Exception:
        pass
    _seen.append((url, auth))
    if url.endswith("/models"):
        return _Resp({"data": [{"id": "deepseek-v4-flash"}, {"id": "deepseek-v4-pro"}]})
    return _Resp({"choices": [{"message": {"content": "pong"}}]})


_u.urlopen = _fake_urlopen          # 本地假厂商，门禁不碰外网
c.post("/api/ai/llm/configure", json={
    "provider": "deepseek", "api_key": %r, "model": "deepseek-v4-flash",
    "api_url": "https://api.deepseek.com/v1/chat/completions",
    "consent_cloud": True, "timeout": 5, "custom_models": [],
})
_t = c.post("/api/ai/llm/test", json={
    "provider": "deepseek", "api_key": "",       # ← 已配置时输入框就是空的
    "model": "deepseek-v4-flash",
    "api_url": "https://api.deepseek.com/v1/chat/completions", "timeout": 5,
}).get_json() or {}
print("TEST_BLANK_OK=" + str(_t.get("success")))
print("TEST_BLANK_MSG=" + str(_t.get("message")))
print("TEST_BLANK_MODELOK=" + str(_t.get("model_ok")))
print("TEST_USED_STORED_KEY=" + str(any(%r in a for _u2, a in _seen)))
_t2 = c.post("/api/ai/llm/test", json={
    "provider": "deepseek", "api_key": "",
    "model": "model-that-does-not-exist",
    "api_url": "https://api.deepseek.com/v1/chat/completions", "timeout": 5,
}).get_json() or {}
print("TEST_BADMODEL_MODELOK=" + str(_t2.get("model_ok")))
print("TEST_BADMODEL_MSG=" + str(_t2.get("message"))[:120])
print("TEST_AVAIL=" + ",".join(_t.get("available_models") or []))
''' % (KEY, KEY, KEY)

rb = subprocess.run([PY, "-c", b], capture_output=True, text=True, encoding="utf-8",
                    errors="replace", env=ENV, timeout=240)
out_b = rb.stdout or ""
print()
print("=== B 进程（模拟重启后同步 + 失败路径）===")
print(out_b.strip() or (rb.stderr or "")[-800:])
v = dict(re.findall(r"^([A-Z_]+)=(.*)$", out_b, re.M))

chk("🔴 新进程：重启同步后解密 == 原文（密文当明文加密过的就是这条抓）",
    v.get("NEW_PROC_PLAIN") == KEY, {"实际": v.get("NEW_PROC_PLAIN")})
chk("多次请求后仍然 == 原文（不会每次请求再加密一遍）",
    v.get("STABLE_PLAIN") == KEY, v.get("STABLE_PLAIN"))
chk("新进程：自定义模型没丢", CUSTOM in (v.get("NEW_PROC_CUSTOM") or ""), v.get("NEW_PROC_CUSTOM"))
chk("新进程：status.enabled 为 True", v.get("ENABLED") == "True", v.get("ENABLED"))

chk("调用失败时 llm_model 标「规则引擎」（不许标模型名）",
    v.get("FAIL_LLM_MODEL") == "规则引擎",
    {"llm_model": v.get("FAIL_LLM_MODEL"), "注意": "标模型名会让用户以为是大模型答的"})
chk("调用失败时 llm_used 为 False", v.get("FAIL_LLM_USED") == "False", v.get("FAIL_LLM_USED"))
chk("调用失败时带上失败原因", bool((v.get("FAIL_REASON") or "").strip()) and v.get("FAIL_REASON") != "None",
    v.get("FAIL_REASON"))
chk("llm_enabled 仍为 True（配置没问题，是调用失败）", v.get("FAIL_LLM_ENABLED") == "True",
    v.get("FAIL_LLM_ENABLED"))

chk("无宝宝分支也标「规则引擎」且带原因",
    v.get("NOBABY_MODEL") == "规则引擎" and v.get("NOBABY_USED") == "False"
    and bool((v.get("NOBABY_REASON") or "").strip()) and v.get("NOBABY_REASON") != "None",
    {"model": v.get("NOBABY_MODEL"), "used": v.get("NOBABY_USED"), "reason": v.get("NOBABY_REASON")})

chk("保存时 API Key 留空 = 不修改（改温度不会把已存 Key 抹掉）",
    v.get("BLANK_KEEP_CONFIGURED") == "True" and v.get("BLANK_KEEP_PLAIN") == KEY,
    {"configured": v.get("BLANK_KEEP_CONFIGURED"), "plain": v.get("BLANK_KEEP_PLAIN")})
chk("留空保存时其余字段照常生效（温度已改成 0.3）", v.get("BLANK_KEEP_TEMP") == "0.3",
    v.get("BLANK_KEEP_TEMP"))
chk("换厂商且留空 ⇒ 旧凭据被清掉（逼用户重填）", v.get("SWITCH_CLEARED") == "True",
    v.get("SWITCH_CLEARED"))

chk("测试连接：输入框留空时用库里存的 Key（不再报「请填写 API Key」）",
    v.get("TEST_BLANK_OK") == "True" and "请填写 API Key" not in (v.get("TEST_BLANK_MSG") or ""),
    {"ok": v.get("TEST_BLANK_OK"), "msg": v.get("TEST_BLANK_MSG")})
chk("测试连接：Authorization 头里确实是那个存量 Key",
    v.get("TEST_USED_STORED_KEY") == "True", v.get("TEST_USED_STORED_KEY"))
chk("测试连接：所选模型在可用列表里 ⇒ model_ok 为 True",
    v.get("TEST_BLANK_MODELOK") == "True", v.get("TEST_BLANK_MODELOK"))
chk("测试连接：模型不在可用列表 ⇒ model_ok 为 False 且给出可用清单",
    v.get("TEST_BADMODEL_MODELOK") == "False"
    and "deepseek-v4-pro" in (v.get("TEST_AVAIL") or ""),
    {"model_ok": v.get("TEST_BADMODEL_MODELOK"), "avail": v.get("TEST_AVAIL"),
     "msg": v.get("TEST_BADMODEL_MSG")})

# ---------- F：前端判据 ----------
js = io.open(os.path.join(ROOT, "app", "frontend", "js", "app.js"), "r", encoding="utf-8").read()
chk("前端用 llm_used 判断是否回退（不再靠模型名字符串猜）",
    "data.llm_used" in js and "appendFallbackNotice" in js)
chk("后端响应里带了 llm_used 字段",
    "llm_used" in io.open(os.path.join(BACKEND, "blueprints", "ai.py"),
                          "r", encoding="utf-8").read())

print()
print("=" * 62)
print("通过 %d 项，失败 %d 项" % (len(PASSED), len(FAILED)))
if FAILED:
    for n in FAILED:
        print("  - " + n)
print("=" * 62)
sys.exit(1 if FAILED else 0)
