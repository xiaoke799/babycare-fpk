#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xiaoke799

"""
多平台通知器。
支持企业微信、钉钉、飞书、Bark、PushPlus 推送。

格式约定（按「微信里也能正常看」优先）：
- 企业微信机器人：用 text，不用 markdown。markdown 的 ### / 列表在微信插件里
  会显示成字面量或整段空白 —— 2026-09 复核时用户反馈「微信查看不了」。
- PushPlus（落到微信公众号）：template=html，简单 <b>/<br>；markdown 模板
  在公众号侧经常不渲染或被截断。
- 钉钉：markdown（支持较好），标题用 **加粗** 而不是 ###
- 飞书卡片：lark_md 只认 **加粗** / 链接，去掉标题语法
- Bark：纯文本，剥掉所有 markdown 标记
"""

import html
import json
import re
import time
import logging
import hashlib
import urllib.request
import urllib.parse
import urllib.error
import threading
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# PushPlus 固定接口。
# 必须走 https：这里是 POST 明文 token，用 http 等于把凭据裸奔在网络上。
PUSHPLUS_URL = "https://www.pushplus.plus/send"


# ---------- 文案整形：一套标题/正文，按渠道输出各自能渲染的形态 ----------

_MD_STARS = re.compile(r"\*\*(.+?)\*\*", re.S)
_MD_ITALIC = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)")
_MD_HEAD = re.compile(r"^#{1,6}\s*", re.M)
_MD_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_MD_CODE_FENCE = re.compile(r"```[\s\S]*?```")
_MD_INLINE_CODE = re.compile(r"`([^`]+)`")
_MD_LIST_BULLET = re.compile(r"^\s*[-*+]\s+", re.M)
_MD_LIST_NUM = re.compile(r"^\s*\d+[.)]\s+", re.M)
_MD_QUOTE = re.compile(r"^\s*>\s?", re.M)


def _strip_md(text: str) -> str:
    """把 markdown 压成纯文本（Bark / 企业微信 text 用）。"""
    s = text or ""
    s = _MD_CODE_FENCE.sub(lambda m: m.group(0).strip("`").strip(), s)
    s = _MD_LINK.sub(r"\1", s)
    s = _MD_STARS.sub(r"\1", s)
    s = _MD_ITALIC.sub(r"\1", s)
    s = _MD_INLINE_CODE.sub(r"\1", s)
    s = _MD_HEAD.sub("", s)
    s = _MD_LIST_BULLET.sub("· ", s)
    s = _MD_LIST_NUM.sub("", s)
    s = _MD_QUOTE.sub("", s)
    return s.strip()


def _full_title(prefix: str, title: str) -> str:
    prefix = (prefix or "").strip()
    title = (title or "").strip()
    if prefix and title:
        return f"{prefix} · {title}"
    return prefix or title


def format_plain(prefix: str, title: str, content: str) -> str:
    """纯文本：企业微信 text / Bark。"""
    head = _strip_md(_full_title(prefix, title))
    body = _strip_md(content or "")
    if body:
        return f"{head}\n{body}"
    return head


def format_qywx_text(prefix: str, title: str, content: str) -> str:
    """企业微信 text 正文（微信插件可读）。长度 2048 字节上限，超了截断。"""
    text = format_plain(prefix, title, content)
    raw = text.encode("utf-8")
    if len(raw) <= 2000:
        return text
    # 按字节截断，避免切开半个汉字
    cut = raw[:2000]
    while cut and (cut[-1] & 0xC0) == 0x80:
        cut = cut[:-1]
    return cut.decode("utf-8", errors="ignore") + "\n…"


def format_bold_title_md(prefix: str, title: str, content: str) -> str:
    """钉钉 / 企业微信 markdown 友好：标题加粗，正文保留简单 md。"""
    head = _full_title(prefix, title)
    body = (content or "").strip()
    # 去掉内容里原有的 # 标题语法，统一改成加粗
    body = _MD_HEAD.sub("", body)
    return f"**{head}**\n{body}" if body else f"**{head}**"


def format_lark_md(content: str) -> str:
    """飞书 lark_md：只保留 **加粗** 和链接。"""
    s = content or ""
    s = _MD_HEAD.sub("", s)
    s = _MD_CODE_FENCE.sub(lambda m: m.group(0).strip("`").strip(), s)
    s = _MD_INLINE_CODE.sub(r"\1", s)
    s = _MD_LIST_BULLET.sub("· ", s)
    s = _MD_QUOTE.sub("", s)
    return s.strip()


