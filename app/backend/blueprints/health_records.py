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
import re
from flask import Blueprint, request, jsonify

from utils import get_db, row_to_dict, rows_to_list, normalize_author
from validators import validate_enum as _validate_enum
from user_context import require_not_integration
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


# ==================== 写入端校验（模块内唯一收口）====================
# 体检 / 指标 / 提醒 / 里程碑 / 筛查 / 过敏 / 喂养摘要 七类写入此前各写各的，
# 结果是这些脏数据能直接落库：体温填 999、筛查日期写成 2026-13-40、
# 结果判定存成前端 statusMap 认不出的野串、baby_id 瞎填也照样 201。
# 现在统一走下面这几个函数，每条写入路由都过一遍。

# 医学数值的合理区间：防手误，也防 WHO 百分位换算在极端值上算出无意义的曲线。
_HEALTH_RANGES = {
    "temperature": (25.0, 45.0),           # 摄氏度
    "height": (30.0, 130.0),               # cm
    "weight": (0.5, 45.0),                 # kg
    "head_circumference": (28.0, 60.0),    # cm
    "bmi": (2.0, 40.0),
    "heart_rate": (20.0, 250.0),           # 次/分
    "respiratory_rate": (5.0, 90.0),       # 次/分
    "oxygen_saturation": (40.0, 100.0),    # %
    "age_months": (0, 240),                # 月龄（0-20 岁）
    "actual_age_months": (0, 240),
    "expected_age_months": (0, 240),
    "avg_daily_ml": (0, 5000),             # 日均奶量
    "feeding_frequency": (0, 60),          # 日均次数
    "solid_food_count": (0, 100),          # 辅食种类数
    "weight_gain_month": (-2000, 5000),    # 月增重 g
    "height_gain_month": (-30, 30),        # 月增高 cm
    "summary_value": (0, 10000),
}

_E_RECORD_TYPES = ("routine", "vaccine", "dental", "eye", "blood", "other")
_E_REMINDER_TYPES = ("checkup", "vaccine", "medication", "custom")
_E_MILESTONE_STATUS = ("pending", "achieved", "delayed", "concerned")
_E_MILESTONE_CATEGORIES = ("motor", "fine_motor", "language", "cognitive", "social", "self_care")
_E_SCREENING_TYPES = ("vision_筛查", "hearing_筛查", "vision_诊断", "hearing_诊断")
_E_SCREENING_STATUS = ("normal", "abnormal", "borderline", "pending")
_E_ALLERGY_TYPES = ("food", "drug", "environmental", "other")
_E_ALLERGY_SEVERITY = ("mild", "moderate", "severe", "anaphylaxis")
_E_ALLERGY_STATUS = ("active", "resolved", "monitoring")
_E_FEEDING_TYPES = ("breastmilk", "formula", "mixed", "solid", "")
_E_INDICATOR_STATUS = ("normal", "high", "low", "abnormal")


def _check_baby_exists(db, baby_id):
    """宝宝不存在直接 404。

    少了这一步，任何人拿一个不存在的 baby_id 都能往库里塞记录：
    Overview 统计、导出备份、恢复导入全都按 baby_id 走，孤儿数据会一路污染到换机恢复。
    """
    row = db.execute('SELECT id FROM babies WHERE id = ?', (baby_id,)).fetchone()
    if not row:
        return False, f"宝宝不存在（id={baby_id}）"
    return True, None


def _validate_month(month_str, field_name="月份"):
    """喂养摘要按月记。格式必须是 YYYY-MM，否则 UNIQUE(baby_id, record_month) 拦不住「2026/10」这种写法。"""
    if not month_str:
        return None, None
    text = str(month_str).strip()
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", text):
        return None, f"{field_name}格式无效，应为 YYYY-MM"
    return text, None


