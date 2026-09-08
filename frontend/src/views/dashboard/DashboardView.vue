<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { ArrowDownRight, ArrowUpRight, Database, FlaskConical, Sparkles, TrendingUp, TrendingDown, RefreshCw, Search } from '@lucide/vue'
import { fetchTickers, type Ticker } from '@/api/market'
import { useMarketSocket, type MarketEvent } from '@/ws/marketSocket'
import PageHeader from '@/components/ui/PageHeader.vue'
import HelpDrawer, { type PageHelp } from '@/components/ui/HelpDrawer.vue'
import Panel from '@/components/ui/Panel.vue'
import StateView from '@/components/ui/StateView.vue'
import { useToastStore } from '@/stores/toast'
import { baseOf, cls, fmtCompact, fmtMoney, fmtPct, fmtPrice, MAJOR_SYMBOLS, timeAgo } from '@/utils/format'

const pageHelp: PageHelp = {
  title: '行情看板',
  intro: '主页速览：主流币实时价格与全市场排行。价格即数据，从这里挑标的去「研究」。',
  terms: [
    { term: '交易对 Symbol', desc: '如 BTCUSDT = 用 USDT 计价买卖 BTC（币安现货）。' },
    { term: '24h 涨跌幅', desc: '相对 24 小时前开盘价的变化，计算公式：(现价-24h前价)/24h前价。' },
    { term: '24h 成交额', desc: '过去 24 小时该币种总成交金额，衡量流动性，越大越活跃。' },
    { term: '实时 vs 轮询', desc: '本页价格由 WebSocket 实时推送（带「实时」灯）；全市场表默认 REST 每 30s 刷新以省带宽，可手动/自动。' },
    { term: '24h 区间标尺', desc: '卡片底部的横条表示价格处于当日最高/最低的什么位置，越靠右越接近高点。' },
  ],
  tips: ['点主流币卡片或表格行进入单币详情。', '「自动」开关会每 30s 拉一次全市场（页面不可见时自动暂停）。', '涨绿跌红为加密圈惯例，仅用于行情，不构成买卖建议。'],
}

const toast = useToastStore()
const loading = ref(true)
const error = ref<string | null>(null)
const all = ref<Ticker[]>([])
const keyword = ref('')
const auto = ref(false)
let timer = 0

const bySymbol = computed(() => new Map(all.value.map((t) => [t.symbol, t])))

const majors = computed<Ticker[]>(() =>
  (MAJOR_SYMBOLS as readonly string[])
    .map((s) => bySymbol.value.get(s))
    .filter((t): t is Ticker => Boolean(t)),
)

const livePrices = ref<Record<string, number>>({})
const { connected: streamLive } = useMarketSocket(
  () => (MAJOR_SYMBOLS as readonly string[]).map((s) => ({ symbol: s, kind: 'trade' as const })),
  (ev: MarketEvent) => {
    if (ev.type === 'trade') livePrices.value = { ...livePrices.value, [ev.symbol]: ev.trade.price }
  },
)

function liveOf(t: Ticker): number {
  return livePrices.value[t.symbol] ?? Number(t.lastPrice)
}

const sortKey = ref<keyof Pick<Ticker, 'lastPrice' | 'quoteVolume' | 'priceChangePercent'>>('quoteVolume')
const sortAsc = ref(false)

const rows = computed<Ticker[]>(() => {
  const kw = keyword.value.trim().toUpperCase()
  const list = all.value.filter((t) => !kw || t.symbol.includes(kw))
  return list.sort((a, b) => {
    const av = Number(a[sortKey.value])
    const bv = Number(b[sortKey.value])
    const d = av - bv
    return sortAsc.value ? d : -d
  }).slice(0, 60)
})

const gainers = computed(() =>
  [...all.value].sort((a, b) => Number(b.priceChangePercent) - Number(a.priceChangePercent)).slice(0, 3),
)
const losers = computed(() =>
  [...all.value].sort((a, b) => Number(a.priceChangePercent) - Number(b.priceChangePercent)).slice(0, 3),
)

function sortBy(key: typeof sortKey.value) {
  if (sortKey.value === key) {
    sortAsc.value = !sortAsc.value
  } else {
    sortKey.value = key
    sortAsc.value = false
  }
}

async function load() {
  error.value = null
  if (!all.value.length) loading.value = true
  try {
    all.value = await fetchTickers()
  } catch (e: unknown) {
    error.value = (e as { message?: string }).message ?? '行情加载失败'
    toast.error('行情数据加载失败', error.value)
  } finally {
    loading.value = false
  }
}

function startAuto() {
  stopAuto()
  timer = window.setInterval(() => {
    if (!document.hidden) void load()
  }, 30_000)
}
function stopAuto() {
  if (timer) {
    clearInterval(timer)
    timer = 0
  }
}
function toggleAuto() {
  auto.value = !auto.value
  if (auto.value) startAuto()
  else stopAuto()
}

function rangePct(t: Ticker): number {
  const hi = Number(t.highPrice)
  const lo = Number(t.lowPrice)
  const p = Number(t.lastPrice)
  if (!hi || hi === lo) return 50
  return Math.max(2, Math.min(98, ((p - lo) / (hi - lo)) * 100))
}

