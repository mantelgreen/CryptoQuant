# 04 系统设计方案 v2（Review 版）

> 本版针对 v1（`03-系统设计方案-v1.md`）做全面 review 与修订。
> review 维度：**正确性 / 完整性 / 可行性 / 一致性**。
> 文档结构与 v1 保持一致，新增「v1 → v2 变更清单」一节便于追溯。

---

## v1 → v2 变更清单（速查）

| 编号 | 类别 | 位置 | 问题 | 处理 |
|---|---|---|---|---|
| C1 | 正确性 | §6.2 tickers_24h | 普通表无法承载高频写入 | 改为 hypertable（chunk 1d） |
| C2 | 正确性 | §7 Redis 键空间 | hash/string 混用不一致 | 统一约定 + 修正 |
| C3 | 一致性 | §8.4 ML 默认 LightGBM | 技术栈未列 | 加入 pyproject 可选依赖 |
| C4 | 正确性 | §8.6 限价单挂单 | 没有订单簿撮合逻辑 | 明确「模拟撮合 = 不维护订单簿」 |
| C5 | 正确性 | §9 /market/orderbook | REST 与 WS 重复 | 明确 REST=初始/兜底，WS=实时 |
| C6 | 正确性 | §7 令牌桶描述 | 滑动窗口与 INCR+EXPIRE 不一致 | 改为「固定窗口 + 滑动近似」 |
| C7 | 正确性 | §8.1 增量续传 | 完成后写 last_bar 错 | 改为「每段拉完立即更新」 |
| C8 | 正确性 | §9 agent 流式 | HTTP POST 无法流式 | 改为 SSE `/agent/sessions/{id}/messages/stream` |
| C9 | 正确性 | §10 WS 鉴权 | query 传 JWT 易泄漏 | 改为「首包鉴权」+ Sec-WebSocket-Protocol |
| C10 | 正确性 | §6.4 continuous aggregates | 非整数倍窗口不对齐 | 修正可建窗口表 + 不可建窗口用查询时聚合 |
| C11 | 可行性 | §8.4 AI 因子沙箱 | RestrictedPython 对 numpy 不友好 | 改为「AST 白名单 + 子进程 + 资源限制」 |
| C12 | 可行性 | §8.7 token 估算 | 中文按 1/4 估算偏低 | 改为「英文 1/4、中文 1/1.5、混合按字符比例」 |
| C13 | 可行性 | §6.5 pgvector 维度 | 硬编码 1536 | 改为 `vector(1024)` + 备注按模型调整 |
| C14 | 可行性 | §8.3 手续费 | 默认 0.1% 对合约不适用 | 区分 spot/futures 默认费率 |
| C15 | 可行性 | §8.5 TraderAgent 周期 | 未定义 | 默认 1m，可配置 |
| F1 | 完整性 | 全局 | 缺权限矩阵 | 新增 §20 |
| F2 | 完整性 | 全局 | 缺错误码表 | 新增 §21 |
| F3 | 完整性 | 全局 | 缺关键页面信息架构 | 新增 §22 |
| F4 | 完整性 | 全局 | 缺 LLM 工具 schema | 新增 §23 |
| F5 | 完整性 | 全局 | 缺数据切割规则 | 新增 §24 |
| F6 | 完整性 | 全局 | 缺关键 SQL | 新增 §25 |
| F7 | 完整性 | 全局 | 缺前端路由表 | 新增 §26 |
| F8 | 完整性 | 全局 | 缺 M0 交付清单 | 新增 §27 |
| F9 | 完整性 | §6.2 audit_logs | 无字段 | 补字段 |
| F10 | 完整性 | §6.2 sim_accounts | 无 type 枚举 | 显式 enum |
| F11 | 完整性 | §8.3 风控配置 | 字段位置未定义 | 入 `strategy_versions.risk_config JSONB` |
| F12 | 完整性 | §14 FormField | 未说明 VeeValidate 集成 | 补集成方式 |
| F13 | 完整性 | §17 initdb | 脚本命名未规范 | 规范命名 |
| F14 | 完整性 | §18 验收 | 缺 DoD | 补 Definition of Done |

> 共 **15 条正确性/可行性修复** + **14 条完整性补充**，下面给出 v2 正文。

---

## 目录（v2）
1. 产品定位与设计原则
2. 总体架构
3. 技术栈与版本
4. 仓库 / 后端目录结构
5. 仓库 / 前端目录结构
6. 数据模型
7. Redis 键空间设计
8. 模块设计
9. REST API 设计
10. 实时通道设计（含 SSE）
11. LLM Agent 与 Token 统计
12. 异步任务与队列
13. 限速与反封 IP
14. 主题 / 国际化 / 前端组件库
15. 安全与多租户
16. 可观测性
17. 部署拓扑
18. 里程碑与 DoD
19. 风险与开放问题
20. 权限矩阵
21. 错误码表
22. 关键页面信息架构
23. LLM 工具 Schema 示例
24. 数据切割规则
25. 关键 SQL 片段
26. 前端路由表
27. M0 交付清单

