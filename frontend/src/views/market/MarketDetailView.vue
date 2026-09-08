<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ArrowDown, ArrowLeft, ArrowUp, RefreshCw } from '@lucide/vue'
import { fetchCoverage, fetchKlines, type KlineBar, type KlineCoverage } from '@/api/data'
import { fetchOrderbook, fetchTicker, type DepthLevel, type Ticker } from '@/api/market'
import { fetchKlines as fetchBinanceRawKlines } from '@/api/market'
import { useMarketSocket, type MarketEvent, type SubChannel } from '@/ws/marketSocket'
import { listAccounts, placeOrder, fetchSymbolActivity, type SimAccount, type SimActivityItem } from '@/api/sim'
import { useToastStore } from '@/stores/toast'
import KLineChart from '@/components/chart/KLineChart.vue'
import IntradayChart from '@/components/chart/IntradayChart.vue'
import PageHeader from '@/components/ui/PageHeader.vue'
import HelpDrawer, { type PageHelp } from '@/components/ui/HelpDrawer.vue'
import Panel from '@/components/ui/Panel.vue'
import StateView from '@/components/ui/StateView.vue'
import { cls, fmtCompact, fmtMoney, fmtPct, fmtPrice, MAJOR_SYMBOLS, fmtTime } from '@/utils/format'

const pageHelp: PageHelp = {
  title: '行情详情（实时）',
  intro: '单币种的“交易终端”视图：分时、K 线、盘口、逐笔成交四件套，价格/K线/深度全部实时。',
  terms: [
    { term: '分时图', desc: '展示“今天”每分钟的走势；价格线 + 黄色虚线=开盘以来累计均价线（成交额/成交量），紫色柱=每分钟成交量。' },
    { term: 'K 线（蜡烛图）', desc: '一根代表一个周期(interval)：开盘/最高/最低/收盘(OHLC)。每根柱体上下影线标出该周期高/低价。' },
    { term: '颗粒度 interval', desc: 'K 线周期：1m/5m/…/1d。库里只有细粒度（如 15m）时，30m/1h/1d 等粗粒度会由时序库自动按时间桶(time_bucket)聚合，无需额外下载。' },
    { term: '订单簿 / 盘口', desc: '当前买单(买入)与卖单(卖出)挂单；右列卖一为最低卖出价。盘口深度条长度=挂单量。中间价=(卖一+买一)/2，价差=卖一-买一。' },
    { term: '逐笔成交 tape', desc: '最近真实成交流；“吃单买/卖”表示主动成交方向（主动吃卖单=买入）。价格/数量实时跳动。' },
    { term: '本地历史库', desc: 'K 线历史来自本地 TimescaleDB（需先「K 线下载」），在此之上叠加币安实时流补最新一根。' },
  ],
  tips: ['切换顶部币种 chips 换标的；时间范围 24H~90 天。', '无数据显示“数据雷达”，点已有数据集自动定位到该区间。', '大周期请配合小周期看入场节奏——分时与 1m 用于当下，1h/1d 用于结构。'],
}

const route = useRoute()
const symbol = computed(() => String(route.params.symbol || 'BTCUSDT').toUpperCase())

const intervals = ['1m', '5m', '15m', '30m', '1h', '4h', '1d'] as const
const interval = ref<string>('1m')
const chartTab = ref<'intraday' | 'kline'>('kline')
const rangeDays = ref(7)
const ranges = [
  { label: '24H', days: 1 },
  { label: '7 天', days: 7 },
  { label: '30 天', days: 30 },
  { label: '90 天', days: 90 },
  { label: '1 年', days: 365 },
  { label: '2 年', days: 730 },
]

const coverage = ref<KlineCoverage[]>([])
const coverageLoading = ref(false)
const winFrom = ref<number | null>(null)
const winTo = ref<number | null>(null)

const intraday = ref<MinutePoint[]>([])
const intradayLoading = ref(false)

interface MinutePoint {
  ts: number
  price: number
  vol: number
}

function utcDayStart(now: number): number {
  const d = new Date(now)
  d.setUTCHours(0, 0, 0, 0)
  return d.getTime()
}
function inToday(ts: number): boolean {
  const now = Date.now()
  return ts >= utcDayStart(now) && ts <= now
}

const ticker = ref<Ticker | null>(null)
const bars = ref<KlineBar[]>([])
const loadingChart = ref(true)
const chartError = ref<string | null>(null)
const latestBar = ref<{ t: number; o: number; h: number; l: number; c: number } | null>(null)

