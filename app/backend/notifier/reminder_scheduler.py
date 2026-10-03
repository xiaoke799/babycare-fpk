#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xiaoke799

"""
定时提醒调度器。
检查喂奶、换尿布、睡眠、用药、疫苗等提醒。
"""

import os
import time
import logging
import threading
from typing import Dict, Any, List, Optional, Callable
from datetime import datetime, timedelta

# 跨进程锁：生产启动是 gunicorn --workers 2（见 start_production.sh），
# 两个进程都会初始化调度器；而 _last_reminders 只是**进程内内存**，
# 不做互斥的话同一时刻两个调度线程各查各的、各发一遍 —— 用户每条提醒收两遍。
# storage.py 的自动备份循环已用同样手法处理过这个场景。
try:
    import fcntl
    _HAS_FCNTL = True
except ImportError:          # Windows 默认没有 fcntl，走下面的 msvcrt
    fcntl = None
    _HAS_FCNTL = False

try:
    import msvcrt            # Windows 的等价物：本地开发时也能真正互斥，便于验证
    _HAS_MSVCRT = True
except ImportError:
    msvcrt = None
    _HAS_MSVCRT = False

logger = logging.getLogger(__name__)


def _parse_dt(value):
    """解析库里的时间字符串。兼容 'YYYY-MM-DD HH:MM:SS' 与 'YYYY-MM-DDTHH:MM'"""
    if not value:
        return None
    text = str(value).replace("T", " ").strip()[:19]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _row_get(row, key, default=""):
    """从 sqlite3.Row 安全取值。

    本项目的连接都设了 row_factory = sqlite3.Row（database.get_db / utils.get_db），
    **Row 没有 .get()** —— 直接调会抛 AttributeError，被调度器的 except 吞掉后
    提醒就永远发不出来（用药/疫苗提醒曾因此整条链路失效）。
    """
    try:
        value = row[key]
    except (KeyError, IndexError, TypeError):
        return default
    return default if value is None else value


