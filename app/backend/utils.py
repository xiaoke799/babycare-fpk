#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xiaoke799
"""
共享工具函数模块。
供 server.py 与 blueprints/ 共同使用，避免循环导入。
"""

import datetime
import os
import re
import sqlite3
import urllib.parse
import urllib.request
from functools import lru_cache
from flask import g, request, jsonify

from constants import DB_PATH

# ==================== LLM 出网：本机地址必须直连 ====================
# ⚠ 2026-10-05 实测（.workbuddy/tmp/px2.py 可复现）：
#   `urllib.request.urlopen()` 的默认 opener 会走 `getproxies()`，也就是
#   **HTTP_PROXY / http_proxy 环境变量**。Linux NAS（飞牛）上这道环境变量
#   很常见，而 NO_PROXY 往往是空的 —— 于是「连 127.0.0.1 都被代理拦成 502」。
#   后果直击本项目：Ollama / LM Studio / 自定义本地网关本来就只能配
#   localhost（ai_engine 里两处模型发现都硬白名单 `localhost|127.0.0.1|::1`），
#   机器一设代理就**全部调不通**，症状是「配置看着没问题，就是一直走规则引擎」。
#   实测对照：裸 urlopen 打 127.0.0.1 → 请求到本机；显式注入 ProxyHandler 后
#   → 请求被送去代理、本机收到 0 个。
#   修法规则：目标机是**回环/私有网段/NO_PROXY 声明过**的 ⇒ 直连；
#   **外部厂商照旧走系统代理**（不破坏原本靠代理才能出网的用户）。

_LOCAL_HOST_NAMES = frozenset((
    'localhost', '::1', '0:0:0:0:0:0:0:1', '0.0.0.0', '[::1]',
))

_IPV4_RE = re.compile(r'^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$')


def is_local_host(host):
    """host 是不是「不该走代理」的地址（回环 / 私有网段 / NO_PROXY 声名）。

    ⚠ 只判**地址归属**，不判 scheme、不判端口 —— 让人一眼能看出它管什么。
    """
    if not host:
        return False
    host = host.strip().strip('[]').strip('"').lower()
    if host in _LOCAL_HOST_NAMES:
        return True
    # 127.0.0.0/8 整个段都是回环，别只认一个 127.0.0.1
    if host.startswith('127.'):
        return True
    m = _IPV4_RE.match(host)
    if m:
        parts = [int(x) for x in m.groups()]
        if any(p > 255 for p in parts):
            return False          # 不是合法 IPv4，交给后面的规则判断
        a, b = parts[0], parts[1]
        if a == 10 or (a == 192 and b == 168) or (a == 172 and 16 <= b <= 31):
            return True
        return False
    # NO_PROXY 里显式声明的（可能是 .example.com 这种后缀）
    declared = os.environ.get('no_proxy') or os.environ.get('NO_PROXY') or ''
    for item in declared.split(','):
        item = item.strip().strip('.').lower()
        if not item:
            continue
        if host == item or host.endswith('.' + item) or host == item + '.':
            return True
    return False


@lru_cache(maxsize=1)
def _opener_direct():
    """空代理表 = 任何地址都直连。"""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


@lru_cache(maxsize=1)
def _opener_default():
    """默认 opener（读 getproxies），等价于原来裸 `urlopen()` 的行为。"""
    return urllib.request.build_opener()


def urlopen_bound(req, timeout=None):
    """LLM 专用出网入口：`urlopen` 但**本机地址不走代理**。

    用法和 `urllib.request.urlopen(req, timeout=...)` 一致，唯一区别是
    目标 host 落在回环/私有网段时换用空代理表的 opener。

    ⚠ 三分：① 回环/私有网段 ⇒ 直连（否则本地 provider 在设了代理的机器上废掉）；
       ② 外部厂商 ⇒ 系统默认（原来靠代理出网的用户不受影响）；
       ③ 拿不到 host（畸形 URL）⇒ 退回默认，别把请求 cutdown 掉 —— 宁可
          走代理失败报错，也别静默换路径让人查不到原因。
    """
    try:
        host = urllib.parse.urlparse(req.full_url).hostname or ''
    except Exception:
        host = ''
    if host and is_local_host(host):
        return _opener_direct().open(req, timeout=timeout)
    return _opener_default().open(req, timeout=timeout)


def get_db():
    """获取数据库连接（线程安全，存于 Flask g 对象）"""
    if 'db' not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        # busy_timeout: 遇到写锁时等待而非立即抛 "database is locked"
        # （gunicorn 多 worker 并发启动/写入场景下避免偶发 500）
        g.db.execute("PRAGMA busy_timeout=5000")
        g.db.execute("PRAGMA journal_mode=WAL")
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db


def json_body():
    """安全获取 JSON 请求体，永远返回 dict。

    直接写 `request.json` / `request.get_json()` 有三个坑：
      1) 客户端没带 Content-Type: application/json → 抛 415，打断业务逻辑；
      2) body 是字面量 null（前端 JSON.stringify(null) 就会发 "null"）→ 返回 None，
         紧接着 data.get(...) 直接 AttributeError → 接口 500；
      3) body 是数组 → 返回 list，同样没有 .get() → 500。
    统一走这里：非 dict 一律当空 dict，业务代码只需要校验必填字段。
    注意用 silent=True，让「没带 Content-Type」也退化成一个空 body 而不是 415。
    """
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def close_db():
    """关闭当前请求的数据库连接（用于备份恢复前）"""
    db = g.pop('db', None)
    if db is not None:
        db.close()


