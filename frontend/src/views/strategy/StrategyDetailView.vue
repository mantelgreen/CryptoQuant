<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import {
  ArrowLeft,
  ChevronDown,
  FlaskConical,
  LoaderCircle,
  Play,
  Plus,
  RefreshCw,
  Trash2,
} from '@lucide/vue'
import {
  createVersion,
  deleteBacktest,
  getBacktest,
  getStrategy,
  listBacktests,
  listTemplates,
  runBacktest,
  type Backtest,
  type BacktestDetail,
  type ParamSpec,
  type Strategy,
  type StrategyVersion,
  type TemplateMeta,
} from '@/api/strategy'
import PageHeader from '@/components/ui/PageHeader.vue'
import HelpDrawer, { type PageHelp } from '@/components/ui/HelpDrawer.vue'
import Panel from '@/components/ui/Panel.vue'
import Modal from '@/components/ui/Modal.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import StateView from '@/components/ui/StateView.vue'
import EquityChart from '@/components/chart/EquityChart.vue'
import { useToastStore } from '@/stores/toast'
import { reviewBacktest } from '@/api/agent'
import { fmtTime, RESEARCH_INTERVALS, MAJOR_SYMBOLS, intervalLabel, timeAgo, fmtPct } from '@/utils/format'

const pageHelp: PageHelp = {
  title: '回测研究台',
  intro: '给策略版本配上标的/周期/时间窗，跑向量化回测，看指标卡与资金曲线。结果与参数可追溯。',
  terms: [
    { term: '回测假设', desc: '撮合=信号在下“一根 K 线开盘价”成交；费率 0.1%（现货 taker）、滑点 5bps（=0.05%）；只用历史数据，无未来函数。' },
    { term: '累计/年化收益', desc: '累计=期末净值/期初-1；年化=把区间收益按每年约 bar 数换算成“一年”的复利收益，跨周期可比。' },
    { term: '最大回撤', desc: '净值从峰顶跌到谷底的最大幅度（负值），衡量最坏情况有多痛。' },
    { term: '夏普 / 索提诺 / 卡玛', desc: '夏普=(超额收益)/总波动；索提诺把分母换成“下行波动”；卡玛=年化/最大回撤。三者都是“每承担一单位风险换多少收益”。' },
    { term: '胜率 / 盈亏比', desc: '胜率=赚钱平仓次数占比；盈亏比(profit factor)=总盈利/总亏损。胜率低但盈亏比高仍可盈利。' },
    { term: '资金曲线 / SSE', desc: '资金曲线=净值随时间变化；回测在 ARQ worker 异步跑，阶段进度经 SSE（服务器推送）实时回传，秒级出结果。' },
    { term: '数据要求', desc: '回测从本地库读 K 线。区间没数据会报 no_kline_data_in_range，先去「K 线下载」补齐。' },
  ],
  tips: ['没有版本先建 v1：选模板调参。', '同一区间跑「买入持有」作基准，看策略能否跑赢“躺着不动”。', '改参数请新建版本而不是覆盖，便于对比回归。'],
}

const route = useRoute()
const toast = useToastStore()
const strategyId = String(route.params.id)

const strategy = ref<Strategy | null>(null)
const versions = ref<StrategyVersion[]>([])
const templates = ref<Record<string, TemplateMeta>>({})
const backtests = ref<Backtest[]>([])
const detail = ref<BacktestDetail | null>(null)
const activeVersionId = ref<string | null>(null)
const detailLoading = ref(false)
const listLoading = ref(true)
const pageError = ref<string | null>(null)
const removingBacktest = ref<string | null>(null)

// —— 运行配置 ——
const runSymbol = ref('BTCUSDT')
const runInterval = ref('1h')
const runDays = ref(90)
const customRange = ref(false)
const customStart = ref('')
const customEnd = ref('')
const submitting = ref(false)

// —— 版本创建弹窗 ——
const showVersionModal = ref(false)
const creating = ref(false)
const newTemplate = ref<string>('sma_cross')
const newParams = ref<Record<string, number | string>>({})

// —— 回测轮询 ——
let pollTimer = 0
let es: EventSource | null = null
const busyCount = computed(() => backtests.value.filter((b) => b.status === 'running' || b.status === 'queued' || b.status === 'pending').length)

function templateTitleOf(version: StrategyVersion): string {
  const tid = (version.params?.template_id as string) ?? ''
  return templates.value[tid]?.title ?? (tid || '模板')
}

function schemaOf(id: string): Record<string, ParamSpec> {
  return templates.value[id]?.params_schema ?? {}
}

