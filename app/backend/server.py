#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xiaoke799
"""
育儿宝 (BabyCare) - 飞牛 fnOS 原生育儿软件后端
提供宝宝成长记录、照护追踪、育儿知识等功能
"""

import os
import sys

# 将 vendor 内嵌依赖目录加入搜索路径（离线 NAS 环境无法 pip install）
_vendor_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor")
if os.path.isdir(_vendor_dir) and _vendor_dir not in sys.path:
    sys.path.insert(0, _vendor_dir)

import json
import time
import signal
import sqlite3
import datetime
from datetime import timedelta
import secrets
import urllib.parse
from functools import wraps
from flask import Flask, request, session, jsonify, send_from_directory, g

import growth_utils

# 基础设施模块
from constants import (
    DB_PATH,
    WHO_DB_PATH,
    DATA_DIR,
    CONFIG_DIR,
    DB_BUSY_TIMEOUT_MS,
    HTTP_OK,
    HTTP_BAD_REQUEST,
    HTTP_UNAUTHORIZED,
    HTTP_FORBIDDEN,
    HTTP_NOT_FOUND,
    HTTP_TOO_MANY_REQUESTS,
    HTTP_SERVER_ERROR,
    HTTP_SERVICE_UNAVAILABLE,
)
from database import db_manager, db_retry, with_db_connection
from errors import (
    AppError,
    BadRequestError,
    UnauthorizedError,
    ForbiddenError,
    NotFoundError,
    RateLimitError,
    ServiceUnavailableError,
    register_error_handlers,
)
# 说明：config.py 提供了 Development/Testing/Production 三套配置类，但这里**刻意不调用**
# configure_app()。原因是它的 SECRET_KEY 走 _resolve_secret_key()，在未设环境变量时
# 每次进程启动随机生成 —— 而本项目是 gunicorn 多 worker，随机密钥会让各 worker 的
# 会话互不认账（登录反复失效）。密钥改由下面的 _load_or_create_secret_key() 持久化到
# 文件，多 worker 共享。路径等配置也在此显式设置，不依赖配置类。
from logger import setup_logging, get_logger, log_request, log_exception

# PIL 可选导入（用于图片缩略图处理，系统缺少时跳过）
try:
    import PIL
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

app = Flask(__name__, static_folder=None)

# ==================== 网关前缀中间件 ====================
# fnOS 网关转发请求时带有 /app/babycare-fpk 前缀
# Flask 路由定义不带前缀，需要中间件剥离前缀使路由正确匹配
# 同时设置 SCRIPT_NAME 让 url_for() 生成带前缀的 URL
GATEWAY_PREFIX = os.environ.get('GATEWAY_PREFIX', '/app/babycare-fpk')

class GatewayPrefixMiddleware:
    """WSGI 中间件：剥离网关前缀，使 Flask 路由正确匹配"""
    def __init__(self, app, prefix=''):
        self.app = app
        self.prefix = prefix.rstrip('/')

    def __call__(self, environ, start_response):
        path_info = environ.get('PATH_INFO', '')
        script_name = environ.get('SCRIPT_NAME', '')

        # 如果请求路径以前缀开头，剥离前缀
        if self.prefix and path_info.startswith(self.prefix):
            # 更新 PATH_INFO（去掉前缀后的路径）
            environ['PATH_INFO'] = path_info[len(self.prefix):]
            if not environ['PATH_INFO']:
                environ['PATH_INFO'] = '/'
            # 设置 SCRIPT_NAME 为前缀（url_for 会用它生成完整 URL）
            environ['SCRIPT_NAME'] = self.prefix
            # 标记：本请求确实带了网关前缀，说明来自 fnOS 统一网关转发，
            # 只有此时 X-Trim-* 身份头才可信（防止直连 socket 伪造头部提权）。
            environ['BABYCARE_GATEWAY_TRUSTED'] = '1'

        return self.app(environ, start_response)

# 应用中间件（仅在配置了前缀时）
if GATEWAY_PREFIX and GATEWAY_PREFIX != '/':
    app.wsgi_app = GatewayPrefixMiddleware(app.wsgi_app, prefix=GATEWAY_PREFIX)

# 初始化日志系统
setup_logging(
    level=os.environ.get("BABYCARE_LOG_LEVEL", "INFO"),
    enable_console=os.environ.get("BABYCARE_LOG_CONSOLE", "0") == "1",
    enable_file=True,
)
logger = get_logger("server")

# 注册 Blueprint 模块化路由（延迟导入，避免循环依赖）
from blueprints import register_blueprints
register_blueprints(app)

# ==================== 推送通知模块 ====================
_notifier = None
_scheduler = None

def _init_notifier():
    """初始化推送通知模块（幂等；由 _do_init() 在数据库就绪后调用）"""
    global _notifier, _scheduler
    if _notifier is not None:
        return  # 幂等：重复调用会多起一个提醒调度线程
    try:
        from notifier import UnifiedNotifier
        from notifier.reminder_scheduler import ReminderScheduler
        from notifier.multi_platform_notifier import MultiPlatformNotifier

        # 从数据库加载配置。
        # 清单直接引用 notifications.CONFIG_KEYS —— 早前这里另抄了一份 keys 列表，
        # 结果比写接口少一项（notify_timeout），于是「设置页能改、重启就丢」。
        # 注意：不能用 utils._get_app_setting —— 它走 Flask 的 g.db，
        # 而本函数在请求上下文之外执行（首次请求的初始化钩子里），拿不到连接，
        # 会被静默吞掉变成「配置全部走默认值」。
        def load_notify_config():
            from blueprints.notifications import CONFIG_KEYS
            cfg = {}
            try:
                conn = sqlite3.connect(DB_PATH)
                stored = dict(conn.execute(
                    "SELECT key, value FROM app_settings WHERE key LIKE 'notify%'"
                ).fetchall())
                conn.close()
            except Exception as e:
                logger.warning("读取推送配置失败: %s", e)
                stored = {}
            for key in CONFIG_KEYS:
                val = stored.get(f"notify_{key}")
                if val is None:
                    continue
                # 类型还原：布尔与数值项从库里读回来都是字符串
                if key.endswith("_enabled"):
                    cfg[key] = val.lower() in ("1", "true", "yes") if isinstance(val, str) else bool(val)
                elif key.endswith(("_interval", "_days", "_timeout")):
                    try:
                        cfg[key] = float(val) if "." in str(val) else int(val)
                    except (ValueError, TypeError):
                        cfg[key] = val
                else:
                    cfg[key] = val
            return cfg

        config = load_notify_config()

        # 推送历史库放在应用数据目录（与业务数据同一处）。
        # 早期回退分支是「代码目录/../data」—— 升级即丢，且安装目录通常不可写。
        import os
        from constants import DATA_DIR as _DATA_DIR
        data_dir = os.environ.get("TRIM_PKGVAR", "").strip() or _DATA_DIR
        os.makedirs(data_dir, exist_ok=True)
        history_db = os.path.join(data_dir, "push_history.db")

        _notifier = UnifiedNotifier(config, history_db)

        # 初始化调度器
        def get_db():
            from database import get_db as _get_db
            return _get_db()

        _scheduler = ReminderScheduler(_notifier, get_db, config)
        _scheduler.start()

        # 注入到 notifications blueprint
        from blueprints.notifications import init_notifier as _init_bp_notifier
        _init_bp_notifier(_notifier, _scheduler)

        logger.info("[Notifier] 推送通知模块已初始化")
    except Exception as e:
        logger.error("[Notifier] 初始化失败: %s", e)

# 注意：_init_notifier() **不能在这里调用**。
# 它依赖下面从 utils 导入的 _get_app_setting（导入语句在更后面），写在这行会让
# 每次启动都抛 NameError 并被 except 静默吞掉 —— 结果是推送模块从未初始化成功
# （_notifier = None、提醒调度器也没启动），设置页永远显示「推送模块未初始化」。
# 现在统一由 _do_init() 调用：那时数据库已建好、配置也读得到。

