#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""「模型名还活着吗」在线探针 —— 默认**跳过**，必须显式 --online 才发网络请求。

===== 为什么需要它 =====
2026-10-05 的真实事故：DeepSeek 于 2026-09-10 退役了 `deepseek-v4-flash`，
而我们的 `PROVIDER_TEMPLATES['deepseek']['default_model']` 还指着它。
后果：用户在 NAS 上配好、点「测试连接」显示成功（测试用的明文 key + 另一个端点），
但**每一次真实对话都 404**，最后被熔断，全部回落到本地规则引擎 —— 用户看到的
只是「大模型没生效」，没有任何可行动的信息。

而当时的门禁 `check_llm_model_lists.py` **必然全绿**：它只比对「前端推荐表 vs
后端 models」，两边都错、且错得一致时它查不出来。**全绿 ≠ 没问题。**

===== 设计参照 =====
DeepSeek Harness (dsh) 的 `llm-pi-ai/src/discovery.ts` 给了这套机制的正解：
  · 目录里已收录的路由 → 直接从目录回答，**不发请求**（省流量、不依赖网络）
  · 只有目录外的路由 → 才去线上问 `GET {base}/models`
  · 4 MiB 上限，超了**拒绝**而不是截断（截断的 JSON 解析失败会掩盖真因）
  · 拿不到时明确告知「请手工录入该厂商的模型名」，**不假装成功**

本脚本沿用同样的分层原则，但立场更保守：
  · 默认不跑（离线门禁的纯净性优先）；-—online 才联网
  · 联网拿不到 → 判「未知」，**不判失败**（厂商没实现 /models 是合法的）
  · 联网拿到了、且**一个内置模型名都不在里面** → 这才是**_强信号**：清单可能整体过期
  · 联网拿到了、部分在 → 只提示，不算失败（厂商保留旧别名是常见做法）

===== 关键实测事实（2026-10-05 亲自 curl 验证）=====
  $ curl -s -o /dev/null -w '%{http_code}' https://api.deepseek.com/v1/models \\
        -H 'Authorization: Bearer sk-invalid'
  401
  ⇒ 端点**存在**，且**先验鉴权、后验模型**。
  ⇒ 推论一：无效 key 返回 401（不是 404）⇒ 端点可用于发现，不需要真 key 探路。
  ⇒ 推论二：用户当时看到的 404 **不是** URL 错、**不是** key 错，
             而是 key 有效 + 模型名不存在 —— 这一条直接把排查范围缩小到一半。

跑法：
  # 只看端点连通性（不需要任何 key，最安全）
  python .workbuddy/scripts/check_llm_models_live.py --online

  # 用真实 key 校验内置模型名是否仍在厂商清单里（会读到 key，注意环境）
  python .workbuddy/scripts/check_llm_models_live.py --online --key deepseek=sk-xxx

  # 只看某一家
  python .workbuddy/scripts/check_llm_models_live.py --online --provider deepseek

