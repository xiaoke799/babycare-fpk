# -*- coding: utf-8 -*-
"""开包验货 —— 打完 fpk 后确认「改动真的进包了」

与 `.workbuddy/scripts/verify_fpk_package.py` 的分工（两者互补，不是重复）：
  - verify_fpk_package.py：历次功能点的回归断言（某个功能有没有进包，攒了很多条）
  - 本脚本：**「这个包是不是刚从当前源码打出来的」** —— 逐字节比对 + 归位守卫
    它验的是「新鲜度」，那种「包看着是新的、内容其实是旧的」只有它能发现。
    2026-09-23 真踩过：漏了归位那一步，fix_fpk_exec_bits 把旧包重写了一遍，
    时间戳变新、内容照旧；就是本脚本的逐字节比对把它揪出来的。

为什么必须做：fnpack 是在**父目录**对 `babycare-fpk/` 目录打包的。如果源文件改完
忘了保存、或在错误的目录上打包、或打到了上一轮的产物，产出的包看起来一切正常
（能安装、能启动），但装的其实是旧代码。**只有开包定点比对能发现**。

用法：
    python verify_fpk_contents.py ../babycare-fpk.fpk
    （不传参则默认找同级的 ../../babycare-fpk.fpk）

检查三类：
  1. 包内文件的**内容**与工作区源文件一致（逐字节比对，比看日期可靠）
  2. 关键改动点的特征字符串确实在包里（防止「文件对了但内容被覆盖」）；
     以及反向串（NOT_MARKS）—— 已废掉的写法**不该**出现在包里
  3. 包内后端 .py 全部能 py_compile 通过
"""
import io
import os
import re
import sys
import tarfile
import hashlib
import tempfile
import py_compile

BACKEND = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(BACKEND, "..", ".."))

PASSED, FAILED = [], []


def check(name, cond, detail=None):
    (PASSED if cond else FAILED).append(name)
    line = ("PASS  " if cond else "FAIL  ") + name
    if detail is not None and not cond:
        line += "\n        <- " + str(detail)
    print(line)


# 包内路径 -> 工作区源文件（相对仓库根）
# ⚠️ 包内的 app.tgz 去掉了仓库里的 app/ 前缀：仓库的 app/frontend/x 在包里是 frontend/x。
#    这是实际开包量出来的，别按仓库布局想当然。
PAIRS = [
    ("backend/blueprints/analytics.py", "app/backend/blueprints/analytics.py"),
    ("frontend/js/app.js", "app/frontend/js/app.js"),
    ("frontend/index.html", "app/frontend/index.html"),
    ("frontend/css/main.css", "app/frontend/css/main.css"),
    # 图片也要比对：漏进包就是真机上破图（界面上不报错，只是没图）
    ("frontend/images/qr-wechat.png", "app/frontend/images/qr-wechat.png"),
    ("frontend/images/qr-alipay.png", "app/frontend/images/qr-alipay.png"),
]

