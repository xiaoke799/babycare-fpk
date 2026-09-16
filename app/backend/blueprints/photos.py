#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xiaoke799
"""
照片与日记路由 Blueprint。
提供成长对比照片、普通照片上传/查看/删除、日记增删等功能。
"""

import os
import re
import uuid
import shutil
import datetime

from flask import Blueprint, request, jsonify, send_file

from utils import get_db, row_to_dict, rows_to_list, json_body
from user_context import require_admin
from constants import DATA_DIR
from logger import get_logger
from security import safe_path

logger = get_logger("photos")

# 相册分类（与前端下拉一致；空值 = 未分类）
VALID_PHOTO_CATEGORIES = {
    "monthly": "月度记录",
    "milestone": "里程碑",
    "comparison": "成长对比",
    "daily": "日常",
    "other": "其他",
}

MAX_DESCRIPTION_LEN = 200
MAX_UPLOAD_BYTES = 10 * 1024 * 1024      # 单张原图上限
MAX_IMAGE_SIDE = 1200                    # 原图长边上限
THUMBNAIL_SIDE = 300
PHOTO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# 按扩展名返回正确的 MIME：降级模式（无 Pillow）下存的就是原格式，
# 一律回 image/jpeg 会让 png/webp/gif 在浏览器里显示异常或直接下载。
_EXT_MIME = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".png": "image/png", ".webp": "image/webp", ".gif": "image/gif",
}


def _mime_of(path):
    return _EXT_MIME.get(os.path.splitext(str(path or ""))[1].lower(), "application/octet-stream")


# 图片魔数（文件头）白名单
_IMAGE_MAGIC_PREFIX = (b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"GIF87a", b"GIF89a")


def _looks_like_image(head: bytes) -> bool:
    """按文件头判断是不是图片。

    需要它是因为：无 Pillow 的降级模式**没有解码环节**兜底，只校验扩展名的话，
    把任意文件改名成 .jpg 就能塞进相册目录并被当作图片提供出去。
    """
    if not head:
        return False
    if head.startswith(_IMAGE_MAGIC_PREFIX):
        return True
    return head[:4] == b"RIFF" and head[8:12] == b"WEBP"


def _normalize_photo_date(value, fallback_today=True):
    """归一化拍摄日期为 YYYY-MM-DD；非法返回 None。

    photo_date 是相册排序与「本月新增」统计的依据，早期直接把表单值原样入库，
    填进非法字符串就会让这张照片在排序里乱飘。
    """
    text = str(value or "").strip()
    if not text:
        return datetime.date.today().strftime("%Y-%m-%d") if fallback_today else None
    if not PHOTO_DATE_RE.match(text):
        return None
    try:
        datetime.datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        return None
    return text


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
    data = json_body()
    if not data.get("photo_date") or not data.get("photo_path"):
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
    cur = db.execute("DELETE FROM growth_photos WHERE id = ?", (record_id,))
    db.commit()
    if cur.rowcount == 0:
        return jsonify({"success": False, "message": "记录不存在"}), 404
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
    data = json_body()
    if not data.get("title") or not data.get("entry_date"):
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
    cur = db.execute("DELETE FROM diary_entries WHERE id = ?", (entry_id,))
    db.commit()
    if cur.rowcount == 0:
        return jsonify({"success": False, "message": "日记不存在"}), 404
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
    data = json_body()
    if not data:
        return jsonify({"success": False, "message": "无效数据"}), 400

    db = get_db()
    cur = db.execute(
        """UPDATE diary_entries SET title=?, content=?, entry_date=?, mood=?
           WHERE id=? AND baby_id=?""",
        (data.get("title"), data.get("content", ""), data.get("entry_date"), data.get("mood"), entry_id, baby_id)
    )
    db.commit()
    if cur.rowcount == 0:
        return jsonify({"success": False, "message": "日记不存在"}), 404
    return jsonify({"success": True, "message": "已更新"})


# ==================== API: 照片记录 ====================


