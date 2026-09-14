#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xiaoke799
"""
认证路由 Blueprint。

fnOS 网关认证模式：
- 信任网关传入的 X-Trim-Userid / X-Trim-Isadmin 头部
- 网关已验证用户身份，应用无需独立登录
- X-Trim-Isadmin == "true" 的用户即为应用管理员

本地开发模式（无网关头）：
- 回退到 session-based 认证（兼容开发调试）
"""

import os
import time
from flask import Blueprint, request, session, jsonify

bp = Blueprint("auth", __name__, url_prefix="/api/auth")

# 开发模式免密登录开关：默认关闭，需显式设置 BABYCARE_DEV_AUTH=1 才启用。
# 安全背景：/api/auth/login 无需任何口令即可拿到 admin 会话，若不设防，
# 任何能触达本服务的客户端（包括通过网关的普通用户）都可自我提权。
# 本地调试时由 start_server.py 自动设置该环境变量，无需手动配置。
def _dev_auth_enabled():
    return os.environ.get("BABYCARE_DEV_AUTH", "") == "1"

# 登录失败计数（用于防爆破）— 使用网关用户 ID 而非 IP
_login_fail_counts = {}
_MAX_LOGIN_FAIL_ENTRIES = 10000  # 最大条目数，防止内存无限增长


def _get_client_identifier():
    """
    获取客户端标识符（用于限流）。
    优先使用网关传入的 X-Trim-Userid，回退到 X-Forwarded-For，最后回退到 remote_addr。
    """
    # fnOS 网关传入的用户 ID（最可靠）
    trim_user = request.headers.get("X-Trim-Userid", "").strip()
    if trim_user:
        return f"user:{trim_user}"

    # 反向代理转发的真实 IP
    forwarded = request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
    if forwarded:
        return f"ip:{forwarded}"

    # 最后回退（Unix Socket 场景可能为空）
    remote = request.remote_addr or "unknown"
    return f"ip:{remote}"


def get_current_user():
    """
    获取当前用户信息。
    优先从网关头部获取，回退到 session（开发模式）。

    安全说明：X-Trim-* 头只有在该请求确实带网关前缀（由
    GatewayPrefixMiddleware 标记 request.gateway_trusted）时才可信，
    否则视为客户端伪造、忽略之，防止直连 socket 提权。
    """
    # 生产模式：fnOS 网关传入的头部（仅网关可信时采信）
    gateway_trusted = bool(getattr(request, 'gateway_trusted', False))
    trim_user = request.headers.get("X-Trim-Userid", "").strip()
    if gateway_trusted and trim_user:
        return {
            "id": trim_user,
            "username": request.headers.get("X-Trim-Username", trim_user).strip(),
            "is_admin": request.headers.get("X-Trim-Isadmin", "").lower() == "true",
            "source": "gateway",
        }

    # 开发模式：session-based
    if session.get("authenticated"):
        return {
            "id": "dev",
            "username": "开发者",
            "is_admin": True,
            "source": "session",
        }

    return None


def is_authenticated():
    """检查当前请求是否已认证"""
    return get_current_user() is not None


def is_admin():
    """检查当前用户是否为管理员"""
    user = get_current_user()
    return user is not None and user.get("is_admin", False)


@bp.route("/status", methods=["GET"])
def auth_status():
    """获取认证状态

    免密模式：本应用不设独立密码，网关模式信任 X-Trim-* 头部，开发模式由
    /login 直接放行。因此这里固定返回 configured=True、passwordless=True——
    configured 若为假，前端会落到早已移除的「创建管理密码」流程并调一个
    不存在的 /api/auth/setup，导致本地部署卡死在登录页。
    """
    user = get_current_user()
    return jsonify({
        "success": True,
        "authenticated": user is not None,
        "user": user,
        "gateway_mode": bool(getattr(request, 'gateway_trusted', False)) and request.headers.get("X-Trim-Userid", "") != "",
        "configured": True,
        "passwordless": True,
    })


@bp.route("/login", methods=["POST"])
def auth_login():
    """
    登录（仅开发模式使用）。
    生产环境下 fnOS 网关已认证，此端点返回提示。
    """
    # 如果已经在网关模式下认证，直接返回成功
    user = get_current_user()
    if user and user.get("source") == "gateway":
        return jsonify({"success": True, "message": "已通过网关认证", "user": user})

    # 开发模式：session-based 登录（需显式开启 BABYCARE_DEV_AUTH=1）
    # 注意：此模式仅供本地开发调试；生产环境由 fnOS 网关认证。
    # 若未开启开关，拒绝登录，防止无口令提权。
    if not _dev_auth_enabled():
        return jsonify({
            "success": False,
            "message": "生产环境请通过 fnOS 网关登录；本地调试请设置环境变量 BABYCARE_DEV_AUTH=1",
        }), 403

    session["authenticated"] = True
    return jsonify({
        "success": True,
        "message": "开发模式登录成功",
        "user": get_current_user(),
    })


@bp.route("/logout", methods=["POST"])
def auth_logout():
    """退出登录（仅开发模式有效，网关模式无需登出）"""
    session.clear()
    return jsonify({
        "success": True,
        "message": "已退出登录",
        "gateway_mode": bool(getattr(request, 'gateway_trusted', False)) and request.headers.get("X-Trim-Userid", "") != "",
    })


@bp.before_request
def _cleanup_login_fail_counts_hook():
    """Blueprint 级别定时清理登录失败计数（每 100 次请求检查一次）"""
    cleanup_login_fail_counts()


def cleanup_login_fail_counts() -> None:
    """定期清理超过 24 小时未活动的登录失败计数，防止内存泄漏"""
    now_ts = time.time()

    # 每 100 次调用检查一次，避免每次请求都遍历字典
    if not hasattr(cleanup_login_fail_counts, "_counter"):
        cleanup_login_fail_counts._counter = 0
    cleanup_login_fail_counts._counter += 1
    if cleanup_login_fail_counts._counter % 100 != 0:
        return

    cutoff = now_ts - 86400  # 24 小时
    expired = [
        key
        for key, info in _login_fail_counts.items()
        if isinstance(info, dict) and info.get("last_attempt", 0) < cutoff
    ]
    for key in expired:
        _login_fail_counts.pop(key, None)
