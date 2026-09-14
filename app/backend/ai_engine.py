# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xiaoke799
"""
育儿宝 AI 智能分析引擎
基于规则 + 数据驱动的专家系统，无需外部 API
支持对接云端大模型获得更智能的分析能力
"""

import datetime
import json
import logging
import os
import urllib.parse
import urllib.request
from base64 import b64decode, b64encode
from functools import lru_cache

import growth_utils

logger = logging.getLogger(__name__)


# ==================== API Key 加密 ====================

def _encryption_key_path():
    """获取加密密钥文件路径"""
    data_dir = os.path.dirname(os.path.abspath(__file__))
    secrets_dir = os.path.join(data_dir, "secrets")
    os.makedirs(secrets_dir, exist_ok=True)
    return os.path.join(secrets_dir, "api-key.key")


def _get_encryption_key():
    """获取或生成 AES-256 密钥 (32 字节)"""
    path = _encryption_key_path()
    if not os.path.exists(path):
        key = os.urandom(32)
        with open(path, "wb") as f:
            f.write(key)
        os.chmod(path, 0o600)
    with open(path, "rb") as f:
        return f.read()


def encrypt_api_key(plaintext: str) -> str:
    """
    使用 AES-256-GCM 加密 API Key
    格式: base64(iv + authTag + ciphertext)
    """
    if not plaintext:
        return ""
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        key = _get_encryption_key()
        iv = os.urandom(12)
        aesgcm = AESGCM(key)
        ciphertext = aesgcm.encrypt(iv, plaintext.encode("utf-8"), None)
        return b64encode(iv + ciphertext).decode("ascii")
    except ImportError:
        # 降级: 无 cryptography 库时回退 Fernet
        try:
            from cryptography.fernet import Fernet
            # 从 AES 密钥派生 Fernet 密钥
            import hashlib
            fernet_key = b64encode(hashlib.sha256(_get_encryption_key()).digest())
            f = Fernet(fernet_key)
            return f.encrypt(plaintext.encode("utf-8")).decode("ascii")
        except ImportError:
            # 最终降级: 明文存储 (开发环境)
            logger.warning("cryptography 库不可用，API Key 明文存储")
            return plaintext


def decrypt_api_key(ciphertext: str) -> str:
    """解密 AES-256-GCM 加密的 API Key"""
    if not ciphertext:
        return ""
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        key = _get_encryption_key()
        data = b64decode(ciphertext.encode("ascii"))
        iv = data[:12]
        ct = data[12:]
        aesgcm = AESGCM(key)
        return aesgcm.decrypt(iv, ct, None).decode("utf-8")
    except ImportError:
        try:
            from cryptography.fernet import Fernet
            import hashlib
            fernet_key = b64encode(hashlib.sha256(_get_encryption_key()).digest())
            f = Fernet(fernet_key)
            return f.decrypt(ciphertext.encode("ascii")).decode("utf-8")
        except ImportError:
            return ciphertext
    except Exception as e:
        logger.error(f"解密 API Key 失败: {e}")
        return ""


