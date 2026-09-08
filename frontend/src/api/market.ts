import http from './http'

export interface Ticker {
  symbol: string
  priceChange: string
  priceChangePercent: string
  weightedAvgPrice: string
  prevClosePrice: string
  lastPrice: string
  lastQty: string
  bidPrice: string
  askPrice: string
  openPrice: string
  highPrice: string
  lowPrice: string
  volume: string
  quoteVolume: string
  openTime: number
  closeTime: number
  count: number
}

export async function fetchTickers(symbol?: string): Promise<Ticker[]> {
  const { data } = await http.get<{ code: string; data: Ticker[] }>('/market/tickers', {
    params: symbol ? { symbol } : {},
  })
  return data.data ?? []
}

export async function fetchTicker(symbol: string): Promise<Ticker | null> {
  const { data } = await http.get<{ code: string; data: Ticker | Ticker[] }>('/market/tickers', {
    params: { symbol },
  })
  const d = data.data
  if (Array.isArray(d)) return d[0] ?? null
  return d
}

export interface DepthLevel {
  price: number
  qty: number
}

export async function fetchOrderbook(symbol: string, limit = 100): Promise<{ bids: DepthLevel[]; asks: DepthLevel[] }> {
  const { data } = await http.get<{ code: string; data: { bids: string[][]; asks: string[][] } }>(
    `/market/orderbook/${symbol}`,
    { params: { limit } },
  )
  const d = data.data
  return {
    bids: (d.bids ?? []).map((r) => ({ price: Number(r[0]), qty: Number(r[1]) })),
    asks: (d.asks ?? []).map((r) => ({ price: Number(r[0]), qty: Number(r[1]) })),
  }
}

export async function fetchKlines(symbol: string, interval: string, limit = 500) {
  const { data } = await http.get<{ code: string; data: unknown[] }>('/market/klines', {
    params: { symbol, interval, limit },
  })
  return data.data ?? []
}
