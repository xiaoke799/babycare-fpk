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

# ============================================================
# 第二段：模板**自洽性**校验（2026-10-05，对照 dsh resolveModels 补充）
#
# 背景：第一段只查「前端推荐 vs 后端清单」，两边都错、且错得一致时就查不出来。
# 这一段查的是**模板自己跟自己**是否说得通 —— 纯本地判断，不发网络请求。
# 灵感来自 DeepSeek Harness 的 `resolveModels()`：它对模型目录逐条穷尽校验，
# 任何一条不自洽就抛错、启动即失败（fail loud），而不是等用户点了才 404。
#
# ⚠ 判据是「自洽」而不是「我用眼睛看着像」—— 下面每条都写成**可复现的判断**，
#    免得将来有人（包括我）凭印象把真问题说成伪问题。
# ============================================================

# 厂商的 models 允许**故意为空**：这类是「用户自己填模型名」的入口。
# dsh 也有同构设计（拿不到目录时告诉用户 "enter this provider's models by hand"）。
# 名单要跟 ai_engine.PROVIDER_TEMPLATES 里 model_hint 说明「自定义」的厂商对齐。
MODELS_MAY_BE_EMPTY = {"custom"}

# ⚠ 刻意**不校验**「models 与 vision_models 不相交」：
#    多模态模型（claude-opus-4-8 / gpt-5.6-sol / kimi-k3 …）本来就能同时干两件事，
#    同时列进两个清单是正确设计。曾经写过这条判据，实测误伤 5 家厂商，已回退。

print()
print("=== 模板自洽性（纯本地，不发网络请求）===")
print()

self_bad = []
for pid in sorted(TEMPLATES):
    tpl = TEMPLATES[pid]
    models = tpl.get("models") or []
    dm = tpl.get("default_model") or ""
    vms = tpl.get("vision_models") or []
    dvm = tpl.get("default_vision_model") or ""
    probs = []

    # ① models 为空 —— 仅当该厂商不属于「用户自填」白名单时才算问题
    if not models and pid not in MODELS_MAY_BE_EMPTY:
        probs.append("models 为空（该厂商不在「用户自填」白名单里）")

    # ② 重复项：重复会让下拉框出现两个一模一样的选项，且 default 选中哪个不确定
    dup = sorted({m for m in models if models.count(m) > 1})
    if dup:
        probs.append("models 有重复项 %s" % "、".join(dup))

    # ③ 🔴 最要命的一条：default_model 必须**真的在**自己的 models 里。
    #    不在 = 用户一装上、还没动任何设置，就是一个必然 404 的配置。
    #    （2026-10-05 的 deepseek-v4-flash 事件如果当初有这条，根本不会发生。）
    if models and not dm:
        probs.append("models 非空但 default_model 为空（下拉框选不出默认项）")
    if dm and models and dm not in models:
        probs.append("default_model=%s 不在 models 里 ← 装上就是 404" % dm)
    if dm and not models and pid not in MODELS_MAY_BE_EMPTY:
        probs.append("default_model=%s 但 models 为空" % dm)

    # ④ default_vision_model 必须在 vision_models 里（同理，不许选了不存在的那项）
    if dvm and dvm not in vms:
        probs.append("default_vision_model=%s 不在 vision_models 里" % dvm)

    # ⑤ vision_models 的每一项都必须是**非空字符串**。
    #    ⚠ 这里**曾经**写成「models ∩ vision_models 必须为空」，那是**错的判据**：
    #    claude-opus-4-8 / gpt-5.6-sol / kimi-k3 这类**本来就是多模态模型**，
    #    既能文本也能看图，同时出现在两个清单里是**正确设计**，不是污染。
    #    （实测 5 家厂商被这条错误判据误伤 —— 已回退。）
    #    真正会造成运行时报错的是「清单里混进了空值 / 非字符串」：
    #    下拉框会出现一个 value='' 的空选项，选中后请求里 model 字段为空。
    for vm in vms:
        if not isinstance(vm, str) or not vm.strip():
            probs.append("vision_models 里有空值或非字符串：%r" % (vm,))

    # ⑥ vision_enabled 开着却没有任何视觉模型可选 —— 界面会给出一个空下拉框
    if tpl.get("vision_enabled") and not vms:
        probs.append("vision_enabled 为真但 vision_models 为空")

    if probs:
        self_bad.extend("%s: %s" % (pid, p) for p in probs)
        print("  [FAIL] %-9s models=%d vision=%d default=%s"
              % (pid, len(models), len(vms), dm or "(空)"))
        for p in probs:
            print("           -> " + p)
    else:
        skip = "  （models 为空：用户自填，允许）" if (not models and pid in MODELS_MAY_BE_EMPTY) else ""
        print("  [ok]   %-9s models=%d vision=%d default=%s%s"
              % (pid, len(models), len(vms), dm or "(空)", skip))

# 解析器自证：这一段的输出必须**真的**覆盖到所有厂商，
# 否则「0 条失败」可能只是循环没跑（假绿）。
if len(TEMPLATES) < MIN_PROVIDERS:
    print()
    print("[FAIL] 自洽性段只遍历到 %d 家厂商（预期 ≥ %d）—— 解析器自己失效了，"
          "这种「0 条失败」不算通过" % (len(TEMPLATES), MIN_PROVIDERS))
    sys.exit(1)
print()
print("  [ok]   自洽性段覆盖 %d 家厂商（≥ %d）" % (len(TEMPLATES), MIN_PROVIDERS))

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
print("  第一段通过：前端推荐的每个模型都能在下拉框里选到")

if self_bad:
    print()
    print("自洽性对不上 %d 处：" % len(self_bad))
    for b in self_bad:
        print("  - " + b)
    print()
    print("修法：把 default_model 改成 models 里真实存在的那一项；")
    print("      自定义（用户自填）厂商请把 id 加进脚本里的 MODELS_MAY_BE_EMPTY 白名单。")
    sys.exit(1)
print("  第二段通过：每家厂商的模板自己跟自己说得通（默认模型真的存在于自己的清单里）")
print()
print("全部通过。")
sys.exit(0)
