#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xiaoke799
"""
健康档案综合路由 Blueprint。
提供体检记录、生长曲线、指标追踪、健康提醒、发育里程碑、
视力听力筛查、过敏史、喂养摘要、疫苗计划等健康管理功能。

增强功能：
- 完整的错误处理和数据验证
- WHO 生长标准百分位计算
- 疫苗计划管理和自动提醒
- 健康时间线统一视图
- 生长速度分析和异常检测
"""

import datetime
import json
import math
from flask import Blueprint, request, jsonify

from utils import get_db, row_to_dict, rows_to_list
import growth_utils

bp = Blueprint("health_records", __name__)


# ==================== 数据验证工具 ====================

def _validate_date(date_str, field_name="日期"):
    """验证日期格式"""
    if not date_str:
        return None, f"{field_name}不能为空"
    try:
        datetime.datetime.strptime(str(date_str)[:10], "%Y-%m-%d")
        return str(date_str)[:10], None
    except (ValueError, TypeError):
        return None, f"{field_name}格式无效，应为 YYYY-MM-DD"


def _validate_number(value, min_val=None, max_val=None, field_name="数值"):
    """验证数值范围"""
    if value is None or value == '':
        return None, None
    try:
        num = float(value)
        if min_val is not None and num < min_val:
            return None, f"{field_name}不能小于 {min_val}"
        if max_val is not None and num > max_val:
            return None, f"{field_name}不能大于 {max_val}"
        return num, None
    except (ValueError, TypeError):
        return None, f"{field_name}格式无效"


def _safe_execute(db, query, params=()):
    """安全执行 SQL，返回 (success, result_or_error)"""
    try:
        result = db.execute(query, params)
        return True, result
    except Exception as e:
        return False, str(e)


# ==================== WHO 生长标准 ====================

# WHO 参考值统一取自 who_data.py 装载进库的 who_*_percentiles 表
# （与 analytics.py / ai_tools.py 同口径），这里只保留"指标 → 表名"的映射。
#
# 历史坑：此前这里有一套硬编码的 WHO_GROWTH_BOYS / WHO_GROWTH_GIRLS 简化表，但内容
# 是坏的——p3 列被写成了和 p50 一样（如身高出生 p3 记成 49.9，实际应为 46.3），
# 于是 `value <= p3` 直接吃掉了「低于中位数」的全部区间，**任何低于中位数的宝宝
# 都会被报成第 3 百分位**；而且 0 月龄身高那行键写成了 {50:...,50:...,97:...}，
# 取 .get(3) 恒为 None，导致 0 月龄身高百分位永远算不出来。
WHO_METRIC_TABLE = {
    'height': 'who_height_percentiles',
    'weight': 'who_weight_percentiles',
    'bmi': 'who_bmi_percentiles',
    'head_circumference': 'who_head_percentiles',
}

# 参考点 → 百分位数值，用于在相邻锚点之间线性插值
_WHO_ANCHORS = (('p3', 3.0), ('p15', 15.0), ('p50', 50.0), ('p85', 85.0), ('p97', 97.0))


def _calc_percentile(value, age_months, metric='height', gender='boy'):
    """按 WHO 参考表估算百分位（0-100 的数值，前端直接展示成"N 分位"）。

    做法与 analytics._get_who_percentile 保持一致：
      1. 从 who_*_percentiles 取该性别的参考点；
      2. growth_utils.interpolate_reference 按日龄线性插值出 p3/p15/p50/p85/p97；
      3. 在这 5 个锚点之间线性插值，得到数值百分位。

    说明：性别只认 boy/girl，其余（如未填写的 'other'）沿用旧行为按女孩曲线估算。
    """
    if value is None or age_months is None:
        return None
    table = WHO_METRIC_TABLE.get(metric)
    if not table:
        return None

    sex = 'boy' if gender == 'boy' else 'girl'
    # 参考表以"天"为键，这里用平均月长换算，避免 //30 的累计误差
    age_in_days = float(age_months) * 30.4375

    db = get_db()
    rows = db.execute(
        f'SELECT age_in_days, p3, p15, p50, p85, p97 FROM {table} '
        'WHERE sex = ? ORDER BY age_in_days',
        (sex,)
    ).fetchall()
    ref = growth_utils.interpolate_reference(rows, age_in_days)
    if not ref:
        return None

    anchors = [(ref.get(k), pct) for k, pct in _WHO_ANCHORS if ref.get(k) is not None]
    if len(anchors) < 2:
        return None

    v = float(value)
    # 低于 P3 / 高于 P97 一律钳到端点，不做不可靠外推
    if v <= anchors[0][0]:
        return anchors[0][1]
    if v >= anchors[-1][0]:
        return anchors[-1][1]

    for (v0, p0), (v1, p1) in zip(anchors, anchors[1:]):
        if v0 <= v <= v1:
            if v1 == v0:
                return round(p0, 1)
            return round(p0 + (v - v0) / (v1 - v0) * (p1 - p0), 1)
    return None


# ==================== 综合概览 ====================

