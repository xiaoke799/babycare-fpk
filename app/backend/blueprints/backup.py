#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xiaoke799
"""
备份管理路由 Blueprint。
提供文件级备份、自动备份、备份历史管理、备份下载和恢复功能。
"""

import datetime
import json
import os
import glob
import shutil
from flask import Blueprint, request, jsonify, send_file, current_app

from utils import get_db, json_body
from user_context import require_admin
from constants import EXPORT_TABLES, BABY_SCOPED_TABLES as _BABY_SCOPED_TABLES

bp = Blueprint("backup", __name__)

# 备份文件存储目录（相对于应用数据目录）
BACKUP_DIR_NAME = "backups"

# 默认保留的备份数量
DEFAULT_KEEP_COUNT = 10


def _get_backup_dir():
    """获取备份目录路径"""
    # 从应用配置获取数据目录
    data_dir = current_app.config.get('DATA_DIR', '')
    if not data_dir:
        # 回退到环境变量
        data_dir = os.environ.get('BABYCARE_DATA_DIR', '/tmp/babycare')
    
    backup_dir = os.path.join(data_dir, BACKUP_DIR_NAME)
    os.makedirs(backup_dir, exist_ok=True)
    return backup_dir


def _get_db_path():
    """获取数据库文件路径"""
    data_dir = current_app.config.get('DATA_DIR', '')
    if not data_dir:
        data_dir = os.environ.get('BABYCARE_DATA_DIR', '/tmp/babycare')
    return os.path.join(data_dir, 'babycare.db')


# 导出表清单 EXPORT_TABLES 与「带 baby_id 的表」_BABY_SCOPED_TABLES 均已集中到
# constants.py —— 与 settings.py 的 /api/export-all 共用同一份，避免两处清单走岔。
# 本文件继续沿用常量原名，下游使用点无需改动。

# 恢复时可写入的表名白名单（防止备份 JSON 里的 table 键被注入任意 SQL）。
# 只允许 EXPORT_TABLES 中已知的业务表，且不允许 babies 表被直接拼接恢复
# （babies 由 restore 逻辑单独处理）。
_RESTORE_ALLOWED_TABLES = {t for t in EXPORT_TABLES if t != 'babies'}

# 备份文件名合法格式，杜绝路径穿越/任意文件读写。
# 手动备份叫 backup_<时间戳>.json；自动备份（storage.py 的定时线程）叫
# auto_backup_<时间戳>.json。两者都要放行——此前只认 backup_ 前缀，导致自动备份
# 既不出现在列表里、也没法恢复/删除，而且清理逻辑也匹配不到它（越攒越多直到占满磁盘）。
import re as _re
_BACKUP_FILENAME_RE = _re.compile(r'^(?:auto_)?backup_[A-Za-z0-9_\-]+\.json$')

# 备份文件的两套前缀，集中在这里，避免各处 glob 写不一致
BACKUP_GLOBS = ('backup_*.json', 'auto_backup_*.json')


def iter_backup_files(backup_dir):
    """列出备份目录下所有备份文件（手动 + 自动），去掉重复。"""
    files = []
    for pattern in BACKUP_GLOBS:
        files.extend(glob.glob(os.path.join(backup_dir, pattern)))
    return sorted(set(files), key=os.path.getmtime, reverse=True)


def _validate_backup_filename(filename):
    """校验备份文件名，防路径穿越。
    只允许 backup_ 前缀 + 字母数字下划线连字符 + .json 后缀，
    天然拒绝 ..、/、\\、空字节等一切路径技巧。
    """
    return bool(filename) and bool(_BACKUP_FILENAME_RE.match(filename))

# 列名合法字符：字母数字下划线，杜绝 SQL 注入
_COL_NAME_RE = _re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')


def _safe_cols_and_values(row):
    """校验并返回 (cols, values)：
    - 列名必须匹配合法标识符，否则丢弃该列（防注入）
    - 返回可安全拼接的列名列表和参数值列表
    """
    cols = []
    values = []
    for k, v in row.items():
        if _COL_NAME_RE.match(k):
            cols.append(k)
            values.append(v)
    return cols, values


