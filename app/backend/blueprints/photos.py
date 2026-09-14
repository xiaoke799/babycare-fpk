#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xiaoke799
"""
照片与日记路由 Blueprint。
提供成长对比照片、普通照片上传/查看/删除、日记增删等功能。
"""

import os
import uuid
import datetime

from flask import Blueprint, request, jsonify, send_file

from utils import get_db, row_to_dict, rows_to_list
from user_context import require_admin
from constants import DATA_DIR
from logger import get_logger
from security import safe_path

logger = get_logger("photos")


def _resolve_photo_path(rel_path):
    """将数据库中的相对路径安全地解析到 DATA_DIR 内。

    防止 file_path / thumbnail_path 字段被污染成 ../.. 后越界读文件。
    返回 (is_safe, absolute_path)。
    """
    return safe_path(DATA_DIR, rel_path)

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    Image = None
    HAS_PIL = False

bp = Blueprint("photos", __name__)


# ==================== API: 成长对比照片 ====================


@bp.route("/api/babies/<int:baby_id>/growth-photos", methods=["POST"])
def add_growth_photo(baby_id):
    """添加成长照片记录"""
    data = request.get_json()
    if not data or not data.get("photo_date") or not data.get("photo_path"):
        return jsonify({"success": False, "message": "请填写日期和照片路径"}), 400

    # 校验 photo_path 必须是 DATA_DIR 内的合法相对路径，防止写入越界值后被
    # serve_photo 读到任意文件。
    is_safe, _full, _err = _resolve_photo_path(data["photo_path"])
    if not is_safe:
        logger.warning("成长照片路径非法，已拒绝: %s", data["photo_path"])
        return jsonify({"success": False, "message": "照片路径非法"}), 400

    db = get_db()
    db.execute(
        """INSERT INTO growth_photos (baby_id, photo_date, photo_path, age_months, caption, category)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (baby_id, data["photo_date"], data["photo_path"], data.get("age_months"),
         data.get("caption", ""), data.get("category", "monthly")),
    )
    db.commit()
    return jsonify({"success": True, "message": "照片记录已添加"})


@bp.route("/api/growth-photos/<int:record_id>", methods=["DELETE"])
def delete_growth_photo(record_id):
    """删除成长照片记录"""
    db = get_db()
    db.execute("DELETE FROM growth_photos WHERE id = ?", (record_id,))
    db.commit()
    return jsonify({"success": True, "message": "已删除"})


@bp.route("/api/babies/<int:baby_id>/growth-photos", methods=["GET"])
def get_growth_photos(baby_id):
    """获取成长照片列表"""
    db = get_db()
    rows = db.execute(
        "SELECT * FROM growth_photos WHERE baby_id = ? ORDER BY photo_date DESC",
        (baby_id,),
    ).fetchall()
    return jsonify({"success": True, "data": rows_to_list(rows)})


# ==================== API: 日记 ====================


@bp.route("/api/babies/<int:baby_id>/diary", methods=["POST"])
def add_diary_entry(baby_id):
    """添加日记"""
    data = request.get_json()
    if not data or not data.get("title") or not data.get("entry_date"):
        return jsonify({"success": False, "message": "请填写标题和日期"}), 400

    import json as _json

    photos = _json.dumps(data.get("photos", []), ensure_ascii=False)

    db = get_db()
    db.execute(
        """INSERT INTO diary_entries (baby_id, title, content, entry_date, mood, photos)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (baby_id, data["title"], data.get("content", ""),
         data["entry_date"], data.get("mood"), photos),
    )
    db.commit()
    return jsonify({"success": True, "message": "日记已添加"})


@bp.route("/api/diary/<int:entry_id>", methods=["DELETE"])
def delete_diary_entry(entry_id):
    """删除日记"""
    db = get_db()
    db.execute("DELETE FROM diary_entries WHERE id = ?", (entry_id,))
    db.commit()
    return jsonify({"success": True, "message": "已删除"})


@bp.route("/api/babies/<int:baby_id>/diary", methods=["GET"])
def get_diary_list(baby_id):
    """获取日记列表"""
    db = get_db()
    rows = db.execute(
        "SELECT * FROM diary_entries WHERE baby_id = ? ORDER BY entry_date DESC",
        (baby_id,),
    ).fetchall()
    return jsonify({"success": True, "data": rows_to_list(rows)})


