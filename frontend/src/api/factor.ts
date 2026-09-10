import http from './http'

export interface FactorMeta {
  code: string
  name: string
  description: string
  tags: string[]
  min_bars: number
  is_user?: boolean
  expression?: string
  source_model?: string | null
  created_at?: string
}

export interface FactorSymbolResult {
  symbol: string
  n: number
  ic?: number
  rank_ic?: number
  note?: string
}

export interface FactorResearch {
  code: string
  name: string
  tags: string[]
  interval: string
  found: boolean
  ic: number | null
  rank_ic: number | null
  decay: Record<string, number | null>
  n_total: number
  per_symbol: FactorSymbolResult[]
  symbols?: string[]
  start?: string
  end?: string
  message?: string
}

export interface FactorRun {
  id: string
  code: string
  symbol: string
  interval: string
  start_time: string
  end_time: string
  ic: number | null
  rank_ic: number | null
  ic_h1: number | null
  ic_h3: number | null
  ic_h5: number | null
  n: number
  created_at: string
}

export async function listFactors(): Promise<FactorMeta[]> {
  const { data } = await http.get<{ data: FactorMeta[] }>('/factors')
  return data.data ?? []
}

export async function joinFactor(payload: {
  name: string
  description?: string
  expression: string
  source_model?: string
}): Promise<{ factor_id: string }> {
  const { data } = await http.post<{ data: { factor_id: string } }>('/factors/join', payload)
  return data.data
}
export async function removeUserFactor(id: string) {
  await http.delete(`/factors/join/${id}`)
}

export interface FactorUsage {
  bound: boolean
  models: Array<{ id: string; name: string }>
  strategies: Array<{ id: string; name: string }>
}
export async function getFactorUsage(id: string): Promise<FactorUsage> {
  const { data } = await http.get<{ data: FactorUsage }>(`/factors/join/${id}/usage`)
  return data.data
}

export async function fetchLatestRuns(): Promise<FactorRun[]> {
  const { data } = await http.get<{ data: FactorRun[] }>('/factors/runs/latest')
  return data.data ?? []
}

export async function fetchRunsByCode(code: string): Promise<FactorRun[]> {
  const { data } = await http.get<{ data: FactorRun[] }>(`/factors/${code}/runs`)
  return data.data ?? []
}

export interface FactorMatrixRow {
  code: string
  name: string
  ic: number | null
  rank_ic: number | null
}
export interface FactorMatrix {
  found: boolean
  interval: string
  symbols: string[]
  factors: FactorMatrixRow[]
  corr: number[][]
  message?: string
}
export async function factorMatrix(payload: {
  codes: string[]
  symbols: string[]
  interval: string
  start: string
  end: string
}): Promise<FactorMatrix> {
  const { data } = await http.post<{ data: FactorMatrix }>('/factors/matrix', payload, { timeout: 150_000 })
  return data.data
}

export interface VerifyResult {
  found: boolean
  ok?: boolean
  expression: string
  ic: number | null
  rank_ic: number | null
  n_total: number
  message?: string
  errors?: string[]
}
export async function verifyExpression(payload: {
  expression: string
  symbol: string
  interval: string
  start: string
  end: string
}): Promise<VerifyResult> {
  const { data } = await http.post<{ data: VerifyResult }>('/factors/verify', payload)
  return data.data
}

export interface MlFeature {
  code: string
  name: string
  coef: number
  ic_oos: number | null
  rank_ic_oos: number | null
}

export interface MlFeatureFilter {
  max_corr: number | null
  removed: Array<{ code: string; name: string; dropped_because: string }>
}

export interface MlOosCharts {
  scatter: Array<{ ts: string; pred: number; actual: number }>
  deciles: Array<{ label: string; mean_actual: number; mean_pred: number; n: number }>
  nav: { ts: string[]; q1: number[]; q2: number[]; q3: number[]; q4: number[]; q5: number[] }
}

export interface MlLearning {
  rounds: number[]
  train: number[]
  oos: number[]
}

