# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xiaoke799
"""
growth_utils.py — 育儿领域纯函数算法模块

设计约束：
- 不依赖 Flask / sqlite / 项目内其他模块，可在隔离环境做单元测试；
- 所有涉及医学参考的阈值均来自公开临床常用近似值，注释中注明口径；
- 本模块只负责"纯计算"，数据读取与路由由 server.py 完成。

包含能力：
1. 纠正月龄（早产儿，0-24 月龄适用）计算
2. WHO 参考值线性插值（替代"最近邻取点"）
3. 百分位分档 + 对称 SD 近似 z-score
4. 生长速度分龄评估（体重/身高）
5. Wonder Weeks 飞跃期时间表生成
"""

from __future__ import annotations

import datetime as _dt

# ==================== 日期与月龄 ====================


def parse_date(value) -> _dt.date | None:
    """解析 'YYYY-MM-DD'（容忍多余空白），失败返回 None"""
    if not value:
        return None
    try:
        return _dt.datetime.strptime(str(value).strip()[:10], '%Y-%m-%d').date()
    except (ValueError, TypeError):
        return None


def calc_age_days(birthday, ref_date=None) -> int | None:
    """实际日龄：ref_date(默认今天) - 生日"""
    b = parse_date(birthday)
    if b is None:
        return None
    r = parse_date(ref_date) or _dt.date.today()
    return max((r - b).days, 0)


def effective_age_basis(birthday, due_date, age_days: int | None = None):
    """返回计算发育月龄应使用的"基准出生日"。

    规则（临床惯例）：
    - 未填写预产期           → 用实际生日（不纠正）
    - 已填写预产期且未满 24 月龄 → 用预产期起算（纠正月龄；早产儿才真正受影响，
      足月儿两者几乎重合，统一用预产期亦正确）
    - 已满 24 月龄           → 停止纠正，回到实际生日

    返回 (basis_date, is_corrected)
    """
    b = parse_date(birthday)
    d = parse_date(due_date)
    if b is None:
        return None, False
    if d is not None and (age_days is None or age_days <= 730):
        return d, True
    return b, False


def corrected_age_months(birthday, due_date=None, ref_date=None) -> tuple[float, bool]:
    """返回 (用于展示/评估的月龄[支持小数], 是否为纠正月龄)。

    以 30.4375 天 = 1 个月的平均月长度换算（避免 //30 的累计漂移）。
    """
    b = parse_date(birthday)
    if b is None:
        return 0.0, False
    r = parse_date(ref_date) or _dt.date.today()
    age_days = max((r - b).days, 0)
    basis, corrected = effective_age_basis(birthday, due_date, age_days)
    eff_days = max((r - basis).days, 0)
    return round(eff_days / 30.4375, 1), corrected


# ==================== WHO 参考值插值 ====================


_REF_KEYS = ('p3', 'p15', 'p50', 'p85', 'p97')


def interpolate_reference(rows, age_in_days: float) -> dict | None:
    """在 WHO 关键点之间做线性插值。

    rows: 按 age_in_days 升序的序列（dict 或 sqlite3.Row），
          每行含 age_in_days 与 p3/p15/p50/p85/p97。
    行为：落在两点间 → 线性插值；早于首点/晚于末点 → 钳制到端点（外推不可靠）。
    """
    pts = []
    for r in rows:
        row = dict(r)
        a = row.get('age_in_days')
        if a is None:
            continue
        pts.append((float(a), row))
    if not pts:
        return None
    pts.sort(key=lambda t: t[0])

    x = float(age_in_days)
    if x <= pts[0][0]:
        return {k: pts[0][1].get(k) for k in _REF_KEYS}
    if x >= pts[-1][0]:
        return {k: pts[-1][1].get(k) for k in _REF_KEYS}

    for i in range(len(pts) - 1):
        x0, r0 = pts[i]
        x1, r1 = pts[i + 1]
        if x0 <= x <= x1:
            t = (x - x0) / (x1 - x0) if x1 > x0 else 0.0
            out = {}
            for k in _REF_KEYS:
                v0, v1 = r0.get(k), r1.get(k)
                if v0 is None or v1 is None:
                    out[k] = v1 if v0 is None else v0
                else:
                    out[k] = round(v0 + (v1 - v0) * t, 3)
            return out
    return None


