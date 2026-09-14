#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xiaoke799
"""
育儿宝 AI 数据写入引擎。
提供 AI 辅助数据录入能力：日记生成、照片描述、智能记录建议等。

设计原则：
- AI 生成草稿，用户确认后保存（不自动写入）
- 生成内容经过与手动输入相同的验证
- 尊重隐私设置：本地模型优先，云端需用户同意
"""

import datetime
import json
import logging
from typing import Optional, Tuple, Dict, Any

logger = logging.getLogger(__name__)


# ==================== 系统提示词模板 ====================

DIARY_GENERATION_PROMPT = """你是一位育儿日记助手。根据用户提供的信息，生成一篇温馨、生动的育儿日记。

要求：
1. 日记风格温馨自然，符合父母的口吻
2. 包含具体细节（时间、动作、感受）
3. 适当加入育儿知识或感悟
4. 字数控制在 200-500 字
5. 不要使用 markdown 格式，使用纯文本

宝宝信息：
{baby_context}

用户提供的信息：
{user_input}

请生成日记内容："""


PHOTO_DESCRIPTION_PROMPT = """你是一位婴儿照片描述助手。根据照片内容和上下文，生成一段简洁、温馨的照片描述。

要求：
1. 描述照片中的主要场景和动作
2. 捕捉宝宝的情绪和状态
3. 适当加入环境细节
4. 字数控制在 50-150 字
5. 不要使用 markdown 格式

照片上下文：
{context}

请生成照片描述："""


FEEDING_SUMMARY_PROMPT = """你是一位婴儿喂养记录助手。根据用户描述，提取喂养记录的结构化信息。

要求：
1. 从用户自然语言描述中提取：喂养类型、奶量/食量、时长
2. 如果信息不完整，使用合理的默认值
3. 以 JSON 格式返回

用户描述：{user_input}
当前时间：{current_time}

返回格式：
```json
{{
  "feeding_type": "breast|bottle|solid",
  "amount": 数字（毫升或克，未知为 null）,
  "duration_minutes": 数字（分钟，未知为 null）,
  "note": "补充说明"
}}
```

请提取："""


SLEEP_SUMMARY_PROMPT = """你是一位婴儿睡眠记录助手。根据用户描述，提取睡眠记录的结构化信息。

要求：
1. 从用户自然语言描述中提取：入睡时间、醒来时间、睡眠质量
2. 如果信息不完整，使用合理的默认值
3. 以 JSON 格式返回

用户描述：{user_input}
当前时间：{current_time}

返回格式：
```json
{{
  "start_time": "YYYY-MM-DD HH:MM:SS",
  "end_time": "YYYY-MM-DD HH:MM:SS（未知为空）",
  "duration_minutes": 数字（分钟，未知为 null）,
  "sleep_quality": "good|normal|poor",
  "is_nap": true|false,
  "note": "补充说明"
}}
```

请提取："""


MILESTONE_SUGGESTION_PROMPT = """你是一位婴儿发育评估助手。根据宝宝的月龄和近期表现，建议可能达成的发育里程碑。

要求：
1. 参考 WHO 发育里程碑标准
2. 结合宝宝月龄推荐合适的里程碑
3. 以 JSON 格式返回建议列表

宝宝月龄：{age_months} 个月
性别：{gender}
近期表现：{recent_performance}

返回格式：
```json
{{
  "suggestions": [
    {{
      "title": "里程碑名称",
      "description": "详细描述",
      "category": "motor|language|social|cognitive",
      "typical_age": 典型月龄数字
    }}
  ]
}}
```

请建议："""


# ==================== AI 写入核心函数 ====================

def _call_llm_for_generation(prompt: str, max_tokens: int = 1000) -> Tuple[bool, Optional[str]]:
    """
    调用 LLM 生成内容。
    优先使用本地模型，回退到云端（需用户同意）。
    
    Returns:
        (success, content)
    """
    try:
        from ai_engine import call_llm, LLM
        if not LLM.get('provider'):
            return False, None
        ok, answer = call_llm(prompt)
        return ok, answer if ok else None
    except Exception as e:
        logger.error("AI 生成调用失败: %s", e)
        return False, None


