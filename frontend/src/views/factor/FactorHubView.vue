<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ArrowUpRight, Boxes, Check, FlaskConical, Info, LoaderCircle, RefreshCw, Search, Sparkles } from '@lucide/vue'
import { createStrategy, createVersion, runBacktest } from '@/api/strategy'
import PageHeader from '@/components/ui/PageHeader.vue'
import HelpDrawer, { type PageHelp } from '@/components/ui/HelpDrawer.vue'
import Panel from '@/components/ui/Panel.vue'
import Modal from '@/components/ui/Modal.vue'
import StateView from '@/components/ui/StateView.vue'
import { useToastStore } from '@/stores/toast'
import {
  listFactors,
  fetchLatestRuns,
  fetchRunsByCode,
  factorMatrix,
  researchFactors,
  verifyExpression,
  joinFactor,
  removeUserFactor,
  trainMl,
  saveMl,
  listMlModels,
  scoreMlModel,
  type FactorMeta,
  type FactorResearch,
  type FactorRun,
  type FactorMatrix,
  type MlTrainResult,
  type MlTrainArgs,
  type MlModelRow,
  type MlScore,
} from '@/api/factor'
import EquityChart from '@/components/chart/EquityChart.vue'
import { INTERVALS, MAJOR_SYMBOLS, timeAgo, fmtTime } from '@/utils/format'

const pageHelp: PageHelp = {
  title: '因子库与研究',
  intro: '因子 = 用行情计算的“特征函数”，衡量它对未来收益的预测力。这里是因子的“体检科”而不是策略区。',
  terms: [
    { term: '因子（Factor）', desc: '一个可复用的函数：输入 K 线，输出一串与时间对齐的数值（如动量、RSI、量比）。' },
    { term: 'IC / RankIC', desc: 'IC = 因子值与“下一根收益”的相关系数；RankIC 换成“排序”后计算更稳健。>0 表示因子越大收益越高，|IC| 通常>0.03 才算有信号。' },
    { term: '衰减 decay h1/h3/h5', desc: '因子对未来 1/3/5 根收益的 IC 是否衰减。预测力衰减快 → 需更频繁换仓。' },
    { term: '样本与窗口', desc: '至少 30 个对齐样本才有统计意义；用更长历史更可靠。IC 是统计相关，不等于利润。' },
    { term: '相关矩阵', desc: '两因子序列的相关系数。|r|>0.7 视为冗余，组合时重复的信息不会带来额外 alpha。' },
    { term: '无未来函数', desc: '因子在 t 时刻只用 ≤t 的数据，收益取 t→t+1，避免“用未来算过去”的假信号。' },
    { term: '研究记录', desc: '每次研究自动落库（factor_ic_runs），可在「历史」里看 IC 随时间的演进，判断信号是否退化。' },
  ],
  tips: ['流程建议：单因子 IC 筛选 → 相关矩阵去冗余 → 组合 → 做成策略回测。', '库里只有 15m 时可选 30m/1h/1d，后端会自动按时间桶聚合。', '“研究全部”= 对 6 个内置因子在 BTC 1d 上批量体检，方便横向对比。'],
}

const toast = useToastStore()
const loading = ref(true)
const error = ref<string | null>(null)
const factors = ref<FactorMeta[]>([])
const builtins = computed(() => factors.value.filter((f) => !f.is_user))
const results = ref<Record<string, FactorResearch>>({})

const q = ref('')
const tag = ref<string>('全部')
const tags = computed(() => Array.from(new Set(factors.value.flatMap((f) => f.tags))))

const filtered = computed(() => {
  const kw = q.value.trim().toLowerCase()
  return factors.value.filter((f) => {
    const okTag = tag.value === '全部' || f.tags.includes(tag.value)
    const okKw = !kw || f.name.toLowerCase().includes(kw) || f.code.toLowerCase().includes(kw) || f.description.toLowerCase().includes(kw)
    return okTag && okKw
  })
})

const counts = computed(() => ({
  total: factors.value.length,
  researched: Object.keys(results.value).length,
  best: Object.values(results.value).reduce<number | null>((m, r) => (r.rank_ic != null ? Math.max(m ?? -Infinity, r.rank_ic) : m), null),
}))

async function load() {
  loading.value = true
  error.value = null
  try {
    factors.value = await listFactors()
    await prefillRuns()
  } catch (e: unknown) {
    error.value = (e as { message?: string }).message ?? '因子库加载失败'
    toast.error('因子库加载失败', error.value)
  } finally {
    loading.value = false
  }
}

function toResearch(r: FactorRun, meta: FactorMeta | undefined): FactorResearch {
  return {
    code: r.code,
    name: meta?.name ?? r.code,
    tags: meta?.tags ?? [],
    interval: r.interval,
    found: true,
    ic: r.ic,
    rank_ic: r.rank_ic,
    decay: { h_1: r.ic_h1, h_3: r.ic_h3, h_5: r.ic_h5 },
    n_total: r.n,
    per_symbol: [],
  }
}

async function prefillRuns() {
  try {
    const runs = await fetchLatestRuns()
    if (!runs.length) return
    const metaByCode = new Map(factors.value.map((f) => [f.code, f]))
    const out: Record<string, FactorResearch> = {}
    for (const r of runs) out[r.code] = toResearch(r, metaByCode.get(r.code))
    results.value = out
  } catch {
    /* 历史读取失败不阻塞列表 */
  }
}

const bulkRunning = ref(false)
const bulkText = ref('')

