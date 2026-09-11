<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import {
  Boxes,
  ChartLine,
  Check,
  ChevronDown,
  ChevronRight,
  Cpu,
  FlaskConical,
  GitBranch,
  LoaderCircle,
  Play,
  RefreshCw,
  Square,
  TableProperties,
  Trash2,
  TrendingUp,
} from '@lucide/vue'
import PageHeader from '@/components/ui/PageHeader.vue'
import Modal from '@/components/ui/Modal.vue'
import StateView from '@/components/ui/StateView.vue'
import TrainCharts from '@/views/train/TrainCharts.vue'
import { useToastStore } from '@/stores/toast'
import {
  deletePipeline,
  getPipeline,
  listPipelines,
  startPipeline,
  stopPipeline,
  type PipelineDiagnosis,
  type PipelineRound,
  type PipelineRun,
} from '@/api/pipeline'
import type { MlLearning, MlOosCharts } from '@/api/factor'
import { fmtStamp, MAJOR_SYMBOLS } from '@/utils/format'

const toast = useToastStore()

const STAGES = [
  { key: 'gen', label: '因子生成' },
  { key: 'research', label: '因子研究' },
  { key: 'select', label: '因子选择' },
  { key: 'train', label: '模型训练' },
  { key: 'backtest', label: '数据回测' },
  { key: 'evaluate', label: '效果评估' },
  { key: 'finalize', label: '入库/诊断' },
] as const

// —— 表单 ——
function localDate(d: Date): string {
  const p = (x: number) => String(x).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}
function daysAgo(n: number): string {
  return localDate(new Date(Date.now() - n * 86400000))
}
function isoOf(dateStr: string, endOfDay = false): string {
  return new Date(`${dateStr}T${endOfDay ? '23:59:59' : '00:00:00'}`).toISOString()
}

/** 数字输入框被清空时 v-model.number 会得到 ''，直接提交会触发 422；这里夹取到合法区间并给默认值兜底。 */
function clampInt(v: unknown, min: number, max: number, fallback: number): number {
  const n = Math.round(Number(v))
  if (!Number.isFinite(n)) return fallback
  return Math.min(max, Math.max(min, n))
}

/** 把后端错误（含 FastAPI 422 的 detail 数组）转成可读文案。 */
function errText(e: unknown): string {
  const err = e as { message?: string; detail?: unknown }
  if (typeof err?.message === 'string' && err.message) return err.message
  if (Array.isArray(err?.detail)) {
    return err.detail
      .map((d) => {
        const item = d as { loc?: unknown[]; msg?: string }
        const loc = Array.isArray(item.loc) ? item.loc.slice(1).join('.') : ''
        return loc ? `${loc}: ${item.msg ?? ''}` : (item.msg ?? '')
      })
      .filter(Boolean)
      .join('；')
  }
  return '未知错误'
}

const form = reactive({
  name: '',
  objective: '',
  researchSymbols: ['BTCUSDT'] as string[],
  researchInterval: '1d',
  researchStart: daysAgo(700),
  researchEnd: daysAgo(180),
  backtestSymbol: 'BTCUSDT',
  backtestStart: daysAgo(180),
  backtestEnd: localDate(new Date()),
  factorCount: 4,
  maxPick: 6,
  maxRounds: 5,
  model: 'ridge',
  horizon: 1,
  testRatio: 0.25,
  alpha: 1,
  seedEnsemble: 3,
  btWindow: 60,
  btEnterQ: 0.8,
  btExitQ: 0.2,
  stopOnExcellent: true,
  saveQuality: true,
})

// —— 运行状态 ——
const activeRun = ref<PipelineRun | null>(null)
const rounds = ref<PipelineRound[]>([])
const history = ref<PipelineRun[]>([])
const livePhase = ref('')
const liveRound = ref(0)
const starting = ref(false)
const loadingDetail = ref(false)
let es: EventSource | null = null
let sseRunId: string | null = null
let sseAttempt = 0
let sseRetryTimer = 0
let refreshTimer = 0
let pollTimer = 0

const runStatus = computed(() => activeRun.value?.status ?? null)
const running = computed(() => runStatus.value === 'running' || runStatus.value === 'pending')
const selectedTab = ref<'active' | 'history'>('active')

// —— 删除确认 ——
const delOpen = ref(false)
const delRun = ref<PipelineRun | null>(null)
const delBusy = ref(false)
const delErr = ref('')

function openDelete(run: PipelineRun) {
  delRun.value = run
  delErr.value = ''
  delOpen.value = true
}

async function confirmDelete() {
  const run = delRun.value
  if (!run || delBusy.value) return
  delBusy.value = true
  delErr.value = ''
  try {
    await deletePipeline(run.id)
    delOpen.value = false
    toast.success('流水线已删除', run.name)
    if (activeRun.value?.id === run.id) {
      closeSse()
      activeRun.value = null
      rounds.value = []
    }
    await loadHistory()
  } catch (e: unknown) {
    delErr.value = (e as { message?: string }).message ?? '删除失败'
  } finally {
    delBusy.value = false
  }
}

// —— 研究标的多选下拉 ——
const symOpen = ref(false)
const symRef = ref<HTMLElement | null>(null)

function toggleResearchSymbol(s: string) {
  const i = form.researchSymbols.indexOf(s)
  if (i >= 0) form.researchSymbols.splice(i, 1)
  else form.researchSymbols.push(s)
}

function onDocClick(e: MouseEvent) {
  if (symRef.value && !symRef.value.contains(e.target as Node)) symOpen.value = false
}

