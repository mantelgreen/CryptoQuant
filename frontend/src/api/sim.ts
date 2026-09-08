import http from './http'

export interface SimAccount {
  id: string
  name: string
  account_type: string
  config: Record<string, unknown>
  initial_cash: number
  current_cash: number
  created_at: string
  updated_at: string
}

export interface SimPosition {
  symbol: string
  qty: number
  avg: number
  mark: number
  value: number
  unrealized_pnl: number
}

export interface SimOrder {
  id: string
  symbol: string
  side: 'buy' | 'sell'
  order_type: string
  qty: number
  price: number
  filled_price: number
  fee: number
  status: string
  created_at: string
  filled_at: string
}

export interface SimDetail {
  account: SimAccount
  positions: SimPosition[]
  orders: SimOrder[]
  equity: Array<{ run_type: string; run_id: string; ts: string; equity: number }>
  total_equity: number
}

export async function createAccount(name: string): Promise<SimAccount> {
  const { data } = await http.post<{ data: SimAccount }>('/sim/accounts', { name })
  return data.data
}
export async function listAccounts(): Promise<SimAccount[]> {
  const { data } = await http.get<{ data: SimAccount[] }>('/sim/accounts')
  return data.data ?? []
}
export async function getAccount(id: string): Promise<SimDetail> {
  const { data } = await http.get<{ data: SimDetail }>(`/sim/accounts/${id}`)
  return data.data
}
export async function placeOrder(
  id: string,
  payload: { symbol: string; side: 'buy' | 'sell'; qty: number },
): Promise<{
  order_id: string
  fill_price: number
  fee: number
  cash: number
  equity: number
}> {
  const { data } = await http.post<{ data: { order_id: string; fill_price: number; fee: number; cash: number; equity: number } }>(
    `/sim/accounts/${id}/orders`,
    payload,
  )
  return data.data
}

export interface SimOverviewRow {
  id: string
  name: string
  account_type: string
  initial_cash: number
  current_cash: number
  last_equity: number | null
  orders: number
  curve_points: number
}
export async function fetchOverview(): Promise<SimOverviewRow[]> {
  const { data } = await http.get<{ data: SimOverviewRow[] }>('/sim/overview')
  return data.data ?? []
}

export interface SimActivityItem {
  account_id: string
  account_name: string
  account_type: string
  side: 'buy' | 'sell'
  qty: number
  price: number
  fee: number
  created_at: string
}
export async function fetchSymbolActivity(symbol: string): Promise<SimActivityItem[]> {
  const { data } = await http.get<{ data: { items: SimActivityItem[] } }>('/sim/activity', {
    params: { symbol },
  })
  return data.data?.items ?? []
}

export interface AutoTickResult {
  action: 'open' | 'close' | 'hold' | 'skip' | 'error'
  score?: number
  message?: string
  qty?: number
  order?: { fill_price: number; fee: number; cash: number; equity: number }
}
export async function createAutoAccount(payload: {
  name: string
  symbol: string
  interval: string
  model_id: string
  enter?: number
  exit?: number
}): Promise<SimAccount> {
  const { data } = await http.post<{ data: SimAccount }>('/sim/auto/accounts', payload)
  return data.data
}
export async function tickAutoAccount(id: string): Promise<AutoTickResult> {
  const { data } = await http.post<{ data: AutoTickResult }>(`/sim/auto/${id}/tick`)
  return data.data
}
export interface CompGroup {
  type: string
  label: string
  accounts: Array<{ id: string; name: string }>
  latest: number | null
  points: Array<{ ts: string; value: number }>
}
export async function fetchComparison(): Promise<CompGroup[]> {
  const { data } = await http.get<{ data: CompGroup[] }>('/sim/comparison')
  return data.data ?? []
}
