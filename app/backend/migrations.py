#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xiaoke799
"""
数据库迁移系统。
提供版本化的 schema 升级，支持：
- 自动检测当前版本并执行待处理迁移
- 迁移前自动备份
- 幂等迁移（可安全重试）
- 迁移历史记录
"""

import os
import sqlite3
import shutil
import logging
import time
from datetime import datetime
from typing import List, Callable, Optional

from constants import DB_PATH, DATA_DIR

logger = logging.getLogger("babycare.migrations")

# ==================== 迁移注册表 ====================

# 迁移函数列表，按版本号排序
# 每个函数接收 sqlite3.Connection 参数，在事务中执行
_MIGRATIONS: List[tuple] = []


def migration(version: int, description: str = ""):
    """装饰器：注册迁移函数"""
    def decorator(func: Callable[[sqlite3.Connection], None]):
        _MIGRATIONS.append((version, description, func))
        _MIGRATIONS.sort(key=lambda x: x[0])  # 按版本排序
        func._migration_version = version
        func._migration_desc = description
        return func
    return decorator


# ==================== 迁移管理 ====================

MIGRATION_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS _schema_migrations (
    version INTEGER PRIMARY KEY,
    description TEXT DEFAULT '',
    applied_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    execution_time_ms INTEGER DEFAULT 0
);
"""


def _get_current_version(conn: sqlite3.Connection) -> int:
    """获取当前数据库 schema 版本"""
    try:
        row = conn.execute(
            "SELECT MAX(version) FROM _schema_migrations"
        ).fetchone()
        return row[0] if row[0] else 0
    except sqlite3.OperationalError:
        # 表不存在，说明是初始版本
        return 0


def _backup_db() -> Optional[str]:
    """迁移前自动备份数据库"""
    if not os.path.exists(DB_PATH):
        return None
    
    backup_dir = os.path.join(DATA_DIR, "backups", "db")
    os.makedirs(backup_dir, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(backup_dir, f"pre-migration-{timestamp}.db")
    
    try:
        # 使用 SQLite 的 VACUUM INTO 进行热备份
        conn = sqlite3.connect(DB_PATH)
        conn.execute("VACUUM INTO ?", (backup_path,))
        conn.close()
        logger.info("数据库已备份到: %s", backup_path)
        return backup_path
    except Exception as e:
        logger.warning("数据库备份失败: %s", e)
        return None


def _cleanup_old_backups(keep: int = 5) -> None:
    """清理旧备份，只保留最近 N 个"""
    backup_dir = os.path.join(DATA_DIR, "backups", "db")
    if not os.path.isdir(backup_dir):
        return
    
    backups = sorted([
        os.path.join(backup_dir, f)
        for f in os.listdir(backup_dir)
        if f.startswith("pre-migration-") and f.endswith(".db")
    ])
    
    # 删除旧备份
    for old_backup in backups[:-keep]:
        try:
            os.remove(old_backup)
            logger.debug("删除旧备份: %s", old_backup)
        except OSError:
            pass


def run_migrations(conn: Optional[sqlite3.Connection] = None) -> dict:
    """
    执行所有待处理的迁移。
    
    Args:
        conn: 可选的数据库连接。如果为 None，自动创建。
    
    Returns:
        迁移结果统计
    """
    result = {
        "current_version": 0,
        "target_version": 0,
        "applied_count": 0,
        "applied_versions": [],
        "backup_path": None,
        "errors": [],
    }
    
    should_close = False
    if conn is None:
        conn = sqlite3.connect(DB_PATH)
        try:
            # WAL 是数据库级持久设置，设一次即可。并发启动时另一个进程可能正持锁，
            # 这里失败不影响正确性（先到的进程会设好），所以只吞掉不中断。
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError:
            pass
        conn.execute("PRAGMA foreign_keys=ON")
        should_close = True
    
    try:
        # 确保迁移表存在
        conn.executescript(MIGRATION_TABLE_SQL)
        
        current = _get_current_version(conn)
        result["current_version"] = current
        
        # 找到最新迁移版本
        latest = _MIGRATIONS[-1][0] if _MIGRATIONS else 0
        result["target_version"] = latest
        
        if current >= latest:
            logger.info("数据库已是最新版本 (v%d)", current)
            return result
        
        logger.info("数据库迁移: v%d → v%d", current, latest)
        
        # 迁移前备份
        result["backup_path"] = _backup_db()
        
        # 执行待处理迁移
        for version, description, func in _MIGRATIONS:
            if version <= current:
                continue  # 已应用的跳过
            
            start_time = time.time()
            try:
                # 在事务中执行迁移
                conn.execute("BEGIN IMMEDIATE")

                # 拿到锁后再确认一次版本。gunicorn 是多 worker 进程，两个 worker 会各自
                # 跑到这里；外面的 current 是抢锁前读的，可能已经过期。不复查会导致
                # 重复执行（v2 有幂等保护还好）并撞 _schema_migrations 主键报错。
                already = conn.execute(
                    "SELECT 1 FROM _schema_migrations WHERE version = ?", (version,)
                ).fetchone()
                if already:
                    conn.rollback()
                    continue  # 别的进程已经应用过了，不算失败

                func(conn)
                conn.execute(
                    "INSERT INTO _schema_migrations (version, description, execution_time_ms) VALUES (?, ?, ?)",
                    (version, description, int((time.time() - start_time) * 1000))
                )
                conn.commit()

                result["applied_count"] += 1
                result["applied_versions"].append(version)
                logger.info("迁移 v%d 应用成功: %s", version, description)

            except Exception as e:
                conn.rollback()
                error_msg = f"迁移 v%d 失败: %s" % (version, e)
                result["errors"].append(error_msg)
                logger.error(error_msg)
                # 迁移失败不继续（避免级联错误）
                break
        
        # 清理旧备份
        _cleanup_old_backups()
        
    finally:
        if should_close:
            conn.close()
    
    return result


def get_migration_history(conn: Optional[sqlite3.Connection] = None) -> List[dict]:
    """获取迁移历史记录"""
    should_close = False
    if conn is None:
        conn = sqlite3.connect(DB_PATH)
        should_close = True
    
    try:
        conn.executescript(MIGRATION_TABLE_SQL)
        rows = conn.execute(
            "SELECT version, description, applied_at, execution_time_ms "
            "FROM _schema_migrations ORDER BY version"
        ).fetchall()
        return [
            {
                "version": r[0],
                "description": r[1],
                "applied_at": r[2],
                "execution_time_ms": r[3],
            }
            for r in rows
        ]
    except sqlite3.OperationalError:
        return []
    finally:
        if should_close:
            conn.close()


# ==================== 迁移定义 ====================
# 在此处定义所有迁移，按版本号排序

@migration(1, "初始 schema（由 init_db 创建）")
def _migration_v1(conn: sqlite3.Connection):
    """v1 是初始版本，由 init_db() 创建。此迁移仅作为占位。"""
    pass


@migration(2, "里程碑表增加 is_first 字段，区分「第一次」与普通里程碑")
def _migration_v2(conn: sqlite3.Connection):
    """
    里程碑与「第一次」共用 milestones 表，此前两者无法区分。
    新增 is_first 标记，并把标题以「第一次」开头的历史记录自动回填为 1，
    这样老数据不需要手工整理就能落到正确的页面里。
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(milestones)").fetchall()}
    if "is_first" not in cols:
        conn.execute("ALTER TABLE milestones ADD COLUMN is_first INTEGER NOT NULL DEFAULT 0")
    conn.execute(
        "UPDATE milestones SET is_first = 1 "
        "WHERE is_first = 0 AND (title LIKE '第一次%' OR title LIKE '首次%')"
    )
    # 归一化历史分类：早期「第一次」页用过 physical / 精细动作 等写法，
    # 统一收敛到 motor/language/social/cognitive/other，避免筛选时掉进「其他」。
    for legacy in ("physical", "精细动作", "大运动", "运动"):
        conn.execute(
            "UPDATE milestones SET category = 'motor' WHERE category = ?",
            (legacy,)
        )
    conn.execute(
        "UPDATE milestones SET category = 'other' "
        "WHERE category NOT IN ('motor', 'language', 'social', 'cognitive', 'other')"
    )