def _check_field(data, key, errs, label=None, kind="date", enum_values=None):
    """体检一个写入字段：通过就把归一化值写回 data，失败记一条错误并把该字段摘掉（不再写库）。

    kind: date（YYYY-MM-DD）/ month（YYYY-MM）/ number（按 _HEALTH_RANGES 区间）
    空值一律放过——可空列跟 validate_* 的既有口径一致，NOT NULL 的列由调用方单独判。
    """
    if key not in data:
        return
    label = label or key
    raw = data.get(key)
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return
    if kind == "month":
        val, err = _validate_month(raw, label)
    elif kind == "number":
        lo, hi = _HEALTH_RANGES.get(key, (None, None))
        val, err = _validate_number(raw, lo, hi, label)
    elif enum_values is not None:
        ok_enum, val, err = _validate_enum(raw, set(enum_values), label)
        if not ok_enum:
            errs.append(err)
            data.pop(key, None)
    else:
        val, err = _validate_date(raw, label)
    if err:
        errs.append(err)
        data.pop(key, None)
    else:
        data[key] = val


def _check_dates(data, keys, errs):
    """批量日期校验"""
    for k in keys:
        _check_field(data, k, errs, kind="date")


def _check_numbers(data, keys, errs):
    """批量数值校验"""
    for k in keys:
        _check_field(data, k, errs, kind="number")


def _check_enum(data, key, errs, enum_values, label=None):
    """批量枚举校验"""
    _check_field(data, key, errs, label=label, kind="enum", enum_values=enum_values)


def _errs_to_response(errs):
    return jsonify({'success': False, 'message': '；'.join(errs)}), 400


