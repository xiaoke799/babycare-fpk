#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xiaoke799
"""
全局错误处理器与自定义异常体系。
提供统一的错误响应格式，避免 HTML 错误页面泄露。
同时将严重错误记录到 error_logs 表供管理员查看。
"""

import traceback
from typing import Tuple, Dict, Any
from flask import Flask, jsonify, request
from werkzeug.exceptions import HTTPException

from constants import (
    HTTP_OK,
    HTTP_BAD_REQUEST,
    HTTP_UNAUTHORIZED,
    HTTP_FORBIDDEN,
    HTTP_NOT_FOUND,
    HTTP_METHOD_NOT_ALLOWED,
    HTTP_UNSUPPORTED_MEDIA_TYPE,
    HTTP_TOO_MANY_REQUESTS,
    HTTP_SERVER_ERROR,
    HTTP_SERVICE_UNAVAILABLE,
)
from logger import get_logger

logger = get_logger("errors")

# 状态码 → 中文说明。前端统一读 success 字段，但 message 要能让人看懂。
_HTTP_MESSAGES = {
    400: "请求参数错误",
    401: "请先登录",
    403: "无权访问",
    404: "请求的资源不存在",
    HTTP_METHOD_NOT_ALLOWED: "请求方法不被支持",
    409: "数据冲突",
    413: "上传内容过大",
    HTTP_UNSUPPORTED_MEDIA_TYPE: "请求格式不受支持（请使用 Content-Type: application/json）",
    422: "请求参数无法处理",
    HTTP_TOO_MANY_REQUESTS: "请求过于频繁，请稍后重试",
    500: "服务器内部错误",
    503: "服务暂时不可用",
}


def _error_body(message: str, **extra) -> Dict[str, Any]:
    """统一的错误响应体。

    同时带 `success: False` 与 `status: "error"`：
    - 蓝图里的错误一律是 {'success': False, ...}，前端也是按 res.success 判断的；
      全局错误处理器此前只返回 status，导致前端读到错误响应时可能把它当成功。
    - 保留 status 是为了不破坏既有调用方。
    """
    body = {"success": False, "status": "error", "message": message}
    body.update(extra)
    return body


# ==================== 自定义异常 ====================

class AppError(Exception):
    """应用基础异常"""

    def __init__(
        self,
        message: str = "服务器内部错误",
        status_code: int = HTTP_SERVER_ERROR,
        payload: Dict[str, Any] = None,
    ):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.payload = payload or {}

    def to_dict(self) -> Dict[str, Any]:
        result = {"status": "error", "message": self.message}
        result.update(self.payload)
        return result


class BadRequestError(AppError):
    """请求参数错误"""

    def __init__(self, message: str = "请求参数错误", payload: Dict[str, Any] = None):
        super().__init__(message, HTTP_BAD_REQUEST, payload)


class UnauthorizedError(AppError):
    """未登录或登录过期"""

    def __init__(self, message: str = "请先登录", payload: Dict[str, Any] = None):
        super().__init__(message, HTTP_UNAUTHORIZED, payload)


class ForbiddenError(AppError):
    """无权访问"""

    def __init__(self, message: str = "无权访问", payload: Dict[str, Any] = None):
        super().__init__(message, HTTP_FORBIDDEN, payload)


class NotFoundError(AppError):
    """资源不存在"""

    def __init__(self, message: str = "资源不存在", payload: Dict[str, Any] = None):
        super().__init__(message, HTTP_NOT_FOUND, payload)


class RateLimitError(AppError):
    """请求过于频繁"""

    def __init__(self, message: str = "请求过于频繁，请稍后重试", payload: Dict[str, Any] = None):
        super().__init__(message, HTTP_TOO_MANY_REQUESTS, payload)


class ServiceUnavailableError(AppError):
    """服务暂时不可用"""

    def __init__(self, message: str = "服务暂时不可用，请稍后重试", payload: Dict[str, Any] = None):
        super().__init__(message, HTTP_SERVICE_UNAVAILABLE, payload)


# ==================== 错误处理器 ====================

def handle_app_error(error: AppError) -> Tuple[Dict[str, Any], int]:
    """处理自定义应用异常"""
    logger.warning("AppError [%d]: %s", error.status_code, error.message)
    body = _error_body(error.message)
    body.update(error.payload)
    return body, error.status_code


def handle_404_error(error) -> Tuple[Dict[str, Any], int]:
    """处理 404 路由不存在"""
    if request.path.startswith("/api/"):
        return _error_body("API 端点不存在"), HTTP_NOT_FOUND
    return _error_body("页面不存在"), HTTP_NOT_FOUND


def handle_405_error(error) -> Tuple[Dict[str, Any], int]:
    """处理 405 方法不允许

    此前这里返回的是 HTTP_NOT_FOUND(404)，把「方法不对」谎报成「没有这个接口」，
    排查时很容易走偏（前端只看到 404 会以为路由没注册）。
    """
    return _error_body(f"该地址不支持 {request.method} 请求"), HTTP_METHOD_NOT_ALLOWED


def handle_413_error(error) -> Tuple[Dict[str, Any], int]:
    """处理 413 请求体过大"""
    return _error_body("上传文件过大"), HTTP_BAD_REQUEST


