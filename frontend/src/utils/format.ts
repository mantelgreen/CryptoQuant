/** 展示层通用：数字 / 时间 / 状态 的格式化与字典 */

export const MAJOR_SYMBOLS = [
  'BTCUSDT',
  'ETHUSDT',
  'BNBUSDT',
  'SOLUSDT',
  'XRPUSDT',
  'ADAUSDT',
  'DOGEUSDT',
  'AVAXUSDT',
  'LINKUSDT',
  'DOTUSDT',
  'TRXUSDT',
  'MATICUSDT',
  'LTCUSDT',
  'SHIBUSDT',
  'UNIUSDT',
] as const

export const INTERVALS = ['1m', '5m', '15m', '30m', '1h', '2h', '4h', '6h', '12h', '1d'] as const

// 训练/研究/回测/流水线可选的目标粒度：排除过重的 1m/5m。
// 注意：1m/5m 仍保留在 INTERVALS 中，供「K 线下载/定时任务」及「细粒度增强」底库使用。
export const RESEARCH_INTERVALS = INTERVALS.filter((i) => i !== '1m' && i !== '5m')

export const MAX_ML_FEATURES = 12

export type Interval = (typeof INTERVALS)[number]

export const RANGE_PRESETS = [
  { key: '1d', label: '24H', days: 1 },
  { key: '7d', label: '7 天', days: 7 },
  { key: '30d', label: '30 天', days: 30 },
  { key: '90d', label: '90 天', days: 90 },
  { key: '365d', label: '1 年', days: 365 },
] as const

/** 去掉 USDT 等报价后缀，展示基础币种 */
export function baseOf(symbol: string): string {
  return symbol.replace(/(USDT|BUSD|USDC|BTC|ETH)$/, '')
}

/** 带符号百分比，如 +1.24% */
export function fmtPct(v: number | string | null | undefined, digits = 2): string {
  const n = Number(v)
  if (!Number.isFinite(n)) return '—'
  const s = n >= 0 ? '+' : ''
  return `${s}${n.toFixed(digits)}%`
}

/** 带符号绝对变化 */
export function fmtSigned(v: number | string | null | undefined, digits = 2): string {
  const n = Number(v)
  if (!Number.isFinite(n)) return '—'
  const s = n >= 0 ? '+' : ''
  return `${s}${n.toFixed(digits)}`
}

export function cls(v: number | string | null | undefined): string {
  const n = Number(v)
  if (!Number.isFinite(n) || n === 0) return 'num-flat'
  return n > 0 ? 'num-up' : 'num-down'
}

/** 根据价格量级选择小数位 */
export function priceDecimals(price: number): number {
  if (!Number.isFinite(price)) return 2
  const a = Math.abs(price)
  if (a >= 1000) return 2
  if (a >= 1) return 4
  if (a >= 0.01) return 6
  return 8
}

export function fmtPrice(v: number | string | null | undefined): string {
  const n = Number(v)
  if (!Number.isFinite(n)) return '—'
  return n.toLocaleString('en-US', {
    minimumFractionDigits: priceDecimals(n),
    maximumFractionDigits: priceDecimals(n),
  })
}

export function fmtMoney(v: number | string | null | undefined, digits = 2): string {
  const n = Number(v)
  if (!Number.isFinite(n)) return '—'
  return n.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

/** 科学/超大数缩写：1.23M */
export function fmtCompact(v: number | string | null | undefined): string {
  const n = Number(v)
  if (!Number.isFinite(n)) return '—'
  const abs = Math.abs(n)
  if (abs >= 1e12) return (n / 1e12).toFixed(2) + 'T'
  if (abs >= 1e9) return (n / 1e9).toFixed(2) + 'B'
  if (abs >= 1e6) return (n / 1e6).toFixed(2) + 'M'
  if (abs >= 1e3) return (n / 1e3).toFixed(2) + 'K'
  return n.toFixed(0)
}

export function fmtTime(iso: string | null | undefined, withSeconds = false): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  const p = (x: number) => String(x).padStart(2, '0')
  const base = `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
  return withSeconds ? `${base}:${p(d.getSeconds())}` : base
}

/** 带年份的完整时间：YYYY-MM-DD HH:mm（用于跨年份的区间展示） */
export function fmtStamp(iso: string | null | undefined, withSeconds = false): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  const p = (x: number) => String(x).padStart(2, '0')
  const base = `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
  return withSeconds ? `${base}:${p(d.getSeconds())}` : base
}

export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso).getTime()
  if (Number.isNaN(d)) return '—'
  const diff = Date.now() - d
  const m = Math.floor(diff / 60000)
  if (m < 1) return '刚刚'
  if (m < 60) return `${m} 分钟前`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h} 小时前`
  const days = Math.floor(h / 24)
  if (days < 30) return `${days} 天前`
  return fmtTime(iso)
}

export const JOB_STATUS: Record<string, { label: string; tone: string }> = {
  pending: { label: '排队中', tone: 'tone-neutral' },
  queued: { label: '排队中', tone: 'tone-neutral' },
  running: { label: '进行中', tone: 'tone-primary' },
  succeeded: { label: '已完成', tone: 'tone-up' },
  failed: { label: '失败', tone: 'tone-down' },
  cancelled: { label: '已取消', tone: 'tone-neutral' },
}

export function jobMeta(status: string): { label: string; tone: string } {
  return JOB_STATUS[status] ?? { label: status, tone: 'tone-neutral' }
}

export function intervalLabel(i: string): string {
  const map: Record<string, string> = {
    '1m': '1 分钟',
    '3m': '3 分钟',
    '5m': '5 分钟',
    '15m': '15 分钟',
    '30m': '30 分钟',
    '1h': '1 小时',
    '2h': '2 小时',
    '4h': '4 小时',
    '6h': '6 小时',
    '12h': '12 小时',
    '1d': '1 天',
  }
  return map[i] ?? i
}

export function todayUtcStart(): Date {
  const d = new Date()
  d.setHours(0, 0, 0, 0)
  return d
}
