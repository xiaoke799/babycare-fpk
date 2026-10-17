#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对话内直接记录门禁（原「AI 帮写 UI」）—— 静态结构 + 假厂商端到端 + 负向自检。

沿革：
  2026-10-14 用户拍板「AI 帮写要做 UI」：/api/ai/write/* 端点接成独立页
  page-ai-writer（记录栏「智能」组）+ 相册「AI 描述」按钮。
  2026-10-17 用户拍板撤掉独立入口与页面：记录统一走小萌对话框的确认卡
  （说话 → extract-auto 识别 → 是/否确认卡 → 点「记下来」落库）；页面里
  3 个生成类功能（日记帮写/本周总结/里程碑建议）一并撤掉，后端 17 个端点
  保留（能力储备）。同一天还关闭了「模型在对话里自主直写记录」的老通道
  （chat 不再注入 create_* 写工具），保证「每次记录前都会弹框」没有旁路。

本门禁防的是这几类**静默**缺陷（读代码看不出来、用户点了没反应或数据
凭空消失的那类）：
  S1. 独立页面撤不干净/复活（section / initAIWriterPage / loadPageData case
      三处任一残留 = 死代码死链）；相册「AI 描述」按钮被误删
  S2. AIW_TYPES 每类少配一项（label/placeholder/extract/fields/timeField/save）
      ⇒ 对话确认卡该类直接失灵
  S3. extract URL 拼路径（check_frontend_api_exists 只认字面 URL）
  S4. 字段双向错位：save 读的键不在字段集合（值恒空、静默丢失）
  S5. 依赖符号缺失（缺一个运行时就抛）
  S6. 确认卡样式被误删 / aiw-* 死样式复活（独立页面已撤，复活多半是回退残留）
  S7. 相册「AI 描述」按钮绑定断链
  S9. 时间归一丢失；保存失败不给真实原因；临时控件不清理
  S10. 后端 17 个 ai/write 端点被误删（前端不用 ≠ 后端能删）
  S11. 对话内直接记录接线三件套：extract-auto / 是-否确认卡 / 授权默认开启
  S12. 老通道复活：chat 又注入 create_* 写工具（模型自主直写无确认框，
      与「每次记录前都要弹框」冲突，还会和确认卡重复写入）
  E1. 未配置 LLM：17 个端点都必须 400「请先配置 AI 大模型」
  E2. 配置后 11 extract + 5 生成端点全部可用（假厂商 json-ok）
  E2b. extract-auto 认得出 feeding（json-auto）；授权关掉必须 403 not_authorized
  E3/E4. 失败语义不串味 + record 键 ⊆ 确认集合（无静默丢失）
  E6/E7. /api/health/milestones 与 sleep is_nap 落库闭环
  E8. 对话请求真正发往假厂商的 tools 里没有 create_*（老通道关闭的端到端铁证）

跑法（必须用项目 venv，要 import server）：
  C:\\Users\\X\\.workbuddy\\binaries\\python\\envs\\babycare\\Scripts\\python.exe \\
      .workbuddy/scripts/verify_ai_writer_ui.py
  ... 加 --selftest 跑内置负向对照（破坏代码后判据必须转红）
退出码：0 全过；1 有失败；2 自检失败（判据本身失灵）。
"""
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
BACKEND = os.path.join(ROOT, "app", "backend")
FE = os.path.join(ROOT, "app", "frontend")
sys.path.insert(0, BACKEND)
sys.path.insert(0, HERE)

# 门禁隔离：必须在 import server 之前设（app.config 无效，get_db 只认
# import 时的模块级 DB_PATH）。假隔离会让门禁静默打默认库。
TMP = tempfile.mkdtemp(prefix="babycare_aiwui_")
os.environ.update({
    "BABYCARE_DATA_DIR": TMP,
    "BABYCARE_DB_PATH": os.path.join(TMP, "babycare.db"),
    "BABYCARE_LOG_DIR": os.path.join(TMP, "logs"),
    "BABYCARE_DEV_AUTH": "1",
})

import mock_llm_server as mk      # noqa: E402
import server                     # noqa: E402
import ai_client                  # noqa: E402

JS_PATH = os.path.join(FE, "js", "app.js")
HTML_PATH = os.path.join(FE, "index.html")
CSS_PATH = os.path.join(FE, "css", "main.css")
AI_PY = os.path.join(BACKEND, "blueprints", "ai.py")
ENGINE_PY = os.path.join(BACKEND, "ai_engine.py")

PASS, FAIL = [], []


def _record_auth_default_on(eng_src):
    """LLM defaults 里 ai_record_authorized 默认开启（新功能默认就要能用）。"""
    return bool(re.search(r"'ai_record_authorized':\s*True", eng_src))


def ok(msg):
    PASS.append(msg)
    print("  [OK]   " + msg)


def bad(msg, detail=""):
    FAIL.append(msg)
    print("  [FAIL] " + msg + (("   实际 " + str(detail)) if detail != "" else ""))


# ============================================================
# 静态部分：解析 app.js 的 AIW_TYPES 与页面结构
# ============================================================

def block_of(src, open_idx):
    """从 src[open_idx] == '{' 起做括号配对，返回 (体, 结束下标)。"""
    d, i, n = 0, open_idx, len(src)
    while i < n:
        if src[i] == "{":
            d += 1
        elif src[i] == "}":
            d -= 1
            if d == 0:
                return src[open_idx + 1:i], i
        i += 1
    return None, -1


def parse_aiw_types(js):
    """解析 const AIW_TYPES = { key: {...}, ... }，返回 {key: cfg_dict}。

    cfg_dict: label / placeholder / extract_url / save_url / fields(keys) /
    time_key / extra_time_key / has_prepare / prepare_refs / save_refs
    """
    start = js.index("const AIW_TYPES = {")
    body, _ = block_of(js, js.index("{", start))
    out = {}
    # 扫深度 1 的 "key: {" 项
    i, n, depth = 0, len(body), 0
    while i < n:
        ch = body[i]
        if ch in "'\"`":
            q = ch
            i += 1
            while i < n:
                if body[i] == "\\":
                    i += 2
                    continue
                if body[i] == q:
                    break
                i += 1
            i += 1
            continue
        if ch == "{":
            if depth == 0:
                m = re.search(r"([A-Za-z_]\w*)\s*:\s*$", body[:i])
                if not m:
                    depth += 1
                    i += 1
                    continue
                key = m.group(1)
                blk, end = block_of(body, i)
                if blk is None:
                    break
                # —— 类块内提取 ——
                ml = re.search(r"\blabel:\s*'([^']*)'", blk)
                mph = re.search(r"\bplaceholder:\s*'([^']*)'", blk)
                mex = re.search(r"api\(\s*'([^']*)'", blk)
                msv = re.search(r"api\(\s*`([^`]*)`", blk)
                mf = re.search(r"\bfields:\s*\[", blk)
                field_keys = []
                no_save = set()
                if mf:
                    arr_start = mf.end() - 1
                    d2, j = 0, arr_start
                    while j < len(blk):
                        if blk[j] == "[":
                            d2 += 1
                        elif blk[j] == "]":
                            d2 -= 1
                            if d2 == 0:
                                break
                        j += 1
                    field_keys = re.findall(r"key:\s*'([^']*)'", blk[arr_start:j])
                    # noSave：有意不落库的展示字段（白名单，须在代码里写明理由）
                    for fm in re.finditer(r"key:\s*'([^']*)'[^}]*?noSave:\s*true", blk[arr_start:j]):
                        no_save.add(fm.group(1))
                mtf = re.search(r"\btimeField:\s*\{\s*key:\s*'([^']*)'", blk)
                mx = re.search(r"\bextraTimeField:\s*\{\s*key:\s*'([^']*)'", blk)
                mp = re.search(r"\bprepare\s*\(", blk)
                prep_refs = []
                if mp:
                    pblk, _ = block_of(blk, blk.index("{", mp.start()))
                    prep_refs = re.findall(r"\brec\.(\w+)", pblk or "")
                msave = re.search(r"\bsave\s*\(\s*v\s*\)\s*\{", blk)
                save_refs, save_url = [], ""
                if msave:
                    sblk, _ = block_of(blk, blk.index("{", msave.start()))
                    save_refs = sorted(set(re.findall(r"\bv\.(\w+)", sblk or "")))
                    mu = re.search(r"api\(\s*`([^`]*)`", sblk or "")
                    if mu:
                        save_url = mu.group(1)
                out[key] = {
                    "label": ml.group(1) if ml else "",
                    "placeholder": mph.group(1) if mph else "",
                    "extract_url": mex.group(1) if mex else "",
                    "save_url": save_url or (msv.group(1) if msv else ""),
                    "fields": field_keys,
                    "no_save": no_save,
                    "time_key": mtf.group(1) if mtf else None,
                    "extra_time_key": mx.group(1) if mx else None,
                    "has_prepare": bool(mp),
                    "prepare_refs": prep_refs,
                    "save_refs": save_refs,
                }
                i = end + 1
                continue
            depth += 1
        elif ch == "}":
            depth -= 1
        i += 1
    return out


def static_checks(js, html, css):
    print("=== S. 静态结构 ===")
    # S1 撤除完整性（2026-10-17 用户拍板：独立页面/入口/初始化全撤，记录走
    # 小萌对话框确认卡）。防「撤不干净/半撤销」留下的死代码死链；
    # 相册「AI 描述」是保留功能，必须还在。
    gone = []
    if 'id="page-ai-writer"' in html:
        gone.append("<section id=page-ai-writer> 复活")
    if "function initAIWriterPage" in js or "case 'ai-writer'" in js:
        gone.append("initAIWriterPage / loadPageData case 残留")
    ok("AI 帮写独立页面已彻底撤除（section/init/case 三处全无）") if not gone \
        else bad("独立页面代码复活（撤不干净）", gone)
    n_photo = html.count('id="aiPhotoDescBtn"')
    ok("相册挂点 aiPhotoDescBtn 恰好 1 处") if n_photo == 1 \
        else bad("aiPhotoDescBtn 出现 %d 次（重复 id 会让绑定只生效一半）" % n_photo)

    # S2 AIW_TYPES 配置
    types = parse_aiw_types(js)
    ok("AIW_TYPES 解析出 %d 类" % len(types)) if len(types) == 11 \
        else bad("AIW_TYPES 类型数不是 11", sorted(types))
    for k, c in sorted(types.items()):
        holes = [f for f in ("label", "placeholder", "extract_url", "fields",
                             "save_url") if not c[f]]
        if c["time_key"] is None:
            holes.append("timeField")
        if holes:
            bad("%s 类配置缺项" % k, holes)
    if not any("类配置缺项" in f for f in FAIL):
        ok("11 类配置齐备（label/placeholder/extract/fields/timeField/save）")

    # S3 extract URL 必须字面（check_frontend_api_exists 的口面只认字面 URL，
    # 拼路径会被判「后端没这条路由」—— 项目里为此宁可写 11 个一字函数）
    dyn = [k for k, c in types.items() if "${" in c["extract_url"] or not c["extract_url"]]
    ok("11 个 extract URL 全是字面") if not dyn else bad("extract URL 是动态拼接", dyn)

    # S4 字段双向对齐：fields∪timeField∪extraTimeField 的键 save 都要用；
    # save 读的键都必须能被 aiwCollectValues 收集到（收集范围 = 同集合）。
    # noSave 是唯一豁免（有意不落库的展示字段，代码里写明了理由）。
    for k, c in sorted(types.items()):
        collect = set(c["fields"])
        if c["time_key"]:
            collect.add(c["time_key"])
        if c["extra_time_key"]:
            collect.add(c["extra_time_key"])
        unused = sorted(x for x in collect - c["no_save"] if x not in c["save_refs"])
        if unused:
            bad("%s 类这些字段配了但 save 没用（用户改了没保存）" % k, unused)
        out_of_range = sorted(x for x in c["save_refs"] if x not in collect)
        if out_of_range:
            bad("%s 类 save 读了收集不到的键（值恒为空，静默丢失）" % k, out_of_range)
    if not any("字段配了但 save 没用" in f or "收集不到的键" in f for f in FAIL):
        ok("11 类字段双向对齐（fields ↔ save 引用 ↔ 收集范围）")

    # S5 依赖符号（缺一个运行时就抛）——只留对话确认卡/相册描述真正用到的
    deps = ["function gotoAISettings", "FEEDING_TYPE_OPTIONS =", "const TEETH_DATA",
            "function toLocalDatetime", "function getToday", "function calcAgeMonths",
            "function readAuthor", "photoPageState", "photoViewCurrentId",
            "function escapeHtml", "function aiwToothName", "function showToast"]
    miss = [d for d in deps if d not in js]
    ok("模块依赖的 %d 个符号都在" % len(deps)) if not miss else bad("依赖符号缺失", miss)

    # S6 样式：确认卡样式必须在；aiw-* 死样式不得复活（独立页面已撤）
    need_cls = ["chat-record-card", "chat-record-title", "chat-record-summary",
                "chat-record-actions", "chat-record-done"]
    miss_cls = [c for c in need_cls if ("." + c) not in css]
    ok("确认卡 5 个样式类都有 CSS 定义") if not miss_cls \
        else bad("确认卡样式缺失（卡片裸奔）", miss_cls)
    aiw_left = []
    if re.search(r"\.aiw-", css):
        aiw_left.append("css .aiw-* %d 处" % len(re.findall(r"\.aiw-", css)))
    for cm in re.finditer(r'class="([^"]*)"', js):
        hit = [x for x in re.sub(r"\$\{[^}]*\}", " ", cm.group(1)).split()
               if x.startswith("aiw-")]
        if hit:
            aiw_left.append("js class=%r" % (hit[0],))
    for cm in re.finditer(r"className\s*=\s*'([^']*)'", js):
        hit = [x for x in re.sub(r"\$\{[^}]*\}", " ", cm.group(1)).split()
               if x.startswith("aiw-")]
        if hit:
            aiw_left.append("js className=%r" % (hit[0],))
    if re.search(r'class="[^"]*aiw-', html):
        aiw_left.append("html class 含 aiw-")
    ok("aiw-* 死类名/死样式已清零（独立页面残留的痕迹）") if not aiw_left \
        else bad("aiw-* 死类名/样式复活（多半是回退残留）", aiw_left[:6])

    # S7 相册「AI 描述」按钮的绑定（verify_event_bindings 的静态 id 口径之外，
    # 这里判「按钮 → 处理函数」这条链没断）
    ok("相册绑了 aiPhotoDescBtn → aiwGeneratePhotoDesc") if \
        re.search(r"getElementById\('aiPhotoDescBtn'\)", js) and \
        re.search(r"addEventListener\('click',\s*aiwGeneratePhotoDesc\)", js) else \
        bad("相册 AI 描述按钮没绑或绑定断链")

    # S9 时间归一与失败分支
    ok("aiwToDbTime 把 T 换空格并补 :00") if \
        re.search(r"replace\('T', ' '\)\s*\+\s*':00'", js) else \
        bad("aiwToDbTime 的时间归一不见了")
    # 确认卡保存失败：真实原因 + 网络错提示（不许静默卡死）
    ok("确认卡保存失败给真实原因（message/网络错误）") if \
        "showToast.error((res && res.message) || '保存失败')" in js and \
        "'网络错误，请重试'" in js else \
        bad("确认卡保存失败的提示不完整（用户看不到为什么没记上）")
    # aiwChatSave 的临时记录者下拉必须清理（否则 DOM 里越积越多）
    ok("aiwChatSave 失败时也清理临时控件（err 回调）") if \
        re.search(r"err\s*=>\s*\{\s*cleanup\(\);\s*throw err;\s*\}", js) else \
        bad("aiwChatSave 失败路径不清理临时控件")
    # 相册 AI 描述的异步调用有 catch（网络错不静默）
    ok("相册 AI 描述有 catch 失败分支") if \
        re.search(r"aiwGeneratePhotoDesc[\s\S]{0,1600}?\.catch\(", js) else \
        bad("AI 描述网络错会静默")

    # S10 后端 17 个 ai/write 端点都在（前端不用 ≠ 后端能删；这里是防回归）
    # 2026-10-17：16 → 17（新增 extract-auto，对话内直接记录用）。独立页面
    # 撤除后，11 extract + 4 生成端点暂为能力储备（前端只再引用 extract-auto
    # 与 photo-description）；后端端点一个都不许删。
    ai_py = open(AI_PY, encoding="utf-8").read()
    eps = re.findall(r"@bp\.route\('/api/ai/write/([\w-]+)'", ai_py)
    ok("后端 ai/write 端点 17 个") if len(eps) == 17 else \
        bad("后端 ai/write 端点数变了", len(eps))
    # 剥注释再抽（注释里的示例路径 `/api/ai/write/extract-${type}` 会被
    # 抽成不存在的 `extract-` —— 断言先剥注释再判，是项目的常规纪律）
    js_nc = re.sub(r"//[^\n]*", "", js)
    js_nc = re.sub(r"/\*[\s\S]*?\*/", "", js_nc)
    cited = set(re.findall(r"/api/ai/write/([\w-]+)", js_nc))
    miss = sorted(cited - set(eps))
    ok("前端引用的 %d 个 ai/write 端点后端都有" % len(cited)) if not miss \
        else bad("前端引了后端没有的", miss)

    # S11 对话内直接记录（小萌浮窗）接线三件套：
    #   ① sendMsg 里真的发 extract-auto（字面 URL，动态拼的 check_frontend_api_exists 不认）
    #   ② 识别出记录后渲染的是「是/否」确认卡（用户拍板的形态）
    #   ③ 授权开关默认开启（ai_engine 的 LLM defaults）+ 端点真的校验授权
    # 少任何一件，这个功能就是「设置了却静默不工作」。
    mascot_src = js[js.index("function sendMsg"):js.index("function renderBotText")]
    ok("小萌 sendMsg 接线 extract-auto（对话内直接记录）") if \
        "/api/ai/write/extract-auto" in mascot_src and "renderRecordConfirmCard" in mascot_src \
        else bad("小萌对话内没有接线 extract-auto")
    ok("确认卡是「是/否」形态（data-act=yes/no）") if \
        'data-act="yes"' in js and 'data-act="no"' in js \
        else bad("确认卡不是是/否形态")
    eng = open(ENGINE_PY, encoding="utf-8").read()
    ok("LLM defaults 含 ai_record_authorized 且默认开启") if \
        _record_auth_default_on(eng) else bad("授权开关缺失或默认关闭")
    ok("extract-auto 端点校验授权（not_authorized）") if \
        "not_authorized" in ai_py and "ai_record_authorized" in ai_py \
        else bad("端点没有授权校验")

    # S12 老通道关闭（2026-10-17 用户拍板）：对话 chat 不再注入 create_* 写工具——
    # 模型自主直写没有确认框，与「每次记录前都要弹框」冲突，还会和确认卡
    # 重复写入。记录统一走 extract-auto → 确认卡 → 用户点「记下来」。
    tools_py = open(os.path.join(BACKEND, "ai_tools.py"), encoding="utf-8").read()
    ok("对话端点调用 get_tool_definitions(include_create_records=False)") if \
        "get_tool_definitions(baby_id, include_create_records=False)" in ai_py else \
        bad("对话端点又把 create_* 写工具注回去了（或传参丢了）")
    ok("get_tool_definitions 有 create_* 过滤实现") if \
        "include_create_records" in tools_py and 'startswith("create_")' in tools_py else \
        bad("工具过滤实现缺失（参数收了不干活/被删）")
    ok("SYSTEM_PROMPT 有「不能直接创建记录」的假成功防线") if \
        "声称就是假成功" in eng else \
        bad("提示词没拦住模型「嘴上记录」（没工具却声称已记录）")
    return types