@bp.route('/api/health/summary/<int:baby_id>', methods=['GET'])
def health_summary(baby_id):
    """获取健康模块综合概览数据"""
    try:
        db = get_db()

        # 体检统计
        checkup_count = db.execute(
            'SELECT COUNT(*) FROM health_records WHERE baby_id = ?', (baby_id,)
        ).fetchone()[0]

        latest_record = db.execute(
            'SELECT * FROM health_records WHERE baby_id = ? ORDER BY record_date DESC LIMIT 1',
            (baby_id,)
        ).fetchone()

        # 待办提醒
        pending_reminders = db.execute(
            'SELECT COUNT(*) FROM health_reminders WHERE baby_id = ? AND is_completed = 0',
            (baby_id,)
        ).fetchone()[0]

        # 疫苗统计
        vaccine_total = db.execute(
            'SELECT COUNT(*) FROM vaccine_details WHERE baby_id = ?', (baby_id,)
        ).fetchone()[0]
        vaccine_completed = db.execute(
            "SELECT COUNT(*) FROM vaccine_details WHERE baby_id = ? AND status = 'completed'",
            (baby_id,)
        ).fetchone()[0]
        vaccine_pending = db.execute(
            "SELECT COUNT(*) FROM vaccine_details WHERE baby_id = ? AND status = 'pending'",
            (baby_id,)
        ).fetchone()[0]

        # 里程碑统计
        milestone_total = db.execute(
            'SELECT COUNT(*) FROM milestone_details WHERE baby_id = ?', (baby_id,)
        ).fetchone()[0]
        milestone_achieved = db.execute(
            "SELECT COUNT(*) FROM milestone_details WHERE baby_id = ? AND status = 'achieved'",
            (baby_id,)
        ).fetchone()[0]

        # 筛查统计
        screening_count = db.execute(
            'SELECT COUNT(*) FROM screenings WHERE baby_id = ?', (baby_id,)
        ).fetchone()[0]
        screening_abnormal = db.execute(
            "SELECT COUNT(*) FROM screenings WHERE baby_id = ? AND result_status IN ('abnormal', 'borderline')",
            (baby_id,)
        ).fetchone()[0]

        # 过敏统计
        allergy_count = db.execute(
            "SELECT COUNT(*) FROM allergy_history WHERE baby_id = ? AND status = 'active'",
            (baby_id,)
        ).fetchone()[0]

        # ASQ 筛查
        asq_count = db.execute(
            'SELECT COUNT(*) FROM asq_screenings WHERE baby_id = ?', (baby_id,)
        ).fetchone()[0]
        latest_asq = db.execute(
            'SELECT * FROM asq_screenings WHERE baby_id = ? ORDER BY screening_date DESC LIMIT 1',
            (baby_id,)
        ).fetchone()

        # 喂养摘要月份数
        feeding_months = db.execute(
            'SELECT COUNT(*) FROM feeding_summary WHERE baby_id = ?', (baby_id,)
        ).fetchone()[0]

        # 最近疫苗
        latest_vaccine = db.execute(
            "SELECT * FROM vaccine_details WHERE baby_id = ? AND status = 'completed' ORDER BY actual_date DESC LIMIT 1",
            (baby_id,)
        ).fetchone()

        # 即将到期疫苗（30天内）
        today = datetime.date.today()
        upcoming_date = (today + datetime.timedelta(days=30)).strftime('%Y-%m-%d')
        upcoming_vaccines = db.execute(
            """SELECT * FROM vaccine_details 
               WHERE baby_id = ? AND status = 'pending' 
               AND scheduled_date <= ? AND scheduled_date >= ?
               ORDER BY scheduled_date ASC LIMIT 5""",
            (baby_id, upcoming_date, today.strftime('%Y-%m-%d'))
        ).fetchall()

        # 获取宝宝性别用于百分位计算
        baby = db.execute('SELECT gender FROM babies WHERE id = ?', (baby_id,)).fetchone()
        gender = baby['gender'] if baby else 'boy'

        # 计算最新生长百分位
        # 注意：连接设了 row_factory = sqlite3.Row，Row 没有 .get()，
        # 这里必须先转 dict 再判空，否则只要存在体检记录就会 AttributeError → 500。
        latest = dict(latest_record) if latest_record else {}
        latest_height = latest.get('height')
        latest_weight = latest.get('weight')
        height_percentile = None
        weight_percentile = None
        age_m = latest.get('age_months')
        if latest and age_m:
            if latest_height:
                height_percentile = round(_calc_percentile(latest_height, age_m, 'height', gender), 1)
            if latest_weight:
                weight_percentile = round(_calc_percentile(latest_weight, age_m, 'weight', gender), 1)

        return jsonify({
            'success': True,
            'summary': {
                'checkup_count': checkup_count,
                'latest_height': latest_height,
                'latest_weight': latest_weight,
                'height_percentile': height_percentile,
                'weight_percentile': weight_percentile,
                'latest_record_date': latest_record['record_date'] if latest_record else None,
                'pending_reminders': pending_reminders,
                'vaccine_total': vaccine_total,
                'vaccine_completed': vaccine_completed,
                'vaccine_pending': vaccine_pending,
                'milestone_total': milestone_total,
                'milestone_achieved': milestone_achieved,
                'screening_count': screening_count,
                'screening_abnormal': screening_abnormal,
                'allergy_count': allergy_count,
                'asq_count': asq_count,
                'latest_asq_date': latest_asq['screening_date'] if latest_asq else None,
                'latest_asq_result': latest_asq['result'] if latest_asq else None,
                'feeding_months': feeding_months,
                'latest_vaccine_date': latest_vaccine['actual_date'] if latest_vaccine else None,
                'latest_vaccine_name': latest_vaccine['vaccine_name'] if latest_vaccine else None,
                'upcoming_vaccines': rows_to_list(upcoming_vaccines),
            }
        })
    except Exception as e:
        import logging
        logging.getLogger(__name__).error("health_summary error: %s", e, exc_info=True)
        return jsonify({'success': False, 'message': '数据加载失败'}), 500


# ==================== 体检记录 ====================

@bp.route('/api/health/records/<int:baby_id>', methods=['GET'])
def health_records_list(baby_id):
    """获取体检记录列表"""
    try:
        db = get_db()
        record_type = request.args.get('type', '')
        query = 'SELECT * FROM health_records WHERE baby_id = ?'
        params = [baby_id]
        if record_type:
            query += ' AND record_type = ?'
            params.append(record_type)
        query += ' ORDER BY record_date DESC'
        rows = db.execute(query, params).fetchall()
        records = rows_to_list(rows)

        # 为每条记录附加 indicators
        for r in records:
            indicators = db.execute(
                'SELECT * FROM health_indicators WHERE record_id = ? ORDER BY indicator_name',
                (r['id'],)
            ).fetchall()
            r['indicators'] = rows_to_list(indicators)

        return jsonify({'success': True, 'data': records, 'records': records})
    except Exception as e:
        import logging
        logging.getLogger(__name__).error("health_records_list error: %s", e)
        return jsonify({'success': False, 'message': '数据加载失败'}), 500


@bp.route('/api/health/records/<int:baby_id>', methods=['POST'])
def health_record_create(baby_id):
    """创建体检记录"""
    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'message': '无效数据'}), 400
    if not data.get('record_date') or not data.get('title'):
        return jsonify({'success': False, 'message': '请填写日期和标题'}), 400

    db = get_db()
    cursor = db.execute(
        """INSERT INTO health_records
           (baby_id, record_date, record_type, title, hospital, doctor,
            height, weight, head_circumference, bmi,
            heart_result, lung_result, abdomen_result, skin_result,
            bone_result, hearing_result, vision_result, blood_result,
            urine_result, other_exam, diagnosis, advice, next_visit_date, note)
           VALUES (?, ?, ?, ?, ?, ?,  -- baby_id, record_date, record_type, title, hospital, doctor
                   ?, ?, ?, ?,        -- height, weight, head_circumference, bmi
                   ?, ?, ?, ?,        -- heart_result, lung_result, abdomen_result, skin_result
                   ?, ?, ?, ?,        -- bone_result, hearing_result, vision_result, blood_result
                   ?, ?, ?, ?,        -- urine_result, other_exam, diagnosis, advice
                   ?, ?)              -- next_visit_date, note""",
        (baby_id, data['record_date'], data.get('record_type', 'routine'),
         data['title'], data.get('hospital', ''), data.get('doctor', ''),
         data.get('height'), data.get('weight'), data.get('head_circumference'),
         data.get('bmi'), data.get('heart_result', ''), data.get('lung_result', ''),
         data.get('abdomen_result', ''), data.get('skin_result', ''),
         data.get('bone_result', ''), data.get('hearing_result', ''),
         data.get('vision_result', ''), data.get('blood_result', ''),
         data.get('urine_result', ''), data.get('other_exam', ''),
         data.get('diagnosis', ''), data.get('advice', ''),
         data.get('next_visit_date'), data.get('note', ''))
    )
    record_id = cursor.lastrowid

    # 处理关联指标
    indicators = data.get('indicators', [])
    for ind in indicators:
        if ind.get('indicator_name'):
            db.execute(
                """INSERT INTO health_indicators
                   (record_id, baby_id, indicator_name, indicator_code, value, unit,
                    reference_low, reference_high, reference_text, result_status, record_date, note)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (record_id, baby_id, ind['indicator_name'], ind.get('indicator_code', ''),
                 ind.get('value', ''), ind.get('unit', ''), ind.get('reference_low'),
                 ind.get('reference_high'), ind.get('reference_text', ''),
                 ind.get('result_status', 'normal'), data['record_date'], ind.get('note', ''))
            )

    db.commit()
    return jsonify({'success': True, 'message': '记录已保存', 'id': record_id})


@bp.route('/api/health/records/detail/<int:record_id>', methods=['GET'])
def health_record_detail(record_id):
    """获取体检记录详情（含 indicators）"""
    db = get_db()
    record = db.execute('SELECT * FROM health_records WHERE id = ?', (record_id,)).fetchone()
    if not record:
        return jsonify({'success': False, 'message': '记录不存在'}), 404

    result = row_to_dict(record)
    indicators = db.execute(
        'SELECT * FROM health_indicators WHERE record_id = ? ORDER BY indicator_name',
        (record_id,)
    ).fetchall()
    result['indicators'] = rows_to_list(indicators)
    return jsonify({'success': True, 'record': result})


@bp.route('/api/health/records/detail/<int:record_id>', methods=['PUT'])
def health_record_update(record_id):
    """更新体检记录"""
    db = get_db()
    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'message': '无效数据'}), 400

    fields = []
    values = []
    field_map = [
        'record_date', 'record_type', 'title', 'hospital', 'doctor',
        'height', 'weight', 'head_circumference', 'bmi',
        'heart_result', 'lung_result', 'abdomen_result', 'skin_result',
        'bone_result', 'hearing_result', 'vision_result', 'blood_result',
        'urine_result', 'other_exam', 'diagnosis', 'advice', 'next_visit_date', 'note'
    ]
    for key in field_map:
        if key in data:
            fields.append(f'{key} = ?')
            values.append(data[key])

    if not fields:
        return jsonify({'success': False, 'message': '无更新字段'}), 400

    values.append(record_id)
    db.execute(f'UPDATE health_records SET {", ".join(fields)} WHERE id = ?', values)

    # 如果提供了 indicators，先删除旧的再插入新的
    if 'indicators' in data:
        db.execute('DELETE FROM health_indicators WHERE record_id = ?', (record_id,))
        for ind in data['indicators']:
            if ind.get('indicator_name'):
                db.execute(
                    """INSERT INTO health_indicators
                       (record_id, baby_id, indicator_name, indicator_code, value, unit,
                        reference_low, reference_high, reference_text, result_status, record_date, note)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (record_id, data.get('baby_id'), ind['indicator_name'],
                     ind.get('indicator_code', ''), ind.get('value', ''),
                     ind.get('unit', ''), ind.get('reference_low'),
                     ind.get('reference_high'), ind.get('reference_text', ''),
                     ind.get('result_status', 'normal'),
                     data.get('record_date', datetime.date.today().strftime('%Y-%m-%d')),
                     ind.get('note', ''))
                )

    db.commit()
    return jsonify({'success': True, 'message': '记录已更新'})