def handle_429_error(error) -> Tuple[Dict[str, Any], int]:
    """处理 429 请求过于频繁"""
    return _error_body("请求过于频繁，请稍后重试"), HTTP_TOO_MANY_REQUESTS


def handle_http_exception(error: HTTPException) -> Tuple[Dict[str, Any], int]:
    """兜住所有未单独注册的 werkzeug HTTPException，并保留原始状态码。

    为什么必须有它：下面注册了 `Exception` 兜底处理器，而 HTTPException 是
    Exception 的子类——于是所有没单独注册的 HTTP 异常（415 请求格式不对、
    400、409、422……）都会被兜底接住并变成 **500**。

    最典型的是 415：任何客户端 POST 时没带 `Content-Type: application/json`，
    `request.json` 就会抛 UnsupportedMediaType，结果接口返回 500「服务器内部错误」，
    同时还会被当成 CRITICAL 写进 error_logs 表——把客户端的用法问题伪装成服务端故障。
    返回真实状态码后，前端和运维都能一眼看出是请求端的问题。
    """
    code = getattr(error, "code", None) or HTTP_SERVER_ERROR
    if code >= 500:
        logger.error("HTTPException %d: %s", code, error)
    else:
        # 4xx 是调用方问题，不该污染错误日志的严重级别
        logger.warning("HTTPException %d: %s", code, error)
    return _error_body(_HTTP_MESSAGES.get(code, "请求处理失败")), code


def handle_500_error(error) -> Tuple[Dict[str, Any], int]:
    """处理 500 服务器内部错误"""
    tb = traceback.format_exc()
    logger.error("Server Error: %s\n%s", error, tb)
    # 记录到错误日志表（仅记录未处理的 500 错误）
    try:
        from error_tracker import record_error
        record_error(
            message=f"500 Error: {error}",
            exc=None,
            level="ERROR",
            module="server",
            request=request,
        )
    except Exception:
        pass
    return _error_body(_HTTP_MESSAGES[500]), HTTP_SERVER_ERROR


def handle_sqlite_error(error) -> Tuple[Dict[str, Any], int]:
    """处理 SQLite 异常"""
    from sqlite3 import OperationalError, IntegrityError
    if isinstance(error, IntegrityError):
        logger.warning("数据库完整性错误: %s", error)
        return {"status": "error", "message": "数据冲突，请检查输入"}, HTTP_BAD_REQUEST
    if isinstance(error, OperationalError):
        logger.error("数据库操作错误: %s", error)
        # 记录数据库操作错误
        try:
            from error_tracker import record_error
            record_error(
                message=f"SQLite OperationalError: {error}",
                exc=error,
                level="ERROR",
                module="sqlite3",
                request=request,
            )
        except Exception:
            pass
        return {"status": "error", "message": "数据库操作失败，请稍后重试"}, HTTP_SERVER_ERROR
    tb = traceback.format_exc()
    logger.error("数据库错误: %s\n%s", error, tb)
    try:
        from error_tracker import record_error
        record_error(
            message=f"SQLite Error: {error}",
            exc=error,
            level="ERROR",
            module="sqlite3",
            request=request,
        )
    except Exception:
        pass
    return {"status": "error", "message": "数据库错误"}, HTTP_SERVER_ERROR


def handle_generic_error(error) -> Tuple[Dict[str, Any], int]:
    """处理未知异常"""
    tb = traceback.format_exc()
    logger.error("Unhandled Error: %s\n%s", error, tb)
    # 记录到错误日志表
    try:
        from error_tracker import record_error
        record_error(
            message=f"Unhandled: {type(error).__name__}: {error}",
            exc=error,
            level="CRITICAL",
            module="server",
            request=request,
        )
    except Exception:
        pass
    return _error_body(_HTTP_MESSAGES[500]), HTTP_SERVER_ERROR


# ==================== 注册函数 ====================

def register_error_handlers(app: Flask) -> None:
    """
    注册所有全局错误处理器到 Flask 应用。
    
    Args:
        app: Flask 应用实例
        
    Example:
        app = Flask(__name__)
        register_error_handlers(app)
    """
    # 自定义异常
    app.register_error_handler(AppError, lambda e: handle_app_error(e))

    # HTTP 标准错误
    app.register_error_handler(404, handle_404_error)
    app.register_error_handler(405, handle_405_error)
    app.register_error_handler(413, handle_413_error)
    app.register_error_handler(429, handle_429_error)
    app.register_error_handler(500, handle_500_error)

    # 数据库异常
    from sqlite3 import Error as SQLiteError
    app.register_error_handler(SQLiteError, lambda e: handle_sqlite_error(e))

    # 其余所有 werkzeug HTTPException（415 请求格式不对、400、409、422 等）
    # 必须显式注册，否则会被下面的 Exception 兜底接住并谎报成 500。
    app.register_error_handler(HTTPException, lambda e: handle_http_exception(e))

    # 兜底异常处理器（放在最后，只接非 HTTP 的未知异常）
    app.register_error_handler(Exception, lambda e: handle_generic_error(e))
