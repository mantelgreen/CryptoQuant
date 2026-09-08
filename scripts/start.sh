#!/usr/bin/env bash
# ============================================================
# CryptoQuant 一键启动脚本（bash / Linux & macOS）
#
# 用法（在仓库根目录下）：
#   ./scripts/start.sh                # 完整流程
#   ./scripts/start.sh --skip-install # 跳过 pip install
#   ./scripts/start.sh --skip-migrate # 跳过 alembic
#   ./scripts/start.sh --only-backend # 只起后端
#   ./scripts/start.sh --only-frontend # 只起前端
#   ./scripts/start.sh --help
# ============================================================
set -euo pipefail

SKIP_INSTALL=0
SKIP_MIGRATE=0
ONLY_BACKEND=0
ONLY_FRONTEND=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --skip-install) SKIP_INSTALL=1; shift;;
        --skip-migrate) SKIP_MIGRATE=1; shift;;
        --only-backend) ONLY_BACKEND=1; shift;;
        --only-frontend) ONLY_FRONTEND=1; shift;;
        -h|--help)
            sed -n '2,20p' "$0"; exit 0;;
        *) echo "unknown arg: $1"; exit 1;;
    esac
done

cd "$(dirname "$0")/.."
ROOT="$(pwd)"

step() { printf "\n\033[36m=== %s ===\033[0m\n" "$*"; }
ok()   { printf "\033[32m[OK]\033[0m %s\n" "$*"; }
warn() { printf "\033[33m[WARN]\033[0m %s\n" "$*"; }
fail() { printf "\033[31m[FAIL]\033[0m %s\n" "$*"; exit 1; }

# ===== 1. 后端 venv + 依赖 =====
if [[ $ONLY_FRONTEND -eq 0 ]]; then
    step "后端：创建虚拟环境 (.venv) 与安装依赖"

    if [[ ! -d ".venv" ]]; then
        python3 -m venv .venv
        ok "已创建 .venv"
    else
        ok ".venv 已存在"
    fi

    # shellcheck disable=SC1091
    source .venv/bin/activate
    PY=.venv/bin/python

    if [[ $SKIP_INSTALL -eq 0 ]]; then
        "$PY" -m pip install --upgrade pip >/dev/null
        # pyproject.toml 在 backend/ 下，必须切到该目录再安装
        (cd backend && "$PY" -m pip install -e ".[dev]")
        ok "依赖安装完成"
    else
        ok "已跳过 pip install"
    fi

    # ===== 2. 加载 .env =====
    step "加载 .env 配置"
    if [[ ! -f backend/.env ]]; then
        cp backend/.env.example backend/.env
        warn "已从 .env.example 复制 backend/.env，请按需修改"
    fi
    set -a
    # shellcheck disable=SC1091
    source backend/.env
    set +a
    ok "环境变量已注入（DATABASE_HOST=${DATABASE_HOST:-?} / REDIS_HOST=${REDIS_HOST:-?}）"

    # ===== 3. alembic =====
    if [[ $SKIP_MIGRATE -eq 0 ]]; then
        step "alembic upgrade head"
        (cd backend && "$PY" -m alembic upgrade head)
        ok "迁移完成"
    else
        ok "已跳过 alembic"
    fi

    # ===== 4. smoke =====
    step "smoke test"
    (cd backend && "$PY" -m pytest tests/ -q) || warn "smoke test 有失败，请检查"
fi

# ===== 5. 启动进程 =====
mkdir -p .run
PIDS=()

cleanup() {
    step "退出中，停止子进程..."
    for pid in "${PIDS[@]}"; do
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null || true
        fi
    done
    exit 0
}
trap cleanup INT TERM

if [[ $ONLY_FRONTEND -eq 0 ]]; then
    step "启动 gateway (uvicorn)"
    (
        cd backend
        nohup .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 \
            >../.run/gateway.log 2>../.run/gateway.err &
        echo $! >../.run/gateway.pid
    )
    PIDS+=($(cat .run/gateway.pid))
    ok "gateway pid=$(cat .run/gateway.pid) log=.run/gateway.log"

    step "启动 ARQ worker"
    (
        cd backend
        nohup .venv/bin/python -m arq app.job.arq_app.WorkerSettings \
            >../.run/worker.log 2>../.run/worker.err &
        echo $! >../.run/worker.pid
    )
    PIDS+=($(cat .run/worker.pid))
    ok "ARQ worker pid=$(cat .run/worker.pid) log=.run/worker.log"

    step "启动 beat 调度器"
    (
        cd backend
        nohup .venv/bin/python -m app.job.scheduler \
            >../.run/beat.log 2>../.run/beat.err &
        echo $! >../.run/beat.pid
    )
    PIDS+=($(cat .run/beat.pid))
    ok "beat pid=$(cat .run/beat.pid) log=.run/beat.log"

    sleep 3
    if curl -s -m 5 http://127.0.0.1:8000/health >/dev/null; then
        ok "health OK"
    else
        warn "健康检查失败，请查看 .run/gateway.log"
    fi
fi

if [[ $ONLY_BACKEND -eq 0 ]]; then
    step "前端：npm install"
    cd frontend
    if [[ ! -d node_modules ]]; then
        npm install
    else
        ok "node_modules 已存在"
    fi

    step "前端：vite dev"
    cd ..
    nohup npm --prefix frontend run dev >.run/frontend.log 2>.run/frontend.err &
    echo $! >.run/frontend.pid
    PIDS+=($(cat .run/frontend.pid))
    ok "frontend pid=$(cat .run/frontend.pid) log=.run/frontend.log"
fi

step "访问入口"
echo "  - API      : http://localhost:8000"
echo "  - Health   : http://localhost:8000/health"
echo "  - Docs     : http://localhost:8000/docs"
echo "  - Frontend : http://localhost:5173"
echo "  - Logs     : .run/*.log"
echo ""
echo "停止全部服务: ./scripts/stop.sh"
echo "查看日志    : tail -f .run/gateway.log"

# 不再前台挂住；脚本返回终端，服务由 nohup 在后台运行
exit 0
