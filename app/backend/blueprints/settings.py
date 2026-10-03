#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xiaoke799
"""
设置、数据导入导出、批量记录、计时器、日志管理路由 Blueprint。
"""

import datetime
import json
import os
from flask import Blueprint, request, jsonify, current_app

from utils import get_db, row_to_dict, rows_to_list, json_body
from logger import get_logger, get_recent_logs, get_log_stats, clear_logs
from user_context import require_admin
import safety_code
# 导出表清单与备份蓝图共用一份（constants.py），避免两处各写一份走岔
from constants import EXPORT_TABLES, BABY_SCOPED_TABLES as _BABY_SCOPED_TABLES

logger = get_logger("settings")

bp = Blueprint("settings", __name__)


# ==================== API: 计时器 ====================

@bp.route('/api/babies/<int:baby_id>/timers', methods=['GET'])
def get_timers(baby_id):
    """获取计时器列表（活跃 + 最近历史）"""
    db = get_db()
    # 优先返回活跃计时器，若无则返回最近5个
    active = db.execute(
        'SELECT * FROM timers WHERE baby_id = ? AND is_active = 1 ORDER BY start_time DESC', (baby_id,)
    ).fetchall()
    if active:
        return jsonify({'success': True, 'data': rows_to_list(active)})
    recent = db.execute(
        'SELECT * FROM timers WHERE baby_id = ? ORDER BY start_time DESC LIMIT 5', (baby_id,)
    ).fetchall()
    return jsonify({'success': True, 'data': rows_to_list(recent)})


@bp.route('/api/babies/<int:baby_id>/timers', methods=['POST'])
def start_timer(baby_id):
    """启动计时器"""
    data = json_body()
    timer_type = data.get('timer_type', 'feeding')
    name = data.get('name', '')
    now = datetime.datetime.now().strftime('%Y-%m-%dT%H:%M:%S')

    db = get_db()
    cursor = db.execute(
        'INSERT INTO timers (baby_id, timer_type, start_time, name) VALUES (?, ?, ?, ?)',
        (baby_id, timer_type, now, name)
    )
    db.commit()

    timer = db.execute('SELECT * FROM timers WHERE id = ?', (cursor.lastrowid,)).fetchone()
    return jsonify({'success': True, 'data': row_to_dict(timer)})


