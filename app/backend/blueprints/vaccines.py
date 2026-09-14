#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xiaoke799
"""
疫苗管理路由 Blueprint。
提供疫苗记录增删改查、标准接种计划、疫苗详情、统计时间线等功能。

统一使用 vaccine_details 表作为数据源，支持完整的不良反应追踪、
接种单位记录、批号管理、统计分析和导出功能。
"""

import datetime
import csv
import io
from flask import Blueprint, request, jsonify, Response

from utils import get_db, row_to_dict, rows_to_list

bp = Blueprint("vaccines", __name__)


# ==================== 国家标准接种程序 ====================

def _get_standard_vaccine_schedule():
    """获取标准疫苗接种计划（按出生后天数计）

    包含国家免疫规划疫苗（一类）和推荐自费疫苗（二类），
    scheduled_days = scheduled_age_months * 30.44。
    """
    standard = [
        {'vaccine_name': '乙肝疫苗', 'dose': 1, 'scheduled_age_months': 0, 'type': 'free', 'note': '出生24h内', 'category': 'hepatitis_b'},
        {'vaccine_name': '卡介苗', 'dose': 1, 'scheduled_age_months': 0, 'type': 'free', 'note': '出生时', 'category': 'bcg'},
        {'vaccine_name': '乙肝疫苗', 'dose': 2, 'scheduled_age_months': 1, 'type': 'free', 'note': '1月龄', 'category': 'hepatitis_b'},
        {'vaccine_name': '脊灰灭活疫苗', 'dose': 1, 'scheduled_age_months': 2, 'type': 'free', 'note': '2月龄', 'category': 'polio'},
        {'vaccine_name': '脊灰减毒疫苗', 'dose': 2, 'scheduled_age_months': 3, 'type': 'free', 'note': '3月龄', 'category': 'polio'},
        {'vaccine_name': '百白破疫苗', 'dose': 1, 'scheduled_age_months': 3, 'type': 'free', 'note': '3月龄', 'category': 'dtap'},
        {'vaccine_name': '脊灰减毒疫苗', 'dose': 3, 'scheduled_age_months': 4, 'type': 'free', 'note': '4月龄', 'category': 'polio'},
        {'vaccine_name': '百白破疫苗', 'dose': 2, 'scheduled_age_months': 4, 'type': 'free', 'note': '4月龄', 'category': 'dtap'},
        {'vaccine_name': '百白破疫苗', 'dose': 3, 'scheduled_age_months': 5, 'type': 'free', 'note': '5月龄', 'category': 'dtap'},
        {'vaccine_name': '乙肝疫苗', 'dose': 3, 'scheduled_age_months': 6, 'type': 'free', 'note': '6月龄', 'category': 'hepatitis_b'},
        {'vaccine_name': 'A群流脑多糖疫苗', 'dose': 1, 'scheduled_age_months': 6, 'type': 'free', 'note': '6月龄', 'category': 'meningitis_a'},
        {'vaccine_name': '麻腮风疫苗', 'dose': 1, 'scheduled_age_months': 8, 'type': 'free', 'note': '8月龄', 'category': 'mmr'},
        {'vaccine_name': '乙脑减毒活疫苗', 'dose': 1, 'scheduled_age_months': 8, 'type': 'free', 'note': '8月龄', 'category': 'je'},
        {'vaccine_name': 'A群流脑多糖疫苗', 'dose': 2, 'scheduled_age_months': 9, 'type': 'free', 'note': '9月龄', 'category': 'meningitis_a'},
        {'vaccine_name': '甲肝灭活疫苗', 'dose': 1, 'scheduled_age_months': 18, 'type': 'free', 'note': '18月龄', 'category': 'hepatitis_a'},
        {'vaccine_name': '百白破疫苗', 'dose': 4, 'scheduled_age_months': 18, 'type': 'free', 'note': '18月龄', 'category': 'dtap'},
        {'vaccine_name': '麻腮风疫苗', 'dose': 2, 'scheduled_age_months': 18, 'type': 'free', 'note': '18月龄', 'category': 'mmr'},
        {'vaccine_name': '甲肝灭活疫苗', 'dose': 2, 'scheduled_age_months': 24, 'type': 'free', 'note': '2岁', 'category': 'hepatitis_a'},
        {'vaccine_name': '乙脑减毒活疫苗', 'dose': 2, 'scheduled_age_months': 24, 'type': 'free', 'note': '2岁', 'category': 'je'},
        {'vaccine_name': 'A+C群流脑多糖疫苗', 'dose': 1, 'scheduled_age_months': 36, 'type': 'free', 'note': '3岁', 'category': 'meningitis_ac'},
        {'vaccine_name': '脊灰减毒疫苗', 'dose': 4, 'scheduled_age_months': 48, 'type': 'free', 'note': '4岁', 'category': 'polio'},
        {'vaccine_name': 'A+C群流脑多糖疫苗', 'dose': 2, 'scheduled_age_months': 72, 'type': 'free', 'note': '6岁', 'category': 'meningitis_ac'},
        {'vaccine_name': '白破疫苗', 'dose': 1, 'scheduled_age_months': 72, 'type': 'free', 'note': '6岁', 'category': 'td'},
    ]
    result = []
    for v in standard:
        result.append({
            'vaccine_name': v['vaccine_name'],
            'vaccine_type': v['type'],
            'dose_number': v['dose'],
            'scheduled_days': int(v['scheduled_age_months'] * 30.44),
            'note': v.get('note', ''),
            'category': v.get('category', ''),
        })
    return result


