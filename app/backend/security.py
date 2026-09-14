#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xiaoke799
"""
安全工具函数。
提供：
- 输入验证（ID、文件名、路径）
- 路径遍历防护
- SQL 注入防护（参数化查询已足够，此处为额外校验）
- 安全响应头
"""

import os
import re
import logging
from typing import Optional, Tuple
from functools import wraps
from flask import request, jsonify, abort

logger = logging.getLogger("babycare.security")

# ==================== 输入验证 ====================

# 有效 ID 格式：正整数
_ID_PATTERN = re.compile(r'^[1-9]\d*$')

# 有效文件名：字母数字中文下划线点横线，不含路径分隔符
_FILENAME_PATTERN = re.compile(r'^[\w\u4e00-\u9fff\.\-\s]+$')

# 危险路径模式
_DANGEROUS_PATH_PATTERNS = [
    re.compile(r'\.\.'),           # 路径遍历 ..
    re.compile(r'~'),              # 用户主目录
    re.compile(r'^/', re.M),       # 绝对路径（在相对路径上下文中）
    re.compile(r'[;|&`$]'),        # 命令注入字符
]


def validate_id(value: str, field_name: str = "ID") -> Tuple[bool, Optional[int], Optional[str]]:
    """
    验证 ID 是否合法。
    
    Returns:
        (is_valid, parsed_id, error_message)
    """
    if not value:
        return False, None, f"{field_name} 不能为空"
    
    # 只允许纯数字
    if not _ID_PATTERN.match(str(value)):
        return False, None, f"{field_name} 必须为正整数"
    
    try:
        parsed = int(value)
        if parsed > 2**31 - 1:  # 防止溢出
            return False, None, f"{field_name} 超出有效范围"
        return True, parsed, None
    except (ValueError, OverflowError):
        return False, None, f"{field_name} 格式错误"


def validate_filename(filename: str) -> Tuple[bool, Optional[str]]:
    """
    验证文件名是否安全（不含路径遍历）。
    
    Returns:
        (is_valid, error_message)
    """
    if not filename or len(filename) > 255:
        return False, "文件名不能为空或超过 255 字符"
    
    # 必须匹配安全字符集
    if not _FILENAME_PATTERN.match(filename):
        return False, "文件名包含非法字符"
    
    # 检查危险模式
    for pattern in _DANGEROUS_PATH_PATTERNS:
        if pattern.search(filename):
            return False, "文件名包含不安全字符"
    
    # 不能以点开头（隐藏文件）
    if filename.startswith('.'):
        return False, "文件名不能以点开头"
    
    return True, None


def safe_path(base_dir: str, user_input: str) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    构建安全路径，防止路径遍历攻击。

    Args:
        base_dir: 允许的基目录
        user_input: 用户输入的相对路径（可含多层目录，如 photos/1/abc.jpg）

    Returns:
        (is_safe, resolved_path, error_message)

    注意：这里的 user_input 是「相对路径」而非「纯文件名」，与
    validate_filename（只接受不含 / 的纯文件名）语义不同，故不复用后者，
    而是用 realpath 边界检查作为真正的防线。
    """
    if not user_input or len(user_input) > 512:
        return False, None, "路径不能为空或超过 512 字符"

    # 拒绝绝对路径与危险模式
    for pattern in _DANGEROUS_PATH_PATTERNS:
        if pattern.search(user_input):
            logger.warning("危险路径拦截: %s", user_input)
            return False, None, "路径包含不安全字符"

    # 解析为绝对路径并验证在基目录内（防 ../ 遍历的核心防线）
    base_dir = os.path.realpath(base_dir)
    target = os.path.realpath(os.path.join(base_dir, user_input))

    if not target.startswith(base_dir + os.sep) and target != base_dir:
        logger.warning("路径遍历尝试: base=%s, input=%s, resolved=%s", base_dir, user_input, target)
        return False, None, "路径不在允许范围内"

    return True, target, None


# ==================== 安全响应头 ====================

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "SAMEORIGIN",
    "X-XSS-Protection": "1; mode=block",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


def add_security_headers(response):
    """添加安全响应头"""
    for header, value in SECURITY_HEADERS.items():
        response.headers[header] = value
    return response


# ==================== 装饰器 ====================

def require_valid_id(param_name: str = "id"):
    """路由装饰器：验证路径参数中的 ID"""
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            raw_id = kwargs.get(param_name) or request.view_args.get(param_name)
            if raw_id is not None:
                is_valid, parsed_id, error = validate_id(str(raw_id), param_name)
                if not is_valid:
                    return jsonify({"success": False, "message": error}), 400
                # 替换为验证后的整数
                if param_name in kwargs:
                    kwargs[param_name] = parsed_id
            return f(*args, **kwargs)
        return wrapper
    return decorator


def sanitize_error(error: Exception, context: str = "") -> str:
    """
    清理错误消息，防止泄露敏感信息（路径、SQL、配置等）。
    用于向用户展示的通用错误消息。
    """
    # 内部错误的通用消息（不暴露细节）
    safe_messages = {
        "FileNotFoundError": "请求的资源不存在",
        "PermissionError": "权限不足",
        "sqlite3.OperationalError": "数据库操作失败",
        "sqlite3.IntegrityError": "数据冲突",
        "ValueError": "参数格式错误",
        "KeyError": "请求的资源不存在",
        "TypeError": "参数类型错误",
    }
    
    error_type = type(error).__name__
    return safe_messages.get(error_type, "操作失败，请稍后重试")
