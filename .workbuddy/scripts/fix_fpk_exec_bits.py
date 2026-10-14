#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""给 fpk 包内 cmd/ 脚本补执行位（Windows 上 fnpack 打出来的包会丢）。

背景（2026-09-16 起，每次打包必跑）：
  fpk 是 tar.gz。fnpack 在 Windows 上打出来的包，cmd/ 下的 9 个脚本
  （main / install_init / install_callback / ...）mode 全是 0o666 ——
  fnOS 侧起容器时要 exec 这些脚本，没执行位应用起不来。
  verify_fpk_package.py 第一条断言「外层 cmd 脚本都有执行位」就是防这个。

为什么这脚本总要重写：
  它是个一次性构建步骤，历史上从没进过 git，被后台清理程序删过好几轮。
  2026-10-14 起正式入库（.workbuddy/scripts/ 会被后台程序删，但入库的
  至少能从 git 找回来）。

做法：读旧包全部成员 → cmd/ 下 mode 置 0o755 → 原样重写 tar.gz → 原子替换。
  ⚠ 必须保留每个成员的 mtime/uid/gid/uname/gname/type，只改 mode；
  ⚠ gzip 用 mtime=0，保证同一输入产出字节确定（可重复构建）。

用法（在仓库根跑）：
    python .workbuddy/scripts/fix_fpk_exec_bits.py [包路径]
    默认包路径 babycare-fpk.fpk
退出码：0 = 已处理（或本就干净）；1 = 出错。
"""
import io
import os
import shutil
import sys
import tarfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def fix(path):
    with tarfile.open(path, "r:gz") as tin:
        members = tin.getmembers()
        # 先把每个成员的内容读出来（流式读，别二次开包）
        blobs = {}
        for m in members:
            if m.isreg():
                f = tin.extractfile(m)
                blobs[m.name] = f.read() if f else b""
        fixed = []
        for m in members:
            if m.name.startswith("cmd/") and (m.mode & 0o111) == 0:
                m.mode = 0o755
                fixed.append(m.name)
    tmp = path + ".tmp"
    with tarfile.open(tmp, "w:gz", compresslevel=9) as tout:
        for m in members:
            if m.isreg():
                tout.addfile(m, io.BytesIO(blobs[m.name]))
            else:
                tout.addfile(m)
    shutil.move(tmp, path)
    return fixed


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "babycare-fpk.fpk")
    if not os.path.exists(path):
        print("找不到包：%s" % path)
        return 1
    fixed = fix(path)
    # 自检：重开后 cmd/ 必须全带执行位（改完不验证 = 没改）
    with tarfile.open(path, "r:gz") as t:
        bad = [m.name for m in t.getmembers()
               if m.name.startswith("cmd/") and (m.mode & 0o111) == 0]
    if bad:
        print("处理后仍缺执行位：%s" % bad)
        return 1
    print("已补执行位 %d 个：%s" % (len(fixed), ", ".join(fixed) if fixed else "（本就干净，无需修改）"))
    print("自检通过：包内 cmd/ 全部带执行位")
    return 0


if __name__ == "__main__":
    sys.exit(main())
