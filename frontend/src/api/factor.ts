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
  hit_rate: number
  top_quintile_mean_ret: number
  bottom_quintile_mean_ret: number
  spread: number
  features: MlFeature[]
  feature_filter?: MlFeatureFilter
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
  features: Array<{ code: string; coef: number; mean: number; std: number }>
  metrics: { oos_ic?: number | null; oos_rank_ic?: number | null; hit_rate?: number | null; spread?: number | null }
  n_train: number
  n_test: number
  created_at: string
}
export async function listMlModels(): Promise<MlModelRow[]> {
  const { data } = await http.get<{ data: MlModelRow[] }>('/factors/ml/models')
  return data.data ?? []
}

export interface MlScore {
  found: boolean
  symbol: string
  interval: string
  n: number
  last_score: number | null
  points: Array<{ ts: string; score: number }>
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