const book = ref<{ bids: DepthLevel[]; asks: DepthLevel[] }>({ bids: [], asks: [] })
const bookLoading = ref(true)
const tape = ref<Array<{ time: number; price: number; qty: number; side: 'buy' | 'sell' }>>([])

const livePrice = ref<number | null>(null)
const flash = ref<'up' | 'down' | null>(null)
let flashTimer: number | null = null
const toast = useToastStore()

// —— 模拟交易台 ——
const simAccounts = ref<SimAccount[]>([])
const simSel = ref<string>('')
const simCash = ref(0)
const simSide = ref<'buy' | 'sell'>('buy')
const simQty = ref(0.01)
const simOrdering = ref(false)
const simActivity = ref<SimActivityItem[]>([])

async function loadSimPanel() {
  try {
    const list = await listAccounts()
    simAccounts.value = list
    if (!simSel.value && list.length) {
      simSel.value = list[0].id
      simCash.value = Number(list[0].current_cash)
    }
  } catch {
    simAccounts.value = []
  }
  await loadSimActivity()
}

async function loadSimActivity() {
  try {
    simActivity.value = await fetchSymbolActivity(symbol.value)
  } catch {
    simActivity.value = []
  }
}

function simPickAccount(id: string) {
  simSel.value = id
  const acct = simAccounts.value.find((a) => a.id === id)
  simCash.value = acct ? Number(acct.current_cash) : 0
}

async function submitSimOrder() {
  if (!simSel.value || simOrdering.value || !(simQty.value > 0)) return
  simOrdering.value = true
  try {
    const r = await placeOrder(simSel.value, {
      symbol: symbol.value,
      side: simSide.value,
      qty: simQty.value,
    })
    simCash.value = r.cash
    toast.success(
      simSide.value === 'buy' ? '模拟买入成交' : '模拟卖出成交',
      `${symbol.value} @ ${fmtPrice(r.fill_price)} · 净值 $${r.equity.toFixed(2)}`,
    )
    await loadSimActivity()
  } catch (e: unknown) {
    toast.error('模拟下单失败', (e as { message?: string }).message)
  } finally {
    simOrdering.value = false
  }
}

const autoTypeMeta: Record<string, { label: string; cls: string }> = {
  manual: { label: '手动', cls: 'tone-neutral' },
  strategy: { label: '策略化', cls: 'tone-primary' },
  llm: { label: 'LLM', cls: 'tone-ai' },
}

// —— 订阅频道：随 symbol / interval 自动重连重订阅 ——
const channels = computed<SubChannel[]>(() => [
  { symbol: symbol.value, kind: 'trade' },
  { symbol: symbol.value, kind: 'depth' },
  { symbol: symbol.value, kind: 'kline', interval: interval.value },
  { symbol: symbol.value, kind: 'kline', interval: '1m' },
])
const { connected } = useMarketSocket(() => channels.value, (ev: MarketEvent) => onEvent(ev))

function flashPrice(price: number) {
  const prev = livePrice.value
  livePrice.value = price
  if (prev !== null && prev !== price) {
    flash.value = price > prev ? 'up' : 'down'
    if (flashTimer) clearTimeout(flashTimer)
    flashTimer = window.setTimeout(() => (flash.value = null), 600)
  }
}

function onEvent(ev: MarketEvent) {
  if (ev.symbol !== symbol.value) return
  if (ev.type === 'trade') {
    const tr = ev.trade
    flashPrice(tr.price)
    tape.value = [{ time: tr.time, price: tr.price, qty: tr.qty, side: tr.side }, ...tape.value].slice(0, 24)
  } else if (ev.type === 'depth') {
    const d = ev.depth
    book.value = {
      bids: (d.bids ?? []).map((r) => ({ price: Number(r[0]), qty: Number(r[1]) })),
      asks: (d.asks ?? []).map((r) => ({ price: Number(r[0]), qty: Number(r[1]) })),
    }
  } else if (ev.type === 'kline') {
    const b = ev.bar
    const t = Number(b.t)
    if (inToday(t)) upsertMinute({ ts: t, price: Number(b.c), vol: Number(b.v) })
    if (ev.interval === interval.value) {
      latestBar.value = { t, o: Number(b.o), h: Number(b.h), l: Number(b.l), c: Number(b.c) }
      flashPrice(Number(b.c))
    }
  }
}