async function researchAll() {
  if (bulkRunning.value || !factors.value.length) return
  bulkRunning.value = true
  bulkText.value = '正在研究全部因子…'
  const end = new Date()
  const start = new Date(end.getTime() - 365 * 24 * 3600 * 1000)
  try {
    const out: Record<string, FactorResearch> = { ...results.value }
    const codes = factors.value.map((f) => f.code)
    for (let i = 0; i < codes.length; i += 4) {
      const part = codes.slice(i, i + 4)
      bulkText.value = `研究 ${Math.min(i + 4, codes.length)}/${codes.length} 个…`
      const res = await researchFactors({
        codes: part,
        symbols: ['BTCUSDT'],
        interval: '1d',
        start: start.toISOString(),
        end: end.toISOString(),
      })
      for (const r of res) out[r.code] = r
    }
    results.value = out
    const found = Object.keys(out).length
    toast.success('批量研究完成', `已对 BTCUSDT 1d 实测 ${found} 个因子的 IC/RankIC`)
  } catch (e: unknown) {
    toast.error('批量研究失败', (e as { message?: string }).message)
  } finally {
    bulkRunning.value = false
    bulkText.value = ''
  }
}

// —— 单因子研究 ——
const showResearch = ref(false)
const researching = ref(false)
const active = ref<FactorMeta | null>(null)
const form = ref({ symbol: 'BTCUSDT', interval: '1d', days: 365 })

function openResearch(f: FactorMeta) {
  active.value = f
  form.value = { symbol: 'BTCUSDT', interval: '1d', days: 365 }
  showResearch.value = true
}

async function runResearch() {
  if (!active.value || researching.value) return
  researching.value = true
  const end = new Date()
  const start = new Date(end.getTime() - form.value.days * 24 * 3600 * 1000)
  const symbol = form.value.symbol.trim().toUpperCase()
  try {
    let r: FactorResearch
    if (active.value.is_user && active.value.expression) {
      // 用户/AI 因子走表达式验证（单标的）
      const v = await verifyExpression({
        expression: active.value.expression,
        symbol,
        interval: '1d',
        start: start.toISOString(),
        end: end.toISOString(),
      })
      if (!v.ok) {
        toast.error('校验未通过', v.errors?.[0] ?? v.message)
        return
      }
      r = {
        code: active.value.code,
        name: active.value.name,
        tags: [],
        interval: '1d',
        found: true,
        ic: v.ic,
        rank_ic: v.rank_ic,
        decay: {},
        n_total: v.n_total,
        per_symbol: [],
      }
    } else {
      const res = await researchFactors({
        codes: [active.value.code],
        symbols: [symbol],
        interval: form.value.interval,
        start: start.toISOString(),
        end: end.toISOString(),
      })
      r = res[0]
    }
    results.value = { ...results.value, [r.code]: r }
    if (r.found) {
      toast.success('研究完成', `${r.name} 实测 IC=${r.ic ?? '—'} · RankIC=${r.rank_ic ?? '—'}（n=${r.n_total}）`)
      showResearch.value = false
    } else {
      toast.error('暂无足够数据', r.message ?? '请先在「K 线下载」补齐该窗口的数据')
    }
  } catch (e: unknown) {
    toast.error('研究失败', (e as { message?: string }).message)
  } finally {
    researching.value = false
  }
}

function resultOf(code: string): FactorResearch | undefined {
  return results.value[code]
}

function decay1(r: FactorResearch | undefined): string {
  const v = r?.decay?.h_1
  return v != null ? v.toFixed(4) : '—'
}

async function removeUser(f: FactorMeta) {
  if (!window.confirm(`从因子库删除「${f.name}」？`)) return
  try {
    await removeUserFactor(f.code)
    toast.success('已删除', f.name)
    await load()
  } catch (e: unknown) {
    toast.error('删除失败', (e as { message?: string }).message)
  }
}

function grade(code: string): { tone: string; label: string } | null {
  const r = results.value[code]
  if (!r || !r.found) return null
  const m = Math.abs(r.rank_ic ?? r.ic ?? 0)
  const decayOk =
    r.decay &&
    (r.decay.h_1 != null || r.decay.h_3 != null) &&
    [r.decay.h_1, r.decay.h_3].filter((x): x is number => x != null).every((x) => Math.sign(x) === Math.sign(r.ic ?? x))
  if (m >= 0.03 && decayOk) return { tone: 'tone-up', label: '值得深挖' }
  if (m >= 0.015 || (decayOk && m >= 0.01)) return { tone: 'tone-primary', label: '可关注' }
  return { tone: 'tone-neutral', label: '信号弱' }
}

// —— 历史记录弹窗 ——
const histOpen = ref(false)
const histLoading = ref(false)
const histName = ref('')
const histRuns = ref<FactorRun[]>([])

async function openHistory(f: FactorMeta) {
  histName.value = f.name
  histOpen.value = true
  histLoading.value = true
  histRuns.value = []
  try {
    histRuns.value = await fetchRunsByCode(f.code)
  } catch {
    toast.error('历史记录加载失败')
  } finally {
    histLoading.value = false
  }
}

const histIcTrend = computed(() => {
  const rows = [...histRuns.value].reverse()
  return rows
    .filter((r) => r.ic != null)
    .map((r) => ({ ts: fmtTime(r.created_at, true), value: r.ic as number }))
})

function histCell(v: number | null): string {
  return v == null ? '—' : v.toFixed(4)
}

// —— 多因子相关矩阵 ——
const mxOpen = ref(false)
const mxCodes = ref<Set<string>>(new Set())
const mxLoading = ref(false)
const mxResult = ref<FactorMatrix | null>(null)
const mxCfg = ref({ symbol: 'BTCUSDT', interval: '1d', days: 365 })

