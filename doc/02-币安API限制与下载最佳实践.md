# 02 币安 API 限制与下载最佳实践

> 数据源全部走 Binance Spot / Futures 公共 REST + WebSocket。本节把官方文档关键限制、内化为本平台的硬约束，避免被封 IP。

## 1. REST 限速（来自官方文档，截至 2026）

| 接口 | 权重 | 限速窗口 | 备注 |
|---|---|---|---|
| `GET /api/v3/klines` | 2 | 每 IP 每分钟 6000 权重 | 即 3000 次/分钟 |
| `GET /api/v3/exchangeInfo` | 20 | 同上 | 启动时缓存 |
| `GET /api/v3/ticker/24hr` | 1/2 | 同上 | 单 symbol 1，全量 80 |
| `GET /api/v3/depth` | 1/5/10/20 | 同上 | limit=100→1，500→5，1000→10，5000→20 |
| `GET /api/v3/trades` | 1/5/10 | 同上 | 50/500/1000 笔 |
| WebSocket | — | 每 24h 断开 ≤ 5 分钟一次 | 单连接订阅上限 200 streams |

> 我们采用**「单进程 + 单 IP + 本地令牌桶 + 备用出口」**的模式。

## 2. K 线下载的「封 IP 红线」与对策

### 红线
1. **未分页循环拉满 1000 根**：每根算 1 次调用 → 限速。
2. **多线程裸跑**：会把令牌桶打穿。
3. **忽略 `X-MBX-USED-WEIGHT` 响应头**：等到 429 就晚了。
4. **请求间隔固定 sleep**：不可控网络抖动会破坏节奏。

### 对策（在本平台落地为 `BinanceKlineFetcher`）
- **令牌桶**：单机 4500 weight / 分钟安全水位（保留 25% 余量），基于 `aiocache` + Redis 共享计数（多机时）。
- **自适应限速**：响应头 `X-MBX-USED-WEIGHT-1m` > 4500 时，自动 sleep 到下一分钟。
- **429 / 418 处理**：指数退避（1s → 2s → 4s → … 上限 60s），并记录熔断事件。
- **分页**：按 `startTime / endTime` 切片，每片 ≤ 1000 根，时间对齐到 bar 起点。
- **本地增量**：每 symbol+interval 维护 `last_bar_ts`，重启自动续传。
- **并发上限**：协程数 ≤ 3，单机同时下载 ≤ 3 个任务。

## 3. 时间颗粒度支持矩阵

Spot K 线 interval：`1s, 1m, 3m, 5m, 15m, 30m, 1h, 2h, 4h, 6h, 8h, 12h, 1d, 3d, 1w, 1M`。  
我们后台颗粒度规范化：`1s/1m/3m/5m/15m/30m/1h/2h/4h/6h/8h/12h/1d/3d/1w/1M`，存储为 `interval` 字符串，前端按需展示。

## 4. WebSocket 节流

- 单连接订阅上限 200 streams，超过则开多连接并由后端 `MarketStreamHub` 做 fanout。
- 行情广播走 **Redis Pub/Sub**：WS Worker 把交易所消息 → Redis → 多个 WS 连接。
- 前端订单簿渲染节流：100ms；蜡烛 tick：250ms。
- 行情空闲断连检测：30s ping。

## 5. 数据一致性 & 完整性检查

- 每轮下载结束后调用「末端验证」：用 `startTime + interval*N` 对比实际 `openTime`，检测丢 bar。
- TimescaleDB 启用 `continuous_aggregates` 做 1m→5m→1h→1d 滚动聚合，避免重复下载。
- pgvector 仅用于存储「因子元信息 embedding」与「策略描述 embedding」，不做行情向量检索。

## 6. 真实场景估算（20 个主流币 × 1m 拉 1 年）

- 一年 1m bar ≈ 365×24×60 = 525,600
- 20 个币 × 525,600 / 1000（每页） = 10,512 次调用 × weight 2 = 21,024 权重
- 实际耗时约 6 分钟（含 sleep），远低于限额；多 worker 并行可压到 2 分钟。

> 这意味着只要遵守「令牌桶 + 自适应 + 指数退避」三件套，可以放心做批量下载任务。
