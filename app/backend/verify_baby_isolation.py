#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""多宝宝数据隔离回归门禁（临时库，不碰正式数据）。

覆盖三类不变量：

  A. 读取隔离（差分法）
     先记录 B 宝宝所有只读接口的响应基线，再往 A 宝宝**每张 baby_id 分表**
     各插一条带标记的行，重取 B 的响应做逐接口比对。
     任何差异都说明「B 的视图被 A 的数据影响了」—— 这就是跨宝宝串数据。
     ⚠ 之所以用差分而不是只查标记串：数值型统计（计数/求和）泄漏不会有标记串，
     「A 记了两条喂奶，B 的统计里+2」用文本标记是查不出来的。

  B. 取消活动收藏（回归）
     收藏按宝宝存储。历史上前端 DELETE 一个参数都没带，后端只读 query
     ⇒ 400「缺少必要参数」，症状是「能收藏、永远取消不掉」。

  C. 热门活动按月龄无关的跨宝宝统计（回归）
     /api/activities?sort=popular 的 played_count 必须按 baby_id 统计，
     否则新宝宝会看到兄弟姐妹的喜好。

跑法：
  $MIMO_PYTHON app/backend/verify_baby_isolation.py
"""
import os
import re
import sqlite3
import sys
import tempfile
from collections import Counter

BACKEND = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BACKEND)

TMP_DIR = tempfile.mkdtemp(prefix="babycare_baby_isolation_")
TMP_DB = os.path.join(TMP_DIR, "babycare.db")
os.environ["BABYCARE_DATA_DIR"] = TMP_DIR
os.environ["BABYCARE_DB_PATH"] = TMP_DB
os.environ["BABYCARE_LOG_DIR"] = os.path.join(TMP_DIR, "logs")
os.environ["BABYCARE_DEV_AUTH"] = "1"

# Windows 无 fcntl：在 import server 之前打桩（项目既有惯例）
_fcntl = type(sys)("fcntl")
for _n in ("LOCK_EX", "LOCK_SH", "LOCK_NB", "LOCK_UN"):
    setattr(_fcntl, _n, 0)
_fcntl.flock = lambda *a, **k: None
_fcntl.lockf = lambda *a, **k: None
sys.modules["fcntl"] = _fcntl

import server  # noqa: E402
from constants import BABY_SCOPED_TABLES  # noqa: E402

_passed = 0
_failed = 0


def check(name, cond, extra=""):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  [OK]   {name}")
    else:
        _failed += 1
        print(f"  [FAIL] {name}" + (f"  -> {extra}" if extra else ""))


print("\n=== 0. 建库 ===")
server.init_db()
try:
    server._run_migrations()
except Exception as e:  # noqa: BLE001
    print(f"  [警告] 迁移抛异常（继续）：{e}")
server.app.config["TESTING"] = True
client = server.app.test_client()
with client.session_transaction() as sess:
    sess["authenticated"] = True


def make_baby(name, birthday):
    r = client.post("/api/babies", json={"name": name, "birthday": birthday, "gender": "other"})
    return ((r.get_json() or {}).get("data") or {}).get("id")


print("\n=== 1. 准备两个宝宝 ===")
A = make_baby("隔离甲", "2024-01-01")
B = make_baby("隔离乙", "2023-06-01")
check("两个宝宝都建好了", isinstance(A, int) and isinstance(B, int) and A != B, f"A={A} B={B}")

# 收集「带 baby_id 的只读接口」模板（只取单一转换器的，避免还要编造其它路径参数）
TEMPLATES = []
for rule in server.app.url_map.iter_rules():
    s = str(rule)
    if "GET" not in rule.methods or "<int:baby_id>" not in s:
        continue
    rest = s.replace("<int:baby_id>", "")
    if "<" in rest:
        continue
    TEMPLATES.append(s)
TEMPLATES = sorted(set(TEMPLATES))


def request_all(baby_id):
    """按**模板**为键存快照。

    ⚠️ 必须用模板当键：早先这里用解析后的 URL 当键、比对时又拿模板去查，
    两边永远都是 None、永远相等 ⇒ 这条差分断言是**永久假绿**的
    （实测：把 photos 的 baby_id 过滤打掉，门禁照样全绿）。
    负向对照抓出来的，别再改回去。
    """
    out = {}
    for tpl in TEMPLATES:
        url = tpl.replace("<int:baby_id>", str(baby_id))
        try:
            r = client.get(url)
            out[tpl] = (r.status_code, r.get_data(as_text=True))
        except Exception as e:  # noqa: BLE001
            out[tpl] = (0, repr(e))
    return out


print(f"\n=== 2. 读取隔离差分（{len(TEMPLATES)} 个只读接口）===")
check("只读接口数量合理（>=60，防止扫描器废掉后假绿）", len(TEMPLATES) >= 60, str(len(TEMPLATES)))

# baby_id 走 query 而不是路径的一组只读接口（同样是「按宝宝看的数据」）
QUERY_SCOPED = [
    "/api/activities/favorites?baby_id=%d",
    "/api/activities/logs?baby_id=%d",
    "/api/activities/stats?baby_id=%d",
]


def request_query_scoped(baby_id):
    """同样按模板为键（理由见 request_all 的注释）。"""
    out = {}
    for tpl in QUERY_SCOPED:
        url = tpl % baby_id
        try:
            r = client.get(url)
            out[tpl] = (r.status_code, r.get_data(as_text=True))
        except Exception as e:  # noqa: BLE001
            out[tpl] = (0, repr(e))
    return out


# 预热一轮：首次请求可能触发初始化 / 首次生成；随后连取两份完全相同的快照，
# 用来识别「接口自身非幂等」的那些（AI 报告类会边读边写自己的历史）——
# 它们在同一次探测里前后两份就不一样，不剔除会被误判成跨宝宝泄漏。
request_all(B)
base1 = request_all(B)
base2 = request_all(B)
UNSTABLE = [tpl for tpl in TEMPLATES if base1.get(tpl) != base2.get(tpl)]
STABLE = [tpl for tpl in TEMPLATES if tpl not in UNSTABLE]

before_q_A = request_query_scoped(A)
before_q_B = request_query_scoped(B)

# 往 A 的每张分表插一条标记行（文本列放标记串、数值列放 1）
MARK = "ZQISOLATE7"
conn = sqlite3.connect(TMP_DB)
cur = conn.cursor()
existing = {r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
inserted = []
for t in sorted(BABY_SCOPED_TABLES):
    if t not in existing:
        continue
    cols = cur.execute("PRAGMA table_info(%s)" % t).fetchall()
    if "baby_id" not in [c[1] for c in cols]:
        continue
    vals = {}
    for c in cols:
        name, typ, notnull, dflt, pk = c[1], (c[2] or "").upper(), c[3], c[4], c[5]
        if pk:
            continue
        if name == "baby_id":
            vals[name] = A
        elif notnull and dflt is None:
            vals[name] = 1 if "INT" in typ else MARK
        else:
            vals[name] = 1 if ("INT" in typ or "REAL" in typ or "NUM" in typ) else MARK
    try:
        cur.execute(
            "INSERT INTO %s (%s) VALUES (%s)" % (t, ",".join(vals), ",".join("?" * len(vals))),
            list(vals.values()),
        )
        inserted.append(t)
    except Exception as e:  # noqa: BLE001
        print(f"  [警告] 给 {t} 插标记行失败：{str(e)[:80]}")
conn.commit()

scoped_in_db = len([t for t in BABY_SCOPED_TABLES if t in existing])
check(
    "标记行覆盖了绝大多数分表（>=90%）",
    len(inserted) >= int(scoped_in_db * 0.9),
    f"{len(inserted)}/{scoped_in_db}",
)

after = request_all(B)
after_q_A = request_query_scoped(A)
after_q_B = request_query_scoped(B)

check(
    "非幂等接口数量在合理范围（应是个位数，多了说明接口在乱写）",
    len(UNSTABLE) <= 12,
    "%d 个：%s" % (len(UNSTABLE), "；".join(UNSTABLE)),
)
check(
    "稳定只读接口数量足够（>=60，防止剔除过头后没剩几条）",
    len(STABLE) >= 60,
    f"{len(STABLE)}/{len(TEMPLATES)}",
)

diffs = []
if os.environ.get("BABY_ISO_DEBUG") == "1":
    print("  [dbg] 非幂等接口:", UNSTABLE)
    for _t in STABLE:
        if base2.get(_t) != after.get(_t):
            print("  [dbg] DIFF", _t)
            print("        before:", str(base2.get(_t))[:220])
            print("        after :", str(after.get(_t))[:220])
for url in STABLE:
    if base2.get(url) != after.get(url):
        diffs.append(url)
check(
    "插入 A 的数据后，B 的只读视图逐接口零变化",
    not diffs,
    "；".join(diffs[:6]) + (" …" if len(diffs) > 6 else ""),
)

diffs_q = []
for tpl in QUERY_SCOPED:
    if before_q_B.get(tpl) != after_q_B.get(tpl):
        diffs_q.append(tpl)
check(
    "插入 A 的数据后，B 的 query 作用域接口也零变化",
    not diffs_q,
    "；".join(diffs_q),
)

# 灵敏度对照（负向对照）：同一批接口换成 A，必须**看得到变化**——
# 否则上一条"零变化"可能只是因为差分机制本身失效（假绿，本项目踩过）。
sensitive = [tpl for tpl in QUERY_SCOPED if before_q_A.get(tpl) != after_q_A.get(tpl)]
check(
    "灵敏度对照：A 的同类接口确实被自己的新数据改变",
    len(sensitive) >= 2,
    f"有变化的接口 {len(sensitive)} 个",
)

codes = Counter(v[0] for v in after.values())
check(
    "B 的只读接口没有 5xx（接口本身能跑）",
    codes.get(500, 0) == 0 and codes.get(0, 0) == 0,
    str(dict(codes)),
)

print("\n=== 3. 取消活动收藏（回归）===")
acts = (client.get("/api/activities").get_json() or {}).get("data") or []
check("活动库非空（否则本节无意义）", len(acts) > 0, str(len(acts)))
activity_id = acts[0]["id"] if acts else 1


def fav_count(baby_id):
    r = client.get("/api/activities/favorites?baby_id=%d" % baby_id)
    return len(((r.get_json() or {}).get("data")) or [])


r = client.post("/api/activities/favorite", json={"baby_id": B, "activity_id": activity_id})
check("POST 收藏成功", r.status_code == 200 and (r.get_json() or {}).get("success"), str(r.get_json()))
check("收藏后列表里有 1 条", fav_count(B) == 1, str(fav_count(B)))

# 上面第 2 节给 A 也插了收藏标记行，这里记下 A 的初值再比对，
# 才能说明「取消 B 的收藏没有波及 A」。
a_fav_before = fav_count(A)

# 前端真实形态：DELETE 带 query
r = client.delete("/api/activities/favorite?baby_id=%d&activity_id=%d" % (B, activity_id))
check(
    "DELETE 带 query 取消收藏成功（前端现行形态）",
    r.status_code == 200 and (r.get_json() or {}).get("success"),
    f"{r.status_code} {r.get_json()}",
)
check("取消后列表清空", fav_count(B) == 0, str(fav_count(B)))

# 容错形态：DELETE 把参数放 body
client.post("/api/activities/favorite", json={"baby_id": B, "activity_id": activity_id})
_r = client.delete("/api/activities/favorite", json={"baby_id": B, "activity_id": activity_id})
if os.environ.get("BABY_ISO_DEBUG") == "1":
    print("  [dbg] delete(body) ->", _r.status_code, _r.get_json())
r = _r
check(
    "DELETE 参数走 body 也能取消（容错）",
    r.status_code == 200 and (r.get_json() or {}).get("success"),
    f"{r.status_code} {r.get_json()}",
)
check("取消后列表清空（body 路径）", fav_count(B) == 0, str(fav_count(B)))

# 都不给：必须明确报错，而不是静默成功
r = client.delete("/api/activities/favorite")
check("DELETE 不带任何参数 -> 400", r.status_code == 400, f"{r.status_code} {r.get_json()}")

check("取消收藏不影响另一个宝宝", fav_count(A) == a_fav_before, f"A 由 {a_fav_before} 变成 {fav_count(A)}")


def played_counts(baby_id=None):
    url = "/api/activities?sort=popular&limit=200"
    if baby_id is not None:
        url += "&baby_id=%d" % baby_id
    rows = ((client.get(url).get_json() or {}).get("data")) or []
    return {r["id"]: r.get("played_count", 0) for r in rows}


print("\n=== 4. 热门活动按宝宝统计（回归）===")
target = activity_id
for _ in range(2):
    _r = client.post("/api/activities/log", json={"baby_id": A, "activity_id": target, "duration_minutes": 5})
    if os.environ.get("BABY_ISO_DEBUG") == "1":
        print("  [dbg] post log ->", _r.status_code, _r.get_json())

ca = played_counts(A)
cb = played_counts(B)
c_all = played_counts(None)

check("A 的统计里该活动被玩了 2 次", ca.get(target) == 2, f"A={ca.get(target)}")
check("B 的统计里该活动为 0 次（不受 A 影响）", cb.get(target) == 0, f"B={cb.get(target)}")
# 敏感度对照：不传 baby_id 时确实会统计到 A 的数据 —— 证明上一条不是因为参数没生效
check(
    "不传 baby_id 时统计到 A 的 2 次（证明 baby_id 参数真的在起作用）",
    c_all.get(target) == 2,
    f"all={c_all.get(target)}",
)

print("\n=== 5. 前端调用形态（绊线）===")
# 上面第 3 节测的是**后端**能否处理带参的取消收藏 —— 即使前端一个参数都不带，
# 那几行也照样绿。所以这里补一条源码级绊线，专盯「前端调用点漏带参数」。
# ⚠ 故意写得宽容（只要求调用点附近出现这两个键名），避免正常重构就误报；
#   若哪天把取消收藏的调用搬走/改名，这里会转红，提醒同步改门禁。
APP_JS = os.path.abspath(os.path.join(BACKEND, "..", "frontend", "js", "app.js"))
check("能找到前端 app.js", os.path.exists(APP_JS), APP_JS)
if os.path.exists(APP_JS):
    js = open(APP_JS, encoding="utf-8").read()
    idxs = [m.start() for m in re.finditer(re.escape("/api/activities/favorite'"), js)]
    check("前端存在 /api/activities/favorite 调用点", len(idxs) >= 1, str(len(idxs)))
    ok_shape = False
    for i in idxs:
        # 只看紧挨着调用点之前的一小段：取消收藏的 query 串就拼在这里。
        # 窗口必须收窄 —— 放宽到上千字符会被邻近的
        # /api/activities/favorite/check?baby_id=…&activity_id=… 误满足，
        # 那样漏带参数的版本照样能绿（实测过）。
        window = js[max(0, i - 250): i + 80]
        if "?baby_id=" in window and "&activity_id=" in window:
            ok_shape = True
            break
    check(
        "取消收藏的调用点带上了 baby_id / activity_id（漏带 = 永远取消不掉）",
        ok_shape,
        "在 /api/activities/favorite 调用点附近没找到 baby_id=…&activity_id=…",
    )

print(f"\n=== 结果：{_passed} 过 / {_failed} 挂 ===")
sys.exit(1 if _failed else 0)