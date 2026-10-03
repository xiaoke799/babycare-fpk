#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xiaoke799
"""
全路由 5xx冒烟：把 url_map 全打一遍，只把 5xx 算失败。

补的是既有门禁的空档：verify_* 全是「针对某功能的定点断言」，
没有任何一个脚本穷举过所有路由。而本项目的历史缺陷形态恰恰是
「客户端发了畸形 body → AttributeError → 500」，这类只有穷举才抓得到。

写接口用 4 种 body 各打一遍：
  1) 完全无 body
  2) {}          （前端漏传字段）
  3) null        （JSON.stringify(null) 的产物）
  4) []          （JSON.stringify([]) 的产物）

⚠ 本脚本自带一条「防假绿」断言，见文件末尾 auth_rate。
   踩过的坑：`/api/auth/logout` 也在 url_map 里，它执行成功会 session.clear()，
   之后上千次请求全部 401 ——「0 个 5xx」完全是假的。
   只看「有没有 5xx」这个门禁，必须同时看「多少请求真的穿过了鉴权」。

跑法：
  C:\\Users\\X\\.workbuddy\\binaries\\python\\envs\\babycare\\Scripts\\python.exe \\
      .workbuddy/scripts/smoke_all_routes.py
退出码：0 = 通过；1 = 有 5xx；2 = 覆盖率过低（假绿，不算通过）
"""
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
BACKEND = os.path.join(ROOT, "app", "backend")
sys.path.insert(0, BACKEND)

TMP_DIR = tempfile.mkdtemp(prefix="babycare_smoke_")
os.environ["BABYCARE_DATA_DIR"] = TMP_DIR
os.environ["BABYCARE_DB_PATH"] = os.path.join(TMP_DIR, "babycare.db")
os.environ["BABYCARE_LOG_DIR"] = os.path.join(TMP_DIR, "logs")
os.environ["BABYCARE_DEV_AUTH"] = "1"

_fcntl = type(sys)("fcntl")
for _n in ("LOCK_EX", "LOCK_SH", "LOCK_NB", "LOCK_UN"):
    setattr(_fcntl, _n, 0)
_fcntl.flock = lambda *a, **k: None
_fcntl.lockf = lambda *a, **k: None
sys.modules["fcntl"] = _fcntl

import server  # noqa: E402

app = server.app
app.config["TESTING"] = False# 让 500 走真实错误处理（而不是被测试客户端抛异常）
app.config["PROPAGATE_EXCEPTIONS"] = False

BODIES = [
    ("无body", None),
    ("{}", "{}"),
    ("null", "null"),
    ("[]", "[]"),
]

# 静态资源 / 前端入口 / 登录与登出（登出会清 session，单独排除）
SKIP_URL = re.compile(
    r"^/static/|favicon|/manifest|^/login$|^/logout$|/auth/logout$|"
    r"^/consent-gate|^/legal/|ws://|healthz"
)


def concrete(rule):
    """把 /api/babies/<int:baby_id> 换成 /api/babies/1；非 /api 的返回 None。"""
    url = str(rule)
    if "<path:" in url or "<" in url and ":" not in url.split("<")[-1]:
        pass
    if "<path:" in url:
        return None
    url = re.sub(r"<(?:[^:<>]+:)?([^<>]+)>", "1", url)
    if not url.startswith("/api/"):
        return None
    if SKIP_URL.search(url):
        return None
    return url


rules = []
seen = set()
for rule in app.url_map.iter_rules():
    url = concrete(rule)
    if not url:
        continue
    for m in sorted((rule.methods or set()) - {"HEAD", "OPTIONS"}):
        key = (m, url)
        if key in seen:
            continue
        seen.add(key)
        rules.append((m, url))

print("=" * 74)
print(f"路由总数（去重后，GET 1 种 body、写接口 4 种 body）：{len(rules)} 条")
print("=" * 74)

client = app.test_client()


def login():
    """保证会话处于「已登录」。被清空就重种 —— 前提，不是保险。"""
    with client.session_transaction() as sess:
        if not sess.get("authenticated"):
            sess["authenticated"] = True
            sess.permanent = True


login()

# 建宝宝 + 设删除安全码，让带<baby_id> / DELETE 的路由能真正往下走
_r = client.post("/api/babies", json={"name": "冒烟宝宝", "birthday": "2026-01-15"})
if _r.status_code != 200:
    print(f"[警告] 建宝宝失败 {_r.status_code} {_r.get_data(as_text=True)[:120]}，"
          f"带 baby_id 的路由会停在 404，覆盖面打折")
else:
    print("[前置] 已建测试宝宝")
_r = client.put("/api/settings/safety-code", json={"code": "2580"})
print("[前置] 已设置删除安全码" if _r.status_code == 200
      else f"[警告] 设安全码失败 {_r.status_code}，DELETE 类路由会停在 403")

status_hist = {}
status_detail = {}
relogin_count = 0

failures = []
touched = 0

for method, url in rules:
    for label, raw in BODIES:
        headers = {}
        kwargs = {}
        if raw is not None:
            headers["Content-Type"] = "application/json"
            kwargs["data"] = raw
        login()
        try:
            resp = client.open(url, method=method, headers=headers, **kwargs)
        except Exception as e:
            failures.append((method, url, label, "EXC " + type(e).__name__ + ": " + str(e)[:160]))
            continue
        touched += 1
        status_hist["%dxx" % (resp.status_code // 100)] = \
            status_hist.get("%dxx" % (resp.status_code // 100), 0) + 1
        status_detail.setdefault(resp.status_code, []).append(f"{method} {url} [{label}]")
        if resp.status_code >= 500:
            failures.append((method, url, label,
                             f"{resp.status_code} " + resp.get_data(as_text=True)[:200].replace("\n", " ")))
        # send_file 之后立刻 close，Windows 句柄不释放会让删除/覆盖报错
        try:
            resp.get_data()
            resp.close()
        except Exception:
            pass
        with client.session_transaction() as sess:
            if not sess.get("authenticated"):
                relogin_count += 1

print(f"实际发出请求：{touched} 次")
print(f"会话被清空需重种：{relogin_count} 次")
print("状态码分布：" + "  ".join(f"{k}={v}" for k, v in sorted(status_hist.items())))
print("=" * 74)
print("按状态码看请求落到哪儿（判断是不是全被鉴权/404 挡在业务之前）：")
for code in sorted(status_detail):
    items = status_detail[code]
    print(f"  {code}: {len(items)} 次   e.g. {items[0]}  /  {items[len(items) // 2]}")
print("=" * 74)
for method, url, label, detail in failures:
    print(f"  [5XX] {method:6} {url:52} body={label:8} {detail}")

print("=" * 74)
# ── 防假绿断言 ────────────────────────────────────────────────
# 绝大多数是 4xx ⇒ 请求被鉴权/校验挡在业务代码之前，"没有 5xx" 没有意义。
n4 = status_hist.get("4xx", 0)
if touched and n4 > touched * 0.85:
    print(f"[断言失败] 4xx 占 {n4}/{touched}，覆盖面过低 ——"
          f"这种「0 个 5xx」是假绿，不许放过。")
    sys.exit(2)
print(f"[断言通过] 4xx 占比 {n4 / max(touched, 1):.0%}，覆盖面可信")

if failures:
    print(f"失败 {len(failures)} 处")
    sys.exit(1)
print("全部路由无 5xx")
sys.exit(0)