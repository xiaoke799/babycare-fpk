#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xiaoke799

"""
定时提醒调度器。
检查喂奶、换尿布、睡眠、用药、疫苗等提醒。
"""

import time
import logging
import threading
from typing import Dict, Any, List, Optional, Callable
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class ReminderScheduler:
    """定时提醒调度器"""

    def __init__(self, notifier, db_getter: Callable, config: Dict[str, Any]):
        """
        Args:
            notifier: UnifiedNotifier 实例
            db_getter: 获取数据库连接的回调函数
            config: 配置字典
        """
        self.notifier = notifier
        self.db_getter = db_getter
        self.config = config
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._check_interval = config.get("reminder_check_interval", 60)  # 默认 60 秒检查一次

        # 上次提醒时间（用于去重）
        self._last_reminders: Dict[str, float] = {}

    def start(self):
        """启动调度器"""
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="ReminderScheduler")
        self._thread.start()
        logger.info("[Scheduler] 定时提醒调度器已启动，检查间隔: %ds", self._check_interval)

    def stop(self):
        """停止调度器"""
        self._running = False
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("[Scheduler] 定时提醒调度器已停止")

    def _run(self):
        """主循环"""
        while self._running and not self._stop_event.is_set():
            try:
                self._check_all()
            except Exception as e:
                logger.error("[Scheduler] 检查提醒异常: %s", e)
            self._stop_event.wait(self._check_interval)

    def _check_all(self):
        """检查所有提醒"""
        if not self.config.get("reminder_enabled", True):
            return

        now = datetime.now()

        # 喂奶提醒
        if self.config.get("feeding_reminder_enabled", False):
            self._check_feeding_reminder(now)

        # 换尿布提醒
        if self.config.get("diaper_reminder_enabled", False):
            self._check_diaper_reminder(now)

        # 用药提醒
        if self.config.get("medication_reminder_enabled", False):
            self._check_medication_reminder(now)

        # 疫苗提醒
        if self.config.get("vaccine_reminder_enabled", False):
            self._check_vaccine_reminder(now)

    def _should_remind(self, key: str, interval_minutes: int) -> bool:
        """检查是否应该提醒（基于间隔）"""
        now = time.time()
        last = self._last_reminders.get(key, 0)
        if now - last >= interval_minutes * 60:
            self._last_reminders[key] = now
            return True
        return False

    def _check_feeding_reminder(self, now: datetime):
        """检查喂奶提醒"""
        interval_hours = self.config.get("feeding_reminder_interval", 3)
        interval_minutes = int(interval_hours * 60)

        try:
            db = self.db_getter()
            # 获取所有宝宝
            babies = db.execute("SELECT id, name FROM babies WHERE active = 1").fetchall()
            for baby in babies:
                # 获取最后一次喂奶记录
                last_feeding = db.execute(
                    "SELECT MAX(created_at) as last_time FROM feedings WHERE baby_id = ?",
                    (baby["id"],),
                ).fetchone()

                if last_feeding and last_feeding["last_time"]:
                    last_time = datetime.fromisoformat(last_feeding["last_time"])
                    elapsed = (now - last_time).total_seconds() / 60  # 分钟

                    if elapsed >= interval_minutes:
                        key = f"feeding_{baby['id']}"
                        if self._should_remind(key, interval_minutes):
                            hours = elapsed / 60
                            self.notifier.send_notification(
                                "喂奶提醒",
                                f"🍼 {baby['name']} 已经 {hours:.1f} 小时没有喂奶了，该喂奶啦！",
                                skip_dedup=True,
                            )
        except Exception as e:
            logger.error("[Scheduler] 检查喂奶提醒失败: %s", e)

    def _check_diaper_reminder(self, now: datetime):
        """检查换尿布提醒"""
        interval_hours = self.config.get("diaper_reminder_interval", 2)
        interval_minutes = int(interval_hours * 60)

        try:
            db = self.db_getter()
            babies = db.execute("SELECT id, name FROM babies WHERE active = 1").fetchall()
            for baby in babies:
                last_diaper = db.execute(
                    "SELECT MAX(created_at) as last_time FROM diaper_changes WHERE baby_id = ?",
                    (baby["id"],),
                ).fetchone()

                if last_diaper and last_diaper["last_time"]:
                    last_time = datetime.fromisoformat(last_diaper["last_time"])
                    elapsed = (now - last_time).total_seconds() / 60

                    if elapsed >= interval_minutes:
                        key = f"diaper_{baby['id']}"
                        if self._should_remind(key, interval_minutes):
                            self.notifier.send_notification(
                                "换尿布提醒",
                                f"[尿布] {baby['name']} 已经 {elapsed/60:.1f} 小时没有换尿布了，检查一下是否需要更换！",
                                skip_dedup=True,
                            )
        except Exception as e:
            logger.error("[Scheduler] 检查换尿布提醒失败: %s", e)

    def _check_medication_reminder(self, now: datetime):
        """检查用药提醒"""
        try:
            db = self.db_getter()
            # 获取所有启用的用药提醒
            reminders = db.execute(
                "SELECT mr.*, b.name as baby_name FROM medication_reminders mr "
                "JOIN babies b ON mr.baby_id = b.id "
                "WHERE mr.enabled = 1 AND b.active = 1"
            ).fetchall()

            for rem in reminders:
                reminder_time = rem["reminder_time"]  # HH:MM 格式
                if not reminder_time:
                    continue

                # 解析提醒时间
                try:
                    h, m = reminder_time.split(":")
                    reminder_dt = now.replace(hour=int(h), minute=int(m), second=0, microsecond=0)
                except (ValueError, IndexError):
                    continue

                # 检查是否到达提醒时间（允许 1 分钟误差）
                diff = abs((now - reminder_dt).total_seconds())
                if diff <= 60:
                    key = f"med_{rem['id']}"
                    if self._should_remind(key, 1440):  # 每天只提醒一次
                        self.notifier.send_notification(
                            "用药提醒",
                            f"💊 {rem['baby_name']} 该吃药了！\n药品：{rem['medication_name']}\n剂量：{rem.get('dosage', '遵医嘱')}",
                            skip_dedup=True,
                        )
        except Exception as e:
            logger.error("[Scheduler] 检查用药提醒失败: %s", e)

    def _check_vaccine_reminder(self, now: datetime):
        """检查疫苗提醒（从 vaccine_details 表读取）"""
        days_ahead = self.config.get("vaccine_reminder_days", 3)

        try:
            db = self.db_getter()
            # 获取即将到期的疫苗（从 vaccine_details 表）
            future_date = (now + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
            today = now.strftime("%Y-%m-%d")

            vaccines = db.execute(
                "SELECT v.*, b.name as baby_name FROM vaccine_details v "
                "JOIN babies b ON v.baby_id = b.id "
                "WHERE v.scheduled_date >= ? AND v.scheduled_date <= ? "
                "AND v.status = 'pending' AND b.active = 1 "
                "ORDER BY v.scheduled_date",
                (today, future_date),
            ).fetchall()

            for vac in vaccines:
                key = f"vaccine_{vac['id']}"
                if self._should_remind(key, 1440):  # 每天只提醒一次
                    scheduled = vac["scheduled_date"]
                    dose = vac.get("dose_number", "")
                    self.notifier.send_notification(
                        "疫苗提醒",
                        f"[!] {vac['baby_name']} 该接种疫苗了!\n疫苗：{vac['vaccine_name']} 第{dose}剂\n预约日期：{scheduled}",
                        skip_dedup=True,
                    )
        except Exception as e:
            logger.error("[Scheduler] 检查疫苗提醒失败: %s", e)

    def reload_config(self, config: Dict[str, Any]):
        """热加载配置"""
        self.config = config
        self._check_interval = config.get("reminder_check_interval", 60)
        logger.info("[Scheduler] 配置已热加载")