def generate_diary_entry(baby_context: Dict[str, Any], user_input: str) -> Tuple[bool, Optional[str]]:
    """
    生成育儿日记内容。
    
    Args:
        baby_context: 宝宝信息字典 {name, age_months, birthday, gender}
        user_input: 用户提供的日记素材/关键词
    
    Returns:
        (success, diary_content)
    """
    # 构建宝宝上下文文本
    ctx_lines = []
    if baby_context.get('name'):
        ctx_lines.append(f"姓名：{baby_context['name']}")
    if baby_context.get('age_months') is not None:
        ctx_lines.append(f"月龄：{baby_context['age_months']} 个月")
    if baby_context.get('gender'):
        ctx_lines.append(f"性别：{'男' if baby_context['gender'] == 'boy' else '女'}")
    baby_ctx = '\n'.join(ctx_lines) if ctx_lines else "暂无宝宝信息"

    prompt = DIARY_GENERATION_PROMPT.format(
        baby_context=baby_ctx,
        user_input=user_input or "今天宝宝的日常"
    )

    ok, content = _call_llm_for_generation(prompt, max_tokens=1500)
    if ok and content:
        # 清理可能的 markdown 格式
        content = content.strip()
        # 移除可能的代码块标记
        if content.startswith('```'):
            lines = content.split('\n')
            if lines[-1].strip().startswith('```'):
                content = '\n'.join(lines[1:-1]).strip()
        return True, content
    return False, None


