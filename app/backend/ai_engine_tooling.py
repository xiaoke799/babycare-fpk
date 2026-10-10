# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xiaoke799
"""
小萌 AI 工具调用（Function Calling）调度。

本模块负责：把工具定义注入 LLM 请求、解析返回的 tool_calls、执行工具、
把结果喂回 LLM 进行下一轮对话，直到模型给出最终文本答案。

设计要点：
- 最大循环轮次 max_rounds（防止模型一直要求工具调用）
- 所有工具调用结果走 JSON 序列化，模型能理解
- 工具调用失败不会中断整体流程，而是把错误信息返回给模型使其自愈
- 支持 OpenAI 兼容接口与 Ollama 本地模型（Ollama >= 0.1.45 支持 tools）

注意：Ollama 旧版不支持 tools 参数，调用时会自动跳过工具，仅做纯对话。
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

import utils

logger = logging.getLogger(__name__)


def _should_emit_tool_calls(response: Dict[str, Any]) -> List[Dict[str, Any]]:
    """从响应中抽取 tool_calls 列表（OpenAI / Ollama 适配）。"""
    choices = response.get("choices") or []
    if choices:
        message = choices[0].get("message") or {}
        calls = message.get("tool_calls")
        if isinstance(calls, list) and calls:
            return calls

    # Ollama 直接返回 message.tool_calls
    message = response.get("message") or {}
    calls = message.get("tool_calls")
    if isinstance(calls, list) and calls:
        return calls
    return []


def _build_tool_result_messages(calls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """把一组 tool_calls 转成 assistant + tool 消息对，加入后续对话。"""
    assistant_call_entry: Dict[str, Any] = {
        "role": "assistant",
        "content": None,
        "tool_calls": [],
    }
    tool_messages: List[Dict[str, Any]] = []
    for c in calls:
        func = (c or {}).get("function") or {}
        call_id = (c or {}).get("id") or (func.get("name") or "call")
        name = (func.get("name") or "").strip()
        raw_args = (func.get("arguments") or "{}")
        try:
            arguments = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
        except Exception:
            arguments = {}
        if not isinstance(arguments, dict):
            arguments = {}
        assistant_call_entry["tool_calls"].append({
            "id": call_id,
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)},
        })
    return [assistant_call_entry, *tool_messages]


def _build_single_tool_result_message(call_id: str, content: str) -> Dict[str, Any]:
    return {
        "role": "tool",
        "tool_call_id": call_id,
        "content": content,
    }


def call_llm_with_tools(
    messages: List[Dict[str, Any]],
    tools: List[Dict[str, Any]],
    model_override: Optional[str] = None,
    execute_tool_fn=None,
    max_rounds: int = 5,
    trace_id: Optional[str] = None,
) -> Tuple[bool, Optional[str]]:
    """
    带函数调用的 LLM 对话。

    Args:
        messages: 已构建好的对话列表（system/user/assistant）
        tools: 工具 JSON Schema 列表
        model_override: 临时指定模型
        execute_tool_fn: (tool_name, tool_args) -> dict 的执行回调
        max_rounds: 最多工具循环次数

    Returns:
        (success, final_text_or_None)
    """
    from ai_engine import (LLM, PROVIDER_TEMPLATES, _effective_timeout, llm_trace,
                           _set_last_llm_error, get_last_llm_error)
    provider = LLM.get("provider", "")

    current_messages: List[Dict[str, Any]] = list(messages)
    _model_now = model_override or LLM.get("model") or ''
    llm_trace(trace_id, '④',
              '工具分支入口 provider=%s model=%s 工具数=%d 消息数=%d 最大轮次=%d'
              % (provider or '(空)', _model_now or '(默认)', len(tools or []),
                 len(current_messages), max_rounds))
    if not provider:
        # 与 ai_client 重试层同因：多进程下本 worker 没同步到配置
        llm_trace(trace_id, '④',
                  '工具分支拦截：provider 为空 —— 本 worker 的 LLM 配置未同步，直接失败')
        _set_last_llm_error('本进程没有同步到大模型配置（provider 为空），请重试或重启应用')
        return False, None

    for _round in range(max_rounds):
        if provider == "ollama":
            ok, payload_or_text, tool_calls = _round_ollama(
                current_messages, tools, model_override, trace_id=trace_id)
        else:
            ok, payload_or_text, tool_calls = _round_openai_compat(
                current_messages, tools, model_override, trace_id=trace_id)

        if not ok:
            logger.warning("LLM 请求失败: %s", payload_or_text)
            llm_trace(trace_id, '⑦',
                      '工具分支第 %d 轮失败 → 整条工具链放弃（不再重试，因工具调用是明确指定的）'
                      ' 原因=%s' % (_round + 1, str(payload_or_text)[:200]))
            # 兜底：底层出口若没写具体原因（未来新增出口也算），至少把这一轮的原因带上。
            # 否则界面只会回一句笼统的"调用失败"，401/模型名错/断网三种处境长得一样。
            if not get_last_llm_error():
                _set_last_llm_error(str(payload_or_text) or '大模型调用失败（未给出具体原因）')
            return False, None

        if tool_calls:
            # 构建单个 assistant 消息（包含全部 tool_calls），再为每个 tool 附一条 tool result
            assistant_entry: Dict[str, Any] = {
                "role": "assistant",
                "content": None,
                "tool_calls": [],
            }
            tool_results: List[Dict[str, Any]] = []
            for c in tool_calls:
                func = (c or {}).get("function") or {}
                call_id = (c or {}).get("id") or (func.get("name") or "call")
                name = (func.get("name") or "").strip()
                raw_args = (func.get("arguments") or "{}")
                try:
                    arguments = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                except Exception:
                    arguments = {}
                if not isinstance(arguments, dict):
                    arguments = {}

                # 执行工具
                result: Dict[str, Any] = {"success": False, "error": "未注册工具"}
                try:
                    if execute_tool_fn is not None:
                        result = execute_tool_fn(name, arguments) or {"success": False, "error": "工具执行返回 None"}
                    else:
                        result = {"success": False, "error": "缺少 execute_tool_fn"}
                except Exception as exc:
                    logger.exception("工具执行异常 %s: %s", name, exc)
                    result = {"success": False, "error": f"工具执行异常: {exc}"}

                assistant_entry["tool_calls"].append({
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": json.dumps(arguments, ensure_ascii=False),
                    },
                })
                tool_results.append(_build_single_tool_result_message(call_id, json.dumps(result, ensure_ascii=False)))
            # 一次性 append assistant(tool_calls) + 所有 tool result（避免循环内重复 append）
            current_messages.append(assistant_entry)
            current_messages.extend(tool_results)
            continue

        # 没有 tool_calls: payload_or_text 是最终文本
        final_text = payload_or_text
        if isinstance(final_text, dict):
            final_text = final_text.get("content") or json.dumps(final_text, ensure_ascii=False)
        if isinstance(final_text, str) and final_text.strip():
            _set_last_llm_error('')
            return True, final_text

        # 200 但没有可用正文：不是网络/凭据问题，而是模型名或额度
        _set_last_llm_error('厂商返回了响应但没有可用内容（可能是模型名不对或额度不足）')
        return True, None

    logger.warning("工具调用超过最大轮次 %d，返回空", max_rounds)
    _set_last_llm_error('工具调用超过最大轮次（%d）仍未拿到最终回答' % max_rounds)
    return True, None


def _round_ollama(messages, tools, model_override, trace_id=None):
    """一轮 Ollama 调用，返回 (ok, text_or_None, tool_calls_or_None)

    ⚠ 与 _round_openai_compat 同理：这条路的失败原先也只留一行 warning，
    不说是哪个地址、也不说 Ollama 是不是没起来。
    """
    from ai_engine import (LLM, PROVIDER_TEMPLATES, _effective_timeout, llm_trace,
                           _set_last_llm_error)
    url = LLM.get("api_url") or PROVIDER_TEMPLATES["ollama"]["api_url"]
    parsed = urllib.parse.urlparse(url)
    if parsed.hostname not in ("localhost", "127.0.0.1", "::1") and parsed.scheme != "https":
        logger.warning("非本地 Ollama 必须使用 HTTPS")
        llm_trace(trace_id, '⑤', '工具分支拦截：非本地 Ollama 必须 HTTPS，已拒发 url=%s' % url[:120])
        _set_last_llm_error('非本地 Ollama 地址必须使用 HTTPS，已拒绝发送请求：%s' % url[:120])
        return False, "non-local url", None

    payload: Dict[str, Any] = {
        "model": model_override or LLM.get("model") or PROVIDER_TEMPLATES["ollama"]["default_model"],
        "messages": messages,
        "stream": False,
        "options": {"temperature": LLM.get("temperature") or 0.7},
    }
    # 仅当实际给了 tools 才发，避免旧版 Ollama 不认识
    if tools:
        payload["tools"] = tools

    llm_trace(trace_id, '⑤',
              '工具分支(Ollama) POST %s model=%s timeout=%ss'
              % (url[:120], payload.get('model'), _effective_timeout()))
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with utils.urlopen_bound(req, timeout=_effective_timeout()) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = ''
        try:
            body = exc.read().decode('utf-8', 'replace')[:200]
        except Exception:
            pass
        logger.warning("Ollama 调用失败: %s", exc)
        llm_trace(trace_id, '⑥', '工具分支(Ollama) HTTP 错误 status=%s url=%s body=%s'
                  % (getattr(exc, 'code', '?'), url[:120], body))
        _set_last_llm_error('HTTP %s: %s' % (getattr(exc, 'code', '?'), body or exc))
        return False, str(exc), None
    except urllib.error.URLError as exc:
        logger.warning("Ollama 调用失败: %s", exc)
        llm_trace(trace_id, '⑥', '工具分支(Ollama) 网络不可达 url=%s 原因=%s（Ollama 未启动？）'
                  % (url[:120], exc.reason))
        _set_last_llm_error('网络不可达: %s' % (getattr(exc, 'reason', exc),))
        return False, str(exc), None
    except (TimeoutError, OSError) as exc:
        logger.warning("Ollama 调用失败: %s", exc)
        llm_trace(trace_id, '⑥', '工具分支(Ollama) 超时/连接异常 url=%s %s: %s'
                  % (url[:120], type(exc).__name__, exc))
        _set_last_llm_error('超时/连接异常: %s' % (exc,))
        return False, str(exc), None
    except Exception as exc:
        logger.warning("Ollama 调用失败: %s", exc)
        llm_trace(trace_id, '⑥', '工具分支(Ollama) 异常 %s: %s' % (type(exc).__name__, str(exc)[:160]))
        _set_last_llm_error('%s: %s' % (type(exc).__name__, exc))
        return False, str(exc), None

    tool_calls = _should_emit_tool_calls(result)
    if tool_calls:
        return True, None, tool_calls

    message = result.get("message") or {}
    content = message.get("content")
    return True, content, None


def _round_openai_compat(messages, tools, model_override, trace_id=None):
    """一轮 OpenAI 兼容调用，返回 (ok, text_or_None, tool_calls_or_None)

    ⚠ 这条是**工具调用**分支（带 tools 时走这里，和 ai_engine.call_llm 是两条路）。
    2026-10-05：用户 NAS 日志里那两行
        LLM 调用失败: HTTP Error 404: Not Found
        LLM 请求失败: HTTP Error 404: Not Found
    就是从这出去的 —— 而当时埋的 trace 在**另一条路**上，所以这两行里
    既没有地址、也没有状态码含义（404 到底是 base_url 拼错还是模型不存在？看不出来）。
    这里补上同样的逐跳留痕，与 ai_client/ai_engine 共用同一个 trace id。
    """
    from ai_engine import (
        LLM,
        PROVIDER_TEMPLATES,
        _effective_timeout,
        decrypt_api_key,
        llm_trace,
        _set_last_llm_error,
    )
    provider_id = LLM.get("provider", "")
    template = PROVIDER_TEMPLATES.get(provider_id, {})

    url = LLM.get("api_url") or template.get("api_url", "")
    model = model_override or LLM.get("model") or template.get("default_model", "")

    parsed = urllib.parse.urlparse(url)
    if parsed.hostname not in ("localhost", "127.0.0.1", "::1") and parsed.scheme != "https":
        logger.warning("非本地 LLM 必须使用 HTTPS: %s", url)
        llm_trace(trace_id, '⑤', '工具分支拦截：非本地地址必须 HTTPS，已拒发 url=%s' % url[:120])
        return False, "non-local url", None

    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": LLM.get("temperature") or 0.7,
        "max_tokens": LLM.get("max_tokens") or template.get("max_output_tokens", 1024),
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    api_key_plain = decrypt_api_key(LLM.get("api_key", ""))
    auth_header = template.get("auth_header", "Authorization")
    auth_format = template.get("auth_format", "Bearer {api_key}")
    headers = {"Content-Type": "application/json"}
    if auth_header and auth_format:
        headers[auth_header] = auth_format.format(api_key=api_key_plain)
    headers.update(template.get("extra_headers", {}) or {})

    llm_trace(trace_id, '⑤',
              '工具分支 POST %s model=%s timeout=%ss 轮次消息=%d'
              % (url[:120], model, _effective_timeout(), len(messages)))
    try:
        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers)
        with utils.urlopen_bound(req, timeout=_effective_timeout()) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # ⚠ 关键：把状态码与响应体贴出来。404 最常见的两种成因——
        #   ① api_url 写错（少了/多了 /v1）；② 模型名在该厂商不存在。
        body = ''
        try:
            body = exc.read().decode('utf-8', 'replace')[:200]
        except Exception:
            pass
        logger.warning("LLM 调用失败: %s", exc)
        llm_trace(trace_id, '⑥',
                  '工具分支 HTTP 错误 status=%s url=%s body=%s'
                  % (getattr(exc, 'code', '?'), url[:120], body))
        # body 里才有真原因（"Model Not Exist" / "Authentication Fails"），别只留一行 warning
        _set_last_llm_error('HTTP %s: %s' % (getattr(exc, 'code', '?'), body or exc))
        return False, str(exc), None
    except urllib.error.URLError as exc:
        logger.warning("LLM 调用失败: %s", exc)
        llm_trace(trace_id, '⑥',
                  '工具分支 网络不可达 url=%s 原因=%s（NAS 能否访问外网？）' % (url[:120], exc.reason))
        _set_last_llm_error('网络不可达: %s' % (getattr(exc, 'reason', exc),))
        return False, str(exc), None
    except (TimeoutError, OSError) as exc:
        logger.warning("LLM 调用失败: %s", exc)
        llm_trace(trace_id, '⑥',
                  '工具分支 超时/连接异常 url=%s 耗时上限=%ss %s: %s'
                  % (url[:120], _effective_timeout(), type(exc).__name__, exc))
        _set_last_llm_error('超时/连接异常: %s' % (exc,))
        return False, str(exc), None
    except Exception as exc:
        logger.warning("LLM 调用失败: %s", exc)
        llm_trace(trace_id, '⑥', '工具分支 异常 %s: %s' % (type(exc).__name__, str(exc)[:160]))
        _set_last_llm_error('%s: %s' % (type(exc).__name__, exc))
        return False, str(exc), None

    llm_trace(trace_id, '⑥', '工具分支 HTTP 200 已返回，解析 choices')

    tool_calls = _should_emit_tool_calls(result)
    if tool_calls:
        return True, None, tool_calls

    choices = result.get("choices") or []
    content = ""
    if choices:
        content = ((choices[0].get("message") or {}).get("content")) or ""
    return True, content, None
