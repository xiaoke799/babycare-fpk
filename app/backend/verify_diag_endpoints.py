#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xiaoke799
"""
「系统诊断」接口离线体检：不启服务、不碰真实数据库。

覆盖设置页「系统诊断」板块用到的全部接口：
  应用信息 / 存储总览 / 数据库健康 / 日志文件 / 日志内容 / 日志清理
  / 异常统计 / 异常列表 / 异常详情 / 异常清理

两个重点：
  ① 返回结构 —— 前端按这些字段渲染，字段改名/换层会让界面白屏；
  ② level 的大小写口径 —— error_tracker 写库时用的是大写 ERROR/CRITICAL，
     而 get_recent_errors 的 level 是**精确匹配**。前端筛选若传小写，
     会「查得到 0 条」却不报错，属于最难发现的静默失败。这里把两种写法
     都打一遍，把事实钉死。

跑法：
  C:\\Users\\X\\.workbuddy\\binaries\\python\\envs\\babycare\\Scripts\\python.exe verify_diag_endpoints.py
"""

import datetime
import os
import sqlite3
import sys
import tempfile

BACKEND = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BACKEND)

# DATA_DIR / DB_PATH / LOG_DIR 都要在 import server 之前定好：
# constants.py 默认指向 /var/apps/... ，Windows 上不存在。
TMP_DIR = tempfile.mkdtemp(prefix="babycare_diag_verify_")
TMP_DB = os.path.join(TMP_DIR, "babycare.db")
os.environ["BABYCARE_DATA_DIR"] = TMP_DIR
os.environ["BABYCARE_DB_PATH"] = TMP_DB
os.environ["BABYCARE_LOG_DIR"] = os.path.join(TMP_DIR, "logs")

# Windows 没有 fcntl，先打桩再 import server
_fcntl = type(sys)("fcntl")
for _n in ("LOCK_EX", "LOCK_SH", "LOCK_NB", "LOCK_UN"):
    setattr(_fcntl, _n, 0)
_fcntl.flock = lambda *a, **k: None
_fcntl.lockf = lambda *a, **k: None
sys.modules["fcntl"] = _fcntl

import server  # noqa: E402

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


print("\n=== 0. 建库（init_db + 迁移，两步缺一不可）===")
server.init_db()
try:
    server._run_migrations()
except Exception as e:
    print(f"  [警告] 迁移抛异常（继续）：{e}")
check("临时库建好了", os.path.exists(TMP_DB))

# 造两条异常记录：一条 ERROR、一条 CRITICAL（故意用后端约定的大写）
NOW = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
conn = sqlite3.connect(TMP_DB)
conn.execute(
    """INSERT INTO error_logs (timestamp, level, module, path, method, message,
                               traceback, request_id, client_ip, user_agent, extra_data)
       VALUES (?, 'ERROR', 'blueprints.feeding', '/api/feeding', 'POST',
               '模拟错误：保存喂奶记录失败', 'Traceback (most recent call last): ...',
               'req-0001', '127.0.0.1', 'verify-agent', '{"baby_id": 1}')""",
    (NOW,),
)
cur = conn.execute(
    """INSERT INTO error_logs (timestamp, level, module, path, method, message,
                               traceback, request_id, client_ip, user_agent, extra_data)
       VALUES (?, 'CRITICAL', 'utils', '/api/backup', 'POST',
               '模拟严重错误：备份写入失败', '', 'req-0002', '127.0.0.1', 'verify-agent', '')""",
    (NOW,),
)
CRITICAL_ID = cur.lastrowid
conn.commit()
conn.close()
check("已塞入 2 条异常记录", CRITICAL_ID is not None)

app = server.app
app.config["TESTING"] = True
client = app.test_client()

# 接口都要登录。测试里直接走 session —— 会话登录恒为管理员，与生产同权限。
with client.session_transaction() as sess:
    sess["authenticated"] = True


def get_json(path):
    r = client.get(path)
    try:
        return r.status_code, r.get_json()
    except Exception:
        return r.status_code, None


def post_json(path, payload):
    r = client.post(path, json=payload)
    try:
        return r.status_code, r.get_json()
    except Exception:
        return r.status_code, None


print("\n=== 1. 应用信息 ===")
code, d = get_json("/api/storage/app-info")
check("GET /api/storage/app-info 200", code == 200, f"实际 {code}")
check("success=True", bool(d) and d.get("success") is True)
for key in ["app_name", "version", "uptime_human", "uptime_seconds", "started_at",
            "server_time", "python_version", "sqlite_version", "platform",
            "data_dir", "db_path", "pid"]:
    check(f"含字段 {key}", bool(d) and key in d, str(list((d or {}).keys()))[:120])
check("运行时长是「秒」级文案", bool(d) and isinstance(d.get("uptime_human"), str)
      and ("秒" in d.get("uptime_human", "") or "分" in d.get("uptime_human", "")))

print("\n=== 2. 存储总览 / 数据库健康 ===")
code, d = get_json("/api/storage/overview")
check("GET /api/storage/overview 200", code == 200, f"实际 {code}")
check("overview 含 database/photos/backups/logs/disk",
      bool(d) and all(k in d for k in ["database", "photos", "backups", "logs", "disk"]))