const activeVersion = computed(() => versions.value.find((v) => v.id === activeVersionId.value) ?? versions.value[0] ?? null)

const hasBacktestData = computed(() => Boolean(backtests.value.length))
const latestRun = computed(() => backtests.value[0] ?? null)

// ================= 数据加载 =================
async function loadMeta() {
  try {
    const [str, tpl] = await Promise.all([getStrategy(strategyId), listTemplates()])
    strategy.value = str.strategy
    versions.value = str.versions
    templates.value = tpl
    if (!activeVersionId.value && str.versions.length) {
      activeVersionId.value = str.versions[0].id
    }
  } catch (e: unknown) {
    pageError.value = (e as { message?: string }).message ?? '策略加载失败'
  }
}

async function loadBacktests() {
  try {
    const all = await listBacktests()
    backtests.value = all.filter((b) => b.strategy_id === strategyId)
  } catch (e: unknown) {
    toast.error('回测记录加载失败', (e as { message?: string }).message)
  } finally {
    listLoading.value = false
  }
}

async function openDetail(id: string) {
  detailLoading.value = true
  try {
    detail.value = await getBacktest(id)
  } catch (e: unknown) {
    toast.error('回测详情加载失败', (e as { message?: string }).message)
  } finally {
    detailLoading.value = false
  }
}

async function removeBacktest(b: Backtest) {
  if (!window.confirm(`删除这次回测（${b.id.slice(0, 8)} · ${b.symbols[0]}/${b.interval}）？其资金曲线与成交明细会一并删除。`)) return
  removingBacktest.value = b.id
  try {
    await deleteBacktest(b.id)
    toast.success('回测已删除', `${b.symbols[0]} ${b.interval} · ${b.id.slice(0, 8)}`)
    stopPoll()
    closeSse()
    await loadBacktests()
    if (detail.value?.backtest.id === b.id) detail.value = null
    if (backtests.value.length) await openDetail(backtests.value[0].id)
  } catch (e: unknown) {
    toast.error('删除失败', (e as { message?: string }).message)
  } finally {
    removingBacktest.value = null
  }
}

function schedulePoll() {
  if (pollTimer) return
  pollTimer = window.setInterval(async () => {
    await loadBacktests()
    if (detail.value && ['running', 'queued', 'pending'].includes(detail.value.backtest.status)) {
      await openDetail(detail.value.backtest.id)
    }
  }, 2000)
}
function stopPoll() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = 0
  }
}
watch(busyCount, (n) => {
  if (n > 0) schedulePoll()
  else stopPoll()
})

async function refreshAll() {
  await Promise.all([loadMeta(), loadBacktests()])
  if (detail.value) {
    const fresh = backtests.value.find((b) => b.id === detail.value?.backtest.id)
    if (fresh) await openDetail(fresh.id)
  }
}

function closeSse() {
  if (es) {
    es.close()
    es = null
  }
}

function openProgressSse(id: string) {
  closeSse()
  const source = new EventSource(`/api/v1/strategy/backtests/${id}/progress`)
  source.onmessage = (ev) => {
    try {
      const msg = JSON.parse(ev.data) as {
        type?: string
        backtest_id?: string
        status?: string
        stage?: string | null
        pct?: number
        error?: string | null
      }
      if (!msg.backtest_id || msg.backtest_id !== id) return
      if (detail.value && detail.value.backtest.id === id) {
        if (msg.status) detail.value.backtest.status = msg.status as Backtest['status']
        if (typeof msg.pct === 'number') detail.value.backtest.progress_pct = msg.pct
        if (msg.stage !== undefined) detail.value.backtest.stage = msg.stage
        if (msg.error !== undefined) detail.value.backtest.error = msg.error
      }
      if (msg.status === 'succeeded' || msg.status === 'failed') {
        closeSse()
        void refreshAll()
      }
    } catch {
      /* ignore */
    }
  }
  source.onerror = () => closeSse()
  es = source
}

watch(
  () => detail.value?.backtest.id ?? '',
  (id) => (id ? openProgressSse(id) : closeSse()),
)

// ================= 运行回测 =================
function timeWindow(): { start: string; end: string } {
  const end = new Date()
  if (customRange.value) {
    return { start: new Date(customStart.value).toISOString(), end: new Date(customEnd.value).toISOString() }
  }
  const start = new Date(end.getTime() - runDays.value * 24 * 3600 * 1000)
  return { start: start.toISOString(), end: end.toISOString() }
}

function presetDays(d: number) {
  runDays.value = d
  customRange.value = false
}