---

## 1. 产品定位与设计原则
（与 v1 一致）

## 2. 总体架构
（与 v1 一致）

## 3. 技术栈与版本

| 层 | 选型 | 版本 |
|---|---|---|
| 前端框架 | Vue 3 + TS（script setup） | 3.5+ |
| 构建 | Vite | 5+ |
| 样式 | Tailwind CSS（darkMode: 'class'） | 3.4+ |
| 状态 | Pinia + pinia-plugin-persistedstate | 2+ |
| 路由 | Vue Router | 4+ |
| 图表 | ECharts 5 + lightweight-charts 4 + Plotly.js | — |
| 表单 | VeeValidate 4 + Zod | — |
| HTTP | Axios（拦截器 + 指数重试） | — |
| 后端 | Python 3.11 + FastAPI 0.115+ + uvicorn | — |
| ORM | SQLAlchemy 2.x async + Alembic | — |
| DB | PostgreSQL 16 + TimescaleDB 2.x + pgvector 0.7+ | — |
| 缓存/MQ | Redis 7 | — |
| 队列 | ARQ（默认）/ Celery 降级 | — |
| LLM Agent | AgentScope（最新 stable） | — |
| 数据/回测 | pandas / numpy / vectorbt / numba / ta-lib | — |
| ML | **PyTorch（可选 [ml] extras）+ LightGBM（[ml]）+ scikit-learn（[ml]）** | — |
| 行情 | ccxt + 自研 Binance REST/WS 适配 | — |
| 配置 | pydantic-settings | — |
| 日志 | structlog | — |
| 监控 | prometheus-client + OpenTelemetry | — |
| 测试 | pytest + httpx + pytest-asyncio + vitest | — |
| 部署 | Docker + docker-compose | — |

> 关键变更：**ML 相关依赖放入可选 extras**，默认 `pip install .` 不下载 torch，确保轻量。

## 4. 后端目录结构
（与 v1 一致，补充 sandbox 子目录）

```
agent/
└── sandbox/
    ├── runner.py          # 子进程执行因子代码
    ├── whitelist.py       # AST 算子白名单
    └── limits.py          # 内存 / CPU / 时长限制
```

## 5. 前端目录结构
（与 v1 一致）

## 6. 数据模型

### 6.1 TimescaleDB 扩展
```sql
CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS vector;
```

### 6.2 表清单（修订）

| 表 | 类型 | 说明 | 关键变更 |
|---|---|---|---|
| `symbols` | 普通表 | 交易对元数据 | — |
| `klines` | hypertable | K 线主表 | chunk 7d |
| `tickers_24h` | **hypertable** | 24h ticker | chunk 1d（**修复 C1**） |
| `download_jobs` | 普通表 | 下载任务 | — |
| `scheduled_jobs` | 普通表 | 定时任务（cron + spec） | — |
| `strategies` | 普通表 | 策略 | + `embedding vector(1024)` |
| `strategy_versions` | 普通表 | 策略代码版本 | + `risk_config JSONB`（**修复 F11**） |
| `backtests` | 普通表 | 回测运行记录 | + `equity_curve_id` |
| `equity_curves` | hypertable | 资金曲线（高频） | chunk 7d（**新增**） |
| `backtest_trades` | 普通表 | 回测成交 | — |
| `backtest_positions` | hypertable | 回测持仓快照 | chunk 7d |
| `backtest_metrics` | 普通表 | 绩效快照 | metric_key+run_id 唯一 |
| `factor_library` | 普通表 | 因子元数据 | + `embedding vector(1024)` + `feature_columns JSONB` |
| `factor_versions` | 普通表 | 因子代码版本 | — |
| `factor_values` | hypertable | 因子值 | chunk 30d |
| `factor_research_runs` | 普通表 | 因子研究运行 | + `train_metrics / test_metrics JSONB` |
| `agent_sessions` | 普通表 | Agent 会话 | + `total_tokens / total_cost_usd` |
| `agent_messages` | 普通表 | Agent 消息 | — |
| `agent_decisions` | 普通表 | LLM 决策 | — |
| `sim_accounts` | 普通表 | 模拟账户 | + `account_type ENUM('manual','strategy','llm') NOT NULL`（**修复 F10**） |
| `sim_orders` | 普通表 | 模拟订单 | — |
| `sim_fills` | 普通表 | 模拟成交 | — |
| `sim_positions` | hypertable | 模拟持仓快照 | chunk 7d |
| `llm_usage` | hypertable | LLM 用量 | chunk 1d |
| `llm_costs` | 普通表 | 模型单价 | 缓存模型维度单价 |
| `audit_logs` | 普通表 | 审计 | **补字段（F9）**：`id, ts, actor_id, action, resource_type, resource_id, before JSONB, after JSONB, ip, ua` |

