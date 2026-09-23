# -*- coding: utf-8 -*-
"""时间线扩容离线验证（临时库，零副作用）

背景：本次把 /api/babies/<id>/timeline 从「5 类硬编码 if/elif」改成「_TIMELINE_SOURCES
表驱动 + 18 类」。这个改法最大的风险不是报错，而是**静默少数据**：表名/列名写错时
SQL 会抛异常，被 per-table 的 try 吞掉只留一条 warning，接口照样 200 返回，
前端看到的只是「这类没有记录」——不查日志根本发现不了。

所以本脚本的重点不是「接口通不通」，而是：
  1. 拿 init_db + _run_migrations 建出的**真实 schema**，逐个核对 18 个数据源的
     表名、时间列、每个字段列是否存在（写错一个就 FAIL，且指名道姓）
  2. 18 类端到端都能查出来，一个都不少（真实数据下走完整查询路径）
  3. type 参数服务端筛选生效（原来前端本地筛，会被 limit 截断）
  4. from/to 日期段生效（「看一个月前」的核心）
  5. limit 生效且超上限被夹住不报错
  6. 原有 5 类的**返回字段名逐字未变**（前端 typeMap/getTimelineDetail 靠它渲染）
  7. 未接种疫苗不进时间线（只有 scheduled_date，挂上会误以为打过了）
  8. 负向对照：故意写错列名 → 该类型被跳过且写 warning，其余类型不受影响
     （证明第 2 条的「全绿」不是因为它抓不到东西）
"""
import os
import re
import sys
import types
import inspect
import tempfile
import sqlite3
import logging

BACKEND = os.path.dirname(os.path.abspath(__file__))

# Windows 没有 fcntl，导入后端前必须打桩
_fcntl = types.ModuleType("fcntl")
_fcntl.LOCK_EX, _fcntl.LOCK_NB, _fcntl.LOCK_UN = 2, 4, 8
_fcntl.flock = lambda *a, **k: None
sys.modules.setdefault("fcntl", _fcntl)

# 数据目录指向临时目录 —— 全程不碰真实库
os.environ["BABYCARE_DATA_DIR"] = tempfile.mkdtemp(prefix="verify_tl_")
os.environ["BABYCARE_CONFIG_DIR"] = tempfile.mkdtemp(prefix="verify_tl_cfg_")

sys.path.insert(0, os.path.join(BACKEND, "vendor"))
sys.path.insert(0, BACKEND)

import constants  # noqa: E402
import server  # noqa: E402

PASSED, FAILED = [], []


def check(name, cond, detail=None):
    (PASSED if cond else FAILED).append(name)
    line = ("PASS  " if cond else "FAIL  ") + name
    if detail is not None and not cond:
        line += "\n        <- " + str(detail)
    print(line)


# ==================== 建库（真实 schema） ====================
server.init_db()
server._run_migrations()          # 生产启动会跑，漏了会误判「缺列」
server._init_done = True          # 跳过请求触发的惰性初始化，保证库就是上面这份

from blueprints import analytics  # noqa: E402

DB = constants.DB_PATH
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

SOURCES = analytics._TIMELINE_SOURCES
SQL_KEYWORDS = {"is", "not", "null", "and", "or", "in", "like", "1"}


def idents(expr):
    """从 SQL 片段里抠出标识符，过滤掉关键字"""
    return [w for w in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", expr or "")
            if w.lower() not in SQL_KEYWORDS]


def table_columns(con, table):
    try:
        return {r[1] for r in con.execute("PRAGMA table_info(%s)" % table)}
    except sqlite3.Error:
        return None


# ==================== 1. schema 交叉核对（本次最有价值的一条） ====================
bad_refs = []
for type_key, table, time_col, columns, extra_where in SOURCES:
    cols = table_columns(conn, table)
    if cols is None:
        bad_refs.append("%s: 表 %s 不存在" % (type_key, table))
        continue
    need = [time_col] + [expr for _alias, expr in columns] + idents(extra_where)
    missing = sorted({c for c in need if c not in cols})
    if missing:
        bad_refs.append("%s(%s): 缺列 %s" % (type_key, table, ",".join(missing)))

check("18 个数据源的表名/列名全部对得上真实 schema（共 %d 类）" % len(SOURCES),
      not bad_refs, bad_refs)

# 类型标识不能重复（重复会让前端 typeMap 和筛选语义都错乱）
type_keys = [s[0] for s in SOURCES]
check("数据源类型标识无重复", len(type_keys) == len(set(type_keys)),
      [t for t in set(type_keys) if type_keys.count(t) > 1])


