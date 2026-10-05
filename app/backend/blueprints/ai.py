#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xiaoke799
"""
AI 相关路由 Blueprint。
提供 AI 分析、聊天、LLM 配置、周报/月报、聊天历史等功能。
"""

import datetime
import json
from flask import Blueprint, request, jsonify

from utils import get_db, row_to_dict, _get_app_setting, _set_app_setting
from logger import get_logger
from user_context import require_admin

bp = Blueprint("ai", __name__)
logger = get_logger("ai")


# ==================== LLM 配置持久化（多 worker 一致性） ====================

def _load_llm_config():
    """从 app_settings 读取持久化的 LLM 配置"""
    try:
        raw = _get_app_setting('llm_config')
        return json.loads(raw) if raw else None
    except Exception:
        return None


def _save_llm_config(cfg):
    """持久化 LLM 配置到 app_settings（gunicorn 多进程下内存配置互不可见）"""
    try:
        _set_app_setting('llm_config', json.dumps(cfg, ensure_ascii=False))
    except Exception:
        pass


def _ensure_llm_applied():
    """确保当前 worker 进程的内存 LLM 配置与持久化配置一致（每次 AI 请求前调用）

    ⚠ 两个坑（2026-10-05 实测复现；用户症状是「配置好了、测试连接也成功，
    但小萌一直用规则引擎回答」）：

      1) 磁盘上存的 api_key 是**密文**，而 configure_llm 会对 api_key 再加密一次。
         把磁盘值直接喂回去 = 「密文当明文又加密一遍」⇒ decrypt 回来得到的是
         **上一次的密文本身** ⇒ 真实对话带着坏 key 打厂商接口必然 401
         （连续 5 次后还会熔断，整段时间全走规则引擎）。
         而设置页「测试连接」用的是前端刚输入的**明文**，所以照样显示"连接成功" ——
         这正是症状的来源：测试过、但对话永远不生效。
      2) custom_models 在磁盘上是 {provider: [models]} 的 dict，而 configure_llm 的
         自定义模型分支只认 list ⇒ 重启后自定义模型静默消失（下拉框里没了）。

    修法：api_key 一律**还原成明文**再比较、再下发；custom_models 单独补回。
    """
    try:
        from ai_engine import LLM, configure_llm, decrypt_api_key
        cfg = _load_llm_config()
        if not cfg:
            return

        disk_key = cfg.get('api_key') or ''
        disk_plain = decrypt_api_key(disk_key) if disk_key else ''
        mem_key = LLM.get('api_key') or ''
        mem_plain = decrypt_api_key(mem_key) if mem_key else ''
        disk_custom = cfg.get('custom_models')
        if not isinstance(disk_custom, dict):
            disk_custom = {}

        def _apply():
            new_cfg = dict(cfg)
            new_cfg['api_key'] = disk_plain          # 明文进去，由 configure_llm 负责加密
            configure_llm(new_cfg)
            # configure_llm 认不了 dict 形态的自定义模型，这里单独补
            if disk_custom and LLM.get('custom_models') != disk_custom:
                LLM['custom_models'] = dict(disk_custom)
            try:
                from ai_engine import llm_trace, _new_trace_id
                llm_trace(_new_trace_id(), '①',
                          '配置已同步到本 worker：provider=%s model=%s key明文=%d字符'
                          % (LLM.get('provider'), LLM.get('model'),
                             len(decrypt_api_key(LLM.get('api_key') or ''))))
            except Exception:
                pass

        # api_key 按明文比对（比密文没意义：每次加密的 IV 都不同，永远"不相等"）
        if mem_plain != disk_plain:
            _apply()
            return
        if disk_custom and LLM.get('custom_models') != disk_custom:
            _apply()
            return
        # 轻量指纹比较，不一致才重新应用
        for key in ('provider', 'model', 'api_url', 'consent_cloud', 'temperature',
                    'max_tokens', 'timeout', 'system_prompt', 'vision_enabled', 'vision_model'):
            if LLM.get(key) != cfg.get(key):
                _apply()
                return
    except Exception as e:
        logger.warning("_ensure_llm_applied 失败: %s", e)
        # 以前这里只写日志、调用方毫无察觉：配置同步失败会直接导致
        # "明明配好了却一直走规则引擎"，且失败原因藏在日志深处。
        try:
            from ai_engine import _set_last_llm_error
            _set_last_llm_error('配置同步失败: %s: %s' % (type(e).__name__, e))
        except Exception:
            pass


# ==================== AI 分析 API ====================

@bp.route('/api/ai/analyze/<int:baby_id>', methods=['GET'])
def ai_analyze(baby_id):
    """AI 分析宝宝数据（结果保存到聊天历史）"""
    try:
        db = get_db()
        from ai_engine import generate_full_report
        result = generate_full_report(db, baby_id)

        # 保存分析结果到聊天历史（便于回顾）
        if result.get('success'):
            try:
                from ai_engine import save_chat_message
                insights = result.get('insights', [])
                if insights:
                    # 构建分析摘要文本
                    summary_lines = [f"📊 AI分析报告（{result.get('age_months', '?')}月龄）"]
                    for insight in insights:
                        icon = '[OK]' if insight.get('type') == 'success' else '[!]' if insight.get('type') == 'warning' else '[i]'
                        summary_lines.append(f"{icon} {insight.get('title', '')}: {insight.get('content', '')}")
                    analysis_text = '\n'.join(summary_lines)
                    save_chat_message(db, baby_id, 'analysis', analysis_text)
            except Exception as e:
                # 保存失败不影响主功能，但需记录
                logger.warning("保存分析历史失败: %s", e)

        return jsonify(result)
    except Exception as e:
        logger.error("ai_analyze error: %s", e, exc_info=True)
        return jsonify({'success': False, 'message': '分析失败，请稍后重试'})


# ==================== AI 聊天 API ====================

