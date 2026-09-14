#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xiaoke799
"""
Blueprint 模块化管理。
提供统一的注册函数，简化 server.py 中的导入和注册流程。
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from flask import Flask

# 导入所有 Blueprint 模块
# 注意：导入顺序影响 URL 前缀匹配，低优先级放前面
from blueprints.static import bp as static_bp
from blueprints.auth import bp as auth_bp
from blueprints.babies import bp as babies_bp
from blueprints.growth import bp as growth_bp
from blueprints.feeding import bp as feeding_bp
from blueprints.sleep import bp as sleep_bp
from blueprints.diaper import bp as diaper_bp
from blueprints.shopping import bp as shopping_bp
from blueprints.development import bp as development_bp
from blueprints.vaccines import bp as vaccines_bp
from blueprints.photos import bp as photos_bp
from blueprints.health import bp as health_bp
from blueprints.storage import bp as storage_bp
from blueprints.ai import bp as ai_bp
from blueprints.knowledge import bp as knowledge_bp
from blueprints.settings import bp as settings_bp
from blueprints.analytics import bp as analytics_bp
from blueprints.health_records import bp as health_records_bp
from blueprints.notifications import bp as notifications_bp
from blueprints.admin_logs import bp as admin_logs_bp
from blueprints.backup import bp as backup_bp

# 导出所有 Blueprint 列表（供 register_blueprints 使用）
ALL_BLUEPRINTS = [
    static_bp,
    auth_bp,
    babies_bp,
    growth_bp,
    feeding_bp,
    sleep_bp,
    diaper_bp,
    shopping_bp,
    development_bp,
    vaccines_bp,
    photos_bp,
    health_bp,
    health_records_bp,
    storage_bp,
    ai_bp,
    knowledge_bp,
    settings_bp,
    analytics_bp,
    notifications_bp,
    admin_logs_bp,
    backup_bp,
]


def register_blueprints(app: "Flask") -> None:
    """
    将所有 Blueprint 注册到 Flask 应用。
    
    Args:
        app: Flask 应用实例
        
    Example:
        from flask import Flask
        from blueprints import register_blueprints
        
        app = Flask(__name__)
        register_blueprints(app)
    """
    for bp in ALL_BLUEPRINTS:
        app.register_blueprint(bp)
