import http from './http'

export interface FactorDraft {
  name: string
  description: string
  code: string
  model: string
  provider?: string
  raw?: string
}
export async function generateFactorDraft(prompt: string, modelId?: string): Promise<FactorDraft> {
  const { data } = await http.post<{ data: FactorDraft }>(
    '/agent/factor-draft',
    { prompt, model_id: modelId },
    { timeout: 240_000 },
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
    { timeout: 240_000 },
  )
  return data.data
}