@bp.route('/api/ai/chat', methods=['POST'])
@require_admin
def ai_chat():
    """AI 聊天问答（支持多轮对话 + 工具调用 + 历史保存）。

    请求体额外字段：
    - enable_tools (bool): 是否启用 AI 工具调用，默认 true（需要同时有 baby_id）
    - max_tool_rounds (int): 工具最大循环轮次，默认 5
    响应额外字段：
    - tool_events (list): 本次对话中 AI 调用的工具列表，前端可渲染卡片
    """
    try:
        data = request.get_json()
        if not data or 'message' not in data:
            return jsonify({'success': False, 'message': '请输入问题'}), 400

        question = data['message']
        baby_id = data.get('baby_id')
        # 对话内选择的回答模型：''=跟随全局设置；'__rule__'=强制规则引擎；其他=临时指定模型
        model_sel = str(data.get('model') or '').strip()
        force_rule = model_sel == '__rule__'
        enable_tools = bool(data.get('enable_tools', True))
        max_tool_rounds = _safe_max_tool_rounds(data.get('max_tool_rounds', 5))
        db = get_db() if baby_id else None

        # 有宝宝时：保存历史 + 多轮对话
        if db and baby_id:
            from ai_engine import (
                save_chat_message, get_chat_context,
                chat_response, LLM, PROVIDER_TEMPLATES,
                _get_baby_context as _build_baby_ctx,
            )

            # 多 worker 部署下同步持久化配置到当前进程
            _ensure_llm_applied()

            # 保存用户消息
            try:
                save_chat_message(db, baby_id, 'user', question)
            except Exception:
                pass  # 保存历史失败不影响主功能

            answer = None
            tool_events: list = []
            fallback_reason = ''
            llm_answered = False     # 这次回答是否**真的**来自大模型（回退时必须是 False）

            provider_now = (LLM.get('provider') or '').strip()
            is_local_provider = provider_now in ('ollama', 'lmstudio')

            def _reason_now():
                breaker_state = ''
                try:
                    from ai_client import get_ai_client_stats
                    breaker_state = (get_ai_client_stats().get('circuit_breaker') or {}).get('state', '')
                except Exception:
                    pass
                # 用**解密后**的 key 判断有没有配，别拿密文当"已配置"
                try:
                    from ai_engine import decrypt_api_key, get_last_llm_error
                    key_ok = bool(decrypt_api_key(LLM.get('api_key') or ''))
                    last_err = get_last_llm_error()
                except Exception:
                    key_ok = bool(LLM.get('api_key'))
                    last_err = ''
                return _llm_fail_reason(
                    provider_now,
                    bool(LLM.get('consent_cloud')),
                    key_ok,
                    breaker_state,
                    last_err,
                )

            # 可用性判定与 ai_client._call_llm_with_retry 的拦截条件保持一致：
            # 云端厂商未同意数据外发时必然被静默拦截，提前视为不可用并给出明确原因，
            # 免得白烧两次重试 + 熔断计数。
            llm_enabled = bool(provider_now) and not force_rule \
                and (is_local_provider or LLM.get('consent_cloud'))
            if not llm_enabled and not force_rule:
                fallback_reason = _reason_now()

            if llm_enabled:
                from ai_client import call_llm_with_resilience
                from ai_tools import get_tool_definitions, execute_tool
                # 构建多轮上下文
                try:
                    history = get_chat_context(db, baby_id, limit=10)
                except Exception:
                    history = None
                # 上一行已把本次提问写入历史，取回后要把末尾这条剔掉，
                # 否则 call_llm 会再把当前问题追加一次，模型收到重复提问。
                if history and history[-1].get('role') == 'user' \
                        and history[-1].get('content') == question:
                    history = history[:-1] or None

                # 注入宝宝精简上下文：本地模型无隐私顾虑；
                # 云端厂商仅在用户已勾选数据外发同意时注入（与 call_llm 的门槛一致）。
                baby_context = None
                if LLM.get('provider') in ('ollama', 'lmstudio') or LLM.get('consent_cloud'):
                    try:
                        ctx = _build_baby_ctx(db, baby_id) or {}
                        parts = []
                        if ctx.get('age_months') is not None:
                            parts.append(f"月龄{ctx['age_months']}个月")
                        if ctx.get('weight'):
                            parts.append(f"体重{ctx['weight']}kg")
                        if ctx.get('height'):
                            parts.append(f"身高{ctx['height']}cm")
                        if ctx.get('head_circumference'):
                            parts.append(f"头围{ctx['head_circumference']}cm")
                        baby_context = '，'.join(parts) or None
                    except Exception:
                        baby_context = None

                # 定义工具执行回调，捕获调用过程
                def _exec_tool(name: str, args: dict) -> dict:
                    result = execute_tool(name, args, db, baby_id)
                    tool_events.append({'tool': name, 'arguments': args, 'result': result, 'success': bool(result.get('success'))})
                    return result

                # 调用 LLM（带韧性层：重试 + 熔断 + 缓存；支持对话内选择模型）
                # 当 enable_tools=True 且存在 baby_id 时注入工具
                tools = get_tool_definitions(baby_id) if enable_tools and baby_id else None
                try:
                    ok, answer = call_llm_with_resilience(
                        question,
                        baby_context=baby_context,
                        history=history,
                        model=model_sel or None,
                        tools=tools,
                        execute_tool_fn=_exec_tool if tools else None,
                        max_tool_rounds=max_tool_rounds,
                    )
                except Exception as e:
                    import logging
                    logging.getLogger(__name__).error("LLM调用异常: %s", e)
                    ok = False
                    answer = None
                llm_answered = bool(ok and answer)
                if not llm_answered:
                    answer = None

            # 没拿到大模型回答就回退规则引擎 —— 并且**一定**把原因带上
            if not llm_answered:
                answer = chat_response(question, db, baby_id)
                if not fallback_reason:
                    fallback_reason = _reason_now()

            # 保存 AI 回复
            try:
                save_chat_message(db, baby_id, 'assistant', answer)
            except Exception:
                pass

            # 回答来源：**只有真的拿到大模型回答才标模型名**。
            # 以前只在 llm_enabled 上判断，于是"配置没问题但调用失败（key 坏/网络不通/
            # 熔断）"时，规则引擎的回答会被标成 deepseek-v4-flash —— 用户看到的就是
            # "配好了、AI 却在装傻"，而且前端据此判断要不要显示失败原因，
            # 结果那套提示在**最常见**的失败场景下永远不出现。
            if llm_answered:
                used_model = (model_sel or LLM.get('model')
                              or PROVIDER_TEMPLATES.get(LLM.get('provider', ''), {}).get('default_model', '规则引擎'))
            else:
                used_model = '规则引擎'

            # ---- 本次 AI 问答的**结论行**：一次失败的所有关键事实汇总成一行 ----
            # 排查「配好了却一直走规则引擎」时，只要 grep 这一行就够，
            # 不用去把前面几跳的日志按 trace id 拼回来。
            try:
                from ai_engine import llm_trace, _new_trace_id
                llm_trace(
                    _new_trace_id(), '══ 本次问答结论',
                    'provider=%s model_sel=%s llm_enabled=%s 实际用=%s%s'
                    % (provider_now or '(空)', model_sel or '(跟随设置)',
                       llm_enabled, used_model,
                       ('｜回退原因：' + str(fallback_reason)[:200]) if fallback_reason else '')
                )
            except Exception:
                pass

            return jsonify({
                'success': True,
                'answer': answer,
                'llm_enabled': llm_enabled,
                'llm_used': llm_answered,      # 显式字段：前端别靠模型名字符串猜
                'llm_model': used_model,
                'fallback_reason': fallback_reason,
                'tool_events': tool_events,
                'timestamp': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            })

        # 无宝宝 ID 时的简单回复
        from ai_engine import chat_response
        answer = chat_response(question, db, baby_id)
        return jsonify({
            'success': True,
            'answer': answer,
            'llm_enabled': False,
            'llm_used': False,
            'llm_model': '规则引擎',
            'fallback_reason': '未选择宝宝，大模型需要宝宝数据作为上下文，本次由本地规则引擎回答',
            'tool_events': [],
            'timestamp': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        })
    except Exception as e:
        import logging
        logging.getLogger(__name__).error("AI chat error: %s", e, exc_info=True)
        # 即使出错也返回一个友好回答，避免前端显示"网络错误"
        try:
            from ai_engine import chat_response
            fallback = chat_response(data.get('message', '') if data else '', None, None)
        except Exception:
            fallback = "抱歉，我暂时无法回答这个问题。请稍后再试，或尝试换个方式提问。"
        return jsonify({
            'success': True,
            'answer': fallback,
            'llm_enabled': False,
            'llm_used': False,
            'llm_model': '规则引擎',
            'fallback_reason': '对话处理异常，已用本地规则引擎兜底',
            'tool_events': [],
            'timestamp': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        })


