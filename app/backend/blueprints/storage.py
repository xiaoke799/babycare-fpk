#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xiaoke799
"""
存储管理 Blueprint。

提供数据存储全方位管理能力：
- 存储总览：数据库、照片、备份、日志各自占用与磁盘余量
- 数据库健康：完整性检查、VACUUM 优化、记录数统计
- 照片孤立文件清理：扫描磁盘上未被 DB 引用的照片/缩略图
- 日志管理：日志文件大小与清理
- 自动备份：基于配置的定时备份与记录
"""

import datetime
import glob
import os
import shutil
import sqlite3
import threading
import time
from flask import Blueprint, request, jsonify, current_app

from constants import DATA_DIR, DB_PATH
from utils import get_db
from user_context import require_admin

bp = Blueprint("storage", __name__)

# ---------------------------------------------------------------------------
# 自动备份调度器（进程内后台线程）
# ---------------------------------------------------------------------------
_auto_backup_thread = None
_auto_backup_lock = threading.Lock()
_auto_backup_state = {
    "enabled": False,
    "interval_hours": 24,
    "keep_count": 10,
    "last_run": None,
    "last_status": None,   # 'success' | 'error'
    "last_message": "",
    "next_run": None,
    "running": False,
}


def _format_size(size):
    """人性化文件大小"""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def _ensure_dirs():
    """确保关键目录存在并返回路径字典"""
    photos_dir = os.path.join(DATA_DIR, "photos")
    backup_dir = os.path.join(DATA_DIR, "backups")
    log_dir = os.path.join(DATA_DIR, "logs")
    for d in (photos_dir, backup_dir, log_dir):
        os.makedirs(d, exist_ok=True)
    return {"photos": photos_dir, "backups": backup_dir, "logs": log_dir}


_has_fcntl = True
try:
    import fcntl  # Linux/macOS：跨进程排他锁
except ImportError:
    _has_fcntl = False  # Windows 开发环境：通常单 worker，直接放行


def _acquire_backup_lock():
    """跨 worker 抢备份锁：同一时刻只有一个 worker 做备份。返回锁句柄或 None。"""
    if not _has_fcntl:
        # Windows / 无 fcntl：直接放行（开发环境默认单 worker）
        return open(os.path.join(DATA_DIR, ".auto_backup.lock"), "w")
    lock_path = os.path.join(DATA_DIR, ".auto_backup.lock")
    try:
        fh = open(lock_path, "w")
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fh.write(str(os.getpid()))
        return fh
    except OSError:
        # 另一 worker 已持有锁 → 本 worker 不参与备份
        return None


def _auto_backup_loop():
    """后台线程：按配置周期创建自动备份（仅持锁 worker 执行）。"""
    # 当前进程是否负责备份：首次即抢锁，抢不到就退出线程
    lock_fh = _acquire_backup_lock()
    if lock_fh is None:
        return  # 其它 worker 已在跑备份调度
    while True:
        time.sleep(60)
        cfg = _auto_backup_state
        if not cfg["enabled"] or cfg["running"]:
            continue
        now = datetime.datetime.now()
        last = cfg.get("last_run_dt")
        interval = max(1, int(cfg["interval_hours"]))
        if last is None or (now - last).total_seconds() >= interval * 3600:
            _run_auto_backup(now)