# ==================== 2. 造数据（每类一条，另加跨越月份的边界样本） ====================
conn.execute("INSERT INTO babies (id, name, birthday, gender) VALUES (1, '验证宝宝', '2025-06-01', 'boy')")

D_MAIN = "2026-09-20"
D_OLD = "2026-08-15"

conn.execute("""INSERT INTO feeding_records
    (baby_id, start_time, end_time, feeding_type, amount, side, note)
    VALUES (1, ?, ?, 'bottle', 120, 'both', '验证喂养')""", (D_MAIN + " 10:00:00", D_MAIN + " 10:20:00"))
# 一个月前那条：用来验 from/to 真的能把旧记录捞出来
conn.execute("""INSERT INTO feeding_records
    (baby_id, start_time, end_time, feeding_type, amount, side, note)
    VALUES (1, ?, ?, 'breast', 0, 'left', '一个月前')""", (D_OLD + " 08:00:00", D_OLD + " 08:15:00"))
conn.execute("""INSERT INTO sleep_records
    (baby_id, start_time, end_time, duration_minutes, sleep_quality, is_nap, note)
    VALUES (1, ?, ?, 90, 'good', 1, '验证睡眠')""", (D_MAIN + " 13:00:00", D_MAIN + " 14:30:00"))
conn.execute("""INSERT INTO diaper_records (baby_id, change_time, diaper_type, color, note)
    VALUES (1, ?, 'wet', 'yellow', '验证尿布')""", (D_MAIN + " 09:00:00",))
conn.execute("""INSERT INTO growth_records
    (baby_id, record_date, height, weight, head_circumference, bmi, note)
    VALUES (1, ?, 75.5, 9.8, 45.0, 17.2, '验证成长')""", (D_MAIN,))
conn.execute("""INSERT INTO milestones (baby_id, title, description, achieved_date, category, is_first)
    VALUES (1, '会翻身', '第一次自己翻身', ?, 'motor', 0)""", (D_MAIN,))
conn.execute("""INSERT INTO pumping_records
    (baby_id, pump_time, duration_minutes, left_amount, right_amount, total_amount, pump_type, note)
    VALUES (1, ?, 20, 60, 50, 110, 'electric', '验证吸奶')""", (D_MAIN + " 06:30:00",))
conn.execute("""INSERT INTO solid_food_records
    (baby_id, food_name, food_category, first_try_date, amount, reaction, note)
    VALUES (1, '西兰花', 'vegetable', ?, '2勺', 'none', '验证辅食')""", (D_MAIN,))
conn.execute("""INSERT INTO tummy_time_records
    (baby_id, start_time, end_time, duration_minutes, milestone, note)
    VALUES (1, ?, ?, 10, '抬头稳', '验证趴睡')""", (D_MAIN + " 15:00:00", D_MAIN + " 15:10:00"))
conn.execute("""INSERT INTO teething_records (baby_id, tooth_name, erupt_date, position, side, note)
    VALUES (1, '下中切牙', ?, '下颌', 'left', '验证出牙')""", (D_MAIN,))
conn.execute("""INSERT INTO fontanelle_records
    (baby_id, check_date, anterior_size, anterior_status, posterior_status, note)
    VALUES (1, ?, 1.5, 'closing', 'closed', '验证囟门')""", (D_MAIN,))
conn.execute("""INSERT INTO leap_records (baby_id, leap_number, start_date, end_date, is_completed, note)
    VALUES (1, 5, ?, ?, 1, '验证飞跃期')""", (D_MAIN, D_MAIN))
conn.execute("""INSERT INTO diary_entries (baby_id, title, content, entry_date, mood)
    VALUES (1, '今天很开心', '宝宝笑了一下午', ?, 'happy')""", (D_MAIN,))
conn.execute("""INSERT INTO photos (baby_id, photo_date, description, file_path, is_cover)
    VALUES (1, ?, '第一次去公园', '/photos/a.jpg', 0)""", (D_MAIN,))
conn.execute("""INSERT INTO temperature_records
    (baby_id, temperature, measure_time, measure_method, is_fever, note)
    VALUES (1, 37.2, ?, 'ear', 0, '验证体温')""", (D_MAIN + " 20:00:00",))
conn.execute("""INSERT INTO medication_records
    (baby_id, medication_name, dosage, dosage_unit, measure_time, note)
    VALUES (1, '布洛芬混悬液', '2.5', 'ml', ?, '验证用药')""", (D_MAIN + " 20:30:00",))
