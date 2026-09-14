#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xiaoke799
"""
错误追踪与聚合模块。

提供：
- record_error(): 将异常写入 error_logs 表
- get_error_stats(): 按模块/级别/时间段聚合统计
- get_recent_errors(): 分页查询错误详情
- get_error_trend(): 按小时/天统计错误趋势
- clear_old_errors(): 清理过期错误记录
"""

import datetime
import traceback
import logging
from typing import Optional, Dict, Any, List

from utils import get_db

logger = logging.getLogger("babycare.error_tracker")


def record_error(
    message: str,
    exc: Optional[Exception] = None,
    level: str = "ERROR",
    module: str = "",
    extra_data: str = "",
    request=None,
) -> Optional[int]:
    """
    将错误记录到 error_logs 表。
    
    Args:
        message: 错误消息
        exc: 异常对象（可选，会提取 traceback）
        level: 日志级别 (ERROR/WARNING/CRITICAL)
        module: 发生错误的模块名
        extra_data: 额外上下文信息（JSON 字符串）
        request: Flask request 对象（可选，自动提取 IP/UA/路径）
    
    Returns:
        插入记录的 ID，失败返回 None
    """
    try:
        tb_str = ""
        if exc:
            tb_str = traceback.format_exception(type(exc), exc, exc.__traceback__)
            tb_str = "".join(tb_str)[-4000:]  # 限制长度

        path = ""
        method = ""
        client_ip = ""
        user_agent = ""
        request_id = ""

        if request:
            path = getattr(request, 'path', '')[:200]
            method = getattr(request, 'method', '')
            client_ip = getattr(request, 'remote_addr', '') or ''
            user_agent = str(getattr(request, 'user_agent', ''))[:200]
            request_id = getattr(request, 'request_id', '')

        db = get_db()
        cursor = db.execute(
            '''INSERT INTO error_logs
               (timestamp, level, module, path, method, message, traceback,
                request_id, client_ip, user_agent, extra_data)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (
                datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                level[:20],
                module[:100],
                path,
                method[:10],
                message[:2000],
                tb_str,
                request_id[:100],
                client_ip[:50],
                user_agent,
                extra_data[:1000],
            )
        )
        db.commit()
        return cursor.lastrowid
    except Exception as e:
        # 记录错误失败时不影响主流程，回退到文件日志
        logger.error("写入错误日志到数据库失败: %s", e)
        return None


def get_error_stats(days: int = 7) -> Dict[str, Any]:
    """
    获取错误统计（按级别、模块、路径分组）。
    
    Args:
        days: 统计最近 N 天
    
    Returns:
        统计数据字典
    """
    try:
        db = get_db()
        since = (datetime.datetime.now() - datetime.timedelta(days=days)).strftime("%Y-%m-%d")

        # 按级别统计
        level_stats = db.execute(
            '''SELECT level, COUNT(*) as cnt FROM error_logs
               WHERE timestamp >= ? GROUP BY level ORDER BY cnt DESC''',
            (since,)
        ).fetchall()

        # 按模块统计
        module_stats = db.execute(
            '''SELECT module, COUNT(*) as cnt FROM error_logs
               WHERE timestamp >= ? AND module != ''
               GROUP BY module ORDER BY cnt DESC LIMIT 20''',
            (since,)
        ).fetchall()

        # 按路径统计
        path_stats = db.execute(
            '''SELECT path, method, COUNT(*) as cnt FROM error_logs
               WHERE timestamp >= ? AND path != ''
               GROUP BY path, method ORDER BY cnt DESC LIMIT 20''',
            (since,)
        ).fetchall()

        # 总数
        total = db.execute(
            'SELECT COUNT(*) FROM error_logs WHERE timestamp >= ?', (since,)
        ).fetchone()[0]

        # 今日
        today = datetime.date.today().strftime("%Y-%m-%d")
        today_count = db.execute(
            'SELECT COUNT(*) FROM error_logs WHERE timestamp >= ?', (today,)
        ).fetchone()[0]

        return {
            "period_days": days,
            "total": total,
            "today": today_count,
            "by_level": [{"level": r[0], "count": r[1]} for r in level_stats],
            "by_module": [{"module": r[0], "count": r[1]} for r in module_stats],
            "by_path": [{"path": r[0], "method": r[1], "count": r[2]} for r in path_stats],
        }
    except Exception as e:
        logger.error("获取错误统计失败: %s", e)
        return {"error": str(e)}


def get_recent_errors(
    limit: int = 50,
    offset: int = 0,
    level: str = "",
    module: str = "",
    days: int = 7,
) -> Dict[str, Any]:
    """
    分页查询错误记录。
    
    Args:
        limit: 每页条数
        offset: 偏移量
        level: 按级别筛选
        module: 按模块筛选
        days: 最近 N 天
    
    Returns:
        分页结果
    """
    try:
        db = get_db()
        since = (datetime.datetime.now() - datetime.timedelta(days=days)).strftime("%Y-%m-%d")

        conditions = ["timestamp >= ?"]
        params: list = [since]

        if level:
            conditions.append("level = ?")
            params.append(level)
        if module:
            conditions.append("module LIKE ?")
            params.append(f"%{module}%")

        where_clause = " AND ".join(conditions)

        # 总数
        total = db.execute(
            f'SELECT COUNT(*) FROM error_logs WHERE {where_clause}', params
        ).fetchone()[0]

        # 分页数据
        query = f'''SELECT id, timestamp, level, module, path, method, message,
                           traceback, request_id, client_ip, user_agent, extra_data
                    FROM error_logs WHERE {where_clause}
                    ORDER BY timestamp DESC LIMIT ? OFFSET ?'''
        params.extend([limit, offset])

        rows = db.execute(query, params).fetchall()
        errors = []
        for r in rows:
            errors.append({
                "id": r[0],
                "timestamp": r[1],
                "level": r[2],
                "module": r[3],
                "path": r[4],
                "method": r[5],
                "message": r[6],
                "traceback": r[7],
                "request_id": r[8],
                "client_ip": r[9],
                "user_agent": r[10],
                "extra_data": r[11],
            })

        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "errors": errors,
        }
    except Exception as e:
        logger.error("查询错误记录失败: %s", e)
        return {"error": str(e), "total": 0, "errors": []}


def get_error_trend(days: int = 7, group_by: str = "day") -> List[Dict[str, Any]]:
    """
    获取错误趋势按时间分组。
    
    Args:
        days: 最近 N 天
        group_by: 分组粒度 ('hour' 或 'day')
    
    Returns:
        趋势数据列表
    """
    try:
        db = get_db()
        since = (datetime.datetime.now() - datetime.timedelta(days=days)).strftime("%Y-%m-%d")

        if group_by == "hour":
            # 按小时分组（最近 24 小时）
            since_h = (datetime.datetime.now() - datetime.timedelta(hours=24)).strftime("%Y-%m-%d %H:00:00")
            rows = db.execute(
                '''SELECT strftime('%H:00', timestamp) as period, COUNT(*) as cnt
                   FROM error_logs WHERE timestamp >= ? AND level = 'ERROR'
                   GROUP BY period ORDER BY period''',
                (since_h,)
            ).fetchall()
        else:
            # 按天分组
            rows = db.execute(
                '''SELECT strftime('%Y-%m-%d', timestamp) as period, COUNT(*) as cnt
                   FROM error_logs WHERE timestamp >= ? AND level = 'ERROR'
                   GROUP BY period ORDER BY period''',
                (since,)
            ).fetchall()

        return [{"period": r[0], "count": r[1]} for r in rows]
    except Exception as e:
        logger.error("获取错误趋势失败: %s", e)
        return []


def get_error_detail(error_id: int) -> Optional[Dict[str, Any]]:
    """
    获取单条错误详情。
    """
    try:
        db = get_db()
        row = db.execute(
            'SELECT * FROM error_logs WHERE id = ?', (error_id,)
        ).fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "timestamp": row[1],
            "level": row[2],
            "module": row[3],
            "path": row[4],
            "method": row[5],
            "message": row[6],
            "traceback": row[7],
            "request_id": row[8],
            "client_ip": row[9],
            "user_agent": row[10],
            "extra_data": row[11],
        }
    except Exception as e:
        logger.error("获取错误详情失败: %s", e)
        return None


def clear_old_errors(days: int = 30) -> int:
    """
    清理超过 N 天的错误记录。
    
    Args:
        days: 保留最近 N 天
    
    Returns:
        清理的条数
    """
    try:
        db = get_db()
        cutoff = (datetime.datetime.now() - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
        cursor = db.execute('DELETE FROM error_logs WHERE timestamp < ?', (cutoff,))
        db.commit()
        count = cursor.rowcount
        logger.info("清理了 %d 条过期错误记录（%s 之前）", count, cutoff)
        return count
    except Exception as e:
        logger.error("清理错误记录失败: %s", e)
        return 0


def log_error_from_exception(exc: Exception, context: str = "", request=None) -> Optional[int]:
    """
    便捷函数：从异常对象直接记录错误（自动提取信息）。
    
    Args:
        exc: 异常对象
        context: 上下文描述
        request: Flask request 对象
    
    Returns:
        记录 ID
    """
    module = ""
    if exc.__class__.__module__ and exc.__class__.__module__ != "builtins":
        module = exc.__class__.__module__
    class_name = exc.__class__.__name__
    message = f"{context}: {class_name}: {exc}" if context else f"{class_name}: {exc}"

    return record_error(
        message=message,
        exc=exc,
        level="ERROR",
        module=module,
        request=request,
    )
