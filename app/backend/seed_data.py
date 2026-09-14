#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xiaoke799
"""
种子数据加载模块。
从 seeds/ 目录读取 JSON 文件，将初始化数据插入到参考表中。
仅当表为空时才插入，避免重复。
"""

import json
import os
import logging

from constants import DB_PATH

logger = logging.getLogger(__name__)

# seeds 目录路径
SEEDS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seeds")

# 种子数据文件列表（按依赖顺序）
SEED_FILES = [
    ("knowledge_articles", "knowledge_articles.json"),
    ("baby_wiki", "baby_wiki.json"),
    ("baby_recipes", "baby_recipes.json"),
    ("baby_activities", "baby_activities.json"),
]


def _load_json(filename: str) -> list:
    """从 seeds 目录加载 JSON 文件"""
    filepath = os.path.join(SEEDS_DIR, filename)
    if not os.path.exists(filepath):
        logger.warning("种子文件不存在: %s", filepath)
        return []
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def _table_is_empty(table_name: str) -> bool:
    """检查表是否为空"""
    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()
        return row[0] == 0
    except Exception:
        return False
    finally:
        conn.close()


def seed_knowledge_articles(conn) -> int:
    """填充育儿知识文章"""
    if not _table_is_empty("knowledge_articles"):
        return 0
    articles = _load_json("knowledge_articles.json")
    if not articles:
        return 0
    conn.executemany(
        "INSERT INTO knowledge_articles (category, title, age_range, content) VALUES (?, ?, ?, ?)",
        [(a["category"], a["title"], a.get("age_range", ""), a["content"]) for a in articles],
    )
    logger.info("已加载 %d 篇育儿知识文章", len(articles))
    return len(articles)


def seed_baby_wiki(conn) -> int:
    """填充健康百科"""
    if not _table_is_empty("baby_wiki"):
        return 0
    items = _load_json("baby_wiki.json")
    if not items:
        return 0
    # 将扩展字段合并到 content 字段（兼容旧表结构）
    rows = []
    for w in items:
        content = w.get("content", "")
        # 如果有扩展字段，追加到 content 下面
        extra_parts = []
        if w.get("cause"):
            extra_parts.append(f"\n\n【病因】\n{w['cause']}")
        if w.get("treatment"):
            extra_parts.append(f"\n\n【处理】\n{w['treatment']}")
        if w.get("prevention"):
            extra_parts.append(f"\n\n【预防】\n{w['prevention']}")
        if w.get("when_to_see_doctor"):
            extra_parts.append(f"\n\n【就医信号】\n{w['when_to_see_doctor']}")
        full_content = content + "".join(extra_parts)
        rows.append((w["category"], w["title"], full_content, w.get("symptoms", "")))
    conn.executemany(
        "INSERT INTO baby_wiki (category, title, content, symptoms) VALUES (?, ?, ?, ?)",
        rows,
    )
    logger.info("已加载 %d 条健康百科", len(items))
    return len(items)


def seed_baby_recipes(conn) -> int:
    """填充辅食食谱"""
    if not _table_is_empty("baby_recipes"):
        return 0
    recipes = _load_json("baby_recipes.json")
    if not recipes:
        return 0
    conn.executemany(
        """INSERT INTO baby_recipes
           (title, icon, age_group, category, ingredients, instructions, allergens,
            prep_time, cook_time, servings, nutrition, tips, difficulty)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            (
                r["title"], r.get("icon", ""), r["age_group"], r["category"],
                r["ingredients"], r["instructions"], r.get("allergens", ""),
                r.get("prep_time", ""), r.get("cook_time", ""), r.get("servings", ""),
                r.get("nutrition", ""), r.get("tips", ""), r.get("difficulty", "简单"),
            )
            for r in recipes
        ],
    )
    logger.info("已加载 %d 个辅食食谱", len(recipes))
    return len(recipes)


def seed_baby_activities(conn) -> int:
    """填充活动游戏推荐"""
    if not _table_is_empty("baby_activities"):
        return 0
    activities = _load_json("baby_activities.json")
    if not activities:
        return 0
    conn.executemany(
        """INSERT INTO baby_activities
           (title, icon, category, min_age_months, max_age_months, summary, duration,
            materials, steps, benefits, difficulty, tips, tags, seasonal, indoor)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            (
                a.get("title", ""), a.get("icon", ""), a.get("category", "general"),
                a.get("min_age_months", 0), a.get("max_age_months", 36),
                a.get("summary", ""),
                a.get("duration", "10分钟"),
                a.get("materials", ""),
                a.get("steps", ""),
                a.get("benefits", ""),
                a.get("difficulty", "简单"),
                a.get("tips", ""),
                a.get("tags", ""),
                a.get("seasonal", "all"),
                a.get("indoor", 1),
            )
            for a in activities
        ],
    )
    logger.info("已加载 %d 个活动推荐", len(activities))
    return len(activities)


# 种子函数映射（按执行顺序）
SEED_FUNCTIONS = [
    seed_knowledge_articles,
    seed_baby_wiki,
    seed_baby_recipes,
    seed_baby_activities,
]


def load_all_seeds() -> dict:
    """
    加载所有种子数据。
    返回每个表加载的记录数。
    """
    from database import db_manager

    results = {}
    with db_manager.get_connection() as conn:
        for func in SEED_FUNCTIONS:
            try:
                count = func(conn)
                results[func.__name__] = count
            except Exception as e:
                logger.error("加载种子数据失败 (%s): %s", func.__name__, e)
                results[func.__name__] = f"error: {e}"
        conn.commit()

    total = sum(v for v in results.values() if isinstance(v, int))
    logger.info("种子数据加载完成，总计 %d 条记录", total)
    return results


if __name__ == "__main__":
    # 命令行运行测试
    import sys
    logging.basicConfig(level=logging.INFO)
    if not os.path.exists(DB_PATH):
        print(f"数据库不存在: {DB_PATH}")
        print("请先运行 init_db() 创建数据库表结构")
        sys.exit(1)
    results = load_all_seeds()
    for name, count in results.items():
        print(f"  {name}: {count}")
