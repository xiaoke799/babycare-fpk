#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xiaoke799
"""
AI 客户端韧性层。
为 LLM 调用提供：重试、熔断、缓存、超时控制。
与 ai_engine.py 的规则引擎互补：本模块负责"怎么调"，ai_engine.py 负责"怎么答"。
"""

import json
import logging
import time
import hashlib
import threading
import functools
from collections import OrderedDict
from typing import Optional, Tuple, Dict, Any

logger = logging.getLogger(__name__)


# ==================== LRU 缓存 ====================

class LRUCache:
    """线程安全的 LRU 缓存，用于缓存 LLM 回复"""

    def __init__(self, max_size: int = 100, ttl_seconds: int = 3600):
        self._cache: OrderedDict = OrderedDict()
        self._max_size = max_size
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            if key not in self._cache:
                self._misses += 1
                return None
            value, expire_at = self._cache[key]
            if time.time() > expire_at:
                del self._cache[key]
                self._misses += 1
                return None
            # 移到末尾（最近使用）
            self._cache.move_to_end(key)
            self._hits += 1
            return value

    def put(self, key: str, value: Any) -> None:
        with self._lock:
            if key in self._cache:
                del self._cache[key]
            elif len(self._cache) >= self._max_size:
                self._cache.popitem(last=False)  # 淘汰最久未用
            self._cache[key] = (value, time.time() + self._ttl)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

    @property
    def stats(self) -> dict:
        with self._lock:
            total = self._hits + self._misses
            return {
                "size": len(self._cache),
                "max_size": self._max_size,
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": round(self._hits / total, 3) if total > 0 else 0,
            }


# ==================== 熔断器 ====================

class CircuitBreaker:
    """
    熔断器（三态：闭合 → 断开 → 半开）
    
    - 闭合态：正常调用，失败计数达阈值转断开
    - 断开态：快速失败，超时后半开试探
    - 半开态：允许一次试探调用，成功转闭合，失败回断开
    """

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        success_threshold: int = 2,
    ):
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._success_threshold = success_threshold
        self._state = self.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time = 0.0
        self._lock = threading.Lock()

    @property
    def state(self) -> str:
        with self._lock:
            # 检查是否该从断开转半开
            if self._state == self.OPEN:
                if time.time() - self._last_failure_time >= self._recovery_timeout:
                    self._state = self.HALF_OPEN
                    self._success_count = 0
            return self._state

    def record_success(self) -> None:
        with self._lock:
            if self._state == self.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self._success_threshold:
                    self._state = self.CLOSED
                    self._failure_count = 0
            else:
                self._failure_count = 0

    def record_failure(self) -> None:
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.time()
            if self._state == self.HALF_OPEN:
                self._state = self.OPEN
            elif self._failure_count >= self._failure_threshold:
                self._state = self.OPEN

    def can_execute(self) -> bool:
        s = self.state  # 触发状态检查
        return s != self.OPEN

    @property
    def stats(self) -> dict:
        return {
            "state": self.state,
            "failure_count": self._failure_count,
            "success_count": self._success_count,
        }


# ==================== 重试装饰器 ====================

