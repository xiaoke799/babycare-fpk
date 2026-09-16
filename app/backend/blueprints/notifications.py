#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xiaoke799

"""
推送通知 API。
提供推送配置、测试、历史记录等接口。
"""

import os
import re
from logger import get_logger
from flask import Blueprint, request, jsonify, g

from user_context import require_auth, require_admin
from utils import json_body

logger = get_logger("notifications")
bp = Blueprint("notifications", __name__, url_prefix="/api/notifications")

# 需要打码的凭据字段：这些值本身就是密钥（webhook URL 里带 key/token）。
# 此前 /api/notifications/config 是「无鉴权 + 明文」返回的，任何能访问到服务的
# 人都能拿到推送凭据，并能改配置/发测试消息。
_SECRET_KEYS = (
    "wechat_webhook_url", "dingtalk_webhook_url", "feishu_webhook_url",
    "bark_url", "pushplus_token",
)

# 可配置项清单：**读接口与写接口共用同一份**，避免两处清单走岔
# （历史上读接口就比写接口少一项，前端改了也不生效）。
CONFIG_KEYS = (
    "wechat_webhook_url", "dingtalk_webhook_url", "feishu_webhook_url",
    "bark_url", "pushplus_token", "pushplus_topic", "title_prefix",
    "dnd_enabled", "dnd_start_time", "dnd_end_time",
    "reminder_enabled", "feeding_reminder_enabled", "feeding_reminder_interval",
    "diaper_reminder_enabled", "diaper_reminder_interval",
    "medication_reminder_enabled", "vaccine_reminder_enabled",
    "vaccine_reminder_days", "reminder_check_interval", "notify_timeout",
)

# 数值型配置的取值范围（写入前夹紧）：这些值直接决定调度频率，
# 传 0 或负数会让提醒逻辑算出错误间隔，传超大值相当于关掉提醒。
_INT_RANGES = {
    "feeding_reminder_interval": (1, 24),
    "diaper_reminder_interval": (1, 24),
    "vaccine_reminder_days": (1, 60),
    "reminder_check_interval": (30, 3600),
    "notify_timeout": (1, 60),
}

_HHMM_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

# 默认配置：读接口的两个分支（模块未初始化 / 已初始化但缺项）共用，
# 免得默认值散落三处、改一处漏两处
_DEFAULTS = {
    "wechat_webhook_url": "", "dingtalk_webhook_url": "", "feishu_webhook_url": "",
    "bark_url": "", "pushplus_token": "", "pushplus_topic": "",
    "title_prefix": "育儿宝",
    "dnd_enabled": False, "dnd_start_time": "22:00", "dnd_end_time": "07:00",
    "reminder_enabled": True,
    "feeding_reminder_enabled": False, "feeding_reminder_interval": 3,
    "diaper_reminder_enabled": False, "diaper_reminder_interval": 2,
    "medication_reminder_enabled": False,
    "vaccine_reminder_enabled": False, "vaccine_reminder_days": 3,
    "reminder_check_interval": 60,
    "notify_timeout": 10,
}


def _config_payload(config, mask_secrets=True):
    """把配置整理成前端要的平铺结构（凭据可选打码）"""
    out = {}
    for key in CONFIG_KEYS:
        raw = config.get(key, _DEFAULTS.get(key))
        out[key] = _mask(raw) if (mask_secrets and key in _SECRET_KEYS) else raw
    return out


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
        # 返回默认配置而不是 500 错误（设置页要能正常渲染）
        return jsonify({
            "success": True,
            "config": _config_payload(_DEFAULTS),
            "channels": [],
            "notifier_initialized": False,
        })

    # 凭据字段只回打码值 —— 此前注释写着「隐藏敏感信息」，
    # 实际是把明文 webhook/token 原样吐出去了。
    return jsonify({
        "success": True,
        "config": _config_payload(notifier.config),
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

    data = json_body()
    cfg = notifier.config

    for key in CONFIG_KEYS:
        if key not in data:
            continue
        value = data[key]

        # 前端把打码值原样提交回来了 —— 说明用户没动这一项，保持原密钥
        if key in _SECRET_KEYS and value == _mask(cfg.get(key)):
            continue

        if key in _INT_RANGES:
            # 这些值直接决定调度频率：传 0/负数/字符串会让提醒逻辑算错间隔，
            # 早期实现直接 int() 后存库，填个「三」就整条请求 500
            try:
                value = int(value)
            except (TypeError, ValueError):
                return jsonify({"success": False, "message": f"{key} 需要是数字"}), 400
            lo, hi = _INT_RANGES[key]
            value = max(lo, min(value, hi))

        elif key in ("dnd_start_time", "dnd_end_time"):
            value = str(value or "").strip()
            if not _HHMM_RE.match(value):
                return jsonify({"success": False, "message": "勿扰时间格式应为 HH:MM（如 22:00）"}), 400

        elif key.endswith("_webhook_url") or key == "bark_url":
            value = str(value or "").strip()
            # 允许清空（= 停用该渠道）；非空则必须是 http(s) 地址，
            # 否则等到真正推送时才在 urlopen 上抛 "unknown url type"
            if value and not value.startswith(("http://", "https://")):
                return jsonify({"success": False, "message": "推送地址需以 http:// 或 https:// 开头"}), 400

        elif key.endswith("_enabled"):
            value = bool(value)

        cfg[key] = value

    # 热加载
    notifier.reload_config(cfg)
    if _scheduler:
        _scheduler.reload_config(cfg)

    # 持久化到数据库：失败必须让用户知道，否则本次「已保存」只是内存里的假象，
    # 重启后配置又退回旧值
    saved = True
    try:
        # 直接用 utils 的实现：早期写的是 `from server import _set_app_setting`，
        # 而 server 里那个名字同样是从 utils 转进来的，绕一圈只会多一个导入时序坑
        from utils import _set_app_setting
        for key in CONFIG_KEYS:
            if key in cfg:
                _set_app_setting(f"notify_{key}", cfg[key])
    except Exception as e:
        saved = False
        logger.warning("保存推送配置失败: %s", e)

    return jsonify({
        "success": True,
        "saved": saved,
        "message": "配置已更新" if saved else "配置已生效，但写入数据库失败（重启后会丢失）",
    })


@bp.route("/test", methods=["POST"])
@require_admin
def send_test():
    """发送测试消息"""
    notifier = _get_notifier()
    if not notifier:
        return jsonify({"success": False, "message": "推送模块未初始化"}), 500

    data = json_body()
    channel = str(data.get("channel") or "").strip()

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

    # 夹紧分页参数：limit 无上限时，前端传个大数就能把整张历史表拉进内存
    limit = request.args.get("limit", 50, type=int) or 50
    limit = max(1, min(limit, 200))
    offset = max(0, request.args.get("offset", 0, type=int) or 0)

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