def mask_api_key(api_key: str) -> str:
    """脱敏显示 API Key，如: sk-****abcd"""
    if not api_key or len(api_key) < 8:
        return "****"
    visible = min(4, len(api_key) // 4)
    return api_key[:visible] + "*" * (len(api_key) - visible * 2) + api_key[-visible:]


# ==================== 知识库 ====================

FEEDING_GUIDELINES = {
    # 月龄: (每次奶量ml, 每日次数, 备注)
    0: (60, 8, "新生儿胃容量小，需少量多餐"),
    1: (90, 7, "逐渐建立规律喂养"),
    2: (120, 6, "每次奶量稳步增长"),
    3: (150, 5, "可开始考虑添加辅食准备"),
    4: (180, 5, "观察辅食添加信号"),
    5: (200, 4, "准备添加辅食"),
    6: (200, 4, "建议开始添加辅食"),
    7: (200, 4, "辅食从米粉开始"),
    8: (200, 3, "逐步增加辅食种类"),
    9: (200, 3, "可添加蛋黄、肉泥"),
    10: (200, 3, "增加辅食稠度"),
    11: (200, 3, "尝试颗粒状食物"),
    12: (200, 3, "过渡到家庭饮食"),
}

SLEEP_GUIDELINES = {
    # 月龄: (总睡眠时长小时, 白天小睡次数, 夜间连续睡眠)
    0: (16, 4, "3-4小时"),
    1: (15, 4, "3-4小时"),
    2: (15, 3, "4-5小时"),
    3: (14, 3, "5-6小时"),
    4: (14, 3, "6-7小时"),
    5: (13, 2, "7-8小时"),
    6: (13, 2, "8-9小时"),
    7: (13, 2, "9-10小时"),
    8: (13, 2, "10-11小时"),
    9: (12, 2, "10-11小时"),
    10: (12, 2, "10-11小时"),
    11: (12, 2, "10-11小时"),
    12: (12, 1, "10-12小时"),
}

MILESTONES = {
    1: ["能短暂抬头", "对声音有反应", "能注视人脸"],
    2: ["能抬头45度", "开始微笑", "发出咕咕声"],
    3: ["能抬头90度", "能追视移动物体", "开始吃手"],
    4: ["能翻身", "能笑出声", "会抓握玩具"],
    5: ["能坐稳片刻", "开始认生", "会伸手要抱"],
    6: ["能独坐", "开始出牙", "对名字有反应"],
    7: ["会爬行", "能传递玩具", "开始模仿"],
    8: ["能扶站", "会拍手", "理解'不'"],
    9: ["能扶走", "会说'爸爸/妈妈'", "会指物"],
    10: ["能独站片刻", "会挥手再见", "模仿说话"],
    11: ["能独走几步", "会用杯喝水", "有意识叫爸妈"],
    12: ["能独走", "会说3-5个词", "会自己吃饭"],
}

SOLID_FOOD_GUIDE = {
    6: {"foods": ["强化铁米粉", "米汤"], "tips": "从单一谷物开始，观察3天无过敏再加新食物"},
    7: {"foods": ["蔬菜泥", "水果泥", "肉泥"], "tips": "逐步添加蔬菜、水果、肉类"},
    8: {"foods": ["蛋黄", "鱼泥", "豆腐"], "tips": "可添加蛋黄（1/4个），观察过敏反应"},
    9: {"foods": ["颗粒状食物", "手指食物"], "tips": "开始尝试颗粒状食物，锻炼咀嚼"},
    10: {"foods": ["软饭", "碎肉", "小块蔬菜"], "tips": "食物性状逐渐向成人靠拢"},
    11: {"foods": ["家庭食物", "全蛋"], "tips": "可尝试全蛋，注意食物安全"},
    12: {"foods": ["家庭饮食", "鲜奶"], "tips": "逐步过渡到家庭饮食，可尝试鲜奶"},
}


# ==================== 分析引擎 ====================

def get_age_months(birthday_str):
    """根据生日计算月龄"""
    try:
        birthday = datetime.datetime.strptime(birthday_str, '%Y-%m-%d')
        today = datetime.datetime.now()
        days = (today - birthday).days
        return days // 30
    except (ValueError, TypeError):
        return 0


def analyze_growth(baby, growth_records):
    """分析生长发育情况"""
    insights = []
    age_months = get_age_months(baby.get('birthday', ''))

    if not growth_records:
        insights.append({
            'type': 'info',
            'title': '暂无生长数据',
            'content': '建议定期记录宝宝的身高、体重、头围数据，以便追踪生长发育趋势。'
        })
        return insights

    latest = growth_records[-1]

    # 体重分析
    if latest.get('weight'):
        weight = latest['weight']
        if age_months <= 12:
            # WHO 参考值（简化版）
            ref_weights = {0: 3.3, 1: 4.5, 2: 5.6, 3: 6.4, 4: 7.0, 5: 7.5, 6: 7.9,
                          7: 8.3, 8: 8.6, 9: 8.9, 10: 9.2, 11: 9.4, 12: 9.6}
            ref = ref_weights.get(min(age_months, 12), 9.6)
            if weight < ref * 0.9:
                insights.append({
                    'type': 'warning',
                    'title': '体重偏低',
                    'content': f'当前体重 {weight}kg，低于同龄参考值（{ref}kg）。建议咨询儿科医生，评估喂养情况。'
                })
            elif weight > ref * 1.2:
                insights.append({
                    'type': 'warning',
                    'title': '体重偏高',
                    'content': f'当前体重 {weight}kg，高于同龄参考值（{ref}kg）。注意合理喂养，避免过度。'
                })
            else:
                insights.append({
                    'type': 'success',
                    'title': '体重正常',
                    'content': f'当前体重 {weight}kg，处于同龄正常范围。继续保持良好的喂养习惯！'
                })

    # 身高分析
    if latest.get('height'):
        height = latest['height']
        if age_months <= 12:
            ref_heights = {0: 50, 1: 54, 2: 58, 3: 61, 4: 63, 5: 65, 6: 67,
                          7: 68, 8: 69, 9: 71, 10: 72, 11: 73, 12: 74}
            ref = ref_heights.get(min(age_months, 12), 74)
            if height < ref - 3:
                insights.append({
                    'type': 'warning',
                    'title': '身高偏矮',
                    'content': f'当前身高 {height}cm，低于同龄参考值（{ref}cm）。建议关注营养摄入和睡眠质量。'
                })
            elif height > ref + 3:
                insights.append({
                    'type': 'success',
                    'title': '身高偏高',
                    'content': f'当前身高 {height}cm，高于同龄参考值（{ref}cm）。宝宝发育得很好！'
                })
            else:
                insights.append({
                    'type': 'success',
                    'title': '身高正常',
                    'content': f'当前身高 {height}cm，处于同龄正常范围。'
                })

    # 生长速度分析
    if len(growth_records) >= 2:
        prev = growth_records[-2]
        if latest.get('weight') and prev.get('weight'):
            weight_diff = latest['weight'] - prev['weight']
            if weight_diff < 0:
                insights.append({
                    'type': 'warning',
                    'title': '体重下降',
                    'content': f'最近一次记录体重下降了 {abs(weight_diff):.2f}kg。如果持续下降，请咨询医生。'
                })

    return insights


def analyze_feeding(baby, feeding_records):
    """分析喂养情况"""
    insights = []
    age_months = get_age_months(baby.get('birthday', ''))

    if not feeding_records:
        insights.append({
            'type': 'info',
            'title': '暂无喂养记录',
            'content': '建议记录每次喂养的时间和奶量，以便分析喂养规律。'
        })
        return insights

    # 近7天统计
    week_ago = (datetime.datetime.now() - datetime.timedelta(days=7)).strftime('%Y-%m-%d')
    recent = [r for r in feeding_records if r.get('start_time', '')[:10] >= week_ago]

    if recent:
        total_feedings = len(recent)
        daily_avg = total_feedings / 7

        # 获取月龄对应的建议
        guide = FEEDING_GUIDELINES.get(min(age_months, 12), FEEDING_GUIDELINES[12])
        suggested_freq = guide[1]

        if daily_avg < suggested_freq - 1:
            insights.append({
                'type': 'warning',
                'title': '喂养频率偏低',
                'content': f'近7天平均每天喂养 {daily_avg:.1f} 次，建议 {suggested_freq} 次。注意观察宝宝是否摄入充足。'
            })
        elif daily_avg > suggested_freq + 2:
            insights.append({
                'type': 'info',
                'title': '喂养频率较高',
                'content': f'近7天平均每天喂养 {daily_avg:.1f} 次。如果宝宝需求旺盛，可适当增加。'
            })
        else:
            insights.append({
                'type': 'success',
                'title': '喂养频率正常',
                'content': f'近7天平均每天喂养 {daily_avg:.1f} 次，符合月龄建议。'
            })

        # 奶量分析
        amounts = [r.get('amount', 0) or 0 for r in recent if r.get('amount')]
        if amounts:
            avg_amount = sum(amounts) / len(amounts)
            suggested_amount = guide[0]
            if avg_amount < suggested_amount * 0.7:
                insights.append({
                    'type': 'warning',
                    'title': '单次奶量偏少',
                    'content': f'平均每次 {avg_amount:.0f}ml，建议 {suggested_amount}ml。注意观察宝宝是否吃饱。'
                })

    return insights


def analyze_sleep(baby, sleep_records):
    """分析睡眠情况"""
    insights = []
    age_months = get_age_months(baby.get('birthday', ''))

    if not sleep_records:
        insights.append({
            'type': 'info',
            'title': '暂无睡眠记录',
            'content': '建议记录宝宝的睡眠时间，帮助建立规律作息。'
        })
        return insights

    # 近7天统计
    week_ago = (datetime.datetime.now() - datetime.timedelta(days=7)).strftime('%Y-%m-%d')
    recent = [r for r in sleep_records if r.get('start_time', '')[:10] >= week_ago]

    if recent:
        total_minutes = sum(r.get('duration_minutes', 0) or 0 for r in recent)
        daily_avg = total_minutes / 7 / 60  # 小时

        guide = SLEEP_GUIDELINES.get(min(age_months, 12), SLEEP_GUIDELINES[12])
        suggested_total = guide[0]

        if daily_avg < suggested_total - 2:
            insights.append({
                'type': 'warning',
                'title': '睡眠不足',
                'content': f'近7天平均每天睡眠 {daily_avg:.1f} 小时，建议 {suggested_total} 小时。睡眠不足可能影响发育。'
            })
        elif daily_avg > suggested_total + 2:
            insights.append({
                'type': 'info',
                'title': '睡眠较多',
                'content': f'近7天平均每天睡眠 {daily_avg:.1f} 小时。如果宝宝精神状态好，无需担心。'
            })
        else:
            insights.append({
                'type': 'success',
                'title': '睡眠正常',
                'content': f'近7天平均每天睡眠 {daily_avg:.1f} 小时，处于正常范围。'
            })

    return insights


def get_milestone_suggestions(baby):
    """获取发育里程碑建议"""
    age_months = get_age_months(baby.get('birthday', ''))
    milestones = MILESTONES.get(min(age_months, 12), [])

    if milestones:
        return {
            'type': 'info',
            'title': f'{age_months}月龄发育要点',
            'content': f'本月宝宝可能达到的里程碑：{"、".join(milestones)}。每个宝宝发育速度不同，仅供参考。'
        }
    return None


def get_solid_food_advice(baby):
    """获取辅食添加建议"""
    age_months = get_age_months(baby.get('birthday', ''))
    guide = SOLID_FOOD_GUIDE.get(min(age_months, 12))

    if guide:
        return {
            'type': 'info',
            'title': '辅食添加建议',
            'content': f'推荐食物：{"、".join(guide["foods"])}。{guide["tips"]}'
        }
    return None


def generate_full_report(db, baby_id):
    """生成完整的 AI 分析报告"""
    baby = db.execute('SELECT * FROM babies WHERE id = ?', (baby_id,)).fetchone()
    if not baby:
        return {'success': False, 'message': '宝宝不存在'}

    # 获取所有数据
    growth = db.execute('SELECT * FROM growth_records WHERE baby_id = ? ORDER BY record_date', (baby_id,)).fetchall()
    feeding = db.execute('SELECT * FROM feeding_records WHERE baby_id = ? ORDER BY start_time DESC LIMIT 100', (baby_id,)).fetchall()
    sleep = db.execute('SELECT * FROM sleep_records WHERE baby_id = ? ORDER BY start_time DESC LIMIT 100', (baby_id,)).fetchall()

    # 转换为字典列表
    growth_list = [dict(r) for r in growth]
    feeding_list = [dict(r) for r in feeding]
    sleep_list = [dict(r) for r in sleep]

    # 生成各项分析
    insights = []
    insights.extend(analyze_growth(dict(baby), growth_list))
    insights.extend(analyze_feeding(dict(baby), feeding_list))
    insights.extend(analyze_sleep(dict(baby), sleep_list))

    # 里程碑建议
    milestone = get_milestone_suggestions(dict(baby))
    if milestone:
        insights.append(milestone)

    # 辅食建议
    solid = get_solid_food_advice(dict(baby))
    if solid:
        insights.append(solid)

    return {
        'success': True,
        'baby': dict(baby),
        'age_months': get_age_months(baby['birthday']),
        'insights': insights,
        'summary': {
            'total_growth_records': len(growth_list),
            'total_feeding_records': len(feeding_list),
            'total_sleep_records': len(sleep_list),
        }
    }


# ==================== 聊天问答引擎 ====================

def chat_response(question, db=None, baby_id=None):
    """基于规则的聊天问答（无网AI本地处理）"""
    question_lower = question.lower().strip()

    # 构建宝宝数据上下文
    baby_context = _get_baby_context(db, baby_id)

    # 关键词匹配（优先级从高到低）
    # 0. 数据查询意图（点击查询，优先于一般问答）
    if any(kw in question_lower for kw in ['最近喂养', '喂养记录', '喂了几次', '今天吃', '最近吃', '奶量统计', '查询喂养']):
        return _answer_query_feeding(db, baby_id, baby_context)
    elif any(kw in question_lower for kw in ['睡眠记录', '最近睡', '睡眠统计', '睡眠情况', '查询睡眠', '睡得怎么样']):
        return _answer_query_sleep(db, baby_id, baby_context)
    elif any(kw in question_lower for kw in ['生长数据', '生长记录', '最新体重', '最新身高', '查询生长']):
        return _answer_query_growth(db, baby_id, baby_context)
    elif any(kw in question_lower for kw in ['接种记录', '疫苗记录', '打过疫苗', '下次疫苗', '疫苗安排']):
        return _answer_vaccine_records(question, db, baby_id, baby_context)

    # 1. 生长数据相关
    elif any(kw in question_lower for kw in ['体重', '胖', '瘦', '多重', '公斤', 'kg']):
        return _answer_weight(question, db, baby_id, baby_context)
    elif any(kw in question_lower for kw in ['身高', '长高', '多高', '厘米', 'cm', '个子']):
        return _answer_height(question, db, baby_id, baby_context)
    elif any(kw in question_lower for kw in ['头围', '头多大', '脑袋']):
        return _answer_head_circumference(question, db, baby_id, baby_context)
    elif any(kw in question_lower for kw in ['bmi', '体质指数', '肥胖', '超重']):
        return _answer_bmi(question, db, baby_id, baby_context)

    # 2. 喂养相关
    elif any(kw in question_lower for kw in ['喂奶', '吃奶', '奶粉', '母乳', '奶量', '喂养', '吃多少', '一次吃', '夜奶', '断奶', '奶不够', '奶水', '下奶', '回奶']):
        return _answer_feeding(question, db, baby_id, baby_context)

    # 3. 睡眠相关
    elif any(kw in question_lower for kw in ['睡觉', '睡眠', '夜醒', '哄睡', '入睡', '睡多久', '几点睡', '睡不好']):
        return _answer_sleep(question, db, baby_id, baby_context)

    # 4. 辅食相关
    elif any(kw in question_lower for kw in ['辅食', '加餐', '吃饭', '米粉', '加什么', '吃什么']):
        return _answer_solid_food(question, db, baby_id, baby_context)

    # 5. 疫苗相关
    elif any(kw in question_lower for kw in ['疫苗', '打针', '接种', '预防针', '免费疫苗']):
        return _answer_vaccine(question, db, baby_id, baby_context)

    # 6. 发育相关
    elif any(kw in question_lower for kw in ['发育', '里程碑', '翻身', '爬行', '走路', '说话', '长牙', '出牙', '坐', '站']):
        return _answer_milestone(question, db, baby_id, baby_context)

    # 7. 健康相关
    elif any(kw in question_lower for kw in ['过敏', '湿疹', '红疹', '反应', '发烧', '发热', '咳嗽', '鼻塞', '鼻涕', '感冒', '便秘', '拉肚子', '腹泻', '黄疸', '吐奶', '溢奶', '拍嗝', '打嗝', '哭闹', '夜哭', '肠绞痛', '维生素d', '补钙', '鱼肝油', '缺钙']):
        return _answer_health(question, db, baby_id, baby_context)

    # 8. 如厕相关
    elif any(kw in question_lower for kw in ['尿布', '纸尿裤', '拉屎', '拉尿', '大便', '小便', '把尿', '如厕', '红屁股', '尿布疹', '红臀', '护臀']):
        return _answer_diaper(question, db, baby_id, baby_context)

    # 10. 自我介绍
    elif any(kw in question_lower for kw in ['你好', '介绍', '你是谁', '功能', '能做什么', '帮助']):
        return _answer_intro()

    # 11. 月龄计算
    elif any(kw in question_lower for kw in ['几个月', '多大', '月龄', '多少天']):
        return _answer_age(baby_context)

    # 默认通用回答
    else:
        return _answer_general(question, baby_context)


# ==================== WHO 生长参考助手（规则引擎共用） ====================

# 指标 → WHO 百分位表（与 health_records._calc_percentile 同一套表与性别约定）
WHO_METRIC_TABLE = {
    'weight': 'who_weight_percentiles',
    'height': 'who_height_percentiles',
    'bmi': 'who_bmi_percentiles',
    'head_circumference': 'who_head_percentiles',
}

# 百分位带 → (结论, 提示文案)
_BAND_TEXT = {
    '<P3': ('偏低', '低于 WHO 参考的 P3，建议尽早就医评估'),
    'P3-P15': ('正常偏下', '处于 P3-P15，参考下段，建议加强喂养并持续观察曲线走向'),
    'P15-P50': ('正常', ''),
    'P50-P85': ('正常', ''),
    'P85-P97': ('正常偏上', '处于 P85-P97，参考上段，注意均衡饮食、避免过度喂养'),
    '>P97': ('偏高', '高于 WHO 参考的 P97，建议咨询儿科医生评估'),
}

DAYS_PER_MONTH = 30.4375  # 平均月长（与 growth_utils 口径一致）


def _who_ref(db, metric, age_in_days, gender=None):
    """按 WHO 百分位表插值出该日龄的参考值 {'p3'..'p97'}，查不到返回 None。

    性别约定与 health_records._calc_percentile 一致：
    gender=='boy' 用男宝曲线，其余（girl/other/未填）按女宝曲线。
    """
    table = WHO_METRIC_TABLE.get(metric)
    if db is None or table is None or age_in_days is None:
        return None
    try:
        sex = 'boy' if gender == 'boy' else 'girl'
        rows = db.execute(
            'SELECT age_in_days, p3, p15, p50, p85, p97 FROM ' + table + ' WHERE sex = ? ORDER BY age_in_days',
            (sex,)
        ).fetchall()
        if not rows:
            return None
        return growth_utils.interpolate_reference(rows, float(age_in_days))
    except Exception as e:
        logger.warning('查询 WHO 参考失败(%s): %s: %s', metric, type(e).__name__, str(e)[:200])
        return None


def _growth_eval_days(birthday, due_date, ref_date):
    """生长评估基准日龄（从生日/预产期到 ref_date）。

    填了预产期且未满 24 月龄时按预产期起算（早产儿矫正月龄口径）。
    返回 (eval_days 或 None, is_corrected)。
    """
    b = growth_utils.parse_date(birthday)
    r = growth_utils.parse_date(ref_date)
    if b is None or r is None:
        return None, False
    age_days = max((r - b).days, 0)
    basis, corrected = growth_utils.effective_age_basis(birthday, due_date, age_days)
    eval_days = max((r - basis).days, 0)
    return eval_days, corrected


def _band_info(value, metric, db, gender, eval_days):
    """返回 (ref, band, verdict)；查不到参考或参考缺 p50 时返回 None。"""
    ref = _who_ref(db, metric, eval_days, gender)
    if not ref or ref.get('p50') is None:
        return None
    try:
        band = growth_utils.classify_percentile(float(value), ref)
    except (TypeError, ValueError):
        return None
    verdict, _ = _BAND_TEXT.get(band, ('正常', ''))
    return ref, band, verdict


def _age_desc(eval_days, corrected):
    """'24.3月龄（矫正月龄）'样式的月龄描述"""
    if eval_days is None:
        return '月龄未知'
    desc = f"{round(eval_days / DAYS_PER_MONTH, 1)}月龄"
    if corrected:
        desc += "（矫正月龄）"
    return desc


def _get_baby_context(db, baby_id):
    """获取宝宝数据上下文"""
    if not db or not baby_id:
        return None
    try:
        baby = db.execute('SELECT * FROM babies WHERE id = ?', (baby_id,)).fetchone()
        if not baby:
            return None
        baby = dict(baby)
        age_months = get_age_months(baby.get('birthday', ''))

        # 获取最新生长数据
        growth = db.execute(
            'SELECT * FROM growth_records WHERE baby_id = ? ORDER BY record_date DESC LIMIT 1',
            (baby_id,)
        ).fetchone()

        context = {
            'name': baby.get('name', '宝宝'),
            'age_months': age_months,
            'birthday': baby.get('birthday', ''),
            'gender': baby.get('gender') or 'other',
        }
        if baby.get('due_date'):
            context['due_date'] = baby['due_date']
        if growth:
            context['weight'] = growth['weight']
            context['height'] = growth['height']
            context['head_circumference'] = growth['head_circumference']
            context['latest_record_date'] = growth['record_date']
        return context
    except Exception:
        return None


def _answer_weight(question, db, baby_id, baby_context=None):
    """回答体重相关问题（WHO 百分位评估）"""
    if db and baby_id:
        record = db.execute('SELECT * FROM growth_records WHERE baby_id = ? ORDER BY record_date DESC LIMIT 1', (baby_id,)).fetchone()
        if record is not None and record['weight']:
            weight = float(record['weight'])
            ctx = baby_context or {}
            eval_days, corrected = _growth_eval_days(ctx.get('birthday'), ctx.get('due_date'), record['record_date'])
            age_desc = _age_desc(eval_days, corrected) if eval_days is not None else f"{ctx.get('age_months', '?')}月龄"
            lines = [f"宝宝当前体重为 {weight}kg（{age_desc}）。"]
            info = _band_info(weight, 'weight', db, ctx.get('gender'), eval_days)
            if info:
                ref, band, verdict = info
                lines.append(f"WHO 同龄参考中位数约 {ref.get('p50')}kg，体重状态：{verdict}（{band}）。")
                tip = _BAND_TEXT.get(band, (verdict, ''))[1]
                if tip:
                    lines.append(f"[提示] {tip}")
            lines += [
                "",
                "建议：",
                "1. 定期测量体重，观察生长曲线趋势（趋势比单次数值更重要）",
                "2. 保持均衡喂养",
                "3. 如有持续异常，建议咨询儿科医生",
            ]
            return "\n".join(lines)

    return "体重是衡量宝宝发育的重要指标。建议：\n1. 每周固定时间测量\n2. 使用同一台秤\n3. 记录生长曲线\n4. 如有异常及时咨询医生"


def _answer_height(question, db, baby_id, baby_context=None):
    """回答身高相关问题（WHO 百分位评估）"""
    if db and baby_id:
        record = db.execute('SELECT * FROM growth_records WHERE baby_id = ? ORDER BY record_date DESC LIMIT 1', (baby_id,)).fetchone()
        if record is not None and record['height']:
            height = float(record['height'])
            ctx = baby_context or {}
            eval_days, corrected = _growth_eval_days(ctx.get('birthday'), ctx.get('due_date'), record['record_date'])
            age_desc = _age_desc(eval_days, corrected) if eval_days is not None else f"{ctx.get('age_months', '?')}月龄"
            lines = [f"宝宝当前身高为 {height}cm（{age_desc}）。"]
            info = _band_info(height, 'height', db, ctx.get('gender'), eval_days)
            if info:
                ref, band, verdict = info
                lines.append(f"WHO 同龄参考中位数约 {ref.get('p50')}cm，身高状态：{verdict}（{band}）。")
                tip = _BAND_TEXT.get(band, (verdict, ''))[1]
                if tip:
                    lines.append(f"[提示] {tip}")
            lines += [
                "",
                "建议：",
                "1. 保证充足睡眠（生长激素主要在睡眠时分泌）",
                "2. 适当户外活动晒太阳",
                "3. 均衡营养，注意钙质摄入",
                "4. 定期测量，观察生长速度",
            ]
            return "\n".join(lines)

    return "身高增长反映宝宝的骨骼发育。建议：\n1. 每2-4周测量一次\n2. 保证充足睡眠\n3. 适当户外活动\n4. 均衡营养摄入\n\n如有异常，建议咨询儿科医生。"


def _answer_feeding(question, db, baby_id, baby_context=None):
    """回答喂养相关问题（结合月龄建议 + 近7天真实记录）"""
    age_months = (baby_context or {}).get('age_months')
    guide = FEEDING_GUIDELINES.get(int(age_months)) if age_months is not None and age_months <= 12 else None

    lines = []
    if guide:
        lines.append(f"[喂养] 喂养建议（{age_months}月龄）：")
        lines.append(f"建议每次奶量约 {guide[0]}ml，每天 {guide[1]} 次（{guide[2]}）")
    elif age_months is not None and age_months > 12:
        lines.append(f"[喂养] 喂养建议（{age_months}月龄）：")
        lines.append("已过周岁：以一日三餐家庭饮食为主，每天保证约 400-500ml 奶量")
    else:
        lines.append("[喂养] 喂养建议：")

    if db and baby_id:
        try:
            week_ago = (datetime.datetime.now() - datetime.timedelta(days=7)).strftime('%Y-%m-%d')
            rows = db.execute(
                'SELECT * FROM feeding_records WHERE baby_id = ? AND date(start_time) >= ? ORDER BY start_time DESC',
                (baby_id, week_ago)
            ).fetchall()
            records = [dict(r) for r in rows]
            if records:
                amounts = [r.get('amount') or 0 for r in records]
                avg_amount = sum(amounts) / len(amounts)
                lines.append(f"近7天共喂养 {len(records)} 次，日均 {len(records) / 7:.1f} 次")
                if avg_amount > 0:
                    ref = f"（建议 {guide[0]}ml）" if guide else ""
                    lines.append(f"平均每次 {avg_amount:.0f}ml{ref}")
                lines.append(f"最近一次喂养：{str(records[0].get('start_time', ''))[:16]}")
            else:
                lines.append("近7天暂无喂养记录，建议及时记录，便于分析喂养规律")
        except Exception as e:
            logger.warning('查询喂养记录失败: %s: %s', type(e).__name__, str(e)[:200])

    lines += [
        "通用建议：按需喂养、观察吃饱信号、喂后拍嗝防吐奶",
        "",
        "[!] 如有喂养困难，建议咨询哺乳顾问或儿科医生。",
    ]
    return "\n".join(lines)


def _answer_sleep(question, db, baby_id, baby_context=None):
    """回答睡眠相关问题（结合月龄建议 + 近7天真实记录）"""
    age_months = (baby_context or {}).get('age_months')
    guide = SLEEP_GUIDELINES.get(int(age_months)) if age_months is not None and age_months <= 12 else None

    lines = []
    if guide:
        lines.append(f"[睡眠] 睡眠建议（{age_months}月龄）：")
        lines.append(f"建议总睡眠约 {guide[0]} 小时/天，白天小睡 {guide[1]} 次，夜间连续睡眠 {guide[2]}")
    elif age_months is not None and age_months > 12:
        lines.append(f"[睡眠] 睡眠建议（{age_months}月龄）：")
        lines.append("周岁后一般每天总睡眠 11-14 小时，白天 1-2 次小睡")
    else:
        lines.append("[睡眠] 睡眠建议：")

    if db and baby_id:
        try:
            week_ago = (datetime.datetime.now() - datetime.timedelta(days=7)).strftime('%Y-%m-%d')
            rows = db.execute(
                'SELECT * FROM sleep_records WHERE baby_id = ? AND date(start_time) >= ? ORDER BY start_time DESC',
                (baby_id, week_ago)
            ).fetchall()
            records = [dict(r) for r in rows]
            if records:
                total_minutes = sum(r.get('duration_minutes') or 0 for r in records)
                daily_hours = total_minutes / 7 / 60
                summary = f"近7天共 {len(records)} 次睡眠记录，日均 {daily_hours:.1f} 小时"
                if guide:
                    summary += f"（建议 {guide[0]} 小时）"
                lines.append(summary)
                latest = records[0]
                latest_time = str(latest.get('start_time', ''))[:16]
                dur = latest.get('duration_minutes') or 0
                if dur:
                    lines.append(f"最近一次睡眠：{latest_time}，{dur // 60}小时{dur % 60:02d}分")
                else:
                    lines.append(f"最近一次睡眠：{latest_time}")
            else:
                lines.append("近7天暂无睡眠记录，建议记录作息，便于分析睡眠规律")
        except Exception as e:
            logger.warning('查询睡眠记录失败: %s: %s', type(e).__name__, str(e)[:200])

    lines += [
        "通用建议：建立固定睡前程序、保持卧室安静、避免睡前过度刺激",
        "",
        "每个宝宝睡眠需求不同，只要精神状态好就无需担心。",
    ]
    return "\n".join(lines)


def _answer_solid_food(question, db, baby_id, baby_context=None):
    """回答辅食相关问题"""
    age_months = baby_context['age_months'] if baby_context else 6
    guide = SOLID_FOOD_GUIDE.get(min(age_months, 12))

    if guide:
        return (
            f"[辅食] 辅食添加建议（{age_months}月龄）：\n"
            f"推荐食物：{'、'.join(guide['foods'])}\n"
            f"[提示] {guide['tips']}\n\n"
            f"[!] 注意逐一添加新食物，观察3天无过敏后再加下一种。"
        )

    tips = [
        "[辅食] 辅食添加指南：",
        "1. 6个月起开始添加辅食",
        "2. 从强化铁米粉开始",
        "3. 由稀到稠，由细到粗",
        "4. 1岁前避免蜂蜜、整颗坚果、盐",
    ]
    return "\n".join(tips)


def _answer_vaccine(question, db, baby_id, baby_context=None):
    """回答疫苗相关问题"""
    tips = [
        "[疫苗] 疫苗接种提醒：",
        "1. 按时接种一类疫苗（免费）",
        "2. 可根据情况选择二类疫苗（自费）",
        "3. 接种前确保宝宝身体健康",
        "4. 接种后观察30分钟再离开",
        "5. 记录每次接种信息",
        "",
        "具体接种计划请遵循当地疾控中心建议。"
    ]
    return "\n".join(tips)


def _answer_milestone(question, db, baby_id, baby_context=None):
    """回答发育里程碑问题"""
    age_months = baby_context['age_months'] if baby_context else None

    if age_months is not None and 1 <= age_months <= 12:
        milestones = MILESTONES.get(age_months, [])
        if milestones:
            return (
                f"[发育] {age_months}月龄发育要点：\n"
                f"本月宝宝可能达到的里程碑：{'、'.join(milestones)}\n\n"
                f"[提示] 每个宝宝发育速度不同，仅供参考。\n"
                f"如发育明显落后，建议咨询儿科医生评估。"
            )

    tips = [
        "[发育] 发育里程碑（大致参考）：",
        "2-3个月：抬头、微笑",
        "4-6个月：翻身、坐稳",
        "7-9个月：爬行、扶站",
        "10-12个月：独站、独走",
        "",
        "如果宝宝发育明显落后，建议咨询儿科医生评估。"
    ]
    return "\n".join(tips)


HEALTH_ADVICE = [
    {'kw': ['发烧', '发热', '烧到', '体温高'], 'title': '发烧护理', 'lines': [
        "38.5°C 以下优先物理降温：温水擦浴、减少衣物、多喂水",
        "38.5°C 以上或精神差，可在医生指导下使用退烧药",
        "3 个月以下宝宝发烧属急症，请立即就医",
        "出现高热不退、抽搐、呼吸急促、嗜睡，请立即就医",
    ]},
    {'kw': ['咳嗽', '咳痰'], 'title': '咳嗽护理', 'lines': [
        "保持室内湿度 50%~60%，多喂温水稀释痰液",
        "1 岁以上可睡前少量蜂蜜缓解夜咳（1 岁内禁用蜂蜜）",
        "避免烟雾、粉尘等刺激",
        "咳嗽伴喘息、呼吸急促或超过 1 周，请就医评估",
    ]},
    {'kw': ['鼻塞', '流鼻涕', '鼻涕', '感冒'], 'title': '感冒/鼻塞护理', 'lines': [
        "可用生理盐水滴鼻或喷雾缓解鼻塞，吸鼻器清理分泌物",
        "抬高头部（小婴儿整体垫高床垫一头）有助呼吸顺畅",
        "少量多次喂水/奶，保证休息",
        "伴持续高热、呼吸急促、拒奶、精神萎靡，请就医",
    ]},
    {'kw': ['拉肚子', '腹泻', '稀便', '大便稀'], 'title': '腹泻护理', 'lines': [
        "预防脱水是关键：继续喂养，可遵医嘱使用口服补液盐",
        "注意臀部护理，每次便后温水清洗、涂护臀膏防红臀",
        "记录大便次数、性状和尿量，就医时提供给医生",
        "出现血便、持续水样便、尿量明显减少、精神差，请立即就医",
    ]},
    {'kw': ['便秘', '排便困难', '几天没拉'], 'title': '便秘护理', 'lines': [
        "已加辅食的宝宝可增加水分和蔬果泥（西梅、梨等）",
        "可做腹部顺时针按摩，鼓励多活动",
        "纯母乳宝宝偶尔几天不拉但精神好、便软，多为攒肚无需担心",
        "伴呕吐、腹胀、大便带血或持续超过 1 周，请就医",
    ]},
    {'kw': ['湿疹', '红疹', '皮疹', '过敏'], 'title': '过敏/湿疹护理', 'lines': [
        "保湿是湿疹护理的基础：每天多次足量涂抹婴儿保湿霜",
        "温水短时间洗澡，避免过热和碱性沐浴露",
        "留意并回避可疑过敏原（食物、尘螨、宠物毛屑等）",
        "疑似食物过敏建议记录饮食日记，必要时就医做过敏原检测",
    ]},
    {'kw': ['黄疸'], 'title': '黄疸观察', 'lines': [
        "多吃多排有助退黄，保证足量喂养",
        "自然光线下观察皮肤黄染范围，记录变化",
        "生后 24 小时内出现、蔓延到手心脚心、伴嗜睡拒奶，请立即就医",
        "出生 2 周后黄疸仍明显（早产儿 4 周），建议就医测胆红素",
    ]},
    {'kw': ['吐奶', '溢奶', '拍嗝', '打嗝'], 'title': '吐奶/拍嗝', 'lines': [
        "喂奶后竖抱拍嗝 10~15 分钟，从下往上轻拍背部",
        "喂奶后 30 分钟内避免平躺和大动作，可右侧卧或斜坡卧",
        "少量多次喂养，避免过度喂养和吸入空气",
        "喷射性呕吐、吐出黄绿色液体、伴体重不增，请就医",
    ]},
    {'kw': ['哭闹', '夜哭', '肠绞痛'], 'title': '哭闹/肠绞痛', 'lines': [
        "先排查：饿、困、尿布、过冷过热、衣物不适",
        "肠绞痛多在生后 2~6 周，可用飞机抱、白噪音、腹部按摩安抚",
        "保持作息规律，避免过度刺激",
        "哭声尖锐、伴发热呕吐、腹部摸到包块或持续安抚无效，请就医",
    ]},
    {'kw': ['维生素d', '补钙', '鱼肝油', '缺钙'], 'title': '维生素 D / 钙补充', 'lines': [
        "纯母乳/混合喂养宝宝出生后数日起每天补充维生素 D 400 IU",
        "配方奶喂养且奶量达标的宝宝，一般无需额外补钙",
        "多晒太阳有助自身合成维生素 D（注意防晒伤）",
        "是否补钙请遵医嘱，不建议自行大量补充",
    ]},
]


def _answer_health(question, db, baby_id, baby_context=None):
    """回答健康相关问题（按症状细分）"""
    q = question.lower()
    for item in HEALTH_ADVICE:
        if any(kw in q for kw in item['kw']):
            return (
                "[健康] " + item['title'] + "：\n"
                + "\n".join(item['lines'])
                + "\n\n[!] 以上建议仅供参考，不能替代医疗诊断；症状持续或加重请及时就医。"
            )

    tips = [
        "[!] 健康提示：",
        "1. 注意观察宝宝精神状态",
        "2. 记录症状出现时间和变化",
        "3. 发烧超过38.5°C建议就医",
        "4. 呼吸困难、持续哭闹需立即就医",
        "",
        "[!] 我的建议仅供参考，不能替代医疗诊断。如有异常请及时就医。"
    ]
    return "\n".join(tips)


def _answer_diaper(question, db, baby_id, baby_context=None):
    """回答尿布/如厕相关问题（结合近7天真实记录；红屁股/尿布疹给专项建议）"""
    q = (question or '').lower()
    rash_mode = any(kw in q for kw in ['红屁股', '尿布疹', '红臀', '护臀', '屁股红'])

    lines = []
    if db and baby_id:
        try:
            week_ago = (datetime.datetime.now() - datetime.timedelta(days=7)).strftime('%Y-%m-%d')
            rows = db.execute(
                'SELECT * FROM diaper_records WHERE baby_id = ? AND date(change_time) >= ? ORDER BY change_time DESC',
                (baby_id, week_ago)
            ).fetchall()
            records = [dict(r) for r in rows]
            if records:
                today = datetime.datetime.now().strftime('%Y-%m-%d')
                today_count = sum(1 for r in records if str(r.get('change_time', ''))[:10] == today)
                wet = sum(1 for r in records if (r.get('diaper_type') or '') in ('wet', 'both'))
                dirty = sum(1 for r in records if (r.get('diaper_type') or '') in ('dirty', 'both'))
                lines.append(f"[数据] 近7天共记录更换 {len(records)} 次（今日 {today_count} 次），"
                             f"其中含小便 {wet} 次、大便 {dirty} 次")
                lines.append(f"最近一次更换：{str(records[0].get('change_time', ''))[:16]}")
                skin = next((r.get('skin_condition') for r in records if r.get('skin_condition')), None)
                if skin:
                    lines.append(f"近期皮肤状况记录：{skin}")
            else:
                lines.append("近7天暂无更换记录，建议在首页及时记录，便于观察排便规律和尿布疹风险")
        except Exception as e:
            logger.warning('查询尿布记录失败: %s: %s', type(e).__name__, str(e)[:200])

    if rash_mode:
        lines += [
            "",
            "[尿布] 红屁股（尿布疹）护理：",
            "1. 勤更换：大便后立即更换，其余每2-3小时检查一次",
            "2. 温水清洗，软布轻拍晾干（避免用力擦拭）",
            "3. 每次清洁后厚涂含氧化锌的护臀膏隔离",
            "4. 每天安排几次不穿尿布的「晾屁股」时间",
            "5. 避免湿巾用力擦和爽身粉（结块反而刺激皮肤）",
            "",
            "[!] 若出现破皮、渗液、水疱，或皮疹扩散到腹股沟以外、持续3天无好转，请就医"
            "（可能合并真菌感染，需要抗真菌药膏）。",
        ]
        return "\n".join(lines)

    lines += [
        "",
        "[尿布] 尿布护理建议：",
        "1. 每2-3小时检查一次尿布",
        "2. 排便后及时更换，预防红屁股",
        "3. 清洁后晾干再穿新尿布",
        "4. 可适当使用护臀膏",
        "",
        "如出现严重红屁股或皮疹持续不消退，请咨询医生。",
    ]
    return "\n".join(lines)


def _answer_vaccine_records(question, db, baby_id, baby_context=None):
    """回答疫苗记录相关问题（优先已到期/即将接种，其次近期已完成）"""
    if not db or not baby_id:
        return "请先在应用中添加宝宝并选择宝宝，我就能帮你查询疫苗接种安排了。"
    try:
        today = datetime.datetime.now().strftime('%Y-%m-%d')
        due = db.execute(
            "SELECT * FROM vaccines WHERE baby_id = ? AND status = 'pending' AND date(scheduled_date) < ? "
            "ORDER BY scheduled_date ASC LIMIT 5",
            (baby_id, today)
        ).fetchall()
        upcoming = db.execute(
            "SELECT * FROM vaccines WHERE baby_id = ? AND status = 'pending' AND date(scheduled_date) >= ? "
            "ORDER BY scheduled_date ASC LIMIT 5",
            (baby_id, today)
        ).fetchall()
        done = db.execute(
            "SELECT * FROM vaccines WHERE baby_id = ? AND status = 'completed' "
            "ORDER BY COALESCE(actual_date, scheduled_date) DESC LIMIT 5",
            (baby_id,)
        ).fetchall()
        if not due and not upcoming and not done:
            return "暂无疫苗接种记录。您可以在健康记录中添加疫苗接种信息。"

        lines = ["[疫苗] 疫苗接种情况："]

        def _dose_suffix(v):
            d = v.get('dose_number')
            return f"第{d}剂" if d else ""

        if due:
            lines += ["", "[!] 已到期待种（请尽快安排）："]
            for row in due:
                v = dict(row)
                lines.append(f"• {v.get('vaccine_name', '')}{_dose_suffix(v)} - 应种日期 {v.get('scheduled_date', '') or '待定'}")
        if upcoming:
            lines += ["", "即将接种："]
            for row in upcoming[:3]:
                v = dict(row)
                vtype = "（自费）" if (v.get('vaccine_type') or '') == 'paid' else ""
                lines.append(f"• {v.get('vaccine_name', '')}{_dose_suffix(v)}{vtype} - {v.get('scheduled_date', '') or '待定'}")
        if done:
            lines += ["", "近期已完成："]
            for row in done[:3]:
                v = dict(row)
                when = v.get('actual_date') or v.get('scheduled_date') or ''
                lines.append(f"[完成] {v.get('vaccine_name', '')}{_dose_suffix(v)} - {when}")
        return "\n".join(lines)
    except Exception as e:
        logger.warning('查询疫苗记录失败: %s: %s', type(e).__name__, str(e)[:200])
        return "查询疫苗记录时出现问题，请稍后再试。"


def _answer_query_feeding(db, baby_id, baby_context=None):
    """查询喂养记录（点击查询）"""
    if not db or not baby_id:
        return "请先在应用中添加宝宝并选择宝宝，我就能帮你查询喂养记录了。"
    try:
        name = (baby_context or {}).get('name', '宝宝')
        week_ago = (datetime.datetime.now() - datetime.timedelta(days=7)).strftime('%Y-%m-%d')
        rows = db.execute(
            'SELECT * FROM feeding_records WHERE baby_id = ? AND date(start_time) >= ? ORDER BY start_time DESC LIMIT 50',
            (baby_id, week_ago)
        ).fetchall()
        records = [dict(r) for r in rows]
        if not records:
            return (f"近7天还没有{name}的喂养记录。\n\n"
                    "提示：在首页点击\"喂奶\"即可快速记录，记录后我可以帮你分析喂养规律。")

        today = datetime.datetime.now().strftime('%Y-%m-%d')
        today_records = [r for r in records if str(r.get('start_time', ''))[:10] == today]
        amounts = [r.get('amount') or 0 for r in records]
        total = sum(amounts)
        avg = total / len(amounts) if amounts else 0

        lines = [f"[数据] {name}的喂养情况：", f"今天已喂养 {len(today_records)} 次",
                 f"近7天共 {len(records)} 次，日均 {len(records) / 7:.1f} 次"]
        if total > 0:
            lines.append(f"近7天总奶量约 {total:.0f}ml，平均每次 {avg:.0f}ml")
        lines.append("")
        lines.append("最近记录：")
        for r in records[:5]:
            t = str(r.get('start_time', ''))[:16]
            a = r.get('amount')
            amount_str = f"，{a:.0f}ml" if a else ""
            lines.append(f"• {t}{amount_str}")
        return "\n".join(lines)
    except Exception as e:
        logger.warning('查询喂养记录失败: %s: %s', type(e).__name__, str(e)[:200])
        return "查询喂养记录时出现问题，请稍后再试。"


def _answer_query_sleep(db, baby_id, baby_context=None):
    """查询睡眠记录（点击查询）"""
    if not db or not baby_id:
        return "请先在应用中添加宝宝并选择宝宝，我就能帮你查询睡眠记录了。"
    try:
        name = (baby_context or {}).get('name', '宝宝')
        week_ago = (datetime.datetime.now() - datetime.timedelta(days=7)).strftime('%Y-%m-%d')
        rows = db.execute(
            'SELECT * FROM sleep_records WHERE baby_id = ? AND date(start_time) >= ? ORDER BY start_time DESC LIMIT 50',
            (baby_id, week_ago)
        ).fetchall()
        records = [dict(r) for r in rows]
        if not records:
            return (f"近7天还没有{name}的睡眠记录。\n\n"
                    "提示：在首页点击\"睡眠\"即可快速记录。")

        total_minutes = sum(r.get('duration_minutes') or 0 for r in records)
        lines = [f"[数据] {name}的睡眠情况：",
                 f"近7天共 {len(records)} 次睡眠记录，日均 {total_minutes / 7 / 60:.1f} 小时",
                 "", "最近记录："]
        for r in records[:5]:
            t = str(r.get('start_time', ''))[:16]
            d = r.get('duration_minutes') or 0
            dur = f"，{d // 60}小时{d % 60:02d}分" if d else ""
            lines.append(f"• {t}{dur}")
        return "\n".join(lines)
    except Exception as e:
        logger.warning('查询睡眠记录失败: %s: %s', type(e).__name__, str(e)[:200])
        return "查询睡眠记录时出现问题，请稍后再试。"


def _answer_query_growth(db, baby_id, baby_context=None):
    """查询最新生长数据（点击查询）"""
    if not db or not baby_id:
        return "请先在应用中添加宝宝并选择宝宝，我就能帮你查询生长数据了。"
    try:
        record = db.execute(
            'SELECT * FROM growth_records WHERE baby_id = ? ORDER BY record_date DESC LIMIT 1',
            (baby_id,)
        ).fetchone()
        if record is None:
            return "暂无生长记录。建议在\"成长\"页定期记录身高体重，便于追踪发育趋势。"

        rec = dict(record)
        ctx = baby_context or {}
        eval_days, corrected = _growth_eval_days(ctx.get('birthday'), ctx.get('due_date'), rec.get('record_date'))
        age_desc = _age_desc(eval_days, corrected) if eval_days is not None else f"{ctx.get('age_months') or 0}月龄"
        lines = [f"[数据] 最近一次生长记录（{rec.get('record_date', '')}，{age_desc}）："]
        for label, metric, unit in (('体重', 'weight', 'kg'), ('身高', 'height', 'cm'), ('头围', 'head_circumference', 'cm')):
            v = rec.get(metric)
            if not v:
                continue
            info = _band_info(v, metric, db, ctx.get('gender'), eval_days)
            if info:
                ref, band, verdict = info
                lines.append(f"• {label} {v}{unit}（参考中位 {ref.get('p50')}{unit}，{verdict}，{band}）")
            else:
                lines.append(f"• {label} {v}{unit}")
        return "\n".join(lines)
    except Exception as e:
        logger.warning('查询生长数据失败: %s: %s', type(e).__name__, str(e)[:200])
        return "查询生长数据时出现问题，请稍后再试。"


def _answer_head_circumference(question, db, baby_id, baby_context=None):
    """回答头围相关问题（WHO 百分位评估）"""
    if db and baby_id:
        records = db.execute('SELECT * FROM growth_records WHERE baby_id = ? ORDER BY record_date DESC LIMIT 1', (baby_id,)).fetchone()
        if records and records['head_circumference']:
            hc = float(records['head_circumference'])
            ctx = baby_context or {}
            eval_days, corrected = _growth_eval_days(ctx.get('birthday'), ctx.get('due_date'), records['record_date'])
            age_desc = _age_desc(eval_days, corrected) if eval_days is not None else f"{ctx.get('age_months', '?')}月龄"
            info = _band_info(hc, 'head_circumference', db, ctx.get('gender'), eval_days)
            if info:
                ref, band, verdict = info
                tip = _BAND_TEXT.get(band, (verdict, ''))[1]
                return (
                    f"宝宝当前头围为 {hc}cm（{age_desc}）。\n"
                    f"WHO 同龄参考中位数约 {ref.get('p50')}cm，评估：{verdict}（{band}）。"
                    + (f"\n[提示] {tip}" if tip else "")
                    + "\n\n[提示] 头围反映大脑发育情况，重点看曲线趋势是否平稳。\n"
                    "如增长突然过快或过慢、或持续超出参考范围，请咨询医生。"
                )
            return (
                f"宝宝当前头围为 {hc}cm。\n\n"
                f"[提示] 头围反映大脑发育情况。\n"
                f"建议定期测量，如有异常（增长过快或过慢）请咨询医生。"
            )

    return "头围是衡量大脑发育的指标之一。建议定期测量并记录。"


def _answer_bmi(question, db, baby_id, baby_context=None):
    """回答BMI相关问题（WHO 百分位评估）"""
    if db and baby_id:
        records = db.execute('SELECT * FROM growth_records WHERE baby_id = ? ORDER BY record_date DESC LIMIT 1', (baby_id,)).fetchone()
        if records and records['weight'] and records['height']:
            weight = float(records['weight'])
            height_cm = float(records['height'])
            bmi = weight / (height_cm / 100) ** 2
            ctx = baby_context or {}
            eval_days, corrected = _growth_eval_days(ctx.get('birthday'), ctx.get('due_date'), records['record_date'])
            age_desc = _age_desc(eval_days, corrected) if eval_days is not None else f"{ctx.get('age_months', '?')}月龄"
            head = f"宝宝当前BMI为 {bmi:.1f}（体重{weight}kg，身高{height_cm}cm，{age_desc}）"
            info = _band_info(bmi, 'bmi', db, ctx.get('gender'), eval_days)
            if info:
                ref, band, verdict = info
                tip = _BAND_TEXT.get(band, (verdict, ''))[1]
                return (
                    f"{head}\n"
                    f"WHO 同龄 BMI 参考中位数约 {ref.get('p50')}，评估：{verdict}（{band}）。"
                    + (f"\n[提示] {tip}" if tip else "")
                    + "\n\n[!] BMI 供参考，如有超重/消瘦担忧建议咨询儿科医生做专业评估。"
                )
            return (
                f"{head}\n\n"
                f"[提示] BMI需要结合年龄和性别评估。\n"
                f"如有疑问，建议咨询儿科医生进行专业评估。"
            )

    return "BMI需要身高和体重数据才能计算。请先在生长记录中记录最新数据。"


def _answer_age(baby_context=None):
    """回答月龄相关问题（真实天数 + 早产儿矫正月龄）"""
    if not baby_context:
        return "请先在应用中添加宝宝信息，我可以帮您计算月龄。"
    birthday = baby_context.get('birthday', '')
    days = growth_utils.calc_age_days(birthday)
    exact_months, _ = growth_utils.corrected_age_months(birthday, None)
    lines = []
    if days is not None:
        lines.append(f"宝宝现在约 {exact_months} 个月大（已出生 {days} 天）。")
    else:
        lines.append(f"宝宝现在 {baby_context.get('age_months', '?')} 个月大。")
    due = baby_context.get('due_date')
    if due:
        corrected_months, is_corr = growth_utils.corrected_age_months(birthday, due)
        if is_corr:
            lines.append(f"按预产期（{due}）计算的矫正月龄约 {corrected_months} 个月（早产儿常用矫正月龄评估，一般参考到 2 岁）。")
    lines.append(f"出生日期：{birthday}")
    return "\n".join(lines)


def _answer_intro():
    """自我介绍"""
    return (
        "[介绍] 你好！我是育儿宝 AI 助手，可以帮你：\n\n"
        "[数据] 分析宝宝的生长发育数据\n"
        "[喂养] 提供喂养和睡眠建议\n"
        "[辅食] 指导辅食添加\n"
        "[疫苗] 解答疫苗接种疑问\n"
        "[发育] 评估发育里程碑\n"
        "[!] 提供健康护理建议\n\n"
        "你可以直接问我任何育儿问题，比如：\n"
        "• \"宝宝体重正常吗？\"\n"
        "• \"6个月宝宝该添加什么辅食？\"\n"
        "• \"宝宝睡眠不好怎么办？\"\n\n"
        "当前模式：本地规则引擎（无需联网）\n"
        "[!] 我的建议仅供参考，具体情况请咨询专业医生。"
    )


def _answer_general(question, baby_context=None):
    """通用回答"""
    ctx = ""
    if baby_context:
        ctx = f"\n\n[数据] 当前宝宝数据：{baby_context.get('name', '宝宝')}，{baby_context.get('age_months', '?')}个月"
    return (
        "感谢你的提问！我可以帮你解答以下方面的问题：\n\n"
        "• 体重身高发育评估\n"
        "• 喂养和奶量建议\n"
        "• 睡眠问题指导\n"
        "• 辅食添加指南\n"
        "• 疫苗接种提醒\n"
        "• 发育里程碑评估\n"
        "• 健康护理建议\n\n"
        f"你可以点击下方的快捷问题，或直接输入你的问题。{ctx}"
    )


# ==================== LLM 调用层 ====================
# 支持：Ollama 本地 / 厂商 API（OpenAI 兼容）
# 灵感来源：DeepSeek Harness Provider Profile 模式 + 健康档案项目 ai-provider.ts

# 内置厂商模板（2025 下半年最新模型，参考健康档案项目设计）
# 每个厂商包含：文本模型、视觉模型（可选）、默认 API 地址、最大输出 Token
# 提供商分组顺序（UI 排序用）
PROVIDER_GROUPS = [
    {'id': 'international', 'name': '国际厂商', 'providers': ['openai', 'anthropic']},
    {'id': 'china', 'name': '国内厂商', 'providers': ['deepseek', 'kimi', 'qwen', 'glm', 'doubao']},
    {'id': 'local', 'name': '本地部署', 'providers': ['ollama', 'lmstudio', 'custom']},
]

PROVIDER_TEMPLATES = {
    'openai': {
        'name': 'OpenAI',
        'group': 'international',
        'base_url': 'https://api.openai.com/v1',
        'api_url': 'https://api.openai.com/v1/chat/completions',
        'models': ['gpt-5.6-sol', 'gpt-5.6-terra', 'gpt-5.6-luna', 'gpt-5.5', 'gpt-5.4', 'gpt-5.4-mini', 'gpt-5.4-nano', 'gpt-chat-latest'],
        'default_model': 'gpt-5.4-mini',
        'vision_models': ['gpt-5.6-sol', 'gpt-5.6-terra', 'gpt-5.6-luna', 'gpt-5.5', 'gpt-5.4'],
        'default_vision_model': 'gpt-5.4',
        'max_output_tokens': 32768,
        'model_hint': '需要 OpenAI API Key',
        'auth_header': 'Authorization',
        'auth_format': 'Bearer {api_key}',
    },
    'anthropic': {
        'name': 'Anthropic (Claude)',
        'group': 'international',
        'base_url': 'https://api.anthropic.com/v1',
        'api_url': 'https://api.anthropic.com/v1/messages',
        'models': ['claude-opus-4-8', 'claude-opus-4-7', 'claude-opus-4-6', 'claude-sonnet-4-6', 'claude-sonnet-4-5-20250929', 'claude-haiku-4-5-20251001'],
        'default_model': 'claude-sonnet-4-6',
        'vision_models': ['claude-opus-4-8', 'claude-opus-4-7', 'claude-opus-4-6', 'claude-sonnet-4-6'],
        'default_vision_model': 'claude-sonnet-4-6',
        'max_output_tokens': 32768,
        'model_hint': '需要 Anthropic API Key',
        'auth_header': 'x-api-key',
        'auth_format': '{api_key}',
        'extra_headers': {'anthropic-version': '2023-06-01'},
    },
    'deepseek': {
        'name': 'DeepSeek',
        'group': 'china',
        'base_url': 'https://api.deepseek.com/v1',
        'api_url': 'https://api.deepseek.com/v1/chat/completions',
        'models': ['deepseek-v4-pro', 'deepseek-v4-flash'],
        'default_model': 'deepseek-v4-flash',
        'vision_models': [],
        'default_vision_model': '',
        'max_output_tokens': 16384,
        'model_hint': '',
        'auth_header': 'Authorization',
        'auth_format': 'Bearer {api_key}',
    },
    'kimi': {
        'name': 'Kimi (月之暗面)',
        'group': 'china',
        'base_url': 'https://api.moonshot.cn/v1',
        'api_url': 'https://api.moonshot.cn/v1/chat/completions',
        'models': ['kimi-k3', 'kimi-k2', 'moonshot-v1-8k', 'moonshot-v1-32k', 'moonshot-v1-128k'],
        'default_model': 'kimi-k3',
        'vision_models': ['kimi-k3', 'kimi-k2', 'moonshot-v1-8k', 'moonshot-v1-32k'],
        'default_vision_model': 'kimi-k3',
        'max_output_tokens': 65536,
        'model_hint': 'Kimi 支持文档理解，可上传 PDF 问答',
        'auth_header': 'Authorization',
        'auth_format': 'Bearer {api_key}',
    },
    'qwen': {
        'name': '通义千问 (阿里)',
        'group': 'china',
        'base_url': 'https://dashscope.aliyuncs.com/compatible-mode/v1',
        'api_url': 'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions',
        'models': ['qwen3.8-flash', 'qwen3-coder-next', 'qwen-coder-plus', 'qwen-max', 'qwen-plus', 'qwen-turbo', 'qwen2.5-72b-instruct'],
        'default_model': 'qwen-turbo',
        'vision_models': ['qwen-vl-plus', 'qwen-vl-max'],
        'default_vision_model': 'qwen-vl-plus',
        'max_output_tokens': 32768,
        'model_hint': '视觉模型需单独开通',
        'auth_header': 'Authorization',
        'auth_format': 'Bearer {api_key}',
    },
    'glm': {
        'name': '智谱 GLM',
        'group': 'china',
        'base_url': 'https://open.bigmodel.cn/api/paas/v4',
        'api_url': 'https://open.bigmodel.cn/api/paas/v4/chat/completions',
        'models': ['glm-5.3-flash', 'glm-5.2', 'glm-5.1', 'glm-5', 'glm-5-turbo', 'glm-4-flash', 'glm-4', 'glm-4-plus', 'glm-4-air'],
        'default_model': 'glm-5.3-flash',
        'vision_models': ['glm-5.3-flash', 'glm-5v-turbo', 'glm-4v', 'glm-4v-plus'],
        'default_vision_model': 'glm-5.3-flash',
        'max_output_tokens': 32768,
        'model_hint': '',
        'auth_header': 'Authorization',
        'auth_format': 'Bearer {api_key}',
    },
    'doubao': {
        'name': '豆包 (字节)',
        'group': 'china',
        'base_url': 'https://ark.cn-beijing.volces.com/api/v3',
        'api_url': 'https://ark.cn-beijing.volces.com/api/v3/chat/completions',
        'models': ['doubao-1.5-pro-256k', 'doubao-1.5-pro-32k', 'doubao-1.5-lite-32k', 'doubao-1.5-lite-128k', 'doubao-seed-1-8-251228', 'doubao-1.5-character-32k'],
        'default_model': 'doubao-1.5-pro-32k',
        'vision_models': ['doubao-1.5-vision-32k', 'doubao-seed-1-6-vision-250815'],
        'default_vision_model': 'doubao-1.5-vision-32k',
        'max_output_tokens': 32768,
        'model_hint': '火山方舟如要求使用推理接入点，请填写控制台中的 ep-... 接入点 ID',
        'auth_header': 'Authorization',
        'auth_format': 'Bearer {api_key}',
    },
    'ollama': {
        'name': 'Ollama (本地)',
        'group': 'local',
        'base_url': 'http://localhost:11434',
        'api_url': 'http://localhost:11434/api/chat',
        'models': ['qwen2.5:7b', 'qwen3:8b', 'llama4:scout', 'llama4:maverick', 'llama3.1:8b', 'llava:7b', 'llava:13b', 'bakllava:7b'],
        'default_model': 'qwen3:8b',
        'vision_models': ['llava:7b', 'llava:13b', 'bakllava:7b'],
        'default_vision_model': 'llava:7b',
        'max_output_tokens': 8192,
        'model_hint': '视觉模型需先 ollama pull llava',
        'auth_header': '',
        'auth_format': '',
    },
    'lmstudio': {
        'name': 'LM Studio (本地)',
        'group': 'local',
        'base_url': 'http://localhost:1234/v1',
        'api_url': 'http://localhost:1234/v1/chat/completions',
        'models': ['qwen2.5-7b-instruct', 'llama-4-scout-17b-16e-instruct', 'llama-3.2-3b-instruct'],
        'default_model': 'qwen2.5-7b-instruct',
        'vision_models': ['llava-v1.6-34b'],
        'default_vision_model': 'llava-v1.6-34b',
        'max_output_tokens': 8192,
        'model_hint': '视觉模型需在 LM Studio 中下载',
        'auth_header': 'Authorization',
        'auth_format': 'Bearer {api_key}',
    },
    'custom': {
        'name': '自定义 (OpenAI 兼容)',
        'group': 'local',
        'base_url': '',
        'api_url': '',
        'models': [],
        'default_model': '',
        'vision_models': [],
        'default_vision_model': '',
        'max_output_tokens': 32768,
        'model_hint': '支持任何 OpenAI 兼容 API（如 OneAPI、NewAPI 等）',
        'auth_header': 'Authorization',
        'auth_format': 'Bearer {api_key}',
    },
}

# 运行时配置（通过设置页修改）
LLM = {
    'provider': '',       # 选中的 provider id，空字符串表示关闭
    'api_key': '',       # 厂商 API Key (AES-256-GCM 加密存储)
    'api_url': '',       # 自定义 API 地址（可选，覆盖模板）
    'base_url': '',      # 自定义 base URL（可选，覆盖模板）
    'model': '',         # 选中的文本模型
    'vision_model': '',  # 选中的视觉模型
    'vision_enabled': False,  # 是否启用视觉功能
    'temperature': 0.7,  # 回复随机性（0~1）
    'max_tokens': 0,     # 最大回复长度，0 表示使用厂商默认
    'timeout': 30,       # 请求超时（秒）
    'system_prompt': '', # 自定义系统提示词，空表示使用内置育儿顾问提示词
    'consent_cloud': False,   # 云端厂商数据外发同意开关
    'custom_models': {}, # 按提供商持久化的自定义模型列表 {provider: [models]}
}

# LLM 配置锁（P1-7: 保护多线程并发读写 LLM 字典）
import threading
_llm_lock = threading.Lock()


def _effective_timeout():
    """读取超时配置，非法或 <=0 时回退默认 15 秒"""
    try:
        timeout = int(LLM.get('timeout', 15))
    except (TypeError, ValueError):
        return 15
    return timeout if timeout > 0 else 15


SYSTEM_PROMPT = """你是「育儿宝」的 AI 育儿顾问，服务对象是用这个应用记录宝宝日常的家长，擅长婴幼儿生长发育、喂养、睡眠、护理与常见问题应对。

【必须遵守】
1. 一切宝宝数据（喂养次数、睡眠时长、身高体重、记录明细）都必须来自工具查询结果，绝不能凭印象编造或估算；工具没返回就直说「暂时查不到」。
2. 育儿知识（怎么做、为什么、注意事项）优先用 search_knowledge 检索站内知识库/健康百科并据此作答；检索不到时明确说明这是通用育儿建议、不是站内资料。
3. 判断生长发育是否达标，先用 assess_growth 拿到 WHO 百分位结果再解读，不要自己按「标准体重表」心算。
4. 写入记录（喂养/睡眠/尿布/生长/健康）必须有用户明确表达；类型、数量、时间缺一不可，信息含糊时先追问一句再写，绝不猜。时间是已发生的事，不要填未来时间；用户只说「今天喂了奶」没给时间，可以不传时间由系统取当前时刻。
5. 记录写错了要改，用 update_record 就地改（先 query_records 拿到 ID，再只改要改的字段）；不要「删掉再重加」——那会换掉记录 ID，还会连带丢字段。
6. 删除记录必须用户明确要求，动手前先向用户复述将删除什么。
7. 一句话里有多件事（如「今天喂了 5 次、睡了 3 觉」）就逐条写成多条记录，不要合并成一条。

【医疗安全红线】
- 只给护理建议与就医提示；不下诊断、不开处方、不给具体用药剂量。
- 出现以下任一情况，先建议尽快就医，不要只给家庭护理：3 月龄以下发热；持续高热或发热超过 3 天；精神萎靡、嗜睡、抽搐、呼吸急促或困难；拒食、尿量明显减少、囟门凹陷等脱水表现；皮疹伴发热；持续呕吐或便血。

【表达风格】
- 中文，简洁亲切，像有经验的育儿嫂在跟家长聊天。
- 先给结论或最该做的事，再简短说理由；必要时分点，不要长篇大论。
- 引用数据时带上具体数字与时间范围（如「近 7 天」），让家长能对上记录。
- 涉及健康判断时，末尾附一句「以上仅供参考，如不放心请及时就医」。"""


def call_llm(question, baby_context=None, history=None, model_override=None):
    """
    调用 LLM，返回 (success, answer)
    失败返回 (False, None)，调用方回退到规则引擎

    Args:
        question: 用户问题
        baby_context: 宝宝数据字符串（可为 None）
        history: 历史消息列表 [{'role': 'user', 'content': '...'}, ...]（可为 None）
        model_override: 临时指定本次调用的模型（如 AI 对话页选择），优先于全局配置
    """
    if not LLM['provider']:
        return False, None

    if LLM.get('provider') not in ('', None, 'ollama', 'lmstudio') and not LLM.get('consent_cloud'):
        logger.warning('未同意数据外发条款，请在设置中开启')
        return False, None

    try:
        system_prompt = LLM.get('system_prompt') or SYSTEM_PROMPT
        messages = [{'role': 'system', 'content': system_prompt}]
        if baby_context:
            messages.append({'role': 'system', 'content': f'宝宝数据：{baby_context}'})

        # 添加历史消息（多轮对话）
        if history:
            messages.extend(history)

        # 最后添加当前问题
        messages.append({'role': 'user', 'content': question})

        provider = LLM['provider']

        if provider == 'ollama':
            return _call_ollama(messages, model_override)
        else:
            return _call_openai_compat(messages, model_override)
    except Exception as exc:
        logger.warning('LLM 调用失败: %s: %s', type(exc).__name__, str(exc)[:200])
    return False, None


def _call_ollama(messages, model_override=None):
    """Ollama 本地 API"""
    url = LLM.get('api_url') or PROVIDER_TEMPLATES['ollama']['api_url']
    parsed = urllib.parse.urlparse(url)
    if parsed.hostname not in ('localhost', '127.0.0.1', '::1') and parsed.scheme != 'https':
        logger.warning('非本地服务必须使用 HTTPS 地址')
        return False, None
    payload = {
        'model': model_override or LLM.get('model') or PROVIDER_TEMPLATES['ollama']['default_model'],
        'messages': messages,
        'stream': False,
        'options': {'temperature': LLM.get('temperature') or 0.7}
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json'}
    )
    with urllib.request.urlopen(req, timeout=_effective_timeout()) as resp:
        result = json.loads(resp.read().decode('utf-8'))
        if 'message' in result:
            return True, result['message']['content']
    return False, None


def _call_openai_compat(messages, model_override=None):
    """OpenAI 兼容格式（DeepSeek、通义千问、Kimi、自定义等）"""
    provider_id = LLM['provider']
    template = PROVIDER_TEMPLATES.get(provider_id, {})

    url = LLM.get('api_url') or template.get('api_url', '')
    model = model_override or LLM.get('model') or template.get('default_model', '')

    parsed = urllib.parse.urlparse(url)
    if parsed.hostname not in ('localhost', '127.0.0.1', '::1') and parsed.scheme != 'https':
        logger.warning('非本地服务必须使用 HTTPS 地址')
        return False, None

    payload = {
        'model': model,
        'messages': messages,
        'temperature': LLM.get('temperature') or 0.7,
        'max_tokens': LLM.get('max_tokens') or template.get('max_output_tokens', 1024),
    }
    # 解密 API Key（存储时加密）
    api_key_plain = decrypt_api_key(LLM.get('api_key', ''))

    # 构建请求头
    auth_header = template.get('auth_header', 'Authorization')
    auth_format = template.get('auth_format', 'Bearer {api_key}')
    headers = {'Content-Type': 'application/json'}
    if auth_header and auth_format:
        headers[auth_header] = auth_format.format(api_key=api_key_plain)

    # 额外请求头（如 Anthropic 需要版本号）
    extra_headers = template.get('extra_headers', {})
    headers.update(extra_headers)

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode('utf-8'),
        headers=headers
    )
    with urllib.request.urlopen(req, timeout=_effective_timeout()) as resp:
        result = json.loads(resp.read().decode('utf-8'))
        if 'choices' in result and result['choices']:
            return True, result['choices'][0]['message']['content']
    return False, None