async function start() {
  if (starting.value || running.value) return
  const syms = (Array.isArray(form.researchSymbols) ? form.researchSymbols : [])
    .map((s) => String(s).trim().toUpperCase())
    .filter(Boolean)
  if (!syms.length) {
    toast.error('请选择研究标的', '至少选择一个交易对，如 BTCUSDT')
    return
  }
  const factorCount = clampInt(form.factorCount, 1, 5, 4)
  const maxPick = clampInt(form.maxPick, 2, 12, 6)
  const maxRounds = clampInt(form.maxRounds, 1, 20, 5)
  const horizon = clampInt(form.horizon, 1, 10, 1)
  if (!form.researchStart || !form.researchEnd || !form.backtestStart || !form.backtestEnd) {
    toast.error('时间窗口不完整', '研究/回测的起止日期都需要填写')
    return
  }
  if (form.researchEnd <= form.researchStart) {
    toast.error('研究窗口无效', '研究结束须晚于研究开始')
    return
  }
  if (form.backtestEnd <= form.backtestStart) {
    toast.error('回测窗口无效', '回测结束须晚于回测开始')
    return
  }
  if (form.backtestStart < form.researchEnd) {
    toast.error('回测窗口未晚于研究窗口', '强制样本外：回测起点须不早于研究结束（避免未来函数）')
    return
  }
  starting.value = true
  try {
    const payload = {
      name: form.name.trim().slice(0, 120) || undefined,
      objective: form.objective.trim().slice(0, 1200) || undefined,
      research_symbols: syms,
      research_interval: form.researchInterval,
      research_start: isoOf(form.researchStart),
      research_end: isoOf(form.researchEnd),
      backtest_symbol: form.backtestSymbol || syms[0],
      backtest_interval: form.researchInterval,
      backtest_start: isoOf(form.backtestStart),
      backtest_end: isoOf(form.backtestEnd, true),
      factor_count_per_round: factorCount,
      max_pick: maxPick,
      max_rounds: maxRounds,
      model: form.model as 'ridge' | 'gbdt',
      horizon,
      test_ratio: form.testRatio,
      alpha: form.alpha,
      seed_ensemble: clampInt(form.seedEnsemble, 1, 10, 3),
      bt_window: clampInt(form.btWindow, 20, 240, 60),
      bt_enter_q: form.btEnterQ,
      bt_exit_q: form.btExitQ,
      stop_on_excellent: form.stopOnExcellent,
      save_quality_factors: form.saveQuality,
    }
    const { run_id } = await startPipeline(payload)
    toast.success('流水线已启动', `run ${run_id.slice(0, 8)} · 后台自动执行，右侧可看进度`)
    selectedTab.value = 'active'
    await loadDetail(run_id)
    openSse(run_id)
  } catch (e: unknown) {
    toast.error('启动失败', errText(e))
  } finally {
    starting.value = false
  }
}

async function stop() {
  if (!activeRun.value) return
  try {
    await stopPipeline(activeRun.value.id)
    toast.info('已请求停止', '下一轮任务开始前将终止')
    await loadDetail(activeRun.value.id)
  } catch (e: unknown) {
    toast.error('停止失败', errText(e))
  }
}

async function loadHistory() {
  try {
    history.value = await listPipelines()
  } catch {
    /* ignore */
  }
}

async function loadDetail(id: string) {
  loadingDetail.value = true
  try {
    const { run, rounds: r } = await getPipeline(id)
    activeRun.value = run
    rounds.value = r
    liveRound.value = run.current_round
  } catch (e: unknown) {
    toast.error('加载失败', errText(e))
  } finally {
    loadingDetail.value = false
  }
}

// SSE 只负责推送「阶段/状态」；每轮的结果（指标/诊断）落库后需要重新拉详情。
// 因此：本轮完成 / 新一轮开始 / 兜底轮询时都刷新详情，无需手动刷新页面。
function scheduleDetailRefresh(id: string, delay = 400) {
  if (refreshTimer) return
  refreshTimer = window.setTimeout(() => {
    refreshTimer = 0
    if (activeRun.value?.id === id) void loadDetail(id)
  }, delay)
}

function startPoll(id: string) {
  stopPoll()
  pollTimer = window.setInterval(() => {
    if (activeRun.value?.id !== id) return stopPoll()
    if (!running.value) return stopPoll()
    void loadDetail(id)
    void loadHistory()
  }, 5000)
}

function stopPoll() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = 0
  }
}

function openSse(id: string) {
  closeSse()
  sseRunId = id
  sseAttempt = 0
  startPoll(id)
  connectSse()
}

function connectSse() {
  const id = sseRunId
  if (!id) return
  const source = new EventSource(`/api/v1/pipeline/${id}/events`)
  es = source
  source.onopen = () => {
    sseAttempt = 0
  }
  source.onmessage = (ev) => {
    if (sseRunId !== id) return
    try {
      const msg = JSON.parse(ev.data) as {
        type?: string
        run_id?: string
        status?: string
        round_no?: number
        phase?: string
        message?: string
        error?: string | null
      }
      if (!msg.run_id || msg.run_id !== id) return
      if (msg.status && activeRun.value && activeRun.value.id === id) {
        activeRun.value.status = msg.status as PipelineRun['status']
      }
      if (typeof msg.round_no === 'number') liveRound.value = msg.round_no
      if (msg.phase) livePhase.value = msg.phase
      if (['succeeded', 'finished', 'failed', 'stopped'].includes(msg.status ?? '')) {
        closeSse()
        void loadDetail(id)
        void loadHistory()
        return
      }
      // 本轮落库完成 → 拉取结果；新一轮开始 → 补出运行中的轮次卡片
      if (msg.message === '本轮完成') {
        scheduleDetailRefresh(id)
        void loadHistory()
      } else if (typeof msg.round_no === 'number' && msg.round_no > rounds.value.length) {
        scheduleDetailRefresh(id)
      }
    } catch {
      /* ignore */
    }
  }
  source.onerror = () => {
    if (es !== source) return
    es = null
    source.close()
    const cur = activeRun.value
    if (sseRunId !== id || !cur || cur.id !== id) return
    if (cur.status !== 'running' && cur.status !== 'pending') return
    // 运行中连接中断：指数退避重连，避免长任务中途丢失进度
    const delay = Math.min(1000 * 2 ** sseAttempt, 15000)
    sseAttempt += 1
    sseRetryTimer = window.setTimeout(() => {
      if (sseRunId === id && !es) connectSse()
    }, delay)
  }
}

function closeSse() {
  sseRunId = null
  stopPoll()
  if (sseRetryTimer) {
    clearTimeout(sseRetryTimer)
    sseRetryTimer = 0
  }
  if (refreshTimer) {
    clearTimeout(refreshTimer)
    refreshTimer = 0
  }
  if (es) {
    es.close()
    es = null
  }
}

function viewRun(run: PipelineRun) {
  selectedTab.value = 'active'
  void loadDetail(run.id)
  if (run.status === 'running' || run.status === 'pending') openSse(run.id)
  else closeSse()
}

