# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xiaoke799
"""
小萌 AI 工具集（Function Calling / Tool Use）。

提供让 LLM 通过结构化 JSON 调用本项目数据操作能力的工具集，
涵盖：查询、创建、分析、删除四大类。所有工具均经过参数校验，
统一通过 ToolRegistry + ToolExecutor 调度执行。

每个工具定义遵循 OpenAI function calling 规范（name + description +
parameters JSON Schema），可被直接序列化后发给 LLM。

安全规则：
- 所有写操作（create / update / delete）只对当前选中宝宝 (baby_id) 生效
- 字段类型、范围、长度全部校验（validators.py）
- 所有 SQL 参数化执行（无字符串拼接）
- 删除操作前强制确认 (confirm=True)，防止误删
"""

from __future__ import annotations

import datetime
import json
import logging
import re
import sqlite3
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from utils import get_db
from validators import (
    validate_amount,
    validate_duration,
    validate_enum,
    validate_height,
    validate_head_circumference,
    validate_text_length,
    validate_weight,
    VALID_FEEDING_TYPES,
    VALID_FEEDING_SIDES,
    VALID_DIAPER_TYPES,
    DURATION_MAX,
    NOTE_MAX_LENGTH,
    NAME_MAX_LENGTH,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 工具定义（OpenAI function calling 格式）
# ---------------------------------------------------------------------------

# validators 里的类型集合是 set，直接塞进 JSON Schema 会导致 json.dumps 报
# "Object of type set is not JSON serializable"，工具定义就发不出去。
# 统一转成排序后的列表：既可序列化，顺序也稳定（提示词可复现）。
FEEDING_TYPE_LIST = sorted(VALID_FEEDING_TYPES)
DIAPER_TYPE_LIST = sorted(VALID_DIAPER_TYPES)

# 取值范围对齐前端下拉框（app.js 的 diaperColor / skin_condition / side 选项）
VALID_DIAPER_COLORS = {"black", "brown", "green", "yellow", "other"}
VALID_SKIN_CONDITIONS = {"normal", "slight_red", "rash", "severe_rash"}
# VALID_FEEDING_SIDES 来自 validators（喂奶路由与 AI 工具共用同一份枚举）
DIAPER_COLOR_LIST = sorted(VALID_DIAPER_COLORS)
SKIN_CONDITION_LIST = sorted(VALID_SKIN_CONDITIONS)
FEEDING_SIDE_LIST = sorted(VALID_FEEDING_SIDES)

# 健康记录类型 → 中文名（与 blueprints/health.py::add_health_record 保持一致）。
# fever/doctor/medication 是前端「健康记录」页在用的类型；routine/vaccine 那套是
# 「体检记录」页的。两边共用 health_records 表，所以枚举必须合起来。
HEALTH_TYPE_NAMES = {
    "fever": "发烧",
    "doctor": "就医",
    "medication": "用药",
    "routine": "常规体检",
    "vaccine": "疫苗接种",
    "dental": "口腔检查",
    "eye": "视力检查",
    "blood": "血液检查",
    "other": "其他",
}
HEALTH_TYPE_LIST = list(HEALTH_TYPE_NAMES)


def get_tool_definitions(baby_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """返回当前可用工具的 JSON Schema 列表。"""
    return [
        {
            "name": "query_baby_info",
            "description": "查询宝宝的基本信息（姓名、生日、月龄、性别）以及最近的统计摘要（总喂养次数、总睡眠时长、总如厕次数）。",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
        {
            "name": "query_records",
            "description": "按类型与时间范围查询宝宝的记录列表。支持的类型: feeding(喂养), sleep(睡眠), diaper(如厕), growth(生长), health(健康/体温)。",
            "parameters": {
                "type": "object",
                "properties": {
                    "record_type": {
                        "type": "string",
                        "enum": ["feeding", "sleep", "diaper", "growth", "health"],
                        "description": "要查询的记录类型",
                    },
                    "days": {
                        "type": "integer",
                        "description": "查询最近多少天的记录，默认 7",
                        "default": 7,
                    },
                    "limit": {
                        "type": "integer",
                        "description": "最多返回条数，默认 20",
                        "default": 20,
                    },
                },
                "required": ["record_type"],
            },
        },
        {
            "name": "create_feeding_record",
            "description": "新增一条喂养记录（母乳/配方奶/辅食/吸奶）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "feeding_type": {
                        "type": "string",
                        "enum": FEEDING_TYPE_LIST,
                        "description": f"喂养类型：{'/'.join(FEEDING_TYPE_LIST)}",
                    },
                    "amount": {
                        "type": "number",
                        "description": "奶量或辅食量（毫升或克），可为 0",
                    },
                    "start_time": {
                        "type": "string",
                        "description": "喂养开始时间，格式 YYYY-MM-DD HH:MM，例如 2026-09-04 14:30。不填默认当前时间。",
                    },
                    "end_time": {
                        "type": "string",
                        "description": "喂养结束时间，格式 YYYY-MM-DD HH:MM。可选。",
                    },
                    "left_duration": {
                        "type": "number",
                        "description": "左侧哺乳时长（单位：分钟，可带小数如 12.5），母乳喂养时可选。",
                    },
                    "right_duration": {
                        "type": "number",
                        "description": "右侧哺乳时长（单位：分钟，可带小数如 8.5），母乳喂养时可选。",
                    },
                    "side": {
                        "type": "string",
                        "enum": sorted(VALID_FEEDING_SIDES),
                        "description": "哺乳侧：left(左)/right(右)/both(双侧)，可选。",
                    },
                    "note": {
                        "type": "string",
                        "description": "备注文字，最多 200 字。",
                    },
                },
                "required": ["feeding_type"],
            },
        },
        {
            "name": "create_sleep_record",
            "description": "新增一条睡眠记录。",
            "parameters": {
                "type": "object",
                "properties": {
                    "start_time": {
                        "type": "string",
                        "description": "入睡时间，格式 YYYY-MM-DD HH:MM。不填默认当前时间。",
                    },
                    "end_time": {
                        "type": "string",
                        "description": "醒来时间，格式 YYYY-MM-DD HH:MM。可选（仍睡着可省略）。",
                    },
                    "duration_minutes": {
                        "type": "number",
                        "description": "睡眠时长（分钟）。如果不填且给了 end_time 会自动计算（跨夜也能算对）。",
                    },
                    "quality": {
                        "type": "string",
                        "description": "睡眠质量：good/normal/poor，可选。",
                    },
                    "is_nap": {
                        "type": "boolean",
                        "description": "是否白天小憩。true=白天小睡，false=夜间睡眠。可选。",
                    },
                    "note": {
                        "type": "string",
                        "description": "备注，最多 200 字。",
                    },
                },
                "required": [],
            },
        },
        {
            "name": "create_diaper_record",
            "description": "新增一条如厕记录（大小便）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "diaper_type": {
                        "type": "string",
                        "enum": DIAPER_TYPE_LIST,
                        "description": f"类型：{'/'.join(DIAPER_TYPE_LIST)}（wet=小便 dirty=大便 both=都有 dry=干爽）",
                    },
                    "time": {
                        "type": "string",
                        "description": "发生时间，格式 YYYY-MM-DD HH:MM。不填默认当前时间。",
                    },
                    "color": {
                        "type": "string",
                        "enum": DIAPER_COLOR_LIST,
                        "description": "便便颜色，大便/混合时填：black(黑，胎便)/brown(棕，正常)/green(绿)/yellow(黄)/other(其他)。",
                    },
                    "skin_condition": {
                        "type": "string",
                        "enum": SKIN_CONDITION_LIST,
                        "description": "臀部皮肤状况：normal(正常)/slight_red(轻微发红)/rash(红臀)/severe_rash(严重红臀)。",
                    },
                    "brand": {
                        "type": "string",
                        "description": "纸尿裤品牌，可选。",
                    },
                    "note": {
                        "type": "string",
                        "description": "备注（如气味、量多量少），最多 200 字。颜色和皮肤状况请填对应字段，不要写进备注。",
                    },
                },
                "required": ["diaper_type"],
            },
        },
        {
            "name": "create_growth_record",
            "description": "新增一条生长记录（体重/身高/头围）。BMI 会根据身高体重自动计算，无需传。",
            "parameters": {
                "type": "object",
                "properties": {
                    "weight_kg": {
                        "type": "number",
                        "description": "体重（公斤），精确到 0.01。",
                    },
                    "height_cm": {
                        "type": "number",
                        "description": "身高/身长（厘米），精确到 0.1。",
                    },
                    "head_cm": {
                        "type": "number",
                        "description": "头围（厘米），精确到 0.1。",
                    },
                    "date": {
                        "type": "string",
                        "description": "测量日期，格式 YYYY-MM-DD。不填默认今天。",
                    },
                    "note": {
                        "type": "string",
                        "description": "备注，最多 200 字。",
                    },
                },
                "required": [],
            },
        },
        {
            "name": "create_health_record",
            "description": (
                "新增一条健康档案记录。共用一张表的两类用途："
                "① 生病/就医/用药（record_type=fever/doctor/medication，配合 temperature/symptom/medication）；"
                "② 体检/疫苗/口腔/视力/血液（record_type=routine/vaccine/dental/eye/blood）。"
                "title 不填会按类型自动生成。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "record_date": {
                        "type": "string",
                        "description": "记录日期，格式 YYYY-MM-DD。不填默认今天。",
                    },
                    "record_type": {
                        "type": "string",
                        "enum": HEALTH_TYPE_LIST,
                        "description": (
                            "记录类型：fever(发烧)/doctor(就医)/medication(用药)/"
                            "routine(常规体检)/vaccine(疫苗)/dental(口腔)/eye(视力)/blood(血液)/other(其他)。默认 routine。"
                        ),
                    },
                    "title": {
                        "type": "string",
                        "description": "记录标题，如 6月龄体检 / 感冒发烧。不填则按类型自动生成。",
                    },
                    "temperature": {
                        "type": "number",
                        "description": "体温（摄氏度）。发烧时必填，如 38.6。取值 30-45。",
                    },
                    "symptom": {
                        "type": "string",
                        "description": "症状描述，如 半夜发热、精神尚可、咳嗽有痰。",
                    },
                    "medication": {
                        "type": "string",
                        "description": "用药记录，如 布洛芬混悬液 4ml。只记录家长告知的用药，不要自行建议用药。",
                    },
                    "hospital": {
                        "type": "string",
                        "description": "医院/机构名称。",
                    },
                    "doctor": {
                        "type": "string",
                        "description": "医生姓名。",
                    },
                    "height": {
                        "type": "number",
                        "description": "身高（cm）。",
                    },
                    "weight": {
                        "type": "number",
                        "description": "体重（kg）。",
                    },
                    "head_circumference": {
                        "type": "number",
                        "description": "头围（cm）。",
                    },
                    "diagnosis": {
                        "type": "string",
                        "description": "医生诊断结果（照录医嘱，不要自行判断）。",
                    },
                    "advice": {
                        "type": "string",
                        "description": "医嘱/建议。",
                    },
                    "note": {
                        "type": "string",
                        "description": "备注，最多 2000 字。",
                    },
                },
                "required": [],
            },
        },
        {
            "name": "update_record",
            "description": (
                "修改一条已存在的记录（改错值专用，不要删了重加）。"
                "先用 query_records 查到 record_id，再把要改的字段放进 fields；"
                "只改 fields 里出现的字段，其余保持不变。"
                "各类型可改字段见 fields 的说明，填错类型的字段会被忽略并告知。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "record_type": {
                        "type": "string",
                        "enum": ["feeding", "sleep", "diaper", "growth", "health"],
                        "description": "要修改的记录类型。",
                    },
                    "record_id": {
                        "type": "integer",
                        "description": "要修改的记录 ID（先用 query_records 查到）。",
                    },
                    "fields": {
                        "type": "object",
                        "description": (
                            "要修改的字段（只填需要改的）。按 record_type 取用："
                            "feeding: start_time/end_time/feeding_type/amount/side/left_duration/right_duration/note；"
                            "sleep: start_time/end_time/duration_minutes/quality/is_nap/note；"
                            "diaper: time/diaper_type/color/skin_condition/brand/note；"
                            "growth: date/weight_kg/height_cm/head_cm/note；"
                            "health: record_date/health_type/title/hospital/doctor/temperature/symptom/medication/diagnosis/advice/note。"
                            "哺乳时长单位是分钟；改生长记录的身高体重后 BMI 会自动重算。"
                        ),
                        "properties": {
                            "start_time": {"type": "string", "description": "开始/入睡时间 YYYY-MM-DD HH:MM（feeding/sleep）。"},
                            "end_time": {"type": "string", "description": "结束/醒来时间 YYYY-MM-DD HH:MM（feeding/sleep）。"},
                            "feeding_type": {"type": "string", "enum": FEEDING_TYPE_LIST, "description": "喂养类型（feeding）。"},
                            "amount": {"type": "number", "description": "奶量/辅食量 ml 或 g（feeding）。"},
                            "side": {"type": "string", "enum": FEEDING_SIDE_LIST, "description": "哺乳侧（feeding）。"},
                            "left_duration": {"type": "number", "description": "左侧哺乳时长，单位分钟（feeding）。"},
                            "right_duration": {"type": "number", "description": "右侧哺乳时长，单位分钟（feeding）。"},
                            "duration_minutes": {"type": "number", "description": "睡眠时长分钟（sleep）。"},
                            "quality": {"type": "string", "enum": ["good", "normal", "poor"], "description": "睡眠质量（sleep）。"},
                            "is_nap": {"type": "boolean", "description": "是否白天小憩（sleep）。"},
                            "time": {"type": "string", "description": "如厕时间 YYYY-MM-DD HH:MM（diaper）。"},
                            "diaper_type": {"type": "string", "enum": DIAPER_TYPE_LIST, "description": "如厕类型（diaper）。"},
                            "color": {"type": "string", "enum": DIAPER_COLOR_LIST, "description": "便便颜色（diaper）。"},
                            "skin_condition": {"type": "string", "enum": SKIN_CONDITION_LIST, "description": "臀部皮肤状况（diaper）。"},
                            "brand": {"type": "string", "description": "纸尿裤品牌（diaper）。"},
                            "date": {"type": "string", "description": "测量日期 YYYY-MM-DD（growth）。"},
                            "weight_kg": {"type": "number", "description": "体重 kg（growth）。"},
                            "height_cm": {"type": "number", "description": "身高 cm（growth）。"},
                            "head_cm": {"type": "number", "description": "头围 cm（growth）。"},
                            "record_date": {"type": "string", "description": "记录日期 YYYY-MM-DD（health）。"},
                            "health_type": {"type": "string", "enum": HEALTH_TYPE_LIST, "description": "健康记录类型（health）。"},
                            "title": {"type": "string", "description": "标题（health）。"},
                            "hospital": {"type": "string", "description": "医院（health）。"},
                            "doctor": {"type": "string", "description": "医生（health）。"},
                            "temperature": {"type": "number", "description": "体温 ℃（health）。"},
                            "symptom": {"type": "string", "description": "症状（health）。"},
                            "medication": {"type": "string", "description": "用药（health）。"},
                            "diagnosis": {"type": "string", "description": "诊断（health）。"},
                            "advice": {"type": "string", "description": "医嘱（health）。"},
                            "note": {"type": "string", "description": "备注（所有类型）。"},
                        },
                    },
                },
                "required": ["record_type", "record_id", "fields"],
            },
        },
        {
            "name": "delete_record",
            "description": "删除宝宝的一条指定记录。出于安全考虑，必须显式传 confirm=True 才会真正执行删除。",
            "parameters": {
                "type": "object",
                "properties": {
                    "record_type": {
                        "type": "string",
                        "enum": ["feeding", "sleep", "diaper", "growth", "health"],
                        "description": "要删除的记录类型。",
                    },
                    "record_id": {
                        "type": "integer",
                        "description": "要删除的记录 ID（从查询结果中获得）。",
                    },
                    "confirm": {
                        "type": "boolean",
                        "description": "确认删除标志。必须显式传 true 才会真正删除。",
                    },
                },
                "required": ["record_type", "record_id"],
            },
        },
        {
            "name": "analyze_baby_data",
            "description": "综合分析宝宝最近一段时间的数据，生成包含生长、喂养、睡眠的周报或月报。",
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {
                        "type": "integer",
                        "description": "分析的时间窗口（天）。默认 7（周报），可设 30（月报）。",
                        "default": 7,
                    },
                },
            },
        },
        {
            "name": "search_knowledge",
            "description": (
                "检索站内育儿知识库与健康百科（本应用自带的权威内容）。"
                "回答育儿知识、护理方法、症状处理、辅食、睡眠、发育常识这类问题时，"
                "应先调用本工具检索，并优先依据检索到的内容作答；"
                "若检索不到相关内容，再明确说明以下为通用建议。"
                "不要凭记忆编造具体条文、数字或机构名称。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {
                        "type": "string",
                        "description": "检索关键词，如「发烧」「辅食添加」「睡眠倒退」「黄疸」「湿疹」。",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "返回条数，默认 3，最多 5。",
                        "default": 3,
                    },
                },
                "required": ["keyword"],
            },
        },
        {
            "name": "assess_growth",
            "description": (
                "评估宝宝生长发育是否达标：按 WHO 生长标准计算体重/身高/头围的百分位分档与近似 z 值，"
                "并结合上一次测量给出增速评估（偏快/正常/偏慢）。"
                "用于回答「发育达标吗」「体重偏轻吗」「长得快不快」「头围正常吗」这类问题。"
                "体检记录页的百分位口径与本工具一致。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "record_date": {
                        "type": "string",
                        "description": "要评估的测量日期 YYYY-MM-DD；不填则用最近一次生长记录。",
                    },
                },
            },
        },
    ]