class ReminderScheduler:
    """定时提醒调度器"""

    def __init__(self, notifier, db_getter: Callable, config: Dict[str, Any],
                 lock_dir: str = ""):
        """
        Args:
            notifier: UnifiedNotifier 实例
            db_getter: 获取数据库连接的回调函数
            config: 配置字典
            lock_dir: 跨进程锁文件目录（默认取推送历史库所在目录）
        """
        self.notifier = notifier
        self.db_getter = db_getter
        self.config = config
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._check_interval = config.get("reminder_check_interval", 60)  # 默认 60 秒检查一次

        # 上次提醒时间（用于去重）。注意：**只在进程内有效** ——
        # 跨 worker 的去重靠下面 _acquire_lock()：同一时刻只有一个进程在跑调度。
        self._last_reminders: Dict[str, float] = {}

        self._lock_handle = None
        self._lock_dir = lock_dir or os.path.dirname(
            getattr(notifier, "db_path", "") or "") or "."

    def _acquire_lock(self):
        """抢跨进程调度锁。拿到返回文件句柄，没拿到返回 None（本进程不跑调度）。

        多 worker 会同时启动并同时走到这里，flock 的 LOCK_NB 保证只有一个成功，
        否则两个进程会各发一遍提醒。
        """
        try:
            os.makedirs(self._lock_dir, exist_ok=True)
            fh = open(os.path.join(self._lock_dir, ".reminder_scheduler.lock"), "w")
        except OSError as e:
            logger.warning("[Scheduler] 无法创建调度锁文件: %s", e)
            return None

        def _give_up():
            # Windows 上被别的进程锁住后，close() 本身也可能抛 PermissionError，
            # 不包起来会把「抢不到锁」升级成整个初始化失败。
            try:
                fh.close()
            except OSError:
                pass
            return None

        if _HAS_FCNTL:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return _give_up()       # 其他 worker 已持有 → 本进程不参与调度
        elif _HAS_MSVCRT:
            try:
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                return _give_up()

        # 拿到锁之后再写 pid（msvcrt 锁的是字节区间，空文件也能锁）
        try:
            fh.seek(0)
            fh.write(str(os.getpid()))
            fh.truncate()
            fh.flush()
        except OSError:
            pass
        return fh

    def start(self):
        """启动调度器。多 worker 下只有一个进程真正跑，避免提醒重复。"""
        if self._running:
            return
        self._lock_handle = self._acquire_lock()
        if self._lock_handle is None:
            logger.info("[Scheduler] 另一进程已持有调度锁，本进程不启动调度线程（避免重复提醒）")
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
        if self._lock_handle:
            try:
                self._lock_handle.close()   # 关闭即释放 flock
            except OSError:
                pass
            self._lock_handle = None
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

        # 体检 / 自定义健康提醒（health_reminders 表）
        if self.config.get("health_reminder_enabled", False):
            self._check_health_reminder(now)

    def _should_remind(self, key: str, interval_minutes: int) -> bool:
        """是否到点该提醒（**只读判断，不改状态**）。

        早期版本在这里就把「已提醒时间」记上了，于是只要发送环节出问题
        （webhook 挂了、网络不通），本次提醒就被记为已发；下次重试要再等一整个
        间隔（喂奶 3 小时、用药 24 小时）—— 等于这条提醒直接丢了。
        现在改为发送成功后调用 _mark_reminded() 记账。
        """
        last = self._last_reminders.get(key, 0)
        return time.time() - last >= interval_minutes * 60

    def _mark_reminded(self, key: str):
        """发送成功后记账（勿扰跳过与发送失败都不算，留给下次检查重试）"""
        self._last_reminders[key] = time.time()

    def _remind(self, key: str, interval_minutes: int, title: str, content: str) -> bool:
        """发送一条提醒：成功才记账。返回是否真正发出。"""
        if not self._should_remind(key, interval_minutes):
            return False
        result = self.notifier.send_notification(title, content, skip_dedup=True)
        # method == 'dnd_skipped' 表示当前在勿扰时段、并没有真的推送，
        # 不能算「已提醒」，否则勿扰结束就不会补发了。
        if result.success and result.method != "dnd_skipped":
            self._mark_reminded(key)
            return True
        return False

    def _check_feeding_reminder(self, now: datetime):
        """检查喂奶提醒"""
        interval_hours = self.config.get("feeding_reminder_interval", 3)
        interval_minutes = int(float(interval_hours) * 60)

        try:
            db = self.db_getter()
            # 获取所有宝宝
            babies = db.execute("SELECT id, name FROM babies").fetchall()
        except Exception as e:
            logger.error("[Scheduler] 检查喂奶提醒失败: %s", e)
            return

        # ⚠️ 每个宝宝单独 try。2026-09-19 修：原先整个 for 被同一个 try 包着，
        # 只要某个宝宝在 _remind 里抛一次异常（推送失败、notifier 返回异常…），
        # for 立刻中断 —— 结果是「排在它后面的宝宝永远收不到提醒」，
        # 而日志只有一条笼统的「检查喂奶提醒失败」，根本看不出漏了谁。
        for baby in babies:
            try:
                # 获取最后一次喂奶记录。
                # 表名是 feeding_records（不是 feedings），且要按 start_time 而不是
                # created_at：补录一条昨晚的记录时 created_at 是「现在」，提醒永远不会响。
                last_feeding = db.execute(
                    "SELECT MAX(replace(start_time, 'T', ' ')) as last_time "
                    "FROM feeding_records WHERE baby_id = ?",
                    (baby["id"],),
                ).fetchone()

                if not (last_feeding and last_feeding["last_time"]):
                    continue
                last_time = _parse_dt(last_feeding["last_time"])
                if last_time is None:
                    continue
                elapsed = (now - last_time).total_seconds() / 60  # 分钟

                # 记成了未来的时间（手抖填错）就跳过：不然 MAX() 一直取到它，
                # 这条宝宝永远「刚喂过」，提醒彻底哑掉
                if elapsed < 0:
                    continue

                if elapsed >= interval_minutes:
                    hours = elapsed / 60
                    self._remind(
                        f"feeding_{baby['id']}", interval_minutes,
                        "喂奶提醒",
                        f"🍼 {baby['name']} 已经 {hours:.1f} 小时没有喂奶了，该喂奶啦！",
                    )
            except Exception as e:
                logger.error("[Scheduler] 宝宝 %s 喂奶提醒检查失败: %s", baby["id"], e)

    def _check_diaper_reminder(self, now: datetime):
        """检查换尿布提醒"""
        interval_hours = self.config.get("diaper_reminder_interval", 2)
        interval_minutes = int(float(interval_hours) * 60)

        try:
            db = self.db_getter()
            babies = db.execute("SELECT id, name FROM babies").fetchall()
        except Exception as e:
            logger.error("[Scheduler] 检查换尿布提醒失败: %s", e)
            return

        # 同喂奶提醒：每个宝宝单独 try，一个宝宝出错不能连累后面的
        for baby in babies:
            try:
                # 表名是 diaper_records（不是 diaper_changes），时间列是 change_time
                last_diaper = db.execute(
                    "SELECT MAX(replace(change_time, 'T', ' ')) as last_time "
                    "FROM diaper_records WHERE baby_id = ?",
                    (baby["id"],),
                ).fetchone()

                if not (last_diaper and last_diaper["last_time"]):
                    continue
                last_time = _parse_dt(last_diaper["last_time"])
                if last_time is None:
                    continue
                elapsed = (now - last_time).total_seconds() / 60
                if elapsed < 0:  # 同上：未来时间不参与提醒判定
                    continue

                if elapsed >= interval_minutes:
                    self._remind(
                        f"diaper_{baby['id']}", interval_minutes,
                        "换尿布提醒",
                        f"[尿布] {baby['name']} 已经 {elapsed/60:.1f} 小时没有换尿布了，检查一下是否需要更换！",
                    )
            except Exception as e:
                logger.error("[Scheduler] 宝宝 %s 换尿布提醒检查失败: %s", baby["id"], e)

    def _check_medication_reminder(self, now: datetime):
        """检查用药提醒"""
        try:
            db = self.db_getter()
            # 获取所有启用的用药提醒
            reminders = db.execute(
                "SELECT mr.*, b.name as baby_name FROM medication_reminders mr "
                "JOIN babies b ON mr.baby_id = b.id "
                "WHERE mr.is_active = 1"
            ).fetchall()
        except Exception as e:
            logger.error("[Scheduler] 检查用药提醒失败: %s", e)
            return

        # 同上：逐条 try，某条用药提醒出问题不影响其他
        for rem in reminders:
            try:
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
                    self._remind(
                        f"med_{rem['id']}", 1440,  # 每天只提醒一次
                        "用药提醒",
                        f"💊 {rem['baby_name']} 该吃药了！\n药品：{rem['medication_name']}\n剂量：{_row_get(rem, 'dosage', '遵医嘱')}",
                    )
            except Exception as e:
                logger.error("[Scheduler] 用药提醒 %s 检查失败: %s", _row_get(rem, "id", "?"), e)

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
                "AND v.status = 'pending' "
                "ORDER BY v.scheduled_date",
                (today, future_date),
            ).fetchall()
        except Exception as e:
            logger.error("[Scheduler] 检查疫苗提醒失败: %s", e)
            return

        # 同上：逐条 try，某条疫苗记录有问题不影响其他
        for vac in vaccines:
            try:
                scheduled = vac["scheduled_date"]
                dose = _row_get(vac, "dose_number", "")
                self._remind(
                    f"vaccine_{vac['id']}", 1440,  # 每天只提醒一次
                    "疫苗提醒",
                    f"[!] {vac['baby_name']} 该接种疫苗了!\n疫苗：{vac['vaccine_name']} 第{dose}剂\n预约日期：{scheduled}",
                )
            except Exception as e:
                logger.error("[Scheduler] 疫苗提醒 %s 检查失败: %s", _row_get(vac, "id", "?"), e)

    def _check_health_reminder(self, now: datetime):
        """检查体检 / 自定义健康提醒（health_reminders 表）。

        这张表一直有增删改查（健康档案 → 提醒 tab），但此前调度器里没有它的分支，
        所以「三天后要去做儿保」这类提醒**永远发不出来** —— 录了跟没录一样。
        疫苗提醒只覆盖 vaccine_details 里的待接种项，体检/看医生/自定义提醒走的是这张表。
        """
        days_ahead = self.config.get("health_reminder_days", 1)
        try:
            days_ahead = int(float(days_ahead))
        except (TypeError, ValueError):
            days_ahead = 1

        try:
            db = self.db_getter()
            today = now.strftime("%Y-%m-%d")
            horizon = (now + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
            reminders = db.execute(
                "SELECT hr.*, b.name as baby_name FROM health_reminders hr "
                "JOIN babies b ON hr.baby_id = b.id "
                "WHERE hr.is_completed = 0 AND hr.due_date <= ? "
                "ORDER BY hr.due_date",
                (horizon,),
            ).fetchall()
        except Exception as e:
            logger.error("[Scheduler] 检查健康提醒失败: %s", e)
            return

        type_labels = {
            "checkup": "体检",
            "vaccine": "疫苗",
            "medication": "用药",
            # 注意：这里不能写 "提醒" —— 下面标题是 f"{label}提醒"，
            # 会拼出「提醒提醒」这种叠词（已被 verify_health_reminder.py 抓到）
            "custom": "自定义",
        }

        # 逐条 try：某条提醒字段缺失/格式怪，不影响其他提醒照常发出
        for rem in reminders:
            try:
                due = _row_get(rem, "due_date", "")
                if not due:
                    continue
                rtype = _row_get(rem, "reminder_type", "custom")
                label = type_labels.get(rtype, "提醒")
                title = _row_get(rem, "title", "健康提醒")
                desc = _row_get(rem, "description", "")
                if desc:
                    desc = f"\n{desc}"
                # 已过期（due_date < 今天）措辞要跟着变，别提醒「还有 0 天」
                day_text = f"{due}（已到期）" if due < today else due
                self._remind(
                    f"health_{rem['id']}", 1440,  # 每天只提醒一次
                    f"{label}提醒",
                    f"{rem['baby_name']} 的{label}：{title}\n时间：{day_text}{desc}",
                )
            except Exception as e:
                logger.error("[Scheduler] 健康提醒 %s 检查失败: %s",
                             _row_get(rem, "id", "?"), e)

    def reload_config(self, config: Dict[str, Any]):
        """热加载配置"""
        self.config = config
        self._check_interval = config.get("reminder_check_interval", 60)
        logger.info("[Scheduler] 配置已热加载")