@bp.route("/api/babies/<int:baby_id>/diary/<int:entry_id>", methods=["GET"])
def get_diary_entry(baby_id, entry_id):
    """获取单条日记"""
    db = get_db()
    row = db.execute(
        "SELECT * FROM diary_entries WHERE id = ? AND baby_id = ?",
        (entry_id, baby_id)
    ).fetchone()
    if not row:
        return jsonify({"success": False, "message": "日记不存在"}), 404
    return jsonify({"success": True, "data": dict(row)})


@bp.route("/api/babies/<int:baby_id>/diary/<int:entry_id>", methods=["PUT"])
def update_diary_entry(baby_id, entry_id):
    """更新日记"""
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "message": "无效数据"}), 400

    db = get_db()
    db.execute(
        """UPDATE diary_entries SET title=?, content=?, entry_date=?, mood=?
           WHERE id=? AND baby_id=?""",
        (data.get("title"), data.get("content", ""), data.get("entry_date"), data.get("mood"), entry_id, baby_id)
    )
    db.commit()
    return jsonify({"success": True, "message": "已更新"})


# ==================== API: 照片记录 ====================


@bp.route("/api/babies/<int:baby_id>/photos", methods=["POST"])
def upload_photo(baby_id):
    """上传照片"""
    if "photo" not in request.files:
        return jsonify({"success": False, "message": "请选择照片"}), 400

    file = request.files["photo"]
    if not file.filename:
        return jsonify({"success": False, "message": "文件名为空"}), 400

    # 生成唯一文件名
    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else "jpg"
    if ext not in ("jpg", "jpeg", "png", "webp", "gif"):
        return jsonify({"success": False, "message": "仅支持 jpg/png/webp/gif 格式"}), 400

    # 显式限制单张图片大小（10MB）
    file.stream.seek(0, os.SEEK_END)
    if file.stream.tell() > 10 * 1024 * 1024:
        return jsonify({"success": False, "message": "图片过大"}), 400
    file.stream.seek(0)

    filename = f"{uuid.uuid4().hex}.{ext}"
    photo_dir = os.path.join(DATA_DIR, "photos", str(baby_id))
    os.makedirs(photo_dir, exist_ok=True)
    file_path = os.path.join(photo_dir, filename)

    # 保存原图并生成缩略图（需要 PIL；缺失时拒绝保存原始上传文件）
    try:
        if not HAS_PIL:
            return jsonify({"success": False, "message": "服务器图像处理不可用"}), 500
        img = Image.open(file.stream)
        img = img.convert("RGB")
        # 限制最大尺寸
        max_size = 1200
        if max(img.size) > max_size:
            ratio = max_size / max(img.size)
            img = img.resize(
                (int(img.width * ratio), int(img.height * ratio)), Image.LANCZOS
            )
        img.save(file_path, "JPEG", quality=85)

        # 生成缩略图
        thumb = img.copy()
        thumb.thumbnail((300, 300), Image.LANCZOS)
        thumb_path = os.path.join(photo_dir, f"thumb_{filename}")
        thumb.save(thumb_path, "JPEG", quality=75)

    except Exception as e:
        # 记录详细错误日志，但返回通用错误信息给前端（防止路径泄露）
        logger.error("图片上传失败: %s", str(e), exc_info=True)
        return jsonify(
            {"success": False, "message": "图片处理失败，请检查格式或文件大小"}
        ), 500

    # 保存到数据库
    photo_date = request.form.get(
        "photo_date", datetime.date.today().strftime("%Y-%m-%d")
    )
    description = request.form.get("description", "")

    db = get_db()
    rel_path = f"photos/{baby_id}/{filename}"
    thumb_rel_path = f"photos/{baby_id}/thumb_{filename}"
    cursor = db.execute(
        "INSERT INTO photos (baby_id, photo_date, description, file_path, thumbnail_path) VALUES (?, ?, ?, ?, ?)",
        (baby_id, photo_date, description, rel_path, thumb_rel_path),
    )
    db.commit()

    photo = db.execute(
        "SELECT * FROM photos WHERE id = ?", (cursor.lastrowid,)
    ).fetchone()
    return jsonify({"success": True, "data": row_to_dict(photo)})


