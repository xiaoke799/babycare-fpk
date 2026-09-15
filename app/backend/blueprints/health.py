#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xiaoke799
"""
健康记录路由 Blueprint。
提供看诊摘要、体温、用药记录、趴睡训练、宝宝健康百科、发烧/就医/用药记录、
辅食过敏测试、纸尿裤价格对比、用药提醒、出牙追踪等健康相关功能。
"""

import datetime
import json
import sqlite3
from flask import Blueprint, request, jsonify

from utils import get_db, row_to_dict, rows_to_list, json_body

bp = Blueprint("health", __name__)


def _safe_query(db, sql, params=()):
    """查历史库可能缺表（vaccine_details / medication_records 都是后加的），
    缺表不能让整个看诊摘要 500——医生面前打不开报告才是真尴尬。"""
    try:
        return [dict(r) for r in db.execute(sql, params).fetchall()]
    except sqlite3.Error:
        return []


# ==================== API: 看诊摘要 ====================

@bp.route('/api/babies/<int:baby_id>/clinic-summary', methods=['GET'])
def get_clinic_summary(baby_id):
    """生成看诊摘要报告"""
    db = get_db()
    baby = db.execute('SELECT * FROM babies WHERE id = ?', (baby_id,)).fetchone()
    if not baby:
        return jsonify({'success': False, 'message': '宝宝不存在'}), 404

    birthday = datetime.datetime.strptime(baby['birthday'], '%Y-%m-%d').date()
    today = datetime.date.today()
    age_days = (today - birthday).days

    # 最近成长记录
    growth_history = db.execute(
        'SELECT * FROM growth_records WHERE baby_id = ? ORDER BY record_date DESC LIMIT 6',
        (baby_id,)
    ).fetchall()

    # 最近7天统计
    week_ago = (today - datetime.timedelta(days=7)).strftime('%Y-%m-%d')
    feeding_count = db.execute(
        'SELECT COUNT(*) FROM feeding_records WHERE baby_id = ? AND date(start_time) >= ?',
        (baby_id, week_ago)
    ).fetchone()[0]

    sleep_total = db.execute(
        'SELECT COALESCE(SUM(duration_minutes), 0) FROM sleep_records WHERE baby_id = ? AND date(start_time) >= ?',
        (baby_id, week_ago)
    ).fetchone()[0]

    diaper_count = db.execute(
        'SELECT COUNT(*) FROM diaper_records WHERE baby_id = ? AND date(change_time) >= ?',
        (baby_id, week_ago)
    ).fetchone()[0]

    # 里程碑
    milestones = db.execute(
        'SELECT * FROM milestones WHERE baby_id = ? ORDER BY achieved_date DESC LIMIT 5',
        (baby_id,)
    ).fetchall()

    # ---- 医生常问的四项：近期体温、用药史、过敏史、疫苗接种 ----
    # 时间列可能是 'YYYY-MM-DD HH:MM:SS' 也可能是带 T 的 datetime-local 格式，
    # 排序统一 replace 成空格，否则两类数据混着排会乱序。
    recent_temperatures = _safe_query(
        db,
        'SELECT temperature, measure_time, measure_method, is_fever, note '
        'FROM temperature_records WHERE baby_id = ? '
        'ORDER BY replace(measure_time, \'T\', \' \') DESC LIMIT 10',
        (baby_id,),
    )

    recent_medications = _safe_query(
        db,
        'SELECT medication_name, dosage, dosage_unit, measure_time, note '
        'FROM medication_records WHERE baby_id = ? '
        'ORDER BY replace(measure_time, \'T\', \' \') DESC LIMIT 10',
        (baby_id,),
    )

    # 过敏史：在效的（active）排最前，其次观察中（monitoring）
    allergies = _safe_query(
        db,
        'SELECT allergen_type, allergen_name, reaction_detail, severity_level, '
        'first_occurrence_date, status FROM allergy_history WHERE baby_id = ? '
        'ORDER BY CASE status WHEN \'active\' THEN 0 WHEN \'monitoring\' THEN 1 ELSE 2 END, id DESC '
        'LIMIT 10',
        (baby_id,),
    )

    # 疫苗：vaccine_details 字段全（含批号、不良反应），vaccines 是早期表。
    # 两个表都可能只有一边有数据，按「疫苗名 + 剂次」去重合并，details 优先。
    vaccinations = []
    seen_vaccine = set()
    for r in _safe_query(
        db,
        'SELECT vaccine_name, dose_number, scheduled_date, actual_date, status, '
        'has_reaction, reaction_detail FROM vaccine_details WHERE baby_id = ? '
        'ORDER BY COALESCE(actual_date, scheduled_date) DESC LIMIT 30',
        (baby_id,),
    ):
        key = (r.get('vaccine_name'), r.get('dose_number'))
        seen_vaccine.add(key)
        vaccinations.append({
            'vaccine_name': r.get('vaccine_name'),
            'dose_number': r.get('dose_number'),
            'scheduled_date': r.get('scheduled_date'),
            'actual_date': r.get('actual_date'),
            'status': r.get('status'),
            'has_reaction': r.get('has_reaction'),
            'reaction_detail': r.get('reaction_detail') or '',
        })
    for r in _safe_query(
        db,
        'SELECT vaccine_name, dose_number, scheduled_date, actual_date, status '
        'FROM vaccines WHERE baby_id = ? '
        'ORDER BY COALESCE(actual_date, scheduled_date) DESC LIMIT 30',
        (baby_id,),
    ):
        key = (r.get('vaccine_name'), r.get('dose_number'))
        if key in seen_vaccine:
            continue
        vaccinations.append({
            'vaccine_name': r.get('vaccine_name'),
            'dose_number': r.get('dose_number'),
            'scheduled_date': r.get('scheduled_date'),
            'actual_date': r.get('actual_date'),
            'status': r.get('status'),
            'has_reaction': 0,
            'reaction_detail': '',
        })
    vaccinations.sort(
        key=lambda x: (x.get('actual_date') or x.get('scheduled_date') or ''), reverse=True)
    vaccinations = vaccinations[:8]

    summary = {
        'baby': row_to_dict(baby),
        'age_days': age_days,
        'growth_history': rows_to_list(growth_history),
        'week_feeding_count': feeding_count,
        'week_sleep_total_minutes': sleep_total,
        'week_diaper_count': diaper_count,
        'recent_milestones': rows_to_list(milestones),
        'recent_temperatures': recent_temperatures,
        'recent_medications': recent_medications,
        'allergies': allergies,
        'vaccinations': vaccinations,
    }

    return jsonify({'success': True, 'data': summary})