def chat_with_llm(question, db=None, baby_id=None):
    """优先 LLM，失败回退规则引擎"""
    # 构建宝宝数据上下文
    context = None
    if db and baby_id:
        try:
            baby = db.execute('SELECT * FROM babies WHERE id = ?', (baby_id,)).fetchone()
            if baby:
                age = get_age_months(baby['birthday'])
                growth = db.execute(
                    'SELECT * FROM growth_records WHERE baby_id = ? ORDER BY record_date DESC LIMIT 1',
                    (baby_id,)
                ).fetchone()
                parts = [f'月龄{age}个月']
                if growth:
                    if growth['weight']: parts.append(f'体重{growth["weight"]}kg')
                    if growth['height']: parts.append(f'身高{growth["height"]}cm')
                context = '，'.join(parts)
        except Exception as exc:
            logger.warning('构建宝宝上下文失败: %s: %s', type(exc).__name__, str(exc)[:200])

    # 尝试 LLM（云端厂商需先取得用户同意）
    if LLM.get('provider') not in ('', None, 'ollama', 'lmstudio') and not LLM.get('consent_cloud'):
        return chat_response(question, db, baby_id)

    if LLM['provider']:
        ok, answer = call_llm(question, context)
        if ok:
            return answer

    # 回退规则引擎
    return chat_response(question, db, baby_id)


