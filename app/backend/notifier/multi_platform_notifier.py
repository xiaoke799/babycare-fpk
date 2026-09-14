#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xiaoke799

"""
多平台通知器。
支持企业微信、钉钉、飞书、Bark、PushPlus 推送。
"""

import json
import time
import logging
import hashlib
import urllib.request
import urllib.parse
import urllib.error
import threading
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# PushPlus 固定接口
PUSHPLUS_URL = "http://www.pushplus.plus/send"


@dataclass
class NotifyResult:
    """单个渠道推送结果"""
    channel: str
    success: bool
    error: str = ""
    response: Any = None


class MultiPlatformNotifier:
    """多平台通知器"""

    def __init__(
        self,
        wechat_webhook_url: str = "",
        dingtalk_webhook_url: str = "",
        feishu_webhook_url: str = "",
        bark_url: str = "",
        pushplus_token: str = "",
        pushplus_topic: str = "",
        title_prefix: str = "育儿宝",
        timeout: int = 10,
    ):
        self.wechat_webhook_url = wechat_webhook_url
        self.dingtalk_webhook_url = dingtalk_webhook_url
        self.feishu_webhook_url = feishu_webhook_url
        self.bark_url = bark_url
        self.pushplus_token = pushplus_token
        self.pushplus_topic = pushplus_topic
        self.title_prefix = title_prefix
        self.timeout = timeout

        # 去重缓存: {hash: timestamp}
        self._dedup_cache: Dict[str, float] = {}
        self._dedup_window = 300  # 5 分钟去重窗口
        self._dedup_lock = threading.Lock()

    def _make_dedup_key(self, title: str, content: str) -> str:
        """生成去重 key"""
        raw = f"{title}:{content}"
        return hashlib.md5(raw.encode()).hexdigest()

    def is_duplicate(self, title: str, content: str) -> bool:
        """检查是否在去重窗口内"""
        key = self._make_dedup_key(title, content)
        now = time.time()
        with self._dedup_lock:
            # 清理过期记录
            expired = [k for k, v in self._dedup_cache.items() if now - v > self._dedup_window]
            for k in expired:
                self._dedup_cache.pop(k, None)
            # 检查是否重复
            if key in self._dedup_cache:
                return True
            self._dedup_cache[key] = now
            return False

    def _http_post(self, url: str, data: dict, headers: Optional[dict] = None) -> Tuple[bool, Any]:
        """HTTP POST 请求"""
        try:
            body = json.dumps(data).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=body,
                headers=headers or {"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                resp_text = resp.read().decode("utf-8")
                try:
                    return True, json.loads(resp_text)
                except json.JSONDecodeError:
                    return True, resp_text
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8", errors="replace") if e.fp else ""
            return False, f"HTTP {e.code}: {error_body}"
        except Exception as e:
            return False, str(e)

    def _send_wechat(self, title: str, content: str) -> NotifyResult:
        """企业微信 Webhook 推送"""
        if not self.wechat_webhook_url:
            return NotifyResult("wechat", False, "未配置 Webhook URL")

        # 企业微信支持 markdown 和 text
        msg = {
            "msgtype": "markdown",
            "markdown": {
                "content": f"### {self.title_prefix} - {title}\n{content}"
            }
        }
        ok, resp = self._http_post(self.wechat_webhook_url, msg)
        if ok and isinstance(resp, dict):
            # 企业微信返回 {"errcode": 0, "errmsg": "ok"}
            if resp.get("errcode") == 0:
                return NotifyResult("wechat", True, response=resp)
            return NotifyResult("wechat", False, resp.get("errmsg", str(resp)), resp)
        return NotifyResult("wechat", ok, "" if ok else str(resp), resp)

    def _send_dingtalk(self, title: str, content: str) -> NotifyResult:
        """钉钉 Webhook 推送"""
        if not self.dingtalk_webhook_url:
            return NotifyResult("dingtalk", False, "未配置 Webhook URL")

        msg = {
            "msgtype": "markdown",
            "markdown": {
                "title": f"{self.title_prefix} - {title}",
                "text": f"### {self.title_prefix} - {title}\n{content}"
            }
        }
        ok, resp = self._http_post(self.dingtalk_webhook_url, msg)
        if ok and isinstance(resp, dict):
            if resp.get("errcode") == 0:
                return NotifyResult("dingtalk", True, response=resp)
            return NotifyResult("dingtalk", False, resp.get("errmsg", str(resp)), resp)
        return NotifyResult("dingtalk", ok, "" if ok else str(resp), resp)

    def _send_feishu(self, title: str, content: str) -> NotifyResult:
        """飞书 Webhook 推送"""
        if not self.feishu_webhook_url:
            return NotifyResult("feishu", False, "未配置 Webhook URL")

        msg = {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {"content": f"{self.title_prefix} - {title}", "tag": "plain_text"},
                    "template": "blue"
                },
                "elements": [
                    {
                        "tag": "div",
                        "text": {"content": content, "tag": "lark_md"}
                    }
                ]
            }
        }
        ok, resp = self._http_post(self.feishu_webhook_url, msg)
        if ok and isinstance(resp, dict):
            # 飞书返回 {"code": 0, "msg": "success"}
            if resp.get("code") == 0:
                return NotifyResult("feishu", True, response=resp)
            return NotifyResult("feishu", False, resp.get("msg", str(resp)), resp)
        return NotifyResult("feishu", ok, "" if ok else str(resp), resp)

    def _send_bark(self, title: str, content: str) -> NotifyResult:
        """Bark (iOS) 推送"""
        if not self.bark_url:
            return NotifyResult("bark", False, "未配置 Bark URL")

        # Bark URL 格式: https://api.day.app/{key}/{title}/{body}
        # 或者 POST 到 https://api.day.app/push
        try:
            # 尝试 POST 方式
            msg = {
                "title": f"{self.title_prefix} - {title}",
                "body": content,
                "group": "育儿宝",
            }
            ok, resp = self._http_post(self.bark_url, msg)
            if ok and isinstance(resp, dict):
                if resp.get("code") == 200:
                    return NotifyResult("bark", True, response=resp)
                return NotifyResult("bark", False, resp.get("message", str(resp)), resp)
            return NotifyResult("bark", ok, "" if ok else str(resp), resp)
        except Exception as e:
            return NotifyResult("bark", False, str(e))

    def _send_pushplus(self, title: str, content: str) -> NotifyResult:
        """PushPlus 推送"""
        if not self.pushplus_token:
            return NotifyResult("pushplus", False, "未配置 Token")

        msg = {
            "token": self.pushplus_token,
            "title": f"{self.title_prefix} - {title}",
            "content": content,
            "template": "markdown",
        }
        if self.pushplus_topic:
            msg["topic"] = self.pushplus_topic

        ok, resp = self._http_post(PUSHPLUS_URL, msg)
        if ok and isinstance(resp, dict):
            if resp.get("code") == 200:
                return NotifyResult("pushplus", True, response=resp)
            return NotifyResult("pushplus", False, resp.get("msg", str(resp)), resp)
        return NotifyResult("pushplus", ok, "" if ok else str(resp), resp)

    def send(
        self,
        title: str,
        content: str,
        channels: Optional[List[str]] = None,
        skip_dedup: bool = False,
    ) -> Tuple[bool, List[NotifyResult]]:
        """
        发送通知到所有已配置渠道。

        Args:
            title: 标题
            content: 内容
            channels: 指定渠道列表，None 表示所有已配置渠道
            skip_dedup: 是否跳过去重检查

        Returns:
            (是否有任一成功, 各渠道结果列表)
        """
        # 去重检查
        if not skip_dedup and self.is_duplicate(title, content):
            logger.info("[Notifier] 重复消息，跳过: %s", title)
            return True, [NotifyResult("dedup", True, "重复消息已跳过")]

        # 确定要发送的渠道
        channel_map = {
            "wechat": (self._send_wechat, self.wechat_webhook_url),
            "dingtalk": (self._send_dingtalk, self.dingtalk_webhook_url),
            "feishu": (self._send_feishu, self.feishu_webhook_url),
            "bark": (self._send_bark, self.bark_url),
            "pushplus": (self._send_pushplus, self.pushplus_token),
        }

        results: List[NotifyResult] = []
        any_success = False

        for name, (sender, configured) in channel_map.items():
            if channels and name not in channels:
                continue
            if not configured:
                continue
            try:
                result = sender(title, content)
                results.append(result)
                if result.success:
                    any_success = True
                    logger.info("[Notifier] %s 推送成功: %s", name, title)
                else:
                    logger.warning("[Notifier] %s 推送失败: %s - %s", name, title, result.error)
            except Exception as e:
                logger.error("[Notifier] %s 推送异常: %s", name, e)
                results.append(NotifyResult(name, False, str(e)))

        return any_success, results

    def get_active_channels(self) -> Dict[str, bool]:
        """获取各渠道配置状态"""
        return {
            "wechat": bool(self.wechat_webhook_url),
            "dingtalk": bool(self.dingtalk_webhook_url),
            "feishu": bool(self.feishu_webhook_url),
            "bark": bool(self.bark_url),
            "pushplus": bool(self.pushplus_token),
        }

    def cleanup_cache(self):
        """清理去重缓存"""
        now = time.time()
        with self._dedup_lock:
            expired = [k for k, v in self._dedup_cache.items() if now - v > self._dedup_window]
            for k in expired:
                self._dedup_cache.pop(k, None)