code, d = get_json("/api/storage/db/health")
check("GET /api/storage/db/health 200", code == 200, f"实际 {code}")

print("\n=== 3. 日志文件（admin 那套，前端用的就是它）===")
code, d = get_json("/api/admin/logs/files")
check("GET /api/admin/logs/files 200", code == 200, f"实际 {code}")
check("success=True 且含 files", bool(d) and d.get("success") is True and "files" in d)
files = (d or {}).get("files") or {}
check("files 是「按类型名索引的对象」而不是数组", isinstance(files, dict),
      f"实际类型 {type(files).__name__}")
check("files.main 含 size_bytes / size_human",
      isinstance(files.get("main"), dict)
      and "size_bytes" in files["main"] and "size_human" in files["main"])

code, d = get_json("/api/admin/logs/files/content?type=main&lines=50")
check("GET /api/admin/logs/files/content 200", code == 200, f"实际 {code}")
check("含 content 字段（字符串）", bool(d) and isinstance(d.get("content"), str))

print("\n=== 4. 异常统计 ===")
code, d = get_json("/api/admin/logs/stats?days=7")
check("GET /api/admin/logs/stats 200", code == 200, f"实际 {code}")
check("含 total / today / by_level",
      bool(d) and all(k in d for k in ["total", "today", "by_level"]))
by_level = (d or {}).get("by_level") or []
levels = {str(x.get("level")) for x in by_level}
check("by_level 里的 level 是**大写**（ERROR/CRITICAL）",
      "ERROR" in levels and "CRITICAL" in levels, f"实际 {sorted(levels)}")
check("total 统计到 2 条", (d or {}).get("total") == 2, f"实际 {(d or {}).get('total')}")

print("\n=== 5. 异常列表 + 级别筛选口径（关键）===")
code, d = get_json("/api/admin/logs/errors?limit=20&offset=0&days=7")
check("GET /api/admin/logs/errors 200", code == 200, f"实际 {code}")
check("含 total / errors", bool(d) and "total" in d and isinstance(d.get("errors"), list))
check("errors 里每条含 id/level/message/timestamp",
      bool(d) and d["errors"] and all(
          k in d["errors"][0] for k in ["id", "level", "message", "timestamp"]),
      str((d or {}).get("errors", [])[:1])[:200])

code, d_upper = get_json("/api/admin/logs/errors?limit=20&level=ERROR&days=7")
check("level=ERROR（大写）能筛到 1 条", (d_upper or {}).get("total") == 1,
      f"实际 {(d_upper or {}).get('total')}")

code, d_lower = get_json("/api/admin/logs/errors?limit=20&level=error&days=7")
check("level=error（小写）查不到 —— 印证必须传大写，前端已按大写",
      (d_lower or {}).get("total") == 0, f"实际 {(d_lower or {}).get('total')}")

print("\n=== 6. 异常详情 / 趋势 ===")
code, d = get_json(f"/api/admin/logs/detail/{CRITICAL_ID}")
check("GET /api/admin/logs/detail/<id> 200", code == 200, f"实际 {code}")
check("详情含 level/message/traceback/extra_data",
      bool(d) and d.get("success") is True and isinstance(d.get("data"), dict)
      and all(k in d["data"] for k in ["level", "message", "traceback", "extra_data"]))
check("详情 level 是大写 CRITICAL",
      bool(d) and d.get("data", {}).get("level") == "CRITICAL")

code, d = get_json("/api/admin/logs/trend?days=7&group_by=day")
check("GET /api/admin/logs/trend 200", code == 200, f"实际 {code}")
check("趋势返回列表", bool(d) and d.get("success") is True and isinstance(d.get("data"), list))

print("\n=== 7. 清理类接口（都用 POST，且需要管理员）===")
code, d = post_json("/api/admin/logs/clear", {"days": 30})
check("POST /api/admin/logs/clear 200 且 success=True",
      code == 200 and bool(d) and d.get("success") is True, f"实际 {code} / {d}")

code, d = post_json("/api/admin/logs/files/clear", {"type": "all"})
check("POST /api/admin/logs/files/clear 200 且 success=True",
      code == 200 and bool(d) and d.get("success") is True, f"实际 {code} / {d}")

code, d = post_json("/api/storage/logs/cleanup", {"action": "truncate"})
check("POST /api/storage/logs/cleanup 200 且 success=True（前端「清空全部」用的它）",
      code == 200 and bool(d) and d.get("success") is True, f"实际 {code} / {d}")

print("\n=== 8. 未登录时必须被挡住 ===")
anon = server.app.test_client()
code = anon.get("/api/storage/app-info").status_code
check("未登录访问 /api/storage/app-info 被拒", code in (401, 403), f"实际 {code}")
code2 = anon.get("/api/admin/logs/files").status_code
check("未登录访问 /api/admin/logs/files 被拒", code2 in (401, 403), f"实际 {code2}")

print("\n==============================================")
print(f"通过 {_passed} 项，失败 {_failed} 项")
print("==============================================")
sys.exit(0 if _failed == 0 else 1)