### 6.3 关键 Hypertable 与索引

```sql
-- klines
SELECT create_hypertable('klines', 'open_time', chunk_time_interval => INTERVAL '7 days');
CREATE UNIQUE INDEX ux_klines ON klines (symbol, interval, open_time);
CREATE INDEX ix_klines_symbol_ts ON klines (symbol, open_time DESC);

-- tickers_24h（修复 C1）
SELECT create_hypertable('tickers_24h', 'ts', chunk_time_interval => INTERVAL '1 day');
CREATE UNIQUE INDEX ux_tickers_24h ON tickers_24h (symbol, ts);

-- factor_values
SELECT create_hypertable('factor_values', 'ts', chunk_time_interval => INTERVAL '30 days');
CREATE UNIQUE INDEX ux_factor_values ON factor_values (factor_id, symbol, ts);

-- sim_positions
SELECT create_hypertable('sim_positions', 'ts', chunk_time_interval => INTERVAL '7 days');

-- equity_curves（新增）
SELECT create_hypertable('equity_curves', 'ts', chunk_time_interval => INTERVAL '7 days');
CREATE UNIQUE INDEX ux_equity_curves ON equity_curves (run_type, run_id, ts);

-- llm_usage
SELECT create_hypertable('llm_usage', 'ts', chunk_time_interval => INTERVAL '1 day');
CREATE INDEX ix_llm_usage_model ON llm_usage (model, ts DESC);
```

### 6.4 Continuous Aggregates（修复 C10）

只有当聚合窗口是基线 interval 的整数倍时，才能用 continuous aggregate：

| 基线 interval | 可建 CA 窗口 | 备注 |
|---|---|---|
| 1m | 5m, 15m, 30m | 60=1h=60m ✅；240=4h ✅；360=6h ✅ |
| 1m | 1d | 1440m ✅ |
| 5m | 1h | 12 ✅；1d 288 ✅ |
| 15m | 1h | 4 ✅；1d 96 ✅ |
| **不可建 CA** | 2h, 8h, 12h, 3d, 1w, 1M | **查询时用 `time_bucket` 现算或建业务视图** |

```sql
CREATE MATERIALIZED VIEW klines_5m WITH (timescaledb.continuous) AS
SELECT symbol, time_bucket('5 minutes', open_time) AS bucket,
       first(open, open_time) open, max(high) high, min(low) low,
       last(close, open_time) close, sum(volume) volume
FROM klines WHERE interval='1m'
GROUP BY symbol, bucket
WITH NO DATA;
SELECT add_continuous_aggregate_policy('klines_5m',
  start_offset => INTERVAL '1 day', end_offset => INTERVAL '1 hour',
  schedule_interval => INTERVAL '5 minutes');
```

### 6.5 pgvector 用法（修复 C13）
- 维度选 **`vector(1024)`** 作为通用默认值（足够覆盖 OpenAI 1536 以下的常用 embedding）；实际存储时若模型维度 > 1024，在迁移里按需扩列。
- 索引：`CREATE INDEX ix_factor_library_vec ON factor_library USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);`
- 数据量小（因子/策略几百到几千），ivfflat 足矣。

### 6.6 关键 ER 关系
（与 v1 一致）

---

## 7. Redis 键空间设计（修复 C2 / C6）

统一约定：**标量 → string(JSON)**；**结构 → hash**；**集合 → set/zset**。

| 键模式 | 类型 | 用途 | TTL | 说明 |
|---|---|---|---|---|
| `mq:arq:{queue}` | list | ARQ 队列 | — | `data/compute/llm/sim/analytics` |
| `ws:market:{symbol}` | pub/sub channel | 行情 fanout | — | 100ms 节流下发 |
| `ws:backtest:{job_id}` | pub/sub channel | 回测进度 | — | — |
| `ws:agent:{session_id}` | pub/sub channel | Agent 事件 | — | — |
| `cache:symbols` | string(JSON) | 交易对清单 | 1h | — |
| `cache:exchange_info` | **string(JSON)** | 交易所元信息 | 1h | 统一为 string |
| `cache:ticker:{symbol}` | string(JSON) | 单 symbol ticker | 5s | — |
| `cache:orderbook:{symbol}` | string(JSON) | 订单簿快照 | 1s | 仅初始拉取 |
| `ratelimit:binance:used:{minute}` | string(int) | 当分钟已用权重 | 90s | **固定窗口**（修复 C6） |
| `ratelimit:binance:cap` | string(int) | 软上限（默认 4500） | — | 启动时载入 |
| `lock:download:{symbol}:{interval}` | string | 分布式锁 | 任务时长 | SET NX EX |
| `lock:backtest:{strategy_id}` | string | 同一策略并发锁 | 任务时长 | 防止误起 |
| `user:session:{token}` | hash | refresh 状态 | 7d | — |
| `sim:portfolio:{account_id}` | hash | 模拟账户快速读 | 5s | — |
| `llm:rate:{provider}` | string(int) | provider RPM | 60s | — |
| `agg:llm_usage:{hour}` | hash | 小时级聚合缓存 | 70min | 给 API 用 |