# 请求日志钩子：记录每个 API 请求的耗时和状态
@app.before_request
def _log_request_start():
    """记录请求开始时间"""
    from flask import request
    request._start_time = time.time()
    
    # 网关用户身份：从 X-Trim-* 头部获取（仅网关模式可信）
    # 注意：不信任客户端伪造的头部，只在网关模式下读取。
    # 只有 GatewayPrefixMiddleware 确认请求带网关前缀（BABYCARE_GATEWAY_TRUSTED）
    # 时才采信 X-Trim-* 头，防止直连 socket 伪造头部冒充管理员。
    request.gateway_trusted = request.environ.get('BABYCARE_GATEWAY_TRUSTED') == '1'
    if request.gateway_trusted:
        request.trim_user_id = request.headers.get("X-Trim-Userid", "")
        request.trim_username = request.headers.get("X-Trim-Username", "")
        request.trim_is_admin = request.headers.get("X-Trim-Isadmin", "false").lower() == "true"
    else:
        request.trim_user_id = ""
        request.trim_username = ""
        request.trim_is_admin = False

@app.after_request
def _log_request_end(response):
    """记录请求结束信息"""
    from flask import request
    if hasattr(request, '_start_time'):
        duration_ms = (time.time() - request._start_time) * 1000
        request._duration_ms = f"{duration_ms:.1f}"
    log_request(response)
    # 添加安全响应头
    from security import add_security_headers
    return add_security_headers(response)

# 注册全局错误处理器（使用 errors.py 统一错误处理）
register_error_handlers(app)

# 配置
DATA_DIR = os.environ.get('BABYCARE_DATA_DIR', '/var/apps/babycare-fpk/var')
CONFIG_DIR = os.environ.get('BABYCARE_CONFIG_DIR', '/var/apps/babycare-fpk/etc')
DB_PATH = os.environ.get('BABYCARE_DB_PATH', os.path.join(DATA_DIR, 'babycare.db'))
PORT = int(os.environ.get('BABYCARE_PORT', 8090))
# GATEWAY_PREFIX 已在第 76 行定义，此处不再重复

# 共享工具函数（从 utils 模块导入，保持命名空间兼容）
# 注意：不在本模块重新定义这些函数，直接使用 utils 版本
from utils import get_db, row_to_dict, rows_to_list, get_baby_age, get_baby_age_months, _get_int_arg
from utils import _get_app_setting, _set_app_setting  # noqa: F401 赋给模块级名称
# close_db 必须一并导入：下面的 _close_db_teardown 会用到它。
# 漏导会让每个请求在 teardown 阶段抛 NameError，响应被替换成 500。
from utils import close_db  # noqa: F401
SOCKET_PATH = os.environ.get('SOCKET_PATH', '')

# 统一网关前缀配置
# 当通过网关访问时，Flask 需要知道 APPLICATION_ROOT 来正确处理路由
if GATEWAY_PREFIX and GATEWAY_PREFIX != '/':
    app.config['APPLICATION_ROOT'] = GATEWAY_PREFIX
FRONTEND_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'frontend')

# ==================== 认证配置 ====================
SECRET_KEY_FILE = os.path.join(DATA_DIR, 'secret_key')


def _load_or_create_secret_key():
    """加载（或首次生成）应用密钥 —— 多 worker 安全（文件锁防竞态）"""
    os.makedirs(DATA_DIR, exist_ok=True)

    # 尝试使用 fcntl 文件锁（Unix/Linux）
    try:
        import fcntl
        lock_path = os.path.join(DATA_DIR, '.secret_key.lock')
        with open(lock_path, 'w') as lock_fd:
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX)
            try:
                if not os.path.exists(SECRET_KEY_FILE):
                    with open(SECRET_KEY_FILE, 'w', encoding='utf-8') as f:
                        f.write(secrets.token_hex(32))
                with open(SECRET_KEY_FILE, 'r', encoding='utf-8') as f:
                    return f.read().strip()
            except Exception:
                return secrets.token_hex(32)
    except ImportError:
        # Windows 或没有 fcntl 的平台：直接读写（单 worker 场景安全）
        if not os.path.exists(SECRET_KEY_FILE):
            with open(SECRET_KEY_FILE, 'w', encoding='utf-8') as f:
                f.write(secrets.token_hex(32))
        with open(SECRET_KEY_FILE, 'r', encoding='utf-8') as f:
            return f.read().strip()


app.secret_key = _load_or_create_secret_key()
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=False,  # 允许 HTTP 下使用 cookie（fnOS 网关可能走 HTTP）
    MAX_CONTENT_LENGTH=20 * 1024 * 1024,
    # permanent session 有效期（"保持登录状态"功能）
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
    # 路径必须显式写进 config：backup.py 的 _get_backup_dir/_get_db_path 读的是
    # current_app.config['DATA_DIR']，取不到就会退回 /tmp/babycare。生产脚本虽 export 了
    # 同名环境变量把坑盖住了，但只要换一种启动方式（直接跑 gunicorn / 测试脚本），
    # 备份就会静默写到 /tmp —— 重启即丢。这里补上，让主路径就是对的。
    DATA_DIR=DATA_DIR,
    CONFIG_DIR=CONFIG_DIR,
)

# ==================== 健康检查 ====================

@app.route('/api/health')
def health_check():
    """健康检查端点 — fnOS 网关用它验证服务是否存活"""
    try:
        db = get_db()
        db.execute('SELECT 1')
        db_ok = True
    except Exception:
        db_ok = False
    return jsonify({
        'status': 'ok' if db_ok else 'degraded',
        'database': 'connected' if db_ok else 'error',
        'pil_available': HAS_PIL,
        'gateway_prefix': GATEWAY_PREFIX
    })

# ==================== 信号处理 ====================

def _graceful_shutdown(signum, frame):
    """优雅关闭：收到 SIGTERM/SIGINT 时退出"""
    app.logger.info(f'Received signal {signum}, shutting down gracefully...')
    sys.exit(0)

signal.signal(signal.SIGTERM, _graceful_shutdown)
signal.signal(signal.SIGINT, _graceful_shutdown)

# ==================== 数据库操作 ====================
# 注意：get_db() 和 close_db() 使用 utils 模块的版本
# 本模块不重新定义这些函数，直接使用 from utils import get_db

@app.teardown_appcontext
def _close_db_teardown(exception):
    """请求结束时自动关闭数据库连接"""
    close_db()


