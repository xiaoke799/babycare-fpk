#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xiaoke799

"""
推送通知 API。
提供推送配置、测试、历史记录等接口。
"""

import os
from logger import get_logger
from flask import Blueprint, request, jsonify, g

from user_context import require_auth, require_admin

logger = get_logger("notifications")
bp = Blueprint("notifications", __name__, url_prefix="/api/notifications")

# 需要打码的凭据字段：这些值本身就是密钥（webhook URL 里带 key/token）。
# 此前 /api/notifications/config 是「无鉴权 + 明文」返回的，任何能访问到服务的
# 人都能拿到推送凭据，并能改配置/发测试消息。
_SECRET_KEYS = (
    "wechat_webhook_url", "dingtalk_webhook_url", "feishu_webhook_url",
    "bark_url", "pushplus_token",
)


def _mask(value):
    # 把凭据打码成固定形态。
    # 「固定」很关键：前端是「加载进输入框 -> 原样提交回后端」的用法，
    # 只要同一原文的掩码恒定，update_config 里就能靠「提交值 == 掩码」
    # 判断出用户没改这一项并跳过写入，不必改前端。
    if not value:
        return ""
    s = str(value)
    if len(s) <= 8:
        return "****"
    return s[:4] + "****" + s[-4:]

# 全局 notifier 实例（由 server.py 注入）
_notifier = None
_scheduler = None


def init_notifier(notifier, scheduler=None):
    """由 server.py 调用注入 notifier 实例"""
    global _notifier, _scheduler
    _notifier = notifier
    _scheduler = scheduler


def _get_notifier():
    """获取 notifier 实例"""
    return _notifier


@bp.route("/config", methods=["GET"])
@require_auth
def get_config():
    """获取推送配置"""
    notifier = _get_notifier()
    if not notifier:
        # 返回默认配置而不是 500 错误
        return jsonify({
            "success": True,
            "config": {
                "wechat_webhook_url": "",
                "dingtalk_webhook_url": "",
                "feishu_webhook_url": "",
                "bark_url": "",
                "pushplus_token": "",
                "pushplus_topic": "",
                "title_prefix": "育儿宝",
                "dnd_enabled": False,
                "dnd_start_time": "22:00",
                "dnd_end_time": "07:00",
                "reminder_enabled": True,
                "feeding_reminder_enabled": False,
                "feeding_reminder_interval": 3,
                "diaper_reminder_enabled": False,
                "diaper_reminder_interval": 2,
                "medication_reminder_enabled": False,
                "vaccine_reminder_enabled": False,
                "vaccine_reminder_days": 3,
                "reminder_check_interval": 60,
            },
            "channels": [],
            "notifier_initialized": False,
        })

    # 返回配置。凭据字段只回打码值 —— 此前注释写着「隐藏敏感信息」，
    # 实际是把明文 webhook/token 原样吐出去了。
    config = notifier.config
    return jsonify({
        "success": True,
        "config": {
            "wechat_webhook_url": _mask(config.get("wechat_webhook_url")),
            "dingtalk_webhook_url": _mask(config.get("dingtalk_webhook_url")),
            "feishu_webhook_url": _mask(config.get("feishu_webhook_url")),
            "bark_url": _mask(config.get("bark_url")),
            "pushplus_token": _mask(config.get("pushplus_token")),
            "pushplus_topic": config.get("pushplus_topic", ""),
            "title_prefix": config.get("title_prefix", "育儿宝"),
            "dnd_enabled": config.get("dnd_enabled", False),
            "dnd_start_time": config.get("dnd_start_time", "22:00"),
            "dnd_end_time": config.get("dnd_end_time", "07:00"),
            # 提醒配置
            "reminder_enabled": config.get("reminder_enabled", True),
            "feeding_reminder_enabled": config.get("feeding_reminder_enabled", False),
            "feeding_reminder_interval": config.get("feeding_reminder_interval", 3),
            "diaper_reminder_enabled": config.get("diaper_reminder_enabled", False),
            "diaper_reminder_interval": config.get("diaper_reminder_interval", 2),
            "medication_reminder_enabled": config.get("medication_reminder_enabled", False),
            "vaccine_reminder_enabled": config.get("vaccine_reminder_enabled", False),
            "vaccine_reminder_days": config.get("vaccine_reminder_days", 3),
            "reminder_check_interval": config.get("reminder_check_interval", 60),
        },
        "channels": notifier.multi_platform.get_active_channels(),
        "notifier_initialized": True,
    })