---

## 8. 模块设计（修复 C3 / C4 / C7 / C11 / C12 / C14 / C15）

### 8.1 数据模块 (`data/`)
- `BinanceKlineFetcher`：限速客户端；任务 `download_klines(symbol, interval, start, end, mode=full|incremental)`
- 增量模式：每拉完一段 **立即** 更新 Redis `last_bar:{symbol}:{interval}` 与 DB 汇总（**修复 C7**）。
- 完整性校验：每段拉完后比对 `openTime + interval` 步长是否连续；不连续写 `download_jobs.gaps` JSONB 记录。

### 8.2 行情模块 (`market/`)
- 与 v1 一致；强调：`StreamHub` 解析后 publish 到 `ws:market:{symbol}`。

### 8.3 策略模块 (`strategy/`)
- 与 v1 一致。**费率默认值按交易类型区分**（**修复 C14**）：
  - spot taker: `0.1%`
  - spot maker: `0.1%`（v1.1 接 VIP 等级）
  - futures taker: `0.04%`
  - futures maker: `0.02%`
- 风控配置存于 `strategy_versions.risk_config JSONB`，结构：
  ```json
  {
    "max_position_pct": 0.3,
    "max_daily_loss_pct": 0.05,
    "max_drawdown_pct": 0.2,
    "allowed_symbols": ["BTCUSDT", "ETHUSDT"]
  }
  ```

### 8.4 因子模块 (`factor/`)
- **ML 默认模型（修复 C3）**：`pip install .[ml]` 后默认 LightGBM（回归或排序目标），高级用户可在因子研究页切到 PyTorch MLP / Transformer。
- **AI 因子沙箱（修复 C11）**：放弃 `RestrictedPython`（对 numpy/ta-lib 支持差），改用：
  1. **AST 静态白名单**：只允许 `import` 来自 `numpy/pandas/ta-lib` 的指定子模块；只允许调用白名单算子（`mean/std/rolling/corr/...`）。
  2. **子进程执行**：`multiprocessing.Process` 起 worker，`rlimit` 限制 CPU/内存/时长。
  3. **结果校验**：返回 `pd.Series` 必须与输入 index 对齐；nan 比例 ≤ 30%；shape 必须 1D。
- 流水线与 v1 一致。

### 8.5 LLM Agent 模块 (`agent/`)
- **TraderAgent 决策周期（修复 C15）**：默认 `1m`，可在 `sim_accounts.config.tick_interval` 配置；可选 5m/15m/1h。
- 决策 JSON schema：
  ```json
  {
    "action": "open" | "close" | "hold",
    "symbol": "BTCUSDT",
    "side": "long" | "short",
    "qty": 0.01,
    "leverage": 1,
    "reasoning": "string"
  }
  ```
- `RiskGuard` 在提交到执行前校验（位置上限 / 当日亏损上限 / 最大回撤 / 拉黑品种）。

### 8.6 模拟交易 (`simtrader/`)（修复 C4）
- **明确不维护订单簿撮合**，按以下规则撮合：
  - 市价单：下一根 K 线开盘价
  - 限价单：当 tick 价穿越限价时按限价成交
  - 默认含手续费 + 滑点（`slippage_bps` 默认 5）
- 订单簿数据**仅用于显示**，撮合不依赖。

### 8.7 LLM 用量统计 (`llm/usage.py`)（修复 C12）
- token 估算（仅当 provider 不返回 usage 时使用）：
  - 英文文本：`tokens ≈ len(text) / 4`
  - 中文文本：`tokens ≈ len(text) / 1.5`
  - 混合：按字符 Unicode 范围分段估算
  - 结果字段 `is_estimated=true`

---

## 9. REST API 设计（修复 C5 / C8）

> 行情实时数据走 WS；REST 的 `/market/orderbook` 明确为 **「初始拉取 + 兜底」**（修复 C5）。

