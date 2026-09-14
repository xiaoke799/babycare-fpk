#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xiaoke799
"""
Flask 应用配置管理。
支持开发、测试、生产三套环境配置。
"""

import os
import secrets
from constants import (
    SECRET_KEY,
    SESSION_LIFETIME_HOURS,
    MAX_UPLOAD_SIZE_MB,
    LOG_FORMAT,
    DATA_DIR,
    CONFIG_DIR,
)


def _resolve_secret_key():
    """解析应用密钥：优先环境变量，否则动态生成。

    不再使用写死在代码里的弱默认值——一旦「无密钥可用」就现场生成随机值
    （仅本次进程有效），避免会话可被伪造。真正的持久化密钥由
    server.py 的 _load_or_create_secret_key() 负责。
    """
    env_key = os.environ.get("BABYCARE_SECRET_KEY", "").strip()
    if env_key:
        return env_key
    return secrets.token_hex(32)


class BaseConfig:
    """基础配置（所有环境共享）"""

    # Flask 核心配置
    SECRET_KEY = _resolve_secret_key()
    JSON_AS_ASCII = False
    JSON_SORT_KEYS = False
    MAX_CONTENT_LENGTH = MAX_UPLOAD_SIZE_MB * 1024 * 1024

    # 会话配置
    PERMANENT_SESSION_LIFETIME = SESSION_LIFETIME_HOURS * 3600
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"

    # 应用元数据
    APP_NAME = "babycare-fpk"
    APP_DISPLAY_NAME = "育儿宝"

    # 路径配置
    DATA_DIR = DATA_DIR
    CONFIG_DIR = CONFIG_DIR

    # 日志配置
    LOG_FORMAT = LOG_FORMAT
    LOG_LEVEL = "INFO"


class DevelopmentConfig(BaseConfig):
    """开发环境配置"""

    DEBUG = True
    TESTING = False
    LOG_LEVEL = "DEBUG"


class TestingConfig(BaseConfig):
    """测试环境配置"""

    DEBUG = False
    TESTING = True
    LOG_LEVEL = "DEBUG"


class ProductionConfig(BaseConfig):
    """生产环境配置"""

    DEBUG = False
    TESTING = False
    LOG_LEVEL = "WARNING"
    SESSION_COOKIE_SECURE = True


# 环境变量到配置类的映射
CONFIG_MAP = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}


def get_config_class(env: str = None) -> type:
    """
    根据环境变量获取配置类。
    默认从 FLASK_ENV 环境变量读取，未设置则使用 production。
    """
    if env is None:
        env = os.environ.get("FLASK_ENV", "production").lower()
    return CONFIG_MAP.get(env, ProductionConfig)


def configure_app(app, config_class: type = None) -> None:
    """
    将配置类应用到 Flask 应用实例。
    
    Args:
        app: Flask 应用实例
        config_class: 配置类，为 None 时自动检测环境
    """
    if config_class is None:
        config_class = get_config_class()
    app.config.from_object(config_class)