# ==================== API: 体温记录 ====================

@bp.route('/api/babies/<int:baby_id>/temperatures', methods=['POST'])
def add_temperature(baby_id):
    """添加体温记录"""
    data = request.get_json()
    if not data or 'temperature' not in data:
        return jsonify({'success': False, 'message': '请填写体温'}), 400

    try:
        temp = float(data['temperature'])
    except (TypeError, ValueError):
        return jsonify({'success': False, 'message': '请填写有效的体温数值'}), 400
    if not (30.0 <= temp <= 45.0):
        return jsonify({'success': False, 'message': '温度数值不合理'}), 400

    # 自动判断发热（腋温 >= 37.3，耳温 >= 37.5，口温 >= 37.6，肛温 >= 38.0）
    method = data.get('measure_method', 'ear')
    fever_thresholds = {'armpit': 37.3, 'ear': 37.5, 'oral': 37.6, 'rectal': 38.0}
    threshold = fever_thresholds.get(method, 37.5)
    is_fever = 1 if temp >= threshold else 0

    db = get_db()
    db.execute(
        '''INSERT INTO temperature_records (baby_id, temperature, measure_time, measure_method, is_fever, note)
           VALUES (?, ?, ?, ?, ?, ?)''',
        (baby_id, temp, data.get('measure_time', datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')),
         method, is_fever, data.get('note', ''))
    )
    db.commit()

    msg = '体温记录已保存'
    if is_fever:
        msg += f' [!] 发热警报！{temp}°C 超过 {threshold}°C 阈值'

    return jsonify({'success': True, 'message': msg, 'is_fever': is_fever})


@bp.route('/api/temperatures/<int:temp_id>', methods=['DELETE'])
def delete_temperature(temp_id):
    """删除体温记录"""
    db = get_db()
    db.execute('DELETE FROM temperature_records WHERE id = ?', (temp_id,))
    db.commit()
    return jsonify({'success': True, 'message': '已删除'})


@bp.route('/api/babies/<int:baby_id>/temperatures/<int:temp_id>', methods=['GET'])
def get_temperature_record(baby_id, temp_id):
    """按 ID 获取单条体温记录（编辑回显用）"""
    db = get_db()
    row = db.execute(
        'SELECT * FROM temperature_records WHERE id = ? AND baby_id = ?',
        (temp_id, baby_id)
    ).fetchone()
    if row is None:
        return jsonify({'success': False, 'message': '记录不存在'}), 404
    return jsonify({'success': True, 'data': dict(row)})


@bp.route('/api/babies/<int:baby_id>/temperatures/<int:temp_id>', methods=['PUT'])
def update_temperature(baby_id, temp_id):
    """更新体温记录（自动重算是否发热）"""
    data = request.get_json()
    if not data or 'temperature' not in data:
        return jsonify({'success': False, 'message': '请填写体温'}), 400

    try:
        temp = float(data['temperature'])
    except (TypeError, ValueError):
        return jsonify({'success': False, 'message': '请填写有效的体温数值'}), 400
    if not (30.0 <= temp <= 45.0):
        return jsonify({'success': False, 'message': '温度数值不合理'}), 400

    method = data.get('measure_method', 'ear')
    fever_thresholds = {'armpit': 37.3, 'ear': 37.5, 'oral': 37.6, 'rectal': 38.0}
    threshold = fever_thresholds.get(method, 37.5)
    is_fever = 1 if temp >= threshold else 0

    db = get_db()
    cur = db.execute(
        '''UPDATE temperature_records
           SET temperature = ?, measure_time = ?, measure_method = ?, is_fever = ?, note = ?
           WHERE id = ? AND baby_id = ?''',
        (temp,
         data.get('measure_time', datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')),
         method, is_fever, data.get('note', ''),
         temp_id, baby_id)
    )
    if cur.rowcount == 0:
        return jsonify({'success': False, 'message': '记录不存在'}), 404
    db.commit()

    msg = '体温记录已更新'
    if is_fever:
        msg += f' [!] 发热警报！{temp}°C 超过 {threshold}°C 阈值'
    return jsonify({'success': True, 'message': msg, 'is_fever': is_fever})


@bp.route('/api/babies/<int:baby_id>/temperatures', methods=['GET'])
def get_temperature_records(baby_id):
    """获取体温记录列表"""
    db = get_db()
    rows = db.execute('SELECT * FROM temperature_records WHERE baby_id = ? ORDER BY measure_time DESC', (baby_id,)).fetchall()
    return jsonify({'success': True, 'data': rows_to_list(rows)})


# ==================== API: 用药记录 ====================

@bp.route('/api/babies/<int:baby_id>/medications', methods=['POST'])
def add_medication(baby_id):
    """添加用药记录"""
    data = request.get_json()
    if not data or not data.get('medication_name'):
        return jsonify({'success': False, 'message': '请填写药品名称'}), 400

    # 计算下次用药时间
    interval = data.get('next_dose_interval', 0)
    try:
        interval = int(interval)
    except (TypeError, ValueError):
        return jsonify({'success': False, 'message': '间隔时间必须为整数'}), 400
    if interval < 0:
        return jsonify({'success': False, 'message': '间隔时间不能为负数'}), 400

    measure_time = data.get('measure_time', datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    next_dose_time = ''
    if interval > 0:
        try:
            t = datetime.datetime.strptime(measure_time, '%Y-%m-%d %H:%M:%S')
        except ValueError:
            return jsonify({'success': False, 'message': '日期格式错误'}), 400
        next_dose_time = (t + datetime.timedelta(hours=interval)).strftime('%Y-%m-%d %H:%M:%S')

    db = get_db()
    db.execute(
        '''INSERT INTO medication_records (baby_id, medication_name, dosage, dosage_unit, measure_time, next_dose_interval, next_dose_time, note)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
        (baby_id, data['medication_name'], data.get('dosage', ''), data.get('dosage_unit', 'mg'),
         measure_time, interval, next_dose_time, data.get('note', ''))
    )
    db.commit()
    return jsonify({'success': True, 'message': '用药记录已保存', 'next_dose_time': next_dose_time})


@bp.route('/api/medications/<int:med_id>', methods=['DELETE'])
def delete_medication(med_id):
    """删除用药记录"""
    db = get_db()
    db.execute('DELETE FROM medication_records WHERE id = ?', (med_id,))
    db.commit()
    return jsonify({'success': True, 'message': '已删除'})


@bp.route('/api/babies/<int:baby_id>/medications', methods=['GET'])
def get_medication_list(baby_id):
    """获取用药记录列表"""
    db = get_db()
    rows = db.execute('SELECT * FROM medication_records WHERE baby_id = ? ORDER BY measure_time DESC', (baby_id,)).fetchall()
    return jsonify({'success': True, 'data': rows_to_list(rows)})


# ==================== API: 趴卧训练（tummytime / tummy-time 同一套实现） ====================
# 历史上这一功能有两套路由（/tummytime 与 /tummy-time），写同一张 tummy_time_records 表，
# 但两套的校验与支持的方法并不一致：旧的一套能列表和统计却没校验，新的一套能编辑却不校验时长。
# 现已合并为一份实现 —— 同一个视图函数挂两条 URL 规则，两个前缀都继续可用（兼容旧客户端
# 与浏览器里缓存的老前端）；编辑能力（GET 回显 / PUT 更新）同时挂到两个前缀上。

def _parse_tummytime_payload(data):
    """把提交数据规整成入库用的字段字典，返回 (payload, error)。"""
    start = (data.get('start_time') or '').strip()
    if not start:
        return None, '请填写开始时间'
    end = (data.get('end_time') or '').strip()

    raw = data.get('duration_minutes', data.get('duration', 0))
    try:
        duration = int(float(raw or 0))
    except (TypeError, ValueError):
        duration = 0

    if not duration and end:
        try:
            t1 = datetime.datetime.strptime(start, '%Y-%m-%d %H:%M:%S')
            t2 = datetime.datetime.strptime(end, '%Y-%m-%d %H:%M:%S')
        except ValueError:
            return None, '日期格式错误'
        duration = max(0, int((t2 - t1).total_seconds() / 60))

    return {
        'start_time': start,
        'end_time': end,
        'duration_minutes': max(0, duration),
        'milestone': data.get('milestone') or '',
        'note': data.get('note') or '',
    }, None


def _delete_tummytime_row(record_id, baby_id=None):
    """删除一条趴卧训练记录；给了 baby_id 就顺带校验归属。返回受影响行数。"""
    db = get_db()
    if baby_id is None:
        cur = db.execute('DELETE FROM tummy_time_records WHERE id = ?', (record_id,))
    else:
        cur = db.execute('DELETE FROM tummy_time_records WHERE id = ? AND baby_id = ?',
                         (record_id, baby_id))
    db.commit()
    return cur.rowcount


@bp.route('/api/babies/<int:baby_id>/tummytime', methods=['POST'])
@bp.route('/api/babies/<int:baby_id>/tummy-time', methods=['POST'])
def add_tummytime_record(baby_id):
    """新增趴卧训练记录（未填时长时按起止时间自动计算）"""
    payload, err = _parse_tummytime_payload(json_body())
    if err:
        return jsonify({'success': False, 'message': err}), 400

    db = get_db()
    db.execute(
        '''INSERT INTO tummy_time_records (baby_id, start_time, end_time, duration_minutes, milestone, note)
           VALUES (?, ?, ?, ?, ?, ?)''',
        (baby_id, payload['start_time'], payload['end_time'],
         payload['duration_minutes'], payload['milestone'], payload['note'])
    )
    db.commit()
    return jsonify({'success': True, 'message': '趴睡训练已记录'})


@bp.route('/api/babies/<int:baby_id>/tummytime', methods=['GET'])
@bp.route('/api/babies/<int:baby_id>/tummy-time', methods=['GET'])
def get_tummytime_records(baby_id):
    """趴卧训练记录列表"""
    db = get_db()
    rows = db.execute('SELECT * FROM tummy_time_records WHERE baby_id = ? ORDER BY start_time DESC',
                      (baby_id,)).fetchall()
    return jsonify({'success': True, 'data': rows_to_list(rows)})


@bp.route('/api/babies/<int:baby_id>/tummytime/stats', methods=['GET'])
def get_tummytime_stats(baby_id):
    """趴卧训练统计（近 N 天按日聚合）"""
    days = request.args.get('days', 7, type=int)
    db = get_db()
    rows = db.execute('''SELECT DATE(start_time) as date, SUM(duration_minutes) as total_duration, COUNT(*) as count
                          FROM tummy_time_records
                          WHERE baby_id = ? AND start_time >= date('now', ? || ' days')
                          GROUP BY DATE(start_time) ORDER BY date''',
                     (baby_id, f'-{days}')).fetchall()
    return jsonify({'success': True, 'data': rows_to_list(rows)})


@bp.route('/api/babies/<int:baby_id>/tummytime/<int:record_id>', methods=['GET'])
@bp.route('/api/babies/<int:baby_id>/tummy-time/<int:record_id>', methods=['GET'])
def get_tummytime_record(baby_id, record_id):
    """按 ID 获取单条趴卧训练记录（编辑回显用）"""
    db = get_db()
    row = db.execute('SELECT * FROM tummy_time_records WHERE id = ? AND baby_id = ?',
                     (record_id, baby_id)).fetchone()
    if row is None:
        return jsonify({'success': False, 'message': '记录不存在'}), 404
    return jsonify({'success': True, 'data': dict(row)})


@bp.route('/api/babies/<int:baby_id>/tummytime/<int:record_id>', methods=['PUT'])
@bp.route('/api/babies/<int:baby_id>/tummy-time/<int:record_id>', methods=['PUT'])
def update_tummytime_record(baby_id, record_id):
    """更新趴卧训练记录（未填时长时按起止时间自动计算）"""
    payload, err = _parse_tummytime_payload(json_body())
    if err:
        return jsonify({'success': False, 'message': err}), 400

    db = get_db()
    cur = db.execute(
        '''UPDATE tummy_time_records
           SET start_time = ?, end_time = ?, duration_minutes = ?, milestone = ?, note = ?
           WHERE id = ? AND baby_id = ?''',
        (payload['start_time'], payload['end_time'], payload['duration_minutes'],
         payload['milestone'], payload['note'], record_id, baby_id)
    )
    if cur.rowcount == 0:
        return jsonify({'success': False, 'message': '记录不存在'}), 404
    db.commit()
    return jsonify({'success': True, 'message': '趴睡训练已更新'})


@bp.route('/api/babies/<int:baby_id>/tummytime/<int:record_id>', methods=['DELETE'])
@bp.route('/api/babies/<int:baby_id>/tummy-time/<int:record_id>', methods=['DELETE'])
def delete_tummytime_record(baby_id, record_id):
    """删除趴卧训练记录"""
    _delete_tummytime_row(record_id, baby_id)
    return jsonify({'success': True, 'message': '已删除'})


@bp.route('/api/tummy-time/<int:record_id>', methods=['DELETE'])
def delete_tummy_time(record_id):
    """删除趴卧训练记录（旧版无 baby_id 的地址，保留兼容）"""
    _delete_tummytime_row(record_id)
    return jsonify({'success': True, 'message': '已删除'})



# 宝宝健康百科路由已迁移到 blueprints/knowledge.py


# ==================== API: 发烧/就医/用药记录 ====================

@bp.route('/api/babies/<int:baby_id>/health-records', methods=['GET'])
def get_health_records(baby_id):
    """获取健康记录（发烧/就医/用药）"""
    record_type = request.args.get('type', '')
    db = get_db()
    query = 'SELECT * FROM health_records WHERE baby_id = ?'
    params = [baby_id]
    if record_type:
        query += ' AND record_type = ?'
        params.append(record_type)
    query += ' ORDER BY record_date DESC'
    rows = db.execute(query, params).fetchall()
    return jsonify({'success': True, 'data': rows_to_list(rows)})


@bp.route('/api/babies/<int:baby_id>/health-records', methods=['POST'])
def add_health_record(baby_id):
    """新增健康记录（发烧/就医/用药）

    前端「健康档案 · 健康记录」页提交的是 temperature/symptom/diagnosis/
    medication/doctor/note，而该 URL 此前只有 GET 和 DELETE，提交直接 405。
    表里 title 是 NOT NULL，故由「记录类型 + 症状」推导出来。
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({'success': False, 'message': '无效数据'}), 400

    type_names = {'fever': '发烧', 'doctor': '就医', 'medication': '用药'}
    record_type = str(data.get('record_type') or '').strip()
    if record_type not in type_names:
        return jsonify({'success': False, 'message': '记录类型无效'}), 400

    record_date = str(data.get('record_date') or '').strip()[:10]
    try:
        datetime.datetime.strptime(record_date, '%Y-%m-%d')
    except ValueError:
        return jsonify({'success': False, 'message': '请选择有效日期'}), 400

    temperature = data.get('temperature')
    if temperature in (None, ''):
        temperature = None
    else:
        try:
            temperature = float(temperature)
        except (TypeError, ValueError):
            return jsonify({'success': False, 'message': '体温必须是数字'}), 400
        if not 30 < temperature <= 45:
            return jsonify({'success': False, 'message': '体温应在 30-45 °C 之间'}), 400

    def _txt(key, limit=500):
        v = data.get(key)
        return str(v).strip()[:limit] if v not in (None, '') else ''

    symptom = _txt('symptom', 200)
    # 标题：体检记录页与本页共用 health_records 表，给个体面且可检索的标题
    title = type_names[record_type] + (f' · {symptom[:40]}' if symptom else '')

    db = get_db()
    cursor = db.execute(
        """INSERT INTO health_records
           (baby_id, record_date, record_type, title, temperature, symptom,
            diagnosis, medication, doctor, note)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (baby_id, record_date, record_type, title, temperature, symptom,
         _txt('diagnosis'), _txt('medication', 200), _txt('doctor', 100), _txt('note', 2000)),
    )
    db.commit()
    return jsonify({'success': True, 'message': '记录已保存', 'id': cursor.lastrowid})


@bp.route('/api/babies/<int:baby_id>/health-records/<int:record_id>', methods=['DELETE'])
def delete_health_record(baby_id, record_id):
    """删除健康记录"""
    db = get_db()
    db.execute('DELETE FROM health_records WHERE id = ? AND baby_id = ?', (record_id, baby_id))
    db.commit()
    return jsonify({'success': True})


# ==================== API: 辅食过敏测试 ====================
# 使用 allergy_tests 表（含 day_number, has_reaction, reaction_detail, status 字段）

@bp.route('/api/babies/<int:baby_id>/allergy-tests', methods=['POST'])
def add_allergy_test(baby_id):
    """添加过敏测试记录"""
    data = json_body()
    # 必填校验
    if not data.get('food_name'):
        return jsonify({'success': False, 'message': '食物名称不能为空'}), 400
    db = get_db()
    db.execute(
        '''INSERT INTO allergy_tests (baby_id, food_name, test_date, day_number, has_reaction, reaction_detail, status, note)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
        (baby_id, data['food_name'], data.get('test_date', ''),
         data.get('day_number', 1), data.get('has_reaction', 0),
         data.get('reaction_detail', ''), data.get('status', 'testing'), data.get('note', ''))
    )
    db.commit()
    return jsonify({'success': True})


@bp.route('/api/babies/<int:baby_id>/allergy-tests/<int:record_id>', methods=['DELETE'])
def delete_allergy_test(baby_id, record_id):
    """删除过敏测试记录"""
    db = get_db()
    db.execute('DELETE FROM allergy_tests WHERE id = ? AND baby_id = ?', (record_id, baby_id))
    db.commit()
    return jsonify({'success': True})


@bp.route('/api/babies/<int:baby_id>/allergy-tests', methods=['GET'])
def get_allergy_tests(baby_id):
    """获取过敏测试记录列表"""
    db = get_db()
    rows = db.execute('SELECT * FROM allergy_tests WHERE baby_id = ? ORDER BY test_date DESC', (baby_id,)).fetchall()
    return jsonify({'success': True, 'data': rows_to_list(rows)})


@bp.route('/api/allergy-tests/<int:test_id>', methods=['PUT'])
def update_allergy_test(test_id):
    """更新过敏测试记录"""
    db = get_db()
    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'message': '无效数据'}), 400
    fields = []
    values = []
    for key in ('food_name', 'test_date', 'day_number', 'has_reaction', 'reaction_detail', 'status', 'note'):
        if key in data:
            fields.append(f'{key} = ?')
            values.append(data[key])
    if not fields:
        return jsonify({'success': False, 'message': '无更新字段'}), 400
    values.append(test_id)
    db.execute(f'UPDATE allergy_tests SET {", ".join(fields)} WHERE id = ?', values)
    db.commit()
    return jsonify({'success': True, 'message': '过敏测试已更新'})


# ==================== API: 纸尿裤价格对比 ====================

@bp.route('/api/diaper-prices', methods=['GET'])
def list_diaper_prices():
    """获取纸尿裤价格列表"""
    db = get_db()
    spec = request.args.get('spec', '')
    brand = request.args.get('brand', '')
    query = 'SELECT * FROM diaper_prices WHERE 1=1'
    params = []
    if spec:
        query += ' AND spec = ?'
        params.append(spec)
    if brand:
        query += ' AND brand LIKE ?'
        params.append(f'%{brand}%')
    query += ' ORDER BY unit_price ASC'
    rows = db.execute(query, params).fetchall()
    return jsonify({'success': True, 'data': [dict(r) for r in rows]})


@bp.route('/api/diaper-prices', methods=['POST'])
def add_diaper_price():
    """添加纸尿裤价格记录"""
    data = json_body()
    # 必填校验
    if not data.get('brand'):
        return jsonify({'success': False, 'message': '品牌不能为空'}), 400
    price = data.get('price', 0)
    count = data.get('count_per_pack', 0)
    unit_price = price / count if count > 0 else 0
    db = get_db()
    db.execute(
        '''INSERT INTO diaper_prices (brand, series, spec, price, count_per_pack, unit_price, source)
           VALUES (?, ?, ?, ?, ?, ?, ?)''',
        (data['brand'], data.get('series', ''), data.get('spec', ''),
         price, count, unit_price, data.get('source', ''))
    )
    db.commit()
    return jsonify({'success': True})


@bp.route('/api/diaper-prices/<int:record_id>', methods=['DELETE'])
def delete_diaper_price(record_id):
    """删除纸尿裤价格记录"""
    db = get_db()
    db.execute('DELETE FROM diaper_prices WHERE id = ?', (record_id,))
    db.commit()
    return jsonify({'success': True})


# ==================== API: 用药提醒 ====================

@bp.route('/api/babies/<int:baby_id>/medication-reminders', methods=['POST'])
def add_medication_reminder(baby_id):
    """添加用药提醒"""
    data = json_body()
    # 必填校验
    if not data.get('medication_name'):
        return jsonify({'success': False, 'message': '药品名称不能为空'}), 400
    db = get_db()
    db.execute(
        '''INSERT INTO medication_reminders (baby_id, medication_name, dosage, reminder_time, next_dose_time, frequency, note)
           VALUES (?, ?, ?, ?, ?, ?, ?)''',
        (baby_id, data['medication_name'], data.get('dosage', ''),
         data.get('reminder_time', data.get('next_dose_time', '')),
         data.get('next_dose_time', ''),
         data.get('frequency', ''), data.get('note', ''))
    )
    db.commit()
    return jsonify({'success': True})


@bp.route('/api/babies/<int:baby_id>/medication-reminders/<int:record_id>', methods=['PUT'])
def update_medication_reminder(baby_id, record_id):
    """更新用药提醒"""
    data = json_body()
    db = get_db()
    # 更新所有字段
    medication_name = data.get('medication_name')
    dosage = data.get('dosage', '')
    frequency = data.get('frequency', '')
    next_dose_time = data.get('next_dose_time')
    note = data.get('note', '')
    is_active = data.get('is_active')

    db.execute(
        '''UPDATE medication_reminders
           SET medication_name=?, dosage=?, frequency=?, next_dose_time=?, note=?, is_active=?
           WHERE id=? AND baby_id=?''',
        (medication_name, dosage, frequency, next_dose_time, note,
         is_active if is_active is not None else 1, record_id, baby_id)
    )
    db.commit()
    return jsonify({'success': True})


@bp.route('/api/babies/<int:baby_id>/medication-reminders/<int:record_id>', methods=['DELETE'])
def delete_medication_reminder(baby_id, record_id):
    """删除用药提醒"""
    db = get_db()
    db.execute('DELETE FROM medication_reminders WHERE id = ? AND baby_id = ?', (record_id, baby_id))
    db.commit()
    return jsonify({'success': True})


@bp.route('/api/babies/<int:baby_id>/medication-reminders', methods=['GET'])
def get_medication_reminders(baby_id):
    """获取用药提醒列表"""
    db = get_db()
    rows = db.execute('SELECT * FROM medication_reminders WHERE baby_id = ? ORDER BY COALESCE(next_dose_time, reminder_time)', (baby_id,)).fetchall()
    return jsonify({'success': True, 'data': rows_to_list(rows)})


@bp.route('/api/babies/<int:baby_id>/medication-reminders/<int:record_id>', methods=['GET'])
def get_medication_reminder(baby_id, record_id):
    """获取单条用药提醒"""
    db = get_db()
    row = db.execute('SELECT * FROM medication_reminders WHERE id = ? AND baby_id = ?', (record_id, baby_id)).fetchone()
    if not row:
        return jsonify({'success': False, 'message': '记录不存在'}), 404
    return jsonify({'success': True, 'data': row_to_dict(row)})


# ==================== API: 出牙追踪 ====================

@bp.route('/api/babies/<int:baby_id>/teeth', methods=['POST'])
def add_teeth_record(baby_id):
    """添加出牙记录"""
    data = json_body()
    db = get_db()
    db.execute(
        'INSERT INTO baby_teeth (baby_id, tooth_code, tooth_name, erupt_date, note) VALUES (?, ?, ?, ?, ?)',
        (baby_id, data['tooth_code'], data.get('tooth_name', ''), data['erupt_date'], data.get('note', ''))
    )
    db.commit()
    return jsonify({'success': True})


@bp.route('/api/babies/<int:baby_id>/teeth/<int:record_id>', methods=['DELETE'])
def delete_teeth_record(baby_id, record_id):
    """删除出牙记录"""
    db = get_db()
    db.execute('DELETE FROM baby_teeth WHERE id = ? AND baby_id = ?', (record_id, baby_id))
    db.commit()
    return jsonify({'success': True})


@bp.route('/api/babies/<int:baby_id>/teeth', methods=['GET'])
def get_teeth_records(baby_id):
    """获取出牙记录列表"""
    db = get_db()
    rows = db.execute('SELECT * FROM baby_teeth WHERE baby_id = ? ORDER BY erupt_date DESC', (baby_id,)).fetchall()
    return jsonify({'success': True, 'data': rows_to_list(rows)})
