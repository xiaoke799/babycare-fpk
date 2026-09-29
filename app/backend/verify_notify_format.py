#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xiaoke799
"""推送报文格式自检：微信/钉钉/飞书/Bark/PushPlus 逐项断言。

跑法：$MIMO_PYTHON app/backend/verify_notify_format.py
"""
import os
import sys

BACKEND = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BACKEND)

from notifier.multi_platform_notifier import (  # noqa: E402
    MultiPlatformNotifier,
    format_plain,
    format_qywx_text,
    format_bold_title_md,
    format_lark_md,
    format_html,
    _strip_md,
    _full_title,
)

_passed = 0
_failed = 0


def check(name, cond, extra=""):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  [OK]   {name}")
    else:
        _failed += 1
        print(f"  [FAIL] {name}" + (f"  -> {extra}" if extra else ""))


print("\n=== 1. 文案整形 ===")
check("去 markdown 标题符号", "###" not in _strip_md("### 标题\n正文"))
check("保留加粗内容", "标题" in _strip_md("**标题**\n正文"))
check("链接变成文字", "点这里" in _strip_md("[点这里](https://x.example)") and "https://" not in _strip_md("[点这里](https://x.example)"))
check("列表符号变成 ·", "· 牛奶" in _strip_md("- 牛奶"))
check("标题拼接", _full_title("育儿宝", "喂奶提醒") == "育儿宝 · 喂奶提醒")

print("\n=== 2. 企业微信 text（微信可读） ===")
n = MultiPlatformNotifier(title_prefix="育儿宝")
# 直接检查将要发送的 payload 构造逻辑
text = format_qywx_text("育儿宝", "喂奶提醒", "🍼 小满 已经 3 小时没有喂奶了")
check("text 含标题", "喂奶提醒" in text, text)
check("text 无 ###", "###" not in text, text)
check("text 无 markdown 星号包标题", "**" not in text, text)
long_text = format_qywx_text("育儿宝", "长文", "字" * 5000)
check("超长 text 按 UTF-8 截断", len(long_text.encode()) <= 2100, len(long_text.encode()))
check("截断后仍是合法 UTF-8", long_text.endswith("…") or len(long_text.encode()) <= 2000)

# 发送侧 payload：用假 URL 拦住 HTTP，抓 body
captured = {}
orig = MultiPlatformNotifier._http_post

def fake_post(self, url, data, headers=None):
    captured["url"] = url
    captured["data"] = data
    # 各渠道成功字段不同：企业微信/钉钉 errcode，飞书 code，Bark/PushPlus code=200
    if "qyapi.weixin" in url or "dingtalk" in url:
        return True, {"errcode": 0, "errmsg": "ok"}
    if "feishu" in url:
        return True, {"code": 0, "msg": "success"}
    return True, {"code": 200, "message": "ok", "msg": "ok"}

MultiPlatformNotifier._http_post = fake_post
try:
    w = MultiPlatformNotifier(wechat_webhook_url="https://qyapi.weixin.qq.com/x", title_prefix="育儿宝")
    r = w._send_wechat("喂奶提醒", "小满该喂奶了")
    check("wechat 调用成功", r.success, r.error)
    check("wechat msgtype=text", captured["data"].get("msgtype") == "text", captured["data"])
    check("wechat 走 text 字段", "text" in captured["data"] and "markdown" not in captured["data"])
    body = captured["data"]["text"]["content"]
    check("wechat 正文无 ###", "###" not in body, body)

    d = MultiPlatformNotifier(dingtalk_webhook_url="https://oapi.dingtalk.com/robot/send?access_token=x", title_prefix="育儿宝")
    r = d._send_dingtalk("喂奶提醒", "### 原标题\n正文一行")
    check("dingtalk 成功", r.success, r.error)
    md = captured["data"]["markdown"]
    check("dingtalk 有 title 字段", bool(md.get("title")), md)
    check("dingtalk 正文标题加粗", md["text"].startswith("**"), md["text"])
    check("dingtalk 正文无 ###", "###" not in md["text"], md["text"])

    f = MultiPlatformNotifier(feishu_webhook_url="https://open.feishu.cn/x", title_prefix="育儿宝")
    r = f._send_feishu("喂奶提醒", "### 标题\n- 列表项")
    check("feishu 成功", r.success, r.error)
    card_text = captured["data"]["card"]["elements"][0]["text"]["content"]
    check("feishu lark_md 无 ###", "###" not in card_text, card_text)
    check("feishu 有卡片头", "喂奶提醒" in captured["data"]["card"]["header"]["title"]["content"])

    b = MultiPlatformNotifier(bark_url="https://api.day.app/KEY/old/title/path", title_prefix="育儿宝")
    r = b._send_bark("喂奶提醒", "**加粗**\n- 项")
    check("bark 成功", r.success, r.error)
    check("bark URL 归一到 key", captured["url"] in ("https://api.day.app/KEY", "https://api.day.app/KEY/"), captured["url"])
    check("bark body 无 markdown 星号", "**" not in captured["data"]["body"], captured["data"])
    check("bark body 无列表横线", not captured["data"]["body"].lstrip().startswith("-"), captured["data"]["body"])

    p = MultiPlatformNotifier(pushplus_token="tok", title_prefix="育儿宝")
    r = p._send_pushplus("喂奶提醒", "内容一行\n第二行")
    check("pushplus 成功", r.success, r.error)
    check("pushplus template=html", captured["data"].get("template") == "html", captured["data"])
    check("pushplus content 是 html", "<b>" in captured["data"]["content"] and "<br>" in captured["data"]["content"], captured["data"]["content"])
    check("pushplus content 无裸 ###", "###" not in captured["data"]["content"])
finally:
    MultiPlatformNotifier._http_post = orig

print("\n=== 3. Bark URL 归一 ===")
norm = MultiPlatformNotifier._normalize_bark_url
check("纯 key", norm("https://api.day.app/abc123") == "https://api.day.app/abc123", norm("https://api.day.app/abc123"))
check("GET 形态收成 key", norm("https://api.day.app/abc123/标题/正文") == "https://api.day.app/abc123", norm("https://api.day.app/abc123/标题/正文"))
check("保留 /push", norm("https://api.day.app/push") == "https://api.day.app/push", norm("https://api.day.app/push"))
check("去掉末尾斜杠", norm("https://api.day.app/abc123/") == "https://api.day.app/abc123", norm("https://api.day.app/abc123/"))

print("\n=== 4. 边界 ===")
check("空内容只有标题", format_plain("育儿宝", "测试", "") == "育儿宝 · 测试", format_plain("育儿宝", "测试", ""))
check("html 转义", "&lt;" in format_html("p", "t", "<script>") or "&lt;script&gt;" in format_html("p", "t", "<script>"), format_html("p", "t", "<script>"))

print("\n" + "=" * 50)
print(f"通过 {_passed} 项，失败 {_failed} 项")
print("=" * 50)
sys.exit(1 if _failed else 0)