// —— 展示辅助 ——
function num(v: number | null | undefined, digits = 4): string {
  return v == null || !Number.isFinite(v) ? '—' : v.toFixed(digits)
}
function signed(v: number | null | undefined, digits = 3): string {
  return v == null || !Number.isFinite(v) ? '—' : (v >= 0 ? '+' : '') + v.toFixed(digits)
}
function pct(v: number | null | undefined): string {
  return v == null || !Number.isFinite(v) ? '—' : (v * 100).toFixed(1) + '%'
}
function tone(v: number | null | undefined): string {
  return (v ?? 0) >= 0 ? 'text-up' : 'text-down'
}
function gradeMeta(g: string): { label: string; tone: string } {
  return (
    { ok: { label: '合格', tone: 'tone-up' }, warn: { label: '一般', tone: 'tone-warn' }, bad: { label: '不理想', tone: 'tone-down' } }[g] ??
    { label: g, tone: 'tone-neutral' }
  )
}
function rootMeta(r: string): { label: string; tone: string } {
  return (
    { params: { label: '训练参数问题', tone: 'tone-primary' }, factor: { label: '因子选择问题', tone: 'tone-warn' }, none: { label: '无', tone: 'tone-up' } }[r] ??
    { label: r, tone: 'tone-neutral' }
  )
}
function stageState(roundNo: number, key: string): 'done' | 'active' | 'pending' {
  if (roundNo < liveRound.value) return 'done'
  if (roundNo > liveRound.value) return 'pending'
  if (!running.value) return 'done'
  const idx = STAGES.findIndex((s) => s.key === key)
  const cur = STAGES.findIndex((s) => s.key === livePhase.value)
  if (idx < cur) return 'done'
  if (idx === cur) return 'active'
  return 'pending'
}
function statusMeta(s: string): { label: string; tone: string } {
  return (
    {
      pending: { label: '排队中', tone: 'tone-neutral' },
      running: { label: '进行中', tone: 'tone-primary' },
      succeeded: { label: '已找到合格模型', tone: 'tone-up' },
      finished: { label: '轮次耗尽', tone: 'tone-neutral' },
      failed: { label: '失败', tone: 'tone-down' },
      stopped: { label: '已停止', tone: 'tone-neutral' },
    }[s] ?? { label: s, tone: 'tone-neutral' }
  )
}
function bestSummary(): string {
  const m = activeRun.value?.best_metrics
  if (!m) return '尚未产出合格模型'
  const parts: string[] = []
  if (m.sharpe != null) parts.push(`夏普 ${m.sharpe.toFixed(2)}`)
  if (m.oos_rank_ic != null) parts.push(`RankIC ${m.oos_rank_ic.toFixed(3)}`)
  if (m.total_return != null) parts.push(`收益 ${(m.total_return * 100).toFixed(1)}%`)
  return parts.join(' · ')
}

function genOnly(r: PipelineRound) {
  const pickedCodes = new Set((r.factors_picked ?? []).map((f) => f.code))
  return (r.factors_generated ?? []).filter((f) => !pickedCodes.has(f.code))
}

function paramsText(d: PipelineDiagnosis): string {
  const p = d.params_applied
  if (!p) return ''
  return Object.entries(p).map(([k, v]) => `${k}=${v}`).join(' · ')
}

// —— 轮次卡片折叠（点击标题切换） ——
const collapsed = reactive<Record<string, boolean>>({})
function toggleRound(id: string) {
  collapsed[id] = !collapsed[id]
}
function isCollapsed(id: string): boolean {
  return !!collapsed[id]
}

// —— 训练详情弹窗 ——
const detailRound = ref<PipelineRound | null>(null)
function openDetail(r: PipelineRound) {
  detailRound.value = r
}
function closeDetail() {
  detailRound.value = null
}
function hasTrainCharts(r: PipelineRound | null): boolean {
  const t = r?.train_result
  if (!t) return false
  return (
    (t.oos_charts?.deciles?.length ?? 0) > 0 ||
    (t.learning?.train?.length ?? 0) > 1 ||
    (t.val_charts?.deciles?.length ?? 0) > 0
  )
}
// 弹窗图表数据：无可用图表时返回 null，便于模板 v-if 收窄类型
const detailCharts = computed<{ oos_charts?: MlOosCharts; learning?: MlLearning } | null>(() => {
  const t = detailRound.value?.train_result
  if (!t) return null
  // 流水线默认不切研究窗内测试集 → 回退用验证集诊断图
  const oc = (t.oos_charts?.deciles?.length ?? 0) > 0 ? t.oos_charts : t.val_charts
  const has = (oc?.deciles?.length ?? 0) > 0 || (t.learning?.train?.length ?? 0) > 1
  return has ? { oos_charts: oc, learning: t.learning } : null
})

// —— 样本数据弹窗 ——
const sampleRound = ref<PipelineRound | null>(null)
const sampleTab = ref<'train' | 'test'>('train')
function hasSamples(r: PipelineRound | null): boolean {
  const t = r?.train_result
  return !!t && ((t.sample_train?.length ?? 0) > 0 || (t.sample_test?.length ?? 0) > 0)
}
function openSamples(r: PipelineRound) {
  sampleRound.value = r
  sampleTab.value = (r.train_result?.sample_train?.length ?? 0) > 0 ? 'train' : 'test'
}
function closeSamples() {
  sampleRound.value = null
}
const sampleColumns = computed<string[]>(() => {
  const t = sampleRound.value?.train_result
  const cols = t?.sample_columns
  if (cols?.length) return cols
  const rows = sampleTab.value === 'train' ? t?.sample_train : t?.sample_test
  const first = rows?.[0]
  return first ? Object.keys(first).filter((k) => k !== 'symbol' && k !== 'ts') : []
})
const sampleRows = computed<Array<Record<string, number | string>>>(() => {
  const t = sampleRound.value?.train_result
  const rows = sampleTab.value === 'train' ? t?.sample_train : t?.sample_test
  return rows ?? []
})
function fmtSampleCell(v: number | string | undefined): string {
  if (v == null) return '—'
  if (typeof v !== 'number') return String(v)
  if (v === 0) return '0'
  const abs = Math.abs(v)
  if (abs >= 1e-3 && abs < 1e5) return v.toFixed(6).replace(/\.?0+$/, '')
  return v.toExponential(4)
}
function factorLabel(code: string): string {
  if (code === 'label') return '标签(未来收益)'
  const r = sampleRound.value
  const f = [...(r?.factors_picked ?? []), ...(r?.factors_generated ?? [])].find((x) => x.code === code)
  return f?.name || code
}

// 训练参数概要：优先用当轮实际生效的 config 快照（含参数调优后的覆盖），历史轮次回退到流水线配置。
const TRAIN_PARAM_LABELS: Array<[string, string]> = [
  ['model', '模型'],
  ['horizon', '预测窗口 h'],
  ['test_ratio', '测试占比'],
  ['val_ratio', '验证占比'],
  ['alpha', 'alpha/L2'],
  ['max_corr', '相关性上限'],
  ['winsor', 'Winsor'],
  ['cost_bps', '成本 bps'],
  ['learning_rate', '学习率'],
  ['n_estimators', '树数量'],
  ['max_depth', '最大深度'],
  ['num_leaves', '叶子数'],
  ['subsample', '行采样'],
  ['colsample_bytree', '列采样'],
  ['fine_interval', '细粒度底库'],
  ['agg_features', '聚合特征'],
]
function trainParams(r: PipelineRound): Array<{ k: string; label: string; v: string }> {
  const cfg = (r.train_result?.config ??
    (activeRun.value?.config as unknown as Record<string, unknown>) ??
    {}) as Record<string, unknown>
  const out: Array<{ k: string; label: string; v: string }> = []
  for (const [k, label] of TRAIN_PARAM_LABELS) {
    const val = cfg[k]
    if (val == null || val === '') continue
    if (Array.isArray(val)) {
      if (!val.length) continue
      out.push({ k, label, v: val.join('/') })
    } else {
      out.push({ k, label, v: String(val) })
    }
  }
  return out
}
function dataVol(r: PipelineRound): string {
  const t = r.train_result
  if (!t) return ''
  const parts: string[] = []
  if (t.n_train != null) parts.push(`训练 ${t.n_train}`)
  if (t.n_val) parts.push(`验证 ${t.n_val}`)
  if (t.n_test != null) parts.push(`测试 ${t.n_test}`)
  return parts.join(' · ')
}