def _collect_all_data(baby_id=None):
    """收集所有数据（用于备份）"""
    db = get_db()
    
    # 获取实际存在的表
    existing_tables = set(
        row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    )
    
    all_data = {
        'version': '1.0',
        'exported_at': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        # 不写入数据库绝对路径（备份文件可能被分享，泄露服务器路径）
        'babies': [],
    }
    
    if baby_id:
        babies = db.execute('SELECT * FROM babies WHERE id = ?', (baby_id,)).fetchall()
    else:
        babies = db.execute('SELECT * FROM babies').fetchall() if 'babies' in existing_tables else []
    
    for baby in babies:
        baby_dict = dict(baby)
        bid = baby_dict['id']
        baby_data = {
            'baby': baby_dict,
            'records': {}
        }
        
        for table in EXPORT_TABLES:
            if table == 'babies' or table not in existing_tables:
                continue
            if table in _BABY_SCOPED_TABLES:
                rows = db.execute(f'SELECT * FROM {table} WHERE baby_id = ?', (bid,)).fetchall()
            else:
                rows = db.execute(f'SELECT * FROM {table}').fetchall()
            baby_data['records'][table] = [dict(r) for r in rows]
        
        all_data['babies'].append(baby_data)
    
    # 全局表（不包含 babies）
    all_data['global'] = {}
    for table in EXPORT_TABLES:
        if table == 'babies' or table in _BABY_SCOPED_TABLES or table not in existing_tables:
            continue
        rows = db.execute(f'SELECT * FROM {table}').fetchall()
        all_data['global'][table] = [dict(r) for r in rows]
    
    return all_data


def _cleanup_old_backups(backup_dir, keep_count):
    """清理旧备份，只保留最近的 keep_count 个（手动 + 自动备份一起算）"""
    backups = iter_backup_files(backup_dir)
    for old_backup in backups[keep_count:]:
        try:
            os.remove(old_backup)
        except OSError:
            pass


# ==================== API: 创建备份 ====================

@bp.route('/api/backup/create', methods=['POST'])
@require_admin
def create_backup():
    """创建新的备份文件"""
    data = json_body()
    baby_id = data.get('baby_id')  # None 表示全部备份
    note = data.get('note', '')
    
    backup_dir = _get_backup_dir()
    
    # 生成备份文件名
    timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    prefix = f'backup_{timestamp}'
    filename = f'{prefix}.json'
    filepath = os.path.join(backup_dir, filename)
    
    # 收集数据
    all_data = _collect_all_data(baby_id)
    all_data['note'] = note
    all_data['backup_type'] = 'manual'
    
    # 写入文件
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(all_data, f, ensure_ascii=False, indent=2)
    
    # 清理旧备份
    keep_count = data.get('keep_count', DEFAULT_KEEP_COUNT)
    # keep_count 校验：非整数或 < 1 一律回退默认值，防止传 0 清空全部历史备份
    if not isinstance(keep_count, int) or isinstance(keep_count, bool) or keep_count < 1:
        keep_count = DEFAULT_KEEP_COUNT
    _cleanup_old_backups(backup_dir, keep_count)

    file_size = os.path.getsize(filepath)

    return jsonify({
        'success': True,
        'message': '备份创建成功',
        'filename': filename,
        # 不返回服务器绝对路径（信息泄露）
        'size': file_size,
        'size_human': _format_size(file_size),
        'created_at': all_data['exported_at'],
    })


# ==================== API: 获取备份列表 ====================

@bp.route('/api/backup/list', methods=['GET'])
@require_admin
def list_backups():
    """获取备份文件列表（手动 + 自动）"""
    backup_dir = _get_backup_dir()
    
    backups = []
    for filepath in iter_backup_files(backup_dir):
        stat = os.stat(filepath)
        backups.append({
            'filename': os.path.basename(filepath),
            'size': stat.st_size,
            'size_human': _format_size(stat.st_size),
            'created_at': datetime.datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S'),
        })
    
    return jsonify({
        'success': True,
        'data': backups,
        'count': len(backups),
        # 不返回服务器备份目录绝对路径（信息泄露）
    })