def get_llm_status():
    """获取 LLM 状态（不含 API Key 明文，只返回脱敏状态）"""
    provider = LLM['provider']
    api_key_stored = LLM.get('api_key', '')
    api_key_plain = decrypt_api_key(api_key_stored) if api_key_stored else ''
    api_key_configured = bool(api_key_plain)
    api_key_masked = mask_api_key(api_key_plain) if api_key_configured else ''
    
    # 获取当前提供商的自定义模型
    custom_models_dict = LLM.get('custom_models', {})
    if isinstance(custom_models_dict, list):
        custom_models_dict = {provider: custom_models_dict} if provider else {}
    current_custom = custom_models_dict.get(provider, [])
    
    base = {
        'temperature': LLM.get('temperature', 0.7),
        'max_tokens': LLM.get('max_tokens', 0),
        'timeout': LLM.get('timeout', 30),
        'system_prompt': LLM.get('system_prompt', ''),
        'consent_cloud': LLM.get('consent_cloud', False),
        'vision_enabled': LLM.get('vision_enabled', False),
        'api_url': LLM.get('api_url', ''),
        'base_url': LLM.get('base_url', ''),
        'api_key_configured': api_key_configured,
        'api_key_masked': api_key_masked,
        'custom_models': current_custom,
    }
    if not provider:
        return {'enabled': False, 'provider': '', 'model': '', 'provider_name': '', **base}

    template = PROVIDER_TEMPLATES.get(provider, {})
    status = {
        'enabled': True,
        'provider': provider,
        'model': LLM.get('model') or template.get('default_model', ''),
        'vision_model': LLM.get('vision_model') or template.get('default_vision_model', ''),
        'provider_name': template.get('name', provider),
        'base_url': LLM.get('base_url') or template.get('base_url', ''),
        **base,
    }

    # 本地服务额外返回已安装的模型列表
    if provider == 'ollama':
        ok, result = get_ollama_models()
        if ok:
            status['local_models'] = result
        else:
            status['local_models'] = []
            status['local_models_error'] = result
    elif provider == 'lmstudio':
        ok, result = get_lmstudio_models()
        if ok:
            status['local_models'] = result
        else:
            status['local_models'] = []
            status['local_models_error'] = result

    return status