def init_db():
    """初始化数据库表结构"""
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(CONFIG_DIR, exist_ok=True)

    db = sqlite3.connect(DB_PATH)
    db.executescript('''
        -- 宝宝信息表
        CREATE TABLE IF NOT EXISTS babies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            birthday TEXT NOT NULL,
            gender TEXT DEFAULT 'other',
            due_date TEXT DEFAULT NULL,
            avatar TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            updated_at TEXT DEFAULT (datetime('now', 'localtime'))
        );

        -- 成长记录表（身高、体重、头围、BMI）
        CREATE TABLE IF NOT EXISTS growth_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            baby_id INTEGER NOT NULL,
            record_date TEXT NOT NULL,
            height REAL DEFAULT NULL,
            weight REAL DEFAULT NULL,
            head_circumference REAL DEFAULT NULL,
            bmi REAL DEFAULT NULL,
            note TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (baby_id) REFERENCES babies(id) ON DELETE CASCADE
        );

        -- 喂奶记录表
        CREATE TABLE IF NOT EXISTS feeding_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            baby_id INTEGER NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT DEFAULT NULL,
            feeding_type TEXT NOT NULL,  -- breast, bottle, solid
            amount REAL DEFAULT NULL,     -- 奶量(ml)或固体食物量(g)
            side TEXT DEFAULT NULL,       -- 哺乳侧：left, right, both
            note TEXT DEFAULT '',
            left_duration INTEGER DEFAULT NULL,   -- 左侧哺乳时长(秒)
            right_duration INTEGER DEFAULT NULL,  -- 右侧哺乳时长(秒)
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (baby_id) REFERENCES babies(id) ON DELETE CASCADE
        );

        -- 睡眠记录表
        CREATE TABLE IF NOT EXISTS sleep_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            baby_id INTEGER NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT DEFAULT NULL,
            duration_minutes INTEGER DEFAULT NULL,
            sleep_quality TEXT DEFAULT NULL,  -- good, normal, poor
            is_nap INTEGER DEFAULT NULL,      -- 0=夜间睡眠, 1=白天小憩, NULL=未判定
            note TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (baby_id) REFERENCES babies(id) ON DELETE CASCADE
        );

        -- 换尿布记录表
        CREATE TABLE IF NOT EXISTS diaper_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            baby_id INTEGER NOT NULL,
            change_time TEXT NOT NULL,
            diaper_type TEXT NOT NULL,  -- wet, dirty, both, dry
            color TEXT DEFAULT NULL,    -- black, brown, green, yellow, other
            skin_condition TEXT DEFAULT NULL,  -- normal, slight_red, rash, severe_rash
            brand TEXT DEFAULT NULL,    -- 尿布品牌
            note TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (baby_id) REFERENCES babies(id) ON DELETE CASCADE
        );

        -- 吸奶记录表
        CREATE TABLE IF NOT EXISTS pumping_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            baby_id INTEGER NOT NULL,
            pump_time TEXT NOT NULL,
            duration_minutes INTEGER DEFAULT NULL,  -- 吸奶时长(分钟)
            left_amount REAL DEFAULT NULL,           -- 左侧奶量(ml)
            right_amount REAL DEFAULT NULL,          -- 右侧奶量(ml)
            total_amount REAL DEFAULT NULL,          -- 总奶量(ml)
            pump_type TEXT DEFAULT NULL,             -- manual, electric, double
            left_duration INTEGER DEFAULT NULL,      -- 左侧吸奶时长(秒)，由计时器记录
            right_duration INTEGER DEFAULT NULL,     -- 右侧吸奶时长(秒)，由计时器记录
            note TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (baby_id) REFERENCES babies(id) ON DELETE CASCADE
        );

        -- WHO 生长百分位参考数据表（体重）
        CREATE TABLE IF NOT EXISTS who_weight_percentiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            age_in_days INTEGER NOT NULL,
            sex TEXT NOT NULL,  -- boy, girl
            p3 REAL NOT NULL,
            p15 REAL NOT NULL,
            p50 REAL NOT NULL,
            p85 REAL NOT NULL,
            p97 REAL NOT NULL
        );

        -- WHO 生长百分位参考数据表（身高）
        CREATE TABLE IF NOT EXISTS who_height_percentiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            age_in_days INTEGER NOT NULL,
            sex TEXT NOT NULL,  -- boy, girl
            p3 REAL NOT NULL,
            p15 REAL NOT NULL,
            p50 REAL NOT NULL,
            p85 REAL NOT NULL,
            p97 REAL NOT NULL
        );

        -- WHO 生长百分位参考数据表（BMI）
        CREATE TABLE IF NOT EXISTS who_bmi_percentiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            age_in_days INTEGER NOT NULL,
            sex TEXT NOT NULL,  -- boy, girl
            p3 REAL NOT NULL,
            p15 REAL NOT NULL,
            p50 REAL NOT NULL,
            p85 REAL NOT NULL,
            p97 REAL NOT NULL
        );

        -- WHO头围百分位（0-24个月）
        CREATE TABLE IF NOT EXISTS who_head_percentiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            age_in_days INTEGER NOT NULL,
            sex TEXT NOT NULL,
            p3 REAL NOT NULL,
            p15 REAL NOT NULL,
            p50 REAL NOT NULL,
            p85 REAL NOT NULL,
            p97 REAL NOT NULL
        );

        -- 计时器表
        CREATE TABLE IF NOT EXISTS timers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            baby_id INTEGER NOT NULL,
            timer_type TEXT NOT NULL DEFAULT 'feeding',  -- feeding, sleep, tummy
            start_time TEXT NOT NULL,
            end_time TEXT DEFAULT NULL,
            is_active INTEGER DEFAULT 1,
            name TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (baby_id) REFERENCES babies(id) ON DELETE CASCADE
        );

        -- 照片记录表
        CREATE TABLE IF NOT EXISTS photos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            baby_id INTEGER NOT NULL,
            photo_date TEXT NOT NULL,
            description TEXT DEFAULT '',
            file_path TEXT NOT NULL,
            thumbnail_path TEXT DEFAULT '',
            is_cover INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (baby_id) REFERENCES babies(id) ON DELETE CASCADE
        );

        -- 错误日志表（用于聚合统计和排查）
        CREATE TABLE IF NOT EXISTS error_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT DEFAULT (datetime('now', 'localtime')),
            level TEXT NOT NULL DEFAULT 'ERROR',
            module TEXT DEFAULT '',
            path TEXT DEFAULT '',
            method TEXT DEFAULT '',
            message TEXT NOT NULL,
            traceback TEXT DEFAULT '',
            request_id TEXT DEFAULT '',
            client_ip TEXT DEFAULT '',
            user_agent TEXT DEFAULT '',
            extra_data TEXT DEFAULT ''
        );

        -- 疫苗记录表
        CREATE TABLE IF NOT EXISTS vaccines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            baby_id INTEGER NOT NULL,
            vaccine_name TEXT NOT NULL,
            vaccine_type TEXT DEFAULT 'free',  -- free=一类自费, paid=自费
            dose_number INTEGER DEFAULT 1,
            scheduled_date TEXT DEFAULT NULL,
            actual_date TEXT DEFAULT NULL,
            status TEXT DEFAULT 'pending',  -- pending, completed, skipped
            note TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (baby_id) REFERENCES babies(id) ON DELETE CASCADE
        );

        -- 体温记录表
        CREATE TABLE IF NOT EXISTS temperature_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            baby_id INTEGER NOT NULL,
            temperature REAL NOT NULL,
            measure_time TEXT DEFAULT (datetime('now', 'localtime')),
            measure_method TEXT DEFAULT 'ear',  -- ear, armpit, oral, rectal
            is_fever INTEGER DEFAULT 0,
            note TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (baby_id) REFERENCES babies(id) ON DELETE CASCADE
        );

        -- 用药记录表
        CREATE TABLE IF NOT EXISTS medication_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            baby_id INTEGER NOT NULL,
            medication_name TEXT NOT NULL,
            dosage TEXT DEFAULT '',
            dosage_unit TEXT DEFAULT 'mg',
            measure_time TEXT DEFAULT (datetime('now', 'localtime')),
            next_dose_interval INTEGER DEFAULT 0,  -- 下次用药间隔（小时）
            next_dose_time TEXT DEFAULT '',
            note TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (baby_id) REFERENCES babies(id) ON DELETE CASCADE
        );

        -- 辅食过敏测试记录表
        CREATE TABLE IF NOT EXISTS allergy_tests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            baby_id INTEGER NOT NULL,
            food_name TEXT NOT NULL,
            test_date TEXT NOT NULL,
            day_number INTEGER DEFAULT 1,  -- 第几天（1-3天连续测试）
            has_reaction INTEGER DEFAULT 0,
            reaction_detail TEXT DEFAULT '',
            status TEXT DEFAULT 'testing',  -- testing, passed, failed
            note TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (baby_id) REFERENCES babies(id) ON DELETE CASCADE
        );

        -- 里程碑记录表
        CREATE TABLE IF NOT EXISTS milestones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            baby_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            description TEXT DEFAULT '',
            achieved_date TEXT NOT NULL,
            category TEXT DEFAULT 'other',  -- motor, language, social, cognitive, other
            is_first INTEGER NOT NULL DEFAULT 0,  -- 1 = 属于「第一次」成就
            photo_path TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (baby_id) REFERENCES babies(id) ON DELETE CASCADE
        );

        -- ASQ发育筛查问卷表
        CREATE TABLE IF NOT EXISTS asq_screenings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            baby_id INTEGER NOT NULL,
            screening_date TEXT NOT NULL,
            age_months INTEGER NOT NULL,
            communication_score INTEGER DEFAULT 0,
            gross_motor_score INTEGER DEFAULT 0,
            fine_motor_score INTEGER DEFAULT 0,
            problem_solving_score INTEGER DEFAULT 0,
            personal_social_score INTEGER DEFAULT 0,
            total_score INTEGER DEFAULT 0,
            result TEXT DEFAULT 'normal',  -- normal, monitor, refer
            note TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (baby_id) REFERENCES babies(id) ON DELETE CASCADE
        );

        -- 体检记录表
        CREATE TABLE IF NOT EXISTS checkup_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            baby_id INTEGER NOT NULL,
            checkup_date TEXT NOT NULL,
            age_months INTEGER DEFAULT NULL,
            height REAL DEFAULT NULL,
            weight REAL DEFAULT NULL,
            head_circumference REAL DEFAULT NULL,
            doctor TEXT DEFAULT '',
            hospital TEXT DEFAULT '',
            result TEXT DEFAULT 'normal',
            advice TEXT DEFAULT '',
            next_checkup_date TEXT DEFAULT '',
            note TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (baby_id) REFERENCES babies(id) ON DELETE CASCADE
        );

        -- 辅食添加记录表
        CREATE TABLE IF NOT EXISTS solid_food_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            baby_id INTEGER NOT NULL,
            food_name TEXT NOT NULL,
            food_category TEXT DEFAULT 'vegetable',  -- vegetable, fruit, meat, grain, dairy, other
            first_try_date TEXT NOT NULL,
            amount TEXT DEFAULT '',
            reaction TEXT DEFAULT 'none',  -- none, mild, severe
            reaction_detail TEXT DEFAULT '',
            is_favorite INTEGER DEFAULT 0,
            note TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (baby_id) REFERENCES babies(id) ON DELETE CASCADE
        );

        -- 发育飞跃期记录表
        CREATE TABLE IF NOT EXISTS leap_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            baby_id INTEGER NOT NULL,
            leap_number INTEGER NOT NULL,  -- 第几次飞跃（1-10）
            start_date TEXT NOT NULL,
            end_date TEXT DEFAULT NULL,
            is_completed INTEGER DEFAULT 0,
            note TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (baby_id) REFERENCES babies(id) ON DELETE CASCADE
        );

        -- 囟门检查记录表
        CREATE TABLE IF NOT EXISTS fontanelle_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            baby_id INTEGER NOT NULL,
            check_date TEXT NOT NULL,
            anterior_size REAL DEFAULT NULL,  -- 前囟大小（cm）
            anterior_status TEXT DEFAULT 'open',  -- open, closing, closed
            posterior_status TEXT DEFAULT 'open',  -- open, closing, closed
            note TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (baby_id) REFERENCES babies(id) ON DELETE CASCADE
        );

        -- 成长对比照片表
        CREATE TABLE IF NOT EXISTS growth_photos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            baby_id INTEGER NOT NULL,
            photo_date TEXT NOT NULL,
            photo_path TEXT NOT NULL,
            age_months INTEGER DEFAULT NULL,
            caption TEXT DEFAULT '',
            category TEXT DEFAULT 'monthly',  -- monthly, milestone, comparison
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (baby_id) REFERENCES babies(id) ON DELETE CASCADE
        );

        -- 日记记录表
        CREATE TABLE IF NOT EXISTS diary_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            baby_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            content TEXT DEFAULT '',
            entry_date TEXT NOT NULL,
            mood TEXT DEFAULT NULL,
            photos TEXT DEFAULT '[]',
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (baby_id) REFERENCES babies(id) ON DELETE CASCADE
        );

        -- 应用设置表
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT DEFAULT (datetime('now', 'localtime'))
        );

        -- 应用认证设置表（管理密码哈希、LLM 配置等）
        CREATE TABLE IF NOT EXISTS app_settings(
            key TEXT PRIMARY KEY,
            value TEXT
        );

        -- ==================== 育儿健康档案 ====================

        -- 健康档案记录表（体检记录、健康检查）
        CREATE TABLE IF NOT EXISTS health_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            baby_id INTEGER NOT NULL,
            record_date TEXT NOT NULL,
            record_type TEXT NOT NULL DEFAULT 'routine',  -- routine, vaccine, dental, eye, blood, other
            title TEXT NOT NULL,
            hospital TEXT DEFAULT '',
            doctor TEXT DEFAULT '',
            temperature REAL DEFAULT NULL,  -- 体温（健康记录·发烧）
            symptom TEXT DEFAULT '',        -- 症状（健康记录）
            medication TEXT DEFAULT '',     -- 用药（健康记录）
            age_months INTEGER DEFAULT NULL,  -- 记录时月龄（自动计算）
            height REAL DEFAULT NULL,
            weight REAL DEFAULT NULL,
            head_circumference REAL DEFAULT NULL,
            bmi REAL DEFAULT NULL,
            heart_result TEXT DEFAULT '',  -- 心脏检查结果
            lung_result TEXT DEFAULT '',   -- 肺部检查结果
            abdomen_result TEXT DEFAULT '',  -- 腹部检查结果
            skin_result TEXT DEFAULT '',   -- 皮肤检查结果
            bone_result TEXT DEFAULT '',   -- 骨骼检查结果
            hearing_result TEXT DEFAULT '',  -- 听力检查结果
            vision_result TEXT DEFAULT '',  -- 视力检查结果
            blood_result TEXT DEFAULT '',  -- 血液检查结果
            urine_result TEXT DEFAULT '',  -- 尿液检查结果
            other_exam TEXT DEFAULT '',    -- 其他检查
            diagnosis TEXT DEFAULT '',     -- 诊断结果
            advice TEXT DEFAULT '',        -- 医生建议
            next_visit_date TEXT DEFAULT '',  -- 下次就诊日期
            attachments TEXT DEFAULT '[]',  -- 附件列表（JSON）
            note TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            updated_at TEXT DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (baby_id) REFERENCES babies(id) ON DELETE CASCADE
        );

        -- 健康指标追踪表（具体的检验指标）
        CREATE TABLE IF NOT EXISTS health_indicators (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            record_id INTEGER DEFAULT NULL,  -- 关联 health_records
            baby_id INTEGER NOT NULL,
            indicator_name TEXT NOT NULL,    -- 指标名称
            indicator_code TEXT DEFAULT '',  -- 指标编码
            value TEXT NOT NULL,             -- 指标值
            unit TEXT DEFAULT '',            -- 单位
            reference_low REAL DEFAULT NULL,  -- 参考值下限
            reference_high REAL DEFAULT NULL,  -- 参考值上限
            reference_text TEXT DEFAULT '',   -- 参考值文本
            result_status TEXT DEFAULT 'normal',  -- normal, high, low, abnormal
            record_date TEXT NOT NULL,
            note TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (baby_id) REFERENCES babies(id) ON DELETE CASCADE,
            FOREIGN KEY (record_id) REFERENCES health_records(id) ON DELETE CASCADE
        );

        -- 健康提醒表
        CREATE TABLE IF NOT EXISTS health_reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            baby_id INTEGER NOT NULL,
            reminder_type TEXT NOT NULL,  -- checkup, vaccine, medication, custom
            title TEXT NOT NULL,
            description TEXT DEFAULT '',
            due_date TEXT NOT NULL,
            is_completed INTEGER DEFAULT 0,
            completed_date TEXT DEFAULT NULL,
            note TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (baby_id) REFERENCES babies(id) ON DELETE CASCADE
        );

        -- AI 聊天记录表（如果还没有）
        CREATE TABLE IF NOT EXISTS chat_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            baby_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (baby_id) REFERENCES babies(id) ON DELETE CASCADE
        );

        -- 疫苗详细记录表（含接种反应追踪）
        CREATE TABLE IF NOT EXISTS vaccine_details (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            baby_id INTEGER NOT NULL,
            vaccine_name TEXT NOT NULL,          -- 疫苗名称（如：乙肝疫苗、百白破）
            vaccine_type TEXT DEFAULT 'free',     -- free=一类, paid=自费
            dose_number INTEGER DEFAULT 1,        -- 第几针
            scheduled_date TEXT DEFAULT NULL,     -- 计划接种日期
            actual_date TEXT DEFAULT NULL,        -- 实际接种日期
            status TEXT DEFAULT 'pending',        -- pending, completed, skipped, delayed
            manufacturer TEXT DEFAULT '',         -- 生产厂家
            batch_number TEXT DEFAULT '',         -- 批号
            hospital TEXT DEFAULT '',             -- 接种单位
            doctor TEXT DEFAULT '',               -- 接种医生
            injection_site TEXT DEFAULT '',       -- 接种部位（左上臂、右大腿等）
            has_reaction INTEGER DEFAULT 0,       -- 是否有不良反应
            reaction_detail TEXT DEFAULT '',      -- 不良反应详情
            reaction_severity TEXT DEFAULT 'none', -- none, mild, moderate, severe
            next_dose_date TEXT DEFAULT NULL,     -- 下次接种日期
            antibody_test_date TEXT DEFAULT NULL, -- 抗体检测日期
            antibody_result TEXT DEFAULT '',      -- 抗体检测结果
            note TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            updated_at TEXT DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (baby_id) REFERENCES babies(id) ON DELETE CASCADE
        );

        -- 视力听力筛查表
        CREATE TABLE IF NOT EXISTS screenings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            baby_id INTEGER NOT NULL,
            screening_type TEXT NOT NULL,         -- vision_筛查, hearing_筛查, vision_诊断, hearing_诊断
            screening_date TEXT NOT NULL,
            age_months INTEGER DEFAULT NULL,      -- 筛查时月龄
            screening_method TEXT DEFAULT '',     -- 筛查方法（如：OAE, ABR, 视力表）
            hospital TEXT DEFAULT '',
            doctor TEXT DEFAULT '',
            result_summary TEXT DEFAULT '',  -- 结果概述
            result_status TEXT DEFAULT 'normal',  -- normal, abnormal, borderline, pending
            detail_left TEXT DEFAULT '',          -- 左侧结果详情
            detail_right TEXT DEFAULT '',         -- 右侧结果详情
            referral_needed INTEGER DEFAULT 0,    -- 是否需要转诊
            referral_done INTEGER DEFAULT 0,      -- 是否已转诊
            next_screening_date TEXT DEFAULT NULL,-- 下次筛查日期
            attachments TEXT DEFAULT '[]',        -- 检查报告附件
            note TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (baby_id) REFERENCES babies(id) ON DELETE CASCADE
        );

        -- 过敏史档案表
        CREATE TABLE IF NOT EXISTS allergy_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            baby_id INTEGER NOT NULL,
            allergen_type TEXT NOT NULL,          -- food, drug, environmental, other
            allergen_name TEXT NOT NULL,          -- 过敏原名称
            reaction_detail TEXT DEFAULT '',      -- 过敏反应详情
            severity_level TEXT DEFAULT 'mild',   -- mild, moderate, severe, anaphylaxis
            first_occurrence_date TEXT DEFAULT NULL, -- 首次发生日期
            last_occurrence_date TEXT DEFAULT NULL,  -- 最近发生日期
            diagnosis_method TEXT DEFAULT '',     -- 诊断方法（如：皮试、血清IgE、食物激发）
            diagnosed_by TEXT DEFAULT '',         -- 诊断医生
            management TEXT DEFAULT '',           -- 处理措施
            status TEXT DEFAULT 'active',         -- active, resolved, monitoring
            note TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (baby_id) REFERENCES babies(id) ON DELETE CASCADE
        );

        -- 发育里程碑详细记录表
        CREATE TABLE IF NOT EXISTS milestone_details (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            baby_id INTEGER NOT NULL,
            milestone_code TEXT NOT NULL,        -- 里程碑编码（如：motor_roll_over）
            milestone_name TEXT NOT NULL,       -- 里程碑名称
            category TEXT NOT NULL,             -- motor大运动, fine_motor精细运动, language语言, cognitive认知, social社交, self_care自理
            expected_age_months REAL DEFAULT NULL, -- 预期达成月龄
            achieved_date TEXT DEFAULT NULL,    -- 实际达成日期
            actual_age_months REAL DEFAULT NULL, -- 达成时月龄
            status TEXT DEFAULT 'pending',      -- pending, achieved, delayed, concerned
            assessment_method TEXT DEFAULT '',  -- 评估方式
            note TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (baby_id) REFERENCES babies(id) ON DELETE CASCADE
        );

        -- 喂养记录汇总表
        CREATE TABLE IF NOT EXISTS feeding_summary (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            baby_id INTEGER NOT NULL,
            record_month TEXT NOT NULL,          -- 月份（YYYY-MM）
            feeding_type TEXT DEFAULT '',        -- 喂养方式（母乳/混合/配方奶/辅食）
            avg_daily_ml REAL DEFAULT NULL,      -- 日均奶量（ml）
            feeding_frequency INTEGER DEFAULT NULL, -- 日均喂养次数
            solid_food_count INTEGER DEFAULT NULL, -- 辅食种类数
            weight_gain_month REAL DEFAULT NULL, -- 当月增重（g）
            height_gain_month REAL DEFAULT NULL, -- 当月增高（cm）
            concerns TEXT DEFAULT '',              -- 喂养问题/注意事项
            note TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (baby_id) REFERENCES babies(id) ON DELETE CASCADE,
            UNIQUE(baby_id, record_month)
        );

        -- 育儿知识文章表
        CREATE TABLE IF NOT EXISTS knowledge_articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT DEFAULT 'general',
            title TEXT NOT NULL,
            age_range TEXT DEFAULT '',
            content TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        );

        -- 宝宝健康百科表
        CREATE TABLE IF NOT EXISTS baby_wiki (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT DEFAULT 'general',
            title TEXT NOT NULL,
            content TEXT DEFAULT '',
            symptoms TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        );

        -- 辅食食谱表
        CREATE TABLE IF NOT EXISTS baby_recipes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            icon TEXT DEFAULT '',
            age_group TEXT DEFAULT '6月+',
            category TEXT DEFAULT '谷物',
            ingredients TEXT DEFAULT '',
            instructions TEXT DEFAULT '',
            allergens TEXT DEFAULT '',
            prep_time TEXT DEFAULT '',
            cook_time TEXT DEFAULT '',
            servings TEXT DEFAULT '',
            nutrition TEXT DEFAULT '',
            tips TEXT DEFAULT '',
            difficulty TEXT DEFAULT '简单',
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        );

        -- 活动游戏推荐表
        CREATE TABLE IF NOT EXISTS baby_activities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            icon TEXT DEFAULT '',
            category TEXT DEFAULT '认知',
            min_age_months INTEGER DEFAULT 0,
            max_age_months INTEGER DEFAULT 36,
            summary TEXT DEFAULT '',
            duration TEXT DEFAULT '',
            materials TEXT DEFAULT '',
            steps TEXT DEFAULT '',
            benefits TEXT DEFAULT '',
            difficulty TEXT DEFAULT '简单',
            tips TEXT DEFAULT '',
            tags TEXT DEFAULT '',
            seasonal TEXT DEFAULT '',
            indoor INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        );

        -- 活动收藏表
        CREATE TABLE IF NOT EXISTS activity_favorites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            baby_id INTEGER NOT NULL,
            activity_id INTEGER NOT NULL,
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (baby_id) REFERENCES babies(id) ON DELETE CASCADE,
            FOREIGN KEY (activity_id) REFERENCES baby_activities(id) ON DELETE CASCADE,
            UNIQUE(baby_id, activity_id)
        );

        -- 活动完成记录表
        CREATE TABLE IF NOT EXISTS activity_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            baby_id INTEGER NOT NULL,
            activity_id INTEGER NOT NULL,
            completed_at TEXT NOT NULL,
            duration_minutes INTEGER DEFAULT 0,
            mood TEXT DEFAULT 'happy',
            note TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (baby_id) REFERENCES babies(id) ON DELETE CASCADE,
            FOREIGN KEY (activity_id) REFERENCES baby_activities(id) ON DELETE CASCADE
        );

        -- 出牙记录表
        CREATE TABLE IF NOT EXISTS baby_teeth (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            baby_id INTEGER NOT NULL,
            tooth_code TEXT NOT NULL,
            tooth_name TEXT DEFAULT '',
            erupt_date TEXT NOT NULL,
            note TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (baby_id) REFERENCES babies(id) ON DELETE CASCADE
        );

        -- 出牙记录表（出牙记录页，含位置/左右侧）
        CREATE TABLE IF NOT EXISTS teething_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            baby_id INTEGER NOT NULL,
            tooth_name TEXT NOT NULL,
            erupt_date TEXT NOT NULL,
            position TEXT DEFAULT '',
            side TEXT DEFAULT '',
            note TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (baby_id) REFERENCES babies(id) ON DELETE CASCADE
        );

        -- 趴睡训练表（旧版，保持兼容）
        CREATE TABLE IF NOT EXISTS baby_tummytime (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            baby_id INTEGER NOT NULL,
            start_time TEXT NOT NULL,
            duration INTEGER DEFAULT 0,
            note TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (baby_id) REFERENCES babies(id) ON DELETE CASCADE
        );

        -- 趴睡训练表（新版，统一使用）
        CREATE TABLE IF NOT EXISTS tummy_time_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            baby_id INTEGER NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT DEFAULT '',
            duration_minutes INTEGER DEFAULT 0,
            milestone TEXT DEFAULT '',
            note TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (baby_id) REFERENCES babies(id) ON DELETE CASCADE
        );

        -- 纸尿裤价格记录表（旧版，v4 迁移已迁移至 product_prices）
        CREATE TABLE IF NOT EXISTS diaper_prices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            brand TEXT NOT NULL,
            series TEXT DEFAULT '',
            spec TEXT DEFAULT '',
            price REAL DEFAULT 0,
            count_per_pack INTEGER DEFAULT 0,
            unit_price REAL DEFAULT 0,
            source TEXT DEFAULT '',
            updated_at TEXT DEFAULT (datetime('now', 'localtime'))
        );

        -- 育儿用品比价表（多品类：纸尿裤/奶粉/湿巾/辅食等）
        CREATE TABLE IF NOT EXISTS product_prices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL DEFAULT 'diaper',
            brand TEXT NOT NULL,
            series TEXT DEFAULT '',
            spec TEXT DEFAULT '',
            package_size REAL DEFAULT 0,
            unit TEXT DEFAULT '片',
            price REAL DEFAULT 0,
            purchase_channel TEXT DEFAULT '',
            purchase_date TEXT DEFAULT '',
            note TEXT DEFAULT '',
            updated_at TEXT DEFAULT (datetime('now', 'localtime'))
        );

        -- 支出记账表
        CREATE TABLE IF NOT EXISTS expense_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL DEFAULT 'other',
            amount REAL NOT NULL DEFAULT 0,
            item_name TEXT DEFAULT '',
            purchase_channel TEXT DEFAULT '',
            expense_date TEXT NOT NULL,
            note TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        );

        -- 用药提醒表
        CREATE TABLE IF NOT EXISTS medication_reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            baby_id INTEGER NOT NULL,
            medication_name TEXT NOT NULL,
            dosage TEXT DEFAULT '',
            reminder_time TEXT NOT NULL,
            repeat_interval INTEGER DEFAULT 0,
            is_active INTEGER DEFAULT 1,
            note TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (baby_id) REFERENCES babies(id) ON DELETE CASCADE
        );
    ''')
    db.commit()

    # 为已有数据库添加新字段（忽略已存在的错误）
    try:
        db.execute('ALTER TABLE feeding_records ADD COLUMN left_duration INTEGER DEFAULT NULL')
    except Exception:
        pass
    try:
        db.execute('ALTER TABLE feeding_records ADD COLUMN right_duration INTEGER DEFAULT NULL')
    except Exception:
        pass

    db.close()