# ==================== API: 下载备份文件 ====================

@bp.route('/api/backup/download/<filename>', methods=['GET'])
@require_admin
def download_backup(filename):
    """下载备份文件"""
    # 安全检查：白名单格式校验，杜绝目录遍历/任意文件读取
    if not _validate_backup_filename(filename):
        return jsonify({'success': False, 'message': '无效的文件名'}), 400
    
    backup_dir = _get_backup_dir()
    filepath = os.path.join(backup_dir, filename)
    
    if not os.path.isfile(filepath):
        return jsonify({'success': False, 'message': '备份文件不存在'}), 404
    
    return send_file(
        filepath,
        mimetype='application/json',
        as_attachment=True,
        download_name=filename,
    )


# ==================== API: 删除备份 ====================

@bp.route('/api/backup/delete/<filename>', methods=['DELETE'])
@require_admin
def delete_backup(filename):
    """删除指定备份文件"""
    # 安全检查：白名单格式校验，杜绝路径穿越
    if not _validate_backup_filename(filename):
        return jsonify({'success': False, 'message': '无效的文件名'}), 400
    
    backup_dir = _get_backup_dir()
    filepath = os.path.join(backup_dir, filename)
    
    if not os.path.isfile(filepath):
        return jsonify({'success': False, 'message': '备份文件不存在'}), 404
    
    try:
        os.remove(filepath)
        return jsonify({'success': True, 'message': '备份已删除'})
    except OSError as e:
        return jsonify({'success': False, 'message': f'删除失败: {str(e)}'}), 500


def _restore_from_data(db, backup_data, source='restore'):
    """从备份数据恢复（替换式：先清除同名宝宝旧记录再写入，避免重复恢复导致数据翻倍）。

    - babies 列名是 birthday（不是 birth_date），匹配/插入均用正确列名
    - 表名/列名双重白名单校验，防 SQL 注入
    返回 (restored, failed) 字典。
    """
    restored = {}
    failed = {}

    # 获取实际存在的表，避免对不存在的表执行 DELETE/INSERT
    existing_tables = set(
        row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    )

    def _delete_baby_records(baby_id):
        """删除指定宝宝的全部业务记录（恢复前置清理）"""
        for table in _RESTORE_ALLOWED_TABLES:
            if table in _BABY_SCOPED_TABLES and table in existing_tables:
                db.execute(f'DELETE FROM {table} WHERE baby_id = ?', (baby_id,))

    def _insert_rows(table, rows, baby_id=None):
        count = 0
        fail = 0
        for row in rows:
            row.pop('id', None)
            if baby_id is not None:
                row['baby_id'] = baby_id
            cols, values = _safe_cols_and_values(row)
            if not cols:
                fail += 1
                continue
            try:
                col_sql = ', '.join(cols)
                placeholders = ', '.join(['?' for _ in cols])
                db.execute(f'INSERT INTO {table} ({col_sql}) VALUES ({placeholders})', values)
                count += 1
            except Exception as e:
                fail += 1
                current_app.logger.error(f'{source} {table} 失败: {e}')
        restored[table] = restored.get(table, 0) + count
        if fail:
            failed[table] = failed.get(table, 0) + fail

    for baby_data in backup_data.get('babies', []):
        baby = baby_data.get('baby', {})
        records = baby_data.get('records', {})

        # 检查是否已存在同名同生日的宝宝（列名：birthday）
        existing = db.execute(
            'SELECT id FROM babies WHERE name = ? AND birthday = ?',
            (baby.get('name'), baby.get('birthday'))
        ).fetchone()

        if existing:
            baby_id = existing['id']
            # 替换式恢复：先清除该宝宝旧记录，避免重复恢复数据翻倍
            _delete_baby_records(baby_id)
        else:
            # 创建新宝宝（列名：birthday）
            cursor = db.execute(
                'INSERT INTO babies (name, birthday, gender, due_date) VALUES (?, ?, ?, ?)',
                (baby.get('name', '未命名'), baby.get('birthday', ''),
                 baby.get('gender', 'other'), baby.get('due_date'))
            )
            baby_id = cursor.lastrowid

        # 恢复各表数据
        for table, rows in records.items():
            # 表名白名单校验：拒绝备份文件中出现的任意表名（防 SQL 注入）
            if table not in _RESTORE_ALLOWED_TABLES:
                failed[table] = failed.get(table, 0) + len(rows)
                current_app.logger.warning('%s时拒绝非法表名: %s', source, table)
                continue
            if not rows or table not in existing_tables:
                continue
            _insert_rows(table, rows, baby_id=baby_id)

    # 恢复全局表（不按 baby 关联，如 diaper_prices）：先清空再写入
    for table, rows in (backup_data.get('global') or {}).items():
        if table not in _RESTORE_ALLOWED_TABLES or table in _BABY_SCOPED_TABLES:
            continue
        if not rows or table not in existing_tables:
            continue
        db.execute(f'DELETE FROM {table}')
        _insert_rows(table, rows)

    return restored, failed