def classify_percentile(value: float, ref: dict) -> str:
    """按 P3/P15/P50/P85/P97 六档分箱"""
    if ref is None:
        return 'unknown'
    if value <= ref.get('p3', 0):
        return '<P3'
    if value <= ref.get('p15', 0):
        return 'P3-P15'
    if value <= ref.get('p50', 0):
        return 'P15-P50'
    if value <= ref.get('p85', 0):
        return 'P50-P85'
    if value <= ref.get('p97', 0):
        return 'P85-P97'
    return '>P97'


def z_score_approx(value: float, ref: dict) -> float | None:
    """对称 SD 近似 z-score：sd ≈ (p85 - p15) / 2。

    注：WHO 官方 z-score 基于 LMS(Box-Cox)，此处为工程近似，
    在 ±2SD 内误差可接受，超出后仅供参考。
    """
    p15, p50, p85 = ref.get('p15'), ref.get('p50'), ref.get('p85')
    if None in (p15, p50, p85):
        return None
    sd = (p85 - p15) / 2
    if sd <= 0:
        return None
    return round((value - p50) / sd, 2)


# ==================== 生长速度分龄评估 ====================

# 体重速度正常带（g/day），按实际月龄分档。
# 口径：公开儿科教材常用中位区间并适度放宽为"正常带"，超出即提示偏快/偏慢。
_WEIGHT_BANDS = [
    (0, 3, 20, 35),
    (3, 6, 10, 25),
    (6, 12, 6, 18),
    (12, 24, 4, 12),
    (24, 999, 3, 9),
]

# 身高速度正常带（cm/day）：由常用 cm/月中位区间换算并放宽。
_HEIGHT_BANDS = [
    (0, 3, 0.07, 0.13),   # 约 2.1-3.9 cm/月
    (3, 6, 0.045, 0.09),  # 约 1.4-2.7 cm/月
    (6, 12, 0.030, 0.065),
    (12, 24, 0.020, 0.045),
    (24, 999, 0.014, 0.035),
]


def assess_weight_velocity(daily_g: float, age_months: float) -> str:
    """按月龄分档评估体重增速（g/day）"""
    return _velocity_verdict(daily_g, _band_for(_WEIGHT_BANDS, age_months))


def assess_height_velocity(daily_cm: float, age_months: float) -> str:
    """按月龄分档评估身高增速（cm/day）"""
    return _velocity_verdict(daily_cm, _band_for(_HEIGHT_BANDS, age_months))


def _band_for(bands, age_months: float):
    if age_months is None:
        return None
    for lo, hi, low, high in bands:
        if lo <= age_months < hi:
            return (low, high)
    return None


def _velocity_verdict(value: float, band) -> str:
    if band is None or value is None:
        return '正常'
    low, high = band
    if value < low:
        return '偏慢'
    if value > high:
        return '偏快'
    return '正常'


# ==================== Wonder Weeks 飞跃期 ====================

LEAP_WEEKS = [5, 8, 12, 17, 26, 36, 44, 53, 61, 72]

_LEAP_DESCRIPTIONS = {
    1: '感官变化 - 对光线和声音更敏感',
    2: '模式认知 - 开始识别简单模式',
    3: '平滑运动 - 动作更加流畅',
    4: '事件感知 - 理解事件顺序',
    5: '关系认知 - 理解事物间关系',
    6: '分类能力 - 开始分类和排序',
    7: '序列理解 - 理解步骤和顺序',
    8: '目标导向 - 开始有目的地行动',
    9: '声音实验 - 发出各种声音',
    10: '社交互动 - 更主动与人互动',
}


def leap_base_date(birthday, due_date=None):
    """飞跃期以预产期为基准（Wonder Weeks 理论）；未填预产期则退回生日。"""
    return parse_date(due_date) or parse_date(birthday)


def build_leap_schedule(birthday, due_date=None) -> list[dict] | None:
    """生成 10 次飞跃期的起止日期（每段约 ±1 周）。"""
    base = leap_base_date(birthday, due_date)
    if base is None:
        return None
    schedule = []
    for i, weeks in enumerate(LEAP_WEEKS, 1):
        start = base + _dt.timedelta(weeks=weeks - 1)
        end = base + _dt.timedelta(weeks=weeks + 1)
        schedule.append({
            'leap_number': i,
            'start_date': start.strftime('%Y-%m-%d'),
            'end_date': end.strftime('%Y-%m-%d'),
            'description': _LEAP_DESCRIPTIONS.get(i, '发育飞跃期'),
        })
    return schedule
