#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证提醒调度器**真的能发**（2026-10-05 严重缺陷的回归门禁）。

缺陷回顾：server.py 曾给调度器传一个坏闭包
    def get_db(): from database import get_db as _get_db
而 database 模块从来没有 get_db ⇒ 每次检查都 ImportError，
被 reminder_scheduler 的 `except Exception: return` 吞掉 ⇒
喂奶/睡眠/尿布/疫苗/用药/体检提醒**一条都不会发**，
启动日志却照旧打印「已初始化」。

⚠ 上轮加的 verify_health_reminder.py 没能抓到它 —— 因为它直接测 check 函数，
   没走「调度器怎么拿连接」这条路径。本脚本专测这一层。

断言：
  A. database.get_scheduler_db 存在且能返回可查询的连接
  B. 两次连续调用不会泄漏（上一条已关闭）
  C. 调度器用**真实构造方式**拿到连接能正常查询（不 ImportError）
  D. 端到端：造喂奶记录 → 触发 _check_feeding_reminder → 确实产出通知
  E. 连接工厂在后台线程里可用（不能依赖 Flask g）
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import threading
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if "fcntl" not in sys.modules:
    m = types.ModuleType("fcntl")
    m.LOCK_EX, m.LOCK_NB, m.LOCK_UN = 2, 4, 8
    m.flock = lambda *a, **k: None
    sys.modules["fcntl"] = m

tmpdir = tempfile.mkdtemp(prefix="babycare_sched_")
os.environ["BABYCARE_DATA_DIR"] = tmpdir
os.environ["BABYCARE_DB_PATH"] = os.path.join(tmpdir, "babycare.db")
os.environ["BABYCARE_CONFIG_DIR"] = tmpdir

import datetime  # noqa: E402

import server  # noqa: E402

app = server.app
app.config["TESTING"] = True
server.init_db()
server._run_migrations()   # 加列都在这一步，只调 init_db 会漏

fails: list[str] = []


def chk(ok: bool, label: str, extra: str = "") -> None:
    print(("  [OK]   " if ok else "  [FAIL] ") + label + (f"  {extra}" if extra else ""))
    if not ok:
        fails.append(label)


import database  # noqa: E402

print("=== A. 连接工厂存在且可用 ===")
chk(hasattr(database, "get_scheduler_db"),
    "database.get_scheduler_db 存在（原来根本没有这个函数）")
conn = database.get_scheduler_db()
chk(conn is not None, "能拿到连接")
try:
    n = conn.execute("SELECT COUNT(*) FROM babies").fetchone()[0]
    chk(isinstance(n, int), "连接可查询（不是坏连接）", f"babies={n}")
except Exception as e:  # noqa: BLE001
    chk(False, "连接可查询", f"异常 {e}")
finally:
    conn.close()

print("\n=== B. 连续调用不泄漏（上一条应被关掉）===")
c1 = database.get_scheduler_db()
c2 = database.get_scheduler_db()
chk(c1 is not c2, "每次返回新连接")
try:
    c1.execute("SELECT 1")
    chk(False, "上一条连接已关闭（不应还能用）")
except sqlite3.ProgrammingError:
    chk(True, "上一条连接已关闭（不应还能用）")
except Exception as e:  # noqa: BLE001
    # 关闭后的连接报错类型可能是 DatabaseError，宽松判定
    chk("closed" in str(e).lower() or "closed" in type(e).__name__.lower(),
        "上一条连接已关闭（不应还能用）", f"异常 {type(e).__name__}: {e}")
c2.close()

print("\n=== C. 调度器构造方式（原来这里 ImportError）===")
import inspect  # noqa: E402

code_only = [ln for ln in inspect.getsource(server).splitlines()
             if not ln.strip().startswith("#") and "\"\"\"" not in ln]
code_text = "\n".join(code_only)
chk("from database import get_db" not in code_text,
    "server.py 代码里已不再 import 那个不存在的 get_db（注释不算）")
chk("get_scheduler_db" in code_text, "server.py 已改用 get_scheduler_db")

from notifier.reminder_scheduler import ReminderScheduler  # noqa: E402


class _Result:
    """notifier.send_notification 的真实返回形状（有 success / method）"""
    def __init__(self, ok=True, method="webhook"):
        self.success = ok
        self.method = method


class _FakeNotifier:
    def __init__(self):
        self.sent = []

    def send_notification(self, title, content, **kw):
        self.sent.append((title, content))
        return _Result()

    def __getattr__(self, name):
        def _noop(*a, **k):
            return None
        return _noop


print("\n=== D. 端到端：真实调用链能否产出通知 ===")
with app.app_context():
    from utils import get_db
    db = get_db()
    db.execute("INSERT INTO babies (name, birthday, gender) VALUES (?,?,?)",
               ("小甲", "2026-01-01", "boy"))
    bid = db.execute("SELECT last_insert_rowid() FROM babies").fetchone()[0]
    now = datetime.datetime.now()
    db.execute(
        "INSERT INTO feeding_records (baby_id, start_time, feeding_type, amount, author)"
        " VALUES (?,?,?,?,?)",
        (bid, (now - datetime.timedelta(hours=4)).strftime("%Y-%m-%d %H:%M:%S"),
         "bottle", 120, "妈妈"))
    db.commit()

notifier = _FakeNotifier()
cfg = {"feeding_reminder_interval": "3"}
sched = ReminderScheduler(notifier, database.get_scheduler_db, cfg,
                          lock_dir=tmpdir)
with app.app_context():
    sched._check_feeding_reminder(datetime.datetime.now())
chk(len(notifier.sent) > 0,
    "_check_feeding_reminder 真实产出通知（原来这里恒为 0 且不报错）",
    f"发出 {len(notifier.sent)} 条")
if notifier.sent:
    print(f"        通知标题：{notifier.sent[0][0]}")

print("\n=== E. 后台线程里可用（不能依赖 Flask g）===")
box = {}


def _worker():
    try:
        c = database.get_scheduler_db()
        box["ok"] = c.execute("SELECT 1").fetchone()[0] == 1
        c.close()
    except Exception as e:  # noqa: BLE001
        box["err"] = f"{type(e).__name__}: {e}"


t = threading.Thread(target=_worker)
t.start()
t.join(timeout=10)
chk(box.get("ok") is True, "无 app_context 的后台线程里连接可用", box.get("err", ""))

database.close_scheduler_db()
database.close_all_connections()

print()
if fails:
    print("失败 %d 项" % len(fails))
    sys.exit(1)
print("通过")