# 已接种 1 条 + 未接种 1 条：后者必须不进时间线
conn.execute("""INSERT INTO vaccine_details
    (baby_id, vaccine_name, vaccine_type, dose_number, actual_date, status, hospital, note)
    VALUES (1, '乙肝疫苗', 'free', 2, ?, 'completed', '社区医院', '验证疫苗')""", (D_MAIN,))
conn.execute("""INSERT INTO vaccine_details
    (baby_id, vaccine_name, vaccine_type, dose_number, scheduled_date, actual_date, status, note)
    VALUES (1, '百白破', 'free', 1, '2026-10-01', NULL, 'pending', '还没打')""")
conn.execute("""INSERT INTO allergy_tests
    (baby_id, food_name, test_date, day_number, has_reaction, reaction_detail, status, note)
    VALUES (1, '鸡蛋', ?, 2, 1, '轻微皮疹', 'failed', '验证过敏')""", (D_MAIN,))
conn.execute("""INSERT INTO checkup_records
    (baby_id, checkup_date, hospital, doctor, result, height, weight, note)
    VALUES (1, ?, '市妇幼', '张医生', 'normal', 75.0, 9.7, '验证体检')""", (D_MAIN,))
conn.commit()

# ==================== 3. 端到端 ====================
server.app.config["PROPAGATE_EXCEPTIONS"] = True
client = server.app.test_client()
with client.session_transaction() as s:
    s["authenticated"] = True

BASE = "/api/babies/1/timeline"


def tl(**kw):
    qs = "&".join("%s=%s" % (k, v) for k, v in kw.items())
    resp = client.get(BASE + ("?" + qs if qs else ""))
    try:
        body = resp.get_json() or {}
    except Exception:
        body = {"raw": resp.get_data(as_text=True)[:200]}
    return resp.status_code, body


code, j = tl(limit=2000)
check("接口返回 200 且 success=True", code == 200 and j.get("success") is True,
      {"code": code, "body": str(j)[:300]})
data = j.get("data") or []

expected_types = {s[0] for s in SOURCES}
got_types = {e["type"] for e in data}
check("18 类全部能查出来，无静默缺失", got_types == expected_types,
      {"缺": sorted(expected_types - got_types), "多": sorted(got_types - expected_types)})

# 每类都恰好 1 条（feeding 有 2 条，单独算）
counts = {}
for e in data:
    counts[e["type"]] = counts.get(e["type"], 0) + 1
singles = {t: c for t, c in counts.items() if c != 1}
check("除 feeding 外每类各 1 条（feeding 应为 2 条）",
      singles == {"feeding": 2}, singles)

check("按时间倒序排列",
      [e["time"] for e in data] == sorted([e["time"] for e in data], reverse=True))

# ---- type 参数：服务端筛选 ----
code, j = tl(type="teething", limit=2000)
d = j.get("data") or []
check("type=teething 只返回出牙（服务端筛选生效）",
      {e["type"] for e in d} == {"teething"} and len(d) == 1,
      {"types": sorted({e["type"] for e in d}), "n": len(d)})

code, j = tl(type="feeding,teething", limit=2000)
d = j.get("data") or []
check("type 支持多值（feeding,teething）",
      {e["type"] for e in d} == {"feeding", "teething"} and len(d) == 3,
      {"types": sorted({e["type"] for e in d}), "n": len(d)})

# ---- from/to：这就是「看一个月前」 ----
code, j = tl(**{"from": "2026-08-01", "to": "2026-08-31", "limit": 2000})
d = j.get("data") or []
check("from/to 能捞出一个月前那条（且只捞它）",
      len(d) == 1 and d[0]["type"] == "feeding" and str(d[0]["time"]).startswith("2026-08-15"),
      d)
check("from/to 返回的条目里含有 8 月记录（跨月取数）",
      any(e["type"] == "feeding" and str(e["time"]).startswith(D_OLD) for e in d), d)

# ---- limit ----
code, j = tl(limit=2)
check("limit=2 只返回 2 条", len(j.get("data") or []) == 2, len(j.get("data") or []))

# ⚠️ _get_int_arg 是「校验式拒绝」：超范围返 400，不是静默夹到上限。
#    所以前端必须保证发出去的值正好等于上限 —— 下面既验契约，也验「前端用的那个值合法」。
code_max, j_max = tl(limit=2000)
check("limit 正好等于上限 2000 时可用（前端就是发这个值）",
      code_max == 200 and j_max.get("success") is True,
      {"code": code_max, "body": str(j_max)[:150]})
code_bad, _ = tl(limit=99999)
check("limit 超上限返回 400（明确拒绝，不是静默截断）", code_bad == 400, code_bad)
code_zero, _ = tl(limit=0)
check("limit=0 返回 400（下限校验生效）", code_zero == 400, code_zero)