| 模块 | Method | Path | 说明 |
|---|---|---|---|
| 行情 | GET | `/market/symbols` | 交易对清单 |
| 行情 | GET | `/market/tickers` | 24h ticker 全量 |
| 行情 | GET | `/market/ticker/{symbol}` | 单 symbol ticker |
| 行情 | GET | `/market/orderbook/{symbol}?limit=100` | **REST 初始/兜底** |
| 行情 | GET | `/market/trades/{symbol}?limit=50` | 最近成交 |
| K线 | GET | `/data/klines` | 查 DB 缓存的 K 线 |
| 数据 | POST | `/data/downloads` | 创建下载任务 |
| 数据 | GET | `/data/downloads` | 任务列表 |
| 数据 | GET | `/data/downloads/{id}` | 任务状态 |
| 数据 | DELETE | `/data/downloads/{id}` | 取消任务 |
| 数据 | POST | `/data/schedules` | 创建定时下载（cron） |
| 数据 | GET/PUT/DELETE | `/data/schedules/{id}` | 管理定时 |
| 策略 | CRUD | `/strategies` | 策略管理 |
| 策略 | POST | `/strategies/{id}/versions` | 新版本 |
| 策略 | POST | `/backtests` | 启动回测（异步） |
| 策略 | GET | `/backtests/{id}` | 回测详情 |
| 策略 | GET | `/backtests/{id}/equity` | 资金曲线 |
| 因子 | GET | `/factors` | 因子库 |
| 因子 | POST | `/factors/{id}/run` | 启动因子研究 |
| 因子 | GET | `/factors/{id}/runs/{run_id}` | 研究结果 |
| 因子 | POST | `/factors/search` | pgvector 相似检索 |
| Agent | POST | `/agent/sessions` | 创建会话 |
| Agent | POST | `/agent/sessions/{id}/messages` | 发消息（非流式） |
| Agent | GET | `/agent/sessions/{id}/messages/stream` | **SSE 流式**（修复 C8） |
| 模拟 | CRUD | `/sim/accounts` | 模拟账户 |
| 模拟 | POST | `/sim/accounts/{id}/orders` | 手动下单 |
| 模拟 | GET | `/sim/accounts/{id}/pnl` | 净值曲线 |
| 模拟 | GET | `/sim/comparison` | 三路对比 |
| LLM | GET | `/llm/usage/summary` | Token 用量汇总 |
| LLM | GET | `/llm/usage/timeseries` | 时序 |
| LLM | GET | `/llm/usage/by-caller` | 按调用方拆分 |
| Auth | POST | `/auth/login` | 登录 |
| Auth | POST | `/auth/refresh` | 刷新 token |
| Auth | POST | `/auth/logout` | 登出 |

---

## 10. 实时通道设计（修复 C8 / C9）

| 通道 | 协议 | 消息 |
|---|---|---|
| `/ws/market?symbols=...` | FastAPI WS | 行情推送 |
| `/ws/backtest/{job_id}` | FastAPI WS | 回测进度 |
| `/ws/sim/{account_id}` | FastAPI WS | 模拟账户推送 |
| `/agent/sessions/{id}/messages/stream` | **SSE** | Agent 流式输出（修复 C8） |
| `/backtests/{id}/progress` | SSE | 回测进度流（与 WS 二选一即可，SSE 优先） |

**WS 鉴权（修复 C9）**：
- 客户端连接后 **5 秒内** 发送首包 `{type:"auth", token:"..."}`
- 服务端校验，失败主动 close。
- 不通过 query 传 token，避免日志泄漏。
- 高安全场景可叠加 `Sec-WebSocket-Protocol` 头。

---

## 11. LLM Agent 与 Token 统计
（与 v1 一致；§23 给出具体工具 schema）

---

## 12. 异步任务与队列

| 任务 | 队列 | 超时 | 重试 |
|---|---|---|---|
| `download_klines` | `data` | 30m | 3 |
| `sync_incremental_klines` | `data` | 10m | 3 |
| `run_backtest` | `compute` | 30m | 1 |
| `run_factor_research` | `compute` | 60m | 1 |
| `train_ml_model` | `compute` | 120m | 1 |
| `llm_agent_run` | `llm` | 15m | 2 |
| `sim_trader_tick` | `sim` | 60s | ∞ |
| `aggregate_llm_usage` | `analytics` | 5m | 3 |

调度器（`beat`）负责：`sync_incremental_klines` 每 5 分钟；`aggregate_llm_usage` 每 5 分钟；`download_scheduled_klines` 按 cron。

---

## 13. 限速与反封 IP（修复 C6）

- **限速算法**：固定窗口（`INCR key, EXPIRE 60`）。当已用 > 软上限时 sleep 到下一分钟。
- 与币安 header 同步：每请求读 `X-MBX-USED-WEIGHT-1m`，若本地计数与 header 偏差 > 10%，以 header 为准并重置本地计数。
- **熔断**：429/418 → BreakerOpen 60s。
- **预留 IP 轮换接口**：`ExchangeAdapter` 可注入 proxy pool。

---

## 14. 主题 / 国际化 / 前端组件库（修复 F12）

