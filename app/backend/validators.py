#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xiaoke799
"""
共享数据验证函数。
提供数值范围校验、枚举值校验、文本长度限制等。
设计原则：防御性校验 + 清晰错误信息 + 不破坏已有合法数据。
"""

import datetime

# ==================== 数值范围定义 ====================

# 喂奶量：0-2000ml（单次最大 2L，覆盖极端情况）
AMOUNT_MIN = 0.0
AMOUNT_MAX = 2000.0

# 身高：30-200cm（新生儿到成人）
HEIGHT_MIN = 30.0
HEIGHT_MAX = 200.0

# 体重：0.5-50kg（早产儿到幼儿）
WEIGHT_MIN = 0.5
WEIGHT_MAX = 50.0

# BMI：5-50（极端范围覆盖）
BMI_MIN = 5.0
BMI_MAX = 50.0

# 头围：20-60cm
HEAD_CIRC_MIN = 20.0
HEAD_CIRC_MAX = 60.0

# 时长：0-1440分钟（24小时）
DURATION_MIN = 0
DURATION_MAX = 1440

# ==================== 枚举值定义 ====================

VALID_FEEDING_TYPES = {"breast", "bottle", "solid", "mixed"}
VALID_DIAPER_TYPES = {"wet", "dirty", "both", "dry"}
VALID_SLEEP_QUALITY = {"good", "normal", "poor"}
VALID_BABY_GENDERS = {"boy", "girl"}
VALID_FEEDING_SIDES = {"left", "right", "both"}

# 秒级时长（左右侧哺乳计时器用）：上限 24 小时
SECONDS_MAX = 86400

# ==================== 文本长度限制 ====================

NOTE_MAX_LENGTH = 2000       # 备注字段
TITLE_MAX_LENGTH = 200      # 标题字段
CONTENT_MAX_LENGTH = 5000   # 日记/描述字段
NAME_MAX_LENGTH = 100       # 名称字段


def validate_amount(value, field_name="奶量"):
    """验证奶量/食物量"""
    if value is None:
        return True, None, None
    try:
        v = float(value)
        if v < AMOUNT_MIN or v > AMOUNT_MAX:
            return False, None, f"{field_name}需在 {AMOUNT_MIN}-{AMOUNT_MAX}ml 之间"
        return True, v, None
    except (ValueError, TypeError):
        return False, None, f"{field_name}必须为数字"


def validate_height(value, field_name="身高"):
    """验证身高"""
    if value is None:
        return True, None, None
    try:
        v = float(value)
        if v < HEIGHT_MIN or v > HEIGHT_MAX:
            return False, None, f"{field_name}需在 {HEIGHT_MIN}-{HEIGHT_MAX}cm 之间"
        return True, v, None
    except (ValueError, TypeError):
        return False, None, f"{field_name}必须为数字"


def validate_weight(value, field_name="体重"):
    """验证体重"""
    if value is None:
        return True, None, None
    try:
        v = float(value)
        if v < WEIGHT_MIN or v > WEIGHT_MAX:
            return False, None, f"{field_name}需在 {WEIGHT_MIN}-{WEIGHT_MAX}kg 之间"
        return True, v, None
    except (ValueError, TypeError):
        return False, None, f"{field_name}必须为数字"


def validate_bmi(value, field_name="BMI"):
    """验证 BMI"""
    if value is None:
        return True, None, None
    try:
        v = float(value)
        if v < BMI_MIN or v > BMI_MAX:
            return False, None, f"{field_name}需在 {BMI_MIN}-{BMI_MAX} 之间"
        return True, v, None
    except (ValueError, TypeError):
        return False, None, f"{field_name}必须为数字"


def validate_head_circumference(value, field_name="头围"):
    """验证头围"""
    if value is None:
        return True, None, None
    try:
        v = float(value)
        if v < HEAD_CIRC_MIN or v > HEAD_CIRC_MAX:
            return False, None, f"{field_name}需在 {HEAD_CIRC_MIN}-{HEAD_CIRC_MAX}cm 之间"
        return True, v, None
    except (ValueError, TypeError):
        return False, None, f"{field_name}必须为数字"