@bp.route('/api/health/records/detail/<int:record_id>', methods=['DELETE'])
def health_record_delete(record_id):
    """删除体检记录（级联删除关联指标）"""
    db = get_db()
    # 先删主记录并确认它真的存在：删不到就返回 404，
    # 不能假成功——前端会以为删掉了、列表却还在，这种不一致最难排查。
    cur = db.execute('DELETE FROM health_records WHERE id = ?', (record_id,))
    if cur.rowcount == 0:
        db.rollback()
        return jsonify({'success': False, 'message': '记录不存在'}), 404
    db.execute('DELETE FROM health_indicators WHERE record_id = ?', (record_id,))
    db.commit()
    return jsonify({'success': True, 'message': '记录已删除'})


# ==================== 生长曲线 ====================

@bp.route('/api/health/trend/<int:baby_id>', methods=['GET'])
def growth_trend(baby_id):
    """获取生长曲线数据（体重/身高/头围/BMI的时间序列）"""
    metric = request.args.get('metric', 'weight')
    metric_field_map = {
        'weight': 'weight',
        'height': 'height',
        'head_circumference': 'head_circumference',
        'bmi': 'bmi'
    }
    field = metric_field_map.get(metric, 'weight')

    db = get_db()
    # 从 growth_records 表读取数据（与成长记录页面数据源一致）
    rows = db.execute(
        f"""SELECT record_date as date, {field} as value
        FROM growth_records
        WHERE baby_id = ? AND {field} IS NOT NULL
        ORDER BY record_date ASC""",
        (baby_id,)
    ).fetchall()

    data = [{'date': r['date'], 'value': r['value']} for r in rows]
    return jsonify({'success': True, 'data': data})


# ==================== 指标追踪 ====================

@bp.route('/api/health/indicators/<int:baby_id>', methods=['GET'])
def health_indicators_list(baby_id):
    """获取健康指标列表"""
    db = get_db()
    name_filter = request.args.get('name', '')
    query = 'SELECT * FROM health_indicators WHERE baby_id = ?'
    params = [baby_id]
    if name_filter:
        query += ' AND indicator_name = ?'
        params.append(name_filter)
    query += ' ORDER BY record_date DESC, indicator_name'
    rows = db.execute(query, params).fetchall()
    return jsonify({'success': True, 'data': rows_to_list(rows), 'indicators': rows_to_list(rows)})


# ==================== 健康提醒 ====================

@bp.route('/api/health/reminders/<int:baby_id>', methods=['GET'])
def health_reminders_list(baby_id):
    """获取健康提醒列表"""
    db = get_db()
    show_completed = request.args.get('completed', '')
    query = 'SELECT * FROM health_reminders WHERE baby_id = ?'
    params = [baby_id]
    if show_completed != 'true':
        query += ' AND is_completed = 0'
    query += ' ORDER BY due_date ASC'
    rows = db.execute(query, params).fetchall()
    return jsonify({'success': True, 'data': rows_to_list(rows), 'reminders': rows_to_list(rows)})