function openMatrix() {
  mxCodes.value = new Set(factors.value.slice(0, 3).map((f) => f.code))
  mxResult.value = null
  mxOpen.value = true
}

function toggleMx(code: string) {
  const s = new Set(mxCodes.value)
  if (s.has(code)) s.delete(code)
  else if (s.size < 8) s.add(code)
  mxCodes.value = s
}

async function runMatrix() {
  if (mxLoading.value || mxCodes.value.size < 2) return
  mxLoading.value = true
  mxResult.value = null
  const end = new Date()
  const start = new Date(end.getTime() - mxCfg.value.days * 24 * 3600 * 1000)
  try {
    mxResult.value = await factorMatrix({
      codes: [...mxCodes.value],
      symbols: [mxCfg.value.symbol.trim().toUpperCase()],
      interval: mxCfg.value.interval,
      start: start.toISOString(),
      end: end.toISOString(),
    })
    if (!mxResult.value.found) toast.error('矩阵计算失败', mxResult.value.message)
  } catch (e: unknown) {
    toast.error('矩阵计算失败', (e as { message?: string }).message)
  } finally {
    mxLoading.value = false
  }
}

function heatStyle(v: number): Record<string, string> {
  const alpha = Math.round(Math.min(Math.abs(v), 1) * 55)
  const color = v >= 0 ? 'var(--color-up)' : 'var(--color-down)'
  return { backgroundColor: `color-mix(in srgb, ${color} ${alpha}%, transparent)` }
}

// —— ML 多因子训练 ——
const mlOpen = ref(false)
const mlCodes = ref<Set<string>>(new Set())
const mlLoading = ref(false)
const mlResult = ref<MlTrainResult | null>(null)
const mlCfg = ref({ symbol: 'BTCUSDT', interval: '1d', days: 365, horizon: 1, alpha: 1, model: 'ridge' })
const mlLast = ref<MlTrainArgs | null>(null)
const mlName = ref('')
const mlSaving = ref(false)

function openMl() {
  mlCodes.value = new Set(factors.value.slice(0, 3).map((f) => f.code))
  mlResult.value = null
  mlOpen.value = true
}

function toggleMl(code: string) {
  const s = new Set(mlCodes.value)
  if (s.has(code)) s.delete(code)
  else if (s.size < 8) s.add(code)
  mlCodes.value = s
}

async function runMl() {
  if (mlLoading.value || mlCodes.value.size < 2) return
  mlLoading.value = true
  mlResult.value = null
  mlLast.value = null
  const end = new Date()
  const start = new Date(end.getTime() - mlCfg.value.days * 24 * 3600 * 1000)
  const args: MlTrainArgs = {
    codes: [...mlCodes.value],
    symbols: [mlCfg.value.symbol.trim().toUpperCase()],
    interval: mlCfg.value.interval,
    start: start.toISOString(),
    end: end.toISOString(),
    horizon: mlCfg.value.horizon,
    test_ratio: 0.25,
    alpha: mlCfg.value.alpha,
    model: mlCfg.value.model,
  }
  try {
    mlResult.value = await trainMl(args)
    mlLast.value = mlResult.value.found ? args : null
    if (!mlResult.value.found) toast.error('训练失败', mlResult.value.message)
  } catch (e: unknown) {
    toast.error('训练失败', (e as { message?: string }).message)
  } finally {
    mlLoading.value = false
  }
}

async function saveResult() {
  if (!mlLast.value || !mlName.value.trim() || mlSaving.value) return
  mlSaving.value = true
  try {
    const saved = await saveMl({ ...mlLast.value, name: mlName.value.trim() })
    mlName.value = ''
    toast.success('模型已保存', `${saved.name}（${saved.interval} · ${saved.features} 个特征）`)
  } catch (e: unknown) {
    toast.error('保存失败', (e as { message?: string }).message)
  } finally {
    mlSaving.value = false
  }
}

// —— 模型库 ——
const modelsOpen = ref(false)
const modelsList = ref<MlModelRow[]>([])
const modelsLoading = ref(false)

async function openModels() {
  modelsOpen.value = true
  modelsLoading.value = true
  modelsList.value = []
  try {
    modelsList.value = await listMlModels()
  } catch {
    toast.error('模型库加载失败')
  } finally {
    modelsLoading.value = false
  }
}

async function scoreModel(m: MlModelRow) {
  const days = m.interval.endsWith('d') ? 30 : m.interval.endsWith('h') ? 14 : 3
  const end = new Date()
  const start = new Date(end.getTime() - days * 24 * 3600 * 1000)
  try {
    const sym = m.symbols?.[0] ?? 'BTCUSDT'
    const res: MlScore = await scoreMlModel(m.id, { symbol: sym, start: start.toISOString(), end: end.toISOString() })
    toast.info('打分完成', `${sym} 最近 ${res.n} bar，最新得分 ${res.last_score ?? '—'}`)
  } catch (e: unknown) {
    toast.error('打分失败', (e as { message?: string }).message)
  }
}

// —— 用模型生成“策略版本 + 回测” ——
const router = useRouter()
const btOpen = ref(false)
const btModel = ref<MlModelRow | null>(null)
const btBusy = ref(false)
const btCfg = ref({ symbol: 'BTCUSDT', days: 30, window: 60, enterQ: 0.8, exitQ: 0.2 })