def retry_with_backoff(
    max_retries: int = 2,
    base_delay: float = 1.0,
    max_delay: float = 10.0,
    retryable_exceptions: tuple = (Exception,),
):
    """
    指数退避重试装饰器。
    
    Args:
        max_retries: 最大重试次数
        base_delay: 基础延迟（秒）
        max_delay: 最大延迟（秒）
        retryable_exceptions: 触发重试的异常类型
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except retryable_exceptions as e:
                    last_exception = e
                    if attempt < max_retries:
                        delay = min(base_delay * (2 ** attempt), max_delay)
                        logger.warning(
                            "%s 第 %d/%d 次重试 (%.1fs): %s",
                            func.__name__, attempt + 1, max_retries, delay, e,
                        )
                        time.sleep(delay)
            raise last_exception
        return wrapper
    return decorator


# ==================== 重试异常 ====================

class LLMCallError(Exception):
    """LLM 调用失败（可重试）。

    ai_engine.call_llm 不会抛异常（内部捕获后返回 (False, None)），
    所以用它把"返回值层面的失败"转成异常，交给退避重试装饰器处理。
    """


# ==================== 缓存键生成 ====================

def _make_cache_key(question: str, baby_context: Optional[str] = None, history: Optional[list] = None, model: Optional[str] = None, provider: Optional[str] = None) -> str:
    """生成缓存键（厂商 + 模型 + 问题 + 上下文 + 最近历史哈希）

    必须带上 provider：不同厂商可能存在同名模型（如 qwen 与自定义），
    否则切换厂商后会命中上一家厂商留下的答案。
    """
    # 包含最近2条历史消息的哈希（避免对话上下文中返回过时答案）
    hist_hash = ""
    if history:
        recent = history[-2:] if len(history) >= 2 else history
        hist_str = json.dumps(recent, ensure_ascii=False, sort_keys=True)
        hist_hash = hashlib.md5(hist_str.encode("utf-8")).hexdigest()[:8]
    raw = f"{provider or ''}|{model or ''}|{question}|{baby_context or ''}|{hist_hash}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


# ==================== 全局实例 ====================

_llm_cache = LRUCache(max_size=200, ttl_seconds=7200)  # 2小时TTL
_circuit_breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=120.0)


# ==================== 带韧性的 LLM 调用 ====================

def call_llm_with_resilience(
    question: str,
    baby_context: Optional[str] = None,
    history: Optional[list] = None,
    use_cache: bool = True,
    model: Optional[str] = None,
    tools: Optional[list] = None,
    execute_tool_fn=None,
    max_tool_rounds: int = 5,
) -> Tuple[bool, Optional[str]]:
    """
    带完整韧性保护的 LLM 调用（支持工具调用 / Function Calling）。
    
    流程：缓存查询 → 熔断检查 → 重试调用 → 结果缓存
    
    Args:
        model: 临时指定本次调用的模型（如 AI 对话页选择），不同模型缓存互不命中
        tools: 工具定义列表（OpenAI function calling 格式）
        execute_tool_fn: (tool_name, args_dict) -> dict 的执行回调
        max_tool_rounds: 工具调用最大循环次数
    
    Returns:
        (success, answer)
    """
    # 1. 缓存命中（仅当无工具调用时才使用缓存，因为工具结果可能受时间影响）
    if use_cache and not tools:
        try:
            from ai_engine import LLM
            _provider = LLM.get('provider') or ''
        except Exception:
            _provider = ''
        cache_key = _make_cache_key(question, baby_context, history, model, _provider)
        cached = _llm_cache.get(cache_key)
        if cached is not None:
            logger.debug("LLM 缓存命中: %s...", question[:30])
            return True, cached

    # 2. 熔断检查
    if not _circuit_breaker.can_execute():
        logger.warning("熔断器断开，跳过 LLM 调用")
        return False, None

    # 3. 实际调用（带重试）
    try:
        if tools:
            # 走工具调用流程（多轮对话，失败不回退到单次调用因为工具调用是明确指定的）
            from ai_engine_tooling import call_llm_with_tools
            from ai_engine import LLM, SYSTEM_PROMPT

            # 构建基础消息
            messages = [{'role': 'system', 'content': LLM.get('system_prompt') or SYSTEM_PROMPT}]
            if baby_context:
                messages.append({'role': 'system', 'content': f'宝宝数据：{baby_context}'})
            if history:
                messages.extend(history)
            messages.append({'role': 'user', 'content': question})

            ok, answer = call_llm_with_tools(
                messages,
                tools,
                model_override=model,
                execute_tool_fn=execute_tool_fn,
                max_rounds=max_tool_rounds,
            )
            if ok and answer:
                _circuit_breaker.record_success()
                return True, answer
            _circuit_breaker.record_failure()
            return False, None
        else:
            ok, answer = _call_llm_with_retry(question, baby_context, history, model)
            if ok:
                _circuit_breaker.record_success()
                # 缓存结果
                if use_cache and answer:
                    _llm_cache.put(cache_key, answer)
                return True, answer
            else:
                _circuit_breaker.record_failure()
                return False, None
    except Exception as e:
        _circuit_breaker.record_failure()
        logger.error("LLM 调用最终失败: %s", e)
        return False, None


@retry_with_backoff(max_retries=1, base_delay=0.5, max_delay=2.0,
                    retryable_exceptions=(LLMCallError,))
def _call_llm_with_retry(
    question: str,
    baby_context: Optional[str] = None,
    history: Optional[list] = None,
    model: Optional[str] = None,
) -> Tuple[bool, Optional[str]]:
    """内部：带重试的 LLM 调用（使用 ai_engine 的 call_llm）

    说明：call_llm 内部会自行捕获异常并返回 (False, None)，不会向外抛，
    因此这里在"未成功"时主动抛 LLMCallError，退避重试才真正生效。
    配置类失败（未选厂商 / 云端未同意外发）重试也没有意义，直接返回。
    """
    from ai_engine import call_llm, LLM

    provider = LLM.get('provider') or ''
    if not provider:
        return False, None
    if provider not in ('ollama', 'lmstudio') and not LLM.get('consent_cloud'):
        return False, None

    ok, answer = call_llm(question, baby_context, history, model_override=model)
    if not ok:
        raise LLMCallError('LLM 调用未成功')
    return True, answer


# ==================== 管理接口 ====================

def get_ai_client_stats() -> dict:
    """获取 AI 客户端运行统计"""
    return {
        "cache": _llm_cache.stats,
        "circuit_breaker": _circuit_breaker.stats,
    }


def clear_ai_cache() -> None:
    """清空 LLM 缓存"""
    _llm_cache.clear()
    logger.info("LLM 缓存已清空")


def reset_circuit_breaker() -> None:
    """手动重置熔断器"""
    global _circuit_breaker
    _circuit_breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=120.0)
    logger.info("熔断器已重置")
