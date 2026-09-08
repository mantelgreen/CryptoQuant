import http from './http'

export interface LlmProvider {
  id: string
  name: string
  provider: string
  base_url: string | null
  api_key: string | null
  model: string
  temperature: number
  max_tokens: number
  enabled: boolean
  is_default: boolean
  created_at: string
  updated_at: string
}

export interface ProviderInput {
  name: string
  provider: string
  base_url?: string | null
  api_key?: string | null
  model: string
  temperature?: number
  max_tokens?: number
  enabled?: boolean
  is_default?: boolean
}

export async function listProviders(): Promise<LlmProvider[]> {
  const { data } = await http.get<{ data: LlmProvider[] }>('/settings/models')
  return data.data ?? []
}
export async function createProvider(payload: ProviderInput): Promise<LlmProvider> {
  const { data } = await http.post<{ data: LlmProvider }>('/settings/models', payload)
  return data.data
}
export async function updateProvider(id: string, payload: Partial<ProviderInput>): Promise<LlmProvider> {
  const { data } = await http.put<{ data: LlmProvider }>(`/settings/models/${id}`, payload)
  return data.data
}
export async function deleteProvider(id: string) {
  await http.delete(`/settings/models/${id}`)
}

export interface LlmTestResult {
  ok: boolean
  name: string
  model: string
  reply: string
  usage: { prompt_tokens: number; completion_tokens: number; total_tokens: number }
  latency_ms: number
}
export async function testProvider(id: string): Promise<LlmTestResult> {
  const { data } = await http.post<{ data: LlmTestResult }>(`/settings/models/${id}/test`)
  return data.data
}