function openBt(m: MlModelRow) {
  btModel.value = m
  btCfg.value = {
    symbol: m.symbols?.[0] ?? 'BTCUSDT',
    days: m.interval.endsWith('d') ? 300 : m.interval.endsWith('h') ? 30 : 7,
    window: 60,
    enterQ: 0.8,
    exitQ: 0.2,
  }
  btOpen.value = true
}

async function runBt() {
  const m = btModel.value
  if (!m || btBusy.value) return
  btBusy.value = true
  const end = new Date()
  const start = new Date(end.getTime() - btCfg.value.days * 24 * 3600 * 1000)
  const features = (m.features as Array<{ code: string; coef: number; mean: number; std: number }>).map((f) => ({
    code: f.code,
    coef: f.coef,
    mean: f.mean,
    std: f.std,
  }))
  const params: Record<string, unknown> = {
    template_id: 'ml_score',
    features,
    window: btCfg.value.window,
    enter_q: btCfg.value.enterQ,
    exit_q: btCfg.value.exitQ,
  }
  try {
    const str = await createStrategy({
      name: `${m.name}-bt`,
      description: `由 ML 模型「${m.name}」打分择时驱动的策略（${m.interval}）。`,
      tags: ['ML', '量化'],
    })
    const ver = await createVersion(str.strategy_id, { template_id: 'ml_score', params })
    await runBacktest({
      strategy_id: str.strategy_id,
      version_id: ver.version_id,
      symbol: btCfg.value.symbol.trim().toUpperCase(),
      interval: m.interval,
      start: start.toISOString(),
      end: end.toISOString(),
      params,
    })
    toast.success('策略已创建并开始回测', `进入策略详情查看指标卡/资金曲线`)
    btOpen.value = false
    router.push(`/strategy/${str.strategy_id}`)
  } catch (e: unknown) {
    toast.error('创建策略失败', (e as { message?: string }).message)
  } finally {
    btBusy.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="flex flex-col gap-6">
    <PageHeader
      eyebrow="Research · Factor"
      title="因子库"
      desc="因子即代码：每个因子都是可校验的 Python 函数。选一个因子 → 选标的与窗口 → 跑真实 IC 研究（本地 K 线，无未来函数）。"
    >
      <template #actions>
        <HelpDrawer :help="pageHelp" />
        <button class="btn btn-ghost" @click="openModels">模型库</button>
        <button class="btn btn-ghost" @click="openMl">ML 训练</button>
        <button class="btn btn-ghost" @click="openMatrix">
          多因子矩阵
        </button>
        <button class="btn btn-ghost" :disabled="bulkRunning" @click="researchAll">
          <LoaderCircle v-if="bulkRunning" :size="14" class="animate-spin" />
          <FlaskConical v-else :size="14" />
          {{ bulkRunning ? bulkText : '研究全部（BTC 1d）' }}
        </button>
        <router-link to="/factor/ai-lab" class="btn btn-primary">
          <Sparkles :size="15" />进入 AI 因子工坊
        </router-link>
      </template>
    </PageHeader>

    <div class="flex items-start gap-2.5 rounded-xl px-4 py-3" style="background: color-mix(in srgb, var(--color-ai) 7%, transparent); border: 1px solid color-mix(in srgb, var(--color-ai) 28%, transparent)">
      <Info :size="15" class="mt-0.5 shrink-0 text-ai-ink" />
      <p class="text-xs leading-relaxed text-muted">
        IC/RankIC 为因子相对「下一根 K 线收益」的相关性，跨标的等权平均；衰减 h_1/h_3/h_5 看预测力持久性。数据来自本地库，不足时请先下载。
      </p>
    </div>

    <div class="grid grid-cols-3 gap-3 lg:max-w-xl">
      <div class="panel px-4 py-3">
        <p class="text-[11px] uppercase tracking-wider text-subtle">内置因子</p>
        <p class="num mt-1 text-xl font-semibold text-fg">{{ counts.total }}</p>
      </div>
      <div class="panel px-4 py-3">
        <p class="text-[11px] uppercase tracking-wider text-subtle">已研究</p>
        <p class="num mt-1 text-xl font-semibold text-fg">{{ counts.researched }}</p>
      </div>
      <div class="panel px-4 py-3">
        <p class="text-[11px] uppercase tracking-wider text-subtle">最优 RankIC</p>
        <p class="num mt-1 text-xl font-semibold" :class="counts.best !== null && counts.best > 0 ? 'text-up' : 'text-fg'">
          {{ counts.best !== null ? counts.best.toFixed(3) : '—' }}
        </p>
      </div>
    </div>

    <Panel v-if="error" :pad="'none'">
      <StateView icon="error" :title="error" desc="后端因子接口未就绪，请确认已重启网关。">
        <template #action><button class="btn btn-primary" @click="load"><RefreshCw :size="15" />重试</button></template>
      </StateView>
    </Panel>

    <template v-else>
      <div class="flex flex-wrap items-center gap-2">
        <div class="relative w-full sm:w-72">
          <Search :size="14" class="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-subtle" />
          <input v-model="q" type="search" placeholder="搜索因子名称 / 代码 / 思路…" class="input pl-9" />
        </div>
        <button v-for="t in ['全部', ...tags]" :key="t" class="chip" :class="{ 'is-active': tag === t }" @click="tag = t">
          {{ t }}
        </button>
        <span class="ml-auto text-xs text-subtle">共 {{ filtered.length }} 个</span>
      </div>

      <div v-if="loading" class="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
        <div v-for="i in 6" :key="i" class="panel h-44 animate-pulse"><div class="space-y-3 p-5"><div class="h-3.5 w-2/5 rounded bg-surface-2" /><div class="h-2.5 w-4/5 rounded bg-surface-2" /><div class="h-2.5 w-3/5 rounded bg-surface-2" /></div></div>
      </div>

      <div v-else-if="filtered.length" class="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
        <article v-for="f in filtered" :key="f.code" class="panel flex flex-col p-5">
          <div class="flex items-center gap-2">
            <span class="flex h-9 w-9 items-center justify-center rounded-lg tint-primary text-primary-ink dark:text-primary">
              <Boxes :size="16" />
            </span>
            <div class="min-w-0 flex-1">
              <h3 class="truncate text-[15px] font-semibold text-fg">{{ f.name }}</h3>
              <p class="truncate font-mono text-[11px] text-subtle" :title="f.is_user ? f.expression : f.code">{{ f.is_user ? (f.expression ?? f.code) : f.code }}</p>
            </div>
            <span class="badge" :class="f.is_user ? 'tone-ai' : 'tone-neutral'">{{ f.is_user ? 'AI' : '内置' }}</span>
          </div>

          <p class="mt-3 line-clamp-2 flex-1 text-[13px] leading-relaxed text-muted">{{ f.description }}</p>

          <div class="mt-3 flex flex-wrap gap-1.5">
            <span v-for="t in f.tags" :key="t" class="badge tone-neutral">{{ t }}</span>
            <span v-if="f.min_bars" class="badge tone-neutral">需 ≥{{ f.min_bars }} bar</span>
          </div>

          <div class="mt-4 border-t border-border pt-3">
            <template v-if="resultOf(f.code)">
              <div v-if="grade(f.code)" class="mb-2 flex items-center justify-between">
                <span class="badge" :class="grade(f.code)?.tone">{{ grade(f.code)?.label }}</span>
                <span class="text-[10px] text-subtle">|RankIC| ≥ 0.03 且衰减同号时判为强信号</span>
              </div>
              <div class="flex items-center justify-between text-xs">
                <span class="text-subtle">实测 IC</span>
                <span class="num font-semibold" :class="(resultOf(f.code)?.ic ?? 0) > 0 ? 'text-up' : (resultOf(f.code)?.ic ?? 0) < 0 ? 'text-down' : 'text-fg'">
                  {{ resultOf(f.code)?.ic ?? '—' }}
                </span>
              </div>
              <div class="mt-1 flex items-center justify-between text-xs">
                <span class="text-subtle">RankIC</span>
                <span class="num font-semibold text-fg">{{ resultOf(f.code)?.rank_ic ?? '—' }}</span>
              </div>
              <div class="mt-1 flex items-center justify-between text-[11px] text-subtle">
                <span>样本 {{ resultOf(f.code)?.n_total ?? 0 }}</span>
                <span class="font-mono">衰减 h1 = {{ decay1(resultOf(f.code)) }}</span>
              </div>
            </template>
            <p v-else class="text-xs text-subtle">尚未研究 —— 点「研究 IC」在本地数据上实测。</p>
          </div>

          <div class="mt-3 flex items-center gap-2">
            <button class="btn btn-primary !h-8 flex-1 !px-2 text-xs" @click="openResearch(f)">
              <FlaskConical :size="13" />研究 IC
            </button>
            <button v-if="!f.is_user" class="btn btn-ghost !h-8 !px-2.5 text-xs" @click="openHistory(f)">历史</button>
            <router-link v-if="!f.is_user" to="/factor/ai-lab" class="btn btn-ghost !h-8 !px-2.5 text-xs">
              AI 工坊<ArrowUpRight :size="12" />
            </router-link>
            <button v-if="f.is_user" class="btn btn-danger-ghost !h-8 flex-1 !px-2.5 text-xs" @click="removeUser(f)">删除</button>
          </div>
        </article>
      </div>

      <Panel v-else :pad="'none'">
        <StateView icon="search" title="没有匹配的因子" desc="换个关键词或清除标签过滤。" />
      </Panel>
    </template>

    <!-- 研究弹窗 -->
    <Modal :open="showResearch" :title="`研究 · ${active?.name ?? ''}`" :sub="`${active?.code} — 对下一根 K 线收益做 IC 度量`" width="520px" @close="showResearch = false">
      <div class="space-y-4">
        <div class="grid grid-cols-2 gap-4">
          <div class="field">
            <label class="label" for="fr-symbol">交易对</label>
            <input id="fr-symbol" v-model="form.symbol" list="fr-symbols" class="input font-mono uppercase" spellcheck="false" />
            <datalist id="fr-symbols">
              <option v-for="s in MAJOR_SYMBOLS" :key="s" :value="s" />
            </datalist>
          </div>
          <div class="field">
            <label class="label" for="fr-interval">K 线颗粒度</label>
            <select id="fr-interval" v-model="form.interval" class="select font-mono">
              <option v-for="i in INTERVALS.filter((x) => ['15m', '30m', '1h', '1d'].includes(x))" :key="i" :value="i">{{ i }}</option>
            </select>
          </div>
        </div>
        <div class="field">
          <label class="label">回看窗口</label>
          <div class="flex gap-1.5">
            <button v-for="d in [90, 180, 365, 730]" :key="d" class="chip !h-8" :class="{ 'is-active': form.days === d }" @click="form.days = d">
              {{ d === 365 ? '1 年' : d === 730 ? '2 年' : `${d} 天` }}
            </button>
          </div>
        </div>
        <p class="text-xs leading-relaxed text-subtle">
          使用本地库中 {{ form.interval }} K 线；若库里只有更细粒度（如 15m），后端会自动按桶聚合到目标粒度。数据不足会提示先去下载。
        </p>
      </div>
      <template #footer>
        <div class="flex items-center justify-end gap-2">
          <button class="btn btn-ghost" @click="showResearch = false">取消</button>
          <button class="btn btn-primary" :disabled="researching || !form.symbol.trim()" @click="runResearch">
            <LoaderCircle v-if="researching" :size="15" class="animate-spin" />
            {{ researching ? '研究中…' : '开始研究' }}
          </button>
        </div>
      </template>
    </Modal>

    <!-- 历史记录弹窗 -->
    <Modal :open="histOpen" :title="`研究历史 · ${histName}`" sub="历次研究的 IC 演进与衰减（数据已落库）" width="680px" @close="histOpen = false">
      <div v-if="histLoading" class="py-10"><StateView busy title="读取历史…" /></div>
      <template v-else-if="histRuns.length">
        <p v-if="histIcTrend.length >= 2" class="mb-1.5 text-xs font-semibold text-muted">IC 演进</p>
        <div v-if="histIcTrend.length >= 2" class="mb-4"><EquityChart :points="histIcTrend" :height="150" /></div>
        <div class="max-h-72 overflow-auto">
          <table class="tbl">
            <thead>
              <tr>
                <th>时间</th>
                <th class="text-right">粒度</th>
                <th class="text-right">IC</th>
                <th class="text-right">RankIC</th>
                <th class="text-right">h1</th>
                <th class="text-right">h3</th>
                <th class="text-right">h5</th>
                <th class="text-right">样本</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="r in histRuns" :key="r.id">
                <td class="tbl-sub">{{ timeAgo(r.created_at) }}</td>
                <td class="num tbl-num text-muted">{{ r.interval }}</td>
                <td class="num tbl-num" :class="(r.ic ?? 0) > 0 ? 'text-up' : (r.ic ?? 0) < 0 ? 'text-down' : 'text-fg'">{{ histCell(r.ic) }}</td>
                <td class="num tbl-num text-muted">{{ histCell(r.rank_ic) }}</td>
                <td class="num tbl-num text-muted">{{ histCell(r.ic_h1) }}</td>
                <td class="num tbl-num text-muted">{{ histCell(r.ic_h3) }}</td>
                <td class="num tbl-num text-muted">{{ histCell(r.ic_h5) }}</td>
                <td class="num tbl-num text-subtle">{{ r.n }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </template>
      <StateView v-else icon="empty" title="还没有研究记录" desc="在该因子卡片上点「研究 IC」后会在这里留痕。" />
    </Modal>

    <!-- 多因子相关矩阵弹窗 -->
    <Modal :open="mxOpen" title="多因子相关矩阵" sub="选 ≥2 个因子，看冗余程度（|相关| 高则高度重叠）与各自 IC" width="720px" @close="mxOpen = false">
      <div class="space-y-4">
        <div>
          <p class="mb-2 text-xs font-semibold uppercase tracking-wider text-subtle">选择因子（2~8）</p>
          <div class="flex flex-wrap gap-1.5">
            <button
              v-for="f in factors"
              :key="f.code"
              class="chip"
              :class="{ 'is-active': mxCodes.has(f.code) }"
              @click="toggleMx(f.code)"
            >
              {{ f.name }}
            </button>
          </div>
        </div>
        <div class="grid grid-cols-3 gap-3">
          <div class="field">
            <label class="label" for="mx-symbol">交易对</label>
            <input id="mx-symbol" v-model="mxCfg.symbol" class="input font-mono uppercase" spellcheck="false" />
          </div>
          <div class="field">
            <label class="label" for="mx-interval">粒度</label>
            <select id="mx-interval" v-model="mxCfg.interval" class="select font-mono">
              <option v-for="i in INTERVALS.filter((x) => ['15m', '30m', '1h', '1d'].includes(x))" :key="i" :value="i">{{ i }}</option>
            </select>
          </div>
          <div class="field">
            <label class="label" for="mx-days">窗口</label>
            <select id="mx-days" v-model.number="mxCfg.days" class="select">
              <option :value="180">180 天</option>
              <option :value="365">1 年</option>
              <option :value="730">2 年</option>
            </select>
          </div>
        </div>
        <button class="btn btn-primary" :disabled="mxLoading || mxCodes.size < 2" @click="runMatrix">
          <LoaderCircle v-if="mxLoading" :size="14" class="animate-spin" />{{ mxLoading ? '计算中…' : '计算相关矩阵' }}
        </button>

        <div v-if="mxResult?.found && mxResult.factors.length" class="overflow-x-auto">
          <table class="tbl">
            <thead>
              <tr>
                <th>因子 \\ 相关</th>
                <th v-for="f in mxResult.factors" :key="f.code" class="text-right text-xs">{{ f.code }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(row, ri) in mxResult.factors" :key="row.code">
                <td class="text-xs">
                  <p class="font-semibold text-fg">{{ row.name }}</p>
                  <p class="font-mono text-[10px] text-subtle">IC {{ row.ic ?? '—' }} · R {{ row.rank_ic ?? '—' }}</p>
                </td>
                <td v-for="(fv, ci) in mxResult.factors" :key="fv.code" class="num tbl-num">
                  <span class="rounded px-1.5 py-0.5" :style="heatStyle(mxResult.corr[ri]?.[ci] ?? 0)">
                    {{ (mxResult.corr[ri]?.[ci] ?? 0).toFixed(3) }}
                  </span>
                </td>
              </tr>
            </tbody>
          </table>
          <p class="mt-2 text-[11px] text-subtle">高相关（|r|>0.7）提示冗余，建议只保留预测力较强的一个；矩阵基于各标的因子序列相关等权平均。</p>
        </div>
        <StateView v-else-if="mxResult && !mxResult.found" icon="search" :title="mxResult.message ?? '暂无结果'" desc="请确认本地有该窗口的 K 线数据。" />
      </div>
    </Modal>

    <!-- ML 多因子训练弹窗 -->
    <Modal :open="mlOpen" title="ML 多因子训练" sub="因子作特征 → 按时间序列切分 → L2 Ridge 回归预测未来收益 → 样本外评价" width="720px" @close="mlOpen = false">
      <div class="space-y-4">
        <div>
          <p class="mb-2 text-xs font-semibold uppercase tracking-wider text-subtle">特征（因子，2~8）</p>
          <div class="flex flex-wrap gap-1.5">
            <button v-for="f in factors" :key="f.code" class="chip" :class="{ 'is-active': mlCodes.has(f.code) }" @click="toggleMl(f.code)">
              {{ f.name }}
            </button>
          </div>
        </div>
        <div class="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <div class="field">
            <label class="label" for="ml-symbol">交易对</label>
            <input id="ml-symbol" v-model="mlCfg.symbol" class="input font-mono uppercase" spellcheck="false" />
          </div>
          <div class="field">
            <label class="label" for="ml-interval">粒度</label>
            <select id="ml-interval" v-model="mlCfg.interval" class="select font-mono">
              <option v-for="i in INTERVALS.filter((x) => ['15m', '30m', '1h', '1d'].includes(x))" :key="i" :value="i">{{ i }}</option>
            </select>
          </div>
          <div class="field">
            <label class="label" for="ml-horizon">预测窗口(h)</label>
            <select id="ml-horizon" v-model.number="mlCfg.horizon" class="select">
              <option :value="1">1</option>
              <option :value="3">3</option>
              <option :value="5">5</option>
            </select>
          </div>
          <div class="field">
            <label class="label" for="ml-model">模型</label>
            <select id="ml-model" v-model="mlCfg.model" class="select">
              <option value="ridge">Ridge 线性</option>
              <option value="gbdt">LightGBM</option>
            </select>
          </div>
          <div class="field">
            <label class="label" for="ml-window">样本窗口</label>
            <select id="ml-window" v-model.number="mlCfg.days" class="select">
              <option :value="180">180 天</option>
              <option :value="365">1 年</option>
              <option :value="730">2 年</option>
            </select>
          </div>
        </div>
        <button class="btn btn-primary" :disabled="mlLoading || mlCodes.size < 2" @click="runMl">
          <LoaderCircle v-if="mlLoading" :size="14" class="animate-spin" />{{ mlLoading ? '训练中…' : '开始训练' }}
        </button>

        <div v-if="mlResult?.found" class="space-y-4">
          <div class="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <div class="panel px-3.5 py-3">
              <p class="text-[11px] uppercase tracking-wider text-subtle">OOS IC</p>
              <p class="num mt-1 text-lg font-semibold" :class="(mlResult.oos_ic ?? 0) > 0 ? 'text-up' : (mlResult.oos_ic ?? 0) < 0 ? 'text-down' : 'text-fg'">{{ mlResult.oos_ic ?? '—' }}</p>
            </div>
            <div class="panel px-3.5 py-3">
              <p class="text-[11px] uppercase tracking-wider text-subtle">OOS RankIC</p>
              <p class="num mt-1 text-lg font-semibold text-fg">{{ mlResult.oos_rank_ic ?? '—' }}</p>
            </div>
            <div class="panel px-3.5 py-3">
              <p class="text-[11px] uppercase tracking-wider text-subtle">命中率</p>
              <p class="num mt-1 text-lg font-semibold text-fg">{{ (mlResult.hit_rate * 100).toFixed(1) }}%</p>
            </div>
            <div class="panel px-3.5 py-3">
              <p class="text-[11px] uppercase tracking-wider text-subtle">Top−Bottom 价差</p>
              <p class="num mt-1 text-lg font-semibold" :class="mlResult.spread > 0 ? 'text-up' : 'text-down'">{{ mlResult.spread.toFixed(5) }}</p>
            </div>
          </div>
          <p class="text-[11px] text-subtle">训练 {{ mlResult.n_train }} · 测试 {{ mlResult.n_test }} 行 · 模型 {{ mlResult.model }}（{{ mlResult.engine }}）· 未来 {{ mlResult.horizon }} 根收益为标签</p>
          <div v-if="mlLast && mlResult?.model !== 'gbdt'" class="flex gap-2">
            <input v-model="mlName" class="input h-8 flex-1 font-mono text-xs" placeholder="模型名称，如 btc-1h-ridge" maxlength="120" />
            <button class="btn btn-primary !h-8 shrink-0 text-xs" :disabled="mlSaving || !mlName.trim()" @click="saveResult">
              <LoaderCircle v-if="mlSaving" :size="13" class="animate-spin" /><Check v-else :size="13" />保存模型
            </button>
          </div>
          <p class="text-[10px] text-subtle">保存后可到「模型库」对任意标的的最新 K 线打分（线性打分，不含未来）。</p>
          <p v-if="mlResult.feature_filter?.removed?.length" class="text-[11px] text-warn">
            已按 |相关| &gt; {{ mlResult.feature_filter.max_corr }} 剔除冗余特征：{{ mlResult.feature_filter.removed.map((r) => r.code).join('、') }}
          </p>
          <div class="overflow-x-auto">
            <table class="tbl">
              <thead>
                <tr>
                  <th>特征</th>
                  <th class="text-right">系数(coef)</th>
                  <th class="text-right">单因子 OOS IC</th>
                  <th class="text-right">RankIC</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="f in mlResult.features" :key="f.code">
                  <td>
                    <span class="font-semibold text-fg">{{ f.name }}</span>
                    <span class="ml-1 font-mono text-[10px] text-subtle">{{ f.code }}</span>
                  </td>
                  <td class="num tbl-num" :class="f.coef > 0 ? 'text-up' : f.coef < 0 ? 'text-down' : 'text-muted'">{{ f.coef.toFixed(5) }}</td>
                  <td class="num tbl-num text-muted">{{ f.ic_oos ?? '—' }}</td>
                  <td class="num tbl-num text-muted">{{ f.rank_ic_oos ?? '—' }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
        <StateView v-else-if="mlResult && !mlResult.found" icon="search" :title="mlResult.message ?? '训练无结果'" desc="需要至少 60 根可用 K 线与 2 个因子特征。" />
      </div>
    </Modal>

    <!-- 模型库 -->
    <Modal :open="modelsOpen" title="模型库" sub="已保存的 Ridge 模型：可复用对最新 K 线打分" width="720px" @close="modelsOpen = false">
      <div v-if="modelsLoading" class="py-10"><StateView busy title="读取模型…" /></div>
      <StateView v-else-if="!modelsList.length" icon="empty" title="还没有模型" desc="在「ML 训练」里跑出结果后填写名称即可保存。" />
      <div v-else class="space-y-2">
        <div v-for="m in modelsList" :key="m.id" class="flex flex-wrap items-center gap-3 rounded-xl border border-border bg-surface-2/40 px-3.5 py-2.5">
          <div class="min-w-0 flex-1">
            <p class="text-sm font-semibold text-fg">{{ m.name }}</p>
            <p class="mt-0.5 text-[11px] text-subtle">
              {{ m.interval }} · 预测 {{ m.horizon }}h · {{ (m.symbols ?? []).join(', ') }} · {{ m.n_train }}tr/{{ m.n_test }}te · {{ timeAgo(m.created_at) }}
            </p>
            <p class="num mt-0.5 text-[11px] text-muted">
              OOS IC {{ m.metrics?.oos_ic ?? '—' }} · RankIC {{ m.metrics?.oos_rank_ic ?? '—' }} · 命中 {{ m.metrics?.hit_rate != null ? (m.metrics.hit_rate * 100).toFixed(1) + '%' : '—' }}
            </p>
          </div>
          <div class="flex shrink-0 items-center gap-2">
            <button class="btn btn-primary !h-8 text-xs" @click="openBt(m)">建策略回测</button>
            <button class="btn btn-ghost !h-8 text-xs" @click="scoreModel(m)">打分</button>
          </div>
        </div>
      </div>
    </Modal>

    <!-- 模型 → 策略回测 -->
    <Modal :open="btOpen" :title="`用模型建策略 · ${btModel?.name ?? ''}`" sub="以该模型打分作择时信号，走标准回测通道（含 SSE 实时进度）" width="520px" @close="btOpen = false">
      <div class="space-y-4">
        <div class="grid grid-cols-2 gap-4">
          <div class="field">
            <label class="label" for="bt-sym">交易对</label>
            <input id="bt-sym" v-model="btCfg.symbol" class="input font-mono uppercase" spellcheck="false" />
          </div>
          <div class="field">
            <label class="label">颗粒度</label>
            <div class="flex h-[34px] items-center rounded-[9px] border border-border bg-surface px-3 font-mono text-sm text-fg">{{ btModel?.interval }}</div>
          </div>
        </div>
        <div class="grid grid-cols-3 gap-3">
          <div class="field">
            <label class="label" for="bt-days">回看天数</label>
            <input id="bt-days" v-model.number="btCfg.days" type="number" min="30" class="input" />
          </div>
          <div class="field">
            <label class="label" for="bt-window">滚动窗口</label>
            <input id="bt-window" v-model.number="btCfg.window" type="number" min="20" class="input" />
          </div>
          <div class="field">
            <label class="label">q80 / q20 入场离场</label>
            <div class="flex h-[34px] items-center gap-1 rounded-[9px] border border-border bg-surface px-2 font-mono text-xs text-muted">
              <input v-model.number="btCfg.enterQ" type="number" step="0.05" min="0.5" max="0.99" class="w-full bg-transparent outline-none" />
              <span>/</span>
              <input v-model.number="btCfg.exitQ" type="number" step="0.05" min="0.01" max="0.5" class="w-full bg-transparent outline-none" />
            </div>
          </div>
        </div>
        <p class="text-xs leading-relaxed text-subtle">
          打分高于滚动 {{ btCfg.enterQ }} 分位 → 满仓做多；低于 {{ btCfg.exitQ }} 分位 → 清仓。只用 ≤t 信息，无未来函数。可直接与「买入持有」基准对比。
        </p>
      </div>
      <template #footer>
        <div class="flex items-center justify-end gap-2">
          <button class="btn btn-ghost" @click="btOpen = false">取消</button>
          <button class="btn btn-primary" :disabled="btBusy || !btCfg.symbol.trim()" @click="runBt">
            <LoaderCircle v-if="btBusy" :size="15" class="animate-spin" />{{ btBusy ? '创建并回测中…' : '创建策略并回测' }}
          </button>
        </div>
      </template>
    </Modal>
  </div>
</template>