def configure_llm(config):
    """更新 LLM 配置（自动加密 API Key）"""
    global LLM
    with _llm_lock:
        # 处理自定义模型列表 - 按提供商持久化
        if 'custom_models' in config and 'provider' in config:
            provider = config['provider']
            new_custom = config.get('custom_models', [])
            if isinstance(new_custom, list):
                # 确保 custom_models 是 dict 格式
                if not isinstance(LLM.get('custom_models'), dict):
                    LLM['custom_models'] = {}
                # 更新当前提供商的自定义模型
                LLM['custom_models'][provider] = [str(m).strip() for m in new_custom if m and len(str(m).strip()) < 100]

        for k, v in config.items():
            if k in LLM and k != 'custom_models':  # custom_models 已单独处理
                # API Key 需要加密存储
                if k == 'api_key' and v:
                    LLM[k] = encrypt_api_key(v)
                else:
                    LLM[k] = v
        # 如果设置了 base_url，自动更新 api_url（如果用户没有自定义 api_url）
        if 'base_url' in config and config.get('base_url'):
            base_url = config['base_url'].rstrip('/')
            provider = LLM.get('provider', '')
            template = PROVIDER_TEMPLATES.get(provider, {})
            # 只有当 api_url 为空或与模板相同时才自动更新
            if not LLM.get('api_url') or LLM.get('api_url') == template.get('api_url', ''):
                # 根据 base_url 推导 api_url
                if provider == 'ollama':
                    LLM['api_url'] = f'{base_url}/api/chat'
                elif provider in ('openai', 'deepseek', 'kimi', 'qwen', 'glm', 'doubao', 'lmstudio', 'custom'):
                    LLM['api_url'] = f'{base_url}/v1/chat/completions'
        return LLM