@bp.route('/api/timers/<int:timer_id>/stop', methods=['POST'])
def stop_timer(timer_id):
    """停止计时器；可选 body.create_record=true 时按 timer_type 写入喂奶/睡眠记录。"""
    now = datetime.datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
    db = get_db()
    timer = db.execute('SELECT * FROM timers WHERE id = ?', (timer_id,)).fetchone()
    if not timer:
        return jsonify({'success': False, 'message': '计时器不存在'}), 404
    db.execute('UPDATE timers SET end_time = ?, is_active = 0 WHERE id = ?', (now, timer_id))

    created = None
    data = json_body()
    if data.get('create_record') and timer['is_active'] in (1, True, '1'):
        start = (timer['start_time'] or now)[:19].replace('T', ' ')
        end = now[:19].replace('T', ' ')
        baby_id = timer['baby_id']
        ttype = (timer['timer_type'] or '').lower()
        try:
            if ttype == 'sleep':
                try:
                    s0 = datetime.datetime.strptime(start[:19], '%Y-%m-%d %H:%M:%S')
                    e0 = datetime.datetime.strptime(end[:19], '%Y-%m-%d %H:%M:%S')
                    if e0 < s0:
                        e0 += datetime.timedelta(days=1)
                    duration = int((e0 - s0).total_seconds() // 60)
                except Exception:
                    duration = 0
                cur = db.execute(
                    '''INSERT INTO sleep_records
                       (baby_id, start_time, end_time, duration_minutes, note)
                       VALUES (?, ?, ?, ?, ?)''',
                    (baby_id, start, end, duration, f'计时器#{timer_id}'),
                )
                created = {'table': 'sleep_records', 'id': cur.lastrowid}
            elif ttype in ('feeding', 'pumping', 'solid'):
                feed_type = 'bottle' if ttype == 'feeding' else ttype
                cur = db.execute(
                    '''INSERT INTO feeding_records
                       (baby_id, start_time, end_time, feeding_type, note)
                       VALUES (?, ?, ?, ?, ?)''',
                    (baby_id, start, end, feed_type, f'计时器#{timer_id}'),
                )
                created = {'table': 'feeding_records', 'id': cur.lastrowid}
        except Exception as e:
            db.rollback()
            return jsonify({'success': False, 'message': f'计时器已停止，但写入记录失败: {e}'}), 500

    db.commit()
    return jsonify({
        'success': True,
        'message': '计时器已停止' + ('，记录已生成' if created else ''),
        'record': created,
    })


# ==================== API: 设置 ====================

@bp.route('/api/settings', methods=['GET'])
def get_settings():
    """获取应用设置"""
    db = get_db()
    rows = db.execute('SELECT key, value FROM settings').fetchall()
    settings = {}
    for row in rows:
        settings[row['key']] = row['value']
    return jsonify({'success': True, 'data': settings})


@bp.route('/api/settings', methods=['PUT'])
@require_admin
def update_settings():
    """更新应用设置（仅管理员）"""
    data = json_body()
    db = get_db()

    for key, value in data.items():
        db.execute(
            'INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, datetime("now", "localtime"))',
            (key, json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value)
        )
    db.commit()
    return jsonify({'success': True, 'message': '设置已更新'})


# ==================== API: 删除安全码 ====================

@bp.route('/api/settings/safety-code', methods=['GET'])
@require_admin
def get_safety_code_status():
    """查询是否已设置删除安全码（不返回码本身）

    与同组的 PUT/DELETE 保持一致：集成 Token（HA 等）已认证但非管理员，
    不该知道本机有没有设安全码。
    """
    db = get_db()
    return jsonify({'success': True, 'data': {'enabled': safety_code.is_set(db)}})


@bp.route('/api/settings/safety-code', methods=['PUT'])
@require_admin
def set_safety_code():
    """设置/修改删除安全码（仅管理员）"""
    data = json_body()
    code = data.get('code', '')
    ok, msg = safety_code.set_code(get_db(), code)
    if not ok:
        return jsonify({'success': False, 'message': msg}), 400
    get_db().commit()
    return jsonify({'success': True, 'message': msg, 'data': {'enabled': True}})


@bp.route('/api/settings/integration-token', methods=['GET', 'POST', 'DELETE'])
@require_admin
def integration_token():
    """智能家居集成 API Token：GET 状态 / POST 重新生成 / DELETE 吊销。

    明文 Token 只在 POST 时返回一次，之后仅能查是否已配置。
    """
    from api_token import generate_api_token, has_api_token, revoke_api_token

    if request.method == 'GET':
        return jsonify({'success': True, 'data': {'configured': has_api_token()}})
    if request.method == 'DELETE':
        revoke_api_token()
        return jsonify({'success': True, 'message': 'Token 已吊销'})
    token = generate_api_token()
    return jsonify({
        'success': True,
        'message': 'Token 已生成，请立即保存，不会再次显示',
        'token': token,
    })


@bp.route('/api/settings/safety-code', methods=['DELETE'])
@require_admin
def clear_safety_code():
    """清除删除安全码（清除后无法删除宝宝）"""
    ok, msg = safety_code.clear_code(get_db())
    if not ok:
        return jsonify({'success': False, 'message': msg}), 400
    get_db().commit()
    return jsonify({'success': True, 'message': msg, 'data': {'enabled': False}})


# ==================== API: 家庭成员（谁在记录） ====================
# 一家子共用一个 NAS 入口，回看时想知道「这条是谁记的」。
# 这里只是称呼标签，不是账号：不做密码、不做数据隔离。
# 记在记录上的是 author 文本（冗余一份称呼），成员删掉后老记录上的名字仍在，
# 避免出现「这条记录的人不见了」的空引用。

FAMILY_MEMBER_NAME_MAX = 20


def _member_rows():
    db = get_db()
    return db.execute(
        "SELECT * FROM family_members ORDER BY sort_order ASC, id ASC"
    ).fetchall()


@bp.route('/api/settings/family-members', methods=['GET'])
def list_family_members():
    """家庭成员列表（录入表单的「谁记的」下拉直接用这个）"""
    return jsonify({'success': True, 'data': rows_to_list(_member_rows())})


@bp.route('/api/settings/family-members', methods=['POST'])
@require_admin
def add_family_member():
    data = json_body()
    name = str(data.get('name', '')).strip()
    if not name:
        return jsonify({'success': False, 'message': '请输入称呼'}), 400
    if len(name) > FAMILY_MEMBER_NAME_MAX:
        return jsonify({'success': False,
                        'message': '称呼最多 %d 个字' % FAMILY_MEMBER_NAME_MAX}), 400

    db = get_db()
    dup = db.execute(
        "SELECT id FROM family_members WHERE name = ?", (name,)).fetchone()
    if dup:
        return jsonify({'success': False, 'message': '已经有这个称呼了'}), 400

    cur = db.execute(
        "INSERT INTO family_members (name, sort_order) VALUES (?, ?)",
        (name, data.get('sort_order', 0) or 0),
    )
    db.commit()
    row = db.execute(
        "SELECT * FROM family_members WHERE id = ?", (cur.lastrowid,)).fetchone()
    return jsonify({'success': True, 'data': row_to_dict(row)})


@bp.route('/api/settings/family-members/<int:member_id>', methods=['PUT'])
@require_admin
def update_family_member(member_id):
    data = json_body()
    db = get_db()
    row = db.execute(
        "SELECT * FROM family_members WHERE id = ?", (member_id,)).fetchone()
    if not row:
        return jsonify({'success': False, 'message': '成员不存在'}), 404

    name = data.get('name')
    if name is not None:
        name = str(name).strip()
        if not name or len(name) > FAMILY_MEMBER_NAME_MAX:
            return jsonify({'success': False,
                            'message': '称呼为空或超过 %d 个字' % FAMILY_MEMBER_NAME_MAX}), 400
        dup = db.execute(
            "SELECT id FROM family_members WHERE name = ? AND id != ?",
            (name, member_id)).fetchone()
        if dup:
            return jsonify({'success': False, 'message': '已经有这个称呼了'}), 400

    # 部分更新：传了才算数（别用 `data.get(k) or 原值`，那样清不掉 0/False）
    name = name if name is not None else row['name']
    is_active = data.get('is_active')
    is_active = 1 if is_active is None else (1 if is_active else 0)
    sort_order = data.get('sort_order')
    sort_order = row['sort_order'] if sort_order is None else (sort_order or 0)

    db.execute(
        "UPDATE family_members SET name = ?, is_active = ?, sort_order = ? WHERE id = ?",
        (name, is_active, sort_order, member_id),
    )
    db.commit()
    row = db.execute(
        "SELECT * FROM family_members WHERE id = ?", (member_id,)).fetchone()
    return jsonify({'success': True, 'data': row_to_dict(row)})


@bp.route('/api/settings/family-members/<int:member_id>', methods=['DELETE'])
@require_admin
def delete_family_member(member_id):
    db = get_db()
    cur = db.execute("DELETE FROM family_members WHERE id = ?", (member_id,))
    db.commit()
    if cur.rowcount == 0:
        # 删不存在的 id 必须 404 —— 否则前端以为删成功了（假成功）
        return jsonify({'success': False, 'message': '成员不存在'}), 404
    return jsonify({'success': True, 'message': '已删除'})


# ==================== API: 数据导入导出 ====================

@bp.route('/api/babies/<int:baby_id>/import', methods=['POST'])
@require_admin
def import_baby_data(baby_id):
    """导入宝宝数据（JSON，仅管理员）"""
    data = json_body()
    if not data or 'baby' not in data:
        return jsonify({'success': False, 'message': '无效的数据格式'}), 400

    db = get_db()
    baby = db.execute('SELECT * FROM babies WHERE id = ?', (baby_id,)).fetchone()
    if not baby:
        return jsonify({'success': False, 'message': '宝宝不存在'}), 404

    # 各表允许导入的列名白名单（防 SQL 注入：拒绝非法列名）
    # 注意：字段名必须与数据库表实际列名一致
    TABLE_COLUMN_WHITELIST = {
        'growth_records': {'record_date', 'height', 'weight', 'head_circumference', 'bmi', 'note'},
        'feeding_records': {'start_time', 'end_time', 'feeding_type', 'amount', 'side', 'note', 'left_duration', 'right_duration'},
        'sleep_records': {'start_time', 'end_time', 'duration_minutes', 'sleep_quality', 'is_nap', 'note'},
        'diaper_records': {'change_time', 'diaper_type', 'color', 'note'},
        'milestones': {'title', 'description', 'achieved_date', 'category', 'note', 'is_first'},
        'diary_entries': {'entry_date', 'title', 'content', 'mood'},
        'vaccines': {'vaccine_name', 'vaccine_type', 'dose_number', 'scheduled_date', 'actual_date', 'status', 'note'},
    }

    # 导入各表数据（跳过已存在的记录）
    tables = [
        ('growth_records', 'record_date'),
        ('feeding_records', 'start_time'),
        ('sleep_records', 'start_time'),
        ('diaper_records', 'change_time'),
        ('milestones', 'achieved_date'),
        ('diary_entries', 'entry_date'),
        ('vaccines', 'scheduled_date'),
    ]

    imported = {}
    import_failed = {}
    for table, _ in tables:
        rows = data.get(table, [])
        if not rows:
            continue
        # 获取该表的合法列名白名单
        allowed_cols = TABLE_COLUMN_WHITELIST.get(table, set())
        count = 0
        failed = 0
        for row in rows:
            # 移除 id 让数据库自动生成
            row.pop('id', None)
            row['baby_id'] = baby_id
            # 过滤掉白名单之外的列名（防注入）
            safe_row = {k: v for k, v in row.items() if k in allowed_cols}
            if not safe_row:
                continue
            # baby_id 是关联字段，不在业务字段白名单里，过滤时必须补回来。
            # 少了这一步，INSERT 会撞 NOT NULL 约束，异常又被下面的 except 吞掉，
            # 结果是「提示导入成功，实际一条都没进去」。
            safe_row['baby_id'] = baby_id
            # 兜底：老备份里没有 is_first 字段，按标题前缀推断，
            # 否则这些记录导入后会从「第一次」页里消失。
            if table == 'milestones' and 'is_first' not in safe_row:
                title = str(safe_row.get('title') or '')
                safe_row['is_first'] = 1 if (title.startswith('第一次') or title.startswith('首次')) else 0
            cols = ', '.join(safe_row.keys())
            placeholders = ', '.join(['?' for _ in safe_row])
            try:
                db.execute(f'INSERT INTO {table} ({cols}) VALUES ({placeholders})', list(safe_row.values()))
                count += 1
            except Exception as e:
                failed += 1
                current_app.logger.error(f'导入 {table} 记录失败: {e}')
        imported[table] = count
        if failed:
            # 失败数必须让用户看见，否则会以为全部导入成功
            import_failed[table] = failed

    db.commit()
    message = '数据导入成功' if not import_failed else \
        f'数据导入完成，但 {sum(import_failed.values())} 条记录导入失败'
    return jsonify({
        'success': True,
        'message': message,
        'imported': imported,
        'failed': import_failed
    })


# 导出表清单 EXPORT_TABLES 与 _BABY_SCOPED_TABLES 已集中到 constants.py，
# 与 blueprints/backup.py 的备份/恢复共用同一份（此前此处只有 27 张表，
# 比备份清单少 16 张，「导出全部数据」导出的是残缺数据）。

@bp.route('/api/export-all', methods=['GET'])
@require_admin
def export_all_data():
    """导出所有宝宝数据（覆盖全部业务表）"""
    db = get_db()

    # 获取当前数据库中实际存在的表名集合
    existing_tables = set(
        row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    )

    babies = db.execute('SELECT * FROM babies').fetchall() if 'babies' in existing_tables else []

    all_data = {
        'version': '1.0',
        'exported_at': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'babies': []
    }

    for baby in babies:
        baby_id = baby['id']
        baby_data = {'baby': row_to_dict(baby)}
        for table in EXPORT_TABLES:
            if table == 'babies':
                continue
            if table not in existing_tables:
                continue
            try:
                if table in _BABY_SCOPED_TABLES:
                    baby_data[table] = rows_to_list(
                        db.execute(f'SELECT * FROM {table} WHERE baby_id = ?', (baby_id,)).fetchall()
                    )
                else:
                    items = rows_to_list(db.execute(f'SELECT * FROM {table}').fetchall())
                    # 剥离凭据：app_settings 内含 llm api_key / notify webhook
                    if table == 'app_settings':
                        from utils import scrub_secrets_from_settings_rows
                        items = scrub_secrets_from_settings_rows(items)
                    baby_data[table] = items
            except Exception:
                baby_data[table] = []
        all_data['babies'].append(baby_data)

    response = jsonify({'success': True, 'data': all_data})
    response.headers['Content-Disposition'] = 'attachment; filename=babycare-export.json'
    return response


# 注意：/api/babies/<id>/export 单宝宝导出统一由 blueprints/babies.py 提供
# （此处旧实现与 babies.py 路由重复，且 babies 先注册导致本函数为死代码，已移除）。


# ==================== API: 批量记录 ====================

@bp.route('/api/babies/<int:baby_id>/batch-records', methods=['POST'])
def add_batch_records(baby_id):
    """批量添加记录"""
    data = json_body()
    if not data or 'records' not in data:
        return jsonify({'success': False, 'message': '无效的数据格式'}), 400

    records = data['records']
    db = get_db()
    added = 0
    errors = []

    for idx, record in enumerate(records):
        record_type = record.get('type')
        try:
            if record_type == 'feeding':
                db.execute(
                    '''INSERT INTO feeding_records (baby_id, start_time, end_time, feeding_type, amount, side, note)
                       VALUES (?, ?, ?, ?, ?, ?, ?)''',
                    (baby_id, record['start_time'], record.get('end_time'),
                     record.get('feeding_type', 'breast'), record.get('amount'),
                     record.get('side'), record.get('note', ''))
                )
            elif record_type == 'sleep':
                start = record['start_time']
                end = record.get('end_time', '')
                duration = record.get('duration_minutes', 0)
                if not duration and end:
                    t1 = datetime.datetime.strptime(start, '%Y-%m-%d %H:%M:%S')
                    t2 = datetime.datetime.strptime(end, '%Y-%m-%d %H:%M:%S')
                    duration = int((t2 - t1).total_seconds() / 60)
                # 判断是否小憩
                is_nap = record.get('is_nap')
                if is_nap is None and start:
                    hour = datetime.datetime.strptime(start, '%Y-%m-%d %H:%M:%S').hour
                    is_nap = 1 if 6 <= hour < 18 else 0
                db.execute(
                    '''INSERT INTO sleep_records (baby_id, start_time, end_time, duration_minutes, sleep_quality, is_nap, note)
                       VALUES (?, ?, ?, ?, ?, ?, ?)''',
                    (baby_id, start, end, duration, record.get('sleep_quality'), is_nap, record.get('note', ''))
                )
            elif record_type == 'diaper':
                db.execute(
                    '''INSERT INTO diaper_records (baby_id, change_time, diaper_type, color, note)
                       VALUES (?, ?, ?, ?, ?)''',
                    (baby_id, record['change_time'], record.get('diaper_type', 'wet'),
                     record.get('color'), record.get('note', ''))
                )
            elif record_type == 'growth':
                db.execute(
                    '''INSERT INTO growth_records (baby_id, record_date, height, weight, head_circumference, bmi, note)
                       VALUES (?, ?, ?, ?, ?, ?, ?)''',
                    (baby_id, record['record_date'], record.get('height'),
                     record.get('weight'), record.get('head_circumference'),
                     record.get('bmi'), record.get('note', ''))
                )
            added += 1
        except Exception as e:
            errors.append({'row': idx, 'message': str(e)})

    db.commit()
    return jsonify({
        'success': True,
        'message': f'成功添加 {added} 条记录',
        'count': added,
        'imported': added,
        'errors': errors
    })


# ==================== API: 日志管理 ====================

@bp.route('/api/logs', methods=['GET'])
@require_admin
def get_logs():
    """获取日志内容（用于前端/CLI 查看，可能含敏感信息，仅管理员）"""
    log_type = request.args.get('type', 'main')  # main/error/access/sql
    try:
        lines = int(request.args.get('lines', 100))
    except (TypeError, ValueError):
        lines = 100
    lines = max(1, min(lines, 1000))  # 限制在 1~1000 行

    valid_types = {'main', 'error', 'access', 'sql'}
    if log_type not in valid_types:
        return jsonify({'success': False, 'message': f'无效类型，可选: {valid_types}'}), 400

    content = get_recent_logs(lines=lines, log_type=log_type)
    return jsonify({
        'success': True,
        'type': log_type,
        'lines': lines,
        'content': content,
    })


@bp.route('/api/logs/stats', methods=['GET'])
@require_admin
def get_logs_stats():
    """获取日志文件统计信息（仅管理员）"""
    stats = get_log_stats()
    return jsonify({'success': True, 'stats': stats})


@bp.route('/api/logs/clear', methods=['POST'])
@require_admin
def clear_logs_api():
    """清空日志文件"""
    data = request.get_json(silent=True) or {}
    log_type = data.get('type', 'all')  # main/error/access/sql/all

    valid_types = {'main', 'error', 'access', 'sql', 'all'}
    if log_type not in valid_types:
        return jsonify({'success': False, 'message': f'无效类型，可选: {valid_types}'}), 400

    results = clear_logs(log_type=log_type)
    logger.info("日志已清空: %s", log_type)
    return jsonify({'success': True, 'results': results})


# ==================== API: 数据库迁移管理 ====================

@bp.route('/api/migrations', methods=['GET'])
@require_admin
def get_migration_history():
    """获取数据库迁移历史（仅管理员）"""
    from migrations import get_migration_history
    history = get_migration_history()
    return jsonify({'success': True, 'migrations': history})


@bp.route('/api/migrations/run', methods=['POST'])
@require_admin
def run_migrations_api():
    """手动触发数据库迁移（通常不需要，启动时自动执行）"""
    from migrations import run_migrations
    result = run_migrations()
    return jsonify({
        'success': len(result['errors']) == 0,
        'result': result,
    })


# ==================== API: 数据备份与恢复 ====================
# 注意：备份/恢复功能统一由 blueprints/backup.py 提供
# （/api/backup/create|list|download|delete|restore|upload-restore）。
# 此处旧的 /api/backup、/api/backup/list、/api/backup/restore、/api/backup/delete
# 已移除——它们与 backup.py 存在路由冲突，且先注册会遮蔽新接口（P0 修复）。


@bp.route('/api/settings/webhook-token', methods=['GET', 'POST', 'DELETE'])
@require_admin
def webhook_token():
    """入站 Webhook Token：状态查询 / 重新生成 / 吊销。

    与集成 Token 分开：webhook 是对外开放的网络入口，泄露面更大，
    必须能单独吊销而不影响 Home Assistant 集成。
    """
    from api_token import (generate_webhook_token, has_webhook_token,
                           revoke_webhook_token)

    if request.method == 'GET':
        return jsonify({'success': True, 'data': {'configured': has_webhook_token()}})
    if request.method == 'DELETE':
        revoke_webhook_token()
        return jsonify({'success': True, 'message': 'Webhook Token 已吊销'})
    token = generate_webhook_token()
    return jsonify({
        'success': True,
        'message': 'Token 已生成，请立即保存，不会再次显示',
        'token': token,
    })
