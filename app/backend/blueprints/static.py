#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xiaoke799
"""
静态文件路由 Blueprint。
提供前端页面和静态资源服务。
"""

import os
from flask import Blueprint, send_from_directory, current_app

bp = Blueprint("static", __name__)

# 前端静态文件目录（blueprints/ 比 server.py 多一层，需三层 dirname 回到 app/）
FRONTEND_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "frontend")


@bp.route("/")
def index():
    """主页"""
    return send_from_directory(FRONTEND_DIR, "index.html")


@bp.route("/static/<path:filename>")
def static_files(filename):
    """静态文件（CSS/JS/图片）"""
    static_dir = os.path.join(FRONTEND_DIR, "static")
    return send_from_directory(static_dir, filename)


@bp.route("/images/<path:filename>")
def images(filename):
    """图片资源"""
    images_dir = os.path.join(FRONTEND_DIR, "images")
    return send_from_directory(images_dir, filename)


@bp.route("/css/<path:filename>")
def css(filename):
    """CSS 样式表"""
    css_dir = os.path.join(FRONTEND_DIR, "css")
    return send_from_directory(css_dir, filename)


@bp.route("/js/<path:filename>")
def js(filename):
    """JavaScript 文件"""
    js_dir = os.path.join(FRONTEND_DIR, "js")
    return send_from_directory(js_dir, filename)


@bp.route("/legal/<path:filename>")
def legal(filename):
    """法律文本页面（隐私政策 / 用户协议）。

    单独一个路由而不是塞进 /static/：legal/ 放的是**要在浏览器里直接打开阅读**
    的完整 HTML 文档，与 css/js 资源不是一类东西。必须是应用自己提供的页面，
    不能让用户跳到外网去看 —— 断网时也得能读到。
    """
    legal_dir = os.path.join(FRONTEND_DIR, "legal")
    return send_from_directory(legal_dir, filename)


# ==================== 网关前缀兼容路由 ====================
# 绝大多数情况下 GatewayPrefixMiddleware 会先剥离前缀，上述路由即可命中。
# 但若运行环境未启用中间件（GATEWAY_PREFIX 为空而网关仍带前缀），
# 请求会直接以 /app/babycare-fpk/... 到达，下面这组带前缀的路由作为双保险，避免 CSS/JS/图片 404。
_GATEWAY_PREFIX = os.environ.get("GATEWAY_PREFIX", "/app/babycare-fpk").rstrip("/")


def _register_prefixed(prefix):
    if not prefix:
        return
    bp.route(prefix + "/")(index)
    bp.route(prefix + "/css/<path:filename>")(css)
    bp.route(prefix + "/js/<path:filename>")(js)
    bp.route(prefix + "/images/<path:filename>")(images)
    bp.route(prefix + "/static/<path:filename>")(static_files)


_register_prefixed(_GATEWAY_PREFIX)