def get_provider_templates():
    """获取所有内置厂商模板（供前端下拉选择），包含用户自定义模型"""
    templates = {}
    custom_models_dict = LLM.get('custom_models', {})
    if isinstance(custom_models_dict, list):
        # 兼容旧格式：列表转为字典
        custom_models_dict = {LLM.get('provider', ''): custom_models_dict}

    for id, t in PROVIDER_TEMPLATES.items():
        # 合并内置模型和用户自定义模型
        all_models = list(t.get('models', []))
        provider_custom = custom_models_dict.get(id, [])
        for cm in provider_custom:
            if cm and cm not in all_models:
                all_models.append(cm)

        templates[id] = {
            'name': t['name'],
            'group': t.get('group', 'custom'),
            'base_url': t.get('base_url', ''),
            'api_url': t.get('api_url', ''),
            'models': all_models,
            'default_model': t.get('default_model', ''),
            'vision_models': t.get('vision_models', []),
            'default_vision_model': t.get('default_vision_model', ''),
            'needs_api_key': id not in ('ollama', 'lmstudio'),
            'model_hint': t.get('model_hint', ''),
            'auth_header': t.get('auth_header', 'Authorization'),
        }
    return templates


def get_provider_groups():
    """获取提供商分组信息（供前端分组显示）"""
    return PROVIDER_TEMPLATES and [
        {
            'id': g['id'],
            'name': g['name'],
            'providers': [p for p in g['providers'] if p in PROVIDER_TEMPLATES]
        }
        for g in PROVIDER_GROUPS
    ]