function upsertMinute(p: MinutePoint) {
  const list = intraday.value
  const i = list.findIndex((x) => x.ts === p.ts)
  const next = i >= 0 ? list.slice() : [...list]
  if (i >= 0) next[i] = p
  else next.push(p)
  next.sort((a, b) => a.ts - b.ts)
  intraday.value = next
}

const intradayAvg = computed<number[]>(() => {
  let turn = 0
  let vol = 0
  return intraday.value.map((p) => {
    turn += p.price * p.vol
    vol += p.vol
    return vol > 0 ? turn / vol : p.price
  })
})

async function seedIntraday() {
  intradayLoading.value = true
  try {
    const raw = await fetchBinanceRawKlines(symbol.value, '1m', 1000)
    const now = Date.now()
    const start = utcDayStart(now)
    const pts: MinutePoint[] = (raw as unknown[][])
      .filter((k) => Array.isArray(k) && k.length > 6)
      .map((k) => ({ ts: Number(k[0]), price: Number(k[4]), vol: Number(k[5]) }))
      .filter((p) => p.ts >= start && p.ts <= now)
      .sort((a, b) => a.ts - b.ts)
    intraday.value = pts
  } catch {
    intraday.value = []
  } finally {
    intradayLoading.value = false
  }
}

async function loadTicker() {
  ticker.value = await fetchTicker(symbol.value)
}

function ivMs(iv: string): number {
  const t: Record<string, number> = {
    '1m': 60_000, '3m': 180_000, '5m': 300_000, '15m': 900_000,
    '30m': 1_800_000, '1h': 3_600_000, '2h': 7_200_000, '4h': 14_400_000,
    '6h': 21_600_000, '12h': 43_200_000, '1d': 86_400_000, '1w': 604_800_000,
  }
  return t[iv] ?? 3_600_000
}

async function loadChart() {
  loadingChart.value = true
  chartError.value = null
  latestBar.value = null
  const end = winTo.value ? new Date(winTo.value) : new Date()
  const start = winFrom.value
    ? new Date(winFrom.value)
    : new Date(end.getTime() - rangeDays.value * 24 * 3600 * 1000)
  try {
    bars.value = await fetchKlines({
      symbol: symbol.value,
      interval: interval.value,
      start: start.toISOString(),
      end: end.toISOString(),
      limit: 1000,
    })
  } catch (e: unknown) {
    chartError.value = (e as { message?: string }).message ?? 'K 线加载失败'
    bars.value = []
  } finally {
    loadingChart.value = false
  }
}

async function loadCoverage() {
  coverageLoading.value = true
  try {
    coverage.value = await fetchCoverage(symbol.value)
  } catch {
    coverage.value = []
  } finally {
    coverageLoading.value = false
  }
}

function presetRange(days: number) {
  winFrom.value = null
  winTo.value = null
  rangeDays.value = days
  void loadChart()
}

function viewCoverage(c: KlineCoverage) {
  interval.value = c.interval
  const mn = new Date(c.min_time).getTime()
  const mx = new Date(c.max_time).getTime()
  winFrom.value = mn
  winTo.value = Math.min(mx + ivMs(c.interval) * 2, Date.now())
  void loadChart()
}

async function loadBook() {
  bookLoading.value = true
  try {
    book.value = await fetchOrderbook(symbol.value, 50)
  } finally {
    bookLoading.value = false
  }
}

watch(symbol, async () => {
  interval.value = '1m'
  rangeDays.value = 7
  winFrom.value = null
  winTo.value = null
  ticker.value = null
  tape.value = []
  coverage.value = []
  intraday.value = []
  latestBar.value = null
  simActivity.value = []
  await Promise.all([loadTicker(), loadChart(), loadBook(), loadCoverage(), seedIntraday(), loadSimActivity()])
})

onMounted(async () => {
  await Promise.all([loadTicker(), loadChart(), loadBook(), loadCoverage(), seedIntraday(), loadSimPanel()])
})
onBeforeUnmount(() => {
  if (flashTimer) clearTimeout(flashTimer)
})