@bp.route("/config", methods=["PUT", "POST"])
@require_admin
def update_config():
    """更新推送配置"""
    notifier = _get_notifier()
    if not notifier:
        return jsonify({"success": False, "message": "推送模块未初始化"}), 500

    data = request.get_json(silent=True) or {}

    # 更新配置
    config_keys = [
        "wechat_webhook_url", "dingtalk_webhook_url", "feishu_webhook_url",
        "bark_url", "pushplus_token", "pushplus_topic", "title_prefix",
        "dnd_enabled", "dnd_start_time", "dnd_end_time",
        "reminder_enabled", "feeding_reminder_enabled", "feeding_reminder_interval",
        "diaper_reminder_enabled", "diaper_reminder_interval",
        "medication_reminder_enabled", "vaccine_reminder_enabled",
        "vaccine_reminder_days", "reminder_check_interval",
    ]

    for key in config_keys:
        if key not in data:
            continue
        if key in _SECRET_KEYS and data[key] == _mask(notifier.config.get(key)):
            # 前端把打码值原样提交回来了 —— 说明用户没动这一项，保持原密钥
            continue
        notifier.config[key] = data[key]

    # 热加载
    notifier.reload_config(notifier.config)
    if _scheduler:
        _scheduler.reload_config(notifier.config)

    # 持久化到数据库
    try:
        from server import _set_app_setting
        for key in config_keys:
            if key in notifier.config:
                _set_app_setting(f"notify_{key}", notifier.config[key])
    except Exception as e:
        logger.warning("保存推送配置失败: %s", e)

    return jsonify({"success": True, "message": "配置已更新"})


@bp.route("/test", methods=["POST"])
@require_admin
def send_test():
    """发送测试消息"""
    notifier = _get_notifier()
    if not notifier:
        return jsonify({"success": False, "message": "推送模块未初始化"}), 500

    data = request.get_json(silent=True) or {}
    channel = data.get("channel", "")

    result = notifier.send_test(channel)
    return jsonify({
        "success": result.success,
        "method": result.method,
        "details": result.details,
    })


@bp.route("/history", methods=["GET"])
@require_auth
def get_history():
    """获取推送历史"""
    notifier = _get_notifier()
    if not notifier:
        return jsonify({"success": False, "message": "推送模块未初始化"}), 500

    limit = request.args.get("limit", 50, type=int)
    offset = request.args.get("offset", 0, type=int)

    history = notifier.get_history(limit=limit, offset=offset)
    return jsonify({"success": True, "history": history})


@bp.route("/stats", methods=["GET"])
@require_auth
def get_stats():
    """获取推送统计"""
    notifier = _get_notifier()
    if not notifier:
        return jsonify({"success": False, "message": "推送模块未初始化"}), 500

    stats = notifier.get_stats()
    return jsonify({"success": True, "stats": stats})


@bp.route("/history", methods=["DELETE"])
@require_admin
def clear_history():
    """清空推送历史"""
    notifier = _get_notifier()
    if not notifier:
        return jsonify({"success": False, "message": "推送模块未初始化"}), 500

    if notifier.db_path and os.path.exists(notifier.db_path):
        try:
            import sqlite3
            conn = sqlite3.connect(notifier.db_path)
            conn.execute("DELETE FROM push_history")
            conn.commit()
            conn.close()
            return jsonify({"success": True, "message": "历史已清空"})
        except Exception as e:
            return jsonify({"success": False, "message": str(e)}), 500

    return jsonify({"success": True, "message": "无历史记录"})