export interface MlTrainResult {
  found: boolean
  model: string
  engine: string
  interval: string
  horizon: number
  n_train: number
  n_test: number
  oos_ic: number | null
  oos_rank_ic: number | null
  train_ic: number | null
  overfit_gap: number | null
  n_features_kept: number
  hit_rate: number | null
  top_quintile_mean_ret: number
  bottom_quintile_mean_ret: number
  spread: number
  net_spread?: number | null
  cost_bps?: number
  fine_interval?: string | null
  agg_features?: string[]
  pred_nunique?: number
  pred_std?: number
  degenerate?: boolean
  features: MlFeature[]
  feature_filter?: MlFeatureFilter
  oos_charts?: MlOosCharts
  learning?: MlLearning
  message?: string
}
export async function trainMl(payload: {
  codes: string[]
  symbols: string[]
  interval: string
  start: string
  end: string
  horizon?: number
  test_ratio?: number
  alpha?: number
  model?: string
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
}): Promise<MlTrainResult> {
  const { data } = await http.post<{ data: MlTrainResult }>('/factors/ml/train', payload, { timeout: 180_000 })
  return data.data
}

export interface MlSaved {
  model_id: string
  name: string
  interval: string
  horizon: number
  features: number
  metrics: { oos_ic: number | null; oos_rank_ic: number | null; hit_rate: number | null }
}
export async function saveMl(payload: MlTrainArgs & { name: string }): Promise<MlSaved> {
  const { data } = await http.post<{ data: MlSaved }>('/factors/ml/models/save', payload)
  return data.data
}

export interface MlModelRow {
  id: string
  name: string
  model_type: string
  interval: string
  horizon: number
  symbols: string[]
  features: Array<{
    code: string
    name?: string
    coef: number
    mean: number
    std: number
    expression?: string
    aggregate?: { type: string; fine_interval?: string | null }
  }>
  metrics: { oos_ic?: number | null; oos_rank_ic?: number | null; hit_rate?: number | null; spread?: number | null }
  n_train: number
  n_test: number
  model_path?: string | null
  train_start?: string | null
  train_end?: string | null
  dataset?: {
    symbols?: string[]
    interval?: string
    start?: string
    end?: string
    test_ratio?: number
    n_train?: number
    n_test?: number
    fine_interval?: string | null
    agg_features?: string[]
    test_windows?: Array<{ symbol: string; interval: string; start: string; end: string; bars: number }>
  } | null
  created_at: string
}
export async function listMlModels(): Promise<MlModelRow[]> {
  const { data } = await http.get<{ data: MlModelRow[] }>('/factors/ml/models')
  return data.data ?? []
}

export interface MlUsage {
  bound: boolean
  strategies: Array<{ id: string; name: string }>
  sim_accounts: Array<{ id: string; name: string }>
}
export async function getMlUsage(id: string): Promise<MlUsage> {
  const { data } = await http.get<{ data: MlUsage }>(`/factors/ml/models/${id}/usage`)
  return data.data
}
export async function deleteMlModel(id: string): Promise<{ deleted: boolean; name?: string }> {
  const { data } = await http.delete<{ data: { deleted: boolean; name?: string } }>(`/factors/ml/models/${id}`)
  return data.data
}

export interface MlScore {
  found: boolean
  symbol: string
  interval: string
  n: number
  last_score: number | null
  points: Array<{ ts: string; score: number }>
  message?: string
  train_window_excluded_to?: string | null
}
export async function scoreMlModel(id: string, payload: { symbol: string; start: string; end: string }): Promise<MlScore> {
  const { data } = await http.post<{ data: MlScore }>(`/factors/ml/models/${id}/score`, payload)
  return data.data
}

export interface MlTrainArgs {
  codes: string[]
  symbols: string[]
  interval: string
  start: string
  end: string
  horizon?: number
  test_ratio?: number
  alpha?: number
  model?: string
  max_corr?: number
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
}

export async function researchFactors(payload: {
  codes: string[]
  symbols: string[]
  interval: string
  start: string
  end: string
}): Promise<FactorResearch[]> {
  const { data } = await http.post<{ data: FactorResearch[] }>('/factors/research', payload, { timeout: 150_000 })
  return data.data ?? []
}