onMounted(load)
onBeforeUnmount(stopAuto)
</script>

<template>
  <div class="flex flex-col gap-6">
    <PageHeader
      eyebrow="Market Console"
      title="行情看板"
      desc="主流数字币实时行情与研究入口。价格即数据：先看盘，再研究。"
    >
      <template #actions>
        <HelpDrawer :help="pageHelp" />
        <span v-if="streamLive" class="badge tone-up mr-1 hidden sm:inline-flex">
          <span class="badge-dot animate-pulse bg-up" />实时
        </span>
        <span class="mr-1 hidden text-xs text-subtle md:inline">更新于 {{ timeAgo(all[0]?.closeTime ? new Date(all[0].closeTime).toISOString() : undefined) }}</span>
        <button class="btn btn-ghost" :class="{ 'tint-primary border-primary text-primary-ink dark:text-primary': auto }" @click="toggleAuto">
          <span class="badge-dot" :class="auto ? 'bg-up' : 'bg-subtle'" />
          自动 {{ auto ? '已开' : '刷新' }}
        </button>
        <button class="btn btn-primary" :disabled="loading" @click="load">
          <RefreshCw :size="15" :class="{ 'animate-spin-slow': loading }" />
          刷新
        </button>
      </template>
    </PageHeader>

    <div v-if="error && !all.length" class="panel">
      <StateView icon="error" title="行情拉取失败" :desc="error">
        <template #action>
          <button class="btn btn-primary" @click="load"><RefreshCw :size="15" />重试</button>
        </template>
      </StateView>
    </div>

    <template v-else>
      <!-- 聚焦主流 -->
      <section aria-label="主流币聚焦">
        <div class="mb-2.5 flex items-center justify-between">
          <h2 class="panel-title">聚焦 · 主流</h2>
          <span class="text-xs text-subtle">{{ majors.length }} 个交易对</span>
        </div>
        <div class="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
          <article
            v-for="t in majors"
            :key="t.symbol"
            class="panel group relative cursor-pointer p-4 transition-transform duration-150 hover:-translate-y-0.5"
            @click="$router.push(`/market/${t.symbol}`)"
          >
            <div class="flex items-center justify-between gap-2">
              <span class="flex items-center gap-2">
                <span class="flex h-6 w-6 items-center justify-center rounded-md border border-border bg-surface-2 font-display text-[10px] font-bold text-muted">
                  {{ baseOf(t.symbol).slice(0, 3) }}
                </span>
                <span class="text-sm font-semibold text-fg">{{ t.symbol }}</span>
              </span>
              <span
                class="badge"
                :class="Number(t.priceChangePercent) >= 0 ? 'tone-up' : 'tone-down'"
              >
                <component :is="Number(t.priceChangePercent) >= 0 ? ArrowUpRight : ArrowDownRight" :size="11" />
                {{ fmtPct(t.priceChangePercent, 1) }}
              </span>
            </div>

            <p class="num mt-3 text-[22px] font-semibold tracking-tight text-fg">{{ fmtPrice(liveOf(t)) }}</p>

            <div class="mt-1 flex items-baseline justify-between text-xs">
              <span class="text-subtle">≈ {{ fmtMoney(t.quoteVolume) }}</span>
              <span class="font-medium" :class="cls(t.priceChangePercent)">今日 {{ fmtPct(t.priceChangePercent) }}</span>
            </div>

            <!-- 24h 区间标尺：价格在区间内的位置 -->
            <div class="mt-3">
              <div class="relative h-[3px] rounded-full bg-surface-2">
                <span
                  class="absolute inset-y-0 rounded-full"
                  :class="Number(t.priceChangePercent) >= 0 ? 'range-fill-up' : 'range-fill-down'"
                  :style="{ left: '0%', right: `${100 - rangePct(t)}%` }"
                />
              </div>
              <div class="mt-1 flex justify-between font-mono text-[9px] text-subtle">
                <span>L {{ fmtPrice(t.lowPrice) }}</span>
                <span>H {{ fmtPrice(t.highPrice) }}</span>
              </div>
            </div>
          </article>
        </div>
      </section>

      <div class="grid grid-cols-1 gap-6 xl:grid-cols-3">
        <!-- 全市场表 -->
        <section class="xl:col-span-2">
          <Panel title="全市场行情" sub="按 24h 成交额排序，点击表头可切换排序" :pad="'none'" flush>
            <template #actions>
              <div class="relative">
                <Search :size="14" class="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-subtle" />
                <input
                  v-model="keyword"
                  type="search"
                  placeholder="搜索币对…"
                  aria-label="搜索币对"
                  class="input h-8 w-44 pl-8 text-xs"
                />
              </div>
            </template>
            <div class="overflow-x-auto">
              <table v-if="rows.length" class="tbl">
                <thead>
                  <tr>
                    <th>币对</th>
                    <th class="text-right">最新价</th>
                    <th class="cursor-pointer select-none" :class="{ 'text-muted': sortKey === 'priceChangePercent' }" @click="sortBy('priceChangePercent')">24h 涨跌</th>
                    <th class="cursor-pointer select-none" :class="{ 'text-muted': sortKey === 'quoteVolume' }" @click="sortBy('quoteVolume')">24h 成交额</th>
                    <th class="text-right">24h 高 / 低</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="t in rows" :key="t.symbol" class="is-clickable" @click="$router.push(`/market/${t.symbol}`)">
                    <td>
                      <span class="font-semibold text-fg">{{ t.symbol }}</span>
                    </td>
                    <td class="num tbl-num font-medium text-fg">{{ fmtPrice(t.lastPrice) }}</td>
                    <td>
                      <span class="inline-flex items-center justify-end gap-1 font-mono text-[13px] font-semibold" :class="cls(t.priceChangePercent)">
                        <component :is="Number(t.priceChangePercent) >= 0 ? TrendingUp : TrendingDown" :size="12" />
                        {{ fmtPct(t.priceChangePercent) }}
                      </span>
                    </td>
                    <td class="num tbl-num text-muted">{{ fmtCompact(t.quoteVolume) }}</td>
                    <td class="tbl-num">
                      <span class="num-up">{{ fmtPrice(t.highPrice) }}</span>
                      <span class="mx-1 text-subtle">/</span>
                      <span class="num-down">{{ fmtPrice(t.lowPrice) }}</span>
                    </td>
                  </tr>
                </tbody>
              </table>
              <StateView v-else icon="search" title="没有匹配的币对" desc="换个关键词试试" />
            </div>
          </Panel>
        </section>

        <!-- 右栏：脉冲 + 入口 -->
        <div class="flex flex-col gap-4">
          <Panel title="市场脉冲" sub="24h 涨跌幅前三" :pad="'sm'">
            <ul class="space-y-3">
              <li v-for="t in gainers" :key="t.symbol" class="flex items-center gap-2.5">
                <span class="flex h-6 w-6 items-center justify-center rounded-md border border-border bg-surface-2 font-display text-[10px] font-bold text-muted">
                  {{ baseOf(t.symbol).slice(0, 3) }}
                </span>
                <router-link :to="`/market/${t.symbol}`" class="min-w-0 flex-1 truncate text-[13px] font-medium hover:text-primary-ink dark:hover:text-primary">
                  {{ t.symbol }}
                </router-link>
                <span class="badge tone-up"><ArrowUpRight :size="11" />{{ fmtPct(t.priceChangePercent, 1) }}</span>
              </li>
              <li class="!my-2 border-t border-border" aria-hidden="true" />
              <li v-for="t in losers" :key="t.symbol" class="flex items-center gap-2.5">
                <span class="flex h-6 w-6 items-center justify-center rounded-md border border-border bg-surface-2 font-display text-[10px] font-bold text-muted">
                  {{ baseOf(t.symbol).slice(0, 3) }}
                </span>
                <router-link :to="`/market/${t.symbol}`" class="min-w-0 flex-1 truncate text-[13px] font-medium hover:text-primary-ink dark:hover:text-primary">
                  {{ t.symbol }}
                </router-link>
                <span class="badge tone-down"><ArrowDownRight :size="11" />{{ fmtPct(t.priceChangePercent, 1) }}</span>
              </li>
            </ul>
          </Panel>

          <Panel title="研究工作台" sub="数据是研究的原材料">
            <div class="flex flex-col gap-2.5">
              <router-link to="/data/downloads" class="group flex items-center gap-3 rounded-xl border border-border bg-surface p-3 transition-colors hover:border-primary">
                <span class="flex h-9 w-9 items-center justify-center rounded-lg tint-primary text-primary-ink dark:text-primary">
                  <Database :size="17" />
                </span>
                <span class="min-w-0 flex-1">
                  <span class="block text-[13px] font-semibold">历史 K 线</span>
                  <span class="block text-xs text-subtle">批量下载 · 定时增量 · 断点续传</span>
                </span>
              </router-link>
              <router-link to="/strategy" class="group flex items-center gap-3 rounded-xl border border-border bg-surface p-3 transition-colors hover:border-primary">
                <span class="flex h-9 w-9 items-center justify-center rounded-lg tint-primary text-primary-ink dark:text-primary">
                  <FlaskConical :size="17" />
                </span>
                <span class="min-w-0 flex-1">
                  <span class="block text-[13px] font-semibold">策略研究</span>
                  <span class="block text-xs text-subtle">模板建仓 · 一键回测 · 指标卡</span>
                </span>
              </router-link>
              <router-link to="/factor/ai-lab" class="group flex items-center gap-3 rounded-xl border border-border bg-surface p-3 transition-colors hover:border-primary">
                <span class="flex h-9 w-9 items-center justify-center rounded-lg tint-ai text-ai-ink">
                  <Sparkles :size="17" />
                </span>
                <span class="min-w-0 flex-1">
                  <span class="block text-[13px] font-semibold">AI 因子工坊</span>
                  <span class="block text-xs text-subtle">用自然语言设计因子与策略组合</span>
                </span>
              </router-link>
            </div>
          </Panel>
        </div>
      </div>
    </template>
  </div>
</template>
