#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xiaoke799
"""
育儿宝 (BabyCare) - WSGI 服务器启动脚本
使用 Werkzeug 内置服务器 + Unix Socket（适配 fnOS 统一网关）

兼容 Werkzeug 3.x：使用 hostname="unix://..." 方式绑定 Unix socket
"""

import os
import sys

# 将 vendor 内嵌依赖目录加入搜索路径
_vendor_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor")
if os.path.isdir(_vendor_dir) and _vendor_dir not in sys.path:
    sys.path.insert(0, _vendor_dir)

# 设置环境变量
DATA_DIR = os.environ.get("BABYCARE_DATA_DIR", "/var/apps/babycare-fpk/var")
# Socket 路径：优先从环境变量读取（cmd/main 会传入 BABYCARE_SOCKET_PATH）
# 默认值与 fnOS 网关约定一致：app.sock 放在 TRIM_APPDEST（appcenter 解压目录）
SOCKET_PATH = os.environ.get("BABYCARE_SOCKET_PATH", "/vol1/@appcenter/babycare-fpk/app.sock")
PID_FILE = os.environ.get("BABYCARE_PID_FILE", os.path.join(DATA_DIR, "babycare-fpk.pid"))

# 本地开发/回退启动方式：启用免密开发登录（auth.py 要求显式开启）
# 说明：gunicorn 生产模式下不会执行本脚本，dev 登录保持关闭
os.environ.setdefault("BABYCARE_DEV_AUTH", "1")

# 确保 socket 目录存在
socket_dir = os.path.dirname(SOCKET_PATH)
if socket_dir:
    os.makedirs(socket_dir, exist_ok=True)

# 清理旧 socket 文件（避免 "Address already in use" 错误）
if os.path.exists(SOCKET_PATH):
    try:
        os.unlink(SOCKET_PATH)
        print(f"[BabyCare] Removed stale socket: {SOCKET_PATH}", flush=True)
    except OSError as e:
        print(f"[BabyCare] Warning: cannot remove socket: {e}", flush=True)

# 写入 PID
with open(PID_FILE, "w") as f:
    f.write(str(os.getpid()))

# 导入 Flask app
from server import app

# 使用 Werkzeug 内置服务器 + Unix Socket
from werkzeug.serving import run_simple

print(f"[BabyCare] Starting server on unix:{SOCKET_PATH}", flush=True)
print(f"[BabyCare] PID: {os.getpid()}", flush=True)
print(f"[BabyCare] PYTHONPATH: {sys.path[0]}", flush=True)

try:
    # Werkzeug 3.x 兼容写法：使用 hostname="unix://..." 参数
    run_simple(
        hostname=f"unix://{SOCKET_PATH}",
        port=0,
        application=app,
        threaded=True,
        processes=1,
        use_reloader=False,
        use_debugger=False,
    )
except Exception as e:
    print(f"[BabyCare] Server error: {e}", flush=True)
    sys.exit(1)