# ==================== 轻量 schema 迁移 ====================
# 项目暂无版本化迁移框架；新增列统一通过此入口，保证老库升级不缺列。

def _ensure_column(table: str, column_ddl: str):
    """幂等加列：列已存在则跳过。column_ddl 形如 'due_date TEXT'"""
    conn = sqlite3.connect(DB_PATH)
    try:
        cols = [r[1] for r in conn.execute(f'PRAGMA table_info({table})').fetchall()]
        col_name = column_ddl.split()[0]
        if col_name not in cols:
            conn.execute(f'ALTER TABLE {table} ADD COLUMN {column_ddl}')
            conn.commit()
    finally:
        conn.close()


def _ensure_table(table: str, create_sql: str):
    """幂等兜底建表：历史库缺失该表时补建，避免相关接口 500（no such table）"""
    conn = sqlite3.connect(DB_PATH)
    try:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        if not exists:
            conn.execute(create_sql)
            conn.commit()
            app.logger.warning("兜底建表: %s（历史库缺失，已补建）", table)
    finally:
        conn.close()


# 喂奶/吸奶历史数据清洗语句（幂等，可反复执行）。
# 抽成常量是为了让离线自检能直接读到同一份 SQL，不出现「测试里的语句和线上不一致」。
FEEDING_CLEANUP_SQL = (
    "UPDATE feeding_records SET start_time = replace(start_time, 'T', ' ') WHERE start_time LIKE '%T%'",
    "UPDATE feeding_records SET end_time = replace(end_time, 'T', ' ') WHERE end_time LIKE '%T%'",
    "UPDATE pumping_records SET pump_time = replace(pump_time, 'T', ' ') WHERE pump_time LIKE '%T%'",
    "UPDATE feeding_records SET amount = NULL WHERE amount = 0",
    "UPDATE feeding_records SET side = NULL WHERE side = ''",
    "UPDATE pumping_records SET side = NULL WHERE side = ''",
)

