import http from './http'

export interface LlmUsageAgg {
  calls: number
  tokens: number
  cost: number
}
export interface LlmOverview {
  today: LlmUsageAgg
  total: LlmUsageAgg
  by_model: Array<{ provider: string; model: string; calls: number; tokens: number; cost: number; latency_ms: number }>
  by_caller: Array<{ caller: string; calls: number; tokens: number }>
}
export interface LlmTsPoint {
  day: string
  calls: number
  tokens: number
  cost: number
}
export async function fetchLlmOverview(): Promise<LlmOverview> {
  const { data } = await http.get<{ data: LlmOverview }>('/analytics/llm/overview')
  return data.data
}
export async function fetchLlmTimeseries(days = 30): Promise<LlmTsPoint[]> {
  const { data } = await http.get<{ data: LlmTsPoint[] }>('/analytics/llm/timeseries', {
    params: { days },
  })
  return data.data ?? []
}