def _get_recommended_vaccines():
    """获取推荐自费疫苗接种计划"""
    return [
        {'vaccine_name': '五联疫苗', 'dose': 1, 'scheduled_age_months': 2, 'type': 'paid', 'note': '可选替代脊灰+百白破+Hib', 'category': 'pentavalent'},
        {'vaccine_name': '五联疫苗', 'dose': 2, 'scheduled_age_months': 4, 'type': 'paid', 'note': '', 'category': 'pentavalent'},
        {'vaccine_name': '五联疫苗', 'dose': 3, 'scheduled_age_months': 6, 'type': 'paid', 'note': '', 'category': 'pentavalent'},
        {'vaccine_name': '五联疫苗', 'dose': 4, 'scheduled_age_months': 18, 'type': 'paid', 'note': '', 'category': 'pentavalent'},
        {'vaccine_name': '13价肺炎疫苗', 'dose': 1, 'scheduled_age_months': 2, 'type': 'paid', 'note': '', 'category': 'pcv13'},
        {'vaccine_name': '13价肺炎疫苗', 'dose': 2, 'scheduled_age_months': 4, 'type': 'paid', 'note': '', 'category': 'pcv13'},
        {'vaccine_name': '13价肺炎疫苗', 'dose': 3, 'scheduled_age_months': 6, 'type': 'paid', 'note': '', 'category': 'pcv13'},
        {'vaccine_name': '13价肺炎疫苗', 'dose': 4, 'scheduled_age_months': 12, 'type': 'paid', 'note': '', 'category': 'pcv13'},
        {'vaccine_name': '五价轮状病毒疫苗', 'dose': 1, 'scheduled_age_months': 2, 'type': 'paid', 'note': '口服', 'category': 'rotavirus'},
        {'vaccine_name': '五价轮状病毒疫苗', 'dose': 2, 'scheduled_age_months': 3, 'type': 'paid', 'note': '口服', 'category': 'rotavirus'},
        {'vaccine_name': '五价轮状病毒疫苗', 'dose': 3, 'scheduled_age_months': 4, 'type': 'paid', 'note': '口服', 'category': 'rotavirus'},
        {'vaccine_name': 'Hib疫苗', 'dose': 1, 'scheduled_age_months': 2, 'type': 'paid', 'note': '', 'category': 'hib'},
        {'vaccine_name': 'Hib疫苗', 'dose': 2, 'scheduled_age_months': 4, 'type': 'paid', 'note': '', 'category': 'hib'},
        {'vaccine_name': 'Hib疫苗', 'dose': 3, 'scheduled_age_months': 6, 'type': 'paid', 'note': '', 'category': 'hib'},
        {'vaccine_name': 'Hib疫苗', 'dose': 4, 'scheduled_age_months': 18, 'type': 'paid', 'note': '', 'category': 'hib'},
        {'vaccine_name': '水痘疫苗', 'dose': 1, 'scheduled_age_months': 12, 'type': 'paid', 'note': '', 'category': 'varicella'},
        {'vaccine_name': '水痘疫苗', 'dose': 2, 'scheduled_age_months': 48, 'type': 'paid', 'note': '', 'category': 'varicella'},
        {'vaccine_name': '流感疫苗', 'dose': 1, 'scheduled_age_months': 6, 'type': 'paid', 'note': '每年秋季', 'category': 'flu'},
        {'vaccine_name': '手足口疫苗(EV71)', 'dose': 1, 'scheduled_age_months': 6, 'type': 'paid', 'note': '', 'category': 'ev71'},
        {'vaccine_name': '手足口疫苗(EV71)', 'dose': 2, 'scheduled_age_months': 7, 'type': 'paid', 'note': '', 'category': 'ev71'},
        {'vaccine_name': '23价肺炎疫苗', 'dose': 1, 'scheduled_age_months': 24, 'type': 'paid', 'note': '2岁以上高危人群', 'category': 'ppsv23'},
    ]