@migration(3, "知识库、食谱、活动页面补全所需字段")
def _migration_v3(conn: sqlite3.Connection):
    """
    为知识文章、辅食食谱、亲子活动页面补充前端展示与筛选所需字段：
    - knowledge_articles.age_range：文章适用月龄范围
    - baby_recipes.icon/cook_time/servings/nutrition/tips/difficulty：食谱卡片与详情
    - baby_activities.icon/summary/duration/tips：活动卡片与详情
    """
    def add_column(table: str, ddl: str):
        col_name = ddl.split()[0]
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if col_name not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")

    add_column('knowledge_articles', "age_range TEXT DEFAULT ''")
    add_column('baby_recipes', "icon TEXT DEFAULT ''")
    add_column('baby_recipes', "cook_time TEXT DEFAULT ''")
    add_column('baby_recipes', "servings TEXT DEFAULT ''")
    add_column('baby_recipes', "nutrition TEXT DEFAULT ''")
    add_column('baby_recipes', "tips TEXT DEFAULT ''")
    add_column('baby_recipes', "difficulty TEXT DEFAULT '简单'")
    add_column('baby_activities', "icon TEXT DEFAULT ''")
    add_column('baby_activities', "summary TEXT DEFAULT ''")
    add_column('baby_activities', "duration TEXT DEFAULT ''")
    add_column('baby_activities', "tips TEXT DEFAULT ''")