function localDatetime(d: Date): string {
  const p = (x: number) => String(x).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`
}

function toggleCustomRange() {
  if (!customRange.value && (!customStart.value || !customEnd.value)) {
    const end = new Date()
    const start = new Date(end.getTime() - runDays.value * 24 * 3600 * 1000)
    customStart.value = localDatetime(start)
    customEnd.value = localDatetime(end)
  }
  customRange.value = !customRange.value
}

async function submitRun() {
  const v = activeVersion.value
  if (!v) {
    showVersionModal.value = true
    return
  }
  if (customRange.value) {
    const start = new Date(customStart.value)
    const end = new Date(customEnd.value)
    if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) {
      toast.error('自定义区间无效', '开始与结束时间都需要填写')
      return
    }
    if (end <= start) {
      toast.error('自定义区间无效', '结束时间须晚于开始时间')
      return
    }
  }
  submitting.value = true
  try {
    const { start, end } = timeWindow()
    const r = await runBacktest({
      strategy_id: strategyId,
      version_id: v.id,
      symbol: runSymbol.value.trim().toUpperCase(),
      interval: runInterval.value,
      start,
      end,
      params: { ...(v.params as Record<string, unknown>) },
    })
    toast.success('回测任务已提交', `回测 ${r.backtest_id.slice(0, 8)}… 正在后台运行`)
    await loadBacktests()
    await openDetail(r.backtest_id)
    window.setTimeout(() => {
      // 让列表先更新，立即开始轮询
      if (busyCount.value > 0) schedulePoll()
    }, 400)
  } catch (e: unknown) {
    toast.error('回测提交失败', (e as { message?: string }).message)
  } finally {
    submitting.value = false
  }
}

// ================= 版本创建 =================
function fillDefaultsFor(tid: string) {
  const schema = schemaOf(tid)
  const out: Record<string, number | string> = {}
  for (const [k, spec] of Object.entries(schema)) {
    out[k] = (spec.default ?? spec.min ?? 0) as number
  }
  newParams.value = out
}
function pickTemplate(tid: string) {
  newTemplate.value = tid
  fillDefaultsFor(tid)
}

async function submitVersion() {
  creating.value = true
  try {
    const r = await createVersion(strategyId, {
      template_id: newTemplate.value,
      params: { ...newParams.value },
    })
    toast.success('策略版本已创建', `version v${versions.value.length + 1} · ${templates.value[newTemplate.value]?.title}`)
    showVersionModal.value = false
    await loadMeta()
    activeVersionId.value = r.version_id
  } catch (e: unknown) {
    toast.error('创建版本失败', (e as { message?: string }).message)
  } finally {
    creating.value = false
  }
}

function openCreateVersion() {
  if (!newTemplate.value) newTemplate.value = Object.keys(templates.value)[0] ?? 'sma_cross'
  if (!Object.keys(newParams.value).length) fillDefaultsFor(newTemplate.value)
  showVersionModal.value = true
}

// ================= 指标卡 =================
const metricCards: Array<{ key: string; label: string; fmt: 'pct' | 'ratio' | 'count' | 'dd' | 'plainPct' }> = [
  { key: 'total_return', label: '累计收益', fmt: 'pct' },
  { key: 'annual_return', label: '年化收益', fmt: 'pct' },
  { key: 'max_drawdown', label: '最大回撤', fmt: 'dd' },
  { key: 'sharpe', label: '夏普比率', fmt: 'ratio' },
  { key: 'sortino', label: '索提诺', fmt: 'ratio' },
  { key: 'calmar', label: '卡玛', fmt: 'ratio' },
  { key: 'win_rate', label: '胜率', fmt: 'plainPct' },
  { key: 'profit_factor', label: '盈亏比', fmt: 'ratio' },
  { key: 'num_trades', label: '交易次数', fmt: 'count' },
  { key: 'volatility', label: '年化波动', fmt: 'plainPct' },
]
// 读数台结构：第一行三个“决定性问题”，其余降为仪器格
const primaryMetrics = metricCards.filter((c) => ['total_return', 'annual_return', 'max_drawdown'].includes(c.key))
const readoutMetrics = metricCards.filter((c) => !['total_return', 'annual_return', 'max_drawdown'].includes(c.key))

function metricValue(key: string): number | undefined {
  return (detail.value?.backtest.summary?.metrics as Record<string, number> | undefined)?.[key]
}

function metricText(card: { key: string; fmt: string }): string {
  const v = metricValue(card.key)
  if (v === undefined) return '—'
  switch (card.fmt) {
    case 'pct':
      return fmtPct(v * 100, 1)
    case 'dd':
      return `${(v * 100).toFixed(1)}%`
    case 'plainPct':
      return `${(v * 100).toFixed(1)}%`
    case 'ratio':
      return v === Infinity ? '∞' : v.toFixed(2)
    case 'count':
      return Math.round(v).toLocaleString('en-US')
    default:
      return String(v)
  }
}

function metricTone(card: { key: string }): string {
  const v = metricValue(card.key)
  if (v === undefined) return 'text-muted'
  if (card.key === 'max_drawdown') return 'text-down'
  if (card.key === 'volatility') return 'text-muted'
  if (card.key === 'win_rate' || card.key === 'num_trades') return 'text-fg'
  if (v > 0) return 'text-up'
  if (v < 0) return 'text-down'
  return 'text-muted'
}

const equityPoints = computed(() =>
  (detail.value?.equity ?? []).map((p) => ({ ts: fmtTime(p.ts, true), value: p.equity })),
)
const resultMeta = computed(() => {
  const b = detail.value?.backtest
  if (!b) return null
  return {
    symbol: b.symbols[0] ?? '—',
    interval: b.interval,
    range: `${fmtTime(b.start_time)} → ${fmtTime(b.end_time)}`,
    bars: b.summary?.bars,
    finished: b.finished_at ? timeAgo(b.finished_at) : '—',
    stage: b.stage,
  }
})

const currentStatus = computed(() => detail.value?.backtest.status ?? 'none')

// —— AI 回测复盘 ——
const reviewing = ref(false)
const reviewText = ref<string | null>(null)
const reviewModel = ref<string | null>(null)
async function runReview() {
  const b = detail.value?.backtest
  if (!b || b.status !== 'succeeded' || reviewing.value) return
  reviewing.value = true
  reviewText.value = null
  try {
    const r = await reviewBacktest(b.id)
    reviewModel.value = r.model
    reviewText.value = r.review
  } catch (e: unknown) {
    toast.error('复盘失败', (e as { message?: string }).message)
  } finally {
    reviewing.value = false
  }
}

onMounted(async () => {
  await Promise.all([loadMeta(), loadBacktests()])
  if (!pageError.value && backtests.value.length) {
    await openDetail(backtests.value[0].id)
  }
})
onBeforeUnmount(() => {
  stopPoll()
  closeSse()
})
</script>

<template>
  <div class="flex flex-col gap-6">
    <PageHeader v-if="strategy" eyebrow="Research · Strategy" :title="strategy.name">
      <template #actions>
        <HelpDrawer :help="pageHelp" />
        <router-link to="/strategy" class="btn btn-ghost"><ArrowLeft :size="15" />策略列表</router-link>
        <button class="btn btn-ghost" @click="refreshAll"><RefreshCw :size="15" />刷新</button>
        <button class="btn btn-primary" :disabled="!Object.keys(templates).length" @click="openCreateVersion">
          <Plus :size="15" />新建版本
        </button>
      </template>
    </PageHeader>

    <div v-if="pageError" class="panel">
      <StateView icon="error" :title="pageError" desc="可能该策略不存在，或网关不可达。">
        <template #action><router-link to="/strategy" class="btn btn-primary">返回策略列表</router-link></template>
      </StateView>
    </div>

    <template v-else-if="strategy">
      <!-- 策略描述 -->
      <div class="panel px-5 py-4">
        <div class="flex flex-wrap items-center gap-2">
          <p v-if="strategy.description" class="flex-1 text-sm leading-relaxed text-muted">{{ strategy.description }}</p>
          <p v-else class="flex-1 text-sm text-subtle">这个策略还没有描述——也许是用一句话解释“它相信什么”的时候。</p>
          <span v-if="strategy.tags?.length" class="flex flex-wrap gap-1.5">
            <span v-for="t in strategy.tags" :key="t" class="badge tone-neutral">{{ t }}</span>
          </span>
          <span class="text-xs text-subtle">创建于 {{ timeAgo(strategy.created_at) }}</span>
        </div>
      </div>

      <div class="grid grid-cols-1 gap-6 xl:grid-cols-7">
        <!-- 左列：版本 + 回测历史 -->
        <div class="flex flex-col gap-6 xl:col-span-2">
          <Panel title="策略版本" :sub="`共 ${versions.length} 个版本`" :pad="'none'" flush>
            <ul class="divide-y divide-border">
              <li
                v-for="v in versions"
                :key="v.id"
                class="flex cursor-pointer items-center gap-3 px-4 py-3 transition-colors hover:bg-surface-2"
                :class="activeVersionId === v.id ? 'bg-surface-2' : ''"
                @click="activeVersionId = v.id"
              >
                <span
                  class="flex h-7 w-7 shrink-0 items-center justify-center rounded-md font-mono text-[11px] font-semibold"
                  :class="activeVersionId === v.id ? 'tint-primary text-primary-ink dark:text-primary' : 'bg-surface-2 text-muted'"
                >
                  v{{ v.version }}
                </span>
                <div class="min-w-0 flex-1">
                  <p class="truncate text-[13px] font-medium text-fg">{{ templateTitleOf(v) }}</p>
                  <p class="truncate font-mono text-[11px] text-subtle">
                    {{ Object.entries(v.params ?? {}).filter(([k]) => k !== 'template_id').map(([k, val]) => `${k}=${val}`).join(' · ') || '无参数' }}
                  </p>
                </div>
                <ChevronDown :size="14" class="text-subtle" />
              </li>
              <li v-if="!versions.length">
                <div class="px-4 py-6 text-center">
                  <p class="text-sm text-subtle">还没有版本。选择一个模板创建一个。</p>
                  <button class="btn btn-soft mt-3" @click="openCreateVersion"><Plus :size="14" />创建版本 v1</button>
                </div>
              </li>
            </ul>
          </Panel>

          <Panel title="回测记录" :sub="busyCount ? '运行中，自动刷新' : `共 ${backtests.length} 次`" :pad="'none'" flush>
            <div v-if="listLoading && !backtests.length" class="py-8"><StateView busy title="读取回测记录…" /></div>
            <div v-else-if="!backtests.length" class="py-6">
              <StateView icon="flask" title="还没有回测" desc="配置好运行参数，点右上角「运行回测」，指标卡与资金曲线会出现在这里。" />
            </div>
            <ul v-else class="divide-y divide-border">
              <li
                v-for="b in backtests"
                :key="b.id"
                class="group cursor-pointer px-4 py-3 transition-colors hover:bg-surface-2"
                :class="detail?.backtest.id === b.id ? 'bg-surface-2' : ''"
                @click="openDetail(b.id)"
              >
                <div class="flex items-center gap-2">
                  <StatusBadge :status="b.status" :pulse="false" />
                  <span class="font-mono text-[11px] text-subtle">{{ b.id.slice(0, 8) }}</span>
                  <span class="ml-auto flex items-center gap-1.5">
                    <span class="text-xs text-subtle">{{ timeAgo(b.created_at) }}</span>
                    <button
                      class="rounded-md p-1 text-subtle opacity-0 transition-colors hover:bg-danger/10 hover:text-danger focus-visible:opacity-100 group-hover:opacity-100"
                      :disabled="removingBacktest === b.id"
                      :title="`删除这次回测 ${b.id.slice(0, 8)}`"
                      :aria-label="`删除回测 ${b.id.slice(0, 8)}`"
                      @click.stop.prevent="removeBacktest(b)"
                    >
                      <LoaderCircle v-if="removingBacktest === b.id" :size="13" class="animate-spin" />
                      <Trash2 v-else :size="13" />
                    </button>
                  </span>
                </div>
                <p class="mt-1.5 text-xs text-muted">
                  <span class="font-semibold text-fg">{{ b.symbols[0] }}</span>
                  <span class="font-mono"> · {{ b.interval }}</span>
                  <span class="text-subtle"> · {{ fmtTime(b.start_time) }} → {{ fmtTime(b.end_time) }}</span>
                </p>
                <p v-if="b.summary?.final_equity" class="num mt-1 text-[13px] font-semibold text-fg">
                  ${{ b.summary.final_equity.toLocaleString('en-US', { maximumFractionDigits: 0 }) }}
                  <span class="ml-2 text-xs font-medium" :class="(b.summary.metrics?.total_return ?? 0) >= 0 ? 'text-up' : 'text-down'">
                    {{ fmtPct((b.summary.metrics?.total_return ?? 0) * 100, 1) }}
                  </span>
                </p>
                <p v-else-if="b.status === 'failed' && b.error" class="mt-1 truncate text-xs text-down">{{ b.error }}</p>
                <p v-else-if="['queued', 'pending'].includes(b.status)" class="mt-1 text-xs text-subtle">排队等待 worker…</p>
              </li>
            </ul>
          </Panel>

          <!-- AI 复盘：跟随所选回测记录 -->
          <Panel v-if="detail?.backtest?.status === 'succeeded'" title="AI 复盘" :sub="reviewModel ? `由 ${reviewModel} 生成 · 已计入 LLM 用量` : '让大模型解读这次回测的结果、风险与改进点'">
            <template #actions>
              <button class="btn btn-soft !h-8 text-xs" :disabled="reviewing || !detail?.backtest.summary" @click="runReview">
                <LoaderCircle v-if="reviewing" :size="13" class="animate-spin" />
                {{ reviewText ? '重新复盘' : 'AI 复盘' }}
              </button>
            </template>
            <p v-if="reviewText" class="whitespace-pre-wrap text-[13px] leading-relaxed text-muted">{{ reviewText }}</p>
            <p v-else class="text-xs text-subtle">
              点击后会结合本次回测的收益/回撤/夏普/胜率/盈亏比与样本量，给出“结果说明 → 风险 → 改进建议”三段式解读（需先在「模型设置」配置默认模型）。
            </p>
          </Panel>
        </div>

        <!-- 右列：运行台 + 结果 -->
        <div class="flex flex-col gap-6 xl:col-span-5">
          <Panel title="回测运行台" sub="策略始终使用“下一根开盘价”撮合，含 0.1% 手续费与 5bps 滑点">
            <template #head>
              <span v-if="activeVersion" class="badge tone-primary">将运行 v{{ activeVersion.version }}</span>
            </template>
            <div class="grid grid-cols-1 gap-4 md:grid-cols-2">
              <div class="field">
                <label class="label" for="bt-symbol">交易对</label>
                <input id="bt-symbol" v-model="runSymbol" list="bt-symbols" class="input font-mono uppercase" spellcheck="false" />
                <datalist id="bt-symbols">
                  <option v-for="s in MAJOR_SYMBOLS" :key="s" :value="s" />
                </datalist>
              </div>
              <div class="field">
                <label class="label" for="bt-interval">K 线颗粒度</label>
                <select id="bt-interval" v-model="runInterval" class="select font-mono">
                  <option v-for="i in RESEARCH_INTERVALS" :key="i" :value="i">{{ i }}</option>
                </select>
              </div>
            </div>

            <div class="mt-4 flex flex-wrap items-end gap-2">
              <div v-if="!customRange" class="flex flex-wrap gap-1.5">
                <button v-for="d in [30, 90, 180, 365, 730]" :key="d" class="chip !h-8" :class="{ 'is-active': runDays === d }" @click="presetDays(d)">
                  {{ d === 365 ? '1 年' : d === 730 ? '2 年' : `${d} 天` }}
                </button>
              </div>
              <div v-else class="grid flex-1 grid-cols-2 gap-3">
                <div class="field"><label class="label" for="bt-start">开始</label><input id="bt-start" v-model="customStart" type="datetime-local" class="input" :max="customEnd" /></div>
                <div class="field"><label class="label" for="bt-end">结束</label><input id="bt-end" v-model="customEnd" type="datetime-local" class="input" :min="customStart" /></div>
              </div>
              <button class="chip !h-8" :class="{ 'is-active': customRange }" @click="toggleCustomRange">
                {{ customRange ? '预设区间' : '自定义区间' }}
              </button>
            </div>

            <div class="mt-4 flex items-center justify-between gap-3 border-t border-border pt-4">
              <p class="text-xs leading-relaxed text-subtle">
                需要 <span class="font-semibold text-fg">{{ runSymbol.toUpperCase() }}</span> 的 {{ intervalLabel(runInterval) }} 历史数据；
                缺失时回测会报“no data”，请先到 K 线下载补齐。
              </p>
              <button class="btn btn-primary shrink-0 px-5" :disabled="submitting" @click="submitRun">
                <LoaderCircle v-if="submitting" :size="15" class="animate-spin" />
                <Play v-else :size="15" />
                {{ submitting ? '提交中…' : activeVersion ? '运行回测' : '先创建版本' }}
              </button>
            </div>
          </Panel>

          <!-- 回测结果 -->
          <div v-if="detail && detail.backtest" class="flex flex-col gap-6">
            <div v-if="['queued', 'pending', 'running'].includes(detail.backtest.status)" class="panel flex flex-col items-center gap-3 px-6 py-12 text-center">
              <LoaderCircle :size="26" class="animate-spin text-primary" />
              <div>
                <p class="text-sm font-semibold text-fg">
                  {{ detail.backtest.status === 'queued' ? '任务排队中' : '正在回测…' }}
                </p>
                <p class="mt-1 text-xs text-muted">
                  {{ detail.backtest.stage === 'loading_data' ? '读取本地 K 线' : detail.backtest.stage === 'backtesting' ? '逐 bar 撮合中' : detail.backtest.stage === 'persisting' ? '写入资金曲线与成交' : '准备中' }}
                </p>
              </div>
              <div class="h-1.5 w-56 overflow-hidden rounded-full bg-surface-2">
                <div class="h-full rounded-full bg-primary transition-all duration-500" :style="{ width: `${detail.backtest.progress_pct ?? 0}%` }" />
              </div>
            </div>

            <div v-else-if="detail.backtest.status === 'failed'" class="panel">
              <StateView icon="error" title="这次回测失败了" :desc="detail.backtest.error || '未知错误'">
                <template #action>
                  <router-link to="/data/downloads" class="btn btn-ghost">检查历史数据</router-link>
                </template>
              </StateView>
            </div>

            <template v-else>
              <!-- 结果 meta -->
              <div class="flex flex-wrap items-center gap-x-5 gap-y-2 rounded-xl border border-border bg-surface px-4 py-3 text-xs">
                <span class="flex items-center gap-2 font-semibold text-fg">
                  <FlaskConical :size="14" class="text-primary" />
                  {{ resultMeta?.symbol }} <span class="font-mono text-muted">{{ resultMeta?.interval }}</span>
                </span>
                <span class="text-subtle">{{ resultMeta?.range }}</span>
                <span v-if="resultMeta?.bars" class="num text-subtle">{{ resultMeta.bars }} bars</span>
                <span class="ml-auto flex items-center gap-1.5 text-subtle">
                  <RefreshCw :size="11" />完成于 {{ resultMeta?.finished }}
                </span>
              </div>

              <!-- 绩效读数台：三个决定性数字打头，其余降为辅助格 -->
              <section class="panel px-5 py-4">
                <div class="mb-3 flex flex-wrap items-center justify-between gap-2">
                  <div>
                    <p class="panel-title">绩效读数</p>
                    <p class="mt-0.5 text-[11px] text-subtle">先看收益与回撤，再往下核对夏普、胜率与成交质量</p>
                  </div>
                  <span v-if="detail.backtest.summary?.final_equity" class="badge tone-primary">
                    终值 ${{ Number(detail.backtest.summary.final_equity).toLocaleString('en-US', { maximumFractionDigits: 0 }) }}
                  </span>
                </div>

                <div class="grid grid-cols-1 gap-3 sm:grid-cols-3">
                  <div
                    v-for="c in primaryMetrics"
                    :key="c.key"
                    class="rounded-xl border px-4 py-3.5"
                    :class="c.key === 'max_drawdown' ? 'border-border bg-danger/[0.05]' : 'border-border bg-surface-2/50'"
                  >
                    <p class="text-[11px] font-medium uppercase tracking-wider text-subtle">{{ c.label }}</p>
                    <p class="num mt-1.5 text-[28px] font-semibold leading-none tracking-tight" :class="metricTone(c)">{{ metricText(c) }}</p>
                  </div>
                </div>

                <div class="mt-4 grid grid-cols-2 gap-x-6 gap-y-2 border-t border-border pt-3 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-7">
                  <div v-for="c in readoutMetrics" :key="c.key" class="flex items-baseline justify-between gap-2 py-0.5">
                    <span class="text-[11px] text-subtle">{{ c.label }}</span>
                    <span class="num text-sm font-semibold" :class="metricTone(c)">{{ metricText(c) }}</span>
                  </div>
                </div>
              </section>

              <!-- 资金曲线：证据的中心 -->
              <Panel title="资金曲线" :sub="`初始资金 $100,000 · 抽样 ≤1500 点`">
                <EquityChart v-if="equityPoints.length" :points="equityPoints" :baseline="100000" :height="360" />
                <StateView v-else icon="chart" title="回测没有产出净值数据" desc="可能区间内没有发生交易，或历史数据为空。" />
              </Panel>

              <!-- 成交明细 / 回测日志 -->
              <Panel v-if="detail.trades?.length" title="成交明细（回测日志）" :sub="`最近 ${detail.trades.length} 笔 · 成交单价 / 买入均价 / 已实现 PnL（摊薄成本）`" :pad="'none'" flush>
                <div class="max-h-96 overflow-auto">
                  <table class="tbl">
                    <thead>
                          <tr>
                            <th>时间</th>
                            <th>币种</th>
                            <th>操作</th>
                            <th class="text-right">成交单价</th>
                            <th class="text-right">买入均价</th>
                            <th class="text-right">数量</th>
                            <th class="text-right">手续费</th>
                            <th class="text-right">PnL</th>
                          </tr>
                        </thead>
                        <tbody>
                          <tr v-for="t in detail.trades" :key="t.id">
                            <td class="tbl-sub font-mono">{{ fmtTime(t.ts, true) }}</td>
                            <td class="font-mono text-[11px] font-semibold text-fg">{{ t.symbol }}</td>
                            <td>
                              <span class="flex items-center gap-1.5">
                                <span class="badge" :class="t.side === 'buy' ? 'tone-up' : 'tone-down'">
                                  {{ t.side === 'buy' ? '买入' : '卖出' }}
                                </span>
                                <span v-if="t.reason" class="text-[11px] text-subtle">{{ t.reason }}</span>
                              </span>
                              <span v-if="t.position != null" class="mt-0.5 block text-[10px] text-subtle">仓后 {{ t.position.toFixed(3) }}</span>
                            </td>
                            <td class="num tbl-num text-fg">{{ t.price.toLocaleString('en-US', { maximumFractionDigits: 6 }) }}</td>
                            <td class="num tbl-num text-muted">{{ t.basis != null ? t.basis.toLocaleString('en-US', { maximumFractionDigits: 6 }) : '—' }}</td>
                            <td class="num tbl-num text-muted">{{ t.qty.toFixed(6) }}</td>
                            <td class="num tbl-num text-subtle">{{ t.fee.toFixed(4) }}</td>
                            <td class="num tbl-num font-semibold" :class="t.pnl > 0 ? 'text-up' : t.pnl < 0 ? 'text-down' : 'text-muted'">
                              {{ t.pnl > 0 ? '+' : '' }}{{ t.pnl.toLocaleString('en-US', { maximumFractionDigits: 2 }) }}
                            </td>
                          </tr>
                        </tbody>
                  </table>
                </div>
              </Panel>
            </template>
          </div>
        </div>
      </div>
    </template>

    <!-- 版本创建弹窗 -->
    <Modal :open="showVersionModal" :title="versions.length ? '创建新版本' : '创建版本 v1'" sub="模板定义了交易信号，参数控制灵敏度。版本一经创建即可被回测引用。" width="640px" @close="showVersionModal = false">
      <div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div class="sm:col-span-2">
          <p class="mb-2 text-xs font-semibold uppercase tracking-wider text-subtle">选择模板</p>
          <div class="grid grid-cols-1 gap-2 sm:grid-cols-2">
            <label
              v-for="(tpl, tid) in templates"
              :key="tid"
              class="flex cursor-pointer items-start gap-2.5 rounded-xl border p-3 transition-colors"
              :class="newTemplate === tid ? 'tint-faint border-primary' : 'border-border hover:border-strong'"
            >
              <input type="radio" :value="tid" v-model="newTemplate" class="mt-0.5 accent-[var(--color-primary)]" @change="pickTemplate(tid)" />
              <span class="min-w-0">
                <span class="block text-[13px] font-semibold text-fg">{{ tpl.title }}</span>
                <span class="mt-0.5 block text-xs leading-snug text-muted">{{ tpl.description }}</span>
                <span v-if="tpl.logic" class="mt-1.5 block rounded-md border border-border bg-inset px-2 py-1.5 font-mono text-[11px] leading-relaxed text-muted">
                  {{ tpl.logic }}
                </span>
                <span v-if="tpl.long_short" class="badge tone-warn mt-1.5">支持做空</span>
              </span>
            </label>
          </div>
        </div>
        <div class="sm:col-span-2">
          <p class="mb-2 text-xs font-semibold uppercase tracking-wider text-subtle">参数</p>
          <div v-if="Object.keys(schemaOf(newTemplate)).length" class="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <div v-for="(spec, name) in schemaOf(newTemplate)" :key="name" class="field">
              <label class="label" :for="`p-${name}`">{{ name }}</label>
              <input
                :id="`p-${name}`"
                type="number"
                class="input font-mono"
                :value="newParams[name]"
                :min="spec.min"
                :max="spec.max"
                :step="spec.type === 'int' ? 1 : 'any'"
                @input="newParams[name] = Number(($event.target as HTMLInputElement).value)"
              />
              <p class="text-[10px] text-subtle">{{ spec.min }} ~ {{ spec.max }}</p>
            </div>
          </div>
          <p v-else class="text-xs text-muted">该模板无参数。</p>
        </div>
      </div>
      <template #footer>
        <div class="flex items-center justify-between gap-2">
          <p class="text-xs leading-relaxed text-subtle">
            版本 = 「模板 + 参数」的可追溯快照，供回测引用与 A/B 对比。
            Python 代码编辑器将在后续里程碑提供；当前阶段“写策略”= 选模板 + 调参。
          </p>
          <div class="flex gap-2">
            <button class="btn btn-ghost" @click="showVersionModal = false">取消</button>
            <button class="btn btn-primary" :disabled="creating || !newTemplate" @click="submitVersion">
              {{ creating ? '创建中…' : '创建版本' }}
            </button>
          </div>
        </div>
      </template>
    </Modal>
  </div>
</template>
