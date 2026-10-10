# CryptoQuant · 数字币量化交易策略研究平台

> 搞模釜AgentSteamer智能体平台空闲时间换脑用的产物，你可以把它当成玩具，也可以当成因子研究学习平台
> 模釜官网：<https://www.agentsteamer.com> 官方 Blog：<https://blog.agentsteamer.com>

> 一个面向数字币的「数据 → 因子挖掘 -> 模型训练 → 回测 → 模拟盘 → LLM 复盘」一站式AI因子挖掘研究平台。  
> 没有实盘

## 目录
- [项目结构](#项目结构)
- [快速开始（Docker 一键跑）](#快速开始docker-一键跑)
- [本地开发与启动（含 worker / beat）](#本地开发与启动含-worker--beat)
- [设计文档](#设计文档)
- [里程碑](#里程碑)
- [下一步](#下一步)

## 项目结构
```
.
├── backend/                      # FastAPI 后端（包名 app）
│   ├── src/app/                  # 业务包
│   ├── alembic/                  # 迁移（0001 建表 / 0002 Continuous Aggregates）
│   ├── docker/initdb/            # TimescaleDB / pgvector 初始化
│   ├── tests/                    # smoke + 策略引擎
│   ├── Dockerfile
│   └── pyproject.toml
├── frontend/                     # Vue3 + Tailwind 前端
│   ├── src/
│   ├── package.json
│   └── vite.config.ts
├── scripts/                      # start.ps1 / start.sh（gateway+worker+beat+前端）
└── docker-compose.yml            # postgres + redis + gateway
```

## 快速开始（Docker 一键跑）

> 前提：已安装 Docker Desktop（WSL2 后端 / Linux 原生均可），本地能访问 `127.0.0.1:5432 / 6379`。
> 如果是单机跑，把 `backend/.env` 中 `DATABASE_HOST` / `REDIS_HOST` 改为 `postgres` / `redis`（docker-compose 中已自动覆盖）。

```bash
# 1. 复制后端配置
cp backend/.env.example backend/.env
# （可选）按需修改

# 2. 启动
docker-compose up -d --build

# 3. 验证
curl http://localhost:8000/health
# 期望：{"code":"OK","data":{"service":"cryptoquant-gateway","db":true,"redis":true,...}}

curl "http://localhost:8000/api/v1/market/tickers?symbol=BTCUSDT"
# 期望：返回币安 BTC 24h ticker

# 4. 启动前端
cd frontend
npm install
npm run dev
# 浏览器打开 http://localhost:5173
```

## 本地开发与启动（含 worker / beat）

> 前提：能访问 `127.0.0.1:5432 / 6379`（数据库与 Redis，见 `backend/.env` 或 `docker-compose.yml`），并能访问 `api.binance.com`。
> 后端进程全部通过 `backend/.env`（`DATABASE_HOST / REDIS_HOST ...`）取配置；不设环境变量时默认 `127.0.0.1`。

### 1. 安装与迁移

```powershell
cd backend
python -m pip install -e ".[dev]"

# 首次或表结构变更后应用迁移（0001 建表 + 0002 Continuous Aggregates）
python -m alembic upgrade head
# 期望输出：Running upgrade 0001_m1_initial -> 0002_m2_caggs, ...
```

测试：`cd backend && python -m pytest -q`（smoke + 策略引擎用例，13 passed）。

### 2. 启动后端（三进程，需分别开终端）

**① gateway —— REST + 实时行情 WS + 回测 SSE**

```powershell
cd backend
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

**② ARQ worker —— 执行 K 线下载 / 定时增量同步 / 回测（不开它任务只排队不执行）**

```powershell
cd backend
python -m arq app.job.arq_app.WorkerSettings
# 期望：Starting worker for 3 functions: download_klines_task, sync_incremental_klines_task, run_backtest_task
```

**③ beat 调度器 —— 把「定时任务」页的 cron 真正派发为增量同步**

```powershell
cd backend
python -m app.job.scheduler
# 期望：beat.start ... schedule.round
```

> 说明：worker 与 beat 使用与 gateway 相同的 Redis（默认 DB 8）作为队列/发布总线，必须与 gateway 同一 `.env` 配置。

**验证**

```powershell
curl http://localhost:8000/health
# {"code":"OK",...,"db":true,"redis":true}
```

### 3. 启动前端

```powershell
cd frontend
npm install
npm run dev
# 浏览器打开 http://localhost:5173（/ws 已代理到 ws://localhost:8000）
```

### 4. 一键脚本（Windows）

```powershell
.\scripts\start.ps1    # 后台启动 gateway + worker + beat + vite，日志在 .run\*.log
.\scripts\stop.ps1     # 停止全部
```

## 里程碑

| 里程碑 | 内容 | 状态 |
|---|---|---|
| **M0 设计 + 骨架** | 4+2 份设计文档 + 后端骨架 + DB 初始化 + 看板拉 ticker demo | ✅ |
| M1 数据闭环 | 下载任务 / 定时(beat) / 增量续传 / Continuous Aggregates / 实时行情 WS / 回测进度 SSE | ✅ 后端完成，CA/beat 已建 |
| M2 策略 + 因子 v1 | 策略模板 / 向量化回测引擎 / 指标卡+资金曲线前端 / 因子研究 | ✅ 策略研究端到端  |
| M3 Agent v1 | FactorAgent / StrategyAgent / TraderAgent + pgvector + Token 用量页 | 🟡部分完成 |
| M4 模拟盘 + 三路对比 | manual / strategy / llm 三账户 + 净值对比 | 🟡部分完成 |
| M5 完善 | 主题 / 移动 / 监控告警 / 文档站 | 🟡 明暗主题已完成 |