@bp.route("/api/babies/<int:baby_id>/photos", methods=["POST"])
def upload_photo(baby_id):
    """上传照片（统一转为 JPEG 保存并生成缩略图）"""
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
    if file.stream.tell() > MAX_UPLOAD_BYTES:
        return jsonify({"success": False, "message": "图片过大（上限 10MB）"}), 400
    file.stream.seek(0)

    # 文件头校验：确认内容确实是图片（扩展名可以随便改，文件头骗不了人）
    head = file.stream.read(16)
    file.stream.seek(0)
    if not _looks_like_image(head):
        return jsonify({"success": False, "message": "文件内容不是有效的图片"}), 400

    category = str(request.form.get("category") or "").strip()
    if category and category not in VALID_PHOTO_CATEGORIES:
        return jsonify({"success": False, "message": "分类无效"}), 400

    photo_date = _normalize_photo_date(request.form.get("photo_date"))
    if photo_date is None:
        return jsonify({"success": False, "message": "日期格式应为 YYYY-MM-DD"}), 400
    description = str(request.form.get("description") or "")[:MAX_DESCRIPTION_LEN]

    photo_dir = os.path.join(DATA_DIR, "photos", str(baby_id))
    os.makedirs(photo_dir, exist_ok=True)
    thumb_rel_path = ""

    if HAS_PIL:
        # 有 Pillow：统一转 JPEG（压缩 + 按 EXIF 转正方向 + 生成缩略图）。
        # 磁盘上一律存 .jpg，不沿用原扩展名，否则会出现「photo.gif 里装着 JPEG」的不一致。
        filename = f"{uuid.uuid4().hex}.jpg"
        file_path = os.path.join(photo_dir, filename)
        thumb_path = os.path.join(photo_dir, f"thumb_{filename}")
        try:
            img = Image.open(file.stream)
            # 手机竖拍的方向信息只写在 EXIF 里，不按它旋转的话存下来就是躺倒的
            try:
                from PIL import ImageOps
                img = ImageOps.exif_transpose(img)
            except Exception:
                pass
            img = img.convert("RGB")
            # 限制最大尺寸
            if max(img.size) > MAX_IMAGE_SIDE:
                ratio = MAX_IMAGE_SIDE / max(img.size)
                img = img.resize(
                    (int(img.width * ratio), int(img.height * ratio)), Image.LANCZOS
                )
            img.save(file_path, "JPEG", quality=85)

            # 生成缩略图
            thumb = img.copy()
            thumb.thumbnail((THUMBNAIL_SIDE, THUMBNAIL_SIDE), Image.LANCZOS)
            thumb.save(thumb_path, "JPEG", quality=75)
            thumb_rel_path = f"photos/{baby_id}/thumb_{filename}"

        except Exception as e:
            # 记录详细错误日志，但返回通用错误信息给前端（防止路径泄露）
            logger.error("图片上传失败: %s", str(e), exc_info=True)
            # 处理失败时清掉可能已落盘的半成品，别留下没有数据库记录的孤儿文件
            for p in (file_path, thumb_path):
                try:
                    if os.path.isfile(p):
                        os.remove(p)
                except OSError:
                    pass
            return jsonify(
                {"success": False, "message": "图片处理失败，请检查格式或文件大小"}
            ), 500
    else:
        # 没有 Pillow 时**降级为原样保存**，而不是直接失败。
        # 早期这里缺库就返回 500 —— 而本项目的 vendor 里并没有 Pillow，
        # 等于整个相册上传功能在生产上不可用。降级后保留原格式、不做压缩与缩略图
        # （serve_thumbnail 会自动回退到原图）。
        filename = f"{uuid.uuid4().hex}.{ext}"
        file_path = os.path.join(photo_dir, filename)
        try:
            file.stream.seek(0)
            with open(file_path, "wb") as out:
                shutil.copyfileobj(file.stream, out)
        except OSError as e:
            logger.error("保存上传文件失败: %s", e)
            return jsonify({"success": False, "message": "保存文件失败"}), 500

    # 保存到数据库
    db = get_db()
    rel_path = f"photos/{baby_id}/{filename}"
    # 该宝宝的相册还没有任何照片时，这一张自动当封面
    has_any = db.execute(
        "SELECT 1 FROM photos WHERE baby_id = ? LIMIT 1", (baby_id,)
    ).fetchone()
    cursor = db.execute(
        "INSERT INTO photos (baby_id, photo_date, description, file_path, thumbnail_path, category, is_cover) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (baby_id, photo_date, description, rel_path, thumb_rel_path,
         category, 0 if has_any else 1),
    )
    db.commit()

    photo = db.execute(
        "SELECT * FROM photos WHERE id = ?", (cursor.lastrowid,)
    ).fetchone()
    return jsonify({"success": True, "data": row_to_dict(photo)})


