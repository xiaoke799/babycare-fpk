#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xiaoke799
"""
Flask 扩展初始化。
集中管理所有第三方扩展的创建和初始化，避免循环导入。
"""

import logging
from flask import Flask

logger = logging.getLogger(__name__)


# ==================== 扩展实例（延迟初始化） ====================

# 此处预留扩展实例，后续按需添加
# 示例:
# flask_bcrypt = Bcrypt()
# flask_limiter = Limiter()
# flask_cors = CORS()


def init_extensions(app: Flask) -> None:
    """
    初始化所有 Flask 扩展。
    
    Args:
        app: Flask 应用实例
        
    Example:
        app = Flask(__name__)
        init_extensions(app)
    """
    # 后续扩展在此初始化
    # 示例:
    # flask_bcrypt.init_app(app)
    # flask_limiter.init_app(app)
    # flask_cors.init_app(app)
    logger.debug("Extensions initialized")