def _safe_max_tool_rounds(v) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return 5
    return max(1, min(n, 10))


def _llm_fail_reason(provider_now, consent_cloud, api_key_set, breaker_state='', last_error=''):
    """推断「这次为什么没走大模型」，随响应带回前端展示。

    此前 LLM 失败会静默回退规则引擎：用户配了厂商却没勾「数据外发同意」
    （或密钥错/网络不通/熔断）时，界面毫无提示，看起来就像"接入了但 AI 装傻"。
    纯参数函数，方便离线单测。

    last_error：最近一次调用的真实报错（来自 get_last_llm_error()）。
    401 / 模型不存在 / 超时 是完全不同的处境，只回一句"调用失败"用户没法排查。
    """
    provider_now = (provider_now or '').strip()
    if not provider_now:
        return '未配置大模型，请到 设置 → AI 大模型 选择厂商并填入 API Key'
    is_local = provider_now in ('ollama', 'lmstudio')
    if not is_local and not consent_cloud:
        return '云端厂商需先在 设置 → AI 大模型 勾选「同意数据发送至云端」'
    if not is_local and not api_key_set:
        return 'API Key 未配置，请到 设置 → AI 大模型 填写'
    if breaker_state == 'open':
        return '连续调用失败已熔断，约 2 分钟后自动恢复，可先检查网络与密钥'
    base = '调用失败（检查 API Key、模型名或 NAS 外网连通性）'
    tail = (last_error or '').strip()
    return base + ('；最近一次错误：' + tail if tail else '')


# ==================== LLM 状态与配置 API ====================

@bp.route('/api/ai/llm/status', methods=['GET'])
@require_admin
def ai_llm_status():
    """获取 LLM 状态（含 api_url / system_prompt 等配置细节，仅管理员）"""
    try:
        _ensure_llm_applied()
        from ai_engine import get_llm_status
        from ai_client import get_ai_client_stats
        status = get_llm_status()
        status['client_stats'] = get_ai_client_stats()
        return jsonify({
            'success': True,
            'status': status
        })
    except Exception as e:
        import logging
        logging.getLogger(__name__).error("ai_llm_status error: %s", e)
        return jsonify({'success': True, 'status': {'enabled': False, 'provider': '', 'model': ''}})


@bp.route('/api/ai/llm/providers', methods=['GET'])
def ai_llm_providers():
    """获取内置厂商模板列表（含分组信息）"""
    try:
        from ai_engine import get_provider_templates, get_provider_groups
        return jsonify({
            'success': True,
            'providers': get_provider_templates(),
            'groups': get_provider_groups(),
        })
    except Exception as e:
        import logging
        logging.getLogger(__name__).error("ai_llm_providers error: %s", e)
        return jsonify({'success': False, 'message': '加载提供商列表失败'}), 500


def _reset_breaker_silently():
    """把熔断器清零（失败静默）。

    用户改完配置 / 测试通了之后，上一次的连续失败不该再拦着他 ——
    否则"改好了还要再等 2 分钟才生效"，用户只会以为改动没用。
    """
    try:
        from ai_client import reset_circuit_breaker
        reset_circuit_breaker()
    except Exception:
        pass