# 睡眠历史数据清洗语句（幂等，可反复执行）。
SLEEP_CLEANUP_SQL = (
    "UPDATE sleep_records SET start_time = replace(start_time, 'T', ' ') WHERE start_time LIKE '%T%'",
    "UPDATE sleep_records SET end_time = replace(end_time, 'T', ' ') WHERE end_time LIKE '%T%'",
    "UPDATE sleep_records SET sleep_quality = NULL WHERE sleep_quality = ''",
    # is_nap 没判定的，按入睡钟点补：6:00–18:00 算白天小憩
    "UPDATE sleep_records SET is_nap = "
    "  CASE WHEN CAST(substr(start_time, 12, 2) AS INTEGER) >= 6"
    "       AND CAST(substr(start_time, 12, 2) AS INTEGER) < 18 THEN 1 ELSE 0 END "
    "  WHERE is_nap IS NULL",
)


def _normalize_record_timestamps():
    """幂等数据清洗：历史记录里带 'T' 的时间统一成空格分隔，顺带清掉脏值。

    前端 datetime-local 提交的是 'YYYY-MM-DDTHH:MM'，快捷按钮写入的是
    'YYYY-MM-DD HH:MM:SS'。SQLite 里这是字符串，'T'(0x54) > ' '(0x20)，
    按 `start_time <= '当天 23:59:59'` 筛选时，带 T 的下午/晚上记录会被漏掉。
    另外快捷按钮会给母乳写 amount=0、给瓶喂写 side=''，这里一并清成 NULL。
    """
    conn = sqlite3.connect(DB_PATH)
    try:
        changed = 0
        for sql in FEEDING_CLEANUP_SQL + SLEEP_CLEANUP_SQL:
            try:
                changed += conn.execute(sql).rowcount or 0
            except sqlite3.Error:
                # 表/列不存在（历史库版本差异）就跳过，不能阻断启动
                continue
        conn.commit()
        if changed:
            app.logger.warning("清洗历史喂奶/吸奶/睡眠记录：%d 行", changed)
    finally:
        conn.close()