退出码：0 = 没有发现「清单整体过期」的强信号；1 = 发现（需人工确认模型名）。
"""
import argparse
import io
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
BACKEND = os.path.join(ROOT, "app", "backend")
sys.path.insert(0, BACKEND)

# 只读模板常量，不碰真实数据目录
_T = tempfile.mkdtemp(prefix="babycare_livemodels_")
os.environ["BABCARE_LIVEMODELS"] = "1"
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

# ---- 代理绕行（真实坑，2026-10-05 实测踩到）----
# 本机/CI 常设 HTTP_PROXY，但 NO_PROXY 往往是空的。
# 后果：连 127.0.0.1 的本地假服务也会被送去代理，得到 502 ——
# 看起来像"厂商挂了"，实际是代理拦的。NAS 上若配了代理，
# 连本地 Ollama/LM Studio 也会同样被拦。
# 修法：显式给「环回 + 私网」加 NO_PROXY，不动用户其他的代理设置。
_loopback = "127.0.0.1,localhost,::1,0.0.0.0"
for _env in ("NO_PROXY", "no_proxy"):
    _cur = os.environ.get(_env, "")
    _add = [x for x in _loopback.split(",") if x not in _cur]
    os.environ[_env] = ",".join([p for p in [_cur] + _add if p])

# 用「不带代理」的 opener（urllib 默认会读环境代理）
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# ---- 只探这些厂商：本地部署的（ollama/lmstudio/custom）没意义且会拖慢 ----
SKIP_PROVIDERS = {"ollama", "lmstudio", "custom"}

# dsh 同款：响应体上限，超了**拒绝**而不是截断
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
HTTP_TIMEOUT = 12


def models_url_of(api_url):
    """从 chat/completions 端点推同源的 /models 端点。

    与 blueprints/ai.py 的 _list_remote_models() 保持同一套推导规则
    （那里已上线验证过；这里不重复发明）。
    """
    u = (api_url or "").rstrip("/")
    for suffix in ("/chat/completions", "/messages", "/completions"):
        if u.endswith(suffix):
            u = u[: -len(suffix)]
            break
    if not u:
        return ""
    # Anthropic 风格端点是 https://api.anthropic.com/v1/messages → ssh 出 /v1/models
    return u + "/models"


def probe(url, key=None, auth_header="Authorization", auth_format="Bearer {api_key}"):
    """探一个 /models 端点。

    返回 (state, models, note)：
      state ∈ {'ok', 'unauthorized', 'notfound', 'error'}
      models: 解析出的模型名列表（拿不到就是 []）
    """
    headers = {"Accept": "application/json"}
    if key:
        headers[auth_header] = auth_format.replace("{api_key}", key)
    req = urllib.request.Request(url, headers=headers)
    try:
        with _OPENER.open(req, timeout=HTTP_TIMEOUT) as resp:
            # content-length 预检
            cl = resp.headers.get("Content-Length")
            if cl and cl.isdigit() and int(cl) > MAX_RESPONSE_BYTES:
                return "error", [], "响应体 %s 字节，超过 %d 上限（拒绝读取）" % (cl, MAX_RESPONSE_BYTES)
            raw = resp.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                return "error", [], "响应体超 %d 字节上限（拒绝）" % MAX_RESPONSE_BYTES
            data = json.loads(raw.decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            return "unauthorized", [], "HTTP %d（端点存在，鉴权未过）" % exc.code
        if exc.code == 404:
            return "notfound", [], "HTTP 404（该厂商没有这个端点）"
        return "error", [], "HTTP %d" % exc.code
    except urllib.error.URLError as exc:
        return "error", [], "网络不可达：%s" % (str(exc.reason)[:80])
    except Exception as exc:
        return "error", [], "%s: %s" % (type(exc).__name__, str(exc)[:80])

    # 宽容解析（dsh readListing 的同款策略：data 数组 / models 数组两种都要认）
    out = []
    if isinstance(data, dict) and isinstance(data.get("data"), list):
        for m in data["data"]:
            if isinstance(m, dict) and m.get("id"):
                out.append(str(m["id"]))
    elif isinstance(data, dict) and isinstance(data.get("models"), list):
        for m in data["models"]:
            if isinstance(m, dict):
                name = m.get("name") or m.get("id")
                if name:
                    out.append(str(name))
            elif isinstance(m, str):
                out.append(m)
    return "ok", out, "HTTP 200，%d 个模型" % len(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--online", action="store_true",
                    help="真的发网络请求（不加此参数则整个脚本跳过）")
    ap.add_argument("--provider", default="",
                    help="只探指定厂商 id（默认全部）")
    ap.add_argument("--key", action="append", default=[],
                    help="provider=key 形式，可重复；不给则只探端点连通性（会得到 401）")
    args = ap.parse_args()

    print("=== 模型名活性探针 ===")
    if not args.online:
        print()
        print("  未指定 --online：跳过（这是**有意**的默认值）。")
        print("  理由：离线门禁必须随时可跑、不依赖外网、不消耗配额。")
        print("  要看真实厂商清单请加： --online")
        print("  （只探端点连通性不需要 key：会得到 401，恰好证明端点存在）")
        sys.exit(0)

    keys = {}
    for kv in args.key:
        if "=" in kv:
            p, k = kv.split("=", 1)
            keys[p.strip()] = k.strip()

    targets = sorted(p for p in TEMPLATES if p not in SKIP_PROVIDERS)
    if args.provider:
        targets = [p for p in targets if p == args.provider]
        if not targets:
            print("[FAIL] 没有匹配的厂商：%s" % args.provider)
            sys.exit(1)

    print("探测厂商 %d 家：%s" % (len(targets), ", ".join(targets)))
    print()

    strong = []      # 「整体过期」的强信号
    hinted = []      # 只提示
    unknown = []     # 拿不到结论（不算失败）
    probed = 0

    for pid in targets:
        tpl = TEMPLATES[pid]
        api_url = tpl.get("api_url") or ""
        if not api_url:
            print("  [skip] %-9s 无 api_url（用户自填端点）" % pid)
            continue
        murl = models_url_of(api_url)
        key = keys.get(pid)
        auth_h = tpl.get("auth_header", "Authorization")
        auth_f = tpl.get("auth_format", "Bearer {api_key}")
        probed += 1

        state, remote, note = probe(murl, key=key, auth_header=auth_h, auth_format=auth_f)

        if state != "ok":
            unknown.append(pid)
            print("  [ ?? ] %-9s %s" % (pid, note))
            if state == "unauthorized" and not key:
                print("           端点存在（401=先验鉴权）—— 想校验模型名请用 --key %s=sk-xxx" % pid)
            continue

        local = list(tpl.get("models") or [])
        hit = [m for m in local if m in remote]
        miss = [m for m in local if m not in remote]

        if not local:
            print("  [skip] %-9s 本地清单为空（用户自填）" % pid)
            continue

        if not hit:
            # 🔴 强信号：一个都对不上，多半是清单整体过期
            strong.append("%s: 内置 %d 个模型名在厂商清单里**一个都找不到**（厂商现有 %d 个）"
                          % (pid, len(local), len(remote)))
            print("  [FAIL] %-9s %s ← 清单可能整体过期" % (pid, note))
            print("           本地: %s" % ", ".join(local[:6]))
            print("           厂商: %s" % ", ".join(remote[:6]))
        elif miss:
            hinted.append("%s: %s 不在厂商清单里（其余 %d 个有效）"
                          % (pid, "、".join(miss[:4]), len(hit)))
            print("  [warn] %-9s %s ← 有效 %d / 失效 %d" % (pid, note, len(hit), len(miss)))
            print("           失效: %s" % ", ".join(miss[:6]))
        else:
            print("  [ok]   %-9s %s ← 内置 %d 个全部有效" % (pid, note, len(local)))

    print()
    # 自证：至少真的探过若干家，否则「0 条失败」可能只是全被跳过了（假绿）
    if probed == 0:
        print("[FAIL] 一家都没探到 —— 循环没跑起来，这种「0 条失败」不算通过")
        sys.exit(2)
    print("  实际探测 %d 家（跳过的本地部署厂商不计）" % probed)
    print("  拿不到结论 %d 家 %s（不算失败：厂商没实现 /models 是合法的）"
          % (len(unknown), "(" + ", ".join(unknown) + ")" if unknown else ""))

    if strong:
        print()
        print("发现 %d 处「清单可能整体过期」：" % len(strong))
        for s in strong:
            print("  - " + s)
        print()
        print("修法：打开厂商控制台/文档确认真实模型名，改 ai_engine.PROVIDER_TEMPLATES")
        print("      里该厂商的 models 与 default_model（**默认名必须是规范名**）。")
        sys.exit(1)

    if hinted:
        print()
        print("提示 %d 处（厂商可能仍在兼容旧别名，不判失败）：" % len(hinted))
        for h in hinted:
            print("  - " + h)

    print()
    print("未发现「清单整体过期」的强信号。")
    sys.exit(0)


if __name__ == "__main__":
    main()
