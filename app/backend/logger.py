#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xiaoke799
"""
统一日志管理系统。
提供：
- 文件日志（带轮转，防止占满磁盘）
- 控制台日志（开发调试用）
- 结构化格式（时间戳 + 级别 + 模块 + 消息）
- 按组件分级日志（Flask/DB/AI/Blueprint 各自独立级别）
- 请求访问日志（记录每个 API 请求）
- 异常自动捕获和格式化
"""

import os
import sys
import logging
import logging.handlers
import traceback
import threading
import time
from datetime import datetime
from typing import Optional

from constants import (
    DATA_DIR,
    LOG_FORMAT,
    LOG_MAX_BYTES,
    LOG_BACKUP_COUNT,
)

# ==================== PII 脱敏过滤器 ====================

class PIIFilter(logging.Filter):
    """
    个人信息脱敏过滤器。
    自动将日志中的敏感信息替换为占位符，符合飞牛开发规范。
    """
    
    import re
    
    # 匹配模式：中国手机号、邮箱、IPv4、文件路径
    PATTERNS = [
        (re.compile(r'1[3-9]\d{9}'), '[PHONE]'),           # 中国手机号
        (re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'), '[EMAIL]'),  # 邮箱
        (re.compile(r'\b(?:\d{1,3}\.){3}\d{1,3}\b'), '[IP]'),  # IPv4
        (re.compile(r'/[a-zA-Z0-9_./-]{10,}'), '[PATH]'),    # Unix 文件路径
        # 敏感字段名（如 password, token, secret 等字段的值）
        (re.compile(r'((?:password|token|secret|api[_-]?key)\s*[=:]\s*)[^\s,&]+', re.I), r'\1[REDACTED]'),
    ]
    
    def filter(self, record):
        """过滤日志记录中的 PII"""
        if isinstance(record.msg, str):
            for pattern, replacement in self.PATTERNS:
                record.msg = pattern.sub(replacement, record.msg)
        # 处理 args 中的 PII
        if record.args:
            new_args = []
            for arg in record.args:
                if isinstance(arg, str):
                    for pattern, replacement in self.PATTERNS:
                        arg = pattern.sub(replacement, arg)
                new_args.append(arg)
            record.args = tuple(new_args)
        return True


# ==================== 日志目录 ====================

LOG_DIR = os.environ.get("BABYCARE_LOG_DIR", os.path.join(DATA_DIR, "logs"))
LOG_FILE = os.path.join(LOG_DIR, "babycare.log")
ERROR_LOG_FILE = os.path.join(LOG_DIR, "error.log")
ACCESS_LOG_FILE = os.path.join(LOG_DIR, "access.log")
SQL_LOG_FILE = os.path.join(LOG_DIR, "sql.log")

# 确保日志目录存在
os.makedirs(LOG_DIR, exist_ok=True)

# ==================== 日志格式 ====================

# 详细格式（文件用）
DETAILED_FORMAT = logging.Formatter(
    fmt="%(asctime)s [%(levelname)-8s] %(name)-20s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# 简洁格式（控制台用）
CONSOLE_FORMAT = logging.Formatter(
    fmt="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)

# 访问日志格式
ACCESS_FORMAT = logging.Formatter(
    fmt="%(asctime)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# SQL 日志格式
SQL_FORMAT = logging.Formatter(
    fmt="%(asctime)s [%(duration_ms).1fms] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


# ==================== 日志过滤器 ====================

class ErrorFilter(logging.Filter):
    """只记录 ERROR 及以上级别"""
    def filter(self, record):
        return record.levelno >= logging.ERROR


class WarningFilter(logging.Filter):
    """只记录 WARNING 及以上级别"""
    def filter(self, record):
        return record.levelno >= logging.WARNING


class InfoFilter(logging.Filter):
    """只记录 INFO 到 WARNING 之间（排除 ERROR）"""
    def filter(self, record):
        return logging.INFO <= record.levelno < logging.ERROR


class BlueprintFilter(logging.Filter):
    """只记录 Blueprint 相关日志"""
    def filter(self, record):
        return record.name.startswith("blueprints.")


class SQLFilter(logging.Filter):
    """只记录 SQL 相关日志"""
    def filter(self, record):
        return record.name == "babycare.sql"


# ==================== 请求上下文日志适配器 ====================

class RequestContextAdapter(logging.LoggerAdapter):
    """自动附加请求上下文信息（IP、路径、方法）"""
    
    def process(self, kwargs):
        # 尝试从 Flask request 获取上下文
        try:
            from flask import request, has_request_context
            if has_request_context():
                record = kwargs.get('extra', {})
                record['client_ip'] = request.remote_addr or 'unknown'
                record['method'] = request.method
                record['path'] = request.path
                record['request_id'] = getattr(request, 'request_id', 'N/A')
                kwargs['extra'] = record
        except Exception:
            pass
        return kwargs


# ==================== 线程安全的日志初始化锁 ====================

_init_lock = threading.Lock()
_initialized = False


# ==================== 核心配置函数 ====================

def setup_logging(
    level: str = "INFO",
    console_level: str = "WARNING",
    enable_console: bool = True,
    enable_file: bool = True,
) -> None:
    """
    初始化全局日志系统。
    幂等：多次调用只生效一次。
    
    Args:
        level: 文件日志级别 (DEBUG/INFO/WARNING/ERROR/CRITICAL)
        console_level: 控制台日志级别
        enable_console: 是否输出到控制台
        enable_file: 是否输出到文件
    """
    global _initialized
    
    with _init_lock:
        if _initialized:
            return
        _initialized = True
        
        # 根日志器
        root_logger = logging.getLogger("babycare")
        root_logger.setLevel(logging.DEBUG)  # 捕获所有，由 handler 过滤
        root_logger.handlers.clear()
        
        # 文件 handler（主日志）
        if enable_file:
            _setup_file_handlers(root_logger)
        
        # 控制台 handler
        if enable_console:
            _setup_console_handler(console_level)
        
        # 配置第三方库日志级别（减少噪音）
        _configure_third_party_loggers()
        
        root_logger.info("日志系统初始化完成 (级别: %s, 目录: %s)", level, LOG_DIR)


def _setup_file_handlers(root_logger: logging.Logger) -> None:
    """配置文件日志 handler"""
    
    # PII 脱敏过滤器（所有文件 handler 都添加）
    pii_filter = PIIFilter()
    
    # 主日志文件（轮转）
    main_handler = logging.handlers.RotatingFileHandler(
        filename=LOG_FILE,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    main_handler.setLevel(logging.DEBUG)
    main_handler.setFormatter(DETAILED_FORMAT)
    main_handler.addFilter(pii_filter)
    root_logger.addHandler(main_handler)
    
    # 错误日志文件（只记录 ERROR+）
    error_handler = logging.handlers.RotatingFileHandler(
        filename=ERROR_LOG_FILE,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(DETAILED_FORMAT)
    error_handler.addFilter(pii_filter)
    root_logger.addHandler(error_handler)
    
    # 访问日志（独立文件）
    access_logger = logging.getLogger("babycare.access")
    access_handler = logging.handlers.RotatingFileHandler(
        filename=ACCESS_LOG_FILE,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    access_handler.setFormatter(ACCESS_FORMAT)
    access_handler.addFilter(pii_filter)
    access_logger.addHandler(access_handler)
    access_logger.propagate = False  # 不向根日志器传播
    
    # SQL 日志（独立文件）
    sql_logger = logging.getLogger("babycare.sql")
    sql_handler = logging.handlers.RotatingFileHandler(
        filename=SQL_LOG_FILE,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    sql_handler.setFormatter(SQL_FORMAT)
    sql_handler.addFilter(pii_filter)
    sql_logger.addHandler(sql_handler)
    sql_logger.propagate = False


def _setup_console_handler(level: str) -> None:
    """配置控制台日志 handler"""
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(getattr(logging, level.upper(), logging.WARNING))
    console_handler.setFormatter(CONSOLE_FORMAT)
    logging.getLogger("babycare").addHandler(console_handler)


def _configure_third_party_loggers() -> None:
    """降低第三方库的日志级别，减少噪音"""
    # Werkzeug 请求日志（太吵，只保留错误）
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    # urllib3
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    # PIL
    logging.getLogger("PIL").setLevel(logging.WARNING)


# ==================== 便捷函数 ====================

def get_logger(name: str) -> logging.Logger:
    """
    获取指定名称的日志器。
    
    用法：
        logger = get_logger(__name__)
        logger.info("用户 %s 登录成功", username)
    """
    return logging.getLogger(f"babycare.{name}")


def get_access_logger() -> logging.Logger:
    """获取访问日志器（记录每个 API 请求）"""
    return logging.getLogger("babycare.access")


def get_sql_logger() -> logging.Logger:
    """获取 SQL 日志器（记录慢查询）"""
    return logging.getLogger("babycare.sql")


def log_request(response=None) -> None:
    """
    记录 API 请求（在 after_request 中调用）。
    自动从 Flask request 上下文获取信息。
    """
    try:
        from flask import request, has_request_context
        if not has_request_context():
            return
        
        access_logger = get_access_logger()
        status = response.status_code if response else "?"
        duration = getattr(request, '_duration_ms', '?')
        
        access_logger.info(
            "%-6s %-50s | %3s | %6sms | %s",
            request.method,
            request.path[:50],
            status,
            duration,
            request.remote_addr or "local",
        )
    except Exception:
        pass  # 日志记录不应影响主流程


def log_exception(exc: Exception, context: str = "") -> None:
    """
    格式化记录异常（包含完整堆栈）。
    
    用法：
        try:
            ...
        except Exception as e:
            log_exception(e, "处理用户请求")
    """
    logger = get_logger("error")
    tb = traceback.format_exc()
    logger.error("异常 [%s]: %s\n%s", context, exc, tb)


def log_slow_query(sql: str, duration_ms: float, params: tuple = ()) -> None:
    """记录慢查询（超过 100ms）"""
    if duration_ms > 100:
        sql_logger = get_sql_logger()
        sql_logger.warning(
            "慢查询 [%.1fms]: %s | params=%s",
            duration_ms, sql[:200], params[:5] if params else None,
        )


# ==================== 日志查看工具 ====================

def get_recent_logs(lines: int = 100, log_type: str = "main") -> str:
    """
    读取最近的日志行（用于前端/CLI 查看）。
    
    Args:
        lines: 读取行数
        log_type: 日志类型 (main/error/access/sql)
    
    Returns:
        日志文本
    """
    log_files = {
        "main": LOG_FILE,
        "error": ERROR_LOG_FILE,
        "access": ACCESS_LOG_FILE,
        "sql": SQL_LOG_FILE,
    }
    
    filepath = log_files.get(log_type, LOG_FILE)
    if not os.path.exists(filepath):
        return f"日志文件不存在: {filepath}"
    
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            all_lines = f.readlines()
            return "".join(all_lines[-lines:])
    except Exception as e:
        return f"读取日志失败: {e}"


def get_log_stats() -> dict:
    """获取日志文件统计信息"""
    stats = {}
    for name, path in [
        ("main", LOG_FILE),
        ("error", ERROR_LOG_FILE),
        ("access", ACCESS_LOG_FILE),
        ("sql", SQL_LOG_FILE),
    ]:
        if os.path.exists(path):
            size = os.path.getsize(path)
            mtime = os.path.getmtime(path)
            stats[name] = {
                "path": path,
                "size_bytes": size,
                "size_human": _format_size(size),
                "last_modified": datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S"),
            }
        else:
            stats[name] = {"path": path, "size_bytes": 0, "size_human": "0 B"}
    return stats


def _format_size(size_bytes: int) -> str:
    """格式化文件大小"""
    for unit in ["B", "KB", "MB", "GB"]:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


def clear_logs(log_type: str = "all") -> dict:
    """
    清空日志文件。
    
    Args:
        log_type: 日志类型 (main/error/access/sql/all)
    
    Returns:
        操作结果
    """
    log_files = {
        "main": LOG_FILE,
        "error": ERROR_LOG_FILE,
        "access": ACCESS_LOG_FILE,
        "sql": SQL_LOG_FILE,
    }
    
    if log_type == "all":
        targets = list(log_files.values())
    else:
        targets = [log_files.get(log_type, LOG_FILE)]
    
    results = {}
    for path in targets:
        if os.path.exists(path):
            try:
                # 清空文件（不删除文件本身，避免权限问题）
                with open(path, "w", encoding="utf-8") as f:
                    f.write("")
                results[path] = "cleared"
            except Exception as e:
                results[path] = f"error: {e}"
        else:
            results[path] = "not_found"
    
    return results


# ==================== 自动初始化 ====================

# 注意：不在模块级别自动初始化，避免重复导入时产生重复日志。
# 应用启动时应在 server.py 中显式调用 setup_logging()。
# 如需独立使用本模块，调用方需自行调用 setup_logging()。