def _clear_ai_cache_silently():
    """清掉回答缓存（失败静默）。

    配置一变（换厂商 / 换模型 / 换 Key），旧缓存必须作废：缓存键只带
    provider+model，TTL 又是 1 小时 ⇒ 不清理的话，换完厂商后同样的提问
    还会拿到**上一个厂商**的回答，看起来像"改了没生效"。
    （clear_ai_cache 一直没人调过，2026-10-05 补上。）
    """
    try:
        from ai_client import clear_ai_cache
        clear_ai_cache()
    except Exception:
        pass


def _list_remote_models(api_url, headers, timeout_val):
    """尽力取厂商的可用模型列表（OpenAI 兼容的 /models）。

    取不到一律返回 []：这是**加分项**，不能因为厂商没实现 /models 就把测试判失败。
    安全：只从已经过 _is_safe_url 校验的 api_url 推同源地址，推导结果再校验一次。
    """
    import json as _json2
    import urllib.request as _ur

    u = (api_url or '').rstrip('/')
    for suffix in ('/chat/completions', '/completions'):
        if u.endswith(suffix):
            u = u[:-len(suffix)]
            break
    if not u:
        return []
    models_url = u + '/models'
    if not _is_safe_url(models_url):
        return []
    try:
        req = _ur.Request(models_url, headers=headers)
        with _ur.urlopen(req, timeout=min(int(timeout_val or 10), 10)) as resp:
            data = _json2.loads(resp.read().decode('utf-8'))
    except Exception:
        return []
    if isinstance(data, dict) and isinstance(data.get('data'), list):        # OpenAI 兼容
        return [str(m.get('id')) for m in data['data'] if isinstance(m, dict) and m.get('id')]
    if isinstance(data, dict) and isinstance(data.get('models'), list):      # 少数厂商用 models
        out = []
        for m in data['models']:
            if isinstance(m, dict):
                name = m.get('name') or m.get('id')
                if name:
                    out.append(str(name))
        return out
    return []


def _is_safe_url(url):
    """检查 URL 是否安全（防止 SSRF）。

    禁止链路本地/元数据/私有网段；仅放行 localhost 给 Ollama/LM Studio。
    此前 172.* 会误伤 172.16–172.31 以外的公网 172.x，同时又漏掉
    169.254.169.254（云元数据）、0.0.0.0、IPv6 ULA/link-local。
    """
    if not url:
        return True
    import ipaddress
    import socket
    import urllib.parse
    try:
        parsed = urllib.parse.urlparse(str(url))
        if parsed.scheme not in ('http', 'https'):
            return False
        hostname = (parsed.hostname or '').lower()
        if not hostname:
            return False
        # 本地服务（ollama/lmstudio）允许 localhost
        if hostname in ('localhost', '127.0.0.1', '::1'):
            return True
        # 解析为 IP 后按网段判定（覆盖十进制/编码 IP 与多数直接 IP）
        try:
            infos = socket.getaddrinfo(hostname, None)
            addrs = {item[4][0] for item in infos}
        except socket.gaierror:
            addrs = set()
        if not addrs:
            # 无法解析则退回字面量规则
            addrs = {hostname}
        for addr in addrs:
            try:
                ip = ipaddress.ip_address(addr.split('%')[0])
            except ValueError:
                return False
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                return False
            if ip.version == 6 and (ip.is_site_local or ip.ipv4_mapped):
                mapped = ip.ipv4_mapped
                if mapped and (mapped.is_private or mapped.is_link_local or mapped.is_loopback):
                    return False
                if ip.is_site_local:
                    return False
        return True
    except Exception:
        return False


