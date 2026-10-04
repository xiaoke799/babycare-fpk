#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xiaoke799
"""
管理员日志查看路由。

提供：
- /api/admin/logs/stats    - 错误统计（按模块/级别/路径聚合）
- /api/admin/logs/errors   - 分页查询错误记录
- /api/admin/logs/trend    - 错误趋势（按小时/天）
- /api/admin/logs/detail/<id> - 单条错误详情
- /api/admin/logs/clear    - 清理过期错误记录
- /api/admin/logs/files    - 文件日志内容
- /api/admin/logs/webhooks - Webhook 调用审计（对外入口的排查依据）
"""

from flask import Blueprint, request, jsonify

from error_tracker import (
    get_error_stats,
    get_recent_errors,
    get_error_trend,
    get_error_detail,
    clear_old_errors,
)
from logger import get_recent_logs, get_log_stats, clear_logs, LOG_DIR
from logger import get_logger
from user_context import require_admin

bp = Blueprint("admin_logs", __name__)
logger = get_logger("admin.logs")


# ==================== 错误聚合统计 ====================


@bp.route("/api/admin/logs/stats", methods=["GET"])
@require_admin
def admin_log_stats():
    """获取错误日志统计"""
    try:
        days = request.args.get("days", 7, type=int)
        days = min(max(days, 1), 90)  # 限制 1-90 天
        stats = get_error_stats(days=days)
        return jsonify({"success": True, **stats})
    except Exception as e:
        logger.error("获取错误统计失败: %s", e, exc_info=True)
        return jsonify({"success": False, "message": str(e)}), 500


@bp.route("/api/admin/logs/errors", methods=["GET"])
@require_admin
def admin_log_errors():
    """分页查询错误记录"""
    try:
        limit = request.args.get("limit", 50, type=int)
        offset = request.args.get("offset", 0, type=int)
        level = request.args.get("level", "")
        module = request.args.get("module", "")
        days = request.args.get("days", 7, type=int)

        limit = min(max(limit, 1), 200)
        days = min(max(days, 1), 90)

        result = get_recent_errors(
            limit=limit, offset=offset, level=level, module=module, days=days
        )
        return jsonify({"success": True, **result})
    except Exception as e:
        logger.error("查询错误记录失败: %s", e, exc_info=True)
        return jsonify({"success": False, "message": str(e)}), 500


@bp.route("/api/admin/logs/trend", methods=["GET"])
@require_admin
def admin_log_trend():
    """获取错误趋势"""
    try:
        days = request.args.get("days", 7, type=int)
        group_by = request.args.get("group_by", "day")
        days = min(max(days, 1), 90)

        trend = get_error_trend(days=days, group_by=group_by)
        return jsonify({"success": True, "data": trend})
    except Exception as e:
        logger.error("获取错误趋势失败: %s", e, exc_info=True)
        return jsonify({"success": False, "message": str(e)}), 500


@bp.route("/api/admin/logs/detail/<int:error_id>", methods=["GET"])
@require_admin
def admin_log_detail(error_id):
    """获取单条错误详情"""
    try:
        detail = get_error_detail(error_id)
        if not detail:
            return jsonify({"success": False, "message": "记录不存在"}), 404
        return jsonify({"success": True, "data": detail})
    except Exception as e:
        logger.error("获取错误详情失败: %s", e, exc_info=True)
        return jsonify({"success": False, "message": str(e)}), 500


@bp.route("/api/admin/logs/clear", methods=["POST"])
@require_admin
def admin_log_clear():
    """清理过期错误记录"""
    try:
        data = request.get_json(silent=True) or {}
        days = data.get("days", 30)
        days = min(max(days, 1), 365)
        count = clear_old_errors(days=days)
        logger.info("管理员清理了 %d 条错误记录（保留 %d 天）", count, days)
        return jsonify({"success": True, "message": f"已清理 {count} 条过期错误记录", "count": count})
    except Exception as e:
        logger.error("清理错误记录失败: %s", e, exc_info=True)
        return jsonify({"success": False, "message": str(e)}), 500


# ==================== 文件日志查看 ====================


@bp.route("/api/admin/logs/files", methods=["GET"])
@require_admin
def admin_log_files():
    """获取文件日志统计"""
    try:
        file_stats = get_log_stats()
        disk_free = 0
        try:
            import shutil
            disk = shutil.disk_usage(LOG_DIR)
            disk_free = disk.free
        except Exception:
            pass

        return jsonify({
            "success": True,
            "files": file_stats,
            "disk_free": disk_free,
        })
    except Exception as e:
        logger.error("获取日志文件统计失败: %s", e, exc_info=True)
        return jsonify({"success": False, "message": str(e)}), 500