def get_ollama_models(api_url=None):
    """
    获取 Ollama 本地已安装的模型列表
    返回: (success, models_list 或 error_message)
    """
    import urllib.request
    import urllib.parse

    url = api_url or LLM.get('api_url') or PROVIDER_TEMPLATES['ollama']['api_url']
    parsed = urllib.parse.urlparse(url)

    # 安全：仅允许本地地址
    if parsed.hostname not in ('localhost', '127.0.0.1', '::1'):
        return False, '仅支持本地 Ollama'

    try:
        # 从 /api/chat 地址推导出 /api/tags
        base_url = url.rsplit('/api/chat', 1)[0]
        tags_url = base_url + '/api/tags'
        req = urllib.request.Request(tags_url)
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            models = [m.get('name', '') for m in data.get('models', []) if m.get('name')]
            return True, models
    except Exception as e:
        return False, f'无法连接 Ollama: {str(e)[:100]}'


def get_lmstudio_models(api_url=None):
    """
    获取 LM Studio 本地已加载的模型列表
    返回: (success, models_list 或 error_message)
    """
    import urllib.request
    import urllib.parse

    url = api_url or LLM.get('api_url') or PROVIDER_TEMPLATES['lmstudio']['api_url']
    parsed = urllib.parse.urlparse(url)

    # 安全：仅允许本地地址
    if parsed.hostname not in ('localhost', '127.0.0.1', '::1'):
        return False, '仅支持本地 LM Studio'

    try:
        # 从 /v1/chat/completions 推导出 /v1/models
        base_url = url.rsplit('/v1/chat/completions', 1)[0]
        models_url = base_url + '/v1/models'
        req = urllib.request.Request(models_url)
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            models = [m.get('id', '') for m in data.get('data', []) if m.get('id')]
            return True, models
    except Exception as e:
        return False, f'无法连接 LM Studio: {str(e)[:100]}'