@bp.route('/api/ai/llm/test', methods=['POST'])
@require_admin
def ai_llm_test():
    """测试 LLM 连接是否正常"""
    import json as _json
    import urllib.request
    import urllib.parse
    import urllib.error
    import logging

    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'message': '无效的配置'}), 400

    provider = data.get('provider', '')
    if not provider:
        return jsonify({'success': False, 'message': '请选择提供商'}), 400

    from ai_engine import PROVIDER_TEMPLATES
    if provider not in PROVIDER_TEMPLATES:
        return jsonify({'success': False, 'message': '不支持的提供商'}), 400

    template = PROVIDER_TEMPLATES[provider]
    api_url = data.get('api_url') or template.get('api_url', '')
    model = data.get('model') or template.get('default_model', '')
    api_key = data.get('api_key', '')
    timeout_val = min(int(data.get('timeout', 15)), 60)

    if not api_url:
        return jsonify({'success': False, 'message': '请填写 API 地址'})

    # SSRF 防护：与 /api/ai/llm/configure 同一套白名单，禁止内网探测
    if not _is_safe_url(api_url):
        return jsonify({'success': False, 'message': 'API 地址不安全或格式错误'}), 400

    # 检查本地服务
    if provider in ('ollama', 'lmstudio'):
        try:
            parsed_url = api_url.rstrip('/')
            if provider == 'ollama':
                check_url = f"{parsed_url.replace('/api/chat', '')}/api/tags"
            else:
                check_url = f"{parsed_url.replace('/v1/chat/completions', '')}/v1/models"
            req = urllib.request.Request(check_url, headers={'Accept': 'application/json'})
            with urllib.request.urlopen(req, timeout=5) as resp:
                result = _json.loads(resp.read().decode('utf-8'))
                models = []
                if provider == 'ollama' and 'models' in result:
                    models = [m.get('name', '') for m in result['models']]
                elif 'data' in result:
                    models = [m.get('id', '') for m in result['data']]
                _reset_breaker_silently()      # 测通了：旧失败不该再拦着
                return jsonify({
                    'success': True,
                    'message': f'连接成功！发现 {len(models)} 个模型',
                    'models': models,
                })
        except Exception as e:
            return jsonify({'success': False, 'message': f'连接失败: {str(e)}'})

    # 云端服务 - 发送测试请求
    if not api_key:
        # 输入框在"已配置"时是空的 ⇒ 用**库里存的那份**来测。
        # 否则会出现"明明配好了，点测试却提示请填写 API Key"，
        # 逼用户为了测试再把 Key 翻出来粘一遍（还可能粘错）。
        try:
            from ai_engine import LLM as _LLM_now, decrypt_api_key
            api_key = decrypt_api_key(_LLM_now.get('api_key') or '')
        except Exception:
            api_key = ''
    if not api_key:
        return jsonify({'success': False, 'message': '请填写 API Key'})

    try:
        # 构建测试消息
        messages = [{'role': 'user', 'content': 'Hi'}]
        payload = {
            'model': model,
            'messages': messages,
            'max_tokens': 10,
            'temperature': 0,
        }

        # 构建请求头
        auth_header = template.get('auth_header', 'Authorization')
        auth_format = template.get('auth_format', 'Bearer {api_key}')
        headers = {'Content-Type': 'application/json'}
        if auth_header and auth_format:
            headers[auth_header] = auth_format.format(api_key=api_key)

        extra_headers = template.get('extra_headers', {})
        headers.update(extra_headers)

        # 顺手问一下「这个账号到底能用哪些模型」——失败不影响主流程。
        # 用途：① 选了不存在/没开通的模型时，错误信息能直接给出可用清单；
        #       ② 成功时也能校验所选模型是否真的在列表里（推荐项漂移的兜底）。
        available = _list_remote_models(api_url, headers, timeout_val)

        req = urllib.request.Request(
            api_url,
            data=_json.dumps(payload).encode('utf-8'),
            headers=headers,
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_val) as resp:
                result = _json.loads(resp.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            body = e.read().decode('utf-8', errors='replace')[:200] if hasattr(e, 'read') else ''
            msg = f'HTTP {e.code}: {body}'
            if available:
                msg += '｜该账号可用模型：' + ', '.join(available[:12])
            return jsonify({'success': False, 'message': msg,
                            'available_models': available[:50]})

        if 'choices' in result and result['choices']:
            reply = result['choices'][0].get('message', {}).get('content', 'Connected')
            resp_obj = {
                'success': True,
                'message': '连接成功！',
                'reply': reply[:100],
            }
            if available:
                resp_obj['available_models'] = available[:50]
                resp_obj['model_ok'] = (not model) or (model in available)
                if model and model not in available:
                    resp_obj['message'] = (
                        '连接成功，但所选模型 %s 不在该账号的可用模型列表里（仍可能可用，'
                        '如自定义/别名模型）。该账号可用：%s' % (model, ', '.join(available[:10]))
                    )
            _reset_breaker_silently()          # 测通了：旧失败不该再拦着对话
            return jsonify(resp_obj)
        else:
            return jsonify({'success': False, 'message': '响应格式异常'})
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='replace')[:200] if hasattr(e, 'read') else ''
        return jsonify({'success': False, 'message': f'HTTP {e.code}: {body}'})
    except urllib.error.URLError as e:
        return jsonify({'success': False, 'message': f'连接失败: {str(e.reason)}'})
    except Exception as e:
        return jsonify({'success': False, 'message': f'测试失败: {str(e)}'})


@bp.route('/api/ai/ollama/models', methods=['GET'])
def ai_ollama_models():
    """获取 Ollama 本地已安装的模型列表"""
    from ai_engine import LLM, PROVIDER_TEMPLATES
    import urllib.request
    import urllib.parse
    import json as _json

    url = LLM.get('api_url') or PROVIDER_TEMPLATES['ollama']['api_url']
    parsed = urllib.parse.urlparse(url)

    # 安全：仅允许本地地址
    if parsed.hostname not in ('localhost', '127.0.0.1', '::1'):
        return jsonify({'success': False, 'message': '仅支持本地 Ollama'}), 403

    try:
        api_url = url.rsplit('/api/chat', 1)[0] + '/api/tags'
        req = urllib.request.Request(api_url)
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = _json.loads(resp.read().decode('utf-8'))
            models = [m.get('name', '') for m in data.get('models', []) if m.get('name')]
            return jsonify({'success': True, 'models': models})
    except Exception as e:
        return jsonify({'success': False, 'message': f'无法连接 Ollama: {str(e)[:100]}'})


@bp.route('/api/ai/lmstudio/models', methods=['GET'])
def ai_lmstudio_models():
    """获取 LM Studio 本地已加载的模型列表"""
    from ai_engine import get_lmstudio_models
    ok, result = get_lmstudio_models()
    if ok:
        return jsonify({'success': True, 'models': result})
    else:
        return jsonify({'success': False, 'message': result})