def generate_photo_description(photo_context: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """
    生成照片描述。
    
    Args:
        photo_context: 照片上下文 {baby_name, age_months, photo_time, location, notes}
    
    Returns:
        (success, description)
    """
    ctx_lines = []
    if photo_context.get('baby_name'):
        ctx_lines.append(f"宝宝：{photo_context['baby_name']}")
    if photo_context.get('age_months') is not None:
        ctx_lines.append(f"月龄：{photo_context['age_months']} 个月")
    if photo_context.get('photo_time'):
        ctx_lines.append(f"拍摄时间：{photo_context['photo_time']}")
    if photo_context.get('location'):
        ctx_lines.append(f"地点：{photo_context['location']}")
    if photo_context.get('notes'):
        ctx_lines.append(f"备注：{photo_context['notes']}")
    ctx = '\n'.join(ctx_lines) if ctx_lines else "宝宝照片"

    prompt = PHOTO_DESCRIPTION_PROMPT.format(context=ctx)
    ok, content = _call_llm_for_generation(prompt, max_tokens=500)
    if ok and content:
        return True, content.strip()
    return False, None


def extract_feeding_record(user_input: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """
    从自然语言描述中提取喂养记录。
    
    Args:
        user_input: 用户自然语言描述，如"宝宝喝了120ml奶粉，喝了15分钟"
    
    Returns:
        (success, record_dict)
    """
    current_time = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    prompt = FEEDING_SUMMARY_PROMPT.format(user_input=user_input, current_time=current_time)

    ok, content = _call_llm_for_generation(prompt, max_tokens=500)
    if not ok or not content:
        return False, None

    # 解析 JSON
    try:
        # 尝试提取 JSON 块
        text = content.strip()
        if '```json' in text:
            text = text.split('```json')[1].split('```')[0].strip()
        elif '```' in text:
            text = text.split('```')[1].split('```')[0].strip()
        data = json.loads(text)
        return True, {
            'feeding_type': data.get('feeding_type', 'breast'),
            'amount': data.get('amount'),
            'duration_minutes': data.get('duration_minutes'),
            'note': data.get('note', ''),
        }
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        logger.warning("解析喂养记录 JSON 失败: %s", e)
        return False, None


def extract_sleep_record(user_input: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """
    从自然语言描述中提取睡眠记录。
    
    Args:
        user_input: 用户自然语言描述，如"宝宝下午2点睡了1个半小时"
    
    Returns:
        (success, record_dict)
    """
    current_time = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    prompt = SLEEP_SUMMARY_PROMPT.format(user_input=user_input, current_time=current_time)

    ok, content = _call_llm_for_generation(prompt, max_tokens=500)
    if not ok or not content:
        return False, None

    try:
        text = content.strip()
        if '```json' in text:
            text = text.split('```json')[1].split('```')[0].strip()
        elif '```' in text:
            text = text.split('```')[1].split('```')[0].strip()
        data = json.loads(text)
        return True, {
            'start_time': data.get('start_time', ''),
            'end_time': data.get('end_time', ''),
            'duration_minutes': data.get('duration_minutes'),
            'sleep_quality': data.get('sleep_quality', 'normal'),
            'is_nap': data.get('is_nap'),
            'note': data.get('note', ''),
        }
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        logger.warning("解析睡眠记录 JSON 失败: %s", e)
        return False, None


def suggest_milestones(baby_context: Dict[str, Any], recent_performance: str = "") -> Tuple[bool, Optional[list]]:
    """
    建议发育里程碑。
    
    Args:
        baby_context: 宝宝信息 {age_months, gender}
        recent_performance: 近期表现描述
    
    Returns:
        (success, suggestions_list)
    """
    prompt = MILESTONE_SUGGESTION_PROMPT.format(
        age_months=baby_context.get('age_months', 0),
        gender='男' if baby_context.get('gender') == 'boy' else '女',
        recent_performance=recent_performance or "暂无特殊表现"
    )

    ok, content = _call_llm_for_generation(prompt, max_tokens=800)
    if not ok or not content:
        return False, None

    try:
        text = content.strip()
        if '```json' in text:
            text = text.split('```json')[1].split('```')[0].strip()
        elif '```' in text:
            text = text.split('```')[1].split('```')[0].strip()
        data = json.loads(text)
        suggestions = data.get('suggestions', [])
        # 验证每个建议的必填字段
        validated = []
        for s in suggestions:
            if s.get('title') and s.get('category'):
                validated.append({
                    'title': str(s['title'])[:200],
                    'description': str(s.get('description', ''))[:500],
                    'category': str(s['category'])[:50],
                    'typical_age': int(s.get('typical_age', 0)),
                })
        return True, validated
    except (json.JSONDecodeError, KeyError, IndexError, ValueError) as e:
        logger.warning("解析里程碑建议 JSON 失败: %s", e)
        return False, None


def generate_weekly_summary(baby_context: Dict[str, Any], weekly_data: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """
    生成周报文字总结。
    
    Args:
        baby_context: 宝宝信息
        weekly_data: 本周统计数据
    
    Returns:
        (success, summary_text)
    """
    prompt = f"""你是一位育儿周报助手。根据本周数据，生成一段简洁的周报总结。

宝宝信息：
姓名：{baby_context.get('name', '宝宝')}
月龄：{baby_context.get('age_months', '?')} 个月

本周数据：
- 喂奶次数：{weekly_data.get('feeding_count', 0)} 次
- 平均奶量：{weekly_data.get('avg_amount', 0)} ml
- 总睡眠时长：{weekly_data.get('total_sleep_hours', 0)} 小时
- 平均夜间睡眠：{weekly_data.get('avg_night_sleep', 0)} 小时
- 换尿布次数：{weekly_data.get('diaper_count', 0)} 次
- 体重变化：{weekly_data.get('weight_change', '无数据')}
- 身高变化：{weekly_data.get('height_change', '无数据')}

要求：
1. 总结本周宝宝的整体情况
2. 指出值得关注的趋势
3. 给出下周的育儿建议
4. 字数 300-600 字
5. 温馨鼓励的语气

请生成周报总结："""

    ok, content = _call_llm_for_generation(prompt, max_tokens=1200)
    if ok and content:
        return True, content.strip()
    return False, None


# ==================== 更多记录类型的 AI 提取 ====================

DIAPER_EXTRACTION_PROMPT = """你是一位婴儿护理记录助手。根据用户描述，提取换尿布记录的结构化信息。

要求：
1. 从用户自然语言描述中提取：时间、尿布类型、颜色
2. 如果信息不完整，使用合理的默认值
3. 以 JSON 格式返回

用户描述：{user_input}
当前时间：{current_time}

返回格式：
```json
{{
  "change_time": "YYYY-MM-DD HH:MM:SS",
  "diaper_type": "wet|dirty|both|dry",
  "color": "颜色描述（未知为空）",
  "note": "补充说明"
}}
```

请提取："""


GROWTH_EXTRACTION_PROMPT = """你是一位婴儿成长记录助手。根据用户描述，提取成长记录的结构化信息。

要求：
1. 从用户自然语言描述中提取：记录日期、身高、体重、头围
2. 身高单位：cm，体重单位：kg，头围单位：cm
3. 如果信息不完整，缺失字段设为 null
4. 以 JSON 格式返回

用户描述：{user_input}
当前时间：{current_time}

返回格式：
```json
{{
  "record_date": "YYYY-MM-DD",
  "height": 数字（cm，未知为 null）,
  "weight": 数字（kg，未知为 null）,
  "head_circumference": 数字（cm，未知为 null）,
  "note": "补充说明"
}}
```

请提取："""


TEMPERATURE_EXTRACTION_PROMPT = """你是一位婴儿健康记录助手。根据用户描述，提取体温记录的结构化信息。

要求：
1. 从用户自然语言描述中提取：测量时间、体温值、测量部位
2. 体温单位：摄氏度（°C）
3. 正常体温范围：35.5-37.5°C
4. 以 JSON 格式返回

用户描述：{user_input}
当前时间：{current_time}

返回格式：
```json
{{
  "measure_time": "YYYY-MM-DD HH:MM:SS",
  "temperature": 数字（摄氏度）,
  "measure_location": "ear|armpit|oral|rectal|forehead",
  "note": "补充说明"
}}
```

请提取："""


MEDICATION_EXTRACTION_PROMPT = """你是一位婴儿用药记录助手。根据用户描述，提取用药记录的结构化信息。

要求：
1. 从用户自然语言描述中提取：药品名称、剂量、用药时间、用药原因
2. 以 JSON 格式返回

用户描述：{user_input}
当前时间：{current_time}

返回格式：
```json
{{
  "medication_name": "药品名称",
  "dose": "剂量（如 5ml, 1片）,
  "administered_time": "YYYY-MM-DD HH:MM:SS",
  "reason": "用药原因/症状",
  "note": "补充说明"
}}
```

请提取："""


VACCINE_EXTRACTION_PROMPT = """你是一位婴儿疫苗记录助手。根据用户描述，提取疫苗接种记录的结构化信息。

要求：
1. 从用户自然语言描述中提取：疫苗名称、剂次、接种日期、接种部位、批号
2. 以 JSON 格式返回

用户描述：{user_input}
当前时间：{current_time}

返回格式：
```json
{{
  "vaccine_name": "疫苗名称",
  "dose_number": 剂次数字,
  "vaccination_date": "YYYY-MM-DD",
  "vaccination_site": "接种部位（如 左臂、右腿）",
  "batch_number": "批号（未知为空）",
  "note": "补充说明"
}}
```

请提取："""


PUMPING_EXTRACTION_PROMPT = """你是一位婴儿喂养记录助手。根据用户描述，提取吸奶记录的结构化信息。

要求：
1. 从用户自然语言描述中提取：吸奶时间、左侧奶量、右侧奶量、总奶量、时长
2. 奶量单位：毫升（ml）
3. 如果总奶量未提及但左右侧有值，自动计算总和
4. 以 JSON 格式返回

用户描述：{user_input}
当前时间：{current_time}

返回格式：
```json
{{
  "pump_time": "YYYY-MM-DD HH:MM:SS",
  "duration_minutes": 数字（分钟，未知为 null）,
  "left_amount": 数字（ml，未知为 null）,
  "right_amount": 数字（ml，未知为 null）,
  "total_amount": 数字（ml，未知为 null）,
  "note": "补充说明"
}}
```

请提取："""


MILESTONE_EXTRACTION_PROMPT = """你是一位婴儿发育记录助手。根据用户描述，提取发育里程碑记录的结构化信息。

要求：
1. 从用户自然语言描述中提取：里程碑标题、描述、达成日期、类别
2. 类别可选：motor（大运动）、language（语言）、social（社交）、cognitive（认知）
3. 以 JSON 格式返回

用户描述：{user_input}
当前时间：{current_time}

返回格式：
```json
{{
  "title": "里程碑名称",
  "description": "详细描述",
  "achieved_date": "YYYY-MM-DD",
  "category": "motor|language|social|cognitive",
  "is_first": true|false
}}
```

请提取："""


TEETHING_EXTRACTION_PROMPT = """你是一位婴儿出牙记录助手。根据用户描述，提取出牙记录的结构化信息。

要求：
1. 从用户自然语言描述中提取：出牙日期、牙齿位置、牙齿名称
2. 位置可选：upper_front（上排前牙）、lower_front（下排前牙）、upper_molar（上排磨牙）、lower_molar（下排磨牙）
3. 以 JSON 格式返回

用户描述：{user_input}
当前时间：{current_time}

返回格式：
```json
{{
  "eruption_date": "YYYY-MM-DD",
  "tooth_position": "upper_front|lower_front|upper_molar|lower_molar|upper_canine|lower_canine",
  "tooth_name": "牙齿名称（如 中切牙、侧切牙）",
  "note": "补充说明"
}}
```

请提取："""


ALLERGY_TEST_EXTRACTION_PROMPT = """你是一位婴儿过敏测试记录助手。根据用户描述，提取过敏测试记录的结构化信息。

要求：
1. 从用户自然语言描述中提取：测试日期、过敏原、测试结果、测试方法
2. 测试结果可选：positive（阳性）、negative（negative）、uncertain（不确定）
3. 以 JSON 格式返回

用户描述：{user_input}
当前时间：{current_time}

返回格式：
```json
{{
  "test_date": "YYYY-MM-DD",
  "allergen": "过敏原名称",
  "test_result": "positive|negative|uncertain",
  "test_method": "测试方法（如 皮肤点刺、血液检测）",
  "note": "补充说明"
}}
```

请提取："""


DIARY_FULL_GENERATION_PROMPT = """你是一位育儿日记助手。根据用户提供的素材，生成一篇完整的育儿日记。

要求：
1. 日记风格温馨自然，符合父母的口吻
2. 包含具体细节（时间、动作、感受、环境）
3. 适当加入育儿知识或感悟
4. 字数控制在 300-800 字
5. 不要使用 markdown 格式，使用纯文本
6. 如果用户提供了日期，使用该日期作为日记日期

宝宝信息：
{baby_context}

用户提供的素材：
{user_input}

请生成完整日记（包含标题和正文，用空行分隔）："""


# ==================== 通用 JSON 解析辅助函数 ====================

def _parse_json_response(content: str) -> Optional[Dict[str, Any]]:
    """从 LLM 响应中解析 JSON"""
    if not content:
        return None
    try:
        text = content.strip()
        if '```json' in text:
            text = text.split('```json')[1].split('```')[0].strip()
        elif '```' in text:
            text = text.split('```')[1].split('```')[0].strip()
        return json.loads(text)
    except (json.JSONDecodeError, IndexError) as e:
        logger.warning("解析 JSON 失败: %s", e)
        return None


# ==================== 各记录类型提取函数 ====================

def extract_diaper_record(user_input: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """从自然语言提取换尿布记录"""
    current_time = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    prompt = DIAPER_EXTRACTION_PROMPT.format(user_input=user_input, current_time=current_time)
    ok, content = _call_llm_for_generation(prompt, max_tokens=500)
    if not ok or not content:
        return False, None
    data = _parse_json_response(content)
    if not data:
        return False, None
    return True, {
        'change_time': data.get('change_time', ''),
        'diaper_type': data.get('diaper_type', 'wet'),
        'color': data.get('color', ''),
        'note': data.get('note', ''),
    }


def extract_growth_record(user_input: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """从自然语言提取成长记录"""
    current_time = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    prompt = GROWTH_EXTRACTION_PROMPT.format(user_input=user_input, current_time=current_time)
    ok, content = _call_llm_for_generation(prompt, max_tokens=500)
    if not ok or not content:
        return False, None
    data = _parse_json_response(content)
    if not data:
        return False, None
    return True, {
        'record_date': data.get('record_date', ''),
        'height': data.get('height'),
        'weight': data.get('weight'),
        'head_circumference': data.get('head_circumference'),
        'note': data.get('note', ''),
    }


def extract_temperature_record(user_input: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """从自然语言提取体温记录"""
    current_time = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    prompt = TEMPERATURE_EXTRACTION_PROMPT.format(user_input=user_input, current_time=current_time)
    ok, content = _call_llm_for_generation(prompt, max_tokens=500)
    if not ok or not content:
        return False, None
    data = _parse_json_response(content)
    if not data:
        return False, None
    return True, {
        'measure_time': data.get('measure_time', ''),
        'temperature': data.get('temperature'),
        'measure_location': data.get('measure_location', 'armpit'),
        'note': data.get('note', ''),
    }


def extract_medication_record(user_input: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """从自然语言提取用药记录"""
    current_time = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    prompt = MEDICATION_EXTRACTION_PROMPT.format(user_input=user_input, current_time=current_time)
    ok, content = _call_llm_for_generation(prompt, max_tokens=500)
    if not ok or not content:
        return False, None
    data = _parse_json_response(content)
    if not data:
        return False, None
    return True, {
        'medication_name': data.get('medication_name', ''),
        'dose': data.get('dose', ''),
        'administered_time': data.get('administered_time', ''),
        'reason': data.get('reason', ''),
        'note': data.get('note', ''),
    }


def extract_vaccine_record(user_input: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """从自然语言提取疫苗记录"""
    current_time = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    prompt = VACCINE_EXTRACTION_PROMPT.format(user_input=user_input, current_time=current_time)
    ok, content = _call_llm_for_generation(prompt, max_tokens=500)
    if not ok or not content:
        return False, None
    data = _parse_json_response(content)
    if not data:
        return False, None
    return True, {
        'vaccine_name': data.get('vaccine_name', ''),
        'dose_number': data.get('dose_number', 1),
        'vaccination_date': data.get('vaccination_date', ''),
        'vaccination_site': data.get('vaccination_site', ''),
        'batch_number': data.get('batch_number', ''),
        'note': data.get('note', ''),
    }


def extract_pumping_record(user_input: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """从自然语言提取吸奶记录"""
    current_time = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    prompt = PUMPING_EXTRACTION_PROMPT.format(user_input=user_input, current_time=current_time)
    ok, content = _call_llm_for_generation(prompt, max_tokens=500)
    if not ok or not content:
        return False, None
    data = _parse_json_response(content)
    if not data:
        return False, None
    # 自动计算总奶量
    total = data.get('total_amount')
    left = data.get('left_amount')
    right = data.get('right_amount')
    if total is None and (left is not None or right is not None):
        total = (left or 0) + (right or 0)
    return True, {
        'pump_time': data.get('pump_time', ''),
        'duration_minutes': data.get('duration_minutes'),
        'left_amount': left,
        'right_amount': right,
        'total_amount': total,
        'note': data.get('note', ''),
    }


def extract_milestone_record(user_input: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """从自然语言提取里程碑记录"""
    current_time = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    prompt = MILESTONE_EXTRACTION_PROMPT.format(user_input=user_input, current_time=current_time)
    ok, content = _call_llm_for_generation(prompt, max_tokens=500)
    if not ok or not content:
        return False, None
    data = _parse_json_response(content)
    if not data:
        return False, None
    return True, {
        'title': data.get('title', ''),
        'description': data.get('description', ''),
        'achieved_date': data.get('achieved_date', ''),
        'category': data.get('category', 'other'),
        'is_first': data.get('is_first', True),
    }


def extract_teething_record(user_input: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """从自然语言提取出牙记录"""
    current_time = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    prompt = TEETHING_EXTRACTION_PROMPT.format(user_input=user_input, current_time=current_time)
    ok, content = _call_llm_for_generation(prompt, max_tokens=500)
    if not ok or not content:
        return False, None
    data = _parse_json_response(content)
    if not data:
        return False, None
    return True, {
        'eruption_date': data.get('eruption_date', ''),
        'tooth_position': data.get('tooth_position', ''),
        'tooth_name': data.get('tooth_name', ''),
        'note': data.get('note', ''),
    }


def extract_allergy_test_record(user_input: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """从自然语言提取过敏测试记录"""
    current_time = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    prompt = ALLERGY_TEST_EXTRACTION_PROMPT.format(user_input=user_input, current_time=current_time)
    ok, content = _call_llm_for_generation(prompt, max_tokens=500)
    if not ok or not content:
        return False, None
    data = _parse_json_response(content)
    if not data:
        return False, None
    return True, {
        'test_date': data.get('test_date', ''),
        'allergen': data.get('allergen', ''),
        'test_result': data.get('test_result', 'uncertain'),
        'test_method': data.get('test_method', ''),
        'note': data.get('note', ''),
    }


def generate_full_diary(baby_context: Dict[str, Any], user_input: str) -> Tuple[bool, Optional[Dict[str, str]]]:
    """生成完整日记（含标题和正文）"""
    ctx_lines = []
    if baby_context.get('name'):
        ctx_lines.append(f"姓名：{baby_context['name']}")
    if baby_context.get('age_months') is not None:
        ctx_lines.append(f"月龄：{baby_context['age_months']} 个月")
    if baby_context.get('gender'):
        ctx_lines.append(f"性别：{'男' if baby_context['gender'] == 'boy' else '女'}")
    baby_ctx = '\n'.join(ctx_lines) if ctx_lines else "暂无宝宝信息"

    prompt = DIARY_FULL_GENERATION_PROMPT.format(
        baby_context=baby_ctx,
        user_input=user_input or "今天宝宝的日常"
    )

    ok, content = _call_llm_for_generation(prompt, max_tokens=2000)
    if ok and content:
        content = content.strip()
        # 移除可能的 markdown 格式
        if content.startswith('```'):
            lines = content.split('\n')
            if lines[-1].strip().startswith('```'):
                content = '\n'.join(lines[1:-1]).strip()
        # 尝试分离标题和正文
        parts = content.split('\n\n', 1)
        if len(parts) == 2:
            title = parts[0].strip().lstrip('#').strip()
            body = parts[1].strip()
        else:
            title = f"{baby_context.get('name', '宝宝')}的日记"
            body = content
        return True, {'title': title[:200], 'content': body[:5000]}
    return False, None
