#!/usr/bin/env bash
# 停止由 start.sh 启动的 gateway / ARQ worker / vite
set -uo pipefail

cd "$(dirname "$0")/.."

stop_pidfile() {
    local f="$1"
    local name="$2"
    if [[ -f "$f" ]]; then
        local pid
        pid=$(cat "$f")
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null || true
            printf "\033[32m[OK]\033[0m stopped %s pid=%s\n" "$name" "$pid"
        fi
        rm -f "$f"
    fi
}

echo "=== 停止 gateway ==="
stop_pidfile .run/gateway.pid  gateway
echo "=== 停止 ARQ worker ==="
stop_pidfile .run/worker.pid   worker
echo "=== 停止 beat 调度器 ==="
stop_pidfile .run/beat.pid     beat
echo "=== 停止 vite ==="
stop_pidfile .run/frontend.pid frontend

# 兜底：按命令匹配
pkill -f "uvicorn app.main:app" 2>/dev/null || true
pkill -f "arq app.job.arq_app"   2>/dev/null || true
pkill -f "app.job.scheduler"     2>/dev/null || true
pkill -f "vite"                          2>/dev/null || true

echo "done."