@bp.route('/api/health/reminders/<int:baby_id>', methods=['POST'])
def health_reminder_create(baby_id):
    """创建健康提醒"""
    data = request.get_json()
    if not data or not data.get('title'):
        return jsonify({'success': False, 'message': '请填写标题'}), 400

    db = get_db()
    db.execute(
        """INSERT INTO health_reminders
           (baby_id, reminder_type, title, description, due_date, note)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (baby_id, data.get('reminder_type', 'custom'), data['title'],
         data.get('description', ''), data.get('due_date',
         datetime.date.today().strftime('%Y-%m-%d')), data.get('note', ''))
    )
    db.commit()
    return jsonify({'success': True, 'message': '提醒已添加'})


@bp.route('/api/health/reminders/detail/<int:reminder_id>', methods=['PUT'])
def health_reminder_update(reminder_id):
    """更新健康提醒（含标记完成）"""
    db = get_db()
    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'message': '无效数据'}), 400

    fields = []
    values = []
    for key in ('reminder_type', 'title', 'description', 'due_date', 'note'):
        if key in data:
            fields.append(f'{key} = ?')
            values.append(data[key])

    if 'is_completed' in data:
        fields.append('is_completed = ?')
        values.append(1 if data['is_completed'] else 0)
        if data['is_completed']:
            fields.append('completed_date = ?')
            values.append(datetime.date.today().strftime('%Y-%m-%d'))

    if not fields:
        return jsonify({'success': False, 'message': '无更新字段'}), 400

    values.append(reminder_id)
    db.execute(f'UPDATE health_reminders SET {", ".join(fields)} WHERE id = ?', values)
    db.commit()
    return jsonify({'success': True, 'message': '提醒已更新'})


@bp.route('/api/health/reminders/detail/<int:reminder_id>', methods=['DELETE'])
def health_reminder_delete(reminder_id):
    """删除健康提醒"""
    db = get_db()
    cur = db.execute('DELETE FROM health_reminders WHERE id = ?', (reminder_id,))
    if cur.rowcount == 0:
        db.rollback()
        return jsonify({'success': False, 'message': '提醒不存在'}), 404
    db.commit()
    return jsonify({'success': True, 'message': '提醒已删除'})


# ==================== 发育里程碑 ====================

@bp.route('/api/health/milestones/<int:baby_id>', methods=['GET'])
def milestones_list(baby_id):
    """获取发育里程碑列表"""
    db = get_db()
    category = request.args.get('category', '')
    status = request.args.get('status', '')
    query = 'SELECT * FROM milestone_details WHERE baby_id = ?'
    params = [baby_id]
    if category:
        query += ' AND category = ?'
        params.append(category)
    if status:
        query += ' AND status = ?'
        params.append(status)
    query += ' ORDER BY category, expected_age_months ASC'
    rows = db.execute(query, params).fetchall()
    return jsonify({'success': True, 'data': rows_to_list(rows), 'milestones': rows_to_list(rows)})


@bp.route('/api/health/milestones/<int:baby_id>', methods=['POST'])
def milestone_create(baby_id):
    """创建发育里程碑"""
    data = request.get_json()
    if not data or not data.get('milestone_name'):
        return jsonify({'success': False, 'message': '请填写里程碑名称'}), 400

    db = get_db()
    db.execute(
        """INSERT INTO milestone_details
           (baby_id, milestone_code, milestone_name, category, expected_age_months,
            achieved_date, actual_age_months, status, note)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (baby_id, data.get('milestone_code', ''), data['milestone_name'],
         data.get('category', 'motor'), data.get('expected_age_months'),
         data.get('achieved_date'), data.get('actual_age_months'),
         data.get('status', 'pending'), data.get('note', ''))
    )
    db.commit()
    return jsonify({'success': True, 'message': '里程碑已添加'})


@bp.route('/api/health/milestones/detail/<int:milestone_id>', methods=['PUT'])
def milestone_update(milestone_id):
    """更新发育里程碑"""
    db = get_db()
    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'message': '无效数据'}), 400

    fields = []
    values = []
    for key in ('milestone_name', 'milestone_code', 'category', 'expected_age_months',
                'achieved_date', 'actual_age_months', 'status', 'note'):
        if key in data:
            fields.append(f'{key} = ?')
            values.append(data[key])

    if not fields:
        return jsonify({'success': False, 'message': '无更新字段'}), 400

    values.append(milestone_id)
    db.execute(f'UPDATE milestone_details SET {", ".join(fields)} WHERE id = ?', values)
    db.commit()
    return jsonify({'success': True, 'message': '里程碑已更新'})


@bp.route('/api/health/milestones/detail/<int:milestone_id>', methods=['DELETE'])
def milestone_delete(milestone_id):
    """删除发育里程碑"""
    db = get_db()
    cur = db.execute('DELETE FROM milestone_details WHERE id = ?', (milestone_id,))
    if cur.rowcount == 0:
        db.rollback()
        return jsonify({'success': False, 'message': '里程碑不存在'}), 404
    db.commit()
    return jsonify({'success': True, 'message': '里程碑已删除'})


@bp.route('/api/health/milestones/template/<int:baby_id>', methods=['POST'])
def milestones_template_init(baby_id):
    """批量生成标准发育里程碑模板（43条）"""
    db = get_db()

    # 检查是否已有记录
    existing = db.execute(
        'SELECT COUNT(*) FROM milestone_details WHERE baby_id = ?', (baby_id,)
    ).fetchone()[0]
    if existing > 0:
        return jsonify({'success': False, 'message': '已存在里程碑记录，不会覆盖'}), 400

    templates = [
        # 大运动 (motor)
        ('motor_roll_over', '翻身', 'motor', 4),
        ('motor_sit_support', '扶坐', 'motor', 4),
        ('motor_sit_alone', '独坐', 'motor', 6),
        ('motor_crawl', '爬行', 'motor', 8),
        ('motor_stand_support', '扶站', 'motor', 9),
        ('motor_stand_alone', '独站', 'motor', 11),
        ('motor_walk', '独走', 'motor', 12),
        ('motor_run', '跑', 'motor', 18),
        ('motor_jump', '双脚跳', 'motor', 24),
        ('motor_climb', '上下楼梯', 'motor', 24),
        # 精细运动 (fine_motor)
        ('fine_grasp', '抓握', 'fine_motor', 3),
        ('fine_transfer', '双手传递', 'fine_motor', 6),
        ('fine_pincer', '钳指抓', 'fine_motor', 9),
        ('fine_stack2', '叠2块积木', 'fine_motor', 12),
        ('fine_stack4', '叠4块积木', 'fine_motor', 15),
        ('fine_stack6', '叠6块积木', 'fine_motor', 18),
        ('fine_draw', '乱画', 'fine_motor', 18),
        ('fine_circle', '画圆形', 'fine_motor', 30),
        ('fine_cross', '画十字', 'fine_motor', 36),
        # 语言 (language)
        ('lang_coo', '咿呀发音', 'language', 2),
        ('lang_ba', '发ba/da音', 'language', 6),
        ('lang_mama', '有意识叫爸妈', 'language', 10),
        ('lang_1word', '说1个词', 'language', 12),
        ('lang_2words', '说2词短语', 'language', 18),
        ('lang_10words', '说10个词', 'language', 18),
        ('lang_sentence', '说简单句', 'language', 24),
        ('lang_sing', '唱短歌', 'language', 30),
        ('lang_tell_story', '讲故事', 'language', 36),
        # 认知 (cognitive)
        ('cog_track', '追视移动物', 'cognitive', 2),
        ('cog_find_hidden', '找隐藏物品', 'cognitive', 9),
        ('cog_pretend', '假扮游戏', 'cognitive', 18),
        ('cog_sort', '分类形状/颜色', 'cognitive', 24),
        ('cog_count', '数数', 'cognitive', 30),
        ('cog_letters', '认识字母', 'cognitive', 36),
        # 社交 (social)
        ('soc_smile', '社会性微笑', 'social', 1),
        ('soc_laugh', '大笑', 'social', 3),
        ('soc_stranger', '认生', 'social', 6),
        ('soc_wave', '挥手再见', 'social', 9),
        ('soc_play', '平行游戏', 'social', 24),
        ('soc_share', '分享', 'social', 30),
        ('soc_cooperate', '合作游戏', 'social', 36),
        # 自理 (self_care)
        ('self_drink', '自己用杯喝', 'self_care', 12),
        ('self_spoon', '自己用勺', 'self_care', 18),
        ('self_undress', '脱衣服', 'self_care', 24),
        ('self_toilet', '示意如厕', 'self_care', 24),
        ('self_dress', '穿简单衣服', 'self_care', 30),
    ]

    for code, name, cat, age_months in templates:
        db.execute(
            """INSERT INTO milestone_details
               (baby_id, milestone_code, milestone_name, category, expected_age_months, status)
               VALUES (?, ?, ?, ?, ?, 'pending')""",
            (baby_id, code, name, cat, age_months)
        )

    db.commit()
    return jsonify({'success': True, 'message': f'已生成 {len(templates)} 条标准发育里程碑'})


# ==================== 视力听力筛查 ====================