- **VeeValidate 集成**：`<FormField name="..." label="..." :rules="...">` 内部包 `<Field as="...">`，错误自动显示。
- **主题**：CSS 变量定义在 `styles/theme.css`，明暗切换只改 class。
- **持久化**：`pinia-plugin-persistedstate` 把 `theme` 写 localStorage；启动时读 OS preference（`prefers-color-scheme`）。

---

## 15. 安全与多租户
（与 v1 一致；§20 权限矩阵补充）

---

## 16. 可观测性
（与 v1 一致）

---

## 17. 部署拓扑（修复 F13）

`docker/initdb/` 脚本规范命名：
- `00-extensions.sql`：`CREATE EXTENSION`
- `10-roles.sql`：角色与权限
- `20-hypertables.sql`：hypertable 与索引
- `30-continuous-aggregates.sql`：CA 与刷新策略
- `99-seed.sql`：种子数据（币种、模型单价）

---

## 18. 里程碑与 Definition of Done（修复 F14）

| 里程碑 | 内容 | DoD |
|---|---|---|
| **M0 设计+骨架** | 本文档 + 后端骨架 + DB 初始化 + 中间件 + 「看板拉 ticker」demo | `docker-compose up` → 前端看板可见；`/health` 200；`/api/v1/market/tickers` 返回数据；README 一键跑 |
| M1 数据闭环 | K 线下载 + 定时 + 增量 + CA + 数据看板 | 能拉 20 币 × 1m × 1y，断点续传，CA 5m/15m/1h 可用 |
| M2 策略 / 因子 v1 | 事件引擎 + vectorbt + 因子注册 + 回测指标 | 能跑均线策略并看到指标卡与资金曲线 |
| M3 Agent v1 | FactorAgent / StrategyAgent / TraderAgent + pgvector + Token 页 | 自然语言生成因子并入库；Token 页可用 |
| M4 模拟盘+三路对比 | 三账户 + 净值 + 对比 | 三账户同时跑一周，对比图清晰 |
| M5 完善 | 主题 / 移动 / 监控 / 文档 | 全部功能可用；Lighthouse 性能 ≥ 80 |

---

## 19. 风险与开放问题
（与 v1 一致）

---

## 20. 权限矩阵（F1 补充）

| 操作 | viewer | researcher | admin |
|---|---|---|---|
| 浏览看板 / 行情 | ✅ | ✅ | ✅ |
| 查询 K 线 / 因子 / 策略 | ✅ | ✅ | ✅ |
| 创建 / 修改自己的策略 | ❌ | ✅ | ✅ |
| 跑回测 | ❌ | ✅ | ✅ |
| 创建 / 修改因子 | ❌ | ✅ | ✅ |
| 创建 Agent 会话 | ❌ | ✅ | ✅ |
| 启动 LLM 模拟交易 | ❌ | ✅ | ✅ |
| 创建 / 删除其他用户 | ❌ | ❌ | ✅ |
| 系统设置 / 模型单价 | ❌ | ❌ | ✅ |

---

## 21. 错误码表（F2 补充）

| code | http | 含义 |
|---|---|---|
| `OK` | 200 | 成功 |
| `BAD_REQUEST` | 400 | 参数错误 |
| `UNAUTHORIZED` | 401 | 未登录 |
| `FORBIDDEN` | 403 | 无权限 |
| `NOT_FOUND` | 404 | 资源不存在 |
| `CONFLICT` | 409 | 资源冲突 |
| `RATE_LIMITED` | 429 | 限速（含币安限速封禁） |
| `UPSTREAM_ERROR` | 502 | 上游错误（币安 / LLM） |
| `TIMEOUT` | 504 | 超时 |
| `JOB_FAILED` | 500 | 后台任务失败（含 detail） |
| `RISK_REJECTED` | 422 | 风控拒绝（含 detail） |

---

## 22. 关键页面信息架构（F3 补充）

### 22.1 行情看板 `/dashboard`
- 顶部：搜索 + 自选星标 + 主题切换 + 用户菜单
- 左侧：分类（自选 / 主流 / 涨幅 / 跌幅 / 资金费率 / 持仓量）
- 主区：币种卡片网格（symbol / 价格 / 24h% / 量 / 资金费率）
- 卡片支持「点击进入详情」「右键加入对比」

### 22.2 行情详情 `/market/:symbol`
- 顶部标签：现货 / 合约 / 资金费率（v1.1）
- 主区：K 线（lightweight-charts）+ 指标下拉
- 右侧：订单簿（深度条）+ 最近成交（实时滚动）
- 下方：资金费率 / 多空比 / 持仓量（v1.1）

### 22.3 K 线下载 `/data/downloads`
- Step 1：选 symbol × interval × 时间范围
- Step 2：高级选项（增量 / 完整 / 并发 worker）
- Step 3：确认 → 后台跑 → 实时进度条
- 列表页：历史任务，可取消 / 重跑 / 失败诊断
- 定时任务 Tab：cron 表达式编辑器

