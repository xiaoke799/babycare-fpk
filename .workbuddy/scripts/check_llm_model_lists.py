#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""「推荐模型」与「后端真实模型清单」一致性门禁。

背景（2026-10-05）：AI 大模型设置页里，下拉框选中 deepseek-v4-flash，
右侧却写着「推荐: deepseek-chat」—— **下拉框里根本没有这个模型**。
根因是模型名单写了两份：前端 `LLM_MODELS_FEATURED`（app.js）与后端
`PROVIDER_TEMPLATES`（ai_engine.py）。厂商更新模型时只改了后端，前端推荐没跟上，
于是 4 家厂商（deepseek / openai / doubao / lmstudio）都在推荐一个**选不到**的模型。
用户照着"推荐"去找，只会一头雾水。

判据：
  ① 前端推荐表里每个模型，都必须存在于后端该厂商的 models 里
  ② 推荐表里出现的厂商 id 必须都是后端真实存在的厂商（拼错了会被静默忽略）
  ③ 前端必须有「推荐项过滤 + 为空回落 default_model」的兜底逻辑
     （过滤是为了即使将来再漂，界面也不会显示一个选不到的推荐项）
  ④ 抓取到的厂商数不能少于 5（防止解析器写废了还报"0 条"假绿）

跑法（要 import ai_engine）：
  C:\\Users\\X\\.workbuddy\\binaries\\python\\envs\\babycare\\Scripts\\python.exe \\
      .workbuddy/scripts/check_llm_model_lists.py
退出码：0 = 一致；1 = 有推荐项对不上。
"""
import io
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
BACKEND = os.path.join(ROOT, "app", "backend")
JS_PATH = os.path.join(ROOT, "app", "frontend", "js", "app.js")
sys.path.insert(0, BACKEND)

# 只要模板常量，不碰真实数据目录
_T = tempfile.mkdtemp(prefix="babycare_llmmodels_")
os.environ["BABYCARE_DATA_DIR"] = _T
os.environ["BABYCARE_DB_PATH"] = os.path.join(_T, "babycare.db")
os.environ["BABYCARE_LOG_DIR"] = os.path.join(_T, "logs")
os.environ["BABYCARE_DEV_AUTH"] = "1"
_fcntl = type(sys)("fcntl")
for _n in ("LOCK_EX", "LOCK_SH", "LOCK_NB", "LOCK_UN"):
    setattr(_fcntl, _n, 0)
_fcntl.flock = lambda *a, **k: None
_fcntl.lockf = lambda *a, **k: None
sys.modules["fcntl"] = _fcntl

import ai_engine  # noqa: E402

TEMPLATES = ai_engine.PROVIDER_TEMPLATES

js = io.open(JS_PATH, "r", encoding="utf-8").read()
m = re.search(r"const LLM_MODELS_FEATURED = \{(.*?)\n\};", js, re.S)
if not m:
    print("[FAIL] app.js 里找不到 LLM_MODELS_FEATURED")
    sys.exit(1)
FEATURED = {}
for pid, body in re.findall(r"(\w+):\s*\[([^\]]*)\]", m.group(1)):
    FEATURED[pid] = re.findall(r"'([^']+)'", body)

MIN_PROVIDERS = 5
print("=== 推荐模型 vs 后端模型清单 ===")
print("后端厂商: %d 家；前端推荐表: %d 家" % (len(TEMPLATES), len(FEATURED)))
print()

bad = []
for pid, feats in sorted(FEATURED.items()):
    tpl = TEMPLATES.get(pid)
    if tpl is None:
        bad.append("%s: 后端没有这个厂商 id（拼错了会被静默忽略）" % pid)
        print("  [FAIL] %-9s 后端无此厂商" % pid)
        continue
    models = tpl.get("models") or []
    missing = [f for f in feats if f not in models]
    note = "推荐 %s" % (",".join(feats) if feats else "（空，回落默认模型 %s）" % (tpl.get("default_model") or "-"))
    if missing:
        bad.append("%s: 推荐了不存在的模型 %s" % (pid, "、".join(missing)))
        print("  [FAIL] %-9s %s  ← 不存在：%s" % (pid, note, "、".join(missing)))
    else:
        print("  [ok]   %-9s %s" % (pid, note))

# ---- 兜底逻辑必须在（否则将来再漂，界面又会显示选不到的推荐项）----
has_filter = ".filter(m => (provider.models || []).includes(m))" in js
has_fallback = "featured.length === 0 && provider.default_model" in js
print()
print("  [%s]   前端会过滤掉不在下拉框里的推荐项" % ("ok" if has_filter else "FAIL"))
print("  [%s]   推荐项全被过滤时回落到 default_model" % ("ok" if has_fallback else "FAIL"))
if not has_filter:
    bad.append("前端缺少「过滤不在下拉框里的推荐项」逻辑")
if not has_fallback:
    bad.append("前端缺少「推荐为空时回落 default_model」逻辑")

if len(TEMPLATES) < MIN_PROVIDERS:
    print()
    print("[FAIL] 只解析到 %d 家厂商（预期 ≥ %d）—— 解析器自己失效了，"
          "这种「0 条失败」不算通过" % (len(TEMPLATES), MIN_PROVIDERS))
    sys.exit(1)

print()
if bad:
    print("对不上 %d 处：" % len(bad))
    for b in bad:
        print("  - " + b)
    print()
    print("修法：把前端推荐项改成后端 models 里真实存在的模型；")
    print("      或者留空数组 [](界面会回落到后端的 default_model)。")
    sys.exit(1)
print("全部一致：前端推荐的每个模型都能在下拉框里选到")
sys.exit(0)