@bp.route('/api/health/screenings/<int:baby_id>', methods=['GET'])
def screenings_list(baby_id):
    """获取视力听力筛查列表"""
    db = get_db()
    stype = request.args.get('type', '')
    query = 'SELECT * FROM screenings WHERE baby_id = ?'
    params = [baby_id]
    if stype:
        query += ' AND screening_type = ?'
        params.append(stype)
    query += ' ORDER BY screening_date DESC'
    rows = db.execute(query, params).fetchall()
    return jsonify({'success': True, 'data': rows_to_list(rows), 'screenings': rows_to_list(rows)})


@bp.route('/api/health/screenings/<int:baby_id>', methods=['POST'])
def screening_create(baby_id):
    """创建筛查记录"""
    data = request.get_json()
    if not data or not data.get('screening_type'):
        return jsonify({'success': False, 'message': '请选择筛查类型'}), 400

    db = get_db()
    db.execute(
        """INSERT INTO screenings
           (baby_id, screening_type, screening_date, age_months, screening_method,
            hospital, doctor, result_summary, result_status, detail_left, detail_right,
            referral_needed, referral_done, next_screening_date, note)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (baby_id, data['screening_type'],
         data.get('screening_date', datetime.date.today().strftime('%Y-%m-%d')),
         data.get('age_months'), data.get('screening_method', ''),
         data.get('hospital', ''), data.get('doctor', ''),
         data.get('result_summary', ''), data.get('result_status', 'normal'),
         data.get('detail_left', ''), data.get('detail_right', ''),
         data.get('referral_needed', 0), data.get('referral_done', 0),
         data.get('next_screening_date'), data.get('note', ''))
    )
    db.commit()
    return jsonify({'success': True, 'message': '筛查记录已添加'})


@bp.route('/api/health/screenings/detail/<int:screening_id>', methods=['PUT'])
def screening_update(screening_id):
    """更新筛查记录"""
    db = get_db()
    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'message': '无效数据'}), 400

    fields = []
    values = []
    for key in ('screening_type', 'screening_date', 'age_months', 'screening_method',
                'hospital', 'doctor', 'result_summary', 'result_status',
                'detail_left', 'detail_right', 'referral_needed', 'referral_done',
                'next_screening_date', 'note'):
        if key in data:
            fields.append(f'{key} = ?')
            values.append(data[key])

    if not fields:
        return jsonify({'success': False, 'message': '无更新字段'}), 400

    values.append(screening_id)
    db.execute(f'UPDATE screenings SET {", ".join(fields)} WHERE id = ?', values)
    db.commit()
    return jsonify({'success': True, 'message': '筛查记录已更新'})


@bp.route('/api/health/screenings/detail/<int:screening_id>', methods=['DELETE'])
def screening_delete(screening_id):
    """删除筛查记录"""
    db = get_db()
    cur = db.execute('DELETE FROM screenings WHERE id = ?', (screening_id,))
    if cur.rowcount == 0:
        db.rollback()
        return jsonify({'success': False, 'message': '筛查记录不存在'}), 404
    db.commit()
    return jsonify({'success': True, 'message': '筛查记录已删除'})


# ==================== 过敏史 ====================

@bp.route('/api/health/allergies/<int:baby_id>', methods=['GET'])
def allergies_list(baby_id):
    """获取过敏史列表"""
    db = get_db()
    atype = request.args.get('type', '')
    status = request.args.get('status', '')
    query = 'SELECT * FROM allergy_history WHERE baby_id = ?'
    params = [baby_id]
    if atype:
        query += ' AND allergen_type = ?'
        params.append(atype)
    if status:
        query += ' AND status = ?'
        params.append(status)
    query += ' ORDER BY severity_level DESC, allergen_name'
    rows = db.execute(query, params).fetchall()
    return jsonify({'success': True, 'data': rows_to_list(rows), 'allergies': rows_to_list(rows)})


@bp.route('/api/health/allergies/<int:baby_id>', methods=['POST'])
def allergy_create(baby_id):
    """创建过敏记录"""
    data = request.get_json()
    if not data or not data.get('allergen_name'):
        return jsonify({'success': False, 'message': '请填写过敏原名称'}), 400

    db = get_db()
    db.execute(
        """INSERT INTO allergy_history
           (baby_id, allergen_type, allergen_name, reaction_detail, severity_level,
            first_occurrence_date, last_occurrence_date, diagnosis_method,
            diagnosed_by, management, status, note)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (baby_id, data.get('allergen_type', 'food'), data['allergen_name'],
         data.get('reaction_detail', ''), data.get('severity_level', 'mild'),
         data.get('first_occurrence_date'), data.get('last_occurrence_date'),
         data.get('diagnosis_method', ''), data.get('diagnosed_by', ''),
         data.get('management', ''), data.get('status', 'active'), data.get('note', ''))
    )
    db.commit()
    return jsonify({'success': True, 'message': '过敏记录已添加'})


@bp.route('/api/health/allergies/detail/<int:allergy_id>', methods=['PUT'])
def allergy_update(allergy_id):
    """更新过敏记录"""
    db = get_db()
    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'message': '无效数据'}), 400

    fields = []
    values = []
    for key in ('allergen_type', 'allergen_name', 'reaction_detail', 'severity_level',
                'first_occurrence_date', 'last_occurrence_date', 'diagnosis_method',
                'diagnosed_by', 'management', 'status', 'note'):
        if key in data:
            fields.append(f'{key} = ?')
            values.append(data[key])

    if not fields:
        return jsonify({'success': False, 'message': '无更新字段'}), 400

    values.append(allergy_id)
    db.execute(f'UPDATE allergy_history SET {", ".join(fields)} WHERE id = ?', values)
    db.commit()
    return jsonify({'success': True, 'message': '过敏记录已更新'})


@bp.route('/api/health/allergies/detail/<int:allergy_id>', methods=['DELETE'])
def allergy_delete(allergy_id):
    """删除过敏记录"""
    db = get_db()
    cur = db.execute('DELETE FROM allergy_history WHERE id = ?', (allergy_id,))
    if cur.rowcount == 0:
        db.rollback()
        return jsonify({'success': False, 'message': '过敏记录不存在'}), 404
    db.commit()
    return jsonify({'success': True, 'message': '过敏记录已删除'})


# ==================== 喂养摘要 ====================

@bp.route('/api/health/feeding_summary/<int:baby_id>', methods=['GET'])
def feeding_summary_list(baby_id):
    """获取喂养摘要列表"""
    db = get_db()
    rows = db.execute(
        'SELECT * FROM feeding_summary WHERE baby_id = ? ORDER BY record_month DESC',
        (baby_id,)
    ).fetchall()
    return jsonify({'success': True, 'data': rows_to_list(rows), 'feeding_summary': rows_to_list(rows)})


