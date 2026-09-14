#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xiaoke799
"""
用户上下文管理。
从 fnOS 网关头部获取用户身份，用于：
- 数据隔离（多用户场景）
- 操作审计（谁做了什么）
- 权限控制
"""

import logging
from typing import Optional, Dict
from flask import request, g, has_request_context

logger = logging.getLogger("babycare.user_context")


# ==================== 用户身份 ====================

class UserContext:
    """当前请求的用户上下文"""
    
    def __init__(self):
        self.user_id: str = ""
        self.username: str = ""
        self.is_admin: bool = False
        self.is_authenticated: bool = False
        self.source: str = "unknown"  # gateway / session / anonymous
    
    @property
    def display_name(self) -> str:
        """显示名称"""
        return self.username or self.user_id or "访客"
    
    def to_dict(self) -> dict:
        return {
            "user_id": self.user_id,
            "username": self.username,
            "is_admin": self.is_admin,
            "is_authenticated": self.is_authenticated,
            "source": self.source,
        }


def get_current_user() -> UserContext:
    """
    获取当前用户上下文。
    优先级：网关头部 > 会话 > 匿名
    """
    if not has_request_context():
        return UserContext()
    
    # 尝试从 Flask g 对象获取（已缓存）
    if hasattr(g, '_user_context'):
        return g._user_context
    
    user = UserContext()
    
    # 1. 尝试从网关头部获取（仅网关模式可信）
    # 注意：这些头部由 fnOS 网关注入。只有当请求确实带网关前缀
    # （GatewayPrefixMiddleware 标记 request.gateway_trusted）时才采信，
    # 防止直连 socket 伪造头部冒充管理员。
    gateway_trusted = bool(getattr(request, 'gateway_trusted', False))
    trim_user_id = request.headers.get("X-Trim-Userid", "")
    trim_username = request.headers.get("X-Trim-Username", "")
    trim_is_admin = request.headers.get("X-Trim-Isadmin", "false")
    
    if gateway_trusted and trim_user_id:
        user.user_id = trim_user_id
        user.username = trim_username
        user.is_admin = trim_is_admin.lower() == "true"
        user.is_authenticated = True
        user.source = "gateway"
    else:
        # 2. 回退到会话认证（本地密码登录）
        from flask import session
        if session.get("authenticated"):
            user.user_id = "local_admin"
            user.username = "管理员"
            user.is_admin = True
            user.is_authenticated = True
            user.source = "session"
        else:
            # 3. 匿名用户
            user.source = "anonymous"
    
    # 缓存到请求上下文
    g._user_context = user
    return user


def require_auth(f):
    """装饰器：要求认证"""
    from functools import wraps
    from flask import jsonify
    
    @wraps(f)
    def wrapper(*args, **kwargs):
        user = get_current_user()
        if not user.is_authenticated:
            return jsonify({
                "success": False,
                "message": "请先登录",
                "needAuth": True,
            }), 401
        return f(*args, **kwargs)
    return wrapper


def require_admin(f):
    """装饰器：要求管理员权限"""
    from functools import wraps
    from flask import jsonify
    
    @wraps(f)
    def wrapper(*args, **kwargs):
        user = get_current_user()
        if not user.is_authenticated:
            return jsonify({
                "success": False,
                "message": "请先登录",
                "needAuth": True,
            }), 401
        if not user.is_admin:
            return jsonify({
                "success": False,
                "message": "权限不足",
            }), 403
        return f(*args, **kwargs)
    return wrapper