# 健康档案的医疗免责提示。前端不写死医学文案（跟 guidance_data.py / ASQ 一个规矩），
# 由这里出、由 /api/health/notice 发，前端只负责渲染。
HEALTH_NOTICE = {
    "title": "健康档案只作家庭记录，不能代替医生诊断",
    "body": (
        "这里保存的是你在医院和家里的观察记录（体检、筛查、过敏、用药、发育里程碑），"
        "仅供日常回顾和跟医生沟通时参考，不能代替医生的专业判断。"
    ),
    "advice": (
        "看到异常指标、发育落后或孩子有明显不适，请尽快到正规医院儿科或儿童保健科就诊；"
        "呼吸困难、抽搐、意识不清、全身皮疹伴腹痛等紧急情况请立即拨打 120。"
    ),
    "scopes": ("体检", "筛查", "过敏", "用药", "里程碑", "ASQ"),
}


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

        # 最近体温 / 是否发热（38.0 起算发热，概览一眼能看到）
        # 表名是 temperature_records（不是 temperatures）
        latest_temp = db.execute(
            "SELECT temperature, measure_time, measure_method, is_fever FROM temperature_records "
            "WHERE baby_id = ? ORDER BY replace(measure_time, 'T', ' ') DESC LIMIT 1",
            (baby_id,),
        ).fetchone()
        latest_temperature = (
            float(latest_temp["temperature"])
            if latest_temp and latest_temp["temperature"] is not None else None
        )
        temperature_time = latest_temp["measure_time"] if latest_temp else None
        # 优先信库里的 is_fever 标记；没有就按 38.0 阈值现算
        if latest_temp is not None and latest_temp["is_fever"] is not None:
            has_fever = bool(int(latest_temp["is_fever"]))
        else:
            has_fever = bool(latest_temperature is not None and latest_temperature >= 38.0)

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
                'latest_temperature': latest_temperature,
                'temperature_time': temperature_time,
                'has_fever': has_fever,
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
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'success': False, 'message': '无效数据'}), 400
    if not data.get('record_date') or not data.get('title'):
        return jsonify({'success': False, 'message': '请填写日期和标题'}), 400

    db = get_db()
    # 体检日期是 NOT NULL 列：缺失会让 sqlite 抛 IntegrityError（前端拿到的是 500 而不是人话）。
    # 顺带给 baby_id 和全部数值/枚举字段过一遍校验，脏数据不落库。
    ok, msg = _check_baby_exists(db, baby_id)
    if not ok:
        return jsonify({'success': False, 'message': msg}), 404
    errs = []
    _check_field(data, 'record_date', errs, '体检日期')
    _check_dates(data, ('next_visit_date',), errs)
    _check_numbers(data, ('temperature', 'height', 'weight',
                          'head_circumference', 'bmi', 'age_months'), errs)
    _check_enum(data, 'record_type', errs, _E_RECORD_TYPES, '记录类型')
    if errs:
        return _errs_to_response(errs)

    cursor = db.execute(
        """INSERT INTO health_records
           (baby_id, record_date, record_type, title, hospital, doctor,
            height, weight, head_circumference, bmi,
            heart_result, lung_result, abdomen_result, skin_result,
            bone_result, hearing_result, vision_result, blood_result,
            urine_result, other_exam, diagnosis, advice, next_visit_date, note, author)
           VALUES (?, ?, ?, ?, ?, ?,  -- baby_id, record_date, record_type, title, hospital, doctor
                   ?, ?, ?, ?,        -- height, weight, head_circumference, bmi
                   ?, ?, ?, ?,        -- heart_result, lung_result, abdomen_result, skin_result
                   ?, ?, ?, ?,        -- bone_result, hearing_result, vision_result, blood_result
                   ?, ?, ?, ?,        -- urine_result, other_exam, diagnosis, advice
                   ?, ?, ?)           -- next_visit_date, note, author""",
        (baby_id, data['record_date'], data.get('record_type', 'routine'),
         data['title'], data.get('hospital', ''), data.get('doctor', ''),
         data.get('height'), data.get('weight'), data.get('head_circumference'),
         data.get('bmi'), data.get('heart_result', ''), data.get('lung_result', ''),
         data.get('abdomen_result', ''), data.get('skin_result', ''),
         data.get('bone_result', ''), data.get('hearing_result', ''),
         data.get('vision_result', ''), data.get('blood_result', ''),
         data.get('urine_result', ''), data.get('other_exam', ''),
         data.get('diagnosis', ''), data.get('advice', ''),
         data.get('next_visit_date'), data.get('note', ''), normalize_author(data))
    )
    record_id = cursor.lastrowid

    # 处理关联指标：指标值必须是数字、判定必须在白名单里（否则整条体检存进去也是脏数据）
    indicators = []
    for ind in data.get('indicators', []) or []:
        if not isinstance(ind, dict) or not ind.get('indicator_name'):
            continue
        ind_val, ind_err = _validate_number(ind.get('value'), None, None, '指标值')
        if ind_err:
            return jsonify({'success': False, 'message': f"{ind['indicator_name']}：{ind_err}"}), 400
        ok_enum, _, ind_status_err = _validate_enum(
            ind.get('result_status') or 'normal', _E_INDICATOR_STATUS, '结果判定')
        if not ok_enum:
            return jsonify({'success': False, 'message':
                            f"{ind['indicator_name']}：{ind_status_err}"}), 400
        indicators.append((ind, ind_val))
    for ind, ind_val in indicators:
        db.execute(
            """INSERT INTO health_indicators
               (record_id, baby_id, indicator_name, indicator_code, value, unit,
                reference_low, reference_high, reference_text, result_status, record_date, note)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (record_id, baby_id, ind['indicator_name'], ind.get('indicator_code', ''),
             ind_val, ind.get('unit', ''), ind.get('reference_low'),
             ind.get('reference_high'), ind.get('reference_text', ''),
             ind.get('result_status') or 'normal', data['record_date'], ind.get('note', ''))
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
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'success': False, 'message': '无效数据'}), 400

    # 记录不存在就别往下走：此前 UPDATE 命中 0 行也返回 200「记录已更新」，
    # 前端以为改成功了、一刷新还是原样 —— 跟 DELETE 那边同一类假成功。
    # 顺带把记录的 baby_id 取出来：下面是重写 indicators 用的，必须认「记录自己属于谁」，
    # 不能认请求体里那个 baby_id（客户端不传就是 None，指标会从此在按 baby_id 过滤的
    # 指标列表里查不到；传别的宝宝的 id 更是把指标挂到别人名下）。
    row = db.execute('SELECT baby_id FROM health_records WHERE id = ?', (record_id,)).fetchone()
    if not row:
        return jsonify({'success': False, 'message': '记录不存在'}), 404
    rec_baby_id = row['baby_id']

    # 改体检同样要过闸：此前 PUT 分支完全裸奔，把「2026-13-40」也能原样 UPDATE 进去，
    # 而且 record_date 改成空串会直接踩 NOT NULL 报 500。
    errs = []
    if data.get('record_date') is not None:
        _check_field(data, 'record_date', errs, '体检日期')
    _check_dates(data, ('next_visit_date',), errs)
    _check_numbers(data, ('temperature', 'height', 'weight',
                          'head_circumference', 'bmi', 'age_months'), errs)
    _check_enum(data, 'record_type', errs, _E_RECORD_TYPES, '记录类型')
    if errs:
        return _errs_to_response(errs)

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

    # 如果提供了 indicators，先删除旧的再插入新的。
    # 删除与重插必须在一个事务里：中途出错要整体回滚，否则旧指标已经被删、新指标没插上，
    # 等于「改一下指标反而把原有指标弄丢了」。
    if 'indicators' in data:
        # 改指标同样校验，否则「指标值填abc」会在这里被 float() 顶出一个 500
        ind_records = []
        for ind in data['indicators'] or []:
            if not isinstance(ind, dict) or not ind.get('indicator_name'):
                continue
            ind_val, ind_err = _validate_number(ind.get('value'), None, None, '指标值')
            if ind_err:
                return jsonify({'success': False, 'message': f"{ind['indicator_name']}：{ind_err}"}), 400
            ok_enum, _, ind_status_err = _validate_enum(
                ind.get('result_status') or 'normal', _E_INDICATOR_STATUS, '结果判定')
            if not ok_enum:
                return jsonify({'success': False, 'message':
                                f"{ind['indicator_name']}：{ind_status_err}"}), 400
            ind_records.append((ind, ind_val))
        try:
            db.execute('DELETE FROM health_indicators WHERE record_id = ?', (record_id,))
            for ind, ind_val in ind_records:
                db.execute(
                    """INSERT INTO health_indicators
                       (record_id, baby_id, indicator_name, indicator_code, value, unit,
                        reference_low, reference_high, reference_text, result_status, record_date, note)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (record_id, rec_baby_id, ind['indicator_name'],
                     ind.get('indicator_code', ''), ind_val,
                     ind.get('unit', ''), ind.get('reference_low'),
                     ind.get('reference_high'), ind.get('reference_text', ''),
                     ind.get('result_status') or 'normal',
                     data.get('record_date', datetime.date.today().strftime('%Y-%m-%d')),
                     ind.get('note', ''))
                )
        except Exception as e:
            db.rollback()
            import logging
            logging.getLogger(__name__).error(
                "health_record_update indicators error: %s", e, exc_info=True)
            return jsonify({'success': False, 'message': '更新指标失败'}), 500

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
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not data.get('title'):
        return jsonify({'success': False, 'message': '请填写标题'}), 400

    db = get_db()
    # due_date 是 NOT NULL 列：不校验的话，前端空着提交就是 IntegrityError（500）
    ok, msg = _check_baby_exists(db, baby_id)
    if not ok:
        return jsonify({'success': False, 'message': msg}), 404
    errs = []
    _check_field(data, 'due_date', errs, '到期日期')
    _check_enum(data, 'reminder_type', errs, _E_REMINDER_TYPES, '提醒类型')
    if errs:
        return _errs_to_response(errs)

    db.execute(
        """INSERT INTO health_reminders
           (baby_id, reminder_type, title, description, due_date, note)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (baby_id, data.get('reminder_type', 'custom'), data['title'],
         data.get('description', ''), data.get('due_date'), data.get('note', ''))
    )
    db.commit()
    return jsonify({'success': True, 'message': '提醒已添加'})


@bp.route('/api/health/reminders/detail/<int:reminder_id>', methods=['PUT'])
def health_reminder_update(reminder_id):
    """更新健康提醒（含标记完成）"""
    db = get_db()
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'success': False, 'message': '无效数据'}), 400

    errs = []
    _check_field(data, 'due_date', errs, '到期日期')
    _check_enum(data, 'reminder_type', errs, _E_REMINDER_TYPES, '提醒类型')
    if errs:
        return _errs_to_response(errs)

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
    cur = db.execute(f'UPDATE health_reminders SET {", ".join(fields)} WHERE id = ?', values)
    if cur.rowcount == 0:
        db.rollback()
        return jsonify({'success': False, 'message': '提醒不存在'}), 404
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
    ok, msg = _check_baby_exists(db, baby_id)
    if not ok:
        return jsonify({'success': False, 'message': msg}), 404
    errs = []
    _check_dates(data, ('achieved_date', 'expected_date'), errs)
    _check_numbers(data, ('expected_age_months', 'actual_age_months'), errs)
    _check_enum(data, 'category', errs, _E_MILESTONE_CATEGORIES, '里程碑分类')
    _check_enum(data, 'status', errs, _E_MILESTONE_STATUS, '里程碑状态')
    if errs:
        return _errs_to_response(errs)
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
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'success': False, 'message': '无效数据'}), 400

    errs = []
    _check_dates(data, ('achieved_date', 'expected_date'), errs)
    _check_numbers(data, ('expected_age_months', 'actual_age_months'), errs)
    _check_enum(data, 'category', errs, _E_MILESTONE_CATEGORIES, '里程碑分类')
    _check_enum(data, 'status', errs, _E_MILESTONE_STATUS, '里程碑状态')
    if errs:
        return _errs_to_response(errs)

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
    cur = db.execute(f'UPDATE milestone_details SET {", ".join(fields)} WHERE id = ?', values)
    if cur.rowcount == 0:
        db.rollback()
        return jsonify({'success': False, 'message': '里程碑不存在'}), 404
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
    ok, msg = _check_baby_exists(db, baby_id)
    if not ok:
        return jsonify({'success': False, 'message': msg}), 404
    # screening_date 是 NOT NULL 列：不校验就是 IntegrityError（500），
    # 而且「2026/10/01」这种写法能原样落进库，列表按日期排序时全部沉底。
    errs = []
    _check_field(data, 'screening_date', errs, '筛查日期')
    _check_dates(data, ('next_screening_date',), errs)
    _check_numbers(data, ('age_months',), errs)
    _check_enum(data, 'screening_type', errs, _E_SCREENING_TYPES, '筛查类型')
    _check_enum(data, 'result_status', errs, _E_SCREENING_STATUS, '结果判定')
    if errs:
        return _errs_to_response(errs)
    db.execute(
        """INSERT INTO screenings
           (baby_id, screening_type, screening_date, age_months, screening_method,
            hospital, doctor, result_summary, result_status, detail_left, detail_right,
            referral_needed, referral_done, next_screening_date, note)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (baby_id, data['screening_type'],
         data.get('screening_date'),
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
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'success': False, 'message': '无效数据'}), 400

    errs = []
    _check_field(data, 'screening_date', errs, '筛查日期')
    _check_dates(data, ('next_screening_date',), errs)
    _check_numbers(data, ('age_months',), errs)
    _check_enum(data, 'screening_type', errs, _E_SCREENING_TYPES, '筛查类型')
    _check_enum(data, 'result_status', errs, _E_SCREENING_STATUS, '结果判定')
    if errs:
        return _errs_to_response(errs)

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
    cur = db.execute(f'UPDATE screenings SET {", ".join(fields)} WHERE id = ?', values)
    if cur.rowcount == 0:
        db.rollback()
        return jsonify({'success': False, 'message': '筛查记录不存在'}), 404
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
    ok, msg = _check_baby_exists(db, baby_id)
    if not ok:
        return jsonify({'success': False, 'message': msg}), 404
    errs = []
    _check_dates(data, ('first_occurrence_date', 'last_occurrence_date'), errs)
    _check_enum(data, 'allergen_type', errs, _E_ALLERGY_TYPES, '过敏原类型')
    _check_enum(data, 'severity_level', errs, _E_ALLERGY_SEVERITY, '严重程度')
    _check_enum(data, 'status', errs, _E_ALLERGY_STATUS, '过敏状态')
    if errs:
        return _errs_to_response(errs)
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
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'success': False, 'message': '无效数据'}), 400

    errs = []
    _check_dates(data, ('first_occurrence_date', 'last_occurrence_date'), errs)
    _check_enum(data, 'allergen_type', errs, _E_ALLERGY_TYPES, '过敏原类型')
    _check_enum(data, 'severity_level', errs, _E_ALLERGY_SEVERITY, '严重程度')
    _check_enum(data, 'status', errs, _E_ALLERGY_STATUS, '过敏状态')
    if errs:
        return _errs_to_response(errs)

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
    cur = db.execute(f'UPDATE allergy_history SET {", ".join(fields)} WHERE id = ?', values)
    if cur.rowcount == 0:
        db.rollback()
        return jsonify({'success': False, 'message': '过敏记录不存在'}), 404
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
    ok, msg = _check_baby_exists(db, baby_id)
    if not ok:
        return jsonify({'success': False, 'message': msg}), 404
    # record_month 是 NOT NULL 且参与 UNIQUE(baby_id, record_month)：
    # 「2026/10」这种写法会当成另一个月份插进去，同一个月变两条，列表和统计全对不上。
    errs = []
    _check_field(data, 'record_month', errs, '月份', kind='month')
    _check_numbers(data, ('avg_daily_ml', 'feeding_frequency', 'solid_food_count',
                          'weight_gain_month', 'height_gain_month'), errs)
    _check_enum(data, 'feeding_type', errs, _E_FEEDING_TYPES, '喂养方式')
    if errs:
        return _errs_to_response(errs)
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
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'success': False, 'message': '无效数据'}), 400

    errs = []
    _check_field(data, 'record_month', errs, '月份', kind='month')
    _check_numbers(data, ('avg_daily_ml', 'feeding_frequency', 'solid_food_count',
                          'weight_gain_month', 'height_gain_month'), errs)
    _check_enum(data, 'feeding_type', errs, _E_FEEDING_TYPES, '喂养方式')
    if errs:
        return _errs_to_response(errs)

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
    cur = db.execute(f'UPDATE feeding_summary SET {", ".join(fields)} WHERE id = ?', values)
    if cur.rowcount == 0:
        db.rollback()
        return jsonify({'success': False, 'message': '喂养摘要不存在'}), 404
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


