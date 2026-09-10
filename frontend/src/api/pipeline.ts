import http from './http'

export interface PipelineThresholds {
  oos_rank_ic?: number
  spread?: number
  hit_rate?: number
  overfit_gap?: number
  total_return?: number
  sharpe?: number
  max_drawdown?: number
  num_trades?: number
  excellent_sharpe?: number
  excellent_max_drawdown?: number
}

export interface PipelineStartPayload {
  name?: string
  objective?: string
  llm_model_id?: string | null
  research_symbols: string[]
  research_interval: string
  research_start: string
  research_end: string
  backtest_symbol?: string | null
  backtest_interval?: string | null
  backtest_start: string
  backtest_end: string
  factor_count_per_round?: number
  max_pick?: number
  max_rounds?: number
  model?: 'ridge' | 'gbdt'
  horizon?: number
  test_ratio?: number
  alpha?: number
  max_corr?: number | null
  winsor?: number | null
  cost_bps?: number
  fine_interval?: string | null
  agg_features?: string[]
  learning_rate?: number
  n_estimators?: number
  max_depth?: number
  num_leaves?: number
  subsample?: number
  colsample_bytree?: number
  thresholds?: PipelineThresholds
  stop_on_excellent?: boolean
  save_quality_factors?: boolean
}

export interface PipelineRun {
  id: string
  name: string
  config: PipelineStartPayload
  status: 'pending' | 'running' | 'succeeded' | 'finished' | 'failed' | 'stopped'
  current_round: number
  factor_pool: string[]
  stall_count: number
  best_model_id: string | null
  best_metrics: Record<string, number> | null
  error: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
}

export interface PipelineDiagnosis {
  grade: 'ok' | 'warn' | 'bad'
  root_cause: 'none' | 'params' | 'factor'
  review: string
  advice: string
  backtest_review: string
  keep: string[]
  drop: Array<{ code: string; name: string; reason: string }>
  params: Record<string, unknown>
  fallback: boolean
  checks?: Record<string, boolean>
  param_attempts?: number
  param_retry_limit?: number
  params_applied?: Record<string, unknown>
}

export interface PipelineFactor {
  code: string
  name: string
  description: string
  expression: string
}

export interface PipelineRound {
  id: string
  pipeline_run_id: string
  round_no: number
  phase: string
  status: 'pending' | 'running' | 'done' | 'failed'
  factors_generated: Array<PipelineFactor> | null
  factors_picked: Array<PipelineFactor> | null
  train_result: {
    oos_ic?: number | null
    oos_rank_ic?: number | null
    train_ic?: number | null
    overfit_gap?: number | null
    hit_rate?: number | null
    spread?: number | null
    net_spread?: number | null
    degenerate?: boolean
    n_features_kept?: number
    features?: Array<{ code: string; name?: string; coef: number; ic_oos?: number | null; rank_ic_oos?: number | null }>
    message?: string
  } | null
  backtest_summary: {
    metrics?: {
      total_return?: number
      annual_return?: number
      sharpe?: number
      max_drawdown?: number
      win_rate?: number
      num_trades?: number
    }
    equity_points?: Array<{ ts: string; equity: number }>
    summary?: { final_equity?: number | null; bars?: number }
  } | null
  diagnosis: PipelineDiagnosis | null
  model_id: string | null
  error: string | null
  created_at: string
}

export async function startPipeline(payload: PipelineStartPayload): Promise<{ run_id: string; status: string }> {
  const { data } = await http.post<{ data: { run_id: string; status: string } }>('/pipeline/start', payload)
  return data.data
}

export async function stopPipeline(id: string): Promise<{ stopped: boolean }> {
  const { data } = await http.post<{ data: { stopped: boolean } }>(`/pipeline/${id}/stop`)
  return data.data
}

export async function deletePipeline(id: string): Promise<{ deleted: boolean }> {
  const { data } = await http.delete<{ data: { deleted: boolean } }>(`/pipeline/${id}`)
  return data.data
}

export async function listPipelines(): Promise<PipelineRun[]> {
  const { data } = await http.get<{ data: PipelineRun[] }>('/pipeline')
  return data.data ?? []
}

export async function getPipeline(id: string): Promise<{ run: PipelineRun; rounds: PipelineRound[] }> {
  const { data } = await http.get<{ data: { run: PipelineRun; rounds: PipelineRound[] } }>(`/pipeline/${id}`)
  return data.data
}
