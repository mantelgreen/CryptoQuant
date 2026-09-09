import http from './http'

export interface FactorDraft {
  name: string
  description: string
  code: string
  model: string
  provider?: string
  raw?: string
  verified?: boolean
  verify_errors?: string[]
  verify_warnings?: string[]
}
export async function generateFactorDraft(prompt: string, modelId?: string): Promise<FactorDraft> {
  const { data } = await http.post<{ data: FactorDraft }>(
    '/agent/factor-draft',
    { prompt, model_id: modelId },
    { timeout: 320_000 },
  )
  return data.data
}

export interface FactorIdea {
  idea: string
  model: string
  provider?: string
}
export async function generateFactorIdea(): Promise<FactorIdea> {
  const { data } = await http.post<{ data: FactorIdea }>(
    '/agent/factor-idea',
    {},
    { timeout: 120_000 },
  )
  return data.data
}

export interface FactorPickItem {
  code: string
  name: string
  tags?: string[]
  description?: string
  ic?: number | null
  rank_ic?: number | null
  n_total?: number | null
  interval?: string | null
  decay?: Record<string, number | null>
}

export interface FactorPick {
  code: string
  reason: string
}

export interface FactorPickResult {
  model: string
  provider?: string
  picks: FactorPick[]
  fallback?: boolean
}
export async function pickFactorsByAi(
  payload: { factors: FactorPickItem[]; max_pick?: number; model_id?: string },
): Promise<FactorPickResult> {
  const { data } = await http.post<{ data: FactorPickResult }>(
    '/agent/factor-pick',
    payload,
    { timeout: 300_000 },
  )
  return data.data
}

export interface TrainReviewPayload {
  symbol?: string
  interval?: string
  horizon?: number
  model?: string
  engine?: string
  n_train?: number
  n_test?: number
  metrics?: Record<string, number | null | undefined>
  checks?: Array<{ label: string; ok: boolean | null }>
  features?: Array<{
    code: string
    name?: string
    coef?: number
    ic_oos?: number | null
    rank_ic_oos?: number | null
  }>
  removed?: Array<{ code: string; name?: string; dropped_because?: string }>
  oos_charts?: Record<string, number | null | undefined>
  learning?: {
    rounds?: number
    train_final?: number | null
    oos_final?: number | null
    oos_min?: number | null
    oos_min_round?: number | null
  }
}

export type TrainReviewGrade = 'ok' | 'warn' | 'bad'

export interface TrainReviewResult {
  model: string
  provider?: string
  grade: TrainReviewGrade
  review: string
  advice: string
  keep: string[]
  drop: Array<{ code: string; reason: string }>
  fallback?: boolean
}
export async function reviewTraining(payload: TrainReviewPayload): Promise<TrainReviewResult> {
  const { data } = await http.post<{ data: TrainReviewResult }>(
    '/agent/train-review',
    payload,
    { timeout: 320_000 },
  )
  return data.data
}

export interface BacktestReview {
  model: string
  review: string
}
export async function reviewBacktest(backtestId: string): Promise<BacktestReview> {
  const { data } = await http.post<{ data: BacktestReview }>(
    '/agent/review-backtest',
    { backtest_id: backtestId },
    { timeout: 320_000 },
  )
  return data.data
}
