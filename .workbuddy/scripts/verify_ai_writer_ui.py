#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI 帮写 UI 门禁 —— 静态结构 + 假厂商端到端 + 内置负向自检。

背景（2026-10-14，用户拍板「AI 帮写要做 UI」）：
  18 个 /api/ai/write/* 端点自 v0.0.1 起就在后端，前端从未接 UI。本次补齐：
  记录栏新增第 7 组「智能」→ page-ai-writer（一句话记录 / 日记帮写 /
  本周总结 / 里程碑建议 四张卡）+ 相册大图弹窗挂「AI 描述」按钮。
  2026-10-17 新增对话内直接记录（小萌浮窗说话就能记）：第 18 个端点
  extract-auto 自动认类型，配 ai_record_authorized 授权开关（默认开启）。

本门禁防的是这几类**静默**缺陷（都是读代码看不出来、用户点了没反应
或数据凭空消失的那类）：
  S1. 入口/落点/case 缺一样 ⇒ 点进「AI 帮写」白屏（nav_layout 已判导航
      结构与落点，这里不重复判，只判绑定与页面内部 id 齐备）
  S2. AIW_TYPES 每类少配一项（label/placeholder/extract/fields/timeField/save）
      ⇒ 该类 chips 点了没反应或识别直接抛异常
  S3. extract URL 拼路径（`/api/ai/write/extract-${type}`）⇒
      check_frontend_api_exists 判「后端没这条路由」（它的口径只认字面 URL）
  S4. 字段双向错位：fields 配了 save 不用（用户改了没保存）/ save 读的键
      不在收集范围（v.tooth_name 这类 —— 实测真有过，落库恒为空）
  S5. 后端 extract 返回的 record 键不在确认表单里 ⇒ AI 提了用户看不到，
     也不落库（静默丢失）。例外只有一种：prepare 消费的中间键
     （teething 的 tooth_position/tooth_name），且 prepare 必须真的引用它
  S6. 绑定表写法（getElementById(pair[0]) 非字面）是 verify_event_bindings
     的口径盲区 ⇒ 这里显式判绑定表覆盖的 id 都有静态产地
  E1. 未配置 LLM：17 个端点都必须 400「请先配置 AI 大模型」（前端靠这句话
      显示「去配置」入口）
  E2. 配置后 11 extract + 5 生成端点全部可用（假厂商 json-ok）
  E2b. 对话内直接记录：extract-auto 认得出 feeding（json-auto）；
      授权关掉必须 403 not_authorized（授权不能只靠前端不弹框）
  E3. 失败语义不串味：json-bad ⇒ 422「无法识别」且不带「调用失败」；
     err401 ⇒ message 带「HTTP 401」
  E4. record 键 ⊆ 表单键（同 S5，但用真跑出来的 record，不读代码猜）
  E5. /api/health/milestones 落库闭环（crud_roundtrip 只覆盖
     /api/babies/<id>/milestones，漏了 AI 帮写实际用的这条）
  E6. sleep 的 is_nap 落库可见（2026-10-14 修复的回归：AI 提了 is_nap
     却没进表单，用户既看不到也没法纠正）

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
    # S1 section 与页面内 id
    m = re.search(r'<section class="page" id="page-ai-writer">(.*?)</section>', html, re.S)
    if not m:
        bad("index.html 没有 #page-ai-writer section")
        return
    sec = m.group(1)
    ok("index.html 有 #page-ai-writer section")
    need_ids = ["aiwTypeChips", "aiwInput", "aiwIdentifyBtn", "aiwActionsHint",
                "aiwConfirmBox", "aiwDiaryInput", "aiwDiaryBtn", "aiwDiaryBodyBtn",
                "aiwDiaryHint", "aiwDiaryResult", "aiwWeeklyBtn", "aiwWeeklyHint",
                "aiwWeeklyResult", "aiwMsInput", "aiwMsBtn", "aiwMsHint", "aiwMsResult"]
    miss = [i for i in need_ids if ('id="%s"' % i) not in sec]
    ok("section 内 17 个页面 id 齐全") if not miss else bad("section 缺 id", miss)
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

    # S5 依赖符号（缺一个运行时就抛）
    deps = ["function authorSelectHtml", "function gotoAISettings",
            "FEEDING_TYPE_OPTIONS =", "const TEETH_DATA", "function toLocalDatetime",
            "function getToday", "function calcAgeMonths", "function readAuthor",
            "photoPageState", "photoViewCurrentId", "function showDiaryModal",
            "function escapeHtml", "function aiwToothName", "function showToast"]
    miss = [d for d in deps if d not in js]
    ok("模块依赖的 14 个符号都在") if not miss else bad("依赖符号缺失", miss)

    # S6 section 内用到的 aiw-* class 必须在 CSS 有定义（凭空造类名 = 样式裸奔）
    # ⚠ 只从 class 上下文提取：data-aiw-ms-add 这类 data 属性名不是 class，
    # 全文抓 aiw- 前缀会把它们误算成「未定义类名」（第一版就这么误报过）。
    used = set()
    for cm in re.finditer(r'class="([^"]*)"', sec):
        used.update(x for x in re.sub(r"\$\{[^}]*\}", " ", cm.group(1)).split()
                    if x.startswith("aiw-"))
    for cm in re.finditer(r'class="([^"]*)"', js):
        used.update(x for x in re.sub(r"\$\{[^}]*\}", " ", cm.group(1)).split()
                    if x.startswith("aiw-"))
    for cm in re.finditer(r"className\s*=\s*'([^']*)'", js):
        used.update(x for x in re.sub(r"\$\{[^}]*\}", " ", cm.group(1)).split()
                    if x.startswith("aiw-"))
    undef = sorted(c for c in used if ("." + c) not in css)
    ok("section/JS 用到的 %d 个 aiw-* 类都有 CSS 定义" % len(used)) if not undef \
        else bad("aiw-* 类没有 CSS 定义（图标/排版裸奔）", undef)

    # S7 绑定表覆盖（verify_event_bindings 的 BOUND_RE 只认字面 id，
    # getElementById(pair[0]) 这种绑定表写法是它的口径盲区）
    init_blk = js[js.index("function initAIWriterPage("):]
    init_blk = init_blk[:init_blk.index("\n}")]
    static_ids = set(re.findall(r'id="([\w-]+)"', html))
    for bid in ["aiwIdentifyBtn", "aiwDiaryBtn", "aiwDiaryBodyBtn",
                "aiwWeeklyBtn", "aiwMsBtn"]:
        in_table = ("'%s'" % bid) in init_blk
        has_origin = bid in static_ids
        if not (in_table and has_origin):
            bad("绑定表缺 %s 或它没有静态产地" % bid,
                {"在绑定表": in_table, "静态 html 有": has_origin})
    ok("initAIWriterPage 绑定表覆盖 5 个静态 id（event_bindings 口径盲区）") \
        if not any("绑定表缺" in f for f in FAIL) else None
    ok("initPhotoModal 绑了 aiPhotoDescBtn") if "aiPhotoDescBtn" in js \
        and "aiwGeneratePhotoDesc" in js else bad("相册 AI 描述按钮没绑")

    # S8 委托属性同源（动态控件靠 data 属性 + 事件委托）
    pairs = [("data-aiw-type", js.count("data-aiw-type")),
             ("data-aiw-ms-add", js.count("data-aiw-ms-add")),
             ("data-aiw-ms", js.count("data-aiw-ms"))]
    for attr, cnt in pairs:
        ok("委托属性 %s 渲染/委托两处都出现（%d 次）" % (attr, cnt)) if cnt >= 2 \
            else bad("委托属性 %s 只出现 %d 次（渲染与委托必须成对）" % (attr, cnt))

    # S9 时间归一与失败分支
    ok("aiwToDbTime 把 T 换空格并补 :00") if \
        re.search(r"replace\('T', ' '\)\s*\+\s*':00'", js) else \
        bad("aiwToDbTime 的时间归一不见了")
    ok("aiwFail 命中『配置』时给『去配置』入口") if \
        re.search(r"indexOf\('配置'\)\s*>=\s*0", js) and "aiw-goto-config" in js \
        else bad("缺配置时没有给用户「去配置」入口")
    ok("loadPageData 认 ai-writer 且调 initAIWriterPage") if \
        re.search(r"case 'ai-writer':\s*initAIWriterPage\(\)", js) else \
        bad("loadPageData 没接 ai-writer（点进去白屏）")
    # catch 计数从模块第一个函数（aiwNowLocal）切起 —— 从 aiwIdentify 切
    # 会漏掉定义在它前面的 aiwSavePending 的 catch（第一版就这么少数了一个）
    n_catch = len(re.findall(r"\.catch\(", js[js.index("function aiwNowLocal"):]))
    ok("AI 帮写模块的异步调用都有 catch（%d 处）" % n_catch) if n_catch >= 8 \
        else bad("AI 帮写模块 catch 偏少（网络错会静默卡死）", n_catch)

    # S10 后端 17 个 ai/write 端点都在（前端不用 ≠ 后端能删；这里是防回归）
    # 2026-10-17：16 → 17（新增 extract-auto，对话内直接记录用）
    ai_py = open(AI_PY, encoding="utf-8").read()
    eps = re.findall(r"@bp\.route\('/api/ai/write/([\w-]+)'", ai_py)
    ok("后端 ai/write 端点 17 个") if len(eps) == 17 else \
        bad("后端 ai/write 端点数变了", len(eps))
    front_eps = set()
    for k, c in types.items():
        front_eps.add(c["extract_url"].split("/")[-1])
    front_eps.update(["diary", "full-diary", "weekly-summary",
                      "suggest-milestones", "photo-description"])
    miss = sorted(front_eps - set(eps))
    ok("前端调用的 16 个端点后端都有") if not miss else bad("前端调了后端没有的", miss)

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
        #   AI 帮写 milestone 的 save 用的正是它）
        from datetime import date
        today = date.today().isoformat()
        j = client.post("/api/health/milestones/%d" % BID, json={
            "milestone_name": "AI帮写门禁测试", "category": "motor",
            "status": "achieved", "achieved_date": today}).get_json() or {}
        if not j.get("success"):
            bad("/api/health/milestones POST 失败", j)
        else:
            r = client.get("/api/health/milestones/%d" % BID)
            data = (r.get_json() or {}).get("data") or []
            names = [x.get("milestone_name") for x in data if isinstance(x, dict)]
            ok("/api/health/milestones 落库 + 读回闭环") \
                if "AI帮写门禁测试" in names else bad("落库后读不到", names[:5])

        # E7 sleep is_nap 落库可见（2026-10-14 修复回归）
        j = client.post("/api/babies/%d/sleep" % BID, json={
            "start_time": today + " 13:00:00", "end_time": today + " 14:30:00",
            "is_nap": 1, "note": "AI帮写门禁"}).get_json() or {}
        if not j.get("success"):
            bad("sleep 带 is_nap=1 落库失败", j)
        else:
            r = client.get("/api/babies/%d/sleep" % BID)
            data = (r.get_json() or {}).get("data") or []
            hit = [x for x in data if isinstance(x, dict)
                   and x.get("note") == "AI帮写门禁"]
            ok("sleep is_nap=1 落库后读回值一致") \
                if hit and hit[0].get("is_nap") == 1 else \
                bad("is_nap 读回不一致", hit[:1])
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
    print("AI 帮写 UI 门禁通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
