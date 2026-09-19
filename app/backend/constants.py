#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xiaoke799
"""
集中管理所有魔法数字、配置常量和业务规则常量。
避免在代码中散落硬编码值，便于统一调整和维护。
"""

import os

# ==================== 路径配置 ====================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VENDOR_DIR = os.path.join(BASE_DIR, "vendor")
DATA_DIR = os.environ.get("BABYCARE_DATA_DIR", "/var/apps/babycare-fpk/var")
CONFIG_DIR = os.environ.get("BABYCARE_CONFIG_DIR", "/var/apps/babycare-fpk/etc")
DB_PATH = os.environ.get("BABYCARE_DB_PATH", os.path.join(DATA_DIR, "babycare.db"))
WHO_DB_PATH = os.environ.get("BABYCARE_WHO_DB_PATH", os.path.join(BASE_DIR, "who_data.db"))

# ==================== 应用信息 ====================

# 与包根 manifest 的 version 字段保持一致（设置页「系统诊断」里展示）。
# 发布新版本时两处一起改。
APP_VERSION = os.environ.get("BABYCARE_APP_VERSION", "0.0.1")
APP_NAME = "育儿宝"

# ==================== 数据库配置 ====================

DB_BUSY_TIMEOUT_MS = 5000
DB_MAX_RETRIES = 3
DB_RETRY_DELAY_BASE = 0.1

# ==================== 安全配置 ====================

SECRET_KEY = os.environ.get("BABYCARE_SECRET_KEY", "")
SESSION_LIFETIME_HOURS = 24
MAX_LOGIN_ATTEMPTS = 5
LOGIN_COOLDOWN_SECONDS = 30
BCRYPT_ROUNDS = 12
TOKEN_LENGTH = 32

# ==================== 分页与限制 ====================

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100
MAX_UPLOAD_SIZE_MB = 10
MAX_TEXT_LENGTH = 5000

# ==================== 业务规则 ====================

MAX_BABIES_PER_USER = 10
MAX_PHOTOS_PER_BABY = 500
MAX_MILESTONES_PER_BABY = 200
FEEDING_MAX_DURATION_MIN = 180
SLEEP_MAX_DURATION_HOURS = 20

# ==================== WHO 生长标准 ====================

WHO_PERCENTILE_Z_SCORES = {
    "p3": -1.881,
    "p15": -1.036,
    "p50": 0.0,
    "p85": 1.036,
    "p97": 1.881,
}

WHO_AGE_MIN_DAYS = 0
WHO_AGE_MAX_DAYS = 1856

# ==================== 缓存配置 ====================

CACHE_DEFAULT_TTL = 300
AI_RESULT_CACHE_TTL = 3600

# ==================== AI 引擎配置 ====================

AI_TIMEOUT_SECONDS = 30
AI_MAX_RETRIES = 2
AI_RETRY_DELAY_SECONDS = 1
AI_MAX_TOKENS = 2048
AI_TEMPERATURE = 0.7

# ==================== 日志配置 ====================

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 3

# ==================== HTTP 状态码 ====================

HTTP_OK = 200
HTTP_CREATED = 201
HTTP_BAD_REQUEST = 400
HTTP_UNAUTHORIZED = 401
HTTP_FORBIDDEN = 403
HTTP_NOT_FOUND = 404
HTTP_METHOD_NOT_ALLOWED = 405
HTTP_CONFLICT = 409
HTTP_UNSUPPORTED_MEDIA_TYPE = 415
HTTP_TOO_MANY_REQUESTS = 429
HTTP_SERVER_ERROR = 500
HTTP_SERVICE_UNAVAILABLE = 503

# ==================== 数据导出 / 备份表清单 ====================
# 唯一事实来源（single source of truth）。备份蓝图（/api/backup/*）与
# 导出全部数据（/api/export-all）必须用同一份清单 —— 此前两处各写一份，
# 改了一边忘了另一边，结果「导出全部数据」少导 16 张表，用户换机后
# 记录静默消失。任何新增业务表都只改这里。
#
# 判断标准：**用户会产生/修改的数据**都要在里面。
# 有意排除的只有三类：种子内置内容（seed_data.py 每次启动重灌）、
# WHO 参考数据（随程序分发）、error_logs（运维日志，没必要跨机搬）。

EXPORT_TABLES = [
    # --- 宝宝主体 ---
    'babies',
    # --- 日常记录 ---
    'growth_records', 'feeding_records', 'sleep_records', 'diaper_records',
    'pumping_records', 'tummy_time_records', 'baby_tummytime',
    'temperature_records', 'medication_records', 'solid_food_records',
    'teething_records', 'baby_teeth', 'diary_entries', 'feeding_summary',
    # --- 健康档案 ---
    'health_records', 'health_indicators', 'health_reminders',
    'checkup_records', 'vaccines', 'vaccine_details',
    'allergy_tests', 'allergy_history', 'screenings',
    'asq_screenings', 'leap_records', 'fontanelle_records',
    'milestones', 'milestone_details', 'medication_reminders',
    # --- 活动/知识使用痕迹 ---
    'activity_favorites', 'activity_logs', 'baby_activities',
    'knowledge_articles', 'baby_wiki',
    # --- 媒体 ---
    'photos', 'growth_photos',
    # --- 其他用户数据 ---
    'timers', 'diaper_prices', 'product_prices', 'expense_records',
    'baby_recipes', 'chat_history',
    # --- 知识库（全局参考表，管理员可维护）---
    'knowledge_articles', 'baby_wiki',
    # --- 用户配置（全局，不含 babies）---
    'settings', 'app_settings',
]

# 带 baby_id 外键、需要按宝宝筛选/恢复的表
BABY_SCOPED_TABLES = {
    'growth_records', 'feeding_records', 'sleep_records', 'diaper_records',
    'pumping_records', 'tummy_time_records', 'baby_tummytime',
    'temperature_records', 'medication_records', 'solid_food_records',
    'teething_records', 'baby_teeth', 'diary_entries', 'feeding_summary',
    'health_records', 'health_indicators', 'health_reminders',
    'checkup_records', 'vaccines', 'vaccine_details',
    'allergy_tests', 'allergy_history', 'screenings',
    'asq_screenings', 'leap_records', 'fontanelle_records',
    'milestones', 'milestone_details', 'medication_reminders',
    'activity_favorites', 'activity_logs',
    'photos', 'growth_photos',
    'timers',
    'chat_history',
}

# ==================== 响应消息模板 ====================

MSG_SUCCESS = {"status": "ok"}
MSG_NOT_FOUND = {"status": "error", "message": "资源不存在"}
MSG_UNAUTHORIZED = {"status": "error", "message": "请先登录"}
MSG_FORBIDDEN = {"status": "error", "message": "无权访问"}
MSG_BAD_REQUEST = {"status": "error", "message": "请求参数错误"}
MSG_SERVER_ERROR = {"status": "error", "message": "服务器内部错误"}
MSG_SERVICE_UNAVAILABLE = {"status": "error", "message": "服务暂时不可用，请稍后重试"}