# ==================== 医疗免责提示 ====================

@bp.route('/api/health/notice')
def health_notice():
    """健康档案的统一免责提示。

    体检、筛查、过敏、用药这几类进来的是医院真实结果，APP 只做记录不做判读。
    界面上必须常驻一句「不能代替医生诊断 / 异常请就医」，否则家长会把自己记的数字
    当成结论。文案由后端出（跟 guidance_data.py 一个规矩），前端只负责渲染。
    """
    return jsonify({'success': True, 'notice': HEALTH_NOTICE})


# 注意：曾有一个重复注册的 POST /api/health/records/<baby_id>「增强版」实现，
# 与上方 health_record_create 路由冲突且使用错误列名 birth_date（实际为 birthday），
# 属于永不可达的死代码，已移除（路由冲突修复）。


# ==================== 健康随访规划（0-6 岁儿童健康随访预约卡） ====================
# 既有的体检记录是「回顾」（去过哪），这里补「规划」（该什么时候去）。
# 数据基于《0-6 岁儿童健康随访预约卡》的常规安排，
# 只提示不判读：建议日期 = 出生 + 月龄 × 30.44 天，实际以社区/妇幼安排为准。
# premature_only=True 的是「高危早产儿增加的随访」，仅 is_premature=1 的宝宝可见。
FOLLOWUP_SCHEDULE = [
    # 常规随访（所有儿童）
    {'label': '1月龄', 'age_months': 1, 'premature_only': False, 'location': '社区（儿童健康管理建档）'},
    {'label': '3月龄', 'age_months': 3, 'premature_only': False, 'location': '社区、区妇幼、市妇幼'},
    {'label': '6月龄', 'age_months': 6, 'premature_only': False, 'location': '社区、区妇幼、市妇幼'},
    {'label': '8月龄', 'age_months': 8, 'premature_only': False, 'location': '社区、区妇幼、市妇幼'},
    {'label': '12月龄', 'age_months': 12, 'premature_only': False, 'location': '社区、区妇幼、市妇幼'},
    {'label': '18月龄', 'age_months': 18, 'premature_only': False, 'location': '社区、区妇幼、市妇幼'},
    {'label': '24月龄', 'age_months': 24, 'premature_only': False, 'location': '社区、区妇幼、市妇幼'},
    {'label': '30月龄', 'age_months': 30, 'premature_only': False, 'location': '社区、区妇幼、市妇幼'},
    {'label': '36月龄', 'age_months': 36, 'premature_only': False, 'location': '社区、区妇幼、市妇幼'},
    {'label': '4岁', 'age_months': 48, 'premature_only': False, 'location': '社区'},
    {'label': '5岁', 'age_months': 60, 'premature_only': False, 'location': '社区'},
    {'label': '6岁', 'age_months': 72, 'premature_only': False, 'location': '社区'},
    # 高危早产儿增加的随访（灰色行，仅早产宝宝展示）
    {'label': '42天-2月龄(早产)', 'age_months': 1.4, 'premature_only': True, 'location': '社区、区妇幼、市妇幼'},
    {'label': '4月龄(早产)', 'age_months': 4, 'premature_only': True, 'location': '社区、区妇幼、市妇幼'},
    {'label': '5月龄(早产)', 'age_months': 5, 'premature_only': True, 'location': '社区、区妇幼、市妇幼'},
    {'label': '10月龄(早产)', 'age_months': 10, 'premature_only': True, 'location': '社区、区妇幼、市妇幼'},
    {'label': '15月龄(早产)', 'age_months': 15, 'premature_only': True, 'location': '社区、区妇幼、市妇幼'},
    {'label': '21月龄(早产)', 'age_months': 21, 'premature_only': True, 'location': '社区、区妇幼、市妇幼'},
]