// —— 盘口聚合视图 ——
const askLevels = computed(() => book.value.asks.slice(0, 12).reverse())
const bidLevels = computed(() => book.value.bids.slice(0, 12))
const maxQty = computed(() => {
  const qs = [...book.value.asks, ...book.value.bids].map((d) => d.qty)
  return Math.max(...qs, 1)
})
const spread = computed(() => {
  if (!book.value.asks.length || !book.value.bids.length) return null
  return book.value.asks[0].price - book.value.bids[0].price
})
const mid = computed(() => {
  if (!book.value.asks.length || !book.value.bids.length) return null
  return (book.value.asks[0].price + book.value.bids[0].price) / 2
})

const up = computed(() => Number(ticker.value?.priceChangePercent ?? 0) >= 0)
const displayPrice = computed(() => livePrice.value ?? Number(ticker.value?.lastPrice ?? 0))
const changePct = computed(() => {
  const last = livePrice.value
  if (last == null || !ticker.value?.openPrice) return Number(ticker.value?.priceChangePercent ?? 0)
  const open = Number(ticker.value.openPrice)
  return open > 0 ? ((last - open) / open) * 100 : 0
})

const stats = computed(() => [
  { label: '24h 最高', value: fmtPrice(ticker.value?.highPrice), cls: 'num-up' },
  { label: '24h 最低', value: fmtPrice(ticker.value?.lowPrice), cls: 'num-down' },
  { label: '24h 成交量', value: fmtCompact(ticker.value?.volume) },
  { label: '24h 成交额', value: fmtMoney(ticker.value?.quoteVolume) },
  { label: '成交笔数', value: fmtCompact(ticker.value?.count) },
])
</script>