# ============================================================
# E2E 部分：假厂商 + Flask test_client
# ============================================================

def e2e_checks(types):
    print("\n=== E. 端到端（假厂商） ===")
    srv, PORT = mk.start_mock()
    URL = "http://127.0.0.1:%d/v1/chat/completions" % PORT
    try:
        server.init_db()
        client = server.app.test_client()
        with client.session_transaction() as sess:
            sess["authenticated"] = True
            sess["is_admin"] = True
            sess["user_id"] = 1
        r = client.post("/api/babies", json={"name": "帮写UI", "birthday": "2026-04-01"})
        BID = (r.get_json() or {}).get("data", {}).get("id")

        ALL_EPS = ["/api/ai/write/extract-feeding", "/api/ai/write/extract-sleep",
                   "/api/ai/write/extract-diaper", "/api/ai/write/extract-growth",
                   "/api/ai/write/extract-temperature", "/api/ai/write/extract-medication",
                   "/api/ai/write/extract-vaccine", "/api/ai/write/extract-pumping",
                   "/api/ai/write/extract-milestone", "/api/ai/write/extract-teething",
                   "/api/ai/write/extract-allergy-test", "/api/ai/write/extract-auto",
                   "/api/ai/write/diary",
                   "/api/ai/write/full-diary", "/api/ai/write/weekly-summary",
                   "/api/ai/write/suggest-milestones", "/api/ai/write/photo-description"]

        # E1 未配置：17 个端点全部 400「请先配置 AI 大模型」
        # （前端 aiwFail 靠这句话里的「配置」显示「去配置」入口）
        not_cfg = []
        for ep in ALL_EPS:
            r = client.post(ep, json={"input": "测试", "age_months": 6,
                                      "gender": "boy", "recent_performance": "",
                                      "baby_id": BID, "baby_name": "帮写UI",
                                      "weekly_data": {"feeding_count": 3}})
            j = r.get_json() or {}
            if r.status_code != 400 or "配置" not in str(j.get("message") or ""):
                not_cfg.append((ep.split("/")[-1], r.status_code,
                                str(j.get("message"))[:40]))
        ok("未配置 LLM：17 个端点都回 400「请先配置」") if not not_cfg \
            else bad("有端点未配置时不像预期", not_cfg)

        # 配置假厂商
        r = client.post("/api/ai/llm/configure", json={
            "provider": "deepseek", "api_key": "sk-mock-1234567890",
            "model": "deepseek-flash", "api_url": URL, "consent_cloud": True,
            "timeout": 3, "custom_models": []})
        if not (r.get_json() or {}).get("success"):
            bad("配置假厂商失败", r.get_json())
            return

        def sc(mode):
            mk.set_mode(mode)
            ai_client.reset_circuit_breaker()
            ai_client.clear_ai_cache()

        # E2 11 个 extract 成功 + 收集 record 键
        records = {}
        sc("json-ok")
        for k, c in sorted(types.items()):
            r = client.post(c["extract_url"], json={"input": "今天喂了180ml母乳"})
            j = r.get_json() or {}
            rec = j.get("record")
            if j.get("success") and isinstance(rec, dict):
                records[k] = set(rec.keys())
            else:
                bad("%s 提取失败" % k, j)
        ok("11 个 extract 端点（json-ok）全部成功返回 record") \
            if len(records) == len(types) else bad("有 extract 没成功", len(records))

        # E2b 对话内直接记录（extract-auto，2026-10-17 新增）
        # 正向：认得出类型 + 给出 record（json-auto 假厂商）
        sc("json-auto")
        j = client.post("/api/ai/write/extract-auto",
                        json={"input": "刚喝了120ml奶粉"}).get_json() or {}
        ok("extract-auto 识别出 feeding 并返回 record") if \
            j.get("success") and j.get("type") == "feeding" and isinstance(j.get("record"), dict) \
            else bad("extract-auto 识别失败", j)
        # 反向①：授权关掉 ⇒ 403 not_authorized（只靠前端不弹框等于没有授权）
        r = client.post("/api/ai/llm/configure", json={
            "provider": "deepseek", "consent_cloud": True, "ai_record_authorized": False})
        if not (r.get_json() or {}).get("success"):
            bad("关闭授权失败", r.get_json())
        r = client.post("/api/ai/write/extract-auto",
                        json={"input": "刚喝了120ml奶粉"})
        j = r.get_json() or {}
        ok("授权关闭：extract-auto 403 not_authorized") if \
            r.status_code == 403 and j.get("not_authorized") is True \
            else bad("授权关闭后端点仍放行", (r.status_code, str(j)[:80]))
        # 恢复授权（后面的检查还要用）
        r = client.post("/api/ai/llm/configure", json={
            "provider": "deepseek", "consent_cloud": True, "ai_record_authorized": True})
        if not (r.get_json() or {}).get("success"):
            bad("恢复授权失败", r.get_json())
        # 反向②：模型抽不出结构（json-bad）⇒ 422 无法识别，不 500
        sc("json-bad")
        r = client.post("/api/ai/write/extract-auto",
                        json={"input": "今天天气不错"})
        j = r.get_json() or {}
        ok("json-bad：extract-auto 422『无法识别』不 500") if \
            r.status_code == 422 and "无法识别" in str(j.get("message") or "") \
            else bad("extract-auto 失败语义不对", (r.status_code, str(j)[:80]))

        # E3 5 个生成端点成功
        gen = {"/api/ai/write/diary": {"input": "今天很开心", "baby_id": BID},
               "/api/ai/write/full-diary": {"input": "今天很开心", "baby_id": BID},
               "/api/ai/write/weekly-summary": {"baby_name": "帮写UI", "age_months": 6,
                                                "weekly_data": {"feeding_count": 3}},
               "/api/ai/write/suggest-milestones": {"age_months": 6, "gender": "boy",
                                                    "recent_performance": ""},
               "/api/ai/write/photo-description": {"baby_name": "帮写UI", "age_months": 6,
                                                   "photo_time": "", "location": "",
                                                   "notes": ""}}
        gen_bad = []
        for ep, body in gen.items():
            sc("json-ok")
            j = client.post(ep, json=body).get_json() or {}
            if not j.get("success"):
                gen_bad.append((ep.split("/")[-1], str(j.get("message"))[:40]))
        ok("5 个生成端点（json-ok）全部成功") if not gen_bad else bad("生成端点失败", gen_bad)

        # E4 record 键 ⊆ 表单键（prepare 消费的中间键除外）
        gaps = {}
        for k, keys in records.items():
            c = types[k]
            allowed = set(c["fields"])
            if c["time_key"]:
                allowed.add(c["time_key"])
            if c["extra_time_key"]:
                allowed.add(c["extra_time_key"])
            if c["has_prepare"]:
                allowed.update(c["prepare_refs"])
            lost = sorted(keys - allowed)
            if lost:
                gaps[k] = lost
        ok("11 类 record 键全部落在确认表单里（无静默丢失）") if not gaps \
            else bad("AI 提了但用户看不到的字段", gaps)

        # E5 失败语义不串味
        sc("json-bad")
        j = client.post("/api/ai/write/extract-feeding",
                        json={"input": "今天喂了180ml母乳"}).get_json() or {}
        msg = str(j.get("message") or "")
        ok("json-bad：只说『无法识别』，不把故障措辞塞进来") \
            if "无法识别" in msg and "调用失败" not in msg else bad("不可解析文案不对", msg)
        sc("err401")
        j = client.post("/api/ai/write/extract-feeding",
                        json={"input": "今天喂了180ml母乳"}).get_json() or {}
        msg = str(j.get("message") or "")
        ok("err401：message 带真实原因 HTTP 401") if "HTTP 401" in msg \
            else bad("401 没带真实原因", msg[:100])

        # E6 /api/health/milestones 落库闭环
        # （crud_roundtrip 只覆盖 /api/babies/<id>/milestones，漏了这条；
        #   确认卡里 milestone 的 save 用的正是它）
        from datetime import date
        today = date.today().isoformat()
        j = client.post("/api/health/milestones/%d" % BID, json={
            "milestone_name": "对话记录门禁测试", "category": "motor",
            "status": "achieved", "achieved_date": today}).get_json() or {}
        if not j.get("success"):
            bad("/api/health/milestones POST 失败", j)
        else:
            r = client.get("/api/health/milestones/%d" % BID)
            data = (r.get_json() or {}).get("data") or []
            names = [x.get("milestone_name") for x in data if isinstance(x, dict)]
            ok("/api/health/milestones 落库 + 读回闭环") \
                if "对话记录门禁测试" in names else bad("落库后读不到", names[:5])

        # E7 sleep is_nap 落库可见（2026-10-14 修复回归）
        j = client.post("/api/babies/%d/sleep" % BID, json={
            "start_time": today + " 13:00:00", "end_time": today + " 14:30:00",
            "is_nap": 1, "note": "对话记录门禁"}).get_json() or {}
        if not j.get("success"):
            bad("sleep 带 is_nap=1 落库失败", j)
        else:
            r = client.get("/api/babies/%d/sleep" % BID)
            data = (r.get_json() or {}).get("data") or []
            hit = [x for x in data if isinstance(x, dict)
                   and x.get("note") == "对话记录门禁"]
            ok("sleep is_nap=1 落库后读回值一致") \
                if hit and hit[0].get("is_nap") == 1 else \
                bad("is_nap 读回不一致", hit[:1])

        # E8 老通道关闭的端到端铁证（2026-10-17）：对话请求真正发往厂商的
        # tools 里不能有 create_*（静态判据只证明「代码写了」，这里证明
        # 「真链路发出去的就是过滤版」；查询类仍在，防「过滤过头把工具全关」）
        sc("ok")
        j = client.post("/api/ai/chat", json={
            "message": "宝宝今天该注意什么", "baby_id": BID,
            "enable_tools": True, "max_tool_rounds": 5}).get_json() or {}
        if not j.get("llm_used"):
            bad("E8：对话请求没走到假厂商，无法验证工具清单",
                str(j.get("fallback_reason"))[:80])
        else:
            seen = [s for s in mk._SEEN
                    if str(s.get("path") or "").endswith("/chat/completions")]
            names = (seen[-1].get("tool_names") or []) if seen else []
            got_create = [n for n in names if str(n).startswith("create_")]
            ok("E8：对话实际发出的工具清单不含 create_*（真实链路）") if names and not got_create \
                else bad("对话仍把 create_* 工具发了出去", got_create or names)
            ok("E8：查询类工具仍在（不是把工具全关了）") if "query_baby_info" in names \
                else bad("查询工具也没了（过滤过头）", names)
    finally:
        srv.shutdown()