# ==================== API: 恢复备份 ====================

@bp.route('/api/backup/restore', methods=['POST'])
@require_admin
def restore_backup():
    """从备份文件恢复数据"""
    data = request.get_json()
    if not data or not data.get('filename'):
        return jsonify({'success': False, 'message': '请指定备份文件'}), 400
    
    filename = data['filename']
    # 安全检查：白名单格式校验，杜绝路径穿越
    if not _validate_backup_filename(filename):
        return jsonify({'success': False, 'message': '无效的文件名'}), 400
    
    backup_dir = _get_backup_dir()
    filepath = os.path.join(backup_dir, filename)
    
    if not os.path.isfile(filepath):
        return jsonify({'success': False, 'message': '备份文件不存在'}), 404
    
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            backup_data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        return jsonify({'success': False, 'message': f'读取备份失败: {str(e)}'}), 400
    
    # 验证备份格式
    if 'babies' not in backup_data:
        return jsonify({'success': False, 'message': '无效的备份格式'}), 400
    
    db = get_db()
    restored = {}
    failed = {}
    
    try:
        restored, failed = _restore_from_data(db, backup_data, source='恢复')
        db.commit()

        total_restored = sum(restored.values())
        total_failed = sum(failed.values()) if failed else 0

        return jsonify({
            'success': True,
            'message': f'恢复完成，共恢复 {total_restored} 条记录' + (f'，{total_failed} 条失败' if total_failed else ''),
            'restored': restored,
            'failed': failed,
        })

    except Exception as e:
        db.rollback()
        return jsonify({'success': False, 'message': f'恢复失败: {str(e)}'}), 500


# ==================== API: 上传备份恢复 ====================

@bp.route('/api/backup/upload-restore', methods=['POST'])
@require_admin
def upload_restore():
    """上传备份文件并恢复"""
    if 'file' not in request.files:
        return jsonify({'success': False, 'message': '请选择备份文件'}), 400
    
    file = request.files['file']
    if not file.filename.endswith('.json'):
        return jsonify({'success': False, 'message': '请上传 JSON 格式的备份文件'}), 400
    
    try:
        backup_data = json.load(file.stream)
    except json.JSONDecodeError:
        return jsonify({'success': False, 'message': '无效的 JSON 文件'}), 400
    
    if 'babies' not in backup_data:
        return jsonify({'success': False, 'message': '无效的备份格式'}), 400
    
    db = get_db()
    restored = {}
    failed = {}
    
    try:
        restored, failed = _restore_from_data(db, backup_data, source='上传恢复')
        db.commit()

        total_restored = sum(restored.values())
        total_failed = sum(failed.values()) if failed else 0
        return jsonify({
            'success': True,
            'message': f'恢复完成，共恢复 {total_restored} 条记录' + (f'，{total_failed} 条失败' if total_failed else ''),
            'restored': restored,
            'failed': failed,
        })

    except Exception as e:
        db.rollback()
        return jsonify({'success': False, 'message': f'恢复失败: {str(e)}'}), 500


def _format_size(size):
    """格式化文件大小"""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024:
            return f'{size:.1f} {unit}'
        size /= 1024
    return f'{size:.1f} TB'