@bp.route('/api/ai/llm/configure', methods=['POST'])
@require_admin
def ai_llm_configure():
    """配置 LLM（含同意门控与地址白名单校验）"""
    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'message': '无效的配置'}), 400

    from ai_engine import PROVIDER_TEMPLATES, configure_llm

    provider = data.get('provider', '')
    if provider != '' and provider not in PROVIDER_TEMPLATES:
        return jsonify({'success': False, 'message': '不支持的厂商'}), 400

    # 云端厂商必须取得用户明确同意（儿童健康数据外发合规要求）；本地服务无需同意
    consent_cloud = bool(data.get('consent_cloud'))
    if provider not in ('', 'ollama', 'lmstudio') and not consent_cloud:
        return jsonify({'success': False, 'message': '需先同意数据外发条款'}), 403

    # SSRF 防护：验证 api_url 和 base_url（_is_safe_url 为模块级函数）
    # 验证 base_url
    base_url = data.get('base_url', '')
    if base_url and not _is_safe_url(base_url):
        return jsonify({'success': False, 'message': 'Base URL 地址不安全或格式错误'}), 400

    # 验证 api_url
    api_url = data.get('api_url', '')
    if api_url and not _is_safe_url(api_url):
        return jsonify({'success': False, 'message': 'API URL 地址不安全或格式错误'}), 400

    # 本地服务仅允许本机地址
    if provider in ('ollama', 'lmstudio') and api_url:
        if not (api_url.startswith('http://localhost') or api_url.startswith('http://127.')):
            data.pop('api_url')

    api_key = data.get('api_key')
    if api_key is not None and len(str(api_key)) > 200:
        return jsonify({'success': False, 'message': 'API Key 无效'}), 400

    # ⚠ 输入框在"已配置"时是空的（只显示占位提示「已配置 API Key（输入新值以替换）」），
    # 前端照样会把空串发过来（saveLLMConfig 恒定带 api_key 字段）。
    # 若把空串当成"清空"，用户改个温度再点保存，已存的 Key 就被抹掉了 ——
    # 之后一切调用 401、静默回退规则引擎，而界面看着"配置还在"。
    # 规则：留空 = 不修改；只有显式关闭 LLM（provider 为空）或**换了厂商**时才清掉，
    # 后者是因为把 A 厂商的 Key 拿去打 B 厂商接口必然 401，不如逼用户重填。
    if not str(api_key or '').strip():
        from ai_engine import LLM as _LLM_now
        provider_changed = (provider or '') != (_LLM_now.get('provider') or '')
        if provider and not provider_changed:
            data.pop('api_key', None)          # 保留已存 Key，本次不动它
        else:
            data['api_key'] = ''               # 关闭 / 换厂商：清掉旧凭据

    # 处理自定义模型列表
    custom_models = data.get('custom_models', [])
    if isinstance(custom_models, list):
        # 过滤空值和过长的模型名
        custom_models = [str(m).strip() for m in custom_models if m and len(str(m).strip()) < 100]
        data['custom_models'] = custom_models
    else:
        data['custom_models'] = []

    # 处理视觉模型字段
    if 'vision_enabled' in data:
        data['vision_enabled'] = bool(data.get('vision_enabled'))
    if 'vision_model' not in data:
        data['vision_model'] = ''

    data['consent_cloud'] = consent_cloud
    config = configure_llm(data)
    _save_llm_config(config)
    # 配置变了 ⇒ 熔断器与回答缓存都要作废，否则"改完像没生效"
    _reset_breaker_silently()
    _clear_ai_cache_silently()

    return jsonify({
        'success': True,
        'message': '配置已更新',
        'config': {k: v for k, v in config.items() if k != 'api_key'}  # 不返回 api_key
    })


# ==================== AI 周报/月报 API ====================

@bp.route('/api/ai/report/weekly/<int:baby_id>', methods=['GET'])
def ai_weekly_report(baby_id):
    """生成周报"""
    from ai_engine import generate_weekly_report
    db = get_db()
    result = generate_weekly_report(db, baby_id)
    return jsonify(result)


@bp.route('/api/ai/report/monthly/<int:baby_id>', methods=['GET'])
def ai_monthly_report(baby_id):
    """生成月报"""
    from ai_engine import generate_monthly_report
    db = get_db()
    result = generate_monthly_report(db, baby_id)
    return jsonify(result)


# ==================== 聊天历史 API ====================

@bp.route('/api/ai/chat/history/<int:baby_id>', methods=['GET'])
def ai_get_history(baby_id):
    """获取聊天历史"""
    try:
        from ai_engine import get_chat_history
        db = get_db()
        history = get_chat_history(db, baby_id, limit=50)
        return jsonify({'success': True, 'history': history})
    except Exception as e:
        import logging
        logging.getLogger(__name__).error("get_chat_history error: %s", e)
        return jsonify({'success': True, 'history': []})


@bp.route('/api/ai/chat/history/<int:baby_id>', methods=['DELETE'])
@require_admin
def ai_clear_history(baby_id):
    """清除聊天历史"""
    try:
        from ai_engine import clear_chat_history
        db = get_db()
        clear_chat_history(db, baby_id)
        return jsonify({'success': True, 'message': '聊天历史已清除'})
    except Exception as e:
        import logging
        logging.getLogger(__name__).error("clear_chat_history error: %s", e)
        return jsonify({'success': False, 'message': '清除失败'})


# ==================== AI 数据写入 API ====================
# AI 生成草稿，用户确认后保存（不自动写入数据库）

def _get_baby_context(db, baby_id):
    """获取宝宝信息上下文（供 AI 生成使用）"""
    if not db or not baby_id:
        return {}
    baby = db.execute('SELECT * FROM babies WHERE id = ?', (baby_id,)).fetchone()
    if not baby:
        return {}
    # 注意：连接开启了 row_factory = sqlite3.Row，Row 没有 .get()，
    # 必须先转成 dict，否则调用方（日记/完整日记生成）会 AttributeError 500。
    baby = dict(baby)
    from ai_engine import get_age_months
    return {
        'name': baby.get('name', '宝宝'),
        'birthday': baby.get('birthday', ''),
        'gender': baby.get('gender', ''),
        'age_months': get_age_months(baby.get('birthday', '')),
    }


@bp.route('/api/ai/write/diary', methods=['POST'])
def ai_write_diary():
    """AI 生成育儿日记草稿"""
    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'message': '无效的请求'}), 400

    user_input = data.get('input', '')
    baby_id = data.get('baby_id')
    db = get_db() if baby_id else None

    # 确保 LLM 配置已应用
    _ensure_llm_applied()

    from ai_engine import LLM
    if not LLM.get('provider'):
        return jsonify({'success': False, 'message': '请先配置 AI 大模型'}), 400

    baby_ctx = _get_baby_context(db, baby_id) if db else {}

    from ai_writer import generate_diary_entry
    ok, content = generate_diary_entry(baby_ctx, user_input)

    if ok:
        return jsonify({
            'success': True,
            'content': content,
            'message': '日记草稿已生成，请编辑后保存',
        })
    else:
        return jsonify({'success': False, 'message': '生成失败，请检查 AI 配置或稍后重试'}), 500


