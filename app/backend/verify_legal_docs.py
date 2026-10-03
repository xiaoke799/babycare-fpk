#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""法律文本同步门禁：frontend/legal/*.html 必须与源 .md 一致。

为什么需要这个门禁：legal/ 下的 HTML 是**生成物**（由 .md 转换而来）。
改了 PRIVACY.md / USER_AGREEMENT.md 却忘了重新生成，页面就会停留在旧版本 ——
而这正是最不能出错的地方（用户看到的协议必须和实际生效的一致）。
静态检查看不出来，页面也能正常打开，所以必须用门禁钉死。

HTML 顶部注释里记了源文件的 sha256，这里比对该哈希。

用法：
  python verify_legal_docs.py          # 只校验，失败返回 1
  python verify_legal_docs.py --fix    # 校验并重新生成
"""
from __future__ import annotations

import hashlib
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # app/
ROOT = os.path.dirname(BASE)                                        # 仓库根
LEGAL_DIR = os.path.join(BASE, "frontend", "legal")

PAIRS = [
    (os.path.join(ROOT, "PRIVACY.md"), os.path.join(LEGAL_DIR, "privacy.html"),
     "隐私政策 / Privacy Policy", "育儿宝"),
    (os.path.join(ROOT, "USER_AGREEMENT.md"), os.path.join(LEGAL_DIR, "terms.html"),
     "用户协议 / User Agreement", "育儿宝"),
]

sys.path.insert(0, os.path.join(BASE, "backend"))

fails: list[str] = []


def chk(ok: bool, label: str, extra: str = "") -> None:
    print(("  [OK]   " if ok else "  [FAIL] ") + label + (f"  {extra}" if extra else ""))
    if not ok:
        fails.append(label)


def sha(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def build(md_path: str, out_path: str, title: str, site: str) -> None:
    """md -> html。转换规则与初次生成时保持一致。"""
    import html as _html
    import io

    def inline(text):
        out = _html.escape(text)
        out = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", out)
        out = re.sub(r"`(.+?)`", r"<code>\1</code>", out)
        out = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)",
                     r'<a href="\2" target="_blank" rel="noopener">\1</a>', out)
        return out

    md = io.open(md_path, encoding="utf-8").read()
    lines = re.sub(r"^#\s+.*\n", "", md, count=1).split("\n")
    out, i, n = [], 0, len(lines)
    while i < n:
        s = lines[i].strip()
        if not s:
            i += 1
            continue
        if re.fullmatch(r"-{3,}|\*{3,}|_{3,}", s):
            out.append("<hr>")
            i += 1
            continue
        m = re.match(r"^(#{1,5})\s+(.*)$", s)
        if m:
            lv = len(m.group(1))
            out.append(f"<h{lv}>{inline(m.group(2))}</h{lv}>")
            i += 1
            continue
        if "|" in s and i + 1 < n and re.fullmatch(r"\|[\s:|-]+\|", lines[i + 1].strip()):
            head = [c.strip() for c in s.strip("|").split("|")]
            i += 2
            body = []
            while i < n and "|" in lines[i] and lines[i].strip():
                body.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            out.append("<table><thead><tr>"
                       + "".join(f"<th>{inline(c)}</th>" for c in head)
                       + "</tr></thead><tbody>")
            for r in body:
                out.append("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>")
            out.append("</tbody></table>")
            continue
        if s.startswith(">"):
            buf = []
            while i < n and lines[i].strip().startswith(">"):
                buf.append(lines[i].strip().lstrip(">").strip())
                i += 1
            out.append("<blockquote><p>" + inline(" ".join(buf)) + "</p></blockquote>")
            continue
        if re.match(r"^([-*+]|\d+\.)\s+", s):
            ordered = bool(re.match(r"^\d+\.\s", s))
            items, i = [], i
            while i < n:
                m2 = re.match(r"^([-*+]|\d+\.)\s+(.*)$", lines[i].strip())
                if not m2:
                    break
                item = m2.group(2)
                i += 1
                while i < n and lines[i].strip() and not re.match(r"^([-*+]|\d+\.)\s+", lines[i].strip()) \
                        and not re.match(r"^#{1,5}\s", lines[i].strip()) \
                        and not lines[i].strip().startswith(">"):
                    item += " " + lines[i].strip()
                    i += 1
                items.append(item)
            tag = "ol" if ordered else "ul"
            out.append(f"<{tag}>" + "".join(f"<li>{inline(x)}</li>" for x in items) + f"</{tag}>")
            continue
        buf = []
        while i < n and lines[i].strip() \
                and not re.match(r"^(#{1,5}\s|([-*+]|\d+\.)\s|>|-{3,}$)", lines[i].strip()) \
                and not ("|" in lines[i] and i + 1 < n
                         and re.fullmatch(r"\|[\s:|-]+\|", lines[i + 1].strip())):
            buf.append(lines[i].strip())
            i += 1
        out.append("<p>" + inline(" ".join(buf)) + "</p>" if buf else "")
        if not buf:
            i += 1

    doc = "\n".join(out)
    css_path = os.path.join(BASE, "backend", "verify_legal_docs.py")
    page = f"""<!DOCTYPE html>
