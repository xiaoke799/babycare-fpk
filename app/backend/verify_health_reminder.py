#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证体检/自定义健康提醒（health_reminders 表）真的会发出去。

背景：这张表一直有增删改查（健康档案 → 提醒 tab），但调度器里**没有它的分支**，
所以「三天后去做儿保」这类提醒录了也永远不响 —— 属于「代码能跑但功能是死的」。

静态检查（配置键有没有前端入口）测不出这个，必须真的跑一遍发送路径。

断言：
  A. 今天到期 / 明天到期 / 已过期 → 各发一条
  B. 超出提前天数的 → 不发
  C. 已完成（is_completed=1）→ 不发
  D. 标题为空等脏数据 → 不影响其它提醒照常发出
  E. 去重：同一条连着跑两次只发一次（每天最多一条）
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "notifier"))

if "fcntl" not in sys.modules:
    import types
    m = types.ModuleType("fcntl")
    m.LOCK_EX, m.LOCK_NB, m.LOCK_UN = 2, 4, 8
    m.flock = lambda *a, **k: None
    sys.modules["fcntl"] = m

from reminder_scheduler import ReminderScheduler  # noqa: E402

fails: list[str] = []


def chk(ok: bool, label: str, extra: str = "") -> None:
    print(("  [OK]   " if ok else "  [FAIL] ") + label + (f"  {extra}" if extra else ""))
    if not ok:
        fails.append(label)


class Sent:
    """假的 UnifiedNotifier：只记录被发出去的内容。"""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self.db_path = ""

    def send_notification(self, title, content, skip_dedup=False):
        self.sent.append((title, content))
        return type("R", (), {"success": True, "method": "webhook"})()


def make_db(rows):
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE babies (id INTEGER PRIMARY KEY, name TEXT)")
    con.execute("INSERT INTO babies (id, name) VALUES (1, '小甲')")
    con.execute(
        "CREATE TABLE health_reminders ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " baby_id INTEGER NOT NULL,"
        " reminder_type TEXT NOT NULL,"
        " title TEXT NOT NULL,"
        " description TEXT DEFAULT '',"
        " due_date TEXT NOT NULL,"
        " is_completed INTEGER DEFAULT 0,"
        " completed_date TEXT DEFAULT NULL)")
    con.executemany(
        "INSERT INTO health_reminders"
        " (baby_id, reminder_type, title, description, due_date, is_completed)"
        " VALUES (?,?,?,?,?,?)", rows)
    con.commit()
    return con


def run(rows, days_ahead=1, runs=1):
    db = make_db(rows)
    notifier = Sent()
    sch = ReminderScheduler(notifier, lambda: db, {
        "reminder_enabled": True,
        "health_reminder_enabled": True,
        "health_reminder_days": days_ahead,
    })
    for _ in range(runs):
        sch._check_all()
    return notifier, sch


now = datetime.now()
today = now.strftime("%Y-%m-%d")
tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")
yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
far = (now + timedelta(days=9)).strftime("%Y-%m-%d")

print("=== A. 该发的会发 ===")
n, _ = run([
    (1, "checkup", "3 月龄儿保体检", "带上出生证明", today, 0),
    (1, "custom", "复查血常规", "", tomorrow, 0),
    (1, "checkup", "补种疫苗", "已经逾期两天了", yesterday, 0),
])
chk(len(n.sent) == 3, "今天/明天/已过期各发一条，共 3 条", f"实际 {len(n.sent)}")
chk(any("小甲" in c for _, c in n.sent), "内容带宝宝名")
chk(any("儿保体检" in c for _, c in n.sent), "内容带提醒标题")
chk(any("已到期" in c for _, c in n.sent), "过期那条文案带「已到期」而不是「还有0天」")
chk(any(t == "体检提醒" for t, _ in n.sent), "标题按类型给（体检提醒）")
chk(any(t == "自定义提醒" for t, _ in n.sent),
    "自定义类型的标题不叠词（不能是「提醒提醒」）")

print("\n=== B. 提前天数之外的不发 ===")
n, _ = run([(1, "checkup", "九月远期体检", "", far, 0)], days_ahead=1)
chk(len(n.sent) == 0, "超出提前天数的提醒不发", f"实际发了 {len(n.sent)} 条")

print("\n=== C. 已完成的提醒不发 ===")
n, _ = run([(1, "checkup", "已做完的体检", "", today, 1)])
chk(len(n.sent) == 0, "is_completed=1 不发", f"实际发了 {len(n.sent)} 条")

print("\n=== D. 脏数据不拖累其它提醒 ===")
n, _ = run([
    (1, "checkup", "", "标题为空也不该崩", today, 0),
    (1, "checkup", "正常那条", "", today, 0),
])
chk(len(n.sent) == 2, "两条都发出（空标题不抛异常）", f"实际 {len(n.sent)} 条")

print("\n=== E. 去重：一天最多一条 ===")
n, sch = run([(1, "checkup", "每天一次的体检", "", today, 0)], runs=3)
chk(len(n.sent) == 1, "连跑 3 次只发 1 条", f"实际发了 {len(n.sent)} 条")

print()
if fails:
    print("失败 %d 项" % len(fails))
    sys.exit(1)
print("通过")