def _repair_sleep_durations():
    """修复历史睡眠记录的负数 / 缺失时长。

    老版本直接 end-start 相减，夜里 22:00 睡到次日 06:00 会算出 -960 分钟，
    把「今日总睡眠」拉成负数。这里对 duration_minutes < 0 的记录按跨天重算；
    起止时间齐全但时长为 NULL 的也一并补上。幂等，可反复执行。
    """
    import datetime as _dt

    def _minutes(start, end):
        try:
            s = _dt.datetime.strptime(start[:19], "%Y-%m-%d %H:%M:%S")
            e = _dt.datetime.strptime(end[:19], "%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            return None
        if e <= s:
            e += _dt.timedelta(days=1)
        return int((e - s).total_seconds() // 60)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """SELECT id, start_time, end_time, duration_minutes FROM sleep_records
               WHERE end_time IS NOT NULL AND end_time != ''
                 AND (duration_minutes IS NULL OR duration_minutes < 0)"""
        ).fetchall()
        fixed = 0
        for r in rows:
            minutes = _minutes(r["start_time"], r["end_time"])
            if minutes is None or minutes < 0:
                continue
            conn.execute("UPDATE sleep_records SET duration_minutes = ? WHERE id = ?", (minutes, r["id"]))
            fixed += 1
        conn.commit()
        if fixed:
            app.logger.warning("修复历史睡眠时长：%d 条", fixed)
    except sqlite3.Error:
        pass
    finally:
        conn.close()


def _run_migrations():
    """集中登记所有增量迁移（新装库由 CREATE TABLE 覆盖，此处兜底历史库）"""
    _ensure_column('babies', 'due_date TEXT')
    # 相册分类：此前 photos 表没有分类列，前端「按类型筛选」只能靠描述里的关键词瞎猜
    # （描述里得正好出现「里程碑」才筛得出来），等于没有分类。这里补上真正的分类列。
    _ensure_column('photos', "category TEXT DEFAULT ''")
    # milestones.is_first 每次启动都兜底补列，不依赖版本化迁移。
    # 原因：gunicorn 开了 2 个 worker，两个进程会各自跑 migrations.run_migrations()，
    # 并发时其中一个可能因锁冲突失败。列若没建出来，INSERT 会报
    # "no such column: is_first"，新增里程碑直接不可用。
    # 数据回填（v2）仍由版本化迁移负责，失败时下次启动会重试。
    _ensure_column('milestones', 'is_first INTEGER NOT NULL DEFAULT 0')
    _ensure_column('medication_reminders', 'frequency TEXT DEFAULT \'\'')
    _ensure_column('medication_reminders', 'next_dose_time TEXT DEFAULT \'\'')
    # 知识库、食谱、活动页面补全所需字段
    _ensure_column('knowledge_articles', 'age_range TEXT DEFAULT \'\'')
    _ensure_column('baby_recipes', 'icon TEXT DEFAULT \'\'')
    _ensure_column('baby_recipes', 'cook_time TEXT DEFAULT \'\'')
    _ensure_column('baby_recipes', 'servings TEXT DEFAULT \'\'')
    _ensure_column('baby_recipes', 'nutrition TEXT DEFAULT \'\'')
    _ensure_column('baby_recipes', 'tips TEXT DEFAULT \'\'')
    _ensure_column('baby_recipes', 'difficulty TEXT DEFAULT \'简单\'')
    _ensure_column('baby_activities', 'icon TEXT DEFAULT \'\'')
    _ensure_column('baby_activities', 'summary TEXT DEFAULT \'\'')
    _ensure_column('baby_activities', 'duration TEXT DEFAULT \'\'')
    _ensure_column('baby_activities', 'tips TEXT DEFAULT \'\'')
    _ensure_column('baby_activities', 'tags TEXT DEFAULT \'\'')
    _ensure_column('baby_activities', 'seasonal TEXT DEFAULT \'\'')
    _ensure_column('baby_activities', 'indoor INTEGER DEFAULT 1')
    _ensure_column('diaper_records', 'skin_condition TEXT DEFAULT NULL')
    _ensure_column('diaper_records', 'brand TEXT DEFAULT NULL')
    # 吸奶记录补左右侧计时列（由计时器按秒记录，支持双侧同时吸奶）
    _ensure_column('pumping_records', 'left_duration INTEGER DEFAULT NULL')
    _ensure_column('pumping_records', 'right_duration INTEGER DEFAULT NULL')
    _ensure_column('pumping_records', 'side TEXT DEFAULT NULL')
    # 历史数据清洗（时间格式 / 0ml / 空哺乳侧 / 睡眠时长），幂等，可反复执行
    _normalize_record_timestamps()
    _repair_sleep_durations()
    # 健康记录（发烧/就医/用药）页需要体温、症状、用药三列。
    # 该表是「体检记录」和「健康记录」两个页面共用的，新装库由 CREATE TABLE 带上，
    # 历史库靠这里每次启动兜底补列——缺列会让新增健康记录的 INSERT 直接 500。
    _ensure_column('health_records', 'temperature REAL DEFAULT NULL')
    _ensure_column('health_records', "symptom TEXT DEFAULT ''")
    _ensure_column('health_records', "medication TEXT DEFAULT ''")
    # 活动相关表兜底（历史库缺失时补建）
    _ensure_table('activity_favorites', '''CREATE TABLE IF NOT EXISTS activity_favorites (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        baby_id INTEGER NOT NULL,
        activity_id INTEGER NOT NULL,
        created_at TEXT DEFAULT (datetime('now', 'localtime')),
        FOREIGN KEY (baby_id) REFERENCES babies(id) ON DELETE CASCADE,
        FOREIGN KEY (activity_id) REFERENCES baby_activities(id) ON DELETE CASCADE,
        UNIQUE(baby_id, activity_id)
    )''')
    _ensure_table('activity_logs', '''CREATE TABLE IF NOT EXISTS activity_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        baby_id INTEGER NOT NULL,
        activity_id INTEGER NOT NULL,
        completed_at TEXT NOT NULL,
        duration_minutes INTEGER DEFAULT 0,
        mood TEXT DEFAULT 'happy',
        note TEXT DEFAULT '',
        created_at TEXT DEFAULT (datetime('now', 'localtime')),
        FOREIGN KEY (baby_id) REFERENCES babies(id) ON DELETE CASCADE,
        FOREIGN KEY (activity_id) REFERENCES baby_activities(id) ON DELETE CASCADE
    )''')
    # 照片相关表兜底（历史库缺失时补建，避免照片接口 no such table 500）
    _ensure_table('photos', '''CREATE TABLE IF NOT EXISTS photos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        baby_id INTEGER NOT NULL,
        photo_date TEXT NOT NULL,
        description TEXT DEFAULT '',
        file_path TEXT NOT NULL,
        thumbnail_path TEXT DEFAULT '',
        is_cover INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now', 'localtime')),
        FOREIGN KEY (baby_id) REFERENCES babies(id) ON DELETE CASCADE
    )''')
    _ensure_table('growth_photos', '''CREATE TABLE IF NOT EXISTS growth_photos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        baby_id INTEGER NOT NULL,
        photo_date TEXT NOT NULL,
        photo_path TEXT NOT NULL,
        age_months INTEGER DEFAULT NULL,
        caption TEXT DEFAULT '',
        category TEXT DEFAULT 'monthly',
        created_at TEXT DEFAULT (datetime('now', 'localtime')),
        FOREIGN KEY (baby_id) REFERENCES babies(id) ON DELETE CASCADE
    )''')
    _ensure_table('vaccine_details', '''CREATE TABLE IF NOT EXISTS vaccine_details (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        baby_id INTEGER NOT NULL,
        vaccine_name TEXT NOT NULL,
        vaccine_type TEXT DEFAULT 'free',
        dose_number INTEGER DEFAULT 1,
        scheduled_date TEXT DEFAULT NULL,
        actual_date TEXT DEFAULT NULL,
        status TEXT DEFAULT 'pending',
        manufacturer TEXT DEFAULT '',
        batch_number TEXT DEFAULT '',
        reaction TEXT DEFAULT '',
        note TEXT DEFAULT '',
        created_at TEXT DEFAULT (datetime('now', 'localtime')),
        FOREIGN KEY (baby_id) REFERENCES babies(id) ON DELETE CASCADE
    )''')


def init_indexes():
    """创建性能索引（幂等，CREATE IF NOT EXISTS）"""
    conn = sqlite3.connect(DB_PATH)
    try:
        # WHO 百分位表：按 (sex, age_in_days) 复合索引加速查询
        for tbl in ('who_weight_percentiles', 'who_height_percentiles',
                    'who_bmi_percentiles', 'who_head_percentiles'):
            conn.execute(
                f'CREATE INDEX IF NOT EXISTS idx_{tbl}_sex_age '
                f'ON {tbl} (sex, age_in_days)'
            )
        # 常用业务表索引
        conn.execute('CREATE INDEX IF NOT EXISTS idx_growth_baby_date ON growth_records (baby_id, record_date)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_feeding_baby_time ON feeding_records (baby_id, start_time)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_sleep_baby_time ON sleep_records (baby_id, start_time)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_diaper_baby_time ON diaper_records (baby_id, change_time)')
        # 扩展业务表索引
        conn.execute('CREATE INDEX IF NOT EXISTS idx_vaccines_baby ON vaccines (baby_id, scheduled_date)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_milestones_baby ON milestones (baby_id, achieved_date DESC)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_diary_baby ON diary_entries (baby_id, entry_date DESC)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_temperature_baby ON temperature_records (baby_id, measure_time)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_teeth_baby ON baby_teeth (baby_id, erupt_date)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_tummytime_baby ON baby_tummytime (baby_id, start_time)')
        conn.commit()
    finally:
        conn.close()


# ==================== 模块级初始化（懒加载模式） ====================
# gunicorn import 时不执行种子数据插入，首次请求时触发初始化
# 初始化期间 API 请求返回 503 + Retry-After: 5

import threading

_init_done = False
_init_lock = threading.Lock()


def _load_who_percentile_data():
    """将 who_data.py 中的 WHO 生长百分位数据装载到数据库（幂等：仅空表时插入）"""
    db = get_db()
    # 检查是否已装载（以 who_weight_percentiles 为准）
    count = db.execute("SELECT COUNT(*) FROM who_weight_percentiles").fetchone()[0]
    if count > 0:
        return  # 已装载，跳过

    from who_data import WEIGHT_DATA, HEIGHT_DATA, BMI_DATA, HEAD_DATA
    tables = [
        ("who_weight_percentiles", WEIGHT_DATA),
        ("who_height_percentiles", HEIGHT_DATA),
        ("who_bmi_percentiles", BMI_DATA),
        ("who_head_percentiles", HEAD_DATA),
    ]
    for table_name, data in tables:
        for sex, records in data.items():
            for record in records:
                age_in_days = record[0]
                p3, p15, p50, p85, p97 = record[1], record[2], record[3], record[4], record[5]
                db.execute(
                    f"INSERT INTO {table_name} (age_in_days, sex, p3, p15, p50, p85, p97) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (age_in_days, sex, p3, p15, p50, p85, p97),
                )
    db.commit()
    app.logger.info("WHO 生长百分位数据装载完成")


def _do_init() -> None:
    """执行实际的初始化（阻塞，可能耗时）。调用者必须已持有 _init_lock."""
    global _init_done
    init_db()
    try:
        _run_migrations()
    except Exception:
        pass
    try:
        init_indexes()
    except Exception as e:
        app.logger.warning("索引创建失败: %s", e)

    # 加载种子数据（育儿知识、百科、食谱、活动等）
    try:
        from seed_data import load_all_seeds
        load_all_seeds()
    except Exception as e:
        app.logger.warning("种子数据加载失败: %s", e)

    # 装载 WHO 生长百分位参考数据（who_data.py → who_* 表）
    try:
        _load_who_percentile_data()
    except Exception as e:
        app.logger.warning("WHO 数据装载失败: %s", e)

    # 执行数据库迁移（版本化 schema 升级）
    try:
        from migrations import run_migrations
        migration_result = run_migrations()
        if migration_result["applied_count"] > 0:
            app.logger.info(
                "数据库迁移完成: v%d → v%d, 应用 %d 个迁移",
                migration_result["current_version"],
                migration_result["target_version"],
                migration_result["applied_count"],
            )
    except Exception as e:
        app.logger.warning("数据库迁移失败: %s", e)

    # 启动自动备份后台线程（守护线程，按配置周期创建 JSON 备份）
    try:
        from blueprints.storage import _start_auto_backup_thread
        _start_auto_backup_thread()
        app.logger.info("自动备份调度器已启动（默认关闭，需在设置页启用）")
    except Exception as e:
        app.logger.warning("自动备份调度器启动失败: %s", e)

    # 初始化推送通知模块（含提醒调度器）。放在这里而不是模块导入时：
    # 此刻数据库已建表、推送配置也读得到；导入时调用会因名称未定义而静默失败。
    try:
        _init_notifier()
        if _notifier is None:
            app.logger.warning("推送通知模块未初始化（详见上方日志）")
        else:
            app.logger.info("推送通知模块已初始化")
    except Exception as e:
        app.logger.warning("推送通知模块初始化异常: %s", e)

    _init_done = True


@app.before_request
def _lazy_init_hook():
    """首次请求时触发初始化，初始化期间返回 503."""
    global _init_done
    if _init_done:
        return None
    # 尝试非阻塞获取锁：成功说明无其他线程在初始化，我们来做
    if _init_lock.acquire(blocking=False):
        try:
            if _init_done:  # double-check
                return None
            try:
                _do_init()
            except Exception:
                # 初始化失败：记录完整堆栈并返回 503（而不是把异常抛成 500），
                # 且保持 _init_done=False，下一个请求会重新尝试初始化
                app.logger.exception("服务初始化失败（path=%s）", request.path)
                if request.path.startswith("/api/"):
                    resp = jsonify({
                        "status": "error",
                        "message": "服务初始化失败，请查看应用日志；稍后将自动重试",
                    })
                    resp.status_code = 503
                    resp.headers["Retry-After"] = "5"
                    return resp
                return None
        finally:
            _init_lock.release()
        return None
    # 锁被持有 → 其他线程正在初始化，返回 503
    if request.path.startswith("/api/"):
        resp = jsonify({"status": "error", "message": "服务初始化中，请稍后重试"})
        resp.status_code = 503
        resp.headers["Retry-After"] = "5"
        return resp
    return None







def _json_int(data, key, default=None, minimum=None, maximum=None):
    """从请求 JSON 字典解析整数字段。返回 (value, None)；非法时返回 (None, 错误响应)"""
    raw = data.get(key) if isinstance(data, dict) else None
    if raw is None or raw == '':
        return default, None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None, (jsonify({'success': False, 'message': f'字段 {key} 必须为整数'}), 400)
    if minimum is not None and value < minimum:
        return None, (jsonify({'success': False, 'message': f'字段 {key} 不能小于 {minimum}'}), 400)
    if maximum is not None and value > maximum:
        return None, (jsonify({'success': False, 'message': f'字段 {key} 不能大于 {maximum}'}), 400)
    return value, None


# 静态文件服务已迁移到 blueprints/static.py

# 认证路由已迁移到 blueprints/auth.py（含登录失败计数清理）

# 全局登录校验白名单（其余路由均需登录）
_AUTH_WHITELIST_EXACT = {'/', '/index.html'}
_AUTH_WHITELIST_PREFIXES = ('/css/', '/js/', '/images/')
_AUTH_WHITELIST_API = {'/api/auth/status', '/api/auth/login', '/api/auth/logout', '/api/health'}


@app.before_request
def require_login():
    """
    全局登录校验：
    - 网关模式：X-Trim-Userid 头部存在即视为已认证（fnOS 网关已验证）
    - 开发模式：回退到 session-based 认证
    """
    path = request.path
    if (path in _AUTH_WHITELIST_EXACT
            or path.startswith(_AUTH_WHITELIST_PREFIXES)
            or path in _AUTH_WHITELIST_API):
        return None

    # 网关模式：只有请求确实带网关前缀（BABYCARE_GATEWAY_TRUSTED）时，
    # X-Trim-Userid 才可信。直连 socket 的请求即使伪造该头也不会被采信，
    # 会落入下面的 session 校验，未登录则 401。
    trim_user = getattr(request, 'trim_user_id', '') or ''
    if trim_user:
        return None

    # 开发模式：session-based 认证
    if not session.get('authenticated'):
        return jsonify({'success': False, 'needAuth': True}), 401
    return None


# 宝宝管理路由已迁移到 blueprints/babies.py

# 成长记录/喂奶/睡眠/尿布路由已迁移到 blueprints/growth.py, feeding.py, sleep.py, diaper.py

# 里程碑/ASQ/出牙/体检/辅食/飞跃期/囟门/成长照片/日记路由已迁移到 blueprints/development.py 和 blueprints/photos.py

# 育儿知识/食谱/活动路由已迁移到 blueprints/knowledge.py


# 数据统计/喂奶间隔/时间线/WHO百分位/睡眠分析/24小时节律/睡眠预测路由已迁移到 blueprints/analytics.py