def _format_age_display(age_months):
    """格式化月龄显示"""
    if age_months < 1:
        return f"{int(age_months * 30.44)}天"
    years = int(age_months // 12)
    months = int(age_months % 12)
    if years == 0:
        return f"{months}月龄"
    elif months == 0:
        return f"{years}岁"
    return f"{years}岁{months}月"


def _compute_age_months(baby):
    """根据宝宝生日计算当前月龄"""
    if not baby or not baby.get('birthday'):
        return 0
    try:
        birthday = datetime.datetime.strptime(baby['birthday'], '%Y-%m-%d').date()
        today = datetime.date.today()
        delta = (today - birthday).days
        return delta / 30.44
    except (ValueError, TypeError):
        return 0


# ==================== API: 疫苗记录 CRUD ====================

@bp.route('/api/babies/<int:baby_id>/vaccines', methods=['POST'])
def add_vaccine(baby_id):
    """添加疫苗记录（写入 vaccine_details 表）"""
    data = request.get_json()
    if not data or not data.get('vaccine_name'):
        return jsonify({'success': False, 'message': '请填写疫苗名称'}), 400

    db = get_db()
    db.execute(
        '''INSERT INTO vaccine_details
           (baby_id, vaccine_name, vaccine_type, dose_number, scheduled_date, actual_date,
            status, manufacturer, batch_number, hospital, doctor, injection_site,
            has_reaction, reaction_detail, reaction_severity, next_dose_date,
            antibody_test_date, antibody_result, note)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        (baby_id, data['vaccine_name'], data.get('vaccine_type', 'free'),
         data.get('dose_number', 1), data.get('scheduled_date'),
         data.get('actual_date'), data.get('status', 'pending'),
         data.get('manufacturer', ''), data.get('batch_number', ''),
         data.get('hospital', ''), data.get('doctor', ''),
         data.get('injection_site', ''), 1 if data.get('has_reaction') else 0,
         data.get('reaction_detail', ''), data.get('reaction_severity', 'none'),
         data.get('next_dose_date'), data.get('antibody_test_date', ''),
         data.get('antibody_result', ''), data.get('note', ''))
    )
    db.commit()
    return jsonify({'success': True, 'message': '疫苗记录已添加'})


@bp.route('/api/vaccines/<int:vaccine_id>', methods=['DELETE'])
def delete_vaccine(vaccine_id):
    """删除疫苗记录"""
    db = get_db()
    db.execute('DELETE FROM vaccine_details WHERE id = ?', (vaccine_id,))
    db.commit()
    return jsonify({'success': True, 'message': '已删除'})


@bp.route('/api/babies/<int:baby_id>/vaccines/init', methods=['POST'])
def init_vaccine_schedule(baby_id):
    """根据宝宝生日自动生成疫苗接种计划（写入 vaccine_details 表）"""
    baby = get_db().execute('SELECT * FROM babies WHERE id = ?', (baby_id,)).fetchone()
    if not baby:
        return jsonify({'success': False, 'message': '宝宝不存在'}), 404

    # 检查是否已有疫苗记录（两个表都检查）
    existing = get_db().execute('SELECT COUNT(*) FROM vaccine_details WHERE baby_id = ?', (baby_id,)).fetchone()[0]
    if existing > 0:
        return jsonify({'success': False, 'message': '已存在疫苗记录，不会覆盖'}), 400

    birthday = datetime.datetime.strptime(baby['birthday'], '%Y-%m-%d').date()
    schedule = _get_standard_vaccine_schedule()
    db = get_db()

    for v in schedule:
        scheduled = (birthday + datetime.timedelta(days=v['scheduled_days'])).strftime('%Y-%m-%d')
        db.execute(
            '''INSERT INTO vaccine_details
               (baby_id, vaccine_name, vaccine_type, dose_number, scheduled_date, status, note)
               VALUES (?, ?, ?, ?, ?, ?, ?)''',
            (baby_id, v['vaccine_name'], v['vaccine_type'], v['dose_number'],
             scheduled, 'pending', v.get('note', ''))
        )

    db.commit()
    return jsonify({'success': True, 'message': f'已生成 {len(schedule)} 条疫苗接种计划'})


@bp.route('/api/babies/<int:baby_id>/vaccines', methods=['GET'])
def get_vaccine_list(baby_id):
    """获取疫苗记录列表（从 vaccine_details 表读取）"""
    db = get_db()
    rows = db.execute(
        'SELECT * FROM vaccine_details WHERE baby_id = ? ORDER BY scheduled_date',
        (baby_id,)
    ).fetchall()
    return jsonify({'success': True, 'data': rows_to_list(rows)})


@bp.route('/api/babies/<int:baby_id>/vaccines/status', methods=['GET'])
def get_vaccine_status(baby_id):
    """获取疫苗接种完成状态（合并用户记录和标准计划）"""
    db = get_db()
    baby = db.execute('SELECT * FROM babies WHERE id = ?', (baby_id,)).fetchone()
    if not baby:
        return jsonify({'success': False, 'message': '宝宝不存在'}), 404

    today = datetime.date.today()
    today_str = today.strftime('%Y-%m-%d')

    # 获取用户实际接种记录（从 vaccine_details 表）
    user_vaccines = db.execute(
        'SELECT * FROM vaccine_details WHERE baby_id = ? ORDER BY scheduled_date',
        (baby_id,)
    ).fetchall()
    user_vaccines_list = rows_to_list(user_vaccines)

    # 统计
    total_scheduled = len(user_vaccines_list)
    completed = sum(1 for v in user_vaccines_list if v.get('status') == 'completed')
    pending = sum(1 for v in user_vaccines_list if v.get('status') == 'pending')
    delayed = sum(1 for v in user_vaccines_list if v.get('status') == 'delayed')
    overdue = sum(1 for v in user_vaccines_list
                  if v.get('status') in ('pending', 'delayed')
                  and v.get('scheduled_date', '') < today_str)

    # 即将接种（未来30天内）
    upcoming_deadline = (today + datetime.timedelta(days=30)).strftime('%Y-%m-%d')
    upcoming = [v for v in user_vaccines_list
                if v.get('status') in ('pending', 'delayed')
                and today_str <= v.get('scheduled_date', '') <= upcoming_deadline]

    # 计算月龄
    age_months = _compute_age_months(baby)

    return jsonify({
        'success': True,
        'data': {
            'total': total_scheduled,
            'completed': completed,
            'pending': pending,
            'delayed': delayed,
            'overdue': overdue,
            'completion_rate': round(completed / total_scheduled * 100, 1) if total_scheduled > 0 else 0,
            'upcoming': upcoming,
            'age_months': round(age_months, 1),
            'all_records': user_vaccines_list
        }
    })


@bp.route('/api/vaccines/<int:vaccine_id>', methods=['PUT'])
def update_vaccine(vaccine_id):
    """更新疫苗记录"""
    db = get_db()
    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'message': '无效数据'}), 400
    fields = []
    values = []
    for key in ('vaccine_name', 'vaccine_type', 'dose_number', 'scheduled_date', 'actual_date',
                'status', 'manufacturer', 'batch_number', 'hospital', 'doctor',
                'injection_site', 'has_reaction', 'reaction_detail', 'reaction_severity',
                'next_dose_date', 'antibody_test_date', 'antibody_result', 'note'):
        if key in data:
            fields.append(f'{key} = ?')
            values.append(data[key])
    if not fields:
        return jsonify({'success': False, 'message': '无更新字段'}), 400
    values.append(vaccine_id)
    db.execute(f'UPDATE vaccine_details SET {", ".join(fields)} WHERE id = ?', values)
    db.commit()
    return jsonify({'success': True, 'message': '疫苗记录已更新'})


# ==================== API: 疫苗统计与时间线 ====================

@bp.route('/api/babies/<int:baby_id>/vaccines/timeline', methods=['GET'])
def vaccine_timeline(baby_id):
    """获取疫苗接种时间线（按月龄分组）"""
    db = get_db()
    baby = db.execute('SELECT * FROM babies WHERE id = ?', (baby_id,)).fetchone()
    if not baby:
        return jsonify({'success': False, 'message': '宝宝不存在'}), 404

    birthday = datetime.datetime.strptime(baby['birthday'], '%Y-%m-%d').date()
    today = datetime.date.today()

    records = db.execute(
        '''SELECT * FROM vaccine_details WHERE baby_id = ?
           ORDER BY COALESCE(actual_date, scheduled_date)''',
        (baby_id,)
    ).fetchall()
    records_list = rows_to_list(records)

    # 按接种日期分组构建时间线
    timeline = []
    for r in records_list:
        date_str = r.get('actual_date') or r.get('scheduled_date')
        if not date_str:
            continue
        try:
            record_date = datetime.datetime.strptime(date_str[:10], '%Y-%m-%d').date()
            age_days = (record_date - birthday).days
            age_m = age_days / 30.44
            age_display = _format_age_display(age_m)
        except (ValueError, TypeError):
            age_display = ''

        timeline.append({
            'id': r['id'],
            'vaccine_name': r['vaccine_name'],
            'dose_number': r['dose_number'],
            'vaccine_type': r['vaccine_type'],
            'date': date_str,
            'age_display': age_display,
            'status': r['status'],
            'hospital': r.get('hospital', ''),
            'manufacturer': r.get('manufacturer', ''),
            'batch_number': r.get('batch_number', ''),
            'has_reaction': bool(r.get('has_reaction')),
            'reaction_detail': r.get('reaction_detail', ''),
            'injection_site': r.get('injection_site', ''),
        })

    return jsonify({'success': True, 'data': timeline})


@bp.route('/api/babies/<int:baby_id>/vaccines/stats', methods=['GET'])
def vaccine_stats(baby_id):
    """获取疫苗接种统计（按类别分组）"""
    db = get_db()
    records = db.execute(
        'SELECT * FROM vaccine_details WHERE baby_id = ?', (baby_id,)
    ).fetchall()
    records_list = rows_to_list(records)

    # 按疫苗名称分组统计
    by_vaccine = {}
    for r in records_list:
        name = r['vaccine_name']
        if name not in by_vaccine:
            by_vaccine[name] = {'name': name, 'type': r.get('vaccine_type', 'free'), 'doses': []}
        by_vaccine[name]['doses'].append(r)

    # 计算每种的完成度
    vaccine_summary = []
    for name, info in by_vaccine.items():
        doses = sorted(info['doses'], key=lambda x: x.get('dose_number', 0))
        completed_doses = sum(1 for d in doses if d.get('status') == 'completed')
        total_doses = len(doses)
        vaccine_summary.append({
            'name': name,
            'type': info['type'],
            'total_doses': total_doses,
            'completed_doses': completed_doses,
            'completion_rate': round(completed_doses / total_doses * 100, 1) if total_doses > 0 else 0,
            'last_dose_date': doses[-1].get('actual_date', '') if doses else '',
            'has_reaction': any(d.get('has_reaction') for d in doses),
        })

    # 按类型统计
    free_vaccines = [v for v in vaccine_summary if v['type'] == 'free']
    paid_vaccines = [v for v in vaccine_summary if v['type'] == 'paid']

    return jsonify({
        'success': True,
        'data': {
            'by_vaccine': vaccine_summary,
            'free': {
                'total': len(free_vaccines),
                'completed': sum(1 for v in free_vaccines if v['completion_rate'] >= 100),
                'with_reaction': sum(1 for v in free_vaccines if v['has_reaction']),
            },
            'paid': {
                'total': len(paid_vaccines),
                'completed': sum(1 for v in paid_vaccines if v['completion_rate'] >= 100),
                'with_reaction': sum(1 for v in paid_vaccines if v['has_reaction']),
            },
            'total_doses': len(records_list),
            'total_completed': sum(1 for r in records_list if r.get('status') == 'completed'),
        }
    })


# ==================== API: 疫苗记录导出 ====================

@bp.route('/api/babies/<int:baby_id>/vaccines/export', methods=['GET'])
def export_vaccines(baby_id):
    """导出疫苗记录（CSV 或 JSON 格式）"""
    fmt = request.args.get('format', 'csv')
    db = get_db()
    baby = db.execute('SELECT * FROM babies WHERE id = ?', (baby_id,)).fetchone()
    if not baby:
        return jsonify({'success': False, 'message': '宝宝不存在'}), 404

    records = db.execute(
        '''SELECT vaccine_name, vaccine_type, dose_number, scheduled_date, actual_date,
                  status, manufacturer, batch_number, hospital, doctor, injection_site,
                  has_reaction, reaction_detail, reaction_severity, note
           FROM vaccine_details WHERE baby_id = ?
           ORDER BY scheduled_date''',
        (baby_id,)
    ).fetchall()
    records_list = rows_to_list(records)

    baby_name = baby.get('name', '宝宝')

    if fmt == 'json':
        return jsonify({
            'success': True,
            'baby_name': baby_name,
            'export_date': datetime.date.today().isoformat(),
            'records': records_list
        })

    # CSV 格式
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        '疫苗名称', '类型', '剂次', '计划日期', '实际接种日期', '状态',
        '生产厂家', '批号', '接种单位', '接种医生', '接种部位',
        '不良反应', '反应详情', '反应程度', '备注'
    ])
    type_map = {'free': '一类(免费)', 'paid': '二类(自费)'}
    status_map = {'pending': '待接种', 'completed': '已完成', 'delayed': '延期', 'skipped': '跳过'}
    severity_map = {'none': '无', 'mild': '轻度', 'moderate': '中度', 'severe': '重度'}

    for r in records_list:
        writer.writerow([
            r.get('vaccine_name', ''),
            type_map.get(r.get('vaccine_type', ''), r.get('vaccine_type', '')),
            r.get('dose_number', ''),
            r.get('scheduled_date', ''),
            r.get('actual_date', ''),
            status_map.get(r.get('status', ''), r.get('status', '')),
            r.get('manufacturer', ''),
            r.get('batch_number', ''),
            r.get('hospital', ''),
            r.get('doctor', ''),
            r.get('injection_site', ''),
            '是' if r.get('has_reaction') else '否',
            r.get('reaction_detail', ''),
            severity_map.get(r.get('reaction_severity', ''), ''),
            r.get('note', ''),
        ])

    csv_content = output.getvalue()
    output.close()

    filename = f"{baby_name}_疫苗记录_{datetime.date.today().isoformat()}.csv"
    # 处理中文文件名
    from urllib.parse import quote
    encoded_filename = quote(filename)

    return Response(
        csv_content.encode('utf-8-sig'),  # UTF-8 BOM for Excel compatibility
        mimetype='text/csv; charset=utf-8',
        headers={
            'Content-Disposition': f"attachment; filename*=UTF-8''{encoded_filename}"
        }
    )


# ==================== API: 疫苗详细记录 ====================

@bp.route('/api/health/vaccines/<int:baby_id>', methods=['POST'])
def vaccine_detail_create(baby_id):
    """创建疫苗详细记录"""
    db = get_db()
    data = request.get_json()

    if not data or not data.get('vaccine_name'):
        return jsonify({'success': False, 'message': '疫苗名称为必填'}), 400

    db.execute(
        """INSERT INTO vaccine_details
           (baby_id, vaccine_name, vaccine_type, dose_number, scheduled_date, actual_date,
            status, manufacturer, batch_number, hospital, doctor, injection_site,
            has_reaction, reaction_detail, reaction_severity, next_dose_date,
            antibody_test_date, antibody_result, note)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (baby_id, data['vaccine_name'], data.get('vaccine_type', 'free'),
         data.get('dose_number', 1), data.get('scheduled_date'), data.get('actual_date'),
         data.get('status', 'pending'), data.get('manufacturer', ''), data.get('batch_number', ''),
         data.get('hospital', ''), data.get('doctor', ''), data.get('injection_site', ''),
         data.get('has_reaction', 0), data.get('reaction_detail', ''), data.get('reaction_severity', ''),
         data.get('next_dose_date', ''), data.get('antibody_test_date', ''),
         data.get('antibody_result', ''), data.get('note', ''))
    )
    db.commit()
    return jsonify({'success': True, 'message': '疫苗详细记录已添加'})


@bp.route('/api/health/vaccines/detail/<int:vaccine_id>', methods=['DELETE'])
def vaccine_detail_delete(vaccine_id):
    """删除疫苗记录"""
    db = get_db()
    db.execute('DELETE FROM vaccine_details WHERE id = ?', (vaccine_id,))
    db.commit()
    return jsonify({'success': True, 'message': '疫苗记录已删除'})


@bp.route('/api/health/vaccines/<int:baby_id>', methods=['GET'])
def vaccine_detail_list(baby_id):
    """获取疫苗详细记录列表"""
    db = get_db()
    status_filter = request.args.get('status', '')
    query = 'SELECT * FROM vaccine_details WHERE baby_id = ?'
    params = [baby_id]
    if status_filter:
        query += ' AND status = ?'
        params.append(status_filter)
    query += ' ORDER BY scheduled_date ASC'
    rows = db.execute(query, params).fetchall()
    return jsonify({'success': True, 'vaccines': rows_to_list(rows)})


@bp.route('/api/health/vaccines/detail/<int:vaccine_id>', methods=['PUT'])
def vaccine_detail_update(vaccine_id):
    """更新疫苗详细记录"""
    db = get_db()
    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'message': '无效数据'}), 400
    fields = []
    values = []
    for key in ('vaccine_name', 'vaccine_type', 'dose_number', 'scheduled_date', 'actual_date',
                'status', 'manufacturer', 'batch_number', 'hospital', 'doctor',
                'injection_site', 'has_reaction', 'reaction_detail', 'reaction_severity',
                'next_dose_date', 'antibody_test_date', 'antibody_result', 'note'):
        if key in data:
            fields.append(f'{key} = ?')
            values.append(data[key])
    if not fields:
        return jsonify({'success': False, 'message': '无更新字段'}), 400
    values.append(vaccine_id)
    db.execute(f'UPDATE vaccine_details SET {", ".join(fields)} WHERE id = ?', values)
    db.commit()
    return jsonify({'success': True, 'message': '疫苗记录已更新'})


# ==================== API: 标准疫苗接种计划 ====================

@bp.route('/api/health/vaccines/schedule/<int:baby_id>', methods=['GET'])
def vaccine_schedule(baby_id):
    """获取标准疫苗接种计划"""
    standard_schedule = [
        {'vaccine_name': '乙肝疫苗', 'dose': 1, 'scheduled_age_months': 0, 'type': 'free', 'note': '出生24h内'},
        {'vaccine_name': '卡介苗', 'dose': 1, 'scheduled_age_months': 0, 'type': 'free', 'note': '出生时'},
        {'vaccine_name': '乙肝疫苗', 'dose': 2, 'scheduled_age_months': 1, 'type': 'free', 'note': '1月龄'},
        {'vaccine_name': '脊灰灭活疫苗', 'dose': 1, 'scheduled_age_months': 2, 'type': 'free', 'note': '2月龄'},
        {'vaccine_name': '脊灰减毒疫苗', 'dose': 2, 'scheduled_age_months': 3, 'type': 'free', 'note': '3月龄'},
        {'vaccine_name': '百白破疫苗', 'dose': 1, 'scheduled_age_months': 3, 'type': 'free', 'note': '3月龄'},
        {'vaccine_name': '脊灰减毒疫苗', 'dose': 3, 'scheduled_age_months': 4, 'type': 'free', 'note': '4月龄'},
        {'vaccine_name': '百白破疫苗', 'dose': 2, 'scheduled_age_months': 4, 'type': 'free', 'note': '4月龄'},
        {'vaccine_name': '百白破疫苗', 'dose': 3, 'scheduled_age_months': 5, 'type': 'free', 'note': '5月龄'},
        {'vaccine_name': '乙肝疫苗', 'dose': 3, 'scheduled_age_months': 6, 'type': 'free', 'note': '6月龄'},
        {'vaccine_name': 'A群流脑多糖疫苗', 'dose': 1, 'scheduled_age_months': 6, 'type': 'free', 'note': '6月龄'},
        {'vaccine_name': '麻腮风疫苗', 'dose': 1, 'scheduled_age_months': 8, 'type': 'free', 'note': '8月龄'},
        {'vaccine_name': '乙脑减毒活疫苗', 'dose': 1, 'scheduled_age_months': 8, 'type': 'free', 'note': '8月龄'},
        {'vaccine_name': 'A群流脑多糖疫苗', 'dose': 2, 'scheduled_age_months': 9, 'type': 'free', 'note': '9月龄'},
        {'vaccine_name': '甲肝灭活疫苗', 'dose': 1, 'scheduled_age_months': 18, 'type': 'free', 'note': '18月龄'},
        {'vaccine_name': '百白破疫苗', 'dose': 4, 'scheduled_age_months': 18, 'type': 'free', 'note': '18月龄'},
        {'vaccine_name': '麻腮风疫苗', 'dose': 2, 'scheduled_age_months': 18, 'type': 'free', 'note': '18月龄'},
        {'vaccine_name': '甲肝灭活疫苗', 'dose': 2, 'scheduled_age_months': 24, 'type': 'free', 'note': '2岁'},
        {'vaccine_name': '乙脑减毒活疫苗', 'dose': 2, 'scheduled_age_months': 24, 'type': 'free', 'note': '2岁'},
        {'vaccine_name': 'A+C群流脑多糖疫苗', 'dose': 1, 'scheduled_age_months': 36, 'type': 'free', 'note': '3岁'},
        {'vaccine_name': '脊灰减毒疫苗', 'dose': 4, 'scheduled_age_months': 48, 'type': 'free', 'note': '4岁'},
        {'vaccine_name': 'A+C群流脑多糖疫苗', 'dose': 2, 'scheduled_age_months': 72, 'type': 'free', 'note': '6岁'},
    ]
    recommended = [
        {'vaccine_name': '五联疫苗', 'dose': 1, 'scheduled_age_months': 2, 'type': 'paid', 'note': '可选替代脊灰+百白破+Hib'},
        {'vaccine_name': '五联疫苗', 'dose': 2, 'scheduled_age_months': 4, 'type': 'paid', 'note': ''},
        {'vaccine_name': '五联疫苗', 'dose': 3, 'scheduled_age_months': 6, 'type': 'paid', 'note': ''},
        {'vaccine_name': '五联疫苗', 'dose': 4, 'scheduled_age_months': 18, 'type': 'paid', 'note': ''},
        {'vaccine_name': '13价肺炎疫苗', 'dose': 1, 'scheduled_age_months': 2, 'type': 'paid', 'note': ''},
        {'vaccine_name': '13价肺炎疫苗', 'dose': 2, 'scheduled_age_months': 4, 'type': 'paid', 'note': ''},
        {'vaccine_name': '13价肺炎疫苗', 'dose': 3, 'scheduled_age_months': 6, 'type': 'paid', 'note': ''},
        {'vaccine_name': '13价肺炎疫苗', 'dose': 4, 'scheduled_age_months': 12, 'type': 'paid', 'note': ''},
        {'vaccine_name': '五价轮状病毒疫苗', 'dose': 1, 'scheduled_age_months': 2, 'type': 'paid', 'note': '口服'},
        {'vaccine_name': '五价轮状病毒疫苗', 'dose': 2, 'scheduled_age_months': 3, 'type': 'paid', 'note': '口服'},
        {'vaccine_name': '五价轮状病毒疫苗', 'dose': 3, 'scheduled_age_months': 4, 'type': 'paid', 'note': '口服'},
        {'vaccine_name': 'Hib疫苗', 'dose': 1, 'scheduled_age_months': 2, 'type': 'paid', 'note': ''},
        {'vaccine_name': 'Hib疫苗', 'dose': 2, 'scheduled_age_months': 4, 'type': 'paid', 'note': ''},
        {'vaccine_name': 'Hib疫苗', 'dose': 3, 'scheduled_age_months': 6, 'type': 'paid', 'note': ''},
        {'vaccine_name': 'Hib疫苗', 'dose': 4, 'scheduled_age_months': 18, 'type': 'paid', 'note': ''},
        {'vaccine_name': '水痘疫苗', 'dose': 1, 'scheduled_age_months': 12, 'type': 'paid', 'note': ''},
        {'vaccine_name': '水痘疫苗', 'dose': 2, 'scheduled_age_months': 48, 'type': 'paid', 'note': ''},
        {'vaccine_name': '流感疫苗', 'dose': 1, 'scheduled_age_months': 6, 'type': 'paid', 'note': '每年秋季'},
        {'vaccine_name': '手足口疫苗(EV71)', 'dose': 1, 'scheduled_age_months': 6, 'type': 'paid', 'note': ''},
        {'vaccine_name': '手足口疫苗(EV71)', 'dose': 2, 'scheduled_age_months': 7, 'type': 'paid', 'note': ''},
    ]

    db = get_db()
    baby = db.execute('SELECT birthday FROM babies WHERE id = ?', (baby_id,)).fetchone()
    if baby and baby['birthday']:
        birth = datetime.datetime.strptime(baby['birthday'], '%Y-%m-%d')
        for v in standard_schedule:
            v['scheduled_date'] = (birth + datetime.timedelta(days=int(v['scheduled_age_months'] * 30.44))).strftime('%Y-%m-%d')
            v['scheduled_age_display'] = _format_age_display(v['scheduled_age_months'])
        for v in recommended:
            v['scheduled_date'] = (birth + datetime.timedelta(days=int(v['scheduled_age_months'] * 30.44))).strftime('%Y-%m-%d')
            v['scheduled_age_display'] = _format_age_display(v['scheduled_age_months'])

    return jsonify({'success': True, 'standard': standard_schedule, 'recommended': recommended})