def _run_auto_backup(now):
    """执行一次自动备份（线程内）"""
    cfg = _auto_backup_state
    with _auto_backup_lock:
        cfg["running"] = True
    try:
        dirs = _ensure_dirs()
        backup_dir = dirs["backups"]
        data_dir = DATA_DIR
        os.makedirs(backup_dir, exist_ok=True)

        timestamp = now.strftime("%Y%m%d_%H%M%S")
        filename = f"auto_backup_{timestamp}.json"
        filepath = os.path.join(backup_dir, filename)

        # 收集数据（复用备份蓝图的逻辑，避免 import 循环则内联轻量版）
        from blueprints.backup import _collect_all_data
        all_data = _collect_all_data(baby_id=None)
        all_data["note"] = f"自动备份（间隔 {_auto_backup_state['interval_hours']} 小时）"
        all_data["backup_type"] = "auto"

        with open(filepath, "w", encoding="utf-8") as f:
            import json as _json
            _json.dump(all_data, f, ensure_ascii=False, indent=2)

        # 清理旧备份
        keep = max(1, int(_auto_backup_state.get("keep_count", 10)))
        from blueprints.backup import _cleanup_old_backups
        _cleanup_old_backups(backup_dir, keep)

        cfg["last_run"] = now.strftime("%Y-%m-%d %H:%M:%S")
        cfg["last_run_dt"] = now
        cfg["last_status"] = "success"
        cfg["last_message"] = f"自动备份成功：{filename}"
        cfg["next_run"] = (
            now + datetime.timedelta(hours=int(_auto_backup_state["interval_hours"]))
        ).strftime("%Y-%m-%d %H:%M:%S")
    except Exception as e:
        cfg["last_status"] = "error"
        cfg["last_message"] = f"自动备份失败: {e}"
        try:
            current_app.logger.error("自动备份失败: %s", e, exc_info=True)
        except Exception:
            pass
    finally:
        cfg["running"] = False


def _start_auto_backup_thread():
    """确保后台线程在应用启动时运行（由 server.py 调用）"""
    global _auto_backup_thread
    with _auto_backup_lock:
        if _auto_backup_thread is None or not _auto_backup_thread.is_alive():
            t = threading.Thread(target=_auto_backup_loop, daemon=True, name="auto-backup")
            t.start()
            _auto_backup_thread = t


# ---------------------------------------------------------------------------
# 存储总览
# ---------------------------------------------------------------------------

