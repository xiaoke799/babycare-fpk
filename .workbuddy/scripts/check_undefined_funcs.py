#!/usr/bin/env python3
"""扫描前端 JS，找出「调用了但从未定义」的函数名（死入口的根因）。

原理：
1. 从 index.html 的内联 <script> 与 js/*.js 里收集所有定义：function F、F=function、
   F=(..)=>、const/let/var F、class F、以及对象字面量里的 `key: function`/`key(..) {`。
2. 收集所有「不是属性访问」的调用点：`F(`，排除前面是 `.` 或 `?.` 的（那是方法调用）。
3. 差集 = 疑似未定义。

会有一批内置/浏览器 API 的已知名单，直接忽略。
"""
import os
import re
import sys

ROOT = r"D:\育儿板块\babycare-fpk\app\frontend"

BUILTINS = set("""
if for while switch catch return typeof new delete void in of do else try finally throw
function class extends await async yield
Array Object String Number Boolean Date Math JSON RegExp Error Promise Map Set WeakMap
Symbol Function Infinity NaN undefined parseInt parseFloat isNaN isFinite encodeURIComponent
decodeURIComponent encodeURI decodeURI setTimeout setInterval clearTimeout clearInterval
requestAnimationFrame alert confirm prompt fetch structuredClone queueMicrotask
console document window navigator location localStorage sessionStorage history screen
eval Boolean BigInt Intl Proxy Reflect globalThis arguments super this
AbortController Blob FileReader FileWriter Float32Array Float64Array Int8Array Uint8Array
ArrayBuffer DataView Uint8ClampedArray FormData Option URLSearchParams URL TextEncoder
TextDecoder Image Audio Worker MutationObserver IntersectionObserver ResizeObserver
DOMParser XMLSerializer Event CustomEvent EventSource WebSocket Headers Request Response
OffscreenCanvas Path2D AudioContext MediaRecorder Notification ClipboardItem BroadcastChannel
""".split())

METHODISH = set("""
map filter forEach reduce find findIndex some every sort join split slice splice concat
push pop shift unshift includes indexOf lastIndexOf keys values entries from of assign
stringify parse then catch finally all allSettled race resolve reject toFixed toLocaleString
toISOString getTime getDate getDay getMonth getFullYear getHours getMinutes getSeconds
setDate setMonth setFullYear setHours setMinutes setSeconds replace replaceAll trim
toLowerCase toUpperCase startsWith endsWith padStart padEnd charAt charCodeAt codePointAt
substring substr repeat match matchAll test exec
add remove toggle contains removeAttribute setAttribute getAttribute hasAttribute
appendChild removeChild insertBefore replaceChild cloneNode querySelector querySelectorAll
getElementById getElementsByClassName getElementsByTagName createElement createElementNS
addEventListener removeEventListener dispatchEvent preventDefault stopPropagation
scrollIntoView scrollTo focus blur click submit reset select
textContent innerHTML innerText value checked disabled classList style dataset
toString valueOf hasOwnProperty
toBlob toDataURL drawImage getImageData putImageData createObjectURL revokeObjectURL
""".split())


def read_sources():
    parts = []
    html = os.path.join(ROOT, "index.html")
    with open(html, encoding="utf-8", errors="replace") as f:
        content = f.read()
    for m in re.finditer(r"<script\b[^>]*>(.*?)</script>", content, re.S | re.I):
        parts.append(m.group(1))
    jsdir = os.path.join(ROOT, "js")
    for name in sorted(os.listdir(jsdir)):
        if name.endswith(".js"):
            with open(os.path.join(jsdir, name), encoding="utf-8", errors="replace") as f:
                parts.append(f.read())
    return parts


def _skip_quoted(src: str, i: int, quote: str) -> int:
    """从 src[i]（引号本身）跳到字符串结束之后。未闭合则到行尾为止。"""
    n = len(src)
    i += 1
    while i < n:
        c = src[i]
        if c == "\\":
            i += 2
            continue
        if c == quote:
            return i + 1
        if c == "\n":
            return i  # 单/双引号字符串不跨行，视为未闭合
        i += 1
    return i


