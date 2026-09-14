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
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

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
    from ai_engine import LLM, PROVIDER_TEMPLATES, _effective_timeout
    provider = LLM.get("provider", "")

    current_messages: List[Dict[str, Any]] = list(messages)

    for _round in range(max_rounds):
        if provider == "ollama":
            ok, payload_or_text, tool_calls = _round_ollama(current_messages, tools, model_override)
        else:
            ok, payload_or_text, tool_calls = _round_openai_compat(current_messages, tools, model_override)

        if not ok:
            logger.warning("LLM 请求失败: %s", payload_or_text)
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
            return True, final_text
        return True, None

    logger.warning("工具调用超过最大轮次 %d，返回空", max_rounds)
    return True, None


def _round_ollama(messages, tools, model_override):
    """一轮 Ollama 调用，返回 (ok, text_or_None, tool_calls_or_None)"""
    from ai_engine import LLM, PROVIDER_TEMPLATES, _effective_timeout
    url = LLM.get("api_url") or PROVIDER_TEMPLATES["ollama"]["api_url"]
    parsed = urllib.parse.urlparse(url)
    if parsed.hostname not in ("localhost", "127.0.0.1", "::1") and parsed.scheme != "https":
        logger.warning("非本地 Ollama 必须使用 HTTPS")
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

    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=_effective_timeout()) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        logger.warning("Ollama 调用失败: %s", exc)
        return False, str(exc), None

    tool_calls = _should_emit_tool_calls(result)
    if tool_calls:
        return True, None, tool_calls

    message = result.get("message") or {}
    content = message.get("content")
    return True, content, None


def _round_openai_compat(messages, tools, model_override):
    """一轮 OpenAI 兼容调用，返回 (ok, text_or_None, tool_calls_or_None)"""
    from ai_engine import (
        LLM,
        PROVIDER_TEMPLATES,
        _effective_timeout,
        decrypt_api_key,
    )
    provider_id = LLM.get("provider", "")
    template = PROVIDER_TEMPLATES.get(provider_id, {})

    url = LLM.get("api_url") or template.get("api_url", "")
    model = model_override or LLM.get("model") or template.get("default_model", "")

    parsed = urllib.parse.urlparse(url)
    if parsed.hostname not in ("localhost", "127.0.0.1", "::1") and parsed.scheme != "https":
        logger.warning("非本地 LLM 必须使用 HTTPS: %s", url)
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

    try:
        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers)
        with urllib.request.urlopen(req, timeout=_effective_timeout()) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        logger.warning("LLM 调用失败: %s", exc)
        return False, str(exc), None

    tool_calls = _should_emit_tool_calls(result)
    if tool_calls:
        return True, None, tool_calls

    choices = result.get("choices") or []
    content = ""
    if choices:
        content = ((choices[0].get("message") or {}).get("content")) or ""
    return True, content, None