def validate_duration(value, field_name="时长"):
    """验证时长（分钟）"""
    if value is None:
        return True, None, None
    try:
        v = int(float(value))
        if v < DURATION_MIN or v > DURATION_MAX:
            return False, None, f"{field_name}需在 {DURATION_MIN}-{DURATION_MAX} 分钟之间"
        return True, v, None
    except (ValueError, TypeError):
        return False, None, f"{field_name}必须为整数"


def validate_enum(value, valid_set, field_name="类型"):
    """验证枚举值。

    空串按「没填」处理，返回 None 而不是报错：前端下拉的「请选择/未评」项 value 就是 ''，
    （比如睡眠质量的"未评"），当成非法值会让整条记录保存失败。必填字段在路由里另有
    `if not value: 400` 兜底，不会因此放过空值。
    """
    if value is None or (isinstance(value, str) and value.strip() == ""):
        return True, None, None
    if str(value).lower() not in valid_set:
        return False, None, f"{field_name}无效，有效值：{', '.join(sorted(valid_set))}"
    return True, str(value).lower(), None


def validate_text_length(value, max_length, field_name="文本"):
    """验证文本长度"""
    if value is None:
        return True, None, None
    s = str(value)
    if len(s) > max_length:
        return False, None, f"{field_name}超出最大长度 {max_length} 字符（当前 {len(s)}）"
    return True, s, None


def truncate_text(value, max_length):
    """截断文本到指定长度（静默处理，不报错）"""
    if value is None:
        return None
    s = str(value)
    if len(s) > max_length:
        return s[:max_length]
    return s


def now_local_str():
    """当前本地时间 'YYYY-MM-DD HH:MM:SS'。

    不要 new Date().toISOString().slice(0,10) 那套：那是 UTC，东八区早上 8 点前会差一天。
    """
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def validate_datetime(value, field_name="时间", default_now=False):
    """归一化时间为 'YYYY-MM-DD HH:MM:SS'。返回 (ok, 值, 错误)。

    库里时间一律存这个格式：前端 datetime-local 给的是 'YYYY-MM-DDTHH:MM'，
    快捷按钮给的是 'YYYY-MM-DD HH:MM:SS'，两种混存会让按日期范围筛选漏记录
    （字符串比较时 'T' > ' '，带 T 的下午记录会被 '当天 23:59:59' 挡在门外）。
    """
    if value in (None, ""):
        if default_now:
            return True, now_local_str(), None
        return True, None, None

    raw = str(value).strip()
    text = raw.replace("T", " ").replace("/", "-").strip()

    fmt_used = None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.datetime.strptime(text, fmt)
            fmt_used = fmt
            break
        except ValueError:
            continue
    if fmt_used is None:
        return False, None, f"{field_name}格式无法识别：{raw}，请用 YYYY-MM-DD HH:MM"

    if fmt_used == "%Y-%m-%d":
        # 只给到日期：落到当天 12:00，避免凌晨/未来时刻把统计日算错
        dt = dt.replace(hour=12, minute=0, second=0)

    return True, dt.strftime("%Y-%m-%d %H:%M:%S"), None


def parse_datetime(value):
    """宽松解析库里的时间字符串 → datetime，解析不了返回 None。

    兼容 'YYYY-MM-DD HH:MM:SS'、'... HH:MM'（无秒）以及带 T 的 datetime-local 格式。
    统计、提醒调度、摘要到处都要解析，统一在这里，别各写一份。
    """
    if not value:
        return None
    text = str(value).replace("T", " ").strip()[:19]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def validate_seconds(value, field_name="时长"):
    """验证秒级时长（左右侧哺乳计时器）。0 视为未填，返回 None。"""
    if value in (None, ""):
        return True, None, None
    try:
        v = int(round(float(value)))
    except (TypeError, ValueError):
        return False, None, f"{field_name}必须为数字（单位：秒）"
    if v < 0 or v > SECONDS_MAX:
        return False, None, f"{field_name}需在 0-{SECONDS_MAX} 秒之间"
    return True, (v if v > 0 else None), None