@bp.route("/api/babies/<int:baby_id>/photos", methods=["GET"])
def list_photos(baby_id):
    """获取照片列表（支持分类筛选与分页）。

    早期是死写 `LIMIT 100`：照片超过 100 张后，更早的照片在相册里直接"消失"，
    连"总照片"统计也永远显示 100。现在返回 total，前端据此显示真实数量并按需翻页。
    """
    db = get_db()
    category = request.args.get("category", "").strip()
    try:
        limit = int(request.args.get("limit", 60))
    except (TypeError, ValueError):
        limit = 60
    try:
        offset = int(request.args.get("offset", 0))
    except (TypeError, ValueError):
        offset = 0
    limit = max(1, min(limit, 200))
    offset = max(0, offset)

    where = "WHERE baby_id = ?"
    params = [baby_id]
    if category:
        if category not in VALID_PHOTO_CATEGORIES:
            return jsonify({"success": False, "message": "分类无效"}), 400
        where += " AND category = ?"
        params.append(category)

    total = db.execute(f"SELECT COUNT(*) FROM photos {where}", params).fetchone()[0]
    # 相册统计按「全部照片」口径（不受当前分类筛选影响），由后端算：
    # 前端只拿到分页数据，自己统计会在照片超过一页后算错。
    stats = db.execute(
        """SELECT COUNT(*) AS total, MAX(photo_date) AS latest_date,
                  SUM(CASE WHEN substr(photo_date, 1, 7) = ? THEN 1 ELSE 0 END) AS this_month
           FROM photos WHERE baby_id = ?""",
        (datetime.date.today().strftime("%Y-%m"), baby_id),
    ).fetchone()
    # 同日多张时按 id 兜底排序，保证翻页顺序稳定（否则翻页可能重复/漏图）
    rows = db.execute(
        f"SELECT * FROM photos {where} ORDER BY photo_date DESC, id DESC LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()
    return jsonify({
        "success": True,
        "data": [row_to_dict(r) for r in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": offset + len(rows) < total,
        "stats": {
            "total": stats["total"] or 0,
            "this_month": stats["this_month"] or 0,
            "latest_date": stats["latest_date"] or "",
        },
    })


@bp.route("/api/photos/<int:photo_id>", methods=["PUT"])
def update_photo(photo_id):
    """更新照片信息（部分更新：传了哪个字段就改哪个）。

    支持 description / photo_date / category / is_cover：
    - 早期只能改描述，拍错日期的照片在相册里永远排错位置、也没法归类；
    - is_cover=1 时会把同宝宝其它照片的封面标记清掉（封面唯一）。
    """
    data = json_body()
    db = get_db()
    photo = db.execute("SELECT * FROM photos WHERE id = ?", (photo_id,)).fetchone()
    if not photo:
        return jsonify({"success": False, "message": "照片不存在"}), 404

    sets, params = [], []
    if "description" in data:
        sets.append("description = ?")
        params.append(str(data.get("description") or "")[:MAX_DESCRIPTION_LEN])
    if "photo_date" in data:
        new_date = _normalize_photo_date(data.get("photo_date"), fallback_today=False)
        if new_date is None:
            return jsonify({"success": False, "message": "日期格式应为 YYYY-MM-DD"}), 400
        sets.append("photo_date = ?")
        params.append(new_date)
    if "category" in data:
        cat = str(data.get("category") or "").strip()
        if cat and cat not in VALID_PHOTO_CATEGORIES:
            return jsonify({"success": False, "message": "分类无效"}), 400
        sets.append("category = ?")
        params.append(cat)
    if "is_cover" in data:
        want_cover = bool(data.get("is_cover"))
        if want_cover:
            db.execute("UPDATE photos SET is_cover = 0 WHERE baby_id = ?", (photo["baby_id"],))
        sets.append("is_cover = ?")
        params.append(1 if want_cover else 0)

    if not sets:
        return jsonify({"success": True, "message": "没有需要更新的字段"})

    params.append(photo_id)
    db.execute(f"UPDATE photos SET {', '.join(sets)} WHERE id = ?", params)
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

    # 按实际扩展名给 MIME：无 Pillow 的降级模式下存的是原格式（png/webp/gif），
    # 一律回 image/jpeg 会让这些图在浏览器里显示异常
    return send_file(full_path, mimetype=_mime_of(full_path))


@bp.route("/api/photos/thumbnail/<int:photo_id>")
def serve_thumbnail(photo_id):
    """提供缩略图（没有缩略图时回退原图）"""
    db = get_db()
    photo = db.execute(
        "SELECT * FROM photos WHERE id = ?", (photo_id,)
    ).fetchone()
    if not photo:
        return jsonify({"success": False, "message": "照片不存在"}), 404

    full_path = None
    thumb_rel = photo["thumbnail_path"] if "thumbnail_path" in photo.keys() else ""
    if thumb_rel:
        is_safe, candidate, _err = _resolve_photo_path(thumb_rel)
        if not is_safe:
            logger.warning("缩略图路径越界拦截: %s", thumb_rel)
            return jsonify({"success": False, "message": "文件路径非法"}), 400
        if os.path.exists(candidate):
            full_path = candidate

    # 没有缩略图（无 Pillow 的降级模式）或缩略图文件缺失 → 回退原图。
    # 早期这里写成 `if not photo or not photo["thumbnail_path"]: 404`，
    # 降级模式下缩略图路径本就是空的，于是相册里每张图都是破图。
    if full_path is None:
        is_safe2, candidate2, _err2 = _resolve_photo_path(photo["file_path"])
        if not is_safe2:
            logger.warning("原图路径越界拦截: %s", photo["file_path"])
            return jsonify({"success": False, "message": "文件路径非法"}), 400
        if not os.path.exists(candidate2):
            return jsonify({"success": False, "message": "文件不存在"}), 404
        full_path = candidate2

    return send_file(full_path, mimetype=_mime_of(full_path))
