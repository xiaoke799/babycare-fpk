#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xiaoke799
"""
数据库连接管理与重试机制。
提供：
- 线程安全的连接池
- 自动 busy_timeout 配置
- 可重试的数据库操作装饰器
- WHO 参考数据库只读连接
"""

import os
import sqlite3
import time
import functools
import logging
from contextlib import contextmanager
from typing import Optional, Callable, Any

from constants import (
    DB_PATH,
    WHO_DB_PATH,
    DB_BUSY_TIMEOUT_MS,
    DB_MAX_RETRIES,
    DB_RETRY_DELAY_BASE,
)

logger = logging.getLogger(__name__)


class DatabaseManager:
    """
    数据库管理器（单例模式）。
    管理主数据库和 WHO 参考数据库的连接。
    """

    _instance: Optional["DatabaseManager"] = None
    _initialized: bool = False

    def __new__(cls) -> "DatabaseManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        if not self._initialized:
            self._db_path = DB_PATH
            self._who_db_path = WHO_DB_PATH
            self._busy_timeout = DB_BUSY_TIMEOUT_MS
            self._initialized = True

    @contextmanager
    def get_connection(self, db_path: str = None):
        """
        获取数据库连接的上下文管理器。
        自动配置 busy_timeout 和 WAL 模式。
        
        Yields:
            sqlite3.Connection: 配置好的数据库连接
            
        Example:
            with db_manager.get_connection() as conn:
                cursor = conn.execute("SELECT * FROM babies")
        """
        path = db_path or self._db_path
        conn = sqlite3.connect(path, timeout=self._busy_timeout / 1000)
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout = {self._busy_timeout};")
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA foreign_keys = ON;")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @contextmanager
    def get_who_connection(self):
        """
        获取 WHO 参考数据库的只读连接。
        
        Yields:
            sqlite3.Connection: WHO 数据库只读连接
        """
        if not os.path.exists(self._who_db_path):
            logger.warning("WHO 参考数据库不存在: %s", self._who_db_path)
            yield None
            return

        conn = sqlite3.connect(
            f"file:{self._who_db_path}?mode=ro",
            uri=True,
            timeout=5,
        )
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def execute(
        self,
        query: str,
        params: tuple = None,
        fetch_one: bool = False,
        fetch_all: bool = False,
        db_path: str = None,
    ) -> Any:
        """
        便捷执行方法。
        
        Args:
            query: SQL 查询语句
            params: 查询参数
            fetch_one: 是否返回单行
            fetch_all: 是否返回所有行
            db_path: 自定义数据库路径
            
        Returns:
            查询结果或 None
        """
        with self.get_connection(db_path) as conn:
            cursor = conn.execute(query, params or ())
            if fetch_one:
                return cursor.fetchone()
            if fetch_all:
                return cursor.fetchall()
            return cursor.lastrowid

    def executemany(self, query: str, params_list: list, db_path: str = None) -> int:
        """
        批量执行方法。
        
        Args:
            query: SQL 语句
            params_list: 参数列表
            db_path: 自定义数据库路径
            
        Returns:
            影响的行数
        """
        with self.get_connection(db_path) as conn:
            cursor = conn.executemany(query, params_list)
            return cursor.rowcount


# 全局单例实例
db_manager = DatabaseManager()


def with_db_connection(func: Callable) -> Callable:
    """
    为函数注入数据库连接的装饰器。
    
    被装饰函数的第一个参数将接收数据库连接。
    
    Example:
        @with_db_connection
        def get_all_babies(conn):
            return conn.execute("SELECT * FROM babies").fetchall()
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        with db_manager.get_connection() as conn:
            return func(conn, *args, **kwargs)
    return wrapper


def db_retry(
    max_retries: int = DB_MAX_RETRIES,
    delay_base: float = DB_RETRY_DELAY_BASE,
    retryable_exceptions: tuple = (sqlite3.OperationalError,),
) -> Callable:
    """
    数据库操作重试装饰器。
    自动处理 SQLite 锁冲突（database is locked）。
    
    Args:
        max_retries: 最大重试次数
        delay_base: 延迟基数（秒），实际延迟 = delay_base * (2 ** attempt)
        retryable_exceptions: 可重试的异常类型
        
    Example:
        @db_retry(max_retries=3)
        def update_baby_weight(baby_id: int, weight: float) -> bool:
            db_manager.execute("UPDATE babies SET weight=? WHERE id=?", (weight, baby_id))
            return True
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except retryable_exceptions as e:
                    last_exception = e
                    if "locked" in str(e).lower() or "busy" in str(e).lower():
                        if attempt < max_retries:
                            delay = delay_base * (2 ** attempt)
                            logger.warning(
                                "数据库锁冲突，第 %d/%d 次重试 (%.1fs): %s",
                                attempt + 1, max_retries, delay, e,
                            )
                            time.sleep(delay)
                            continue
                    raise
            raise last_exception
        return wrapper
    return decorator


def init_db_schema(conn: sqlite3.Connection) -> None:
    """
    初始化数据库表结构。
    仅包含 CREATE TABLE 语句，不包含种子数据。
    
    Args:
        conn: 数据库连接
    """
    # 此处将在后续重构中从 server.py 迁移
    # 先保留函数签名，确保导入链完整
    pass


def close_all_connections() -> None:
    """清理所有数据库连接（用于应用关闭时）."""
    # SQLite 连接由上下文管理器自动关闭，此处预留扩展点
    pass


# ==================== 调度器专用连接 ====================
# 起因（2026-10-05 审查发现，严重）：
# server.py 曾定义一个同名闭包 `def get_db(): from database import get_db`，
# 而**本模块从来没有 get_db**（只有 with_db_connection / db_retry / …），
# 于是调度器每次检查都 ImportError，被 reminder_scheduler 里的
# `except Exception: return` 吞掉 —— 结果是
#   喂奶/睡眠/尿布/疫苗/用药/体检提醒**一条都不会发**，
#   而启动日志照旧打印「已初始化」，极具欺骗性。
#
# 另一层：提醒跑在后台线程里，Flask 的 `g` 不存在，utils.get_db()
# 同样用不了（它依赖 app_context）。所以必须给调度器一条独立连接。
#
# 语义：每次返回一条**干净的新连接**，并把上一次那条关掉。
# 于是最多只有一条连接存活 —— 既不泄漏，也不用改动调度器里 5 处调用点。
# 连接存 thread-local：多线程各自持有，不会互相把对方的连接关掉。

import threading as _threading

_sched_local = _threading.local()


def get_scheduler_db() -> sqlite3.Connection:
    """后台线程（提醒调度器）专用：返回独立连接，不依赖 Flask app_context。"""
    prev = getattr(_sched_local, "conn", None)
    if prev is not None:
        try:
            prev.close()
        except Exception:  # noqa: BLE001 — 关不掉也要继续给新连接
            pass
    mgr = db_manager
    conn = sqlite3.connect(mgr._db_path, timeout=mgr._busy_timeout / 1000)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout = {mgr._busy_timeout};")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    _sched_local.conn = conn
    return conn


def close_scheduler_db() -> None:
    """线程退出前显式关掉自己的连接（可选，但更干净）。"""
    conn = getattr(_sched_local, "conn", None)
    if conn is not None:
        try:
            conn.close()
        finally:
            _sched_local.conn = None