### 22.4 策略研究 `/strategy`
- 列表：策略卡片（名称 / 标签 / 最近回测）
- 详情：版本列表 + 当前回测卡片 + 资金曲线 + 指标卡
- 编辑器：Monaco Editor（Python）+ 模板代码 + 「跑回测」按钮
- 回测详情：进度条 + 实时日志 + 资金曲线 + 指标卡 + 交易明细

### 22.5 因子研究 `/factor`
- 左侧：因子库树（按 tags 分组）
- 主区：因子描述 + 历史版本 + IC 曲线 + 因子值预览
- 研究页：选因子 + 选 symbol + 时间窗 + 模型 + 切分比例 → 一键研究
- AI 因子工坊：自然语言输入 → Agent 输出因子草稿 → 确认入库

### 22.6 Agent 实验室 `/agent`
- 左侧：会话列表（按类型分组）
- 主区：聊天 UI + 工具调用折叠卡片 + 流式输出
- 决策历史：JSON 卡片 + Reasoning 折叠 + 是否被风控拒绝

### 22.7 模拟交易 `/sim`
- 顶部：三账户卡片并排（净值 / 今日盈亏 / 持仓数）
- 主区：三路资金曲线叠加 + 基准 BTC 对比
- 下半：订单 / 成交 / 持仓三个 Tab

### 22.8 LLM 用量 `/analytics/llm`
- 顶部 KPI：今日 token / 今日 cost / 调用次数 / 平均延迟
- 趋势图：按小时 / 按天
- 分布：按模型 / 按调用方
- 明细表：可导出 CSV

---

## 23. LLM 工具 Schema 示例（F4 补充）

```python
# agent/tools/market.py
{
  "name": "get_market_snapshot",
  "description": "获取指定 symbol 的最新行情快照（含最新价、24h 涨跌、成交量、最近 5 根 1m K 线）",
  "parameters": {
    "type": "object",
    "properties": {
      "symbol": {"type": "string", "pattern": "^[A-Z0-9]+$"}
    },
    "required": ["symbol"]
  }
}

# agent/tools/backtest.py
{
  "name": "run_backtest",
  "description": "异步启动回测任务，返回 job_id",
  "parameters": {
    "type": "object",
    "properties": {
      "strategy_id": {"type": "string"},
      "version_id": {"type": "string"},
      "symbols": {"type": "array", "items": {"type": "string"}},
      "interval": {"type": "string"},
      "start": {"type": "string", "format": "date-time"},
      "end": {"type": "string", "format": "date-time"},
      "params": {"type": "object"}
    },
    "required": ["strategy_id", "version_id", "symbols", "interval", "start", "end"]
  }
}

# agent/tools/factor.py
{
  "name": "list_factors",
  "description": "按关键词或标签检索因子库",
  "parameters": {
    "type": "object",
    "properties": {
      "query": {"type": "string"},
      "tags": {"type": "array", "items": {"type": "string"}},
      "limit": {"type": "integer", "default": 20}
    }
  }
}

# agent/tools/sim.py
{
  "name": "submit_sim_decision",
  "description": "提交 LLM 决策到模拟账户，会经过 RiskGuard 校验",
  "parameters": {
    "type": "object",
    "properties": {
      "account_id": {"type": "string"},
      "decision": {
        "type": "object",
        "properties": {
          "action": {"enum": ["open", "close", "hold"]},
          "symbol": {"type": "string"},
          "side": {"enum": ["long", "short"]},
          "qty": {"type": "number"},
          "leverage": {"type": "integer", "minimum": 1, "maximum": 125},
          "reasoning": {"type": "string"}
        },
        "required": ["action"]
      }
    },
    "required": ["account_id", "decision"]
  }
}
```

---

## 24. 数据切割规则（F5 补充）

**因子 / 策略训练-回测切割**：
1. **时间序列切割**（默认）：按 `train_ratio / val_ratio / test_ratio` 在时间轴上严格按序切，**禁用随机 shuffle**。
2. **Walk-forward 滚动窗口**（可选）：训练窗长 `train_window`，测试窗长 `test_window`，步长 `step`。例如 6m 训练 / 1m 测试 / 步长 1m。
3. **Purged CV**（可选）：对有标签泄漏的因子（如未来 N 天收益），切割点前后加 `gap` 间隔。
4. **样本内 vs 样本外**：样本外必须严格位于样本内之后，且不重叠。

**回测期 vs 训练期**：默认「样本内 = 训练期 = 回测的 research 期；样本外 = 另一段时间再跑一次 forward-only 回测」。

---

## 25. 关键 SQL 片段（F6 补充）

### 25.1 K 线增量续传
```sql
-- 找最后一根
SELECT max(open_time) FROM klines
WHERE symbol = $1 AND interval = $2;
```

