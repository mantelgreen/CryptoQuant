import http from './http'

export interface DownloadJob {
  id: string
  symbol: string
  interval: string
  start_time: string
  end_time: string
  mode: string
  status: 'pending' | 'running' | 'succeeded' | 'failed'
  downloaded_bars: number | null
  total_bars: number | null
  gaps: unknown[] | null
  error: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
}

export interface Schedule {
  id: string
  name: string
  cron: string
  symbols: string[]
  interval: string
  enabled: boolean
  last_run_at: string | null
  next_run_at: string | null
  created_at: string
}

export interface KlineBar {
  symbol: string
  interval: string
  open_time: string
  open: number
  high: number
  low: number
  close: number
  volume: number
  quote_volume: number | null
}

export interface KlineCoverage {
  symbol: string
  interval: string
  count: number
  min_time: string
  max_time: string
}

export async function listDownloads(): Promise<DownloadJob[]> {
  const { data } = await http.get<{ data: DownloadJob[] }>('/data/downloads')
  return data.data ?? []
}

export async function getDownload(id: string): Promise<DownloadJob | null> {
  const { data } = await http.get<{ data: DownloadJob | null }>(`/data/downloads/${id}`)
  return data.data
}

export async function createDownload(params: {
  symbol: string
  interval: string
  start: string
  end: string
  mode?: 'full' | 'incremental'
}) {
  const { data } = await http.post<{ data: { job_id: string; status: string } }>(
    '/data/downloads',
    null,
    { params },
  )
  return data.data
}

export async function listSchedules(): Promise<Schedule[]> {
  const { data } = await http.get<{ data: Schedule[] }>('/data/schedules')
  return data.data ?? []
}

export async function createSchedule(payload: {
  name: string
  cron: string
  symbols: string[]
  interval: string
  enabled?: boolean
}) {
  const { data } = await http.post<{ data: { schedule_id: string } }>(
    '/data/schedules',
    payload,
  )
  return data.data
}

export async function deleteSchedule(id: string) {
  const { data } = await http.delete<{ data: { deleted: boolean } }>(`/data/schedules/${id}`)
  return data.data
}

export async function fetchKlines(params: {
  symbol: string
  interval: string
  start: string
  end: string
  limit?: number
}): Promise<KlineBar[]> {
  const { data } = await http.get<{ data: KlineBar[] }>('/data/klines', { params })
  return data.data ?? []
}

export async function fetchCoverage(symbol: string): Promise<KlineCoverage[]> {
  const { data } = await http.get<{ data: KlineCoverage[] }>('/data/coverage', {
    params: { symbol },
  })
  return data.data ?? []
}
