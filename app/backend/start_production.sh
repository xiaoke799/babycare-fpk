#!/bin/bash
# 生产环境启动脚本 - 通过 gunicorn 监听 Unix Socket（fnOS 统一网关要求）
# 由 cmd/main 调用，不需要手动执行

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
DATA_DIR="${BABYCARE_DATA_DIR:-/var/apps/babycare-fpk/var}"
CONFIG_DIR="${BABYCARE_CONFIG_DIR:-/var/apps/babycare-fpk/etc}"
DB_PATH="${BABYCARE_DB_PATH:-$DATA_DIR/babycare.db}"
PORT="${BABYCARE_PORT:-8090}"
GATEWAY_PREFIX="${GATEWAY_PREFIX:-/app/babycare-fpk}"
SOCKET_PATH="${SOCKET_PATH:-}"

# 确保目录存在
mkdir -p "$DATA_DIR" "$CONFIG_DIR"

# 设置环境变量
export BABYCARE_DATA_DIR="$DATA_DIR"
export BABYCARE_CONFIG_DIR="$CONFIG_DIR"
export BABYCARE_DB_PATH="$DB_PATH"
export GATEWAY_PREFIX="$GATEWAY_PREFIX"

cd "$APP_DIR"

if [ -n "$SOCKET_PATH" ]; then
    # 统一网关模式：监听 Unix Socket
    echo "Starting babycare-fpk with gunicorn on Socket: $SOCKET_PATH"
    [ -e "$SOCKET_PATH" ] && rm -f "$SOCKET_PATH"
    exec gunicorn \
        --bind "unix:$SOCKET_PATH" \
        --workers 2 \
        --timeout 120 \
        --access-logfile "$DATA_DIR/access.log" \
        --error-logfile "$DATA_DIR/error.log" \
        server:app
else
    # 端口模式（备用）：仅绑定本机回环地址，避免未认证接口暴露到局域网
    echo "Starting babycare-fpk with gunicorn on port: $PORT (loopback only)"
    exec gunicorn \
        --bind "127.0.0.1:$PORT" \
        --workers 2 \
        --timeout 120 \
        --access-logfile "$DATA_DIR/access.log" \
        --error-logfile "$DATA_DIR/error.log" \
        server:app
fi