@bp.route('/api/health/feeding_summary/<int:baby_id>', methods=['POST'])
def feeding_summary_create(baby_id):
    """创建喂养摘要"""
    data = request.get_json()
    if not data or not data.get('record_month'):
        return jsonify({'success': False, 'message': '请选择月份'}), 400

    db = get_db()
    db.execute(
        """INSERT INTO feeding_summary
           (baby_id, record_month, feeding_type, avg_daily_ml, feeding_frequency,
            solid_food_count, weight_gain_month, height_gain_month, concerns, note)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (baby_id, data['record_month'], data.get('feeding_type', ''),
         data.get('avg_daily_ml'), data.get('feeding_frequency'),
         data.get('solid_food_count'), data.get('weight_gain_month'),
         data.get('height_gain_month'), data.get('concerns', ''), data.get('note', ''))
    )
    db.commit()
    return jsonify({'success': True, 'message': '喂养摘要已添加'})


@bp.route('/api/health/feeding_summary/detail/<int:summary_id>', methods=['PUT'])
def feeding_summary_update(summary_id):
    """更新喂养摘要"""
    db = get_db()
    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'message': '无效数据'}), 400

    fields = []
    values = []
    for key in ('record_month', 'feeding_type', 'avg_daily_ml', 'feeding_frequency',
                'solid_food_count', 'weight_gain_month', 'height_gain_month',
                'concerns', 'note'):
        if key in data:
            fields.append(f'{key} = ?')
            values.append(data[key])

    if not fields:
        return jsonify({'success': False, 'message': '无更新字段'}), 400

    values.append(summary_id)
    db.execute(f'UPDATE feeding_summary SET {", ".join(fields)} WHERE id = ?', values)
    db.commit()
    return jsonify({'success': True, 'message': '喂养摘要已更新'})


@bp.route('/api/health/feeding_summary/detail/<int:summary_id>', methods=['DELETE'])
def feeding_summary_delete(summary_id):
    """删除喂养摘要"""
    db = get_db()
    cur = db.execute('DELETE FROM feeding_summary WHERE id = ?', (summary_id,))
    if cur.rowcount == 0:
        db.rollback()
        return jsonify({'success': False, 'message': '喂养摘要不存在'}), 404
    db.commit()
    return jsonify({'success': True, 'message': '喂养摘要已删除'})


@bp.route('/api/health/feeding_summary/generate/<int:baby_id>', methods=['POST'])
def feeding_summary_generate(baby_id):
    """根据最近6个月的喂养记录自动生成月度摘要"""
    db = get_db()
    today = datetime.date.today()

    # 检查是否已有记录
    existing_months = set(
        row[0] for row in db.execute(
            'SELECT record_month FROM feeding_summary WHERE baby_id = ?', (baby_id,)
        ).fetchall()
    )

    generated_count = 0
    for i in range(6):
        month_date = today - datetime.timedelta(days=i * 30)
        month_str = month_date.strftime('%Y-%m')
        if month_str in existing_months:
            continue

        month_start = f'{month_str}-01'
        month_end_date = (month_date.replace(day=28) + datetime.timedelta(days=4)).replace(day=1) - datetime.timedelta(days=1)
        month_end = month_end_date.strftime('%Y-%m-%d')

        # 统计当月喂养
        feeding_stats = db.execute(
            """SELECT COUNT(*) as count, COALESCE(SUM(amount), 0) as total
               FROM feeding_records
               WHERE baby_id = ? AND date(start_time) >= ? AND date(start_time) <= ?""",
            (baby_id, month_start, month_end)
        ).fetchone()

        if feeding_stats['count'] > 0:
            days_in_month = month_end_date.day
            avg_daily = round(feeding_stats['total'] / days_in_month, 1) if days_in_month > 0 else 0
            avg_freq = round(feeding_stats['count'] / days_in_month, 1) if days_in_month > 0 else 0

            # 判断喂养类型
            feeding_type_row = db.execute(
                """SELECT feeding_type, COUNT(*) as cnt
                   FROM feeding_records
                   WHERE baby_id = ? AND date(start_time) >= ? AND date(start_time) <= ?
                   GROUP BY feeding_type ORDER BY cnt DESC LIMIT 1""",
                (baby_id, month_start, month_end)
            ).fetchone()
            feeding_type = feeding_type_row['feeding_type'] if feeding_type_row else ''

            db.execute(
                """INSERT INTO feeding_summary
                   (baby_id, record_month, feeding_type, avg_daily_ml, feeding_frequency)
                   VALUES (?, ?, ?, ?, ?)""",
                (baby_id, month_str, feeding_type, avg_daily, avg_freq)
            )
            generated_count += 1

    db.commit()
    return jsonify({
        'success': True,
        'message': f'已生成 {generated_count} 个月的喂养摘要'
    })


# ==================== 疫苗计划管理 ====================

# 国家免疫规划疫苗程序表（一类疫苗）
VACCINE_SCHEDULE = [
    {'name': '乙肝疫苗', 'dose': 1, 'scheduled_days': 0, 'type': 'free', 'note': '出生24小时内'},
    {'name': '卡介苗', 'dose': 1, 'scheduled_days': 0, 'type': 'free', 'note': '出生时'},
    {'name': '乙肝疫苗', 'dose': 2, 'scheduled_days': 30, 'type': 'free', 'note': '1月龄'},
    {'name': '脊灰疫苗', 'dose': 1, 'scheduled_days': 60, 'type': 'free', 'note': '2月龄'},
    {'name': '脊灰疫苗', 'dose': 2, 'scheduled_days': 90, 'type': 'free', 'note': '3月龄'},
    {'name': '百白破疫苗', 'dose': 1, 'scheduled_days': 90, 'type': 'free', 'note': '3月龄'},
    {'name': '脊灰疫苗', 'dose': 3, 'scheduled_days': 120, 'type': 'free', 'note': '4月龄'},
    {'name': '百白破疫苗', 'dose': 2, 'scheduled_days': 120, 'type': 'free', 'note': '4月龄'},
    {'name': '百白破疫苗', 'dose': 3, 'scheduled_days': 150, 'type': 'free', 'note': '5月龄'},
    {'name': '乙肝疫苗', 'dose': 3, 'scheduled_days': 180, 'type': 'free', 'note': '6月龄'},
    {'name': 'A群流脑多糖疫苗', 'dose': 1, 'scheduled_days': 180, 'type': 'free', 'note': '6月龄'},
    {'name': '麻腮风疫苗', 'dose': 1, 'scheduled_days': 240, 'type': 'free', 'note': '8月龄'},
    {'name': '乙脑减毒活疫苗', 'dose': 1, 'scheduled_days': 240, 'type': 'free', 'note': '8月龄'},
    {'name': 'A群流脑多糖疫苗', 'dose': 2, 'scheduled_days': 270, 'type': 'free', 'note': '9月龄'},
    {'name': '甲肝灭活疫苗', 'dose': 1, 'scheduled_days': 540, 'type': 'free', 'note': '18月龄'},
    {'name': '百白破疫苗', 'dose': 4, 'scheduled_days': 540, 'type': 'free', 'note': '18月龄'},
    {'name': '麻腮风疫苗', 'dose': 2, 'scheduled_days': 540, 'type': 'free', 'note': '18月龄'},
    {'name': '甲肝灭活疫苗', 'dose': 2, 'scheduled_days': 720, 'type': 'free', 'note': '2岁'},
    {'name': '乙脑减毒活疫苗', 'dose': 2, 'scheduled_days': 720, 'type': 'free', 'note': '2岁'},
    {'name': 'A群C群流脑多糖疫苗', 'dose': 1, 'scheduled_days': 1080, 'type': 'free', 'note': '3岁'},
    {'name': '脊灰疫苗', 'dose': 4, 'scheduled_days': 1440, 'type': 'free', 'note': '4岁'},
    {'name': '白破疫苗', 'dose': 1, 'scheduled_days': 2160, 'type': 'free', 'note': '6岁'},
    {'name': 'A群C群流脑多糖疫苗', 'dose': 2, 'scheduled_days': 2160, 'type': 'free', 'note': '6岁'},
]

# 常见自费疫苗推荐
PAID_VACCINES = [
    {'name': '五联疫苗', 'doses': [60, 90, 120, 540], 'type': 'paid', 'note': '替代百白破+脊灰+Hib'},
    {'name': '十三价肺炎疫苗', 'doses': [90, 120, 150, 360], 'type': 'paid', 'note': '2/4/6月龄+加强'},
    {'name': '五价轮状病毒疫苗', 'doses': [60, 90, 120], 'type': 'paid', 'note': '口服，6周龄起'},
    {'name': '手足口疫苗(EV71)', 'doses': [180, 210], 'type': 'paid', 'note': '6月龄后，2针'},
    {'name': '水痘疫苗', 'doses': [360, 1080], 'type': 'paid', 'note': '1岁+4岁'},
    {'name': '流感疫苗', 'doses': [180], 'type': 'paid', 'note': '6月龄后每年接种'},
    {'name': 'AC结合流脑疫苗', 'doses': [90, 120, 150], 'type': 'paid', 'note': '替代A群流脑'},
    {'name': 'Hib疫苗', 'doses': [60, 90, 120, 540], 'type': 'paid', 'note': '2/4/6月龄+加强'},
]


@bp.route('/api/health/vaccine/schedule/<int:baby_id>', methods=['GET'])
def vaccine_schedule_get(baby_id):
    """获取宝宝疫苗计划（含已完成和待接种）"""
    try:
        db = get_db()
        baby = db.execute('SELECT birthday FROM babies WHERE id = ?', (baby_id,)).fetchone()
        if not baby:
            return jsonify({'success': False, 'message': '宝宝不存在'}), 404

        birth_date = baby['birthday']
        try:
            birth = datetime.datetime.strptime(birth_date[:10], '%Y-%m-%d').date()
        except (ValueError, TypeError):
            return jsonify({'success': False, 'message': '宝宝出生日期无效'}), 400

        # 获取已接种记录
        completed = db.execute(
            "SELECT * FROM vaccine_details WHERE baby_id = ? AND status = 'completed' ORDER BY actual_date",
            (baby_id,)
        ).fetchall()
        completed_map = {}
        for v in completed:
            key = f"{v['vaccine_name']}_{v['dose_number']}"
            completed_map[key] = row_to_dict(v)

        # 生成疫苗计划
        schedule = []
        for v in VACCINE_SCHEDULE:
            scheduled_date = birth + datetime.timedelta(days=v['scheduled_days'])
            key = f"{v['name']}_{v['dose']}"
            completed_record = completed_map.get(key)
            status = 'completed' if completed_record else ('overdue' if scheduled_date < datetime.date.today() else 'pending')
            schedule.append({
                'vaccine_name': v['name'],
                'dose': v['dose'],
                'scheduled_date': scheduled_date.strftime('%Y-%m-%d'),
                'type': v['type'],
                'note': v['note'],
                'status': status,
                'actual_date': completed_record.get('actual_date') if completed_record else None,
                'record_id': completed_record.get('id') if completed_record else None,
            })

        return jsonify({
            'success': True,
            'birthday': birth_date,
            'schedule': schedule,
            'completed_count': len(completed),
            'total_count': len(VACCINE_SCHEDULE),
        })
    except Exception as e:
        import logging
        logging.getLogger(__name__).error("vaccine_schedule_get error: %s", e, exc_info=True)
        return jsonify({'success': False, 'message': '加载疫苗计划失败'}), 500


@bp.route('/api/health/vaccine/schedule/init/<int:baby_id>', methods=['POST'])
def vaccine_schedule_init(baby_id):
    """根据出生日期自动生成疫苗计划到 vaccine_details 表"""
    try:
        db = get_db()
        baby = db.execute('SELECT birthday FROM babies WHERE id = ?', (baby_id,)).fetchone()
        if not baby:
            return jsonify({'success': False, 'message': '宝宝不存在'}), 404

        birth_date = baby['birthday']
        try:
            birth = datetime.datetime.strptime(birth_date[:10], '%Y-%m-%d').date()
        except (ValueError, TypeError):
            return jsonify({'success': False, 'message': '宝宝出生日期无效'}), 400

        # 检查是否已有记录
        existing = db.execute(
            'SELECT COUNT(*) FROM vaccine_details WHERE baby_id = ?', (baby_id,)
        ).fetchone()[0]
        if existing > 0:
            return jsonify({'success': False, 'message': '已存在疫苗记录，不会覆盖'}), 400

        count = 0
        for v in VACCINE_SCHEDULE:
            scheduled_date = birth + datetime.timedelta(days=v['scheduled_days'])
            db.execute(
                """INSERT INTO vaccine_details 
                   (baby_id, vaccine_name, vaccine_type, dose_number, scheduled_date, status)
                   VALUES (?, ?, ?, ?, ?, 'pending')""",
                (baby_id, v['name'], v['type'], v['dose'], scheduled_date.strftime('%Y-%m-%d'))
            )
            count += 1

        db.commit()
        return jsonify({'success': True, 'message': f'已生成 {count} 条疫苗计划'})
    except Exception as e:
        import logging
        logging.getLogger(__name__).error("vaccine_schedule_init error: %s", e, exc_info=True)
        return jsonify({'success': False, 'message': '生成疫苗计划失败'}), 500


@bp.route('/api/health/vaccine/paid', methods=['GET'])
def vaccine_paid_list():
    """获取常见自费疫苗推荐列表"""
    return jsonify({'success': True, 'data': PAID_VACCINES, 'vaccines': PAID_VACCINES})


# ==================== 生长曲线增强（含百分位） ====================

@bp.route('/api/health/growth/percentile/<int:baby_id>', methods=['GET'])
def growth_percentile(baby_id):
    """获取生长百分位数据（身高/体重/BMI）"""
    try:
        db = get_db()
        baby = db.execute('SELECT gender, birthday FROM babies WHERE id = ?', (baby_id,)).fetchone()
        if not baby:
            return jsonify({'success': False, 'message': '宝宝不存在'}), 404

        gender = baby['gender'] if baby['gender'] in ('boy', 'girl') else 'boy'

        # 获取所有生长记录
        rows = db.execute(
            """SELECT record_date, age_months, height, weight, head_circumference, bmi
               FROM health_records 
               WHERE baby_id = ? AND (height IS NOT NULL OR weight IS NOT NULL)
               ORDER BY record_date ASC""",
            (baby_id,)
        ).fetchall()

        height_data = []
        weight_data = []
        bmi_data = []

        for r in rows:
            age = r['age_months']
            if age is None:
                continue
            if r['height']:
                pct = _calc_percentile(r['height'], age, 'height', gender)
                height_data.append({
                    'date': r['record_date'],
                    'age_months': age,
                    'value': r['height'],
                    'percentile': round(pct, 1) if pct else None,
                })
            if r['weight']:
                pct = _calc_percentile(r['weight'], age, 'weight', gender)
                weight_data.append({
                    'date': r['record_date'],
                    'age_months': age,
                    'value': r['weight'],
                    'percentile': round(pct, 1) if pct else None,
                })
            if r['bmi']:
                bmi_data.append({
                    'date': r['record_date'],
                    'age_months': age,
                    'value': r['bmi'],
                })

        return jsonify({
            'success': True,
            'gender': gender,
            'height_data': height_data,
            'weight_data': weight_data,
            'bmi_data': bmi_data,
        })
    except Exception as e:
        import logging
        logging.getLogger(__name__).error("growth_percentile error: %s", e, exc_info=True)
        return jsonify({'success': False, 'message': '加载生长百分位数据失败'}), 500


# ==================== 生长速度分析 ====================

@bp.route('/api/health/growth/velocity/<int:baby_id>', methods=['GET'])
def growth_velocity(baby_id):
    """分析生长速度（检测生长迟缓或过快）"""
    try:
        db = get_db()
        rows = db.execute(
            """SELECT record_date, age_months, height, weight
               FROM health_records 
               WHERE baby_id = ? AND height IS NOT NULL AND weight IS NOT NULL
               ORDER BY record_date ASC""",
            (baby_id,)
        ).fetchall()

        if len(rows) < 2:
            return jsonify({
                'success': True,
                'message': '需要至少2条生长记录才能分析速度',
                'velocity': None,
            })

        # 计算最近两次记录的生长速度
        latest = rows[-1]
        previous = rows[-2]

        try:
            date1 = datetime.datetime.strptime(previous['record_date'][:10], '%Y-%m-%d')
            date2 = datetime.datetime.strptime(latest['record_date'][:10], '%Y-%m-%d')
            days_diff = (date2 - date1).days
        except (ValueError, TypeError):
            days_diff = 30

        if days_diff <= 0:
            days_diff = 30

        height_velocity = ((latest['height'] - previous['height']) / days_diff * 30) if previous['height'] else None
        weight_velocity = ((latest['weight'] - previous['weight']) / days_diff * 30 * 1000) if previous['weight'] else None  # 转为g/月

        # 生长速度评估
        age_months = latest['age_months'] or 0
        alerts = []

        if height_velocity is not None:
            if age_months <= 3 and height_velocity < 2:
                alerts.append({'type': 'warning', 'message': '0-3月龄身高增长偏慢（<2cm/月），建议咨询医生'})
            elif 3 < age_months <= 12 and height_velocity < 1:
                alerts.append({'type': 'warning', 'message': '3-12月龄身高增长偏慢（<1cm/月），建议咨询医生'})
            elif height_velocity > 5:
                alerts.append({'type': 'info', 'message': '身高增长较快'})

        if weight_velocity is not None:
            if age_months <= 3 and weight_velocity < 400:
                alerts.append({'type': 'warning', 'message': '0-3月龄体重增长偏慢（<400g/月），建议咨询医生'})
            elif 3 < age_months <= 12 and weight_velocity < 200:
                alerts.append({'type': 'warning', 'message': '3-12月龄体重增长偏慢（<200g/月），建议咨询医生'})
            elif weight_velocity > 1500:
                alerts.append({'type': 'warning', 'message': '体重增长过快，注意喂养量'})

        return jsonify({
            'success': True,
            'velocity': {
                'height_cm_per_month': round(height_velocity, 2) if height_velocity else None,
                'weight_g_per_month': round(weight_velocity, 0) if weight_velocity else None,
                'period_days': days_diff,
                'from_date': previous['record_date'],
                'to_date': latest['record_date'],
            },
            'alerts': alerts,
        })
    except Exception as e:
        import logging
        logging.getLogger(__name__).error("growth_velocity error: %s", e, exc_info=True)
        return jsonify({'success': False, 'message': '分析生长速度失败'}), 500


# ==================== 健康时间线 ====================

@bp.route('/api/health/timeline/<int:baby_id>', methods=['GET'])
def health_timeline(baby_id):
    """获取宝宝健康时间线（所有健康事件按日期排序）"""
    try:
        db = get_db()
        baby = db.execute('SELECT birthday FROM babies WHERE id = ?', (baby_id,)).fetchone()
        if not baby:
            return jsonify({'success': False, 'message': '宝宝不存在'}), 404

        birth_date = baby['birthday']
        try:
            birth = datetime.datetime.strptime(birth_date[:10], '%Y-%m-%d').date()
        except (ValueError, TypeError):
            birth = datetime.date.today()

        timeline = []

        # 体检记录
        records = db.execute(
            "SELECT id, record_date, record_type, title, height, weight FROM health_records WHERE baby_id = ?",
            (baby_id,)
        ).fetchall()
        for r in records:
            timeline.append({
                'date': r['record_date'],
                'type': 'checkup',
                'category': '体检',
                'title': r['title'],
                'detail': f"类型: {r['record_type']}" + (f" | 身高: {r['height']}cm" if r['height'] else '') + (f" | 体重: {r['weight']}kg" if r['weight'] else ''),
                'record_id': r['id'],
            })

        # 疫苗记录
        vaccines = db.execute(
            "SELECT id, vaccine_name, dose_number, actual_date, scheduled_date, status FROM vaccine_details WHERE baby_id = ?",
            (baby_id,)
        ).fetchall()
        for v in vaccines:
            event_date = v['actual_date'] or v['scheduled_date']
            if event_date:
                timeline.append({
                    'date': event_date,
                    'type': 'vaccine',
                    'category': '疫苗',
                    'title': f"{v['vaccine_name']} (第{v['dose_number']}针)",
                    'detail': f"状态: {v['status']}",
                    'record_id': v['id'],
                })

        # 里程碑
        milestones = db.execute(
            "SELECT id, milestone_name, achieved_date, status, category FROM milestone_details WHERE baby_id = ? AND achieved_date IS NOT NULL",
            (baby_id,)
        ).fetchall()
        for m in milestones:
            timeline.append({
                'date': m['achieved_date'],
                'type': 'milestone',
                'category': '里程碑',
                'title': m['milestone_name'],
                'detail': f"类别: {m['category']}",
                'record_id': m['id'],
            })

        # 筛查记录
        screenings = db.execute(
            "SELECT id, screening_type, screening_date, result_status FROM screenings WHERE baby_id = ?",
            (baby_id,)
        ).fetchall()
        for s in screenings:
            timeline.append({
                'date': s['screening_date'],
                'type': 'screening',
                'category': '筛查',
                'title': s['screening_type'],
                'detail': f"结果: {s['result_status']}",
                'record_id': s['id'],
            })

        # 过敏记录
        allergies = db.execute(
            "SELECT id, allergen_name, first_occurrence_date, severity_level FROM allergy_history WHERE baby_id = ?",
            (baby_id,)
        ).fetchall()
        for a in allergies:
            if a['first_occurrence_date']:
                timeline.append({
                    'date': a['first_occurrence_date'],
                    'type': 'allergy',
                    'category': '过敏',
                    'title': a['allergen_name'],
                    'detail': f"严重程度: {a['severity_level']}",
                    'record_id': a['id'],
                })

        # 按日期倒序排列
        timeline.sort(key=lambda x: x['date'], reverse=True)

        # 限制返回数量
        limit = request.args.get('limit', 50, type=int)
        timeline = timeline[:limit]

        return jsonify({
            'success': True,
            'birthday': birth_date,
            'total_events': len(timeline),
            'timeline': timeline,
        })
    except Exception as e:
        import logging
        logging.getLogger(__name__).error("health_timeline error: %s", e, exc_info=True)
        return jsonify({'success': False, 'message': '加载健康时间线失败'}), 500


# 注意：曾有一个重复注册的 POST /api/health/records/<baby_id>「增强版」实现，
# 与上方 health_record_create 路由冲突且使用错误列名 birth_date（实际为 birthday），
# 属于永不可达的死代码，已移除（路由冲突修复）。