# ---- 未接种疫苗必须排除 ----
vacs = [e for e in data if e["type"] == "vaccine"]
check("未接种的疫苗不进时间线（只留已接种那条）",
      len(vacs) == 1 and vacs[0]["vaccine_name"] == "乙肝疫苗",
      [v["vaccine_name"] for v in vacs])

# ---- 原有 5 类字段名逐字未变（前端渲染依赖） ----
EXPECT_KEYS = {
    "feeding": {"id", "type", "time", "subtype", "amount", "side", "note"},
    "sleep": {"id", "type", "time", "end_time", "duration_minutes", "is_nap",
              "sleep_quality", "note"},
    "diaper": {"id", "type", "time", "diaper_type", "color", "note"},
    "growth": {"id", "type", "time", "height", "weight", "head_circumference", "bmi", "note"},
    "milestone": {"id", "type", "time", "title", "description", "category"},
}
for t, keys in EXPECT_KEYS.items():
    ev = next((e for e in data if e["type"] == t), None)
    if ev is None:
        check("原有类型 %s 字段名未变" % t, False, "该类型没查到数据")
        continue
    check("原有类型 %s 字段名未变" % t, set(ev.keys()) == keys,
          {"多": sorted(set(ev.keys()) - keys), "缺": sorted(keys - set(ev.keys()))})

# 新增类型也必须带上「字段名与前端取值对得上」的字段
NEW_TYPE_FIELDS = {
    "pumping": "total_amount", "solidfood": "food_name", "tummy": "duration_minutes",
    "teething": "tooth_name", "fontanelle": "anterior_size", "leap": "leap_number",
    "temperature": "temperature", "medication": "medication_name", "vaccine": "vaccine_name",
    "allergy": "food_name", "checkup": "hospital",
    "diary": "title", "photo": "description",
}
missing_fields = {}
for t, field in NEW_TYPE_FIELDS.items():
    ev = next((e for e in data if e["type"] == t), None)
    if ev is None or field not in ev:
        missing_fields[t] = field
check("新增类型的明细字段都在（前端 getTimelineDetail 靠它）",
      not missing_fields, missing_fields)

# ---- 时间列取值正确：不能是 created_at（否则补录会错位成今天） ----
# leap 用 start_date，把 start_date 与 created_at 设成不同值来验证
conn.execute("UPDATE leap_records SET start_date = '2026-07-01' WHERE baby_id = 1")
conn.commit()
code, j = tl(type="leap", limit=2000)
d = j.get("data") or []
check("时间列取的是事件时间而不是入库时间（leap.start_date）",
      len(d) == 1 and str(d[0]["time"]).startswith("2026-07-01"), d)

# ==================== 4. 负向对照：证明上面全绿不是「抓不到东西」 ====================
log_records = []


class _Capture(logging.Handler):
    def emit(self, record):
        log_records.append(record.getMessage())


_analytics_logger = logging.getLogger("babycare.analytics")
_analytics_logger.addHandler(_Capture())
_analytics_logger.setLevel(logging.DEBUG)

orig_sources = analytics._TIMELINE_SOURCES
teeth_n = counts.get("teething", 0)
try:
    # 把 teething 的时间列故意写错，其余保持原样
    analytics._TIMELINE_SOURCES = [
        ("teething", "teething_records", "no_such_column", [("tooth_name", "tooth_name")], "")
    ] + [s for s in orig_sources if s[0] != "teething"]
    code, j = tl(limit=2000)
    d2 = j.get("data") or []
    t2 = {e["type"] for e in d2}
    check("负向对照：坏列名 → 该类型被跳过，其余类型照常返回",
          code == 200 and "teething" not in t2 and (expected_types - {"teething"}) <= t2
          and len(d2) == len(data) - teeth_n,
          {"code": code, "n": len(d2), "expected": len(data) - teeth_n})
    check("负向对照：确实写了 warning 日志（不是无声无息）",
          any("时间线跳过数据源" in m for m in log_records),
          log_records[:3] or "没有任何 warning")
finally:
    analytics._TIMELINE_SOURCES = orig_sources
    _analytics_logger.removeHandler(_analytics_logger.handlers[-1])

# 复原后必须回到正常（证明上面那次污染没留下副作用）
code, j = tl(limit=2000)
check("负向对照后复原：teething 又能查到了",
      "teething" in {e["type"] for e in (j.get("data") or [])})