@bp.route("/api/storage/overview", methods=["GET"])
@require_admin
def storage_overview():
    """存储总览：数据库、照片、备份、日志占用与磁盘余量"""
    dirs = _ensure_dirs()
    overview = {"success": True, "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

    # 数据库
    db_size = 0
    db_wal = 0
    try:
        db_path = DB_PATH
        if os.path.isfile(db_path):
            db_size = os.path.getsize(db_path)
        wal_path = db_path + "-wal"
        if os.path.isfile(wal_path):
            db_wal = os.path.getsize(wal_path)
    except OSError:
        pass
    db_records = {}
    try:
        d = get_db()
        for t in ("babies", "feeding_records", "sleep_records", "diaper_records",
                  "growth_records", "photos", "milestones", "diary_entries", "vaccines"):
            try:
                row = d.execute(f"SELECT COUNT(*) FROM {t}").fetchone()
                db_records[t] = row[0] if row else 0
            except Exception:
                db_records[t] = 0
    except Exception:
        pass

    overview["database"] = {
        "size": db_size,
        "size_human": _format_size(db_size + db_wal),
        "wal_size": db_wal,
        "tables": db_records,
    }

    # 照片
    photo_count = 0
    photo_size = 0
    thumb_count = 0
    thumb_size = 0
    try:
        for root, _, files in os.walk(dirs["photos"]):
            for fn in files:
                fp = os.path.join(root, fn)
                try:
                    sz = os.path.getsize(fp)
                except OSError:
                    continue
                if fn.startswith("thumb_"):
                    thumb_count += 1
                    thumb_size += sz
                else:
                    photo_count += 1
                    photo_size += sz
    except OSError:
        pass
    overview["photos"] = {
        "count": photo_count,
        "size": photo_size,
        "size_human": _format_size(photo_size),
        "thumb_count": thumb_count,
        "thumb_size": thumb_size,
        "thumb_size_human": _format_size(thumb_size),
        "total_size": photo_size + thumb_size,
        "total_size_human": _format_size(photo_size + thumb_size),
    }

    # 备份
    backup_count = 0
    backup_size = 0
    last_backup = None
    try:
        pattern = os.path.join(dirs["backups"], "*.json")
        for fp in glob.glob(pattern):
            try:
                backup_size += os.path.getsize(fp)
                backup_count += 1
                mt = os.path.getmtime(fp)
                if last_backup is None or mt > last_backup:
                    last_backup = mt
            except OSError:
                pass
    except Exception:
        pass
    overview["backups"] = {
        "count": backup_count,
        "size": backup_size,
        "size_human": _format_size(backup_size),
        "last_backup": datetime.datetime.fromtimestamp(last_backup).strftime("%Y-%m-%d %H:%M:%S")
        if last_backup else None,
    }

    # 日志
    log_count = 0
    log_size = 0
    try:
        for fn in os.listdir(dirs["logs"]):
            fp = os.path.join(dirs["logs"], fn)
            if os.path.isfile(fp):
                try:
                    log_size += os.path.getsize(fp)
                    log_count += 1
                except OSError:
                    pass
    except OSError:
        pass
    overview["logs"] = {
        "count": log_count,
        "size": log_size,
        "size_human": _format_size(log_size),
        "log_dir": dirs["logs"],
    }

    # 磁盘
    try:
        disk = shutil.disk_usage(DATA_DIR)
        overview["disk"] = {
            "total": disk.total,
            "total_human": _format_size(disk.total),
            "used": disk.used,
            "used_human": _format_size(disk.used),
            "free": disk.free,
            "free_human": _format_size(disk.free),
            "percent_used": round(disk.used / disk.total * 100, 1) if disk.total else 0,
        }
    except OSError as e:
        overview["disk"] = {"error": str(e)}

    return jsonify(overview)


# ---------------------------------------------------------------------------
# 数据库健康与优化
# ---------------------------------------------------------------------------

@bp.route("/api/storage/db/health", methods=["GET"])
@require_admin
def db_health():
    """数据库健康检查：完整性 + 页数/空闲页 + 编码"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        try:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            page_count = conn.execute("PRAGMA page_count").fetchone()[0]
            freelist = conn.execute("PRAGMA freelist_count").fetchone()[0]
            encoding = conn.execute("PRAGMA encoding").fetchone()[0]
            journal = conn.execute("PRAGMA journal_mode").fetchone()[0]
        finally:
            conn.close()
        return jsonify({
            "success": True,
            "integrity": integrity,             # 'ok' 表示正常
            "integrity_ok": integrity == "ok",
            "page_count": page_count,
            "freelist_count": freelist,
            "encoding": encoding,
            "journal_mode": journal,
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@bp.route("/api/storage/db/optimize", methods=["POST"])
@require_admin
def db_optimize():
    """执行 VACUUM 回收空闲页，优化数据库大小。可以在空闲时执行。"""
    t0 = time.time()
    try:
        size_before = os.path.getsize(DB_PATH) if os.path.isfile(DB_PATH) else 0
        # VACUUM 不能在事务内执行，需要独立连接
        conn = sqlite3.connect(DB_PATH, timeout=10)
        try:
            conn.execute("VACUUM")
            conn.execute("ANALYZE")
        finally:
            conn.close()
        size_after = os.path.getsize(DB_PATH) if os.path.isfile(DB_PATH) else 0
        return jsonify({
            "success": True,
            "message": "数据库已优化",
            "size_before": size_before,
            "size_before_human": _format_size(size_before),
            "size_after": size_after,
            "size_after_human": _format_size(size_after),
            "saved": size_before - size_after,
            "saved_human": _format_size(max(0, size_before - size_after)),
            "elapsed_seconds": round(time.time() - t0, 2),
        })
    except Exception as e:
        return jsonify({"success": False, "message": f"优化失败: {e}"}), 500


# ---------------------------------------------------------------------------
# 照片孤立文件清理
# ---------------------------------------------------------------------------

@bp.route("/api/storage/photos/orphans", methods=["GET"])
@require_admin
def detect_orphan_photos():
    """扫描磁盘上的照片文件，找出未被 photos 表引用的孤立文件。"""
    dirs = _ensure_dirs()
    photos_dir = dirs["photos"]

    # 收集所有被引用的相对路径
    referenced = set()
    try:
        db = get_db()
        for row in db.execute("SELECT file_path, thumbnail_path FROM photos"):
            ref = dict(row)
            for k in ("file_path", "thumbnail_path"):
                v = ref.get(k)
                if v:
                    referenced.add(v)
                    referenced.add(os.path.basename(v))
    except Exception:
        pass

    orphans = []
    orphan_size = 0
    try:
        for root, _, files in os.walk(photos_dir):
            for fn in files:
                full = os.path.join(root, fn)
                try:
                    sz = os.path.getsize(full)
                except OSError:
                    continue
                rel = os.path.relpath(full, DATA_DIR)
                rel_slash = rel.replace(os.sep, "/")
                base = os.path.basename(full)
                if rel_slash not in referenced and base not in referenced:
                    orphans.append({"path": rel_slash, "size": sz, "size_human": _format_size(sz)})
                    orphan_size += sz
    except OSError:
        pass

    return jsonify({
        "success": True,
        "orphan_count": len(orphans),
        "orphan_size": orphan_size,
        "orphan_size_human": _format_size(orphan_size),
        "orphans": orphans[:200],  # 限制返回数量
    })


@bp.route("/api/storage/photos/cleanup", methods=["POST"])
@require_admin
def cleanup_orphan_photos():
    """清理孤立的照片文件。请求体可传 {"dry_run": true} 仅预览。"""
    data = request.get_json(silent=True) or {}
    dry_run = bool(data.get("dry_run", False))

    dirs = _ensure_dirs()
    photos_dir = dirs["photos"]

    referenced = set()
    try:
        db = get_db()
        for row in db.execute("SELECT file_path, thumbnail_path FROM photos"):
            ref = dict(row)
            for k in ("file_path", "thumbnail_path"):
                v = ref.get(k)
                if v:
                    referenced.add(v)
                    referenced.add(os.path.basename(v))
    except Exception:
        pass

    removed = []
    freed = 0
    errors = []
    try:
        for root, _, files in os.walk(photos_dir):
            for fn in files:
                full = os.path.join(root, fn)
                rel = os.path.relpath(full, DATA_DIR).replace(os.sep, "/")
                base = os.path.basename(full)
                if rel not in referenced and base not in referenced:
                    try:
                        sz = os.path.getsize(full)
                        if not dry_run:
                            os.remove(full)
                        removed.append(rel)
                        freed += sz
                    except OSError as e:
                        errors.append(f"{rel}: {e}")
    except OSError as e:
        return jsonify({"success": False, "message": str(e)}), 500

    return jsonify({
        "success": True,
        "dry_run": dry_run,
        "removed_count": len(removed),
        "freed_size": freed,
        "freed_size_human": _format_size(freed),
        "removed": removed[:50],
        "errors": errors[:20],
    })


# ---------------------------------------------------------------------------
# 日志管理
# ---------------------------------------------------------------------------

@bp.route("/api/storage/logs/files", methods=["GET"])
@require_admin
def log_files():
    """列出各日志文件及其大小"""
    dirs = _ensure_dirs()
    files = []
    try:
        for fn in sorted(os.listdir(dirs["logs"])):
            fp = os.path.join(dirs["logs"], fn)
            if os.path.isfile(fp):
                try:
                    sz = os.path.getsize(fp)
                    mt = os.path.getmtime(fp)
                except OSError:
                    continue
                files.append({
                    "name": fn,
                    "size": sz,
                    "size_human": _format_size(sz),
                    "modified_at": datetime.datetime.fromtimestamp(mt).strftime("%Y-%m-%d %H:%M:%S"),
                })
    except OSError:
        pass
    total = sum(f["size"] for f in files)
    return jsonify({
        "success": True,
        "files": files,
        "total_size": total,
        "total_size_human": _format_size(total),
    })


@bp.route("/api/storage/logs/cleanup", methods=["POST"])
@require_admin
def cleanup_logs():
    """清理日志文件。可按天数清理旧的 error_tracker，或直接清空文件日志。"""
    data = request.get_json(silent=True) or {}
    action = data.get("action", "truncate")  # truncate | clear_error_db
    errors = []
    freed = 0

    if action == "clear_error_db":
        try:
            from error_tracker import clear_old_errors
            days = int(data.get("days", 30))
            days = max(1, min(days, 365))
            count = clear_old_errors(days=days)
            return jsonify({"success": True, True: True, "message": f"已清理 {count} 条错误记录（保留 {days} 天）", "count": count})
        except Exception as e:
            return jsonify({"success": False, "message": str(e)}), 500

    # truncate: 清空文件日志（保留文件但归零）
    dirs = _ensure_dirs()
    try:
        for fn in os.listdir(dirs["logs"]):
            if not fn.endswith(".log"):
                continue
            fp = os.path.join(dirs["logs"], fn)
            try:
                sz = os.path.getsize(fp)
                with open(fp, "w", encoding="utf-8") as f:
                    f.write(f"# truncated at {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                freed += sz
            except OSError as e:
                errors.append(f"{fn}: {e}")
    except OSError as e:
        return jsonify({"success": False, "message": str(e)}), 500

    return jsonify({
        "success": True,
        "message": f"已清空日志，释放 {_format_size(freed)}",
        "freed_size": freed,
        "freed_size_human": _format_size(freed),
        "errors": errors,
    })


# ---------------------------------------------------------------------------
# 自动备份配置
# ---------------------------------------------------------------------------

@bp.route("/api/storage/auto-backup/config", methods=["GET"])
@require_admin
def get_auto_backup_config():
    """获取自动备份配置"""
    cfg = _auto_backup_state
    return jsonify({
        "success": True,
        "enabled": cfg["enabled"],
        "interval_hours": cfg["interval_hours"],
        "keep_count": cfg["keep_count"],
        "last_run": cfg["last_run"],
        "last_status": cfg["last_status"],
        "last_message": cfg["last_message"],
        "next_run": cfg["next_run"],
        "running": cfg["running"],
    })


@bp.route("/api/storage/auto-backup/config", methods=["POST"])
@require_admin
def set_auto_backup_config():
    """更新自动备份配置"""
    data = request.get_json(silent=True) or {}
    cfg = _auto_backup_state
    with _auto_backup_lock:
        if "enabled" in data:
            cfg["enabled"] = bool(data["enabled"])
        if "interval_hours" in data:
            v = int(data["interval_hours"])
            cfg["interval_hours"] = max(1, min(v, 720))  # 1 小时 ~ 30 天
        if "keep_count" in data:
            v = int(data["keep_count"])
            cfg["keep_count"] = max(1, min(v, 30))
        # 重新计算下次运行
        if cfg["enabled"] and cfg.get("last_run_dt"):
            cfg["next_run"] = (
                cfg["last_run_dt"] + datetime.timedelta(hours=cfg["interval_hours"])
            ).strftime("%Y-%m-%d %H:%M:%S")
        elif cfg["enabled"]:
            cfg["next_run"] = "下次检查时"
        else:
            cfg["next_run"] = None
    return jsonify({"success": True, "message": "配置已更新", **{
        "enabled": cfg["enabled"], "interval_hours": cfg["interval_hours"],
        "keep_count": cfg["keep_count"], "next_run": cfg["next_run"],
    }})


@bp.route("/api/storage/auto-backup/trigger", methods=["POST"])
@require_admin
def trigger_auto_backup():
    """立即触发一次自动备份"""
    cfg = _auto_backup_state
    if cfg.get("running"):
        return jsonify({"success": False, "message": "已有一个备份任务在运行中"}), 409
    # 在后台线程执行，避免阻塞请求
    t = threading.Thread(target=_run_auto_backup, args=(datetime.datetime.now(),), daemon=True)
    t.start()
    return jsonify({"success": True, "message": "自动备份任务已启动"})
