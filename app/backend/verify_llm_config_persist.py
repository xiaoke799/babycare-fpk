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
  G. **老库脏值在重启回灌时被修正并落盘**（2026-10-05 第三轮补）
     老版本落库时值不校验 ⇒ 磁盘上可能躺着 temperature='abc'、timeout=99999、
     max_tokens=-5，甚至指向已退役模型的 model。configure_llm 的收口只管内存，
     于是回灌后「内存干净、磁盘仍脏」⇒ 每次重启每个 worker 重收一遍，
     且用户在设置页读到的还是当初填的垃圾。
     判据：磁盘干净时**不**落盘（不无谓写库）；脏时落盘**恰好一次**且写回安全值；
     修好后**不重复**落盘（幂等）；回写的 Key 必须仍是**密文**。
     ⚠ 回写时若把明文 Key 写盘，就直接制造了本门禁 A/B 两条要防的那个事故。
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

# ---------- G：老库脏值在重启回灌时被修正并**落盘**（2026-10-05）----------
# 上一轮加了 configure_llm 的运行时收口，但只管内存。老版本落库时值是不校验的
# （temperature 可能是 'abc'、timeout 99999、model 可能指向已退役的名字），
# 于是回灌后：内存是干净的、**磁盘还是脏的** ⇒
#   ① 每次重启每个 worker 都重收一遍（纯浪费）
#   ② 用户在设置页读到的还是当初填的垃圾，改不掉
# 修法：回灌时判断"磁盘值是否会被收口改掉"，是则把收口后的值写回磁盘。
# ⚠ 判据要**跨进程**（同进程测不出来：内存里那一刻已经是对的）。
g = BOOT + r'''
import json as _j, os as _os

# ⚠ 必须先 push app_context：_set_app_setting 走 utils.get_db()（flask g），
#   没有上下文会直接 RuntimeError: Working outside of application context.
app.app_context().push()

import ai_engine, blueprints.ai as _bai
from blueprints.ai import _load_llm_config, _ensure_llm_applied

_writes = []
_orig_set = _bai._set_app_setting
def _spy(k, v):
    if k == "llm_config":
        _writes.append(v)
    return _orig_set(k, v)
_bai._set_app_setting = _spy

# --- 场景 1：磁盘干净 + 内存不一致 ⇒ 该同步，但**不该**顺带落盘 ---
good = {"provider": "deepseek", "api_key": "", "model": "deepseek-flash",
        "api_url": "https://api.deepseek.com/v1/chat/completions",
        "temperature": 0.5, "max_tokens": 1024, "timeout": 30,
        "vision_enabled": False, "vision_model": "", "custom_models": {},
        "consent_cloud": True}
_orig_set("llm_config", _j.dumps(good, ensure_ascii=False))
_writes.clear()
ai_engine.LLM["api_key"] = ""
ai_engine.LLM["temperature"] = 0.7
ai_engine.LLM["model"] = "deepseek-v4-pro"        # 造出不一致，强制走同步分支
_ensure_llm_applied()
print("CLEAN_WRITES=" + str(len(_writes)))
print("CLEAN_SYNCED_MODEL=" + str(ai_engine.LLM.get("model")))

# --- 场景 2：磁盘是脏的 ⇒ 必须落盘**恰好一次**，且写回安全值 ---
dirty = dict(good); dirty["temperature"] = "abc"; dirty["timeout"] = 99999
_orig_set("llm_config", _j.dumps(dirty, ensure_ascii=False))
_writes.clear()
ai_engine.LLM["api_key"] = ""
_ensure_llm_applied()
_d2 = _load_llm_config() or {}
print("DIRTY_WRITES=" + str(len(_writes)))
print("DIRTY_DISK_TEMP=" + str(_d2.get("temperature")))
print("DIRTY_DISK_TIMEOUT=" + str(_d2.get("timeout")))

# --- 场景 3：修好之后再回灌 ⇒ **不重复落盘**（幂等，否则每次请求都写库）---
_writes.clear()
_ensure_llm_applied()
_ensure_llm_applied()
print("IDEMPOTENT_WRITES=" + str(len(_writes)))

# --- 场景 4：回写时 Key 必须仍是**密文**，绝不能是明文 ---
#   上一轮修的「密文当明文再加密」事故就是这么来的：写盘时用内存里那份密文。
#   ⚠ 判据只能钉「**不等于明文**」，**不能**钉「等于 bcob1: 前缀」——
#     有 cryptography 时走 AESGCM、**没有**前缀；只有 fallback 才用 bcob1:。
#     写死前缀会让这条判据在有 cryptography 的机器上**恒假** ⇒ 等于没有判据。
#   ⚠ 场景必须**既脏又有 Key** 才有意义：磁盘干净时压根不触发落盘（_writes 为空），
#     那时判据会假红。而且 Key 得是**密文形态**（正常落盘就存密文）。
from ai_engine import encrypt_api_key as _enc
good["api_key"] = _enc(%r)          # 磁盘上存的是密文（正常形态）
good["timeout"] = 99999             # ← 制造一个脏值，确保这条路径一定会落盘
_orig_set("llm_config", _j.dumps(good, ensure_ascii=False))
_writes.clear()                     # 清掉场景 3 之前累积的写入，只看本场景
ai_engine.LLM["api_key"] = ""
_ensure_llm_applied()
_w4 = _writes[-1] if _writes else "{}"
_k = _j.loads(_w4).get("api_key") or ""
print("PERSIST_KEY_PLAINTEXT_LEAK=" + str(_k == %r))
print("PERSIST_KEY_IS_CIPHER=" + str(bool(_k) and _k != %r))
# 顺带：回灌后内存里那个 Key 必须能解回原文（否则真重启就用不了）
print("PERSIST_KEY_ROUNDTRIP=" + str(ai_engine.decrypt_api_key(ai_engine.LLM.get("api_key") or "") == %r))
''' % (KEY, KEY, KEY, KEY)
rg = subprocess.run([PY, "-c", g], capture_output=True, text=True, encoding="utf-8",
                    errors="replace", env=ENV, timeout=240)