# ============================================================
# 内置负向自检：破坏代码后判据必须转红（防判据自己失灵假绿）
# ============================================================

def selftest(js):
    print("\n=== T. 内置负向自检（破坏 → 必须转红） ===")
    rc = 0

    def expect_red(label, mutant, check_fn):
        nonlocal rc
        try:
            red = check_fn(mutant)
        except Exception as e:
            red = False
            print("      （自检执行异常：%r）" % e)
        if red:
            print("  [OK]   破坏后转红：%s" % label)
        else:
            print("  [FAIL] 破坏后仍绿：%s —— 判据失灵" % label)
            rc = 2

    def types_of(src):
        try:
            return parse_aiw_types(src)
        except Exception:
            return None

    # T1 删掉 sleep 的 is_nap 字段 ⇒ E4「record 键 ⊆ 表单键」必须红
    m1 = js.replace("{ key: 'is_nap', label: '是否小憩', type: 'select', "
                    "options: [['', '自动判断'], ['1', '小憩'], ['0', '夜间睡眠']], "
                    "fallback: '' },\n            ", "")
    def ck1(src):
        t = types_of(src)
        if not t or "sleep" not in t:
            return False
        c = t["sleep"]
        allowed = set(c["fields"]) | {c["time_key"], c["extra_time_key"]}
        # 后端 extract_sleep_record 的 record 键（照 ai_writer.py 的模板）
        backend_keys = {"start_time", "end_time", "duration_minutes",
                        "sleep_quality", "is_nap", "note"}
        return bool(backend_keys - allowed)
    expect_red("sleep 表单删 is_nap ⇒ record 键比对红", m1, ck1)

    # T2 save 读 v.toothName（收集不到）⇒ 「收集不到的键」必须红
    m2 = js.replace("tooth_name: aiwToothName(v.tooth_code),",
                    "tooth_name: v.toothName || '',")
    def ck2(src):
        t = types_of(src)
        if not t or "teething" not in t:
            return False
        c = t["teething"]
        collect = set(c["fields"]) | {c["time_key"]}
        return any(x not in collect for x in c["save_refs"])
    expect_red("teething save 读收集不到的键 ⇒ 双向对齐红", m2, ck2)

    # T3 extract URL 改动态拼接 ⇒ 「字面 URL」必须红
    # ⚠ 检查函数必须与主判据 S3 同构：主判据是「含 ${ **或** 为空」，
    # 只判「含 ${」会在「URL 被改成模板串后正则抓不到、extract_url 变空」
    # 时漏红（第一版自检就这么失真过）。
    m3 = js.replace("return api('/api/ai/write/extract-feeding', {",
                    "return api(`/api/ai/write/extract-${'feeding'}`, {")
    def ck3(src):
        t = types_of(src)
        if not t or "feeding" not in t:
            return False
        u = t["feeding"]["extract_url"]
        return ("${" in u) or (not u)
    expect_red("extract URL 拼路径 ⇒ 字面 URL 断言红", m3, ck3)

    # T4 类型少一个（diaper）⇒ 类型数断言必须红
    # ⚠ 删除范围必须限定在 AIW_TYPES 体内：`    diaper: {` 这个缩进模式
    # 在文件里别处也有（dashboard 的 Promise.allSettled 附近就有一个），
    # 全文 sub 会从那里一下删掉几十万字符、把判据自己搞崩，
    # 异常被 expect_red 当成「没转红」—— 破坏失控比不破坏更坏。
    _aiw_start = js.index("const AIW_TYPES = {")
    _aiw_body, _ = block_of(js, js.index("{", _aiw_start))
    _m4 = re.sub(r"    diaper: \{[\s\S]*?\n    \},\n", "", _aiw_body, count=1)
    m4 = js[:_aiw_start] + "const AIW_TYPES = {" + _m4 + js[js.index("{", _aiw_start) + len(_aiw_body) + 1:]
    def ck4(src):
        t = types_of(src)
        return t is not None and len(t) != 11
    expect_red("删掉 diaper 类 ⇒ 类型数断言红", m4, ck4)

    # T5 sendMsg 不接线 extract-auto（改成动态拼接）⇒ S11 接线断言必须红
    m5 = js.replace("api('/api/ai/write/extract-auto', {",
                    "api('/api/ai/write/extract-' + 'auto', {")
    def ck5(src):
        try:
            region = src[src.index("function sendMsg"):src.index("function renderBotText")]
        except ValueError:
            return False
        return not ("/api/ai/write/extract-auto" in region and "renderRecordConfirmCard" in region)
    expect_red("sendMsg 不接线 extract-auto ⇒ 接线断言红", m5, ck5)

    # T6 授权默认关闭 ⇒ 「默认开启」断言必须红（新功能默认不能用就是坏的）
    m6 = open(ENGINE_PY, encoding="utf-8").read().replace(
        "'ai_record_authorized': True,", "'ai_record_authorized': False,")
    def ck6(_src):
        return not _record_auth_default_on(m6)
    expect_red("授权默认关闭 ⇒ 默认开启断言红", js, ck6)

    # T7 确认卡不是「是/否」形态 ⇒ 形态断言必须红
    m7 = js.replace('data-act="yes"', 'data-act="confirm"')
    def ck7(src):
        return not ('data-act="yes"' in src and 'data-act="no"' in src)
    expect_red("确认卡改成单按钮 ⇒ 是/否形态断言红", m7, ck7)

    # T8 对话端点把 include_create_records 改回 True / 传参丢失 ⇒ S12 必须红
    m8 = open(AI_PY, encoding="utf-8").read().replace(
        "include_create_records=False", "include_create_records=True")
    def ck8(_src):
        return "get_tool_definitions(baby_id, include_create_records=False)" not in m8
    expect_red("对话写工具注回去 ⇒ S12 转红", js, ck8)

    # T9 过滤实现被破坏（startswith 判断没了）⇒ S12 必须红
    m9 = open(os.path.join(BACKEND, "ai_tools.py"), encoding="utf-8").read().replace(
        'startswith("create_")', 'startswith("zzz_")')
    def ck9(_src):
        return not ('include_create_records' in m9 and 'startswith("create_")' in m9)
    expect_red("工具过滤实现被破坏 ⇒ S12 转红", js, ck9)

    # T10 导航「ai-writer」入口复活 ⇒ nav_layout 的反向断言必须红
    # （本门禁 S1 判 section/init/case；nav 归 verify_nav_layout 判，这里
    #  只做破坏对照，检查函数与那边同构：ai-writer 不得出现在记录栏段内）
    _anchor = "{ page: 'health', label: '健康档案', icon: 'icon-heart2' },"
    m10 = js.replace(
        _anchor,
        _anchor + "\n                { page: 'ai-writer', label: 'AI 帮写', icon: 'icon-robot' },")
    def ck10(src):
        try:
            blk = src[src.index("const NAV_HUBS = ["):]
            blk = blk[:blk.index("\n];")]
        except ValueError:
            return False
        return "'ai-writer'" in blk
    expect_red("导航入口复活 ⇒ 入口已撤断言红", m10, ck10)

    return rc


def main():
    js = open(JS_PATH, encoding="utf-8").read()
    html = open(HTML_PATH, encoding="utf-8").read()
    css = open(CSS_PATH, encoding="utf-8").read()
    types = static_checks(js, html, css)
    if types:
        e2e_checks(types)
    rc = 0
    if "--selftest" in sys.argv:
        rc = selftest(js)
    print("\n" + "=" * 46)
    print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
    print("=" * 46)
    if FAIL:
        print("失败明细：")
        for f in FAIL:
            print("  - " + f)
        return 1
    if rc:
        return rc
    print("对话内直接记录门禁通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