# 这些关键字后面出现 `/` 一定是正则（而不是除法）
REGEX_PRECEDING_KEYWORDS = {
    "return", "typeof", "instanceof", "in", "of", "new", "delete", "void",
    "case", "do", "else", "yield", "await", "throw",
}


def _is_regex_start(src: str, i: int) -> bool:
    """判断 src[i] == '/' 是不是正则字面量的起点。

    JS 里 `/` 既是除法也是正则起点，标准做法是看前一个有效 token：
    运算符 / 左括号 / 逗号 / 冒号 / 关键字之后 => 正则；标识符或数字之后 => 除法。

    为什么必须做这一步（2026-09-29 实测踩坑）：日记渲染里有
    ``t.replace(/`([^`]+)`/g, ...)``——正则内部含反引号，词法扫描器会把它
    当成模板字符串起点，一路吞掉后续 62 行代码，把 renderDiaryContent /
    diaryPreviewText 两个真实存在的函数定义"吃掉"，于是被误报成未定义。
    同一类问题还有 `/^https?:\\/\\//` 里的 `//` 被当成行注释。
    """
    j = i - 1
    while j >= 0 and src[j] in " \t\r\n":
        j -= 1
    if j < 0:
        return True
    c = src[j]
    if c in "(,=:[!&|?{};+-*%~^<>":
        return True
    if c == "/":
        return False  # a / b 的除法（也可能是正则，保守按除法）
    m = re.search(r"([A-Za-z_$][\w$]*)$", src[: j + 1])
    if m and m.group(1) in REGEX_PRECEDING_KEYWORDS:
        return True
    return False


def _skip_regex(src: str, i: int) -> int:
    """src[i] == '/' 且已判定为正则起点，返回正则（含标志位）结束后的下标。"""
    n = len(src)
    i += 1
    in_class = False
    while i < n:
        c = src[i]
        if c == "\\":
            i += 2
            continue
        if c == "\n":
            return i  # 正则不跨行，视为未闭合，就地停下
        if in_class:
            if c == "]":
                in_class = False
            i += 1
            continue
        if c == "[":
            in_class = True
            i += 1
            continue
        if c == "/":
            i += 1
            break
        i += 1
    while i < n and src[i] in "gimsuyd":  # 正则标志
        i += 1
    return i