def row_to_dict(row):
    """将 sqlite3.Row 转为 dict（None 安全）"""
    if row is None:
        return None
    return dict(row)


def rows_to_list(rows):
    """将 sqlite3.Row 列表转为 dict 列表"""
    return [dict(r) for r in rows]


def get_baby_age(birthday_str):
    """计算宝宝年龄（中文描述，供展示用）"""
    try:
        birthday = datetime.datetime.strptime(birthday_str, '%Y-%m-%d').date()
        today = datetime.date.today()
        days = (today - birthday).days
        if days < 0:
            return "未出生"
        if days < 30:
            return f"{days}天"
        if days < 365:
            months = days // 30
            remaining_days = days % 30
            if remaining_days > 0:
                return f"{months}个月{remaining_days}天"
            return f"{months}个月"
        years = days // 365
        remaining_months = (days % 365) // 30
        if remaining_months > 0:
            return f"{years}岁{remaining_months}个月"
        return f"{years}岁"
    except (ValueError, TypeError):
        return "未知"


def get_baby_age_months(birthday_str, due_date_str=None):
    """计算宝宝月龄（数值型，供逻辑判断用）。

    与 growth_utils.corrected_age_months 同一口径：30.4375 天/月，
    预产期未满 24 月龄时用纠正月龄。此前这里用 days//30 且忽略预产期，
    早产儿百分位/疫苗/ASQ 会和成长模块差好几周。
    """
    try:
        from growth_utils import corrected_age_months
        months, _ = corrected_age_months(birthday_str, due_date_str)
        return int(months) if months else 0
    except Exception:
        try:
            birthday = datetime.datetime.strptime(birthday_str, '%Y-%m-%d').date()
            today = datetime.date.today()
            days = (today - birthday).days
            return max(0, int(days / 30.4375))
        except (ValueError, TypeError):
            return 0


def _get_int_arg(name, default, minimum=None, maximum=None):
    """从 query string 解析整数参数。返回 (value, None)；非法时返回 (None, 错误响应)"""
    raw = request.args.get(name)
    if raw is None or raw == '':
        return default, None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None, (jsonify({'success': False, 'message': f'参数 {name} 必须为整数'}), 400)
    if minimum is not None and value < minimum:
        return None, (jsonify({'success': False, 'message': f'参数 {name} 不能小于 {minimum}'}), 400)
    if maximum is not None and value > maximum:
        return None, (jsonify({'success': False, 'message': f'参数 {name} 不能大于 {maximum}'}), 400)
    return value, None


def _get_app_setting(key):
    """读取 app_settings 配置项"""
    try:
        db = get_db()
        row = db.execute('SELECT value FROM app_settings WHERE key = ?', (key,)).fetchone()
        return row['value'] if row else None
    except Exception:
        return None


def _set_app_setting(key, value):
    """写入 app_settings 配置项"""
    db = get_db()
    db.execute('INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)', (key, value))
    db.commit()


# 备份/导出时必须剥离的凭据形态（llm_config JSON 内 api_key、notify_* webhook/token）
_SECRET_SETTING_KEY_RE = None


def scrub_secrets_from_settings_rows(rows):
    """从 app_settings 行列表中剥离凭据，避免 API Key / webhook 进备份/导出。"""
    import json as _json
    import re as _re
    global _SECRET_SETTING_KEY_RE
    if _SECRET_SETTING_KEY_RE is None:
        _SECRET_SETTING_KEY_RE = _re.compile(
            r'(^llm_config$|^notify_|api_key|webhook|token|secret|password|pushplus)', _re.I
        )
    scrubbed = []
    for row in rows:
        item = dict(row)
        key = str(item.get('key') or '')
        if _SECRET_SETTING_KEY_RE.search(key):
            if key == 'llm_config':
                try:
                    cfg = _json.loads(item.get('value') or '{}')
                    if isinstance(cfg, dict) and cfg.get('api_key'):
                        cfg['api_key'] = ''
                    item['value'] = _json.dumps(cfg, ensure_ascii=False)
                except Exception:
                    item['value'] = ''
            else:
                item['value'] = ''
        scrubbed.append(item)
    return scrubbed


AUTHOR_MAX_LEN = 20


def normalize_author(data):
    """取「谁记的」字段：可选，空则存 None。

    称呼是用户自己填的（妈妈/爸爸/奶奶），不做枚举白名单——
    各家叫法不一样，写死反而逼用户将就。只做长度收口与空白清理。
    返回 None 表示「没写」，老数据也是 NULL，前端按空处理。
    """
    v = str(data.get('author') or '').strip()
    if not v:
        return None
    return v[:AUTHOR_MAX_LEN]


# 为了向后兼容：从 server.py 导出的函数名称相同
# blueprints 应改为 from utils import get_db, row_to_dict, ...