<template>
  <div class="flex flex-col gap-6">
    <PageHeader eyebrow="Market Detail" :title="symbol">
      <template #actions>
        <HelpDrawer :help="pageHelp" />
        <router-link to="/dashboard" class="btn btn-ghost">
          <ArrowLeft :size="15" />返回看板
        </router-link>
        <span class="badge" :class="connected ? 'tone-up' : 'tone-neutral'">
          <span class="badge-dot animate-pulse" :class="connected ? 'bg-up' : ''" />
          {{ connected ? '实时' : '连接中…' }}
        </span>
      </template>
    </PageHeader>

    <div class="flex gap-2 overflow-x-auto pb-1">
      <router-link
        v-for="s in MAJOR_SYMBOLS"
        :key="s"
        :to="`/market/${s}`"
        class="chip shrink-0"
        :class="{ 'is-active': s === symbol }"
      >
        {{ s }}
      </router-link>
    </div>

    <!-- 报价卡 -->
    <section class="grid grid-cols-1 gap-3 lg:grid-cols-3">
      <div class="panel flex h-full flex-col p-5">
        <div class="flex items-center gap-2 text-xs text-subtle">
          <span class="font-semibold text-fg">{{ symbol }}</span>
          <span>USDT 现货</span>
          <span class="ml-auto font-mono">UTC {{ fmtTime(new Date().toISOString(), true) }}</span>
        </div>
        <div class="mt-3 flex flex-wrap items-baseline gap-x-3 gap-y-1.5">
          <span
            class="num text-[34px] font-semibold leading-none tracking-tight transition-colors"
            :class="flash === 'up' ? 'text-up' : flash === 'down' ? 'text-down' : 'text-fg'"
          >
            {{ fmtPrice(displayPrice) }}
          </span>
          <span class="badge" :class="up ? 'tone-up' : 'tone-down'">
            <component :is="up ? ArrowUp : ArrowDown" :size="11" />
            {{ fmtPct(changePct) }}
          </span>
        </div>
        <div class="mt-1 text-xs text-subtle">
          买一 <span class="num font-medium text-fg">{{ fmtPrice(ticker?.bidPrice) }}</span>
          <span class="mx-2">·</span>
          卖一 <span class="num font-medium text-fg">{{ fmtPrice(ticker?.askPrice) }}</span>
          <span v-if="connected" class="ml-2 text-up">实时跳动</span>
        </div>
        <div class="mt-4 grid grid-cols-2 gap-3">
          <div v-for="s in stats" :key="s.label">
            <p class="text-[10px] uppercase tracking-wider text-subtle">{{ s.label }}</p>
            <p class="num mt-0.5 text-sm font-medium" :class="s.cls ?? 'text-fg'">{{ s.value }}</p>
          </div>
        </div>
      </div>

      <!-- 订单簿 -->
      <div class="panel flex flex-col overflow-hidden lg:row-span-1">
        <div class="flex items-center gap-2 border-b border-border px-4 py-3">
          <h3 class="panel-title">订单簿</h3>
          <span v-if="mid" class="num text-xs text-subtle">
            中间价 {{ fmtPrice(mid) }} · 价差 {{ spread !== null ? fmtPrice(spread) : '—' }}
          </span>
          <span class="badge ml-auto" :class="connected ? 'tone-primary' : 'tone-neutral'">
            {{ connected ? '实时深度' : '快照' }}
          </span>
        </div>
        <div v-if="bookLoading && !bidLevels.length" class="flex-1">
          <StateView busy title="读取盘口…" />
        </div>
        <div v-else class="grid h-[300px] grid-cols-2 gap-px overflow-y-auto bg-border">
          <div class="bg-surface">
            <p class="px-3 pb-1 pt-2.5 text-right text-[10px] uppercase tracking-wider text-subtle">卖出</p>
            <div class="space-y-px">
              <div v-for="l in askLevels" :key="'a' + l.price" class="relative px-3 py-[5px] text-right">
                <span class="askbar absolute inset-y-0 right-0" :style="{ width: `${(l.qty / maxQty) * 100}%` }" />
                <span class="relative flex items-center justify-end gap-2 font-mono text-[12px]">
                  <span class="num-up">{{ fmtPrice(l.price) }}</span>
                  <span class="num text-subtle">{{ l.qty.toFixed(l.qty < 1 ? 4 : 2) }}</span>
                </span>
              </div>
            </div>
          </div>
          <div class="bg-surface">
            <p class="px-3 pb-1 pt-2.5 text-left text-[10px] uppercase tracking-wider text-subtle">买入</p>
            <div class="space-y-px">
              <div v-for="l in bidLevels" :key="'b' + l.price" class="relative px-3 py-[5px]">
                <span class="bidbar absolute inset-y-0 left-0" :style="{ width: `${(l.qty / maxQty) * 100}%` }" />
                <span class="relative flex items-center justify-between gap-2 font-mono text-[12px]">
                  <span class="num-down">{{ fmtPrice(l.price) }}</span>
                  <span class="num text-subtle">{{ l.qty.toFixed(l.qty < 1 ? 4 : 2) }}</span>
                </span>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- 逐笔成交 -->
      <div class="panel flex h-full flex-col overflow-hidden">
        <div class="flex items-center gap-2 border-b border-border px-4 py-3">
          <h3 class="panel-title">逐笔成交</h3>
          <span v-if="connected" class="badge tone-up"><span class="badge-dot animate-pulse bg-up" />实时</span>
        </div>
        <div class="h-[300px] overflow-y-auto">
          <table class="tbl" v-if="tape.length">
            <tbody>
              <tr v-for="(t, i) in tape" :key="t.time + '-' + i">
                <td class="font-mono text-[11px] text-subtle">{{ fmtTime(new Date(t.time).toISOString(), true) }}</td>
                <td class="num tbl-num font-medium text-fg">{{ fmtPrice(t.price) }}</td>
                <td class="num tbl-num text-muted">{{ t.qty.toFixed(t.qty < 1 ? 4 : 2) }}</td>
                <td class="tbl-num">
                  <span class="badge" :class="t.side === 'buy' ? 'tone-up' : 'tone-down'">
                    {{ t.side === 'buy' ? '吃单买' : '吃单卖' }}
                  </span>
                </td>
              </tr>
            </tbody>
          </table>
          <div v-else class="flex h-full items-center justify-center px-4 text-xs text-subtle">
            {{ connected ? '等待成交推送…' : '连接后展示逐笔成交' }}
          </div>
        </div>
      </div>
    </section>

    <!-- 行情图表（分时 / K 线 Tab） -->
    <Panel
      title="行情图表"
      :sub="chartTab === 'intraday'
        ? `${symbol} · 今日 1 分钟 · 价格线 + 均价 + 分钟量`
        : `${symbol} · ${interval} · ${winFrom ? '已定位到本地数据区间' : '近 ' + rangeDays + ' 天'}（历史本地库，实时叠加）`"
    >
      <template #head>
        <div class="flex items-center gap-1 rounded-lg border border-border bg-surface p-0.5">
          <button
            class="chip !h-7 !border-0 font-mono"
            :class="{ 'is-active': chartTab === 'intraday' }"
            @click="chartTab = 'intraday'"
          >
            分时
          </button>
          <button
            class="chip !h-7 !border-0 font-mono"
            :class="{ 'is-active': chartTab === 'kline' }"
            @click="chartTab = 'kline'"
          >
            K 线
          </button>
        </div>
        <span class="badge" :class="connected ? 'tone-up' : 'tone-neutral'">
          <span class="badge-dot animate-pulse" :class="connected ? 'bg-up' : ''" />
          {{ connected ? '实时' : '同步中' }}
        </span>
      </template>

      <template #actions>
        <template v-if="chartTab === 'kline'">
          <div class="flex items-center gap-1.5">
            <div class="mr-2 flex items-center gap-1 rounded-lg border border-border bg-surface p-0.5">
              <button
                v-for="r in ranges"
                :key="r.days"
                class="chip !h-7 !border-0"
                :class="{ 'is-active': !winFrom && rangeDays === r.days }"
                @click="presetRange(r.days)"
              >
                {{ r.label }}
              </button>
            </div>
            <div class="flex items-center gap-1 rounded-lg border border-border bg-surface p-0.5">
              <button
                v-for="i in intervals"
                :key="i"
                class="chip !h-7 !border-0 font-mono"
                :class="{ 'is-active': interval === i }"
                @click="interval = i; loadChart()"
              >
                {{ i }}
              </button>
            </div>
          </div>
        </template>
      </template>

      <!-- 分时 -->
      <div v-show="chartTab === 'intraday'">
        <div v-if="intradayLoading && !intraday.length" class="py-16 text-center">
          <StateView busy title="读取今日分时…" />
        </div>
        <StateView
          v-else-if="!intraday.length"
          icon="chart"
          title="今日暂无分时数据"
          desc="拉取币安当日 1m 数据失败，或交易日刚开始。连接后分时将从实时流逐分钟累积。"
        />
        <IntradayChart v-else :points="intraday" :avg="intradayAvg" :height="360" />
      </div>

      <!-- K 线 -->
      <div v-show="chartTab === 'kline'">
        <div v-if="loadingChart" class="py-20 text-center">
          <StateView busy title="读取 K 线数据…" />
        </div>
        <StateView
          v-else-if="chartError"
          icon="error"
          title="K 线数据不可用"
          :desc="`${chartError}。请先到「K 线下载」把 ${symbol} 的 ${interval} 历史数据拉入本地库。`"
        >
          <template #action>
            <router-link to="/data/downloads" class="btn btn-primary">去下载数据</router-link>
          </template>
        </StateView>
        <div v-else-if="!bars.length && coverage.length" class="flex flex-col items-center gap-4 px-6 py-10 text-center">
          <div class="flex h-12 w-12 items-center justify-center rounded-2xl border border-border bg-surface-2 font-display text-lg text-subtle">▤</div>
          <div>
            <p class="text-sm font-semibold text-fg">当前窗口没有 {{ symbol }} {{ interval }} 数据</p>
            <p class="mt-1.5 text-xs leading-relaxed text-muted">
              该币在本地库已下载以下数据，点选即可把图表定位过去：
            </p>
          </div>
          <div class="flex max-w-2xl flex-wrap justify-center gap-2">
            <button
              v-for="c in coverage"
              :key="c.interval"
              class="chip !h-9 px-3"
              :class="{ 'is-active': interval === c.interval && winFrom }"
              @click="viewCoverage(c)"
            >
              <span class="font-mono font-semibold">{{ c.interval }}</span>
              <span class="text-subtle">{{ fmtCompact(c.count) }} 根</span>
              <span class="font-mono text-[10px] text-subtle">{{ fmtTime(c.min_time) }} ~ {{ fmtTime(c.max_time) }}</span>
            </button>
          </div>
          <router-link to="/data/downloads" class="btn btn-ghost">继续下载更多数据</router-link>
        </div>
        <StateView
          v-else-if="!bars.length"
          icon="database"
          title="本地还没有这段数据"
          desc="先用 K 线下载功能拉取对应时间范围，图表就会出现。"
        >
          <template #action>
            <router-link to="/data/downloads" class="btn btn-primary">去下载数据</router-link>
          </template>
        </StateView>
        <KLineChart v-else :bars="bars" :latest="latestBar ?? undefined" :height="440" />
      </div>
    </Panel>

    <!-- 模拟交易 + 自动交易观察（图表下方） -->
    <section class="panel overflow-hidden">
      <div class="grid grid-cols-1 gap-px bg-border lg:grid-cols-3">
        <div class="bg-surface p-4 lg:col-span-1">
          <div class="flex items-center gap-2">
            <h3 class="panel-title">模拟交易</h3>
            <span class="badge tone-neutral">纸面盘</span>
          </div>
          <div class="mt-3 flex flex-wrap gap-1.5">
            <button
              v-for="a in simAccounts"
              :key="a.id"
              class="chip !h-7"
              :class="{ 'is-active': simSel === a.id }"
              @click="simPickAccount(a.id)"
            >
              {{ a.name }}<span class="num text-[10px] text-muted">${{ Number(a.current_cash).toLocaleString() }}</span>
            </button>
            <span v-if="!simAccounts.length" class="text-xs text-subtle">先在「模拟交易」页创建账户</span>
          </div>

          <div class="mt-3 flex items-center justify-between rounded-lg border border-border px-3 py-2 text-xs">
            <span class="text-subtle">账户现金</span>
            <span class="num font-semibold text-fg">${{ simCash.toLocaleString(undefined, { maximumFractionDigits: 0 }) }}</span>
          </div>

          <div class="mt-3 grid grid-cols-2 gap-2">
            <div class="flex gap-1.5">
              <button class="chip !h-9 flex-1 !border-0" :class="{ 'is-active': simSide === 'buy' }" @click="simSide = 'buy'">买入</button>
              <button class="chip !h-9 flex-1 !border-0" :class="{ 'is-active': simSide === 'sell' }" @click="simSide = 'sell'">卖出</button>
            </div>
            <input v-model.number="simQty" type="number" min="0" step="any" class="input h-9 font-mono" aria-label="数量" />
          </div>
          <p class="mt-1.5 text-[11px] text-subtle">
            {{ symbol }} @ {{ fmtPrice(displayPrice) }} · 手续费 0.1% · 按现价市价撮合
          </p>
          <button
            class="btn mt-2 w-full !h-9"
            :class="simSide === 'buy' ? 'btn-primary' : 'btn-danger-ghost'"
            :disabled="!simSel || simOrdering || !simAccounts.length || !(simQty > 0)"
            @click="submitSimOrder"
          >
            {{ simOrdering ? '撮合中…' : simSide === 'buy' ? '模拟买入' : '模拟卖出' }}
          </button>
        </div>

        <div class="bg-surface p-4 lg:col-span-2">
          <div class="flex items-center gap-2">
            <h3 class="panel-title">自动交易观察</h3>
            <span class="text-[11px] text-subtle">手动/策略化/LLM 在本币的成交实时汇总（含未来自动交易）</span>
          </div>
          <div v-if="simActivity.length" class="mt-2 max-h-80 overflow-y-auto">
            <table class="tbl">
              <thead>
                <tr>
                  <th>时间</th><th class="text-right">来源</th><th class="text-right">方向</th><th class="text-right">价格</th><th class="text-right">数量</th><th class="text-right">手续费</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(it, i) in simActivity" :key="i">
                  <td class="tbl-sub font-mono">{{ fmtTime(it.created_at, true) }}</td>
                  <td>
                    <span class="badge" :class="autoTypeMeta[it.account_type]?.cls ?? 'tone-neutral'">
                      {{ it.account_name }} · {{ autoTypeMeta[it.account_type]?.label ?? it.account_type }}
                    </span>
                  </td>
                  <td><span class="badge" :class="it.side === 'buy' ? 'tone-up' : 'tone-down'">{{ it.side === 'buy' ? '买入' : '卖出' }}</span></td>
                  <td class="num tbl-num text-fg">{{ fmtPrice(it.price) }}</td>
                  <td class="num tbl-num text-muted">{{ it.qty.toFixed(4) }}</td>
                  <td class="num tbl-num text-subtle">{{ it.fee.toFixed(4) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <div v-else class="mt-2 flex items-start gap-2.5 rounded-lg border border-dashed border-border bg-surface-2/50 px-3.5 py-3 text-xs text-muted">
            <span>暂无该币模拟成交。手动成交即刻出现在这里；接入 ML 策略/LLM 自动交易后，本区会实时列出它们的每一笔（含账户类型来源）。</span>
          </div>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.askbar {
  background: color-mix(in srgb, var(--color-down) 10%, transparent);
}
.bidbar {
  background: color-mix(in srgb, var(--color-up) 10%, transparent);
}
</style>