def format_html(prefix: str, title: str, content: str) -> str:
    """PushPlus template=html：微信公众号能稳定渲染的极简 HTML。"""
    head = html.escape(_full_title(prefix, title))
    body = _strip_md(content or "")
    body_html = "<br>".join(html.escape(line) for line in body.splitlines() if line.strip())
    if body_html:
        return f"<b>{head}</b><br>{body_html}"
    return f"<b>{head}</b>"


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

    def _redact(self, text) -> str:
        """把错误信息里的凭据抹掉。

        urlopen 的异常常把整条 URL 带出来（如 `unknown url type: https://qyapi.weixin.qq.com/...key=xxx`），
        而错误文本会写进 push_history 并通过 /api/notifications/history 回给前端 ——
        等于把 webhook key / token 泄露给任何能看到推送历史的用户。
        """
        if not text:
            return ""
        s = str(text)
        for secret in (self.wechat_webhook_url, self.dingtalk_webhook_url,
                       self.feishu_webhook_url, self.bark_url, self.pushplus_token):
            secret = str(secret or "")
            if len(secret) > 8:
                s = s.replace(secret, secret[:4] + "****")
        return s

    def _http_post(self, url: str, data: dict, headers: Optional[dict] = None) -> Tuple[bool, Any]:
        """HTTP POST 请求（错误信息一律先脱敏再返回）"""
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
            return False, self._redact(f"HTTP {e.code}: {error_body}")
        except Exception as e:
            return False, self._redact(str(e))

    def _send_wechat(self, title: str, content: str) -> NotifyResult:
        """企业微信 Webhook 推送。

        用 text 而不是 markdown：企业微信 markdown 的 ### 在微信插件里
        会显示成字面量，部分版本甚至整条空白（用户反馈「微信查看不了」）。
        text 在企业微信 App 和微信插件里都能正常打开。
        """
        if not self.wechat_webhook_url:
            return NotifyResult("wechat", False, "未配置 Webhook URL")

        msg = {
            "msgtype": "text",
            "text": {
                "content": format_qywx_text(self.title_prefix, title, content),
            },
        }
        ok, resp = self._http_post(self.wechat_webhook_url, msg)
        if ok and isinstance(resp, dict):
            # 企业微信返回 {"errcode": 0, "errmsg": "ok"}
            if resp.get("errcode") == 0:
                return NotifyResult("wechat", True, response=resp)
            return NotifyResult("wechat", False, resp.get("errmsg", str(resp)), resp)
        return NotifyResult("wechat", ok, "" if ok else str(resp), resp)

    def _send_dingtalk(self, title: str, content: str) -> NotifyResult:
        """钉钉 Webhook 推送。markdown.title 是通知栏摘要，text 是会话正文。"""
        if not self.dingtalk_webhook_url:
            return NotifyResult("dingtalk", False, "未配置 Webhook URL")

        full_title = _full_title(self.title_prefix, title)
        msg = {
            "msgtype": "markdown",
            "markdown": {
                "title": full_title,
                "text": format_bold_title_md(self.title_prefix, title, content),
            },
        }
        ok, resp = self._http_post(self.dingtalk_webhook_url, msg)
        if ok and isinstance(resp, dict):
            if resp.get("errcode") == 0:
                return NotifyResult("dingtalk", True, response=resp)
            return NotifyResult("dingtalk", False, resp.get("errmsg", str(resp)), resp)
        return NotifyResult("dingtalk", ok, "" if ok else str(resp), resp)

    def _send_feishu(self, title: str, content: str) -> NotifyResult:
        """飞书 Webhook 推送（卡片）。lark_md 不认 # 标题，正文要先洗净。"""
        if not self.feishu_webhook_url:
            return NotifyResult("feishu", False, "未配置 Webhook URL")

        body = format_lark_md(content) or _full_title(self.title_prefix, title)
        msg = {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {"content": _full_title(self.title_prefix, title), "tag": "plain_text"},
                    "template": "blue",
                },
                "elements": [
                    {
                        "tag": "div",
                        "text": {"content": body, "tag": "lark_md"},
                    }
                ],
            },
        }
        ok, resp = self._http_post(self.feishu_webhook_url, msg)
        if ok and isinstance(resp, dict):
            # 飞书返回 {"code": 0, "msg": "success"}；旧版也有 {"StatusCode":0}
            if resp.get("code") == 0 or resp.get("StatusCode") == 0:
                return NotifyResult("feishu", True, response=resp)
            return NotifyResult("feishu", False, resp.get("msg", str(resp)), resp)
        return NotifyResult("feishu", ok, "" if ok else str(resp), resp)

    @staticmethod
    def _normalize_bark_url(raw: str) -> str:
        """把 Bark 地址收成可 POST 的形态。

        用户常见三种填法：
          https://api.day.app/{key}
          https://api.day.app/{key}/{title}/{body}   ← GET 形态，别把标题编进 URL
          https://api.day.app/push                  ← 需在 body 里带 device_key，这里不展开
        只保留到 /{key} 或 /push，多出来的路径段丢掉。
        """
        url = (raw or "").strip().rstrip("/")
        if not url:
            return ""
        try:
            parsed = urllib.parse.urlparse(url)
        except Exception:
            return url
        parts = [p for p in parsed.path.split("/") if p]
        if not parts:
            return url
        head = parts[0]
        if head == "push":
            path = "/push"
        else:
            path = "/" + head
        return urllib.parse.urlunparse(parsed._replace(path=path))

    def _send_bark(self, title: str, content: str) -> NotifyResult:
        """Bark (iOS) 推送。正文必须是纯文本，markdown 会在通知里露出符号。"""
        if not self.bark_url:
            return NotifyResult("bark", False, "未配置 Bark URL")

        try:
            url = self._normalize_bark_url(self.bark_url)
            msg = {
                "title": _full_title(self.title_prefix, title),
                "body": format_plain("", "", content),
                "group": self.title_prefix or "育儿宝",
            }
            ok, resp = self._http_post(url, msg)
            if ok and isinstance(resp, dict):
                if resp.get("code") == 200:
                    return NotifyResult("bark", True, response=resp)
                return NotifyResult("bark", False, resp.get("message", str(resp)), resp)
            return NotifyResult("bark", ok, "" if ok else str(resp), resp)
        except Exception as e:
            return NotifyResult("bark", False, self._redact(str(e)))

    def _send_pushplus(self, title: str, content: str) -> NotifyResult:
        """PushPlus → 微信公众号。用 html 模板，markdown 在公众号侧经常空白/截断。"""
        if not self.pushplus_token:
            return NotifyResult("pushplus", False, "未配置 Token")

        msg = {
            "token": self.pushplus_token,
            "title": _full_title(self.title_prefix, title),
            "content": format_html(self.title_prefix, title, content),
            "template": "html",
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
                logger.error("[Notifier] %s 推送异常: %s", name, self._redact(str(e)))
                results.append(NotifyResult(name, False, self._redact(str(e))))

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