# ==================== 5. 前后端一致性（对不上也是「静默空列表」） ====================
FRONTEND = os.path.abspath(os.path.join(BACKEND, "..", "frontend"))
html_path = os.path.join(FRONTEND, "index.html")
js_path = os.path.join(FRONTEND, "js", "app.js")
css_path = os.path.join(FRONTEND, "css", "main.css")

with open(html_path, "r", encoding="utf-8") as f:
    html = f.read()
with open(js_path, "r", encoding="utf-8") as f:
    js = f.read()
with open(css_path, "r", encoding="utf-8") as f:
    css = f.read()


def balanced_block(text, brace_idx):
    """从指向 '{' 的下标开始，取配平花括号内部的内容"""
    depth = 0
    for j in range(brace_idx, len(text)):
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
            if depth == 0:
                return text[brace_idx + 1:j]
    return None


def fn_body(text, fn_name):
    """取某个 function 的函数体。

    ⚠️ 必须按函数名定位再配平：app.js 里同名特征（如 const typeMap）会出现多份，
       直接 find 第一个拿到的是别的函数的，会得出「全都对不上」的假警报。
    """
    m = re.search(r"function\s+" + fn_name + r"\s*\(", text)
    if not m:
        return None
    return balanced_block(text, text.find("{", m.end()))


# 5.1 下拉框的类型值必须和后端类型标识一一对应
# 注意 <select ... id="timelineType"> 里 id 在标签属性中，要用「标签整段」匹配，
# 不能从 id 位置往后找 '<select'（它在前面，往后找会抓到下一个 select）
m_sel = re.search(r'<select[^>]*id="timelineType"[^>]*>(.*?)</select>', html, re.S)
sel = m_sel.group(1) if m_sel else None
opts = set(re.findall(r'<option value="([^"]+)"', sel or ""))
backend_keys = {s[0] for s in SOURCES}
check("前端类型下拉的取值与后端类型标识完全一致",
      opts - {"all"} == backend_keys,
      {"前端多出": sorted((opts - {"all"}) - backend_keys),
       "后端未被前端覆盖": sorted(backend_keys - opts)})

# 5.2 每个后端类型在前端 typeMap 里都要有图标/标题分支，否则退化成兜底的「记录」
tl_body = fn_body(js, "loadTimeline") or ""
tm = ""
_tm_i = tl_body.find("const typeMap")
if _tm_i >= 0:
    tm = balanced_block(tl_body, tl_body.find("{", _tm_i)) or ""
front_types = set(re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s*:\s*\{", tm))
check("后端每一类在前端 typeMap 里都有专属图标/标题",
      backend_keys <= front_types,
      {"前端 typeMap 缺失": sorted(backend_keys - front_types)})

# 5.3 每个类型的圆底配色要有对应 CSS，否则图标是白底
css_classes = set(re.findall(r"\.timeline-icon\.([A-Za-z0-9_-]+)\s*\{", css))
map_classes = set(re.findall(r"class:\s*'([A-Za-z0-9_-]+)'", tm))
check("前端 typeMap 用到的每个配色 class 都有 CSS 定义",
      map_classes <= css_classes,
      {"缺 CSS": sorted(map_classes - css_classes)})

# 5.4 新加的 DOM 节点必须真实存在，否则 JS 里 getElementById 静默拿到 null
NEW_IDS = ["timelineCustomRange", "timelineFrom", "timelineTo",
           "timelineApplyBtn", "timelineHint", "timelineDays", "timelineType"]
missing_ids = [i for i in NEW_IDS if ('id="%s"' % i) not in html]
check("时间线新增/引用的 DOM 节点都在 index.html 里",
      not missing_ids, missing_ids)

# 5.5 前端发出去的 limit 必须正好等于后端上限（后端是校验式拒绝，多 1 就 400）
m = re.search(r"const TIMELINE_LIMIT\s*=\s*(\d+)", js)
fe_limit = int(m.group(1)) if m else None
route_src = inspect.getsource(analytics.get_timeline)
mm = re.search(r'_get_int_arg\("limit",\s*\d+,\s*minimum=\d+,\s*maximum=(\d+)\)', route_src)
backend_max = int(mm.group(1)) if mm else None
check("前端 TIMELINE_LIMIT 与后端 limit 上限一致（不一致会直接 400 白屏）",
      fe_limit is not None and fe_limit == backend_max,
      {"前端": fe_limit, "后端上限": backend_max})

conn.close()

# ==================== 汇总 ====================
print("\n" + "=" * 60)
print("通过 %d 项，失败 %d 项" % (len(PASSED), len(FAILED)))
if FAILED:
    print("失败明细：")
    for name in FAILED:
        print("  - " + name)
print("=" * 60)
sys.exit(1 if FAILED else 0)