onMounted(async () => {
  document.addEventListener('mousedown', onDocClick)
  await loadHistory()
  // 刷新/首次进入时自动恢复：优先正在运行的流水线，否则展示最近一次
  const target =
    history.value.find((r) => r.status === 'running' || r.status === 'pending') ?? history.value[0]
  if (!target) return
  selectedTab.value = 'active'
  await loadDetail(target.id)
  if (target.status === 'running' || target.status === 'pending') openSse(target.id)
})
onBeforeUnmount(() => {
  closeSse()
  document.removeEventListener('mousedown', onDocClick)
})
</script>

<template>
  <div class="flex flex-col gap-6">
    <PageHeader
      eyebrow="AI Pipeline"
      title="AI 流水线"
      desc="一键驱动「因子生成 → 因子选择 → 模型训练 → 数据回测 → 效果评估」多轮自进化，好模型自动入库，差模型由 AI 诊断是训练参数问题还是因子选择问题后进入下一轮，直至耗完轮次。"
    >
      <template #actions>
        <router-link to="/models" class="btn btn-ghost">模型库</router-link>
        <router-link to="/factor" class="btn btn-ghost">因子库</router-link>
      </template>
    </PageHeader>

    <div class="grid grid-cols-1 gap-6 xl:grid-cols-[360px_1fr]">
      <!-- 左：配置 -->
      <section class="panel h-fit p-5">
        <h2 class="text-sm font-semibold text-fg">流水线配置</h2>

        <div class="mt-4 space-y-3.5">
          <div class="field">
            <label class="label" for="pipeline-name">名称（选填）</label>
            <input id="pipeline-name" v-model="form.name" class="input" placeholder="自动生成" />
          </div>
          <div class="field">
            <label class="label" for="pipeline-objective">研究目标（选填，空则每轮随机灵感）</label>
            <textarea id="pipeline-objective" v-model="form.objective" rows="2" class="input resize-none" placeholder="如：捕捉放量突破后的趋势延续，年化≥20%、回撤≤15%" />
          </div>

          <div class="grid grid-cols-2 gap-2.5">
            <div class="field">
              <label class="label">研究标的（可多选）</label>
              <div ref="symRef" class="relative">
                <button
                  id="pipeline-symbols"
                  type="button"
                  class="input flex !h-auto min-h-[34px] cursor-pointer flex-wrap items-center gap-1 py-1 text-left"
                  :aria-expanded="symOpen"
                  @click="symOpen = !symOpen"
                >
                  <span
                    v-for="s in form.researchSymbols"
                    :key="s"
                    class="badge tone-primary !h-[18px] !px-1.5 font-mono text-[10px]"
                  >
                    {{ s }}
                  </span>
                  <span v-if="!form.researchSymbols.length" class="text-subtle">选择交易对…</span>
                  <ChevronDown :size="14" class="ml-auto shrink-0 text-subtle" />
                </button>
                <div
                  v-if="symOpen"
                  class="absolute z-20 mt-1 max-h-56 w-full overflow-y-auto rounded-lg border border-border bg-surface p-1 shadow-pop"
                >
                  <button
                    v-for="s in MAJOR_SYMBOLS"
                    :key="s"
                    type="button"
                    class="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left font-mono text-xs transition-colors hover:bg-surface-2"
                    @click="toggleResearchSymbol(s)"
                  >
                    <span
                      class="flex h-4 w-4 shrink-0 items-center justify-center rounded border transition-colors"
                      :class="
                        form.researchSymbols.includes(s)
                          ? 'border-primary bg-primary text-white'
                          : 'border-strong bg-surface'
                      "
                    >
                      <Check v-if="form.researchSymbols.includes(s)" :size="11" />
                    </span>
                    {{ s }}
                  </button>
                </div>
              </div>
            </div>
            <div class="field">
              <label class="label" for="pipeline-research-interval">研究粒度</label>
              <select id="pipeline-research-interval" v-model="form.researchInterval" class="select font-mono">
                <option value="15m">15m</option><option value="30m">30m</option><option value="1h">1h</option><option value="4h">4h</option><option value="1d">1d</option>
              </select>
            </div>
          </div>

          <div class="grid grid-cols-2 gap-2.5">
            <div class="field">
              <label class="label" for="pipeline-research-start">测试（研究）起点</label>
              <input id="pipeline-research-start" v-model="form.researchStart" type="date" class="input font-mono" :max="form.researchEnd" />
            </div>
            <div class="field">
              <label class="label" for="pipeline-research-end">测试（研究）结束</label>
              <input id="pipeline-research-end" v-model="form.researchEnd" type="date" class="input font-mono" :min="form.researchStart" />
            </div>
          </div>

          <div class="grid grid-cols-2 gap-2.5">
            <div class="field">
              <label class="label" for="pipeline-backtest-symbol">回测标的</label>
              <select id="pipeline-backtest-symbol" v-model="form.backtestSymbol" class="select font-mono">
                <option v-for="s in MAJOR_SYMBOLS" :key="s" :value="s">{{ s }}</option>
              </select>
            </div>
            <div class="field">
              <label class="label" for="pipeline-backtest-interval">回测粒度</label>
              <input id="pipeline-backtest-interval" :value="form.researchInterval" class="input font-mono" readonly disabled />
              <p class="mt-1 text-[11px] text-subtle">跟随研究粒度（模型特征绑定 interval，换粒度会导致因子口径漂移）</p>
            </div>
          </div>

          <div class="grid grid-cols-2 gap-2.5">
            <div class="field">
              <label class="label" for="pipeline-backtest-start">回测起点（晚于结束）</label>
              <input id="pipeline-backtest-start" v-model="form.backtestStart" type="date" class="input font-mono" :min="form.researchEnd" :max="form.backtestEnd" />
            </div>
            <div class="field">
              <label class="label" for="pipeline-backtest-end">回测结束</label>
              <input id="pipeline-backtest-end" v-model="form.backtestEnd" type="date" class="input font-mono" :min="form.backtestStart" />
            </div>
          </div>

          <div class="grid grid-cols-3 gap-2.5">
            <div class="field">
              <label class="label" for="pipeline-factor-count">每轮因子数</label>
              <input id="pipeline-factor-count" v-model.number="form.factorCount" type="number" min="1" max="5" class="input font-mono" />
            </div>
            <div class="field">
              <label class="label" for="pipeline-max-pick">进模型因子上限</label>
              <input id="pipeline-max-pick" v-model.number="form.maxPick" type="number" min="2" max="12" class="input font-mono" />
            </div>
            <div class="field">
              <label class="label" for="pipeline-max-rounds">流水线轮次</label>
              <input id="pipeline-max-rounds" v-model.number="form.maxRounds" type="number" min="1" max="20" class="input font-mono" />
            </div>
          </div>

          <div class="grid grid-cols-2 gap-2.5">
            <div class="field">
              <label class="label" for="pipeline-model">模型</label>
              <select id="pipeline-model" v-model="form.model" class="select font-mono">
                <option value="ridge">Ridge（线性）</option>
                <option value="gbdt">LightGBM（gbdt）</option>
              </select>
            </div>
            <div class="field">
              <label class="label" for="pipeline-horizon">预测窗口 h</label>
              <input id="pipeline-horizon" v-model.number="form.horizon" type="number" min="1" max="10" class="input font-mono" />
            </div>
          </div>

          <div class="grid grid-cols-2 gap-2.5">
            <div class="field">
              <label class="label" for="pipeline-seed">集成种子数</label>
              <input id="pipeline-seed" v-model.number="form.seedEnsemble" type="number" min="1" max="10" class="input font-mono" />
            </div>
            <div class="field">
              <label class="label" for="pipeline-btw">回测分位窗口</label>
              <input id="pipeline-btw" v-model.number="form.btWindow" type="number" min="20" max="240" class="input font-mono" />
            </div>
          </div>

          <div class="grid grid-cols-2 gap-2.5">
            <div class="field">
              <label class="label" for="pipeline-bte">入场分位</label>
              <input id="pipeline-bte" v-model.number="form.btEnterQ" type="number" min="0.5" max="0.95" step="0.05" class="input font-mono" />
            </div>
            <div class="field">
              <label class="label" for="pipeline-btx">离场分位</label>
              <input id="pipeline-btx" v-model.number="form.btExitQ" type="number" min="0.05" max="0.5" step="0.05" class="input font-mono" />
            </div>
          </div>

          <label class="mt-1 flex items-center gap-2 text-xs text-muted">
            <input v-model="form.stopOnExcellent" type="checkbox" class="accent-[var(--color-primary)]" />
            达到「优秀」阈值时提前终止
          </label>
          <label class="mt-1 flex items-center gap-2 text-xs text-muted">
            <input v-model="form.saveQuality" type="checkbox" class="accent-[var(--color-primary)]" />
            优质因子入因子库（打「优质」标签）
          </label>
        </div>

        <button id="pipeline-start" class="btn btn-primary mt-5 w-full" :disabled="starting || running" @click="start">
          <LoaderCircle v-if="starting" :size="15" class="animate-spin" />
          <Play v-else :size="15" />
          {{ starting ? '启动中…' : running ? '流水线运行中' : '启动 AI 流水线' }}
        </button>
        <p class="mt-2.5 text-[11px] leading-relaxed text-subtle">
          流水线在后台异步执行（一轮 = 一个任务）。含多次 LLM 调用 + 训练 + 回测，单轮可能耗时数分钟；建议先在「模型设置」配好默认 LLM 供应商。
        </p>
      </section>

      <!-- 右：运行 + 轮次 + 历史 -->
      <section class="min-w-0">
        <!-- 标签页 -->
        <nav class="mb-3 flex items-center gap-1">
          <button
            class="chip"
            :class="selectedTab === 'active' ? 'is-active' : ''"
            @click="selectedTab = 'active'"
          >
            当前运行
            <span v-if="activeRun" class="num opacity-80">{{ rounds.length }}</span>
          </button>
          <button
            class="chip"
            :class="selectedTab === 'history' ? 'is-active' : ''"
            @click="selectedTab = 'history'"
          >
            历史
            <span class="num opacity-80">{{ history.length }}</span>
          </button>
        </nav>

        <!-- 当前运行 -->
        <div v-if="selectedTab === 'active'">
          <StateView
            v-if="!activeRun"
            icon="spark"
            title="还没有流水线运行"
            desc="在左侧配置参数后点击「启动 AI 流水线」，即可在后台自动完成多轮因子→模型→回测→评估闭环。"
          />

          <template v-else>
            <div class="panel mb-3 flex flex-wrap items-center gap-3 p-4">
              <div class="min-w-0 flex-1">
                <div class="flex items-center gap-2">
                  <GitBranch :size="15" class="text-ai" />
                  <span class="truncate text-sm font-semibold text-fg">{{ activeRun.name }}</span>
                  <span class="badge" :class="statusMeta(activeRun.status).tone">{{ statusMeta(activeRun.status).label }}</span>
                </div>
                <p class="mt-1 text-xs text-subtle">
                  轮次 {{ activeRun.current_round }}/{{ activeRun.config.max_rounds }}
                  <template v-if="running && livePhase"> · 当前阶段 {{ STAGES.find((s) => s.key === livePhase)?.label }}</template>
                  <template v-if="activeRun.error"> · <span class="text-down">{{ activeRun.error }}</span></template>
                </p>
                <p class="mt-1 text-xs text-muted">最佳模型：{{ bestSummary() }}</p>
              </div>
              <button v-if="running" class="btn btn-ghost" @click="stop">
                <Square :size="14" />停止
              </button>
              <button v-else class="btn btn-ghost" @click="viewRun(activeRun)">
                <RefreshCw :size="14" />刷新
              </button>
            </div>

            <StateView v-if="loadingDetail && !rounds.length" icon="refresh" title="加载中…" busy />

            <div v-if="!rounds.length && !loadingDetail" class="panel p-4 text-center text-xs text-subtle">
              尚未产生轮次记录（worker 可能未启动或任务排队中）。
            </div>

            <div v-else class="space-y-3">
              <div v-for="r in rounds" :key="r.id" class="panel overflow-hidden">
                <!-- 轮次头（点击标题折叠/展开） -->
                <button
                  type="button"
                  class="flex w-full flex-wrap items-center gap-2 border-b border-border px-4 py-2.5 text-left transition-colors hover:bg-surface-2/50"
                  :aria-expanded="!isCollapsed(r.id)"
                  @click="toggleRound(r.id)"
                >
                  <ChevronDown
                    :size="14"
                    class="shrink-0 text-subtle transition-transform"
                    :class="isCollapsed(r.id) ? '-rotate-90' : ''"
                  />
                  <span class="flex items-center gap-1.5 text-sm font-semibold text-fg">
                    <span class="flex h-5 w-5 items-center justify-center rounded-md tint-primary text-primary-ink text-[11px]">{{ r.round_no }}</span>
                    第 {{ r.round_no }} 轮
                  </span>
                  <span v-if="r.diagnosis" class="badge" :class="gradeMeta(r.diagnosis.grade).tone">{{ gradeMeta(r.diagnosis.grade).label }}</span>
                  <span v-if="r.diagnosis" class="badge" :class="rootMeta(r.diagnosis.root_cause).tone">{{ rootMeta(r.diagnosis.root_cause).label }}</span>
                  <span v-if="r.diagnosis?.param_attempts" class="badge tone-primary">参数调优 {{ r.diagnosis.param_attempts }} 次</span>
                  <span v-if="r.model_id" class="badge tone-up">已入库</span>
                  <span v-if="r.error" class="badge tone-down">异常</span>
                  <span class="ml-auto text-[11px] text-subtle">{{ fmtStamp(r.created_at) }}</span>
                </button>

                <div v-show="!isCollapsed(r.id)">
                <!-- 阶段步骤条 -->
                <div class="flex flex-wrap items-center gap-x-1 gap-y-1.5 px-4 py-2.5">
                  <template v-for="(s, i) in STAGES" :key="s.key">
                    <span
                      class="flex items-center gap-1 rounded-md px-1.5 py-1 text-[11px]"
                      :class="
                        stageState(r.round_no, s.key) === 'done'
                          ? 'text-up'
                          : stageState(r.round_no, s.key) === 'active'
                            ? 'bg-surface-2 font-semibold text-primary'
                            : 'text-subtle'
                      "
                    >
                      <Check v-if="stageState(r.round_no, s.key) === 'done'" :size="11" />
                      <LoaderCircle v-else-if="stageState(r.round_no, s.key) === 'active'" :size="11" class="animate-spin" />
                      <span v-else class="text-[9px]">·</span>
                      {{ s.label }}
                    </span>
                    <ChevronRight v-if="i < STAGES.length - 1" :size="10" class="text-subtle" />
                  </template>
                </div>

                <!-- 指标卡 -->
                <div v-if="r.train_result" class="grid grid-cols-3 gap-2 border-t border-border px-4 py-3 sm:grid-cols-6">
                  <div class="rounded-lg bg-surface-2 px-2.5 py-2">
                    <p class="text-[10px] uppercase tracking-wider text-subtle">OOS RankIC</p>
                    <p class="num text-sm font-semibold" :class="tone(r.train_result.oos_rank_ic)">{{ signed(r.train_result.oos_rank_ic) }}</p>
                  </div>
                  <div class="rounded-lg bg-surface-2 px-2.5 py-2">
                    <p class="text-[10px] uppercase tracking-wider text-subtle">命中率</p>
                    <p class="num text-sm font-semibold text-fg">{{ pct(r.train_result.hit_rate) }}</p>
                  </div>
                  <div class="rounded-lg bg-surface-2 px-2.5 py-2">
                    <p class="text-[10px] uppercase tracking-wider text-subtle">Top−Btm</p>
                    <p class="num text-sm font-semibold" :class="tone(r.train_result.spread)">{{ signed(r.train_result.spread, 5) }}</p>
                  </div>
                  <div class="rounded-lg bg-surface-2 px-2.5 py-2">
                    <p class="text-[10px] uppercase tracking-wider text-subtle">回测夏普</p>
                    <p class="num text-sm font-semibold" :class="tone(r.backtest_summary?.metrics?.sharpe)">{{ num(r.backtest_summary?.metrics?.sharpe, 2) }}</p>
                  </div>
                  <div class="rounded-lg bg-surface-2 px-2.5 py-2">
                    <p class="text-[10px] uppercase tracking-wider text-subtle">回测收益</p>
                    <p class="num text-sm font-semibold" :class="tone(r.backtest_summary?.metrics?.total_return)">{{ pct(r.backtest_summary?.metrics?.total_return) }}</p>
                  </div>
                  <div class="rounded-lg bg-surface-2 px-2.5 py-2">
                    <p class="text-[10px] uppercase tracking-wider text-subtle">最大回撤</p>
                    <p class="num text-sm font-semibold text-down">{{ pct(r.backtest_summary?.metrics?.max_drawdown) }}</p>
                  </div>
                </div>

                <!-- 训练参数概要 + 数据量 -->
                <div v-if="r.train_result" class="border-t border-border px-4 py-2.5">
                  <div class="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px]">
                    <span class="font-semibold text-fg">训练参数</span>
                    <span v-if="r.train_result.model" class="text-muted">
                      模型 <span class="font-mono text-fg">{{ r.train_result.model }}</span>
                      <span v-if="r.train_result.engine" class="text-subtle">（{{ r.train_result.engine }}）</span>
                    </span>
                    <span v-if="r.train_result.interval" class="text-muted">粒度 <span class="font-mono text-fg">{{ r.train_result.interval }}</span></span>
                    <span v-if="r.train_result.horizon != null" class="text-muted">h=<span class="font-mono text-fg">{{ r.train_result.horizon }}</span></span>
                    <span v-if="r.train_result.symbols?.length" class="text-muted">标的 <span class="font-mono text-fg">{{ r.train_result.symbols.join('/') }}</span></span>
                    <span v-if="r.train_result.n_features_kept != null" class="text-muted">特征 <span class="font-mono text-fg">{{ r.train_result.n_features_kept }}</span></span>
                    <span v-if="dataVol(r)" class="text-muted">数据量 <span class="num text-fg">{{ dataVol(r) }}</span></span>
                    <div class="ml-auto flex gap-1.5">
                      <button
                        v-if="hasSamples(r)"
                        class="btn btn-soft !h-7 !px-2.5 text-[11px]"
                        @click="openSamples(r)"
                      >
                        <TableProperties :size="12" />样本数据
                      </button>
                      <button
                        v-if="hasTrainCharts(r)"
                        class="btn btn-soft !h-7 !px-2.5 text-[11px]"
                        @click="openDetail(r)"
                      >
                        <ChartLine :size="12" />训练详情
                      </button>
                    </div>
                  </div>
                  <div v-if="trainParams(r).length" class="mt-1.5 flex flex-wrap gap-1.5">
                    <span v-for="p in trainParams(r)" :key="p.k" class="chip !h-6 font-mono text-[10px]">
                      <span class="text-subtle">{{ p.label }}</span>&nbsp;{{ p.v }}
                    </span>
                  </div>
                </div>

                <!-- 因子详情（公式 + 描述） -->
                <details
                  v-if="(r.factors_generated?.length || r.factors_picked?.length)"
                  class="border-t border-border px-4 py-2.5"
                >
                  <summary class="cursor-pointer select-none text-xs font-semibold text-fg">
                    <Boxes :size="12" class="mr-1 inline text-ai" />
                    因子（生成 {{ r.factors_generated?.length ?? 0 }} · 进入模型 {{ r.factors_picked?.length ?? 0 }}）
                  </summary>
                  <div class="mt-2.5 space-y-2">
                    <div
                      v-for="f in r.factors_picked"
                      :key="'p' + f.code"
                      class="rounded-lg border border-primary/30 bg-primary/5 px-3 py-2"
                    >
                      <div class="flex items-center gap-1.5">
                        <span class="text-xs font-semibold text-fg">{{ f.name }}</span>
                        <span class="badge tone-primary !h-[15px] !px-1 text-[9px]">进模型</span>
                      </div>
                      <p v-if="f.description" class="mt-1 text-[11px] leading-relaxed text-muted">{{ f.description }}</p>
                      <code class="mt-1.5 block whitespace-pre-wrap break-all rounded-md bg-inset px-2.5 py-1.5 font-mono text-[10.5px] leading-relaxed text-fg">{{ f.expression }}</code>
                    </div>
                    <div
                      v-for="f in genOnly(r)"
                      :key="'g' + f.code"
                      class="rounded-lg border border-border bg-surface-2/40 px-3 py-2"
                    >
                      <span class="text-xs font-semibold text-fg">{{ f.name }}</span>
                      <p v-if="f.description" class="mt-1 text-[11px] leading-relaxed text-muted">{{ f.description }}</p>
                      <code class="mt-1.5 block whitespace-pre-wrap break-all rounded-md bg-inset px-2.5 py-1.5 font-mono text-[10.5px] leading-relaxed text-fg">{{ f.expression }}</code>
                    </div>
                  </div>
                </details>

                <!-- 诊断结论 -->
                <div v-if="r.diagnosis" class="border-t border-border px-4 py-3">
                  <p class="text-xs font-semibold text-fg">
                    <FlaskConical :size="12" class="mr-1 inline text-ai" />AI 诊断
                    <span v-if="r.diagnosis.fallback" class="badge tone-neutral ml-1">规则兜底</span>
                  </p>
                  <p class="mt-1.5 text-xs leading-relaxed text-muted">{{ r.diagnosis.review }}</p>
                  <p v-if="r.diagnosis.advice" class="mt-1 text-xs leading-relaxed text-subtle">建议：{{ r.diagnosis.advice }}</p>
                  <p v-if="paramsText(r.diagnosis)" class="mt-1 text-xs leading-relaxed text-subtle">
                    调参（{{ r.diagnosis.param_attempts }}/{{ r.diagnosis.param_retry_limit }}）：{{ paramsText(r.diagnosis) }}
                  </p>
                  <p v-if="r.diagnosis.backtest_review" class="mt-1.5 text-xs leading-relaxed text-muted">复盘：{{ r.diagnosis.backtest_review }}</p>
                  <div v-if="r.diagnosis.drop?.length" class="mt-2 flex flex-wrap gap-1.5">
                    <span class="text-[11px] text-subtle">剔除：</span>
                    <span v-for="d in r.diagnosis.drop" :key="d.code" class="chip !h-6 text-[11px]">
                      {{ d.name }}
                      <span class="text-warn">×</span>
                    </span>
                  </div>
                  <router-link v-if="r.model_id" to="/models" class="btn btn-soft mt-2.5 !h-7 !px-2.5 text-[11px]">
                    <TrendingUp :size="12" />查看入库模型
                  </router-link>
                </div>
                </div>
              </div>
            </div>
          </template>
        </div>

        <!-- 历史 -->
        <div v-else>
          <StateView v-if="!history.length" icon="clock" title="暂无历史流水线" desc="启动一次流水线后，这里会保留每次运行记录。" />
          <ul v-else class="space-y-2">
            <li v-for="r in history" :key="r.id" class="panel group flex items-center gap-3 px-4 py-3 hover:border-strong">
              <button class="flex min-w-0 flex-1 items-center gap-3 text-left" @click="viewRun(r)">
                <span class="min-w-0 flex-1">
                  <span class="flex items-center gap-2">
                    <span class="truncate text-sm font-semibold text-fg">{{ r.name }}</span>
                    <span class="badge" :class="statusMeta(r.status).tone">{{ statusMeta(r.status).label }}</span>
                  </span>
                  <span class="mt-0.5 block text-xs text-subtle">
                    轮次 {{ r.current_round }}/{{ r.config.max_rounds }}
                    <template v-if="r.best_metrics"> · 最佳夏普 {{ num(r.best_metrics.sharpe, 2) }}</template>
                    · {{ fmtStamp(r.created_at) }}
                  </span>
                </span>
                <ChevronRight :size="16" class="text-subtle" />
              </button>
              <button
                class="rounded-md p-1.5 text-subtle transition-colors hover:bg-danger/10 hover:text-danger"
                :title="`删除 ${r.name}`"
                :aria-label="`删除 ${r.name}`"
                @click.stop.prevent="openDelete(r)"
              >
                <Trash2 :size="14" />
              </button>
            </li>
          </ul>
        </div>
      </section>
    </div>

    <!-- 删除流水线确认弹窗 -->
    <Modal
      :open="delOpen"
      :title="`删除流水线 · ${delRun?.name ?? ''}`"
      sub="该运行的轮次记录（因子/训练/回测/诊断）会一并删除"
      width="520px"
      @close="delOpen = false"
    >
      <div class="space-y-3">
        <p class="text-[13px] leading-relaxed text-muted">
          确定要删除流水线“{{ delRun?.name }}”吗？其全部轮次记录将一并移除，且不可恢复。
        </p>
        <p v-if="delRun && (delRun.status === 'running' || delRun.status === 'pending')" class="rounded-lg border border-warn/30 bg-warn/10 px-3 py-2 text-xs text-warn">
          该流水线仍在运行，删除会先停止它。
        </p>
        <p v-if="delErr" class="rounded-lg border border-down/30 bg-down/10 px-3 py-2 text-xs text-down">{{ delErr }}</p>
      </div>
      <template #footer>
        <div class="flex items-center justify-end gap-2">
          <button class="btn btn-ghost" :disabled="delBusy" @click="delOpen = false">取消</button>
          <button class="btn btn-danger-ghost" :disabled="delBusy || !!delErr" @click="confirmDelete">
            <LoaderCircle v-if="delBusy" :size="14" class="animate-spin" />{{ delBusy ? '删除中…' : '删除流水线' }}
          </button>
        </div>
      </template>
    </Modal>

    <!-- 训练详情弹窗：训练参数 + 数据量 + 样本外诊断图表 -->
    <Modal
      :open="!!detailRound"
      :title="`训练详情 · 第 ${detailRound?.round_no ?? ''} 轮`"
      sub="样本外（测试集）诊断图表；验证集指标仅用于调参，此处作对照"
      width="1080px"
      @close="closeDetail"
    >
      <div v-if="detailRound" class="space-y-3">
        <div class="grid grid-cols-2 gap-2 sm:grid-cols-4">
          <div class="rounded-lg bg-surface-2 px-2.5 py-2">
            <p class="text-[10px] uppercase tracking-wider text-subtle">训练 IC</p>
            <p class="num text-sm font-semibold text-fg">{{ signed(detailRound.train_result?.train_ic) }}</p>
          </div>
          <div class="rounded-lg bg-surface-2 px-2.5 py-2">
            <p class="text-[10px] uppercase tracking-wider text-subtle">测试 IC</p>
            <p class="num text-sm font-semibold" :class="tone(detailRound.train_result?.oos_ic)">{{ signed(detailRound.train_result?.oos_ic) }}</p>
          </div>
          <div class="rounded-lg bg-surface-2 px-2.5 py-2">
            <p class="text-[10px] uppercase tracking-wider text-subtle">测试 RankIC</p>
            <p class="num text-sm font-semibold" :class="tone(detailRound.train_result?.oos_rank_ic)">{{ signed(detailRound.train_result?.oos_rank_ic) }}</p>
          </div>
          <div class="rounded-lg bg-surface-2 px-2.5 py-2">
            <p class="text-[10px] uppercase tracking-wider text-subtle">验证 RankIC</p>
            <p class="num text-sm font-semibold" :class="tone(detailRound.train_result?.val_rank_ic)">{{ signed(detailRound.train_result?.val_rank_ic) }}</p>
          </div>
          <div class="rounded-lg bg-surface-2 px-2.5 py-2">
            <p class="text-[10px] uppercase tracking-wider text-subtle">过拟合差</p>
            <p class="num text-sm font-semibold text-fg">{{ num(detailRound.train_result?.overfit_gap) }}</p>
          </div>
          <div class="rounded-lg bg-surface-2 px-2.5 py-2">
            <p class="text-[10px] uppercase tracking-wider text-subtle">命中率</p>
            <p class="num text-sm font-semibold text-fg">{{ pct(detailRound.train_result?.hit_rate) }}</p>
          </div>
          <div class="rounded-lg bg-surface-2 px-2.5 py-2">
            <p class="text-[10px] uppercase tracking-wider text-subtle">Top−Btm</p>
            <p class="num text-sm font-semibold" :class="tone(detailRound.train_result?.spread)">{{ signed(detailRound.train_result?.spread, 5) }}</p>
          </div>
          <div class="rounded-lg bg-surface-2 px-2.5 py-2">
            <p class="text-[10px] uppercase tracking-wider text-subtle">特征数</p>
            <p class="num text-sm font-semibold text-fg">{{ detailRound.train_result?.n_features_kept ?? '—' }}</p>
          </div>
        </div>

        <div class="rounded-lg border border-border bg-surface-2/40 px-3 py-2 text-[11px] leading-relaxed">
          <p class="text-muted">
            训练参数：<span class="font-mono text-fg">{{ trainParams(detailRound).map((p) => `${p.label}=${p.v}`).join(' · ') || '—' }}</span>
          </p>
          <p class="mt-1 text-muted">数据量：<span class="num text-fg">{{ dataVol(detailRound) || '—' }}</span></p>
        </div>

        <TrainCharts v-if="detailCharts" :result="detailCharts" />
        <p v-else class="rounded-lg border border-border bg-surface-2/40 px-3 py-4 text-center text-xs text-subtle">
          本轮没有可用的诊断图表（可能训练未产出模型或样本不足）。
        </p>
      </div>
    </Modal>

    <!-- 样本数据弹窗：训练集 / 测试集前 100 条 -->
    <Modal
      :open="!!sampleRound"
      :title="`样本数据 · 第 ${sampleRound?.round_no ?? ''} 轮`"
      sub="因子原始值（未标准化）与未来收益标签；各取前 100 条，表格可横向滚动"
      width="1080px"
      @close="closeSamples"
    >
      <div v-if="sampleRound" class="space-y-3">
        <div class="flex flex-wrap items-center gap-2">
          <button
            class="btn !h-7 !px-3 text-[11px]"
            :class="sampleTab === 'train' ? 'btn-primary' : 'btn-soft'"
            @click="sampleTab = 'train'"
          >
            训练集（{{ sampleRound.train_result?.sample_train?.length ?? 0 }}）
          </button>
          <button
            class="btn !h-7 !px-3 text-[11px]"
            :class="sampleTab === 'test' ? 'btn-primary' : 'btn-soft'"
            @click="sampleTab = 'test'"
          >
            测试集（{{ sampleRound.train_result?.sample_test?.length ?? 0 }}）
          </button>
          <span class="ml-auto text-[11px] text-subtle">
            共 {{ sampleRows.length }} 条 · {{ sampleColumns.length }} 列
          </span>
        </div>
        <div v-if="!sampleRows.length" class="rounded-lg border border-border bg-surface-2/40 px-3 py-6 text-center text-xs text-subtle">
          本轮没有可展示的样本（可能是历史运行，或训练样本不足）。
        </div>
        <div v-else class="max-h-[60vh] overflow-auto rounded-lg border border-border">
          <table class="w-full border-collapse text-[11px]">
            <thead class="sticky top-0 z-10 bg-surface-2">
              <tr>
                <th class="whitespace-nowrap border-b border-border px-2 py-1.5 text-left font-semibold text-subtle">时间</th>
                <th
                  v-for="c in sampleColumns"
                  :key="c"
                  class="whitespace-nowrap border-b border-border px-2 py-1.5 text-right font-semibold text-subtle"
                >
                  {{ factorLabel(c) }}
                </th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(row, i) in sampleRows" :key="i" class="odd:bg-surface-2/30">
                <td class="whitespace-nowrap px-2 py-1 font-mono text-muted">{{ fmtStamp(String(row.ts)) }}</td>
                <td
                  v-for="c in sampleColumns"
                  :key="c"
                  class="num whitespace-nowrap px-2 py-1 text-right text-fg"
                  :class="c === 'label' ? 'font-semibold' : ''"
                >
                  {{ fmtSampleCell(row[c]) }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </Modal>
  </div>
</template>