def _followup_suggested_date(birthday, age_months):
    """建议日期 = 出生 + 月龄 × 30.44 天，四舍五入到日。生日无效返回 None。"""
    try:
        birth = datetime.datetime.strptime((birthday or '')[:10], '%Y-%m-%d').date()
    except (ValueError, TypeError):
        return None
    return birth + datetime.timedelta(days=round(age_months * 30.44))


def build_followup_items(db, baby):
    """
    构建随访清单（schedule 路由与 dashboard 共用，改口径两处一起变）。
    baby: babies 行（需 birthday / is_premature）。

    对账规则：某档的建议日期窗口 [建议-15天, 建议+60天] 内存在任何体检记录
    （health_records，_record_types 可对上随访语义的都算）→ 该档标记 done。
    体检通常允许前后浮动，窗口放宽到 ±，避免「晚了一周去」被标成未完成。
    """
    birthday = (baby['birthday'] or '') if 'birthday' in baby.keys() else ''
    is_premature = bool(baby['is_premature']) if 'is_premature' in baby.keys() else False

    plans = {
        r['age_months']: row_to_dict(r)
        for r in db.execute(
            'SELECT * FROM followup_plans WHERE baby_id = ?', (baby['id'],)
        ).fetchall()
    }
    # 一次拉全部体检日期，Python 内比对（体检记录量级很小，不值得按档查库）
    checkup_dates = []
    try:
        checkup_dates = [
            r[0][:10] for r in db.execute(
                'SELECT record_date FROM health_records WHERE baby_id = ? '
                "AND record_type IN ('routine','vaccine','other') ORDER BY record_date",
                (baby['id'],),
            ).fetchall()
        ]
    except Exception:
        pass  # 表不可用时退化为「不对账」，清单照出

    today = datetime.date.today()
    items = []
    for slot in FOLLOWUP_SCHEDULE:
        if slot['premature_only'] and not is_premature:
            continue
        suggested = _followup_suggested_date(birthday, slot['age_months'])
        plan = plans.get(slot['age_months']) or {}
        planned = plan.get('planned_date') or ''
        anchor = planned[:10] if planned else (suggested.isoformat() if suggested else '')
        days_left = None
        if anchor:
            try:
                days_left = (
                    datetime.datetime.strptime(anchor[:10], '%Y-%m-%d').date() - today
                ).days
            except (ValueError, TypeError):
                days_left = None

        # 对账：窗口内有体检记录 → 已完成
        status = plan.get('status') or 'planned'
        if suggested and checkup_dates:
            lo = (suggested - datetime.timedelta(days=15)).isoformat()
            hi = (suggested + datetime.timedelta(days=60)).isoformat()
            if any(lo <= d <= hi for d in checkup_dates):
                status = 'done'

        items.append({
            'label': slot['label'],
            'age_months': slot['age_months'],
            'location': slot['location'],
            'premature_only': slot['premature_only'],
            'suggested_date': suggested.isoformat() if suggested else None,
            'planned_date': planned[:10] if planned else None,
            'note': plan.get('note') or '',
            'days_left': days_left,
            'status': status,
        })

    items.sort(key=lambda x: (x['age_months']))
    return is_premature, items