@bp.route("/api/admin/logs/files/content", methods=["GET"])
@require_admin
def admin_log_files_content():
    """读取文件日志内容"""
    try:
        log_type = request.args.get("type", "main")
        lines = request.args.get("lines", 100, type=int)
        lines = min(max(lines, 10), 1000)

        content = get_recent_logs(lines=lines, log_type=log_type)
        return jsonify({"success": True, "content": content, "type": log_type})
    except Exception as e:
        logger.error("读取日志文件失败: %s", e, exc_info=True)
        return jsonify({"success": False, "message": str(e)}), 500


@bp.route("/api/admin/logs/files/clear", methods=["POST"])
@require_admin
def admin_log_files_clear():
    """清空文件日志"""
    try:
        data = request.get_json(silent=True) or {}
        log_type = data.get("type", "all")
        results = clear_logs(log_type=log_type)
        logger.info("管理员清空了日志: %s", log_type)
        return jsonify({"success": True, "results": results})
    except Exception as e:
        logger.error("清空日志失败: %s", e, exc_info=True)
        return jsonify({"success": False, "message": str(e)}), 500


# ==================== Webhook 调用审计 ====================
#
# 只查Webhook 通道（bcw_ Token）的调用，**不含 HA 集成**——
# HA 走集成 Token（bcb_），它的调用记录在 HA 那边看。
# 为什么要查它：这条通道是局域网内对外开放的网络入口，用户接入床头无线按钮之后，
# 「按了没反应」是最高频的问题。以前 webhook_logs 只写不读 —— 既没有查询接口，
# 也不进备份，于是用户只能去翻日志文件（而它根本不写那儿）。
# 换句话说：出问题时，唯一能证明「到底有没有调到、有没有被拒」的证据被关在库里。

@bp.route("/api/admin/logs/webhooks", methods=["GET"])
@require_admin
def admin_log_webhooks():
    """分页查询 webhook 调用审计（成功/失败都记）"""
    try:
        from utils import get_db, row_to_dict, rows_to_list

        limit = min(max(request.args.get("limit", 50, type=int), 1), 200)
        offset = max(request.args.get("offset", 0, type=int), 0)
        days = min(max(request.args.get("days", 7, type=int), 1), 90)
        only_failed = request.args.get("failed") == "1"

        db = get_db()
        where = "WHERE created_at >= datetime('now', 'localtime', ?)"
        params: list = [f"-{days} days"]
        if only_failed:
            where += " AND ok = 0"

        total = db.execute(
            f"SELECT COUNT(*) AS c FROM webhook_logs {where}", tuple(params)
        ).fetchone()["c"]
        rows = db.execute(
            f"SELECT id, created_at, event, baby_id, ok, reason, remote_addr "
            f"FROM webhook_logs {where} "
            f"ORDER BY replace(created_at,'T',' ') DESC, id DESC "
            f"LIMIT ? OFFSET ?",
            tuple(params) + (limit, offset),
        ).fetchall()
        failed_total = db.execute(
            "SELECT COUNT(*) AS c FROM webhook_logs WHERE ok = 0"
            " AND created_at >= datetime('now', 'localtime', ?)", (f"-{days} days",)
        ).fetchone()["c"]

        return jsonify({
            "success": True,
            "data": rows_to_list(rows),
            "total": total,
            "failed": failed_total,
            "limit": limit,
            "offset": offset,
        })
    except Exception as e:
        logger.error("查询 webhook 审计失败: %s", e, exc_info=True)
        return jsonify({"success": False, "message": str(e)}), 500


@bp.route("/api/admin/logs/webhooks/clear", methods=["POST"])
@require_admin
def admin_log_webhooks_clear():
    """清理过期 webhook 审计（保留最近 N 天）"""
    try:
        from utils import get_db

        days = min(max((request.get_json(silent=True) or {}).get("days", 30) or 30, 1), 365)
        db = get_db()
        cur = db.execute(
            "DELETE FROM webhook_logs WHERE created_at < datetime('now','localtime',?)",
            (f"-{days} days",),
        )
        db.commit()
        return jsonify({"success": True, "deleted": cur.rowcount, "keep_days": days})
    except Exception as e:
        logger.error("清理 webhook 审计失败: %s", e, exc_info=True)
        return jsonify({"success": False, "message": str(e)}), 500