@bp.route("/api/babies/<int:baby_id>/photos", methods=["GET"])
def list_photos(baby_id):
    """获取照片列表"""
    db = get_db()
    rows = db.execute(
        "SELECT * FROM photos WHERE baby_id = ? ORDER BY photo_date DESC LIMIT 100",
        (baby_id,)
    ).fetchall()
    return jsonify({"success": True, "data": [row_to_dict(r) for r in rows]})


@bp.route("/api/photos/<int:photo_id>", methods=["PUT"])
def update_photo(photo_id):
    """更新照片信息"""
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"success": False, "message": "无效数据"}), 400
    db = get_db()
    db.execute(
        "UPDATE photos SET description = ? WHERE id = ?",
        (data.get("description", ""), photo_id),
    )
    db.commit()
    return jsonify({"success": True, "message": "已更新"})


@bp.route("/api/photos/<int:photo_id>", methods=["DELETE"])
@require_admin
def delete_photo(photo_id):
    """删除照片（同时清理磁盘上的原图与缩略图，避免残留）"""
    db = get_db()
    photo = db.execute("SELECT * FROM photos WHERE id = ?", (photo_id,)).fetchone()
    if not photo:
        return jsonify({"success": False, "message": "照片不存在"}), 404

    # 删除磁盘文件（路径经 safe_path 校验，防止越界删除）
    removed_files = []
    for path_field in ("file_path", "thumbnail_path"):
        rel_path = photo[path_field] if path_field in photo.keys() else None
        if not rel_path:
            continue
        is_safe, full_path, _err = _resolve_photo_path(rel_path)
        if is_safe and os.path.isfile(full_path):
            try:
                os.remove(full_path)
                removed_files.append(rel_path)
            except OSError as e:
                logger.warning("删除照片文件失败 %s: %s", rel_path, e)

    db.execute("DELETE FROM photos WHERE id = ?", (photo_id,))
    db.commit()
    if removed_files:
        logger.info("照片 %d 已删除，清理文件: %s", photo_id, removed_files)
    return jsonify({"success": True, "message": "照片已删除"})


# ==================== API: 照片文件服务 ====================


@bp.route("/api/photos/file/<int:photo_id>")
def serve_photo(photo_id):
    """提供照片文件"""
    db = get_db()
    photo = db.execute(
        "SELECT * FROM photos WHERE id = ?", (photo_id,)
    ).fetchone()
    if not photo:
        return jsonify({"success": False, "message": "照片不存在"}), 404

    is_safe, full_path, err = _resolve_photo_path(photo["file_path"])
    if not is_safe:
        logger.warning("照片路径越界拦截: %s", photo["file_path"])
        return jsonify({"success": False, "message": "文件路径非法"}), 400
    if not os.path.exists(full_path):
        return jsonify({"success": False, "message": "文件不存在"}), 404

    return send_file(full_path, mimetype="image/jpeg")


@bp.route("/api/photos/thumbnail/<int:photo_id>")
def serve_thumbnail(photo_id):
    """提供缩略图"""
    db = get_db()
    photo = db.execute(
        "SELECT * FROM photos WHERE id = ?", (photo_id,)
    ).fetchone()
    if not photo or not photo["thumbnail_path"]:
        return jsonify({"success": False, "message": "照片不存在"}), 404

    is_safe, full_path, err = _resolve_photo_path(photo["thumbnail_path"])
    if not is_safe:
        logger.warning("缩略图路径越界拦截: %s", photo["thumbnail_path"])
        return jsonify({"success": False, "message": "文件路径非法"}), 400
    if not os.path.exists(full_path):
        # 回退到原图
        is_safe2, full_path, err2 = _resolve_photo_path(photo["file_path"])
        if not is_safe2:
            logger.warning("原图路径越界拦截: %s", photo["file_path"])
            return jsonify({"success": False, "message": "文件路径非法"}), 400
        if not os.path.exists(full_path):
            return jsonify({"success": False, "message": "文件不存在"}), 404

    return send_file(full_path, mimetype="image/jpeg")