# 本次改动的特征串：(描述, 包内文件, 必须出现的子串)
MARKS = [
    ("后端 18 类数据源表在包里", "backend/blueprints/analytics.py", "_TIMELINE_SOURCES"),
    ("后端 limit 上限已放宽到 2000", "backend/blueprints/analytics.py", "maximum=2000"),
    ("前端 TIMELINE_LIMIT=2000 在包里", "frontend/js/app.js", "const TIMELINE_LIMIT = 2000"),
    ("前端自定义日期段节点在包里", "frontend/index.html", 'id="timelineCustomRange"'),
    ("前端「近一年」档在包里", "frontend/index.html", 'value="365"'),
    ("前端新增类型下拉项在包里", "frontend/index.html", '<option value="vaccine">疫苗</option>'),
    ("前端新增类型配色在包里", "frontend/css/main.css", ".timeline-icon.vaccine"),
    ("前端类型筛选已改为服务端下推", "frontend/js/app.js", "params.set('type', typeFilter)"),
    ("首页日期条节点在包里", "frontend/index.html", 'id="homeDateBar"'),
    ("首页回看态逻辑在包里", "frontend/js/app.js", "homeViewDate"),
    ("首页日期条样式在包里", "frontend/css/main.css", ".home-date-bar"),
    ("时间线类型表已提到模块级（首页复用）", "frontend/js/app.js", "const TIMELINE_TYPE_MAP ="),
    ("关于页微信二维码节点在包里", "frontend/index.html", 'id="aboutWechatQr"'),
    ("二维码放大层在包里", "frontend/index.html", 'id="qrZoomModal"'),
    ("关于页交互绑定在包里（点行复制 / 点图放大）",
     "frontend/js/app.js", "function initAboutActions"),
    ("剪贴板 execCommand 兜底在包里（http 下 navigator.clipboard 不存在）",
     "frontend/js/app.js", "document.execCommand('copy')"),
    ("二维码放大层样式在包里", "frontend/css/main.css", ".qr-zoom-overlay"),
    ("关于页二维码样式在包里", "frontend/css/main.css", ".about-support-qr"),
    ("二维码图片文件在包里", "frontend/images/qr-wechat.png", "PNG"),
    ("支付宝二维码节点在包里", "frontend/index.html", 'id="aboutAlipayQr"'),
    ("支付宝二维码图片文件在包里", "frontend/images/qr-alipay.png", "PNG"),
    # ---- 出牙页数据层修复（2026-10-05）----
    ("出牙页已改接 /teething（不再调不存在的 /teeth）",
     "frontend/js/app.js", "function loadTeethRecords"),
    ("出牙删除走 /api/teething/<id>", "frontend/js/app.js", "api(`/api/teething/${id}`"),
    ("出牙记录表已补 tooth_code（迁移 v7）", "backend/migrations.py", "_migration_v7"),
    ("出牙写入端已校验 tooth_code 白名单",
     "backend/blueprints/development.py", "VALID_TOOTH_CODES"),
]

# 反向特征串：(描述, 包内文件, **不得**匹配的正则)
# 光有正向特征不足以证明「旧写法没被漏改回来」——出牙页那次的根因就是
# 前端还留着 /api/babies/<id>/teeth 这个后端已删除的接口，整页静默失效。
NOT_MARKS = [
    ("出牙页不再调不存在的 /teeth 接口",
     "frontend/js/app.js", r"/api/babies/\$\{[^}]*\}/teeth[/`\"')]"),
    ("出牙那套死实现（依赖 #teethingList）没被打回包里",
     "frontend/js/app.js", r"function\s+(loadTeethingRecords|initTeethingPage|deleteTooth)\s*\("),
]