@migration(4, "比价记账：用品价格表 product_prices + 支出记账表 expense_records，并迁移旧纸尿裤价格数据")
def _migration_v4(conn: sqlite3.Connection):
    """
    将纸尿裤比价泛化为多品类育儿用品比价，并新增记账功能：
    - product_prices：用品价格记录（品类/品牌/规格/包装量/单价），替代单品的 diaper_prices
    - expense_records：支出记账记录（分类/金额/品名/渠道/日期）
    - 旧 diaper_prices 数据一次性搬迁到 product_prices（category='diaper'），保留原表不删
    """
    conn.execute('''
        CREATE TABLE IF NOT EXISTS product_prices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL DEFAULT 'diaper',
            brand TEXT NOT NULL,
            series TEXT DEFAULT '',
            spec TEXT DEFAULT '',
            package_size REAL DEFAULT 0,
            unit TEXT DEFAULT '片',
            price REAL DEFAULT 0,
            purchase_channel TEXT DEFAULT '',
            purchase_date TEXT DEFAULT '',
            note TEXT DEFAULT '',
            updated_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS expense_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL DEFAULT 'other',
            amount REAL NOT NULL DEFAULT 0,
            item_name TEXT DEFAULT '',
            purchase_channel TEXT DEFAULT '',
            expense_date TEXT NOT NULL,
            note TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
    ''')
    conn.execute("CREATE INDEX IF NOT EXISTS idx_product_prices_cat ON product_prices(category, spec)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_expense_records_date ON expense_records(expense_date)")

    # 一次性搬迁旧纸尿裤价格记录（product_prices 为空且旧表存在时执行）
    has_old = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'diaper_prices'"
    ).fetchone()
    if has_old:
        new_count = conn.execute("SELECT COUNT(*) FROM product_prices").fetchone()[0]
        old_count = conn.execute("SELECT COUNT(*) FROM diaper_prices").fetchone()[0]
        if new_count == 0 and old_count > 0:
            conn.execute('''
                INSERT INTO product_prices (category, brand, series, spec, package_size, unit,
                                            price, purchase_channel, purchase_date, note, updated_at)
                SELECT 'diaper', brand, series, spec,
                       CASE WHEN count_per_pack > 0 THEN count_per_pack ELSE 0 END,
                       '片', price, source, '', '',
                       updated_at
                FROM diaper_prices
            ''')


@migration(5, "疫苗详情表补全字段：不良反应追踪/接种单位/批号/抗体检测/接种部位/医生")

def _migration_v5(conn: sqlite3.Connection):
    """
    vaccine_details 表早期版本缺少完整追踪字段。
    补齐：manufacturer/batch_number/hospital/doctor/injection_site/
          has_reaction/reaction_detail/reaction_severity/next_dose_date/
          antibody_test_date/antibody_result。
    """
    def add_column(table: str, ddl: str):
        col_name = ddl.split()[0]
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if col_name not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")

    add_column('vaccine_details', "manufacturer TEXT DEFAULT ''")
    add_column('vaccine_details', "batch_number TEXT DEFAULT ''")
    add_column('vaccine_details', "hospital TEXT DEFAULT ''")
    add_column('vaccine_details', "doctor TEXT DEFAULT ''")
    add_column('vaccine_details', "injection_site TEXT DEFAULT ''")
    add_column('vaccine_details', "has_reaction INTEGER DEFAULT 0")
    add_column('vaccine_details', "reaction_detail TEXT DEFAULT ''")
    add_column('vaccine_details', "reaction_severity TEXT DEFAULT 'none'")
    add_column('vaccine_details', "next_dose_date TEXT DEFAULT NULL")
    add_column('vaccine_details', "antibody_test_date TEXT DEFAULT NULL")
    add_column('vaccine_details', "antibody_result TEXT DEFAULT ''")
    add_column('vaccine_details', "created_at TEXT DEFAULT (datetime('now', 'localtime'))")

    # 为疫苗详情表创建索引
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vaccine_details_baby ON vaccine_details(baby_id, scheduled_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vaccine_details_status ON vaccine_details(baby_id, status)")

    # 将旧 vaccines 表数据迁移到 vaccine_details（如果 vaccine_details 为空）
    has_old_vaccines = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'vaccines'"
    ).fetchone()
    if has_old_vaccines:
        new_count = conn.execute("SELECT COUNT(*) FROM vaccine_details").fetchone()[0]
        old_count = conn.execute("SELECT COUNT(*) FROM vaccines").fetchone()[0]
        if new_count == 0 and old_count > 0:
            conn.execute('''
                INSERT INTO vaccine_details
                    (baby_id, vaccine_name, vaccine_type, dose_number, scheduled_date,
                     actual_date, status, note, created_at)
                SELECT baby_id, vaccine_name, vaccine_type, dose_number, scheduled_date,
                       actual_date, status, note, created_at
                FROM vaccines
            ''')


@migration(6, "错误日志表 error_logs：创建表 + 索引，用于错误聚合统计和排查")
def _migration_v6(conn: sqlite3.Connection):
    """
    error_logs 表用于持久化记录应用错误。
     indexer 让管理员可以按模块/时间/级别聚合查看错误。
    """
    # 表已在 init_db 中创建，迁移仅补建索引
    conn.execute("CREATE INDEX IF NOT EXISTS idx_error_logs_timestamp ON error_logs(timestamp)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_error_logs_level ON error_logs(level)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_error_logs_module ON error_logs(module)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_error_logs_path ON error_logs(path)")