### 25.2 订单簿聚合（前端展示用）
```sql
-- 服务端把 bids/asks 聚合到 100 档并按价格步长聚合
SELECT price_bucket, sum(qty) AS qty
FROM (
  SELECT (price::numeric / $bucket)::bigint * $bucket AS price_bucket, qty
  FROM orderbook_snapshots
  WHERE symbol = $1 AND side = 'bid' AND ts = $2
) t
GROUP BY price_bucket
ORDER BY price_bucket DESC
LIMIT 100;
```

### 25.3 因子 IC 计算（基于次日收益）
```sql
WITH f AS (
  SELECT ts, value FROM factor_values
  WHERE factor_id = $1 AND symbol = $2 AND ts BETWEEN $start AND $end
),
r AS (
  SELECT open_time AS ts, (lead(close) OVER (ORDER BY open_time) - close)/close AS ret
  FROM klines WHERE symbol = $2 AND interval='1d' AND open_time BETWEEN $start AND $end
)
SELECT corr(f.value, r.ret) AS ic,
       corr(rank() OVER (ORDER BY f.value), rank() OVER (ORDER BY r.ret)) AS rank_ic
FROM f JOIN r USING (ts);
```

### 25.4 三路绩效对比
```sql
SELECT ts, account_id, equity
FROM equity_curves
WHERE run_type IN ('manual','strategy','llm') AND ts BETWEEN $start AND $end
ORDER BY ts;
```

---

## 26. 前端路由表（F7 补充）

```
/                       → /dashboard
/login                  登录
/dashboard              行情看板
/market/:symbol         行情详情
/data/downloads         K 线下载任务
/data/schedules         定时下载
/strategy               策略列表
/strategy/:id           策略详情
/strategy/:id/edit      策略编辑器
/strategy/:id/backtests/:rid 回测详情
/factor                 因子库
/factor/:id             因子详情
/factor/:id/research    因子研究
/factor/ai-lab          AI 因子工坊
/agent                  Agent 实验室
/agent/:sessionId       会话详情
/sim                    模拟交易总览
/sim/:accountId         单账户详情
/sim/comparison         三路对比
/analytics/llm          LLM 用量
/settings               设置（模型 / 用户 / 主题）
```

---

## 27. M0 交付清单（F8 补充）

> M0 = 本次交付范围。下列条目将作为本轮开发清单。

### 27.1 仓库与文档
- [x] `doc/01-竞品对比分析.md`
- [x] `doc/02-币安API限制与下载最佳实践.md`
- [x] `doc/03-系统设计方案-v1.md`
- [x] `doc/04-系统设计方案-v2-review.md`（本文件）
- [ ] `doc/05-接口契约.openapi.yaml`
- [ ] `README.md`

### 27.2 后端骨架
- [ ] `backend/pyproject.toml` + `[ml]` extras
- [ ] `backend/.env.example`
- [ ] `backend/src/cryptoquant/main.py`（FastAPI app 工厂 + `/health`）
- [ ] `backend/src/cryptoquant/core/`：`config / db / redis / logging / errors / deps / rate_limit`
- [ ] `backend/src/cryptoquant/api/v1/`：至少 `market.py` + `health.py`
- [ ] `backend/src/cryptoquant/exchange/binance/rest.py`（限速客户端 + ticker 拉取）
- [ ] `backend/src/cryptoquant/data/`：models + repository + schemas 占位
- [ ] `backend/src/cryptoquant/market/`：hub.py 占位（先不连 Binance WS，仅留接口）
- [ ] `backend/src/cryptoquant/job/arq_app.py` + `scheduler.py` 占位
- [ ] `backend/src/cryptoquant/schemas/` 通用响应封装
- [ ] `backend/alembic/` 初始化脚本
- [ ] `backend/tests/test_smoke.py`：启动 + ticker

### 27.3 数据库初始化
- [ ] `backend/docker/initdb/00-extensions.sql`
- [ ] `backend/docker/initdb/10-roles.sql`
- [ ] `backend/docker/initdb/20-hypertables.sql`
- [ ] `backend/docker/initdb/30-continuous-aggregates.sql`
- [ ] `backend/docker/initdb/99-seed.sql`（币种 + 模型单价占位）

### 27.4 中间件
- [ ] `docker-compose.yml`：`postgres / redis / gateway / worker / stream_hub / beat`
- [ ] `backend/Dockerfile`

### 27.5 验证 DoD
- [ ] `docker-compose up -d` → `curl http://localhost:8000/health` 200
- [ ] `curl http://localhost:8000/api/v1/market/tickers` 返回 Binance 数据
- [ ] 前端 `/dashboard`（M0 用最简单的卡片列表展示 ticker）可见
- [ ] README 一键跑说明齐全

---

> v2 完。下一步：搭建 M0 后端骨架。
