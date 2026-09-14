#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xiaoke799
"""
共享工具函数模块。
供 server.py 与 blueprints/ 共同使用，避免循环导入。
"""

import datetime
import sqlite3
from flask import g, request, jsonify

from constants import DB_PATH


def get_db():
    """获取数据库连接（线程安全，存于 Flask g 对象）"""
    if 'db' not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        # busy_timeout: 遇到写锁时等待而非立即抛 "database is locked"
        # （gunicorn 多 worker 并发启动/写入场景下避免偶发 500）
        g.db.execute("PRAGMA busy_timeout=5000")
        g.db.execute("PRAGMA journal_mode=WAL")
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db


def json_body():
    """安全获取 JSON 请求体，永远返回 dict。

    直接写 `request.json` / `request.get_json()` 有三个坑：
      1) 客户端没带 Content-Type: application/json → 抛 415，打断业务逻辑；
      2) body 是字面量 null（前端 JSON.stringify(null) 就会发 "null"）→ 返回 None，
         紧接着 data.get(...) 直接 AttributeError → 接口 500；
      3) body 是数组 → 返回 list，同样没有 .get() → 500。
    统一走这里：非 dict 一律当空 dict，业务代码只需要校验必填字段。
    注意用 silent=True，让「没带 Content-Type」也退化成一个空 body 而不是 415。
    """
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def close_db():
    """关闭当前请求的数据库连接（用于备份恢复前）"""
    db = g.pop('db', None)
    if db is not None:
        db.close()


def row_to_dict(row):
    """将 sqlite3.Row 转为 dict（None 安全）"""
    if row is None:
        return None
    return dict(row)


def rows_to_list(rows):
    """将 sqlite3.Row 列表转为 dict 列表"""
    return [dict(r) for r in rows]


def get_baby_age(birthday_str):
    """计算宝宝年龄（中文描述，供展示用）"""
    try:
        birthday = datetime.datetime.strptime(birthday_str, '%Y-%m-%d').date()
        today = datetime.date.today()
        days = (today - birthday).days
        if days < 0:
            return "未出生"
        if days < 30:
            return f"{days}天"
        if days < 365:
            months = days // 30
            remaining_days = days % 30
            if remaining_days > 0:
                return f"{months}个月{remaining_days}天"
            return f"{months}个月"
        years = days // 365
        remaining_months = (days % 365) // 30
        if remaining_months > 0:
            return f"{years}岁{remaining_months}个月"
        return f"{years}岁"
    except (ValueError, TypeError):
        return "未知"


def get_baby_age_months(birthday_str):
    """计算宝宝月龄（数值型，供逻辑判断用）"""
    try:
        birthday = datetime.datetime.strptime(birthday_str, '%Y-%m-%d').date()
        today = datetime.date.today()
        days = (today - birthday).days
        return max(0, days // 30)
    except (ValueError, TypeError):
        return 0


def _get_int_arg(name, default, minimum=None, maximum=None):
    """从 query string 解析整数参数。返回 (value, None)；非法时返回 (None, 错误响应)"""
    raw = request.args.get(name)
    if raw is None or raw == '':
        return default, None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None, (jsonify({'success': False, 'message': f'参数 {name} 必须为整数'}), 400)
    if minimum is not None and value < minimum:
        return None, (jsonify({'success': False, 'message': f'参数 {name} 不能小于 {minimum}'}), 400)
    if maximum is not None and value > maximum:
        return None, (jsonify({'success': False, 'message': f'参数 {name} 不能大于 {maximum}'}), 400)
    return value, None


def _get_app_setting(key):
    """读取 app_settings 配置项"""
    try:
        db = get_db()
        row = db.execute('SELECT value FROM app_settings WHERE key = ?', (key,)).fetchone()
        return row['value'] if row else None
    except Exception:
        return None


def _set_app_setting(key, value):
    """写入 app_settings 配置项"""
    db = get_db()
    db.execute('INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)', (key, value))
    db.commit()


# 为了向后兼容：从 server.py 导出的函数名称相同
# blueprints 应改为 from utils import get_db, row_to_dict, ...