def main():
    fpk = sys.argv[1] if len(sys.argv) > 1 else os.path.join(REPO, "babycare-fpk.fpk")
    if not os.path.exists(fpk):
        print("找不到 fpk：%s" % fpk)
        return 2

    raw = open(fpk, "rb").read()
    print("包文件：%s" % fpk)
    print("md5   ：%s" % hashlib.md5(raw).hexdigest())
    print("大小  ：%d 字节" % len(raw))
    print("-" * 60)

    # 归位守卫（这个坑真踩过）：fnpack 的产物落在**父目录** D:/育儿板块/babycare-fpk.fpk，
    # 交付件却在仓库内 babycare-fpk/babycare-fpk.fpk，中间必须 mv 一次。
    # 忘了归位就会：父目录是新包、仓库内还是旧包，而后续 fix_fpk_exec_bits 会把旧包
    # 「重写」一遍 —— 时间戳变新、内容照旧，看着像刚打的包，实际是旧的。
    parent = os.path.dirname(REPO)
    stray = os.path.join(parent, "babycare-fpk.fpk")
    if os.path.exists(stray):
        s = os.path.getsize(stray)
        check("父目录没有遗留未归位的 fpk（有则说明新包可能没 mv 进来）", False,
              {"遗留": stray, "大小": s, "提示": "把它 mv 成 babycare-fpk/babycare-fpk.fpk 再验"})
    else:
        check("父目录无遗留 fpk（打包产物已归位）", True)

    outer = tarfile.open(fileobj=io.BytesIO(raw))
    names = outer.getnames()
    check("包是 tar 结构且含 app.tgz", "app.tgz" in names, names[:8])

    # 解 app.tgz 到临时目录（用完即删，不留在工作区外）
    app_tar = outer.extractfile("app.tgz").read()
    tmp = tempfile.mkdtemp(prefix="fpk_verify_")
    try:
        inner = tarfile.open(fileobj=io.BytesIO(app_tar))
        try:
            inner.extractall(tmp, filter="data")   # py3.12+ 明确走安全提取
        except TypeError:
            inner.extractall(tmp)
        packed = {}
        for root, _dirs, files in os.walk(tmp):
            for f in files:
                full = os.path.join(root, f)
                rel = os.path.relpath(full, tmp).replace("\\", "/")
                packed[rel] = full

        # ---- 1. 包内文件与工作区逐字节一致 ----
        for inside, src_rel in PAIRS:
            src = os.path.join(REPO, src_rel)
            if inside not in packed:
                # 按文件名兜底找一下，并把真实路径报出来（方便校正 PAIRS）
                cands = [k for k in packed if k.endswith(os.path.basename(inside))]
                check("包内含 %s" % inside, False,
                      {"按文件名找到": cands[:5], "包内文件数": len(packed)})
                continue
            if not os.path.exists(src):
                check("工作区源文件在：%s" % src_rel, False)
                continue
            a = open(packed[inside], "rb").read()
            b = open(src, "rb").read()
            check("%s 与工作区源文件逐字节一致" % os.path.basename(inside),
                  a == b,
                  {"包内": len(a), "工作区": len(b),
                   "包内md5": hashlib.md5(a).hexdigest()[:12],
                   "工作区md5": hashlib.md5(b).hexdigest()[:12],
                   "提示": "包是某一刻打的，之后工作区又被人改过就会不一致。"
                           "本次改动到底有没有进包，看下面 MARKS 那几条；"
                           "若另一会话正在改同一文件（未提交），这里也会不一致 —— "
                           "那种情况别把它的半成品打进包，等它提交后重打即可"})

        # ---- 2. 关键改动点的特征串 ----
        cache = {}
        for desc, inside, needle in MARKS:
            path = packed.get(inside)
            if path is None:
                # 兜底：按文件名找，再回到 packed 里取真实路径（别拿相对名当路径用）
                cands = [k for k in packed if k.endswith(os.path.basename(inside))]
                path = packed.get(cands[0]) if cands else None
            if path is None:
                check(desc, False, "包里找不到 %s" % inside)
                continue
            if path not in cache:
                cache[path] = open(path, "r", encoding="utf-8", errors="replace").read()
            check(desc, needle in cache[path], {"文件": inside, "找的串": needle})

        # ---- 2b. 反向特征串：这些串**不该**出现在包里 ----
        for desc, inside, pattern in NOT_MARKS:
            path = packed.get(inside)
            if path is None:
                cands = [k for k in packed if k.endswith(os.path.basename(inside))]
                path = packed.get(cands[0]) if cands else None
            if path is None:
                check(desc, False, "包里找不到 %s" % inside)
                continue
            if path not in cache:
                cache[path] = open(path, "r", encoding="utf-8", errors="replace").read()
            hit = re.search(pattern, cache[path])
            check(desc, hit is None, {"文件": inside, "不该出现的": hit.group(0) if hit else None})

        # ---- 3. 包内后端 .py 全部编译通过 ----
        bad = []
        n_py = 0
        for rel, full in packed.items():
            if rel.endswith(".py"):
                n_py += 1
                try:
                    py_compile.compile(full, doraise=True, cfile=os.path.join(
                        tempfile.gettempdir(), "fpkchk.pyc"))
                except py_compile.PyCompileError as e:
                    bad.append("%s: %s" % (rel, str(e).splitlines()[-1]))
        check("包内 %d 个后端 .py 全部编译通过" % n_py, not bad, bad[:5])
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 60)
    print("通过 %d 项，失败 %d 项" % (len(PASSED), len(FAILED)))
    if FAILED:
        print("失败明细：")
        for n in FAILED:
            print("  - " + n)
    print("=" * 60)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
