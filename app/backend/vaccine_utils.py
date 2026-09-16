# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xiaoke799
"""
vaccine_utils.py — 疫苗记录的统一读取入口

为什么要有这个模块：
本项目历史上先后建过两张疫苗表 —— 早期表 `vaccines` 与现行表
`vaccine_details`（所有写入都只进 vaccine_details）。迁移脚本会把旧表数据
搬到新表，但两边仍可能存在"只有自己那边有"的记录（迁移只在 details 为空时
才搬，之后两边各自累积过数据）。而读取侧此前各写各的：

  - 看诊摘要（blueprints/health.py）自己内联合并了两张表；
  - AI 疫苗问答（ai_engine.py）只读老表 → 用户新接种的疫苗 AI 永远看不到；
  - 首页仪表盘（blueprints/analytics.py）只读老表 → 疫苗数据恒为空/过时。

统一走本模块后，三个入口看到的是同一份合并结果。

合并规则（与迁移脚本同口径）：
- 以「疫苗名 + 剂次」为去重键；
- 同名同剂次两边都有时，`vaccine_details`（字段更全）优先；
- 合并后按「实际接种日（没有则计划日）」降序排列。

本模块不依赖 Flask，只要求传入的 db 是可执行 SQL 的连接（sqlite3.Row
或 dict 行都兼容），方便离线验证。
"""

from __future__ import annotations

# 读取用 SELECT * 而不是写死列名：老备份还原出来的表可能是旧版本结构
# （例如早期 vaccines 表没有 note 列），写死列名会让整条查询失败、
# 数据静默"消失"；需要哪个列由 _cell() 逐列容错取，缺列给默认值。


def _fetch(db, sql, baby_id):
    """缺表（老库可能只有其中一张）不能让调用方炸掉——缺表时按空列表处理。"""
    try:
        return db.execute(sql, (baby_id,)).fetchall()
    except Exception:
        return []


def _cell(row, key, default=None):
    """同时兼容 sqlite3.Row 与 dict 取值；NULL 一律归一成 default。

    注意：本项目连接的 row_factory 是 sqlite3.Row，没有 .get() 方法，
    所以不能写 row.get(key)。
    """
    try:
        value = row[key]
    except (KeyError, IndexError, TypeError):
        return default
    return default if value is None else value


def merged_vaccinations(db, baby_id, limit=None):
    """返回合并去重后的疫苗记录列表（dict），按接种/计划日期降序。

    limit 在合并、排序之后才生效——否则会把"最早该种"的到期记录一起截掉
    （完整程序有四十多针，按日期倒序截断会让陈年漏种的针永远不出现）。
    """
    merged = []
    seen = set()

    # vaccine_details 优先（字段全：含不良反应）
    for row in _fetch(db, "SELECT * FROM vaccine_details WHERE baby_id = ?", baby_id):
        key = (_cell(row, "vaccine_name"), _cell(row, "dose_number"))
        if key in seen:
            continue
        seen.add(key)
        merged.append({
            "vaccine_name": _cell(row, "vaccine_name"),
            "vaccine_type": _cell(row, "vaccine_type", "free"),
            "dose_number": _cell(row, "dose_number"),
            "scheduled_date": _cell(row, "scheduled_date"),
            "actual_date": _cell(row, "actual_date"),
            "status": _cell(row, "status"),
            "has_reaction": _cell(row, "has_reaction", 0),
            "reaction_detail": _cell(row, "reaction_detail", ""),
            "note": _cell(row, "note", ""),
        })

    # 早期表里"只有这边有"的记录补进来（同名同剂次以 details 为准）
    for row in _fetch(db, "SELECT * FROM vaccines WHERE baby_id = ?", baby_id):
        key = (_cell(row, "vaccine_name"), _cell(row, "dose_number"))
        if key in seen:
            continue
        seen.add(key)
        merged.append({
            "vaccine_name": _cell(row, "vaccine_name"),
            "vaccine_type": _cell(row, "vaccine_type", "free"),
            "dose_number": _cell(row, "dose_number"),
            "scheduled_date": _cell(row, "scheduled_date"),
            "actual_date": _cell(row, "actual_date"),
            "status": _cell(row, "status"),
            "has_reaction": 0,          # 早期表没有不良反应字段
            "reaction_detail": "",
            "note": _cell(row, "note", ""),
        })

    merged.sort(
        key=lambda x: (x.get("actual_date") or x.get("scheduled_date") or ""),
        reverse=True,
    )
    if limit is not None:
        return merged[:limit]
    return merged
