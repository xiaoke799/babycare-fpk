#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xiaoke799

"""
统一通知器。
整合多平台推送、勿扰模式、推送历史、定时提醒。
"""

import json
import time
import logging
import threading
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta

from .multi_platform_notifier import MultiPlatformNotifier, NotifyResult

logger = logging.getLogger(__name__)


@dataclass
class NotificationResult:
    """通知发送结果"""
    success: bool
    method: str
    details: Dict[str, Any] = None


class UnifiedNotifier:
    """统一通知器"""

    def __init__(self, config: Dict[str, Any], db_path: str = ""):
        """
        Args:
            config: 配置字典
            db_path: 推送历史数据库路径
        """
        self.config = config
        self.db_path = db_path
        self._history_lock = threading.Lock()

        # 初始化多平台通知器
        self.multi_platform = MultiPlatformNotifier(
            wechat_webhook_url=config.get("wechat_webhook_url", ""),
            dingtalk_webhook_url=config.get("dingtalk_webhook_url", ""),
            feishu_webhook_url=config.get("feishu_webhook_url", ""),
            bark_url=config.get("bark_url", ""),
            pushplus_token=config.get("pushplus_token", ""),
            pushplus_topic=config.get("pushplus_topic", ""),
            title_prefix=config.get("title_prefix", "育儿宝"),
            timeout=config.get("notify_timeout", 10),
        )

        # 勿扰模式配置
        self._dnd_enabled = config.get("dnd_enabled", False)
        self._dnd_start = config.get("dnd_start_time", "22:00")
        self._dnd_end = config.get("dnd_end_time", "07:00")

        # 初始化历史数据库
        if db_path:
            self._init_history_db()

    def _init_history_db(self):
        """初始化推送历史数据库"""
        import sqlite3
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS push_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    channel TEXT NOT NULL,
                    title TEXT,
                    content TEXT,
                    success INTEGER NOT NULL,
                    error TEXT,
                    response TEXT
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_push_history_timestamp ON push_history(timestamp)")
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error("[Notifier] 初始化历史数据库失败: %s", e)

    def _save_history(self, results: List[NotifyResult], title: str, content: str):
        """保存推送历史"""
        if not self.db_path:
            return
        import sqlite3
        try:
            conn = sqlite3.connect(self.db_path)
            now = time.time()
            for r in results:
                conn.execute(
                    "INSERT INTO push_history (timestamp, channel, title, content, success, error, response) VALUES (?,?,?,?,?,?,?)",
                    (now, r.channel, title, content, 1 if r.success else 0, r.error, json.dumps(r.response, ensure_ascii=False) if r.response else None),
                )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error("[Notifier] 保存历史失败: %s", e)

    def _in_dnd_window(self) -> bool:
        """检查当前是否在勿扰时段"""
        if not self._dnd_enabled:
            return False
        now = datetime.now()
        current_minutes = now.hour * 60 + now.minute

        start_parts = self._dnd_start.split(":")
        end_parts = self._dnd_end.split(":")
        start_minutes = int(start_parts[0]) * 60 + int(start_parts[1])
        end_minutes = int(end_parts[0]) * 60 + int(end_parts[1])

        if end_minutes <= start_minutes:
            # 跨天（如 22:00 - 07:00）
            return current_minutes >= start_minutes or current_minutes < end_minutes
        return start_minutes <= current_minutes < end_minutes

    def send_notification(
        self,
        title: str,
        content: str,
        channels: Optional[List[str]] = None,
        skip_dedup: bool = False,
    ) -> NotificationResult:
        """
        发送通知。

        Args:
            title: 标题
            content: 内容
            channels: 指定渠道，None 表示所有
            skip_dedup: 跳过去重

        Returns:
            发送结果
        """
        # 勿扰模式检查
        if self._in_dnd_window() and not skip_dedup:
            logger.info("[Notifier] 勿扰时段，跳过推送: %s", title)
            return NotificationResult(
                success=True,
                method="dnd_skipped",
                details={"title": title, "dnd": True},
            )

        # 发送
        any_success, results = self.multi_platform.send(title, content, channels, skip_dedup)

        # 保存历史
        self._save_history(results, title, content)

        # 确定方法名
        active = [r.channel for r in results if r.success]
        if not active:
            method = "none"
        elif len(active) == 1:
            method = active[0]
        else:
            method = "multiple"

        return NotificationResult(
            success=any_success,
            method=method,
            details={
                "channels": active,
                "results": [{"channel": r.channel, "success": r.success, "error": r.error} for r in results],
            },
        )

    def send_test(self, channel: str = "") -> NotificationResult:
        """发送测试消息"""
        return self.send_notification(
            "测试消息",
            f"这是一条测试推送，发送时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            channels=[channel] if channel else None,
            skip_dedup=True,
        )

    def get_history(self, limit: int = 50, offset: int = 0) -> List[Dict]:
        """获取推送历史"""
        if not self.db_path:
            return []
        import sqlite3
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM push_history ORDER BY timestamp DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
            conn.close()
            result = []
            for row in rows:
                d = dict(row)
                d["timestamp_str"] = datetime.fromtimestamp(d["timestamp"]).strftime("%Y-%m-%d %H:%M:%S")
                result.append(d)
            return result
        except Exception as e:
            logger.error("[Notifier] 获取历史失败: %s", e)
            return []

    def get_stats(self) -> Dict[str, Any]:
        """获取推送统计"""
        if not self.db_path:
            return {"total": 0, "success": 0, "fail": 0}
        import sqlite3
        try:
            conn = sqlite3.connect(self.db_path)
            total = conn.execute("SELECT COUNT(*) FROM push_history").fetchone()[0]
            success = conn.execute("SELECT COUNT(*) FROM push_history WHERE success = 1").fetchone()[0]
            conn.close()
            return {
                "total": total,
                "success": success,
                "fail": total - success,
                "channels": self.multi_platform.get_active_channels(),
                "dnd_enabled": self._dnd_enabled,
            }
        except Exception as e:
            logger.error("[Notifier] 获取统计失败: %s", e)
            return {"total": 0, "success": 0, "fail": 0}

    def reload_config(self, config: Dict[str, Any]):
        """热加载配置"""
        self.config = config
        self._dnd_enabled = config.get("dnd_enabled", False)
        self._dnd_start = config.get("dnd_start_time", "22:00")
        self._dnd_end = config.get("dnd_end_time", "07:00")

        self.multi_platform.wechat_webhook_url = config.get("wechat_webhook_url", "")
        self.multi_platform.dingtalk_webhook_url = config.get("dingtalk_webhook_url", "")
        self.multi_platform.feishu_webhook_url = config.get("feishu_webhook_url", "")
        self.multi_platform.bark_url = config.get("bark_url", "")
        self.multi_platform.pushplus_token = config.get("pushplus_token", "")
        self.multi_platform.pushplus_topic = config.get("pushplus_topic", "")
        self.multi_platform.title_prefix = config.get("title_prefix", "育儿宝")
        logger.info("[Notifier] 配置已热加载")

    def cleanup_cache(self):
        """清理缓存"""
        self.multi_platform.cleanup_cache()