@bp.route('/api/health/followup/schedule/<int:baby_id>', methods=['GET'])
def followup_schedule_get(baby_id):
    """获取宝宝的随访清单：每档的建议日期 + 已设预约日期 + 距今天数 + 对账状态。"""
    try:
        db = get_db()
        baby = db.execute(
            'SELECT id, birthday, is_premature FROM babies WHERE id = ?', (baby_id,)
        ).fetchone()
        if not baby:
            return jsonify({'success': False, 'message': '宝宝不存在'}), 404

        is_premature, items = build_followup_items(db, baby)
        upcoming = [i for i in items if i['status'] != 'done' and i['days_left'] is not None and i['days_left'] >= 0]
        return jsonify({
            'success': True,
            'data': {
                'is_premature': is_premature,
                'items': items,
                'next': upcoming[0] if upcoming else None,
            },
        })
    except Exception as e:
        import logging
        logging.getLogger(__name__).error("followup_schedule_get error: %s", e, exc_info=True)
        return jsonify({'success': False, 'message': '数据加载失败'}), 500


@bp.route('/api/health/followup/plan/<int:baby_id>', methods=['PUT'])
@require_not_integration
def followup_plan_put(baby_id):
    """设置/修改某档随访的预约日期（upsert）。"""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'success': False, 'message': '无效数据'}), 400
    age_months = data.get('age_months')
    if not isinstance(age_months, (int, float)) or age_months < 0 or age_months > 84:
        return jsonify({'success': False, 'message': '月龄档无效'}), 400
    known = {s['age_months'] for s in FOLLOWUP_SCHEDULE}
    if float(age_months) not in {float(k) for k in known}:
        return jsonify({'success': False, 'message': '不是预约卡内的随访档'}), 400

    planned = data.get('planned_date') or ''
    if planned:
        # _validate_date 返回 (归一化值, 错误)——归一化回写，脏格式不落库
        planned_norm, derr = _validate_date(planned, '预约日期')
        if derr or not planned_norm:
            return jsonify({'success': False, 'message': derr or '预约日期无效'}), 400
        planned = planned_norm

    db = get_db()
    if not _check_baby_exists(db, baby_id):
        return jsonify({'success': False, 'message': '宝宝不存在'}), 404
    note = (data.get('note') or '').strip()[:200]
    db.execute(
        """INSERT INTO followup_plans (baby_id, age_months, planned_date, status, note)
           VALUES (?, ?, ?, 'planned', ?)
           ON CONFLICT(baby_id, age_months)
           DO UPDATE SET planned_date = excluded.planned_date,
                         note = excluded.note,
                         updated_at = datetime('now', 'localtime')""",
        (baby_id, float(age_months), planned, note),
    )
    db.commit()
    return jsonify({'success': True, 'message': '预约已保存'})


@bp.route('/api/health/followup/plan/<int:baby_id>', methods=['DELETE'])
@require_not_integration
def followup_plan_delete(baby_id):
    """取消某档随访的预约。"""
    age_months = request.args.get('age_months', type=float)
    if age_months is None:
        return jsonify({'success': False, 'message': '缺少月龄参数'}), 400
    db = get_db()
    if not _check_baby_exists(db, baby_id):
        return jsonify({'success': False, 'message': '宝宝不存在'}), 404
    cur = db.execute(
        'DELETE FROM followup_plans WHERE baby_id = ? AND age_months = ?',
        (baby_id, age_months),
    )
    db.commit()
    return jsonify({'success': True, 'deleted': cur.rowcount})
