import http from './http'

export interface ParamSpec {
  type: 'int' | 'float' | 'number' | 'string'
  default?: number | string
  min?: number
  max?: number
}

export interface TemplateMeta {
  title: string
  description: string
  logic?: string
  long_short?: boolean
  params_schema?: Record<string, ParamSpec>
}

export interface Strategy {
  id: string
  name: string
  description: string | null
  tags: string[]
  created_at: string
  updated_at?: string | null
}

export interface StrategyVersion {
  id: string
  strategy_id: string
  version: number
  code: string
  params: Record<string, unknown>
  risk_config: Record<string, unknown>
  created_at: string
}

export interface BacktestMetrics {
  total_return?: number
  annual_return?: number
  volatility?: number
  sharpe?: number
  sortino?: number
  calmar?: number
  max_drawdown?: number
  win_rate?: number
  profit_factor?: number
  num_trades?: number
  turnover?: number
  benchmark_return?: number
  alpha?: number
  beta?: number
}

export interface BacktestSummary {
  template_id?: string
  symbol?: string
  interval?: string
  final_equity?: number
  bars?: number
  metrics?: BacktestMetrics
}

export interface Backtest {
  id: string
  strategy_id: string
  version_id: string
  symbols: string[]
  interval: string
  start_time: string
  end_time: string
  params: Record<string, unknown>
  status: 'queued' | 'pending' | 'running' | 'succeeded' | 'failed'
  progress_pct: number
  stage: string | null
  summary: BacktestSummary | null
  error: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
}

export interface EquityPoint {
  run_type: string
  run_id: string
  ts: string
  equity: number
}

export interface Trade {
  id: string
  backtest_id: string
  ts: string
  symbol: string
  side: 'buy' | 'sell'
  price: number
  qty: number
  fee: number
  pnl: number
}

export interface BacktestDetail {
  backtest: Backtest
  equity: EquityPoint[]
  trades: Trade[]
}

export async function listTemplates(): Promise<Record<string, TemplateMeta>> {
  const { data } = await http.get<{ data: Record<string, TemplateMeta> }>('/strategy/templates')
  return data.data ?? {}
}

export async function listStrategies(): Promise<Strategy[]> {
  const { data } = await http.get<{ data: Strategy[] }>('/strategy')
  return data.data ?? []
}

export async function createStrategy(payload: {
  name: string
  description?: string
  tags?: string[]
}): Promise<{ strategy_id: string }> {
  const { data } = await http.post<{ data: { strategy_id: string } }>('/strategy', payload)
  return data.data
}

export async function getStrategy(id: string): Promise<{
  strategy: Strategy
  versions: StrategyVersion[]
}> {
  const { data } = await http.get<{ data: { strategy: Strategy; versions: StrategyVersion[] } }>(
    `/strategy/${id}`,
  )
  return data.data
}

export async function createVersion(
  strategyId: string,
  payload: { template_id: string; code?: string; params?: Record<string, unknown> },
): Promise<{ version_id: string }> {
  const { data } = await http.post<{ data: { version_id: string } }>(
    `/strategy/${strategyId}/versions`,
    payload,
  )
  return data.data
}

export async function runBacktest(payload: {
  strategy_id: string
  version_id?: string | null
  symbol: string
  interval: string
  start: string
  end: string
  params?: Record<string, unknown>
}): Promise<{ backtest_id: string; status: string }> {
  const { data } = await http.post<{ data: { backtest_id: string; status: string } }>(
    '/strategy/backtests',
    payload,
  )
  return data.data
}

export async function listBacktests(): Promise<Backtest[]> {
  const { data } = await http.get<{ data: Backtest[] }>('/strategy/backtests')
  return data.data ?? []
}

export async function getBacktest(id: string): Promise<BacktestDetail> {
  const { data } = await http.get<{ data: BacktestDetail }>(`/strategy/backtests/${id}`)
  return data.data
}