# ---------------------------------------------------------------------------
# 工具执行器
# ---------------------------------------------------------------------------

def _now_str() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _today_str() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d")


def _safe_limit(v: Any, default: int = 20, max_v: int = 100) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return default
    return max(1, min(n, max_v))


def _safe_days(v: Any, default: int = 7, max_v: int = 365) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return default
    return max(1, min(n, max_v))


def _get_baby(db, baby_id: int) -> Optional[Dict[str, Any]]:
    row = db.execute("SELECT * FROM babies WHERE id = ?", (baby_id,)).fetchone()
    if not row:
        return None
    return dict(row)


def _age_months(birthday_str: str) -> int:
    if not birthday_str:
        return 0
    try:
        bd = datetime.datetime.strptime(birthday_str[:10], "%Y-%m-%d")
        today = datetime.datetime.now()
        return max(0, (today - bd).days // 30)
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# 时间 / 数值归一化
#
# 这些辅助函数解决的是「LLM 自然语言 → 数据库严格格式」的落差：
# 模型经常给出 "2026/09/13 15:00"、"2026-09-13T15:00"、"今天下午三点" 这类输入，
# 而库里的时间列是纯文本，后续统计全靠 date(start_time)、start_time >= ? 这类
# 字符串比较。一旦格式不统一，统计会静默算错（不报错，只是数字不对）。
# 所以写入前必须统一成 'YYYY-MM-DD HH:MM:SS'，解析不了就报错让模型重试，
# 而不是原样入库。
# ---------------------------------------------------------------------------

# 允许的自然语言别名：模型有时直接回「今天 / 昨天」而不给完整日期
_RELATIVE_DAY_WORDS = {
    "今天": 0, "今日": 0, "today": 0,
    "昨天": -1, "昨日": -1, "yesterday": -1,
    "前天": -2, "明天": 1, "明日": 1, "tomorrow": 1,
}

# 下限：早于这个时间的记录基本是解析错误（时间戳被当成日期之类）
_MIN_SENSIBLE_DT = datetime.datetime(2000, 1, 1)

# 未来容差：允许 1 天，覆盖时区差异与零点附近的边界情况
_FUTURE_TOLERANCE = datetime.timedelta(days=1)


def _parse_date(value: Any, field_name: str) -> Tuple[bool, Optional[str], Optional[str]]:
    """归一化日期字段为 'YYYY-MM-DD'。返回 (ok, 值, 错误)。"""
    if value in (None, ""):
        return True, None, None
    raw = str(value).strip()
    text = _RELATIVE_DAY_WORDS.get(raw, raw)
    if isinstance(text, int):
        d = (datetime.date.today() + datetime.timedelta(days=text)).strftime("%Y-%m-%d")
        return True, d, None

    text = raw.replace("/", "-").replace(".", "-").replace("年", "-").replace("月", "-").replace("日", "")
    text = text.strip()[:10]
    try:
        d = datetime.datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return False, None, (
            f"{field_name} 格式无法识别：{raw!r}。请用 YYYY-MM-DD（如 2026-09-13）"
        )

    dt = datetime.datetime.combine(d, datetime.time(12, 0))
    if dt > datetime.datetime.now() + _FUTURE_TOLERANCE:
        return False, None, f"{field_name} 是未来日期（{d}），记录的是已发生的事，请确认。"
    if dt < _MIN_SENSIBLE_DT:
        return False, None, f"{field_name} 早于 2000 年（{d}），疑似解析错误，请确认。"
    return True, d.strftime("%Y-%m-%d"), None


def _parse_datetime(
    value: Any, field_name: str, default_now: bool = True,
) -> Tuple[bool, Optional[str], Optional[str]]:
    """归一化时间字段为 'YYYY-MM-DD HH:MM:SS'。返回 (ok, 值, 错误)。

    接受：'YYYY-MM-DD HH:MM[:SS]'、'YYYY-MM-DDTHH:MM[:SS]'、'YYYY-MM-DD'、
    以及「今天/昨天」这类相对日期。
    - 只给日期且是今天 → 用当前时刻（避免记成凌晨/未来）
    - 只给日期且不是今天 → 用当天 12:00（当天内，不会跨统计日）
    - default_now=True 且完全没给 → 当前时刻
    """
    if value in (None, ""):
        return (True, _now_str(), None) if default_now else (True, None, None)
    raw = str(value).strip()
    text = _RELATIVE_DAY_WORDS.get(raw, raw)

    if isinstance(text, int):
        target = datetime.date.today() + datetime.timedelta(days=text)
        if text == 0:
            return True, _now_str(), None
        return True, datetime.datetime.combine(target, datetime.time(12, 0)).strftime("%Y-%m-%d %H:%M:%S"), None

    text = raw.replace("/", "-").replace("年", "-").replace("月", "-").replace("日", "")
    text = text.replace("T", " ").strip()
    # 中文时间描述常见形式：'2026-09-13 15点30'、'2026-09-13 15时30分'
    text = text.replace("点", ":").replace("时", ":").replace("分", "")
    text = re.sub(r"\s+", " ", text)

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.datetime.strptime(text, fmt)
            break
        except ValueError:
            continue
    else:
        return False, None, (
            f"{field_name} 格式无法识别：{raw!r}。请用 YYYY-MM-DD HH:MM（如 2026-09-13 15:00）"
        )

    if fmt == "%Y-%m-%d":
        # 只给到日：今天用当前时刻，其余日期用当天 12:00
        if dt.date() == datetime.date.today():
            return True, _now_str(), None
        dt = datetime.datetime.combine(dt.date(), datetime.time(12, 0))

    if dt > datetime.datetime.now() + _FUTURE_TOLERANCE:
        return False, None, (
            f"{field_name} 是未来时间（{dt:%Y-%m-%d %H:%M}），记录的是已发生的事，请确认。"
        )
    if dt < _MIN_SENSIBLE_DT:
        return False, None, f"{field_name} 早于 2000 年（{dt:%Y-%m-%d}），疑似解析错误，请确认。"
    return True, dt.strftime("%Y-%m-%d %H:%M:%S"), None


def _minutes_to_seconds(value: Any, field_name: str) -> Tuple[bool, Optional[int], Optional[str]]:
    """LLM 按「分钟」描述时长，库里存「秒」（见 server.py 建表注释与前端计时器）。

    schema 里写什么单位，模型就按什么单位给值。这里统一在入库前换算，
    避免让模型自己做乘法（它经常算错），也避免差 60 倍的静默错误。

    注意不要走 validate_duration：那个函数会 int(float(v)) 取整，
    「喂了 12.5 分钟」会被截成 12 分钟，小数部分凭空消失。
    """
    if value in (None, ""):
        return True, None, None
    try:
        minutes = float(value)
    except (TypeError, ValueError):
        return False, None, f"{field_name}必须为数字（单位分钟）"
    if minutes < 0 or minutes > DURATION_MAX:
        return False, None, f"{field_name}需在 0-{DURATION_MAX} 分钟之间"
    return True, int(round(minutes * 60)), None


def _sleep_minutes(start_dt: str, end_dt: str) -> Optional[int]:
    """算睡眠时长（分钟），正确处理跨夜。

    跨夜睡眠（20:00 → 次日 06:00）直接相减是负数，先前被 max(0, ...) 抹成 0，
    等于夜里那觉时长全丢。这里发现 end <= start 就按跨天处理。
    """
    try:
        s = datetime.datetime.strptime(start_dt[:19], "%Y-%m-%d %H:%M:%S")
        e = datetime.datetime.strptime(end_dt[:19], "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return None
    if e == s:
        return 0
    if e < s:
        e += datetime.timedelta(days=1)
    return int((e - s).total_seconds() // 60)


def _calc_bmi(weight_kg: Any, height_cm: Any) -> Optional[float]:
    """体重(kg) / 身高(m)^2，保留 1 位小数。

    与 blueprints/growth.py 的写入逻辑保持一致——否则 AI 记的生长记录
    在 BMI 趋势图里会是一条空线。
    """
    try:
        w = float(weight_kg)
        h = float(height_cm)
    except (TypeError, ValueError):
        return None
    if w <= 0 or h <= 0:
        return None
    h_m = h / 100.0
    if h_m <= 0:
        return None
    bmi = round(w / (h_m * h_m), 1)
    return bmi if 5 <= bmi <= 50 else None


# 健康记录类型 → 中文名见文件顶部 HEALTH_TYPE_NAMES（schema 与执行器共用）


def _validate_choice(value: Any, valid: set, field_name: str) -> Tuple[bool, Optional[str], Optional[str]]:
    """校验取值必须落在给定集合内（比 validate_enum 多一个「不许空」的语义判断入口）。"""
    if value in (None, ""):
        return True, None, None
    v = str(value).strip().lower()
    if v not in valid:
        return False, None, f"{field_name}无效：{value!r}，可选 {'/'.join(sorted(valid))}"
    return True, v, None


def _run_query_baby_info(args: Dict[str, Any], db: sqlite3.Connection, baby_id: int) -> Dict[str, Any]:
    baby = _get_baby(db, baby_id)
    if not baby:
        return {"success": False, "error": f"找不到 id={baby_id} 的宝宝"}
    age_months = _age_months(baby.get("birthday", ""))
    stats = {
        "feeding_count": db.execute("SELECT COUNT(*) FROM feeding_records WHERE baby_id = ?", (baby_id,)).fetchone()[0],
        "sleep_count": db.execute("SELECT COUNT(*) FROM sleep_records WHERE baby_id = ?", (baby_id,)).fetchone()[0],
        "diaper_count": db.execute("SELECT COUNT(*) FROM diaper_records WHERE baby_id = ?", (baby_id,)).fetchone()[0],
        "growth_count": db.execute("SELECT COUNT(*) FROM growth_records WHERE baby_id = ?", (baby_id,)).fetchone()[0],
        "health_count": db.execute("SELECT COUNT(*) FROM health_records WHERE baby_id = ?", (baby_id,)).fetchone()[0],
    }
    return {
        "success": True,
        "baby": {
            "id": baby["id"],
            "name": baby.get("name", ""),
            "birthday": baby.get("birthday", ""),
            "gender": baby.get("gender", ""),
            "age_months": age_months,
        },
        "stats": stats,
    }


def _run_query_records(args: Dict[str, Any], db: sqlite3.Connection, baby_id: int) -> Dict[str, Any]:
    record_type = (args.get("record_type") or "").strip().lower()
    days = _safe_days(args.get("days"), default=7)
    limit = _safe_limit(args.get("limit"), default=20)
    since = (datetime.datetime.now() - datetime.timedelta(days=days)).strftime("%Y-%m-%d 00:00:00")

    table_map = {
        "feeding": ("feeding_records", "start_time"),
        "sleep": ("sleep_records", "start_time"),
        "diaper": ("diaper_records", "change_time"),
        "growth": ("growth_records", "record_date"),
        "health": ("health_records", "record_date"),
    }
    if record_type not in table_map:
        return {"success": False, "error": f"不支持的记录类型: {record_type}"}
    table, time_col = table_map[record_type]

    try:
        rows = db.execute(
            f"SELECT * FROM {table} WHERE baby_id = ? AND {time_col} >= ? ORDER BY {time_col} DESC LIMIT ?",
            (baby_id, since, limit),
        ).fetchall()
        return {
            "success": True,
            "record_type": record_type,
            "days": days,
            "count": len(rows),
            "records": [dict(r) for r in rows],
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def _run_create_feeding_record(args: Dict[str, Any], db: sqlite3.Connection, baby_id: int) -> Dict[str, Any]:
    ok, feeding_type, err = validate_enum(args.get("feeding_type"), VALID_FEEDING_TYPES, "喂养类型")
    if not ok:
        return {"success": False, "error": err}

    ok, amount, err = validate_amount(args.get("amount"), "奶量")
    if not ok:
        return {"success": False, "error": err}
    if amount is None:
        amount = 0

    # 模型按「分钟」给，库里存「秒」
    ok, left_sec, err = _minutes_to_seconds(args.get("left_duration"), "左侧哺乳时长")
    if not ok:
        return {"success": False, "error": err}

    ok, right_sec, err = _minutes_to_seconds(args.get("right_duration"), "右侧哺乳时长")
    if not ok:
        return {"success": False, "error": err}

    ok, side, err = _validate_choice(args.get("side"), VALID_FEEDING_SIDES, "哺乳侧")
    if not ok:
        return {"success": False, "error": err}

    ok, note, err = validate_text_length(args.get("note"), NOTE_MAX_LENGTH, "备注")
    if not ok:
        return {"success": False, "error": err}

    ok, start_time, err = _parse_datetime(args.get("start_time"), "喂养开始时间")
    if not ok:
        return {"success": False, "error": err}

    ok, end_time, err = _parse_datetime(args.get("end_time"), "喂养结束时间", default_now=False)
    if not ok:
        return {"success": False, "error": err}
    if end_time and end_time < start_time:
        return {"success": False, "error": f"喂养结束时间（{end_time}）早于开始时间（{start_time}），请确认。"}

    try:
        cur = db.execute(
            """INSERT INTO feeding_records
               (baby_id, start_time, end_time, feeding_type, amount, side, note, left_duration, right_duration)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (baby_id, start_time, end_time, feeding_type, amount, side, note, left_sec, right_sec),
        )
        db.commit()
        return {
            "success": True,
            "message": f"已添加喂养记录（ID={cur.lastrowid}）",
            "id": cur.lastrowid,
            "feeding_type": feeding_type,
            "amount": amount,
            "start_time": start_time,
        }
    except Exception as e:
        return {"success": False, "error": f"插入喂养记录失败: {e}"}


def _run_create_sleep_record(args: Dict[str, Any], db: sqlite3.Connection, baby_id: int) -> Dict[str, Any]:
    ok, start_time, err = _parse_datetime(args.get("start_time"), "入睡时间")
    if not ok:
        return {"success": False, "error": err}

    ok, end_time, err = _parse_datetime(args.get("end_time"), "醒来时间", default_now=False)
    if not ok:
        return {"success": False, "error": err}

    ok, duration, err = validate_duration(args.get("duration_minutes"), "睡眠时长")
    if not ok:
        return {"success": False, "error": err}

    # 没给时长就用起止时间算；跨夜也能算对（此前被 max(0,...) 抹成 0）。
    # 注意：这里不要因为 end_time < start_time 就报错——跨夜是正常场景，
    # _sleep_minutes 会按「次日」处理，22:00 → 06:00 算 480 分钟。
    if duration is None and end_time:
        duration = _sleep_minutes(start_time, end_time)
        if duration is None:
            # 起止时间都在却算不出来，只可能是时间格式解析失败，别静默写成 NULL
            return {"success": False,
                    "error": f"无法根据入睡/醒来时间计算时长，请检查时间格式（{start_time} → {end_time}）"}

    quality = args.get("quality")
    if quality in (None, ""):
        quality = None
    else:
        quality = str(quality).strip().lower()
        if quality not in ("good", "normal", "poor"):
            return {"success": False, "error": f"quality 无效：{quality}，应为 good/normal/poor"}

    is_nap = args.get("is_nap")
    if is_nap in (None, ""):
        is_nap = None
    else:
        is_nap = 1 if bool(is_nap) else 0

    ok, note, err = validate_text_length(args.get("note"), NOTE_MAX_LENGTH, "备注")
    if not ok:
        return {"success": False, "error": err}

    try:
        cur = db.execute(
            """INSERT INTO sleep_records
               (baby_id, start_time, end_time, duration_minutes, sleep_quality, is_nap, note)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (baby_id, start_time, end_time, duration, quality, is_nap, note),
        )
        db.commit()
        extra = f"，{duration} 分钟" if duration is not None else ""
        return {
            "success": True,
            "message": f"已添加睡眠记录（ID={cur.lastrowid}{extra}）",
            "id": cur.lastrowid,
            "duration_minutes": duration,
            "start_time": start_time,
            "end_time": end_time,
        }
    except Exception as e:
        return {"success": False, "error": f"插入睡眠记录失败: {e}"}


def _run_create_diaper_record(args: Dict[str, Any], db: sqlite3.Connection, baby_id: int) -> Dict[str, Any]:
    ok, diaper_type, err = validate_enum(args.get("diaper_type"), VALID_DIAPER_TYPES, "如厕类型")
    if not ok:
        return {"success": False, "error": err}

    # 颜色/皮肤状态是独立列，不能再塞进 note —— 塞进 note 后前端显示不出颜色标签、
    # 红臀统计也看不到。取值对齐前端下拉框。
    ok, color, err = _validate_choice(args.get("color"), VALID_DIAPER_COLORS, "便便颜色")
    if not ok:
        return {"success": False, "error": err}

    ok, skin, err = _validate_choice(args.get("skin_condition"), VALID_SKIN_CONDITIONS, "皮肤状况")
    if not ok:
        return {"success": False, "error": err}

    ok, brand, err = validate_text_length(args.get("brand"), 100, "尿布品牌")
    if not ok:
        return {"success": False, "error": err}

    ok, note, err = validate_text_length(args.get("note"), NOTE_MAX_LENGTH, "备注")
    if not ok:
        return {"success": False, "error": err}

    ok, time_str, err = _parse_datetime(args.get("time"), "如厕时间")
    if not ok:
        return {"success": False, "error": err}

    try:
        cur = db.execute(
            """INSERT INTO diaper_records
               (baby_id, change_time, diaper_type, color, skin_condition, brand, note)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (baby_id, time_str, diaper_type, color, skin, brand, note),
        )
        db.commit()
        return {
            "success": True,
            "message": f"已添加如厕记录（ID={cur.lastrowid}）",
            "id": cur.lastrowid,
            "diaper_type": diaper_type,
            "time": time_str,
        }
    except Exception as e:
        return {"success": False, "error": f"插入如厕记录失败: {e}"}


def _run_create_growth_record(args: Dict[str, Any], db: sqlite3.Connection, baby_id: int) -> Dict[str, Any]:
    weight = args.get("weight_kg")
    height = args.get("height_cm")
    head = args.get("head_cm")
    if weight is None and height is None and head is None:
        return {"success": False, "error": "weight_kg / height_cm / head_cm 至少填一个"}
    if weight is not None:
        ok, weight, err = validate_weight(weight, "体重")
        if not ok:
            return {"success": False, "error": err}
    if height is not None:
        ok, height, err = validate_height(height, "身高")
        if not ok:
            return {"success": False, "error": err}
    if head is not None:
        ok, head, err = validate_head_circumference(head, "头围")
        if not ok:
            return {"success": False, "error": err}

    ok, note, err = validate_text_length(args.get("note"), NOTE_MAX_LENGTH, "备注")
    if not ok:
        return {"success": False, "error": err}

    ok, date_str, err = _parse_date(args.get("date"), "测量日期")
    if not ok:
        return {"success": False, "error": err}
    if not date_str:
        date_str = _today_str()

    # BMI 要跟 REST 接口一样自动算，否则 AI 记的生长记录在 BMI 图表里是空的
    bmi = _calc_bmi(weight, height)

    try:
        cur = db.execute(
            """INSERT INTO growth_records (baby_id, record_date, weight, height, head_circumference, bmi, note)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (baby_id, date_str, weight, height, head, bmi, note),
        )
        db.commit()
        parts = []
        if weight is not None:
            parts.append(f"体重 {weight}kg")
        if height is not None:
            parts.append(f"身高 {height}cm")
        if head is not None:
            parts.append(f"头围 {head}cm")
        if bmi is not None:
            parts.append(f"BMI {bmi}")
        detail = f"，{'，'.join(parts)}" if parts else ""

        # 同一天重复记录很常见（误操作或一天量两次），提示一下但不禁用
        same_day = db.execute(
            "SELECT COUNT(*) FROM growth_records WHERE baby_id = ? AND date(record_date) = ?",
            (baby_id, date_str),
        ).fetchone()[0]
        tip = f"；注意 {date_str} 当天已有 {same_day} 条生长记录" if same_day > 1 else ""
        return {
            "success": True,
            "message": f"已添加生长记录（ID={cur.lastrowid}{detail}）{tip}",
            "id": cur.lastrowid,
            "date": date_str,
            "bmi": bmi,
        }
    except Exception as e:
        return {"success": False, "error": f"插入生长记录失败: {e}"}


def _run_create_health_record(args: Dict[str, Any], db: sqlite3.Connection, baby_id: int) -> Dict[str, Any]:
    ok, record_date, err = _parse_date(args.get("record_date"), "记录日期")
    if not ok:
        return {"success": False, "error": err}
    if not record_date:
        record_date = _today_str()

    # 类型必须包含 fever/doctor/medication —— 前端「健康记录」页就用这三个，
    # 只给 routine/vaccine 那套会让「宝宝发烧 38.6 度」根本记不下来。
    record_type = str(args.get("record_type") or "routine").strip().lower()
    if record_type not in HEALTH_TYPE_NAMES:
        return {"success": False, "error": f"record_type 无效: {record_type}，可选 {'/'.join(HEALTH_TYPE_LIST)}"}

    def _txt(key, limit):
        v = args.get(key)
        return str(v).strip()[:limit] if v not in (None, "") else ""

    symptom = _txt("symptom", 200)
    temperature = args.get("temperature")
    if temperature in (None, ""):
        temperature = None
    else:
        try:
            temperature = float(temperature)
        except (TypeError, ValueError):
            return {"success": False, "error": "temperature 必须为数字（摄氏度）"}
        # 与 blueprints/health.py 的校验区间一致
        if not 30 < temperature <= 45:
            return {"success": False, "error": "temperature 应在 30-45 ℃ 之间"}

    # title 是 NOT NULL。没给就按「类型中文名 + 症状」推导，与 REST 接口同口径。
    title = _txt("title", 200)
    if not title:
        title = HEALTH_TYPE_NAMES[record_type] + (f" · {symptom[:40]}" if symptom else "")
    if not title:
        return {"success": False, "error": "title 不能为空"}

    ok, note, err = validate_text_length(args.get("note"), NOTE_MAX_LENGTH, "备注")
    if not ok:
        return {"success": False, "error": err}

    ok, hospital, err = validate_text_length(args.get("hospital"), 200, "医院名")
    if not ok:
        return {"success": False, "error": err}

    ok, doctor, err = validate_text_length(args.get("doctor"), 100, "医生名")
    if not ok:
        return {"success": False, "error": err}

    ok, diagnosis, err = validate_text_length(args.get("diagnosis"), 500, "诊断")
    if not ok:
        return {"success": False, "error": err}

    ok, advice, err = validate_text_length(args.get("advice"), 500, "医嘱")
    if not ok:
        return {"success": False, "error": err}

    medication = _txt("medication", 200)

    height = args.get("height")
    if height is not None:
        ok, height, err = validate_height(height, "身高")
        if not ok:
            return {"success": False, "error": err}

    weight = args.get("weight")
    if weight is not None:
        ok, weight, err = validate_weight(weight, "体重")
        if not ok:
            return {"success": False, "error": err}

    head_circ = args.get("head_circumference")
    if head_circ is not None:
        ok, head_circ, err = validate_head_circumference(head_circ, "头围")
        if not ok:
            return {"success": False, "error": err}

    try:
        cur = db.execute(
            """INSERT INTO health_records
               (baby_id, record_date, record_type, title, hospital, doctor,
                height, weight, head_circumference, temperature, symptom, medication,
                diagnosis, advice, note)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (baby_id, record_date, record_type, title, hospital or "", doctor or "",
             height, weight, head_circ, temperature, symptom, medication,
             diagnosis or "", advice or "", note),
        )
        db.commit()
        bits = []
        if temperature is not None:
            bits.append(f"体温 {temperature}℃")
        if symptom:
            bits.append(symptom[:30])
        detail = f"，{'，'.join(bits)}" if bits else ""
        return {
            "success": True,
            "message": f"已添加健康档案（ID={cur.lastrowid}，{title}{detail}）",
            "id": cur.lastrowid,
            "title": title,
            "record_date": record_date,
            "record_type": record_type,
            "temperature": temperature,
        }
    except Exception as e:
        return {"success": False, "error": f"插入健康档案失败: {e}"}


# ---------------------------------------------------------------------------
# 修改记录：字段白名单
#
# 结构：record_type -> { LLM 传的字段名: (数据库列名, 校验/转换方式) }
# 只有表里真实存在的列才在白名单里——模型多传的字段一律忽略并回报，
# 防止它凭空造出 "milk_powder_brand" 之类不存在的列导致 SQL 报错。
# ---------------------------------------------------------------------------

_UPDATE_FIELDS: Dict[str, Dict[str, Tuple[str, str]]] = {
    "feeding": {
        "start_time": ("start_time", "dt"),
        "end_time": ("end_time", "dt_optional"),
        "feeding_type": ("feeding_type", "feeding_type"),
        "amount": ("amount", "amount"),
        "side": ("side", "side"),
        "left_duration": ("left_duration", "min_to_sec"),
        "right_duration": ("right_duration", "min_to_sec"),
        "note": ("note", "note"),
    },
    "sleep": {
        "start_time": ("start_time", "dt"),
        "end_time": ("end_time", "dt_optional"),
        "duration_minutes": ("duration_minutes", "duration"),
        "quality": ("sleep_quality", "quality"),
        "is_nap": ("is_nap", "bool_int"),
        "note": ("note", "note"),
    },
    "diaper": {
        "time": ("change_time", "dt"),
        "diaper_type": ("diaper_type", "diaper_type"),
        "color": ("color", "color"),
        "skin_condition": ("skin_condition", "skin"),
        "brand": ("brand", "text100"),
        "note": ("note", "note"),
    },
    "growth": {
        "date": ("record_date", "date"),
        "weight_kg": ("weight", "weight"),
        "height_cm": ("height", "height"),
        "head_cm": ("head_circumference", "head"),
        "note": ("note", "note"),
    },
    "health": {
        "record_date": ("record_date", "date"),
        "health_type": ("record_type", "health_type"),
        "title": ("title", "title"),
        "hospital": ("hospital", "text200"),
        "doctor": ("doctor", "text100"),
        "temperature": ("temperature", "temperature"),
        "symptom": ("symptom", "text200"),
        "medication": ("medication", "text200"),
        "diagnosis": ("diagnosis", "text500"),
        "advice": ("advice", "text500"),
        "note": ("note", "note"),
    },
}

_UNSET = object()


def _convert_update_value(kind: str, value: Any, field_name: str) -> Tuple[bool, Any, Optional[str]]:
    """把模型给的值转成可以直接绑定进 SQL 的值。返回 (ok, 值, 错误)。"""
    if kind == "dt":
        return _parse_datetime(value, field_name)
    if kind == "dt_optional":
        return _parse_datetime(value, field_name, default_now=False)
    if kind == "date":
        ok, v, err = _parse_date(value, field_name)
        if not ok:
            return False, None, err
        if v is None:
            return False, None, f"{field_name} 不能清空"
        return True, v, None
    if kind == "min_to_sec":
        return _minutes_to_seconds(value, field_name)
    if kind == "duration":
        return validate_duration(value, field_name)
    if kind == "amount":
        return validate_amount(value, field_name)
    if kind == "weight":
        return validate_weight(value, field_name)
    if kind == "height":
        return validate_height(value, field_name)
    if kind == "head":
        return validate_head_circumference(value, field_name)
    if kind == "feeding_type":
        return validate_enum(value, VALID_FEEDING_TYPES, field_name)
    if kind == "diaper_type":
        return validate_enum(value, VALID_DIAPER_TYPES, field_name)
    if kind == "side":
        return _validate_choice(value, VALID_FEEDING_SIDES, field_name)
    if kind == "color":
        return _validate_choice(value, VALID_DIAPER_COLORS, field_name)
    if kind == "skin":
        return _validate_choice(value, VALID_SKIN_CONDITIONS, field_name)
    if kind == "health_type":
        v = str(value or "").strip().lower()
        if v not in HEALTH_TYPE_NAMES:
            return False, None, f"{field_name}无效：{value!r}"
        return True, v, None
    if kind == "quality":
        v = str(value or "").strip().lower()
        if v not in ("good", "normal", "poor"):
            return False, None, f"{field_name}无效：{value!r}，应为 good/normal/poor"
        return True, v, None
    if kind == "bool_int":
        return True, (1 if bool(value) else 0), None
    if kind == "temperature":
        try:
            t = float(value)
        except (TypeError, ValueError):
            return False, None, f"{field_name}必须为数字（摄氏度）"
        if not 30 < t <= 45:
            return False, None, f"{field_name}应在 30-45 ℃ 之间"
        return True, t, None
    if kind == "title":
        s = str(value or "").strip()[:200]
        if not s:
            return False, None, f"{field_name}不能为空"
        return True, s, None
    if kind == "note":
        return validate_text_length(value, NOTE_MAX_LENGTH, field_name)
    if kind.startswith("text"):
        return validate_text_length(value, int(kind[4:]), field_name)
    return True, value, None


def _run_update_record(args: Dict[str, Any], db: sqlite3.Connection, baby_id: int) -> Dict[str, Any]:
    """按 record_id 修改一条记录，只改传入的字段。

    以前只有 create + delete，改一个数字要先删再建：会换 id、丢掉其它字段，
    还容易被误当成两次操作。这里补上"就地修改"。
    """
    record_type = args.get("record_type")
    if isinstance(record_type, dict):
        record_type = record_type.get("type")
    record_type = str(record_type or "").strip().lower()

    field_map = _UPDATE_FIELDS.get(record_type)
    if not field_map:
        return {"success": False, "error": f"不支持修改的类型 {record_type!r}，可选 {'/'.join(_UPDATE_FIELDS)}"}

    record_id = args.get("record_id")
    try:
        record_id = int(record_id)
    except (TypeError, ValueError):
        return {"success": False, "error": "record_id 必须为整数（先用 query_records 查到 ID）"}

    fields = args.get("fields")
    if not isinstance(fields, dict):
        return {"success": False, "error": "fields 必须是对象，例如 {\"amount\": 180}"}
    # 允许模型图省事把字段平铺在顶层
    if not fields:
        fields = {k: v for k, v in args.items()
                  if k in field_map and k not in ("record_type", "record_id", "fields")}
    if not fields:
        return {"success": False, "error": "没有要修改的字段（fields 为空）"}

    table = {"feeding": "feeding_records", "sleep": "sleep_records", "diaper": "diaper_records",
             "growth": "growth_records", "health": "health_records"}[record_type]

    row = db.execute(f"SELECT * FROM {table} WHERE id = ? AND baby_id = ?", (record_id, baby_id)).fetchone()
    if not row:
        return {"success": False, "error": f"找不到 id={record_id} 的 {record_type} 记录（可能不属于当前宝宝）"}
    before = dict(row)

    sets: List[str] = []
    values: List[Any] = []
    changed: Dict[str, Any] = {}
    skipped: List[str] = []

    for key, raw in fields.items():
        mapping = field_map.get(key)
        if not mapping:
            # 不认识就跳过并回报，而不是硬塞进 SQL
            skipped.append(key)
            continue
        column, kind = mapping
        ok, val, err = _convert_update_value(kind, raw, key)
        if not ok:
            return {"success": False, "error": err}
        if val is _UNSET:
            continue
        sets.append(f"{column} = ?")
        values.append(val)
        changed[column] = val

    if not sets:
        return {"success": False, "error": f"没有可修改的字段。不认识的字段：{skipped or '无'}"}

    # 生长记录：改了身高或体重后 BMI 必须跟着重算，否则趋势图会出现旧 BMI
    if record_type == "growth":
        w = changed.get("weight", before.get("weight"))
        h = changed.get("height", before.get("height"))
        sets.append("bmi = ?")
        values.append(_calc_bmi(w, h))

    values.extend([record_id, baby_id])
    try:
        db.execute(f"UPDATE {table} SET {', '.join(sets)} WHERE id = ? AND baby_id = ?", values)
        db.commit()
    except Exception as e:
        return {"success": False, "error": f"修改失败: {e}"}

    after = dict(db.execute(f"SELECT * FROM {table} WHERE id = ?", (record_id,)).fetchone())
    diff = {k: {"旧": before.get(k), "新": after.get(k)}
            for k in after if k in before and before.get(k) != after.get(k)}
    msg = f"已修改 {record_type} 记录 ID={record_id}"
    if skipped:
        msg += f"（忽略了未知字段：{'、'.join(skipped)}）"
    return {
        "success": True,
        "message": msg,
        "id": record_id,
        "record_type": record_type,
        "changed": diff,
    }


def _run_delete_record(args: Dict[str, Any], db: sqlite3.Connection, baby_id: int) -> Dict[str, Any]:
    rec_type_raw = args.get("record_type", {})
    if isinstance(rec_type_raw, dict):
        record_type = (rec_type_raw.get("type") or "").strip().lower()
    elif isinstance(rec_type_raw, str):
        record_type = rec_type_raw.strip().lower()
    else:
        return {"success": False, "error": "record_type 格式不合法"}

    record_id = args.get("record_id")
    if record_id is None:
        return {"success": False, "error": "record_id 不能为空"}
    try:
        record_id = int(record_id)
    except (TypeError, ValueError):
        return {"success": False, "error": "record_id 必须为整数"}

    confirm = bool(args.get("confirm"))
    if not confirm:
        return {"success": False, "error": "删除操作需显式传 confirm=True 确认，本次未执行。"}

    table_map = {
        "feeding": "feeding_records",
        "sleep": "sleep_records",
        "diaper": "diaper_records",
        "growth": "growth_records",
        "health": "health_records",
    }
    if record_type not in table_map:
        return {"success": False, "error": f"不支持的类型 {record_type}"}
    table = table_map[record_type]

    try:
        exist = db.execute(f"SELECT id FROM {table} WHERE id = ? AND baby_id = ?", (record_id, baby_id)).fetchone()
        if not exist:
            return {"success": False, "error": f"找不到 id={record_id} 的 {record_type} 记录（所属宝宝不匹配或记录不存在）"}
        db.execute(f"DELETE FROM {table} WHERE id = ? AND baby_id = ?", (record_id, baby_id))
        db.commit()
        return {"success": True, "message": f"已删除 {record_type} 记录 ID={record_id}", "id": record_id, "type": record_type}
    except Exception as e:
        return {"success": False, "error": f"删除失败: {e}"}


def _run_analyze_baby_data(args: Dict[str, Any], db: sqlite3.Connection, baby_id: int) -> Dict[str, Any]:
    days = _safe_days(args.get("days"), default=7)
    baby = _get_baby(db, baby_id)
    if not baby:
        return {"success": False, "error": f"找不到 id={baby_id} 的宝宝"}
    age_months = _age_months(baby.get("birthday", ""))
    since = (datetime.datetime.now() - datetime.timedelta(days=days)).strftime("%Y-%m-%d 00:00:00")

    # 喂养（注意：row_factory 为 sqlite3.Row，Row 没有 .get()，必须先转 dict）
    feeding_rows = db.execute(
        "SELECT * FROM feeding_records WHERE baby_id = ? AND start_time >= ? ORDER BY start_time DESC",
        (baby_id, since),
    ).fetchall()
    feeding_list = [dict(r) for r in feeding_rows]
    feeding_count = len(feeding_list)
    feeding_total_ml = sum((r.get("amount") or 0) for r in feeding_list if r.get("amount"))
    feeding_types: Dict[str, int] = {}
    for r in feeding_list:
        t = r.get("feeding_type") or "其他"
        feeding_types[t] = feeding_types.get(t, 0) + 1

    # 睡眠
    sleep_rows = db.execute(
        "SELECT * FROM sleep_records WHERE baby_id = ? AND start_time >= ? ORDER BY start_time DESC",
        (baby_id, since),
    ).fetchall()
    sleep_list = [dict(r) for r in sleep_rows]
    sleep_count = len(sleep_list)
    sleep_total_min = sum((r.get("duration_minutes") or 0) for r in sleep_list if r.get("duration_minutes"))
    sleep_hours = round(sleep_total_min / 60, 1)

    # 生长
    latest_growth = db.execute(
        "SELECT * FROM growth_records WHERE baby_id = ? ORDER BY record_date DESC LIMIT 1",
        (baby_id,),
    ).fetchone()
    latest_growth_dict = dict(latest_growth) if latest_growth else None

    period_name = "周报" if days <= 7 else ("月报" if days <= 30 else f"{days}天报")
    return {
        "success": True,
        "period": period_name,
        "days": days,
        "age_months": age_months,
        "feeding": {"count": feeding_count, "total_amount": feeding_total_ml, "by_type": feeding_types},
        "sleep": {"count": sleep_count, "total_hours": sleep_hours},
        "latest_growth": latest_growth_dict,
    }


def _snippet(text: Any, keyword: str, width: int = 120) -> str:
    """截取包含关键词的片段，便于 LLM 直接引用。"""
    flat = re.sub(r"\s+", " ", str(text or "")).strip()
    if not flat:
        return ""
    if not keyword:
        return flat[:width]
    idx = flat.lower().find(keyword.lower())
    if idx < 0:
        return flat[:width]
    start = max(0, idx - width // 3)
    end = min(len(flat), start + width)
    return ("…" if start > 0 else "") + flat[start:end] + ("…" if end < len(flat) else "")


def _tokenize_query(keyword: str) -> List[str]:
    """把查询拆成检索词。

    中文没有空格分词，直接整串 LIKE 经常漏召回（如查「宝宝发烧咳嗽」匹配不到任何文章）。
    这里按标点/空白切词，保留连续中文整体作为一个词，再过滤空串，得到一组检索词。
    """
    s = re.sub(
        r"[\s,，。、；;：:!！?？()（）\[\]【】/\\\"'‘’\"`~@#$%^&*+\-=<>.]+",
        " ",
        keyword,
    )
    return [p.strip() for p in s.split() if p.strip()]


def _score_text(title: str, content: str, symptoms: str, terms: List[str], keyword: str):
    """对单条文本按命中 term 数加权打分。返回 (score, matched_terms)。"""
    hay_title = title.lower()
    hay_body = (content + "\n" + symptoms).lower()
    score = 0
    matched = 0
    for t in terms:
        tl = t.lower()
        hit_title = tl in hay_title
        hit_body = tl in hay_body
        if hit_title:
            score += 3
            matched += 1
        if hit_body:
            score += 1
            matched += 1
    # 整体短语命中标题额外加权（精准命中）
    if keyword.lower() in hay_title:
        score += 5
    return score, matched


def _run_search_knowledge(args: Dict[str, Any], db: sqlite3.Connection, baby_id: int) -> Dict[str, Any]:
    """检索站内育儿知识库（knowledge_articles）+ 健康百科（baby_wiki）。

    改版：从「整串 LIKE」改为「分词 + 相关度排序」。
    - 查询词拆成多个 term，按 term 在 标题/症状/正文 中的命中加权打分；
    - 至少命中一个 term 才入选，按总分降序返回最相关的若干条（跨两个来源统一排序）；
    - 检索不到时给出库里真实存在的话题标题，引导模型/用户换词，避免凭空编造。
    """
    keyword = str(args.get("keyword") or "").strip()
    limit = _safe_limit(args.get("limit"), default=3, max_v=5)
    if not keyword:
        return {"success": False, "error": "请提供检索关键词（如「发烧」「辅食」）"}
    if len(keyword) > 40:
        keyword = keyword[:40]

    terms = _tokenize_query(keyword)
    if not terms:
        return {"success": False, "error": "请提供有效的检索关键词"}

    items: List[Dict[str, Any]] = []
    try:
        art_rows = db.execute(
            "SELECT id, category, title, content FROM knowledge_articles"
        ).fetchall()
        for r in art_rows:
            d = dict(r)
            title = d.get("title") or ""
            content = d.get("content") or ""
            score, matched = _score_text(title, content, "", terms, keyword)
            if matched == 0:
                continue
            items.append({
                "source": "育儿知识",
                "id": d.get("id"),
                "title": title,
                "category": d.get("category") or "",
                "snippet": _snippet(content, keyword),
                "_score": score,
            })

        wiki_rows = db.execute(
            "SELECT id, category, title, content, symptoms FROM baby_wiki"
        ).fetchall()
        for r in wiki_rows:
            d = dict(r)
            title = d.get("title") or ""
            content = d.get("content") or ""
            symptoms = d.get("symptoms") or ""
            score, matched = _score_text(title, content, symptoms, terms, keyword)
            if matched == 0:
                continue
            items.append({
                "source": "健康百科",
                "id": d.get("id"),
                "title": title,
                "category": d.get("category") or "",
                "snippet": _snippet(content, keyword),
                "_score": score,
            })
    except sqlite3.OperationalError as exc:
        # 表缺失（历史库）时不要让工具炸掉整轮对话
        logger.warning("知识库检索失败: %s", exc)
        return {"success": False, "error": f"知识库暂不可用: {exc}"}

    # 按相关度降序；同分按 id 升序保证稳定
    items.sort(key=lambda x: (-x["_score"], x["id"]))
    items = items[:limit]
    for it in items:
        it.pop("_score", None)

    if items:
        return {"success": True, "keyword": keyword, "count": len(items), "results": items}

    # 未命中：回传库里真实存在的话题，引导模型/用户换词
    try:
        titles = [
            dict(r).get("title") or ""
            for r in db.execute(
                "SELECT title FROM knowledge_articles ORDER BY id LIMIT 20"
            ).fetchall()
        ]
    except sqlite3.OperationalError:
        titles = []
    titles = [t for t in titles if t][:8]
    return {
        "success": True,
        "keyword": keyword,
        "count": 0,
        "results": [],
        "hint": "站内未检索到「%s」相关内容，请换关键词重试或按通用育儿常识回答并说明未引用站内资料。" % keyword,
        "available_topics": titles,
    }


def _normalize_sex(gender: Any) -> Optional[str]:
    """把各种写法的性别归一成 WHO 表用的 boy / girl；无法判定返回 None。"""
    g = str(gender or "").strip().lower()
    if g in ("girl", "female", "f", "女", "女孩"):
        return "girl"
    if g in ("boy", "male", "m", "男", "男孩"):
        return "boy"
    return None


def _run_assess_growth(args: Dict[str, Any], db: sqlite3.Connection, baby_id: int) -> Dict[str, Any]:
    """按 WHO 标准评估发育水平，并给出与上次测量相比的增速评估。

    口径与体检记录页（blueprints/analytics.py::_get_who_percentile）保持一致：
    早产儿按预产期算纠正日龄，且只在 24 月龄内纠正。
    """
    try:
        import growth_utils
    except ImportError as exc:
        return {"success": False, "error": f"缺少生长算法模块: {exc}"}

    baby = _get_baby(db, baby_id)
    if not baby:
        return {"success": False, "error": f"找不到 id={baby_id} 的宝宝"}

    birthday = (baby.get("birthday") or "")[:10]
    if not birthday:
        return {"success": False, "error": "宝宝还未设置出生日期，无法评估生长发育"}

    sex = _normalize_sex(baby.get("gender"))
    if sex is None:
        return {
            "success": False,
            "error": "宝宝性别未设置为「男」或「女」，WHO 生长标准按性别分组，无法评估",
        }

    record_date = str(args.get("record_date") or "").strip()[:10]
    if record_date:
        try:
            datetime.datetime.strptime(record_date, "%Y-%m-%d")
        except ValueError:
            return {"success": False, "error": "record_date 需为 YYYY-MM-DD 格式"}
        row = db.execute(
            "SELECT * FROM growth_records WHERE baby_id = ? AND date(record_date) = ? "
            "ORDER BY record_date DESC LIMIT 1",
            (baby_id, record_date),
        ).fetchone()
    else:
        row = db.execute(
            "SELECT * FROM growth_records WHERE baby_id = ? ORDER BY record_date DESC LIMIT 1",
            (baby_id,),
        ).fetchone()

    if not row:
        return {
            "success": False,
            "error": "还没有生长记录，无法评估（可先记录一次身高体重，或在「成长」页添加）",
        }
    latest = dict(row)
    latest_date = (latest.get("record_date") or "")[:10]

    # 评估基准日：有指定日期就用指定日期，否则用这条记录自己的日期
    ref_date = record_date or latest_date or _today_str()

    actual_days = growth_utils.calc_age_days(birthday, ref_date)
    basis_date, is_corrected = growth_utils.effective_age_basis(
        birthday, baby.get("due_date"), actual_days,
    )
    if basis_date is None:
        return {"success": False, "error": "出生日期格式无法解析，无法评估"}
    ref_d = growth_utils.parse_date(ref_date)
    age_in_days = max((ref_d - basis_date).days, 0) if ref_d else 0
    age_months, _ = growth_utils.corrected_age_months(birthday, baby.get("due_date"), ref_date)

    table_map = {
        "weight": "who_weight_percentiles",
        "height": "who_height_percentiles",
        "head_circumference": "who_head_percentiles",
    }
    percentiles: Dict[str, Any] = {}
    for metric, table in table_map.items():
        value = latest.get(metric)
        if value in (None, ""):
            continue
        try:
            value = float(value)
        except (TypeError, ValueError):
            continue
        rows = db.execute(
            f"SELECT age_in_days, p3, p15, p50, p85, p97 FROM {table} "
            f"WHERE sex = ? ORDER BY age_in_days",
            (sex,),
        ).fetchall()
        ref = growth_utils.interpolate_reference(rows, age_in_days)
        if not ref:
            continue
        percentiles[metric] = {
            "value": value,
            "classification": growth_utils.classify_percentile(value, ref),
            "z_score": growth_utils.z_score_approx(value, ref),
            "reference": ref,
        }

    if not percentiles:
        return {
            "success": False,
            "error": "这条生长记录里没有体重/身高/头围数据，或 WHO 参考表未装载，无法评估",
        }

    # 与上一次测量比较，算增速
    velocity: Dict[str, Any] = {}
    prev = db.execute(
        "SELECT * FROM growth_records WHERE baby_id = ? AND date(record_date) < ? "
        "ORDER BY record_date DESC LIMIT 1",
        (baby_id, latest_date),
    ).fetchone()
    if prev:
        prev_d = dict(prev)
        prev_date = (prev_d.get("record_date") or "")[:10]
        span = growth_utils.calc_age_days(prev_date, latest_date)
        if span and span > 0:
            velocity["interval_days"] = span
            velocity["previous_date"] = prev_date
            w_now, w_prev = latest.get("weight"), prev_d.get("weight")
            if w_now and w_prev:
                daily_g = round((float(w_now) - float(w_prev)) * 1000 / span, 1)
                velocity["weight_gain_g_per_day"] = daily_g
                velocity["weight_verdict"] = growth_utils.assess_weight_velocity(daily_g, age_months)
            h_now, h_prev = latest.get("height"), prev_d.get("height")
            if h_now and h_prev:
                daily_cm = round((float(h_now) - float(h_prev)) / span, 3)
                velocity["height_gain_cm_per_day"] = daily_cm
                velocity["height_verdict"] = growth_utils.assess_height_velocity(daily_cm, age_months)

    return {
        "success": True,
        "sex": sex,
        "age_in_days": age_in_days,
        "age_months": age_months,
        "age_corrected": is_corrected,
        "measured_on": latest_date,
        "source": "指定日期" if record_date else "最近一次记录",
        "percentiles": percentiles,
        "velocity": velocity or None,
        "note": (
            "classification 为 WHO 关键百分位分档（<P3 / P3-P15 / P15-P50 / P50-P85 / "
            "P85-P97 / >P97），z_score 为对称 SD 近似值，仅供参考，不能替代医生诊断。"
        ),
    }


# 调度表
_TOOL_RUNNERS: Dict[str, Callable] = {
    "query_baby_info": _run_query_baby_info,
    "query_records": _run_query_records,
    "create_feeding_record": _run_create_feeding_record,
    "create_sleep_record": _run_create_sleep_record,
    "create_diaper_record": _run_create_diaper_record,
    "create_growth_record": _run_create_growth_record,
    "create_health_record": _run_create_health_record,
    "delete_record": _run_delete_record,
    "update_record": _run_update_record,
    "analyze_baby_data": _run_analyze_baby_data,
    "search_knowledge": _run_search_knowledge,
    "assess_growth": _run_assess_growth,
}


# ---------------------------------------------------------------------------
# 写操作防重复
#
# call_llm_with_tools 会循环多轮，模型有时在下一轮把同一件事再说一遍
# （尤其被工具结果打断后），结果是同一条喂养/睡眠被插入两次。
# 这里对写操作做短窗口同参去重：同宝宝 + 同工具 + 完全相同参数，8 秒内
# 只真正执行一次，第二次直接复用上次结果。
# 只影响「参数一字不差」的重复，正常连记两条不同记录（时间/数量不同）不受影响。
# ---------------------------------------------------------------------------

_WRITE_TOOLS = {
    "create_feeding_record",
    "create_sleep_record",
    "create_diaper_record",
    "create_growth_record",
    "create_health_record",
    "update_record",
    "delete_record",
}

_DEDUP_WINDOW_SECONDS = 8.0
_dedup_lock = threading.Lock()
_recent_writes: Dict[str, Tuple[float, Dict[str, Any]]] = {}


def reset_write_dedup():
    """清空写操作去重窗口（测试用）。"""
    with _dedup_lock:
        _recent_writes.clear()


def _dedup_key(tool_name: str, tool_args: Dict[str, Any], baby_id: int) -> str:
    try:
        payload = json.dumps(tool_args or {}, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        payload = repr(tool_args)
    return f"{baby_id}|{tool_name}|{payload}"


def _dedup_lookup(key: str) -> Optional[Dict[str, Any]]:
    now = time.monotonic()
    with _dedup_lock:
        # 顺手清掉过期项，避免字典无限增长
        for k in [k for k, (ts, _) in _recent_writes.items() if now - ts > _DEDUP_WINDOW_SECONDS]:
            _recent_writes.pop(k, None)
        hit = _recent_writes.get(key)
        return hit[1] if hit else None


def _dedup_store(key: str, result: Dict[str, Any]):
    with _dedup_lock:
        _recent_writes[key] = (time.monotonic(), result)


def execute_tool(tool_name: str, tool_args: Dict[str, Any], db: sqlite3.Connection, baby_id: int) -> Dict[str, Any]:
    """执行一个工具调用，返回结构化结果（始终 JSON 可序列化）。

    返回字典至少包含 success: bool，失败时带 error 描述。
    """
    runner = _TOOL_RUNNERS.get(tool_name)
    if not runner:
        return {"success": False, "error": f"未知工具: {tool_name}"}

    if not baby_id:
        return {"success": False, "error": "未选择宝宝，无法调用宝宝相关工具"}

    dedup_key = _dedup_key(tool_name, tool_args, baby_id) if tool_name in _WRITE_TOOLS else None
    if dedup_key:
        cached = _dedup_lookup(dedup_key)
        if cached is not None:
            repeat = dict(cached)
            repeat["deduplicated"] = True
            base = str(cached.get("message") or "")
            repeat["message"] = (base + "（与刚才是同一条，已跳过重复写入）") if base else "已跳过重复写入"
            logger.info("写操作去重命中: %s", tool_name)
            return repeat

    try:
        result = runner(tool_args or {}, db, baby_id)
        if not isinstance(result, dict):
            result = {"success": True, "result": result}
        # 只缓存成功的写操作：失败的结果不需要去重，模型该重试
        if dedup_key and result.get("success"):
            _dedup_store(dedup_key, result)
        return result
    except Exception as exc:
        logger.exception("工具执行异常 %s: %s", tool_name, exc)
        return {"success": False, "error": f"工具执行异常: {exc}"}