<!-- 本文件由 verify_legal_docs.py 从 {os.path.basename(md_path)} 自动生成，请勿手改。
     源文件 sha256: {sha(md_path)} -->
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_html.escape(title)} · {site}</title>
<style>{CSS_TEXT}</style>
</head>
<body>
<div class="wrap">
  <a class="back" id="backBtn" href="../index.html">← 返回育儿宝</a>
  <div class="doc">
    <h1>{_html.escape(title)}</h1>
    {doc}
  </div>
</div>
<script>
document.addEventListener('click', function(e){{
  if(e.target&&e.target.id==='backBtn'){{ history.length>1?history.back():(location.href='../index.html'); }}
}});
</script>
</body>
</html>
"""
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(page)


# 页面样式（与首次生成时一致，避免 --fix 后外观变化）
CSS_TEXT = """
:root{--ink:#2d3436;--sub:#5f6b6d;--line:#ffe0d2;--accent:#d15a7a;--bg:#fffdfb}
*{box-sizing:border-box}
body{margin:0;padding:22px 18px 60px;background:var(--bg);color:var(--ink);
  font-family:"Noto Sans SC","PingFang SC","Microsoft YaHei",system-ui,sans-serif;
  font-size:15px;line-height:1.85;-webkit-text-size-adjust:100%}
.wrap{max-width:820px;margin:0 auto}
.doc{background:#fff;border:1px solid var(--line);border-radius:20px;padding:26px 24px}
h1{font-size:23px;margin:0 0 18px;padding-bottom:14px;border-bottom:2px solid var(--line);line-height:1.4}
h2{font-size:19px;margin:32px 0 12px;padding-left:11px;border-left:4px solid var(--accent);line-height:1.4}
h3{font-size:17px;margin:26px 0 10px;color:#b5536f}
h4{font-size:15.5px;margin:20px 0 8px;color:var(--sub)}
h5{font-size:14.5px;margin:16px 0 6px;color:var(--sub)}
p{margin:11px 0}
ul,ol{margin:11px 0;padding-left:24px}
li{margin:6px 0}
blockquote{margin:14px 0;padding:12px 15px;background:#fff6f2;border-left:4px solid var(--accent);
  border-radius:0 12px 12px 0;color:#8a5a68}
blockquote p{margin:5px 0}
hr{border:0;border-top:1px dashed var(--line);margin:26px 0}
code{background:#fff1ea;padding:1px 6px;border-radius:5px;font-size:13.5px;
  font-family:ui-monospace,Consolas,monospace}
a{color:var(--accent)}
table{width:100%;border-collapse:collapse;margin:14px 0;font-size:14px}
th,td{border:1px solid var(--line);padding:9px 11px;text-align:left;vertical-align:top}
th{background:#fff6f2;font-weight:600}
.back{display:inline-block;margin-bottom:16px;color:var(--accent);text-decoration:none;font-size:14px}
@media(max-width:520px){body{padding:14px 11px 40px}.doc{padding:18px 15px;border-radius:16px}}
"""

fix = "--fix" in sys.argv
print("=== 法律文本页面与源文件一致性 ===")
for md_path, out_path, title, site in PAIRS:
    name = os.path.basename(out_path)
    if not os.path.isfile(md_path):
        chk(False, f"源文件存在 {os.path.basename(md_path)}")
        continue
    if not os.path.isfile(out_path):
        if fix:
            build(md_path, out_path, title, site)
            chk(True, f"{name} 不存在，已生成")
        else:
            chk(False, f"{name} 存在", "缺失（跑 --fix 生成）")
        continue
    page = open(out_path, encoding="utf-8").read()
    m = re.search(r"源文件 sha256:\s*([0-9a-f]{64})", page)
    if not m:
        if fix:
            build(md_path, out_path, title, site)
            chk(True, f"{name} 缺哈希标记，已重新生成")
        else:
            chk(False, f"{name} 带源文件哈希标记", "没有标记（跑 --fix 重新生成）")
        continue
    want = sha(md_path)
    if m.group(1) == want:
        chk(True, f"{name} 与 {os.path.basename(md_path)} 一致")
    elif fix:
        build(md_path, out_path, title, site)
        chk(True, f"{name} 已过期 → 重新生成")
    else:
        chk(False, f"{name} 与源文件一致",
            f"源文件已改但页面没重新生成（跑 python verify_legal_docs.py --fix）")

print()
if fails:
    print("失败 %d 项" % len(fails))
    sys.exit(1)
print("通过")
