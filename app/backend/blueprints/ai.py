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
    """确保当前 worker 进程的内存 LLM 配置与持久化配置一致（每次 AI 请求前调用）"""
    try:
        from ai_engine import LLM, configure_llm
        cfg = _load_llm_config()
        if not cfg:
            return
        # 轻量指纹比较，不一致才重新应用
        for key in ('provider', 'model', 'api_url', 'api_key', 'consent_cloud', 'temperature', 'max_tokens', 'timeout', 'system_prompt', 'vision_enabled', 'vision_model'):
            if LLM.get(key) != cfg.get(key):
                configure_llm(cfg)
                return
    except Exception as e:
        logger.warning("_ensure_llm_applied 失败: %s", e)


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
            llm_enabled = LLM.get('provider') != '' and not force_rule

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

            # LLM 失败则回退规则引擎
            if not answer:
                # 规则引擎模式下若用户意图是写入，主动提示需要 LLM
                answer = chat_response(question, db, baby_id)

            # 保存 AI 回复
            try:
                save_chat_message(db, baby_id, 'assistant', answer)
            except Exception:
                pass

            # 回答来源：对话内选择的模型 > 全局配置模型 > 规则引擎
            if llm_enabled and answer and model_sel:
                used_model = model_sel
            elif llm_enabled:
                used_model = LLM.get('model') or PROVIDER_TEMPLATES.get(LLM.get('provider', ''), {}).get('default_model', '规则引擎')
            else:
                used_model = '规则引擎'

            return jsonify({
                'success': True,
                'answer': answer,
                'llm_enabled': llm_enabled,
                'llm_model': used_model,
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
            'llm_model': '规则引擎',
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
            'llm_model': '规则引擎',
            'tool_events': [],
            'timestamp': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        })


def _safe_max_tool_rounds(v) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return 5
    return max(1, min(n, 10))


# ==================== LLM 状态与配置 API ====================

@bp.route('/api/ai/llm/status', methods=['GET'])
def ai_llm_status():
    """获取 LLM 状态"""
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


def _is_safe_url(url):
    """检查 URL 是否安全（防止 SSRF）：禁止内网/私有地址（除 localhost 本地服务）"""
    if not url:
        return True
    import urllib.parse
    try:
        parsed = urllib.parse.urlparse(str(url))
        hostname = parsed.hostname or ''
        # 本地服务（ollama/lmstudio）允许 localhost
        if hostname in ('localhost', '127.0.0.1', '::1'):
            return True
        # 禁止私有/内网 IP
        if hostname.startswith('10.') or hostname.startswith('192.168.') or hostname.startswith('172.'):
            return False
        # 必须是 http 或 https
        return parsed.scheme in ('http', 'https')
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
                return jsonify({
                    'success': True,
                    'message': f'连接成功！发现 {len(models)} 个模型',
                    'models': models,
                })
        except Exception as e:
            return jsonify({'success': False, 'message': f'连接失败: {str(e)}'})

    # 云端服务 - 发送测试请求
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

        req = urllib.request.Request(
            api_url,
            data=_json.dumps(payload).encode('utf-8'),
            headers=headers,
        )
        with urllib.request.urlopen(req, timeout=timeout_val) as resp:
            result = _json.loads(resp.read().decode('utf-8'))
            if 'choices' in result and result['choices']:
                reply = result['choices'][0].get('message', {}).get('content', 'Connected')
                return jsonify({
                    'success': True,
                    'message': '连接成功！',
                    'reply': reply[:100],
                })
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
