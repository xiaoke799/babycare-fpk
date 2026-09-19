#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xiaoke799
"""DELETE 路由「假成功」静态扫描。

抓的形状：
    db.execute("DELETE FROM t WHERE id = ?", (rid,))
    db.commit()
    return jsonify({"success": True, "message": "已删除"})     # ← 没看 rowcount
删不存在的 id / 不属于该宝宝的 id 时，接口仍回 200 + success=True，
前端弹「已删除」但库里什么都没变 —— 不报错、不写日志的静默缺陷。

**本脚本有假阳性，必须人工确认再改**（2026-09-19 实测 41 条 DELETE 里报 20 条，
其中 9 条是误报）。以下写法是**正确的**，脚本认不出来，要人工排除：
  - 先 SELECT 判存在再删（photos / knowledge / wiki / recipes）
  - 先 os.path.isfile 判存在（backup）
  - 批量清空（AI 聊天历史 / 推送历史）—— 清空空的也算成功是合理的
  - 幂等操作（取消收藏）

配套的动态验证见 app/backend/verify_delete_guard.py（85 项，是真门禁）。

用法：python .workbuddy/scripts/check_delete_guard.py
"""
import glob
import io
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
BE = os.path.join(ROOT, "app", "backend")

rows = []
for p in sorted(glob.glob(os.path.join(BE, "**", "*.py"), recursive=True)):
    if os.sep + "vendor" + os.sep in p or os.sep + "__pycache__" + os.sep in p:
        continue
    src = io.open(p, encoding="utf-8", errors="replace").read()
    for ch in re.split(r"\n(?=@\w+\.route\()", src):
        head = ch[:500]
        if "methods=" not in head:
            continue
        deco = head.split("\ndef ")[0]
        if "DELETE" not in deco:
            continue
        url_m = re.search(r"[\"'](/[^\"']*)[\"']", deco)
        fn_m = re.search(r"\ndef\s+(\w+)\(", ch)
        if not url_m or not fn_m:
            continue
        body = ch[fn_m.start():]
        nxt = body.find("\n@")
        if nxt > 0:
            body = body[:nxt]
        if "DELETE FROM" not in body or "rowcount" in body:
            continue
        rows.append((os.path.basename(p), re.sub(r"<[^>]+>", "*", url_m.group(1)),
                     fn_m.group(1)))

print("【嫌疑】有 DELETE FROM 但没查 rowcount（需人工确认，多为误报）%d 条：" % len(rows))
for f, u, fn in rows:
    print("   %-20s %-46s %s" % (f, u, fn))
if not rows:
    print("   （无）")
print()
print("提示：动态门禁请跑 app/backend/verify_delete_guard.py（删不存在的 id 必须 404）")
