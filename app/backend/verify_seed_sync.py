#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证内置内容同步（sync_seed_content）行为正确。

为什么必须真跑：这段逻辑要同时满足三件互相牵制的事 ——
  ① 新装用户不能被无意义地改一遍（幂等）
  ② 老用户必须拿到内容修正（否则清理「每日 400IU 维生素D」对老装用户等于没做）
  ③ 用户自己改过的条目**绝不能被覆盖**
静态检查一个都测不出来：写错了 SQL 不报错、只是静默不更新或误覆盖。

覆盖：
  A. 首次播种 → 全部写入且带 content_hash
  B. 立刻再跑一次同步 → 0 改动（幂等）
  C. 模拟「老库」：清掉 content_hash 并塞回旧内容 → 同步应修正，且**幂等**
  D. 模拟「用户改过」：改内容但保留旧 hash → 同步应跳过，不覆盖
  E. 百科正文拼接与播种用的是同一函数（hash 对得上的前提）
"""
from __future__ import annotations

import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import seed_data  # noqa: E402

fails: list[str] = []


def chk(ok: bool, label: str, extra: str = "") -> None:
    print(("  [OK]   " if ok else "  [FAIL] ") + label + (f"  {extra}" if extra else ""))
    if not ok:
        fails.append(label)


def fresh_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE knowledge_articles (
        id INTEGER PRIMARY KEY, category TEXT, title TEXT,
        age_range TEXT, content TEXT)""")
    conn.execute("""CREATE TABLE baby_wiki (
        id INTEGER PRIMARY KEY, category TEXT, title TEXT,
        content TEXT, symptoms TEXT)""")
    conn.execute("""CREATE TABLE baby_recipes (
        id INTEGER PRIMARY KEY, category TEXT, title TEXT,
        age_group TEXT, content TEXT)""")
    conn.execute("""CREATE TABLE baby_activities (
        id INTEGER PRIMARY KEY, category TEXT, title TEXT,
        min_age_months INT, max_age_months INT, content TEXT)""")
    conn.execute("""CREATE TABLE seed_applied (
        id INTEGER PRIMARY KEY, source TEXT, title TEXT, applied_at TEXT)""")
    return conn


print("=== A. 首次播种 ===")
conn = fresh_conn()
n1 = seed_data.seed_knowledge_articles(conn)
n2 = seed_data.seed_baby_wiki(conn)
conn.commit()
chk(n1 > 0, f"知识库播种 {n1} 条")
chk(n2 > 0, f"百科播种 {n2} 条")
rows = conn.execute("SELECT COUNT(*) c FROM knowledge_articles "
                    "WHERE content_hash IS NOT NULL").fetchone()["c"]
chk(rows == n1, f"知识库全部带 content_hash（{rows}/{n1}）")
rows = conn.execute("SELECT COUNT(*) c FROM baby_wiki "
                    "WHERE content_hash IS NOT NULL").fetchone()["c"]
chk(rows == n2, f"百科全部带 content_hash（{rows}/{n2}）")

print("\n=== B. 幂等：立刻再跑一次同步 ===")
synced = seed_data.sync_seed_content(conn)
conn.commit()
chk(synced == 0, "新装状态下同步改动 0 条", f"实际 {synced}")

print("\n=== C. 老库场景：清 hash + 塞回旧内容 → 应被修正 ===")
# 取「维生素D补充」做样本
title = "维生素D补充"
old_content = "建议纯母乳喂养的婴儿出生后数日起每日补充400IU维生素D。旧的错误内容。"
conn.execute("UPDATE baby_wiki SET content = ?, content_hash = NULL "
             "WHERE title = ?", (old_content, title))
conn.commit()
synced = seed_data.sync_seed_content(conn)
conn.commit()
after = conn.execute("SELECT content, content_hash FROM baby_wiki "
                     "WHERE title = ?", (title,)).fetchone()
chk("400IU" not in (after["content"] or ""), "老库里的「每日400IU」已被修正",
    f"实际含 400IU: {'400IU' in (after['content'] or '')}")
chk(bool(after["content_hash"]), "同步后补上了 content_hash")
synced2 = seed_data.sync_seed_content(conn)
conn.commit()
chk(synced2 == 0, "修正后再次同步为 0 改动（幂等）", f"实际 {synced2}")

print("\n=== D. 用户改过的条目：绝不能覆盖 ===")
conn2 = fresh_conn()
seed_data.seed_knowledge_articles(conn2)
seed_data.seed_baby_wiki(conn2)
conn2.commit()
mine = "这是我自己重写的内容，请保留。"
conn2.execute("UPDATE baby_wiki SET content = ? WHERE title = ?",
              (mine, title))
conn2.commit()
before_hash = conn2.execute("SELECT content_hash FROM baby_wiki "
                            "WHERE title = ?", (title,)).fetchone()[0]
synced = seed_data.sync_seed_content(conn2)
conn2.commit()
now = conn2.execute("SELECT content, content_hash FROM baby_wiki "
                    "WHERE title = ?", (title,)).fetchone()
chk(now["content"] == mine, "用户改过的内容没有被覆盖",
    f"实际：{now['content'][:30]}")
chk(now["content_hash"] == before_hash, "哈希也未被改写（仍标记为用户版本）")

print("\n=== E. 百科拼接一致性（hash 对得上的前提）===")
import json
items = json.load(open(os.path.join(seed_data.SEEDS_DIR, "baby_wiki.json"),
                       encoding="utf-8"))
one = next(x for x in items if x.get("treatment"))
conn3 = fresh_conn()
seed_data.seed_baby_wiki(conn3)
conn3.commit()
db_text = conn3.execute("SELECT content FROM baby_wiki WHERE title = ?",
                        (one["title"],)).fetchone()[0]
chk(db_text == seed_data.wiki_full_content(one),
    "播种入库的正文 == 同步时算的正文（同一函数）")
synced = seed_data.sync_seed_content(conn3)
conn3.commit()
chk(synced == 0, "百科同步幂等", f"实际 {synced}")

print()
if fails:
    print("失败 %d 项" % len(fails))
    sys.exit(1)
print("通过")