@bp.route('/api/ai/write/photo-description', methods=['POST'])
def ai_write_photo_description():
    """AI 生成照片描述"""
    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'message': '无效的请求'}), 400

    _ensure_llm_applied()
    from ai_engine import LLM
    if not LLM.get('provider'):
        return jsonify({'success': False, 'message': '请先配置 AI 大模型'}), 400

    photo_context = {
        'baby_name': data.get('baby_name', ''),
        'age_months': data.get('age_months'),
        'photo_time': data.get('photo_time', ''),
        'location': data.get('location', ''),
        'notes': data.get('notes', ''),
    }

    from ai_writer import generate_photo_description
    ok, content = generate_photo_description(photo_context)

    if ok:
        return jsonify({
            'success': True,
            'content': content,
            'message': '照片描述已生成',
        })
    else:
        return jsonify({'success': False, 'message': '生成失败，请检查 AI 配置或稍后重试'}), 500


@bp.route('/api/ai/write/extract-feeding', methods=['POST'])
def ai_extract_feeding():
    """AI 从自然语言提取喂养记录"""
    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'message': '无效的请求'}), 400

    user_input = data.get('input', '')
    if not user_input:
        return jsonify({'success': False, 'message': '请输入描述'}), 400

    _ensure_llm_applied()
    from ai_engine import LLM
    if not LLM.get('provider'):
        return jsonify({'success': False, 'message': '请先配置 AI 大模型'}), 400

    from ai_writer import extract_feeding_record
    ok, record = extract_feeding_record(user_input)

    if ok and record:
        return jsonify({
            'success': True,
            'record': record,
            'message': '已提取喂养记录，请确认后保存',
        })
    else:
        return jsonify({'success': False, 'message': '无法识别，请手动填写或换一种描述'}), 422


@bp.route('/api/ai/write/extract-sleep', methods=['POST'])
def ai_extract_sleep():
    """AI 从自然语言提取睡眠记录"""
    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'message': '无效的请求'}), 400

    user_input = data.get('input', '')
    if not user_input:
        return jsonify({'success': False, 'message': '请输入描述'}), 400

    _ensure_llm_applied()
    from ai_engine import LLM
    if not LLM.get('provider'):
        return jsonify({'success': False, 'message': '请先配置 AI 大模型'}), 400

    from ai_writer import extract_sleep_record
    ok, record = extract_sleep_record(user_input)

    if ok and record:
        return jsonify({
            'success': True,
            'record': record,
            'message': '已提取睡眠记录，请确认后保存',
        })
    else:
        return jsonify({'success': False, 'message': '无法识别，请手动填写或换一种描述'}), 422


@bp.route('/api/ai/write/suggest-milestones', methods=['POST'])
def ai_suggest_milestones():
    """AI 建议发育里程碑"""
    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'message': '无效的请求'}), 400

    _ensure_llm_applied()
    from ai_engine import LLM
    if not LLM.get('provider'):
        return jsonify({'success': False, 'message': '请先配置 AI 大模型'}), 400

    baby_context = {
        'age_months': data.get('age_months', 0),
        'gender': data.get('gender', ''),
    }
    recent = data.get('recent_performance', '')

    from ai_writer import suggest_milestones
    ok, suggestions = suggest_milestones(baby_context, recent)

    if ok:
        return jsonify({
            'success': True,
            'suggestions': suggestions,
            'message': f'找到 {len(suggestions)} 个建议里程碑',
        })
    else:
        return jsonify({'success': False, 'message': '生成失败，请检查 AI 配置或稍后重试'}), 500


@bp.route('/api/ai/write/weekly-summary', methods=['POST'])
def ai_write_weekly_summary():
    """AI 生成周报文字总结"""
    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'message': '无效的请求'}), 400

    _ensure_llm_applied()
    from ai_engine import LLM
    if not LLM.get('provider'):
        return jsonify({'success': False, 'message': '请先配置 AI 大模型'}), 400

    baby_context = {
        'name': data.get('baby_name', '宝宝'),
        'age_months': data.get('age_months'),
    }
    weekly_data = data.get('weekly_data', {})

    from ai_writer import generate_weekly_summary
    ok, content = generate_weekly_summary(baby_context, weekly_data)

    if ok:
        return jsonify({
            'success': True,
            'content': content,
            'message': '周报总结已生成',
        })
    else:
        return jsonify({'success': False, 'message': '生成失败，请检查 AI 配置或稍后重试'}), 500


# ==================== 全记录类型 AI 提取 API ====================
# 所有记录类型均支持自然语言 → 结构化数据提取

def _check_llm_available():
    """检查 LLM 是否可用"""
    _ensure_llm_applied()
    from ai_engine import LLM
    if not LLM.get('provider'):
        return False
    return True


@bp.route('/api/ai/write/extract-diaper', methods=['POST'])
def ai_extract_diaper():
    """AI 提取换尿布记录"""
    data = request.get_json()
    if not data or not data.get('input'):
        return jsonify({'success': False, 'message': '请输入描述'}), 400
    if not _check_llm_available():
        return jsonify({'success': False, 'message': '请先配置 AI 大模型'}), 400

    from ai_writer import extract_diaper_record
    ok, record = extract_diaper_record(data['input'])
    if ok:
        return jsonify({'success': True, 'record': record, 'message': '已提取换尿布记录，请确认后保存'})
    return jsonify({'success': False, 'message': '无法识别，请手动填写'}), 422


@bp.route('/api/ai/write/extract-growth', methods=['POST'])
def ai_extract_growth():
    """AI 提取成长记录"""
    data = request.get_json()
    if not data or not data.get('input'):
        return jsonify({'success': False, 'message': '请输入描述'}), 400
    if not _check_llm_available():
        return jsonify({'success': False, 'message': '请先配置 AI 大模型'}), 400

    from ai_writer import extract_growth_record
    ok, record = extract_growth_record(data['input'])
    if ok:
        return jsonify({'success': True, 'record': record, 'message': '已提取成长记录，请确认后保存'})
    return jsonify({'success': False, 'message': '无法识别，请手动填写'}), 422