def test_llm_connection():
    """
    测试当前 LLM 配置是否可用
    返回: (success, message, response)
    """
    if not LLM.get('provider'):
        return False, '未配置 LLM', None

    try:
        ok, answer = call_llm('你好，请回复"连接成功"三个字。')
        if ok:
            return True, '连接成功', answer[:200] if answer else ''
        else:
            return False, 'LLM 调用失败，请检查配置', None
    except Exception as e:
        return False, f'连接失败: {str(e)[:200]}', None


# ==================== 周报/月报生成引擎 ====================

def generate_weekly_report(db, baby_id):
    """生成周报"""
    return _generate_period_report(db, baby_id, days=7, period_name='周')


def generate_monthly_report(db, baby_id):
    """生成月报"""
    return _generate_period_report(db, baby_id, days=30, period_name='月')


def _generate_period_report(db, baby_id, days, period_name):
    """生成指定周期的报告"""
    baby = db.execute('SELECT * FROM babies WHERE id = ?', (baby_id,)).fetchone()
    if not baby:
        return {'success': False, 'message': '宝宝不存在'}

    baby = dict(baby)
    age_months = get_age_months(baby.get('birthday', ''))
    today = datetime.datetime.now().date()
    start_date = today - datetime.timedelta(days=days)
    start_str = start_date.strftime('%Y-%m-%d')

    # === 生长数据 ===
    growth_all = db.execute(
        'SELECT * FROM growth_records WHERE baby_id = ? ORDER BY record_date', (baby_id,)
    ).fetchall()
    growth_period = [dict(r) for r in growth_all if r['record_date'] >= start_str]
    growth_latest = growth_period[-1] if growth_period else (dict(growth_all[-1]) if growth_all else None)

    growth_stats = None
    if len(growth_period) >= 2:
        first = growth_period[0]
        last = growth_period[-1]
        growth_stats = {
            'weight_change': round((last['weight'] or 0) - (first['weight'] or 0), 2) if last.get('weight') and first.get('weight') else None,
            'height_change': round((last['height'] or 0) - (first['height'] or 0), 1) if last.get('height') and first.get('height') else None,
            'record_count': len(growth_period),
        }

    # === 喂养数据 ===
    feeding_period = db.execute(
        """SELECT * FROM feeding_records WHERE baby_id = ?
           AND date(start_time) >= ? ORDER BY start_time""",
        (baby_id, start_str)
    ).fetchall()
    feeding_list = [dict(r) for r in feeding_period]

    feeding_stats = None
    if feeding_list:
        total_feedings = len(feeding_list)
        daily_avg = round(total_feedings / days, 1)
        amounts = [r.get('amount', 0) or 0 for r in feeding_list]
        avg_amount = round(sum(amounts) / len(amounts), 0) if amounts else 0
        total_amount = sum(amounts)

        # 喂养类型分布
        types = {}
        for r in feeding_list:
            t = r.get('feeding_type', '未知')
            types[t] = types.get(t, 0) + 1

        feeding_stats = {
            'total': total_feedings,
            'daily_avg': daily_avg,
            'avg_amount': avg_amount,
            'total_amount': total_amount,
            'type_distribution': types,
        }

    # === 睡眠数据 ===
    sleep_period = db.execute(
        """SELECT * FROM sleep_records WHERE baby_id = ?
           AND date(start_time) >= ? ORDER BY start_time""",
        (baby_id, start_str)
    ).fetchall()
    sleep_list = [dict(r) for r in sleep_period]

    sleep_stats = None
    if sleep_list:
        total_minutes = sum(r.get('duration_minutes', 0) or 0 for r in sleep_list)
        daily_avg_hours = round(total_minutes / days / 60, 1)
        avg_quality = None
        qualities = [r.get('sleep_quality') for r in sleep_list if r.get('sleep_quality')]
        if qualities:
            quality_map = {'优': 3, '良': 2, '中': 1, '差': 0}
            avg_score = sum(quality_map.get(q, 2) for q in qualities) / len(qualities)
            avg_quality = '优' if avg_score > 2.5 else '良' if avg_score > 1.5 else '中' if avg_score > 0.5 else '差'

        sleep_stats = {
            'total_records': len(sleep_list),
            'daily_avg_hours': daily_avg_hours,
            'avg_quality': avg_quality,
        }

    # === AI 总结 ===
    summary = _generate_report_summary(baby, age_months, growth_stats, feeding_stats, sleep_stats, period_name)

    return {
        'success': True,
        'period': period_name,
        'days': days,
        'baby': baby,
        'age_months': age_months,
        'date_range': {
            'start': start_str,
            'end': today.strftime('%Y-%m-%d'),
        },
        'growth': growth_stats,
        'feeding': feeding_stats,
        'sleep': sleep_stats,
        'summary': summary,
        'generated_at': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    }


def _generate_report_summary(baby, age_months, growth, feeding, sleep, period_name):
    """生成报告文字总结"""
    parts = []

    # 生长总结
    if growth:
        if growth.get('weight_change') is not None:
            wc = growth['weight_change']
            if wc > 0:
                parts.append(f"本{period_name}体重增长 {wc:.2f}kg，发育良好。")
            elif wc < 0:
                parts.append(f"本{period_name}体重下降 {abs(wc):.2f}kg，建议关注喂养情况。")
            else:
                parts.append(f"本{period_name}体重无变化。")
        if growth.get('height_change') is not None:
            hc = growth['height_change']
            if hc > 0:
                parts.append(f"身高增长 {hc:.1f}cm。")
    else:
        parts.append(f"本{period_name}暂无生长记录，建议定期测量。")

    # 喂养总结
    if feeding:
        parts.append(f"共喂养 {feeding['total']} 次，日均 {feeding['daily_avg']} 次，总奶量 {feeding['total_amount']:.0f}ml。")
    else:
        parts.append(f"本{period_name}暂无喂养记录。")

    # 睡眠总结
    if sleep:
        parts.append(f"日均睡眠 {sleep['daily_avg_hours']} 小时，平均质量 {sleep['avg_quality'] or '未知'}。")
    else:
        parts.append(f"本{period_name}暂无睡眠记录。")

    return ' '.join(parts) + '\n—— 以上内容仅供参考，不构成医疗建议，如有异常请咨询儿科医生。'


# ==================== 聊天历史管理 ====================

def init_chat_history_table(db):
    """初始化聊天历史表"""
    db.execute('''
        CREATE TABLE IF NOT EXISTS chat_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            baby_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (baby_id) REFERENCES babies(id)
        )
    ''')
    db.commit()


def save_chat_message(db, baby_id, role, content):
    """保存聊天消息"""
    db.execute(
        'INSERT INTO chat_history (baby_id, role, content) VALUES (?, ?, ?)',
        (baby_id, role, content)
    )
    db.commit()


def get_chat_history(db, baby_id, limit=50):
    """获取聊天历史（最近 N 条）"""
    rows = db.execute(
        """SELECT role, content, created_at FROM chat_history
           WHERE baby_id = ? ORDER BY id DESC LIMIT ?""",
        (baby_id, limit)
    ).fetchall()
    return [
        {'role': r['role'], 'content': r['content'], 'created_at': r['created_at']}
        for r in reversed(rows)
    ]


def clear_chat_history(db, baby_id):
    """清除聊天历史"""
    db.execute('DELETE FROM chat_history WHERE baby_id = ?', (baby_id,))
    db.commit()


def get_chat_context(db, baby_id, limit=10):
    """
    获取多轮对话上下文（传给 LLM 的历史消息）
    返回 OpenAI 格式的消息列表
    """
    rows = db.execute(
        """SELECT role, content FROM chat_history
           WHERE baby_id = ? AND role IN ('user','assistant')
           ORDER BY id DESC LIMIT ?""",
        (baby_id, limit)
    ).fetchall()
    # 反转顺序（从旧到新）
    return [
        {'role': r['role'], 'content': r['content']}
        for r in reversed(rows)
    ]