def strip_noise(src: str) -> str:
    """去掉注释与字符串字面量，只留代码骨架。

    为什么不用几条正则依次替换？——**因为顺序会互相破坏**（2026-09-19 实测踩坑）：
    模板字符串里常含不成对的引号（HTML 属性插值、内联 onclick 里的单引号等），
    若先剥引号，那对引号会**跨过模板边界**去配对，顺带把一个反引号也吃掉
    → 模板起点从此错位，一整段代码被当成模板吞掉
    （本库实测：一次吞掉 3416 字符，loadAppInfo 等一串函数定义凭空消失，
    扫描器把它们误报成「未定义」）。反过来先剥模板也不行：行注释会把
    模板里的网址连同行尾的反引号一起删掉，同样失衡。

    所以这里做一次真正的词法扫描：用栈区分「代码」与「模板」两种上下文，
    正确处理注释、单/双引号串、模板串、以及模板内的花括号插值表达式
    （表达式里还能再出现字符串、模板、注释）。模板与字符串的内容整体替换成空串，
    反引号与引号本身也一并抹掉，避免残留字符干扰后续正则。
    """
    out = []
    i = 0
    n = len(src)
    # 栈帧：{'type': 'code', 'braces': int} 或 {'type': 'template'}
    frames = [{"type": "code", "braces": 0}]
    while i < n:
        c = src[i]
        if frames[-1]["type"] == "template":
            if c == "\\":
                i += 2
                continue
            if c == "`":
                frames.pop()
                i += 1
                continue
            if c == "$" and i + 1 < n and src[i + 1] == "{":
                frames.append({"type": "code", "braces": 0})
                i += 2
                continue
            i += 1
            continue

        # ---- 代码上下文 ----
        if c == "/":
            # 顺序要紧：JS 里 `//` 与 `/*` 恒为注释（不存在以 `//` 开头的合法正则），
            # 必须**先判注释再判正则**——反过来会把行注释当成空正则吃掉，
            # 注释正文反而变成代码，里面的 `/`、引号、反引号继续污染词法状态
            # （2026-09-29 实测：反序时 12196 行起 37 个函数定义被吞）。
            if i + 1 < n and src[i + 1] == "/":
                j = src.find("\n", i)
                i = n if j < 0 else j
                continue
            if i + 1 < n and src[i + 1] == "*":
                j = src.find("*/", i + 2)
                i = n if j < 0 else j + 2
                continue
            if _is_regex_start(src, i):
                i = _skip_regex(src, i)
                out.append('""')  # 正则整体抹掉，内部引号/反引号不再干扰
                continue
            out.append(c)  # 除法
            i += 1
            continue
        if c in "'\"":
            i = _skip_quoted(src, i, c)
            out.append('""')
            continue
        if c == "`":
            frames.append({"type": "template"})
            out.append('""')
            i += 1
            continue
        if c == "{":
            frames[-1]["braces"] += 1
            out.append(c)
            i += 1
            continue
        if c == "}":
            if frames[-1]["braces"] > 0:
                frames[-1]["braces"] -= 1
            elif len(frames) > 1:
                frames.pop()  # 闭合模板里的 ${…}
            out.append(c)
            i += 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def collect_definitions(src: str) -> set:
    names = set()
    pats = [
        r"\bfunction\s+([A-Za-z_$][\w$]*)",
        r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=",
        r"\bclass\s+([A-Za-z_$][\w$]*)",
        # 对象字面量方法：  key: function  或  key(args) {  或  key: (args) =>
        r"([A-Za-z_$][\w$]*)\s*:\s*(?:async\s+)?function\b",
        r"([A-Za-z_$][\w$]*)\s*:\s*(?:async\s*)?\([^()]*\)\s*=>",
        r"([A-Za-z_$][\w$]*)\s*\(\s*[^()]*\)\s*\{",
        r"\bwindow\.([A-Za-z_$][\w$]*)\s*=",
    ]
    for p in pats:
        names.update(re.findall(p, src))
    return names


def collect_calls(src: str) -> set:
    names = set()
    for m in re.finditer(r"(?<![\w$.?])([A-Za-z_$][\w$]*)\s*\(", src):
        names.add(m.group(1))
    return names


def collect_params(src: str) -> set:
    """收集函数形参名。

    形参是局部名字，`safeCall(fn, name)` 里的 `fn()`、`makeLoopNoiseBuffer(…, genFn, …)`
    里的 `genFn(…)` 都会命中调用正则，但它们**不是未定义的全局函数**——属于误报。
    （2026-09-19 实测：未加这一步时 `fn` / `genFn` 恒被报为「疑似未定义」。）

    只从「真正的形参表」里取：function 声明/表达式与箭头函数。**刻意不去匹配
    `name(...) {` 这种对象方法写法**——那个正则会连 `if (x) {` / `for (i…) {` 一起命中，
    把条件表达式里的变量名当成形参吞进忽略集，反而会掩盖真实的未定义调用。
    """
    names = set()
    for m in re.finditer(r"\bfunction\s*(?:[A-Za-z_$][\w$]*)?\s*\(([^()]*)\)", src):
        names |= set(re.findall(r"[A-Za-z_$][\w$]*", m.group(1)))
    for m in re.finditer(r"\(([^()]*)\)\s*=>", src):
        names |= set(re.findall(r"[A-Za-z_$][\w$]*", m.group(1)))
    names.discard("")
    return names


def main():
    defined = set()
    params = set()
    called = {}
    for raw in read_sources():
        src = strip_noise(raw)
        defined |= collect_definitions(src)
        params |= collect_params(src)
        for name in collect_calls(src):
            called.setdefault(name, 0)
            called[name] += 1

    skip = BUILTINS | METHODISH | defined | params
    missing = sorted(n for n in called if n not in skip)

    print(f"定义 {len(defined)} 个，形参 {len(params)} 个，调用 {len(called)} 个")
    print("=" * 66)
    if not missing:
        print("未发现未定义的函数调用")
        return 0
    print(f"疑似未定义（{len(missing)} 个）：")
    for n in missing:
        print(f"  {n}()   x{called[n]}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