@bp.route('/api/ai/write/extract-temperature', methods=['POST'])
def ai_extract_temperature():
    """AI 提取体温记录"""
    data = request.get_json()
    if not data or not data.get('input'):
        return jsonify({'success': False, 'message': '请输入描述'}), 400
    if not _check_llm_available():
        return jsonify({'success': False, 'message': '请先配置 AI 大模型'}), 400

    from ai_writer import extract_temperature_record
    ok, record = extract_temperature_record(data['input'])
    if ok:
        return jsonify({'success': True, 'record': record, 'message': '已提取体温记录，请确认后保存'})
    return jsonify({'success': False, 'message': '无法识别，请手动填写'}), 422


@bp.route('/api/ai/write/extract-medication', methods=['POST'])
def ai_extract_medication():
    """AI 提取用药记录"""
    data = request.get_json()
    if not data or not data.get('input'):
        return jsonify({'success': False, 'message': '请输入描述'}), 400
    if not _check_llm_available():
        return jsonify({'success': False, 'message': '请先配置 AI 大模型'}), 400

    from ai_writer import extract_medication_record
    ok, record = extract_medication_record(data['input'])
    if ok:
        return jsonify({'success': True, 'record': record, 'message': '已提取用药记录，请确认后保存'})
    return jsonify({'success': False, 'message': '无法识别，请手动填写'}), 422


@bp.route('/api/ai/write/extract-vaccine', methods=['POST'])
def ai_extract_vaccine():
    """AI 提取疫苗记录"""
    data = request.get_json()
    if not data or not data.get('input'):
        return jsonify({'success': False, 'message': '请输入描述'}), 400
    if not _check_llm_available():
        return jsonify({'success': False, 'message': '请先配置 AI 大模型'}), 400

    from ai_writer import extract_vaccine_record
    ok, record = extract_vaccine_record(data['input'])
    if ok:
        return jsonify({'success': True, 'record': record, 'message': '已提取疫苗记录，请确认后保存'})
    return jsonify({'success': False, 'message': '无法识别，请手动填写'}), 422


@bp.route('/api/ai/write/extract-pumping', methods=['POST'])
def ai_extract_pumping():
    """AI 提取吸奶记录"""
    data = request.get_json()
    if not data or not data.get('input'):
        return jsonify({'success': False, 'message': '请输入描述'}), 400
    if not _check_llm_available():
        return jsonify({'success': False, 'message': '请先配置 AI 大模型'}), 400

    from ai_writer import extract_pumping_record
    ok, record = extract_pumping_record(data['input'])
    if ok:
        return jsonify({'success': True, 'record': record, 'message': '已提取吸奶记录，请确认后保存'})
    return jsonify({'success': False, 'message': '无法识别，请手动填写'}), 422


@bp.route('/api/ai/write/extract-milestone', methods=['POST'])
def ai_extract_milestone():
    """AI 提取里程碑记录"""
    data = request.get_json()
    if not data or not data.get('input'):
        return jsonify({'success': False, 'message': '请输入描述'}), 400
    if not _check_llm_available():
        return jsonify({'success': False, 'message': '请先配置 AI 大模型'}), 400

    from ai_writer import extract_milestone_record
    ok, record = extract_milestone_record(data['input'])
    if ok:
        return jsonify({'success': True, 'record': record, 'message': '已提取里程碑记录，请确认后保存'})
    return jsonify({'success': False, 'message': '无法识别，请手动填写'}), 422


@bp.route('/api/ai/write/extract-teething', methods=['POST'])
def ai_extract_teething():
    """AI 提取出牙记录"""
    data = request.get_json()
    if not data or not data.get('input'):
        return jsonify({'success': False, 'message': '请输入描述'}), 400
    if not _check_llm_available():
        return jsonify({'success': False, 'message': '请先配置 AI 大模型'}), 400

    from ai_writer import extract_teething_record
    ok, record = extract_teething_record(data['input'])
    if ok:
        return jsonify({'success': True, 'record': record, 'message': '已提取出牙记录，请确认后保存'})
    return jsonify({'success': False, 'message': '无法识别，请手动填写'}), 422


@bp.route('/api/ai/write/extract-allergy-test', methods=['POST'])
def ai_extract_allergy_test():
    """AI 提取过敏测试记录"""
    data = request.get_json()
    if not data or not data.get('input'):
        return jsonify({'success': False, 'message': '请输入描述'}), 400
    if not _check_llm_available():
        return jsonify({'success': False, 'message': '请先配置 AI 大模型'}), 400

    from ai_writer import extract_allergy_test_record
    ok, record = extract_allergy_test_record(data['input'])
    if ok:
        return jsonify({'success': True, 'record': record, 'message': '已提取过敏测试记录，请确认后保存'})
    return jsonify({'success': False, 'message': '无法识别，请手动填写'}), 422


@bp.route('/api/ai/write/full-diary', methods=['POST'])
def ai_write_full_diary():
    """AI 生成完整日记（含标题和正文）"""
    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'message': '无效的请求'}), 400
    if not _check_llm_available():
        return jsonify({'success': False, 'message': '请先配置 AI 大模型'}), 400

    baby_id = data.get('baby_id')
    db = get_db() if baby_id else None
    baby_ctx = _get_baby_context(db, baby_id) if db else {}

    from ai_writer import generate_full_diary
    ok, diary = generate_full_diary(baby_ctx, data.get('input', ''))
    if ok:
        return jsonify({'success': True, 'diary': diary, 'message': '日记已生成，请编辑后保存'})
    return jsonify({'success': False, 'message': '生成失败，请检查 AI 配置或稍后重试'}), 500