out_g = rg.stdout or ""
print()
print("=== G 进程（老库脏值回灌 + 落盘）===")
print(out_g.strip() or (rg.stderr or "")[-800:])
w = dict(re.findall(r"^([A-Z_]+)=(.*)$", out_g, re.M))

chk("回灌：磁盘干净时只同步内存、不顺带落盘（不无谓写库）",
    w.get("CLEAN_WRITES") == "0", {"写入次数": w.get("CLEAN_WRITES")})
chk("回灌：内存确实被磁盘值纠正（同步本身没坏）",
    w.get("CLEAN_SYNCED_MODEL") == "deepseek-flash", w.get("CLEAN_SYNCED_MODEL"))
chk("🔴 回灌：老库脏值被修正并**落盘**（否则每次重启重收、用户改不掉）",
    w.get("DIRTY_WRITES") == "1" and isinstance(float(w.get("DIRTY_DISK_TEMP") or "x"), float)
    and w.get("DIRTY_DISK_TIMEOUT") == "600",
    {"写入次数": w.get("DIRTY_WRITES"), "temp": w.get("DIRTY_DISK_TEMP"),
     "timeout": w.get("DIRTY_DISK_TIMEOUT")})
chk("回灌：修好之后不重复落盘（幂等，否则每次 AI 请求都写库）",
    w.get("IDEMPOTENT_WRITES") == "0", {"写入次数": w.get("IDEMPOTENT_WRITES")})
# ⚠ 上面这条**测的是结果、不是某一行代码**，所以它有两道独立的防线：
#   ① `_disk_needs_coercion()` 在回灌入口就返回 False（磁盘已干净 ⇒ 根本不进 persist 分支）
#   ② `to_save != cfg` 在 persist 分支里再挡一次（收口结果与磁盘一致 ⇒ 不写）
#   ⇒ **单独打回任何一条，它都不会转红**（2026-10-05 实测：两条分别打回均仍 30/30）。
#   这不是假绿——任一条防线单独失效时落盘次数仍为 0（冗余覆盖）；
#   真要它转红必须**同时**打回两条。所以验这条时别以为"打了没红=判据没用"，
#   也别反过来以为"它绿=落盘逻辑被覆盖了"。真正能单独定位问题的是上面两条：
#   「老库脏值被修正并落盘」与「磁盘干净时不顺带落盘」。
chk("🔴 回写磁盘时 Key 不是**明文**（写成明文就制造了下一轮「密文当明文」事故）",
    w.get("PERSIST_KEY_PLAINTEXT_LEAK") == "False",
    {"是否泄漏明文": w.get("PERSIST_KEY_PLAINTEXT_LEAK"), "是否为密文形态": w.get("PERSIST_KEY_IS_CIPHER")})
chk("回写磁盘时 Key 确实是密文形态（非空且不等于明文）",
    w.get("PERSIST_KEY_IS_CIPHER") == "True", w.get("PERSIST_KEY_IS_CIPHER"))
chk("回写后内存里的 Key 仍能解回原文（回灌没把凭据搞坏）",
    w.get("PERSIST_KEY_ROUNDTRIP") == "True", w.get("PERSIST_KEY_ROUNDTRIP"))

# 静态：脏值检测与回写的接线要在
ai_src = io.open(os.path.join(BACKEND, "blueprints", "ai.py"), "r", encoding="utf-8").read()
chk("有 _disk_needs_coercion（判断磁盘值是否会被收口改掉）",
    "def _disk_needs_coercion(" in ai_src, "函数不见了")
chk("回灌路径真的按它决定要不要落盘（不是写死 persist=True）",
    "_dirty = _disk_needs_coercion(cfg)" in ai_src and "_apply(persist=True)" in ai_src,
    "回灌没有接上脏值检测")
chk("回写时用内存里的**密文**而不是明文（api_key 单独换回来）",
    "to_save['api_key'] = LLM.get('api_key')" in ai_src,
    "回写路径没有换回密文 —— 会写出明文 Key")

print()
print("=" * 62)
print("通过 %d 项，失败 %d 项" % (len(PASSED), len(FAILED)))
if FAILED:
    for n in FAILED:
        print("  - " + n)
print("=" * 62)
sys.exit(1 if FAILED else 0)
