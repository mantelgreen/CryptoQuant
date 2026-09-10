<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { Boxes, Copy, FlaskConical, Info, LayoutGrid, List, ListChecks, LoaderCircle, RefreshCw, Search, Send, Sparkles, Trash2, X } from '@lucide/vue'
import PageHeader from '@/components/ui/PageHeader.vue'
import HelpDrawer, { type PageHelp } from '@/components/ui/HelpDrawer.vue'
import Panel from '@/components/ui/Panel.vue'
import Modal from '@/components/ui/Modal.vue'
import StateView from '@/components/ui/StateView.vue'
import Pagination from '@/components/ui/Pagination.vue'
import { useToastStore } from '@/stores/toast'
import {
  listFactors,
  fetchLatestRuns,
  fetchRunsByCode,
  factorMatrix,
  researchFactors,
  researchFactorsCS,
  removeUserFactor,
  getFactorUsage,
  type FactorMeta,
  type FactorResearch,
  type FactorRun,
  type FactorMatrix,
  type FactorUsage,
  type CsMetrics,
} from '@/api/factor'
import EquityChart from '@/components/chart/EquityChart.vue'
import { pickFactorsByAi, type FactorPickItem, type FactorPick } from '@/api/agent'
import { useTrainHandoffStore } from '@/stores/trainHandoff'
import { INTERVALS, MAJOR_SYMBOLS, timeAgo, fmtTime, MAX_ML_FEATURES } from '@/utils/format'

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
  tips: ['流程建议：单因子 IC 筛选 → 相关矩阵去冗余 → 组合 → 做成策略回测。', '库里只有 15m 时可选 30m/1h/1d，后端会自动按时间桶聚合。', '“研究全部”= 对库内因子在 BTC 1d 上批量体检，方便横向对比。'],
}

const toast = useToastStore()
const loading = ref(true)
const error = ref<string | null>(null)
const factors = ref<FactorMeta[]>([])
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

const counts = computed(() => {
  const all = Object.values(results.value)
  let bestVal: number | null = null
  let bestName = ''
  for (const r of all) {
    if (r.rank_ic == null) continue
    if (bestVal == null || Math.abs(r.rank_ic) > Math.abs(bestVal)) {
      bestVal = r.rank_ic
      bestName = r.name
    }
  }
  return {
    total: factors.value.length,
    researched: all.length,
    best: bestVal == null ? null : { value: bestVal, name: bestName },
  }
})

// —— 卡片 / 表格视图 与 分页 ——
const view = ref<'card' | 'table'>('card')
const pageSize = ref(9)
const page = ref(1)
const pageCount = computed(() => Math.max(1, Math.ceil(filtered.value.length / pageSize.value)))
const paged = computed(() => filtered.value.slice((page.value - 1) * pageSize.value, page.value * pageSize.value))
watch([q, tag, pageSize], () => {
  page.value = 1
})
watch(pageCount, (n) => {
  if (page.value > n) page.value = Math.max(1, n)
})

function fmtR(v: number | null | undefined): string {
  return v == null ? '—' : (v >= 0 ? '+' : '') + v.toFixed(4)
}

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
    symbols: (r.symbol || '').split('|').filter(Boolean),
    start: r.start_time,
    end: r.end_time,
  }
}

async function prefillRuns() {
  try {
    const runs = await fetchLatestRuns()
    if (!runs.length) return
    const metaByCode = new Map(factors.value.map((f) => [f.code, f]))
    const out: Record<string, FactorResearch> = {}
    const latestByCode: Record<string, FactorRun> = {}
    for (const r of runs) {
      // runs 按 created_at 降序，第一次遇到即最新
      if (metaByCode.has(r.code) && !(r.code in latestByCode)) {
        latestByCode[r.code] = r
      }
    }
    // 优先默认口径（BTCUSDT/1d），取其中最新
    for (const r of runs) {
      const code = r.code
      if (!metaByCode.has(code) || code in out) continue
      if ((r.symbol || '').split('|').includes('BTCUSDT') && r.interval === '1d') {
        out[code] = toResearch(r, metaByCode.get(code))
      }
    }
    // 无默认口径记录的因子，退回到最新一次（任意口径）
    for (const code in latestByCode) {
      if (!(code in out)) out[code] = toResearch(latestByCode[code], metaByCode.get(code))
    }
    results.value = out
  } catch {
    /* 历史读取失败不阻塞列表 */
  }
}

function researchScope(r: FactorResearch | undefined): string {
  if (!r) return ''
  const syms = r.symbols ?? []
  const s = syms.length === 1 ? syms[0] : syms.length > 1 ? `${syms[0]}等${syms.length}` : ''
  return `${s ? s + '·' : ''}${r.interval}`
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
    // 统一走研究接口：内置因子与用户/AI 表达式因子都落库 IC 记录，便于「历史」回看
    const res = await researchFactors({
      codes: [active.value.code],
      symbols: [symbol],
      interval: form.value.interval,
      start: start.toISOString(),
      end: end.toISOString(),
    })
    const r = res[0]
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

// —— 截面研究（同一时点跨标的 IC，需多标的广度） ——
const showCs = ref(false)
const csRunning = ref(false)
const csActive = ref<FactorMeta | null>(null)
const csResult = ref<CsMetrics | null>(null)
const csForm = ref({
  symbols: MAJOR_SYMBOLS.join(', '),
  interval: '1d',
  days: 365,
  horizon: 1,
  minNames: 5,
  neutralize: true,
})

function openCs(f: FactorMeta) {
  csActive.value = f
  csResult.value = null
  csForm.value = {
    symbols: MAJOR_SYMBOLS.join(', '),
    interval: '1d',
    days: 365,
    horizon: 1,
    minNames: 5,
    neutralize: true,
  }
  showCs.value = true
}

function parseSymbols(text: string): string[] {
  return Array.from(
    new Set(text.split(/[\s,，;；]+/).map((s) => s.trim().toUpperCase()).filter(Boolean)),
  )
}

async function runCs() {
  if (!csActive.value || csRunning.value) return
  const syms = parseSymbols(csForm.value.symbols)
  if (syms.length < 2) {
    toast.error('标的不足', '截面研究至少需要 2 个标的，建议 ≥ 20 个')
    return
  }
  csRunning.value = true
  const end = new Date()
  const start = new Date(end.getTime() - csForm.value.days * 24 * 3600 * 1000)
  try {
    const res = await researchFactorsCS({
      codes: [csActive.value.code],
      symbols: syms,
      interval: csForm.value.interval,
      start: start.toISOString(),
      end: end.toISOString(),
      horizon: csForm.value.horizon,
      min_names: csForm.value.minNames,
      neutralize: csForm.value.neutralize,
    })
    const r = res[0] ?? null
    csResult.value = r
    if (r?.found) {
      toast.success('截面研究完成', `${r.name} RankIC=${r.rank_ic ?? '—'} · IR=${r.rank_ic_ir ?? '—'}`)
    } else {
      toast.error('截面样本不足', r?.message ?? '请先下载更多标的的 K 线')
    }
  } catch (e: unknown) {
    toast.error('截面研究失败', (e as { message?: string }).message)
  } finally {
    csRunning.value = false
  }
}

function resultOf(code: string): FactorResearch | undefined {
  return results.value[code]
}

function chipTags(f: FactorMeta): string[] {
  // AI 已由右上角 tone-ai 徽标表达，避免在标签区重复
  return (f.tags ?? []).filter((t) => !(f.is_user && t === 'AI'))
}

function tagTone(t: string): string {
  return t === '优质' ? 'tone-up' : 'tone-neutral'
}

function copyFormula(f: FactorMeta) {
  navigator.clipboard?.writeText(f.expression ?? f.code).then(
    () => toast.success('已复制公式', f.name),
    () => toast.error('复制失败'),
  )
}

function decay1(r: FactorResearch | undefined): string {
  const v = r?.decay?.h_1
  return v != null ? v.toFixed(4) : '—'
}

// —— 删除因子（内置不可删 / 被引用不可删） ——
const delOpen = ref(false)
const delChecking = ref(false)
const delBusy = ref(false)
const delFactor = ref<FactorMeta | null>(null)
const delUsage = ref<FactorUsage | null>(null)
const delErr = ref('')

const delIsBuiltin = computed(() => !!delFactor.value && !delFactor.value.is_user)
const delBound = computed(() => {
  const u = delUsage.value
  return !!u && (u.models.length > 0 || u.strategies.length > 0)
})
const delBlocked = computed(() => delIsBuiltin.value || delBound.value)

async function requestRemove(f: FactorMeta) {
  delFactor.value = f
  delUsage.value = null
  delErr.value = ''
  delOpen.value = true
  if (!f.is_user) return // 内置：无需查询，直接提示不可删
  delChecking.value = true
  try {
    delUsage.value = await getFactorUsage(f.code)
  } catch (e: unknown) {
    delErr.value = (e as { message?: string }).message ?? '检查引用失败'
  } finally {
    delChecking.value = false
  }
}

async function confirmRemoveFactor() {
  const f = delFactor.value
  if (!f || delBusy.value || delBlocked.value) return
  delBusy.value = true
  delErr.value = ''
  try {
    await removeUserFactor(f.code)
    delOpen.value = false
    toast.success('已从因子库删除', f.name)
    await load()
  } catch (e: unknown) {
    const payload = e as { code?: string; message?: string; detail?: FactorUsage }
    if (payload.code === 'CONFLICT' && payload.detail) {
      delUsage.value = payload.detail
    } else {
      delErr.value = payload.message ?? '删除失败'
    }
  } finally {
    delBusy.value = false
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

// —— 候选因子池：浮动按钮 → 勾选因子入库池 → AI 选 / 发送至模型训练 ——
const router = useRouter()
const candOpen = ref(false)
const candCodes = ref<Set<string>>(new Set())
const aiBusy = ref(false)
const aiPhase = ref('')
const aiPicks = ref<FactorPick[]>([])

const candList = computed(() => factors.value.filter((f) => candCodes.value.has(f.code)))

function isCand(code: string): boolean {
  return candCodes.value.has(code)
}
function toggleCand(code: string) {
  const s = new Set(candCodes.value)
  if (s.has(code)) s.delete(code)
  else s.add(code)
  candCodes.value = s
}
function clearCand() {
  candCodes.value = new Set()
  aiPicks.value = []
}

// —— 候选因子浮动按钮：可上下拖动（位置持久化），避免盖住分页 ——
const FAB_KEY = 'cq:factorHubFabY'
const FAB_BOTTOM_MIN = 12
const fabY = ref(clampFabY(Number(localStorage.getItem(FAB_KEY) ?? 24)))
let drag: { startY: number; baseY: number; moved: boolean } | null = null

function clampFabY(v: number): number {
  const max = Math.max(FAB_BOTTOM_MIN, (typeof window !== 'undefined' ? window.innerHeight : 900) - 12 - 56)
  return Math.min(max, Math.max(FAB_BOTTOM_MIN, v || FAB_BOTTOM_MIN))
}
function fabBottomStyle(): Record<string, string> {
  return { bottom: `${fabY.value}px`, right: '20px' }
}
function candPanelStyle(): Record<string, string> {
  const gap = 12
  const fabH = 48
  const bottom = Math.min(fabY.value + fabH + gap, Math.max(16, window.innerHeight - 16))
  const avail = window.innerHeight - bottom - 12
  return {
    bottom: `${bottom}px`,
    right: '20px',
    maxHeight: `${Math.max(160, Math.min(560, avail))}px`,
  }
}
function onFabPointerDown(e: PointerEvent) {
  if (e.button !== 0) return
  e.preventDefault()
  const el = e.currentTarget as HTMLElement
  el.setPointerCapture?.(e.pointerId)
  drag = { startY: e.clientY, baseY: fabY.value, moved: false }
  document.body.style.userSelect = 'none'
  document.body.style.cursor = 'grabbing'
}
function onFabPointerMove(e: PointerEvent) {
  if (!drag) return
  if (Math.abs(e.clientY - drag.startY) > 3) drag.moved = true
  fabY.value = clampFabY(drag.baseY - (e.clientY - drag.startY))
}
function endFabDrag(e?: PointerEvent) {
  if (!drag) return
  const wasClick = !drag.moved && (!e || Math.abs(e.clientY - drag.startY) <= 3)
  drag = null
  document.body.style.userSelect = ''
  document.body.style.cursor = ''
  localStorage.setItem(FAB_KEY, String(Math.round(fabY.value)))
  if (wasClick) candOpen.value = !candOpen.value
}
function cancelFabDrag() {
  if (!drag) return
  drag = null
  document.body.style.userSelect = ''
  document.body.style.cursor = ''
  localStorage.setItem(FAB_KEY, String(Math.round(fabY.value)))
}
function candMeasured(r: FactorResearch | undefined): boolean {
  return !!r?.found && (r.ic != null || r.rank_ic != null)
}

const IC_REUSE_MAX_AGE_MS = 7 * 24 * 3600 * 1000
const IC_REUSE_MIN_WINDOW_MS = 300 * 24 * 3600 * 1000
function reuseIc(r: FactorResearch | undefined): boolean {
  if (!r?.found || (r.ic == null && r.rank_ic == null)) return false
  if (r.interval !== '1d') return false
  if (!(r.symbols ?? []).includes('BTCUSDT')) return false
  if (r.end) {
    const age = Date.now() - new Date(r.end).getTime()
    if (!Number.isFinite(age) || age > IC_REUSE_MAX_AGE_MS) return false
  }
  if (r.start && r.end) {
    const span = new Date(r.end).getTime() - new Date(r.start).getTime()
    if (Number.isFinite(span) && span < IC_REUSE_MIN_WINDOW_MS) return false
  }
  return true
}
function toPickItem(meta: FactorMeta, r: FactorResearch): FactorPickItem {
  return {
    code: meta.code,
    name: meta.name,
    tags: meta.tags ?? [],
    description: meta.description,
    ic: r.ic,
    rank_ic: r.rank_ic,
    n_total: r.n_total,
    interval: r.interval,
    decay: { h1: r.decay?.h_1 ?? null, h3: r.decay?.h_3 ?? null, h5: r.decay?.h_5 ?? null },
  }
}

async function measureCodes(codes: string[], onPhase: (s: string) => void) {
  const out: Record<string, FactorResearch> = { ...results.value }
  const end = new Date()
  const start = new Date(end.getTime() - 365 * 24 * 3600 * 1000)
  for (let i = 0; i < codes.length; i += 4) {
    const part = codes.slice(i, i + 4)
    onPhase(`体检候选因子 ${Math.min(i + 4, codes.length)}/${codes.length}…`)
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
}

function candMeta(code: string): FactorMeta | undefined {
  return factors.value.find((f) => f.code === code)
}

async function aiSelect() {
  if (aiBusy.value || loading.value) return
  // 已有候选时优先基于候选池分析；否则对整库做首轮 AI 推荐
  const pool = candList.value.length >= 2 ? candList.value : factors.value.slice(0, 20)
  if (pool.length < 2) {
    toast.error('候选不足', '至少需要 2 个因子才能做 AI 挑选')
    return
  }
  aiBusy.value = true
  aiPicks.value = []
  aiPhase.value = '准备候选因子的实测数据…'
  try {
    // 统一口径为 BTCUSDT/1d/近一年：已存在同口径且足够新鲜的 IC 直接复用，只补测缺失/过期/口径不符的因子
    const allCodes = pool.map((f) => f.code)
    const need = allCodes.filter((c) => !reuseIc(results.value[c]))
    if (need.length) {
      await measureCodes(need, (s) => (aiPhase.value = s))
    } else {
      aiPhase.value = '复用已有 IC（BTCUSDT/1d），无需重算…'
    }
    const usable = pool
      .map((f) => ({ f, r: results.value[f.code] }))
      .filter((x): x is { f: FactorMeta; r: FactorResearch } => !!x.r && candMeasured(x.r))
    if (usable.length < 2) {
      toast.error('实测数据不足', '所选因子在窗口内都算不出有效 IC/RankIC，请先手动「研究 IC」或换窗口')
      return
    }
    aiPhase.value = 'AI 正在综合 IC / RankIC / 衰减分析并挑选…'
    const picks = await pickFactorsByAi({
      factors: usable.map((x) => toPickItem(x.f, x.r)).slice(0, 40),
      max_pick: Math.min(8, usable.length),
    })
    if (!picks.picks?.length) {
      toast.error('AI 未选出因子', '请检查「模型设置」里是否配置并启用了默认 LLM 供应商')
      return
    }
    const valid = picks.picks.filter((p) => factors.value.some((f) => f.code === p.code))
    const next = new Set(candCodes.value)
    for (const p of valid) next.add(p.code)
    candCodes.value = next
    aiPicks.value = valid
    if (picks.fallback) {
      toast.info('AI 已按规则兜底挑选', `模型未返回可用 JSON，已按 |IC| 规则挑选 ${valid.length} 个`)
    } else {
      toast.ai('AI 已挑选候选因子', `模型 ${picks.model ?? ''} 分析后选中 ${valid.length} 个（已并入候选池）`)
    }
  } catch (e: unknown) {
    toast.error('AI 挑选失败', (e as { message?: string }).message)
  } finally {
    aiBusy.value = false
    aiPhase.value = ''
  }
}

function sendToTrain() {
  const set = candList.value
  if (!set.length) {
    toast.error('候选池为空', '请先勾选因子，或点「AI 选」让模型挑选')
    return
  }
  const codes = set.slice(0, MAX_ML_FEATURES).map((f) => f.code)
  if (set.length > MAX_ML_FEATURES) toast.info(`已截取前 ${MAX_ML_FEATURES} 个`, `模型训练页最多支持 ${MAX_ML_FEATURES} 个特征因子`)
  useTrainHandoffStore().stage(codes)
  void router.push({ name: 'ml-train' })
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
        <router-link to="/models" class="btn btn-ghost">模型库</router-link>
        <router-link to="/train" class="btn btn-ghost">ML 训练</router-link>
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
        <p class="text-[11px] uppercase tracking-wider text-subtle">因子总数</p>
        <p class="num mt-1 text-xl font-semibold text-fg">{{ counts.total }}</p>
      </div>
      <div class="panel px-4 py-3">
        <p class="text-[11px] uppercase tracking-wider text-subtle">已研究</p>
        <p class="num mt-1 text-xl font-semibold text-fg">{{ counts.researched }}</p>
      </div>
      <div class="panel px-4 py-3" :title="counts.best ? `按 |RankIC| 取最强：${counts.best.name}；负值表示强反向信号，取反后同样可用` : '还没有已研究且有 RankIC 的因子'">
        <p class="text-[11px] uppercase tracking-wider text-subtle">最优 |RankIC|</p>
        <p class="num mt-1 text-xl font-semibold" :class="counts.best && counts.best.value > 0 ? 'text-up' : 'text-fg'">
          {{ counts.best ? counts.best.value.toFixed(4) : '—' }}
        </p>
        <p v-if="counts.best" class="mt-0.5 truncate text-[10px] text-subtle" :title="counts.best.name">
          {{ counts.best.name }}{{ counts.best.value < 0 ? '（反向可用）' : '' }}
        </p>
        <p v-else class="mt-0.5 text-[10px] text-subtle">先「研究 IC」实测</p>
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
        <div class="ml-auto flex items-center gap-2">
          <span class="text-xs text-subtle">共 {{ filtered.length }} 个</span>
          <div class="flex items-center gap-0.5 rounded-lg bg-surface-2 p-0.5" role="tablist" aria-label="视图切换">
            <button
              role="tab"
              :aria-selected="view === 'card'"
              class="flex h-7 items-center gap-1.5 rounded-md px-2.5 text-xs font-medium transition-colors"
              :class="view === 'card' ? 'bg-surface text-fg shadow-sm' : 'text-muted hover:text-fg'"
              title="卡片视图"
              @click="view = 'card'"
            >
              <LayoutGrid :size="13" />卡片
            </button>
            <button
              role="tab"
              :aria-selected="view === 'table'"
              class="flex h-7 items-center gap-1.5 rounded-md px-2.5 text-xs font-medium transition-colors"
              :class="view === 'table' ? 'bg-surface text-fg shadow-sm' : 'text-muted hover:text-fg'"
              title="表格视图"
              @click="view = 'table'"
            >
              <List :size="13" />表格
            </button>
          </div>
        </div>
      </div>

      <div v-if="loading" class="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
        <div v-for="i in 6" :key="i" class="panel h-44 animate-pulse"><div class="space-y-3 p-5"><div class="h-3.5 w-2/5 rounded bg-surface-2" /><div class="h-2.5 w-4/5 rounded bg-surface-2" /><div class="h-2.5 w-3/5 rounded bg-surface-2" /></div></div>
      </div>

      <template v-else-if="filtered.length">
        <!-- 卡片视图 -->
        <div v-if="view === 'card'" class="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
          <article
            v-for="f in paged"
            :key="f.code"
            class="group panel flex flex-col p-5"
            :class="candOpen && isCand(f.code) ? 'border-primary/70 ring-1 ring-primary/30' : candOpen ? 'border-border' : ''"
          >
            <div class="flex items-center gap-2">
              <label
                v-if="candOpen"
                class="flex h-5 w-5 shrink-0 cursor-pointer items-center justify-center"
                :title="isCand(f.code) ? `把「${f.name}」移出候选池` : `把「${f.name}」加入候选池`"
              >
                <input
                  type="checkbox"
                  class="accent-[var(--color-primary)]"
                  :checked="isCand(f.code)"
                  :disabled="aiBusy"
                  @change="toggleCand(f.code)"
                />
              </label>
              <span class="flex h-9 w-9 items-center justify-center rounded-lg tint-primary text-primary-ink dark:text-primary">
                <Boxes :size="16" />
              </span>
              <div class="min-w-0 flex-1">
                <h3 class="truncate text-[15px] font-semibold text-fg">{{ f.name }}</h3>
                <p v-if="!f.is_user" class="truncate font-mono text-[11px] text-subtle" :title="f.code">{{ f.code }}</p>
              </div>
              <span class="badge shrink-0" :class="f.is_user ? 'tone-ai' : 'tone-neutral'">{{ f.is_user ? 'AI' : '内置' }}</span>
              <button
                v-if="!candOpen"
                class="shrink-0 rounded-md p-1 text-subtle opacity-0 transition-colors hover:bg-danger/10 hover:text-danger focus-visible:opacity-100 group-hover:opacity-100"
                :aria-label="`删除因子 ${f.name}`"
                @click="requestRemove(f)"
              >
                <Trash2 :size="13" />
              </button>
            </div>

            <p class="mt-3 line-clamp-2 flex-1 text-[13px] leading-relaxed text-muted">{{ f.description }}</p>

            <div v-if="f.is_user && f.expression" class="mt-2.5 flex items-start gap-1.5">
              <code class="min-w-0 flex-1 max-h-24 overflow-y-auto whitespace-pre-wrap break-all rounded-md bg-inset px-2.5 py-2 font-mono text-[10.5px] leading-relaxed text-fg">{{ f.expression }}</code>
              <button class="btn btn-icon !h-7 !w-7 shrink-0 text-muted hover:text-fg" title="复制公式" @click="copyFormula(f)">
                <Copy :size="12" />
              </button>
            </div>

            <div class="mt-3 flex flex-wrap gap-1.5">
              <span v-for="t in chipTags(f)" :key="t" class="badge" :class="tagTone(t)">{{ t }}</span>
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
                  <span>样本 {{ resultOf(f.code)?.n_total ?? 0 }}<template v-if="researchScope(resultOf(f.code))"> · {{ researchScope(resultOf(f.code)) }}</template></span>
                  <span class="font-mono">衰减 h1 = {{ decay1(resultOf(f.code)) }}</span>
                </div>
              </template>
              <p v-else class="text-xs text-subtle">尚未研究 —— 点「研究 IC」在本地数据上实测。</p>
            </div>

            <div class="mt-3 flex items-center gap-2">
              <button class="btn btn-primary !h-8 flex-1 !px-2 text-xs" @click="openResearch(f)">
                <FlaskConical :size="13" />研究 IC
              </button>
              <button class="btn btn-ghost !h-8 !px-2.5 text-xs" @click="openHistory(f)">历史</button>
            </div>
          </article>
        </div>

        <!-- 表格视图 -->
        <Panel v-else :pad="'none'" class="overflow-hidden">
          <div class="overflow-x-auto">
            <table class="tbl">
              <thead>
                <tr>
                  <th v-if="candOpen" class="!w-8" :aria-label="candOpen ? '加入候选' : undefined"></th>
                  <th>因子</th>
                  <th>标签</th>
                  <th class="text-right">实测 IC / RankIC</th>
                  <th class="text-right">判定</th>
                  <th class="text-right">操作</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="f in paged"
                  :key="f.code"
                  class="align-top"
                  :class="candOpen && isCand(f.code) ? '!bg-primary/5' : ''"
                >
                  <td v-if="candOpen">
                    <label
                      class="flex h-5 w-5 cursor-pointer items-center justify-center"
                      :title="isCand(f.code) ? `把「${f.name}」移出候选池` : `把「${f.name}」加入候选池`"
                    >
                      <input type="checkbox" class="accent-[var(--color-primary)]" :checked="isCand(f.code)" :disabled="aiBusy" @change="toggleCand(f.code)" />
                    </label>
                  </td>
                  <td>
                    <p class="flex items-center gap-1.5">
                      <span class="max-w-[15rem] truncate font-semibold text-fg" :title="f.name">{{ f.name }}</span>
                      <span class="badge shrink-0 !h-[16px] !px-1.5 text-[9px]" :class="f.is_user ? 'tone-ai' : 'tone-neutral'">{{ f.is_user ? 'AI' : '内置' }}</span>
                    </p>
                    <p class="mt-0.5 max-w-[15rem] truncate font-mono text-[10px] text-subtle" :title="f.is_user ? f.expression : f.code">
                      {{ f.is_user ? (f.expression ?? f.code) : f.code }}
                    </p>
                    <p class="mt-0.5 line-clamp-1 text-[11px] text-muted" :title="f.description">{{ f.description }}</p>
                  </td>
                  <td>
                    <div class="flex max-w-[12rem] flex-wrap gap-1">
                      <span v-for="t in chipTags(f)" :key="t" class="badge !h-[16px] !px-1.5 text-[9px]" :class="tagTone(t)">{{ t }}</span>
                      <span v-if="f.min_bars" class="badge tone-neutral !h-[16px] !px-1.5 text-[9px]">≥{{ f.min_bars }} bar</span>
                    </div>
                  </td>
                  <td class="tbl-num">
                    <template v-if="resultOf(f.code)">
                      <span class="block font-mono text-[11px]" :class="(resultOf(f.code)?.ic ?? 0) >= 0 ? 'text-up' : 'text-down'">IC {{ fmtR(resultOf(f.code)?.ic) }}</span>
                      <span class="mt-0.5 block font-mono text-[11px] text-fg">RankIC {{ fmtR(resultOf(f.code)?.rank_ic) }}</span>
                      <span class="mt-0.5 block font-mono text-[10px] text-subtle">n={{ resultOf(f.code)?.n_total ?? 0 }} · h1 {{ decay1(resultOf(f.code)) }}<template v-if="researchScope(resultOf(f.code))"> · {{ researchScope(resultOf(f.code)) }}</template></span>
                    </template>
                    <span v-else class="text-xs text-subtle">—</span>
                  </td>
                  <td class="tbl-num">
                    <span v-if="grade(f.code)" class="badge" :class="grade(f.code)?.tone">{{ grade(f.code)?.label }}</span>
                    <span v-else class="text-xs text-subtle">—</span>
                  </td>
                  <td>
                    <div class="flex items-center justify-end gap-1">
                      <button class="btn btn-ghost !h-7 !px-2 text-[11px]" title="实测该因子 IC" @click="openResearch(f)">
                        <FlaskConical :size="12" />研究 IC
                      </button>
                      <button class="btn btn-ghost !h-7 !px-2 text-[11px]" title="同一时点跨标的截面 IC（需多标的广度）" @click="openCs(f)">
                        <LayoutGrid :size="12" />截面
                      </button>
                      <button class="btn btn-ghost !h-7 !px-2 text-[11px]" title="查看研究历史" @click="openHistory(f)">历史</button>
                      <button
                        v-if="!candOpen"
                        class="btn btn-icon !h-7 !w-7 text-muted hover:bg-danger/10 hover:text-danger"
                        :title="`删除因子 ${f.name}`"
                        :aria-label="`删除因子 ${f.name}`"
                        @click="requestRemove(f)"
                      >
                        <Trash2 :size="13" />
                      </button>
                    </div>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </Panel>

        <Pagination
          v-if="filtered.length > pageSize"
          v-model:page="page"
          v-model:pageSize="pageSize"
          :total="filtered.length"
          :sizes="[9, 12, 24]"
          class="!mt-2"
        />
      </template>

      <Panel v-else :pad="'none'">
        <StateView
          v-if="!factors.length"
          icon="empty"
          title="因子库还是空的"
          desc="因子库不再内置示例因子：去「AI 因子工坊」用自然语言生成因子并入库，或在这里加入你自己的因子。"
        >
          <template #action>
            <router-link to="/factor/ai-lab" class="btn btn-primary"><Sparkles :size="15" />去 AI 因子工坊</router-link>
          </template>
        </StateView>
        <StateView v-else icon="search" title="没有匹配的因子" desc="换个关键词或清除标签过滤。" />
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

    <!-- 截面研究弹窗 -->
    <Modal :open="showCs" :title="`截面研究 · ${csActive?.name ?? ''}`" sub="同一时点跨标的：因子排名 vs 未来收益排名（IC/IR/t）" width="720px" @close="showCs = false">
      <div class="space-y-4">
        <div class="field">
          <label class="label" for="cs-syms">标的池（逗号/空格分隔，建议 ≥ 20 个）</label>
          <textarea id="cs-syms" v-model="csForm.symbols" rows="3" class="input resize-none font-mono text-[11px]" spellcheck="false" />
        </div>
        <div class="grid grid-cols-2 gap-4">
          <div class="field">
            <label class="label" for="cs-interval">K 线颗粒度</label>
            <select id="cs-interval" v-model="csForm.interval" class="select font-mono">
              <option v-for="i in INTERVALS.filter((x) => ['15m', '30m', '1h', '4h', '1d'].includes(x))" :key="i" :value="i">{{ i }}</option>
            </select>
          </div>
          <div class="field">
            <label class="label" for="cs-horizon">预测窗口 h</label>
            <input id="cs-horizon" v-model.number="csForm.horizon" type="number" min="1" max="10" class="input font-mono" />
          </div>
        </div>
        <div class="grid grid-cols-2 gap-4">
          <div class="field">
            <label class="label">回看窗口</label>
            <div class="flex gap-1.5">
              <button v-for="d in [90, 180, 365, 730]" :key="d" class="chip !h-8" :class="{ 'is-active': csForm.days === d }" @click="csForm.days = d">
                {{ d === 365 ? '1 年' : d === 730 ? '2 年' : `${d} 天` }}
              </button>
            </div>
          </div>
          <div class="field">
            <label class="label" for="cs-min">每期最少标的数</label>
            <input id="cs-min" v-model.number="csForm.minNames" type="number" min="2" max="200" class="input font-mono" />
          </div>
        </div>
        <label class="flex items-center gap-2 text-xs text-muted">
          <input v-model="csForm.neutralize" type="checkbox" class="accent-[var(--color-primary)]" />
          截面去均值（等权市场中性，仅影响 IC 口径）
        </label>

        <div v-if="csResult" class="rounded-lg border border-border bg-surface-2/40 p-3">
          <p v-if="!csResult.found" class="text-xs text-warn">{{ csResult.message }}</p>
          <template v-else>
            <div class="grid grid-cols-3 gap-2 sm:grid-cols-6">
              <div><p class="text-[10px] text-subtle">IC</p><p class="num text-sm font-semibold" :class="(csResult.ic ?? 0) >= 0 ? 'text-up' : 'text-down'">{{ csResult.ic }}</p></div>
              <div><p class="text-[10px] text-subtle">RankIC</p><p class="num text-sm font-semibold" :class="(csResult.rank_ic ?? 0) >= 0 ? 'text-up' : 'text-down'">{{ csResult.rank_ic }}</p></div>
              <div><p class="text-[10px] text-subtle">IC IR</p><p class="num text-sm font-semibold text-fg">{{ csResult.ic_ir ?? '—' }}</p></div>
              <div><p class="text-[10px] text-subtle">t 值</p><p class="num text-sm font-semibold text-fg">{{ csResult.ic_t ?? '—' }}</p></div>
              <div><p class="text-[10px] text-subtle">多空价差</p><p class="num text-sm font-semibold text-fg">{{ csResult.long_short_spread ?? '—' }}</p></div>
              <div><p class="text-[10px] text-subtle">期数/标的</p><p class="num text-sm font-semibold text-fg">{{ csResult.n_periods }} / {{ csResult.avg_names }}</p></div>
            </div>
            <p class="mt-2 text-[10px] leading-snug text-subtle">IC/IR 由同一时点跨标的截面排序计算；|t|&gt;2 才较可信，广度越大越可靠（当前平均 {{ csResult.avg_names }} 个标的/期）。</p>
          </template>
        </div>
        <p v-else class="text-xs leading-relaxed text-subtle">
          截面研究需要同一时点有足够标的（默认 ≥ {{ csForm.minNames }} 个）；先在「K 线下载」批量补齐标的池后再研究。
          时序 IC 高但截面 IC 低，说明信号只在该标的自身历史里成立，难以做成多资产组合。
        </p>
      </div>
      <template #footer>
        <div class="flex items-center justify-end gap-2">
          <button class="btn btn-ghost" @click="showCs = false">取消</button>
          <button class="btn btn-primary" :disabled="csRunning" @click="runCs">
            <LoaderCircle v-if="csRunning" :size="15" class="animate-spin" />
            {{ csRunning ? '研究中…' : '开始截面研究' }}
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

    <!-- 删除因子：内置不可删 / 被引用不可删 / 确认删除 -->
    <Modal
      :open="delOpen"
      :title="delBlocked ? `无法删除 · ${delFactor?.name ?? ''}` : `删除因子 · ${delFactor?.name ?? ''}`"
      :sub="delIsBuiltin ? '系统内置因子' : delBound ? '该因子正被模型 / 策略引用' : 'AI 因子 · 删除后不可恢复'"
      width="520px"
      @close="delOpen = false"
    >
      <div v-if="delChecking" class="py-10"><StateView busy title="检查引用…" /></div>

      <template v-else-if="delIsBuiltin">
        <div class="rounded-lg border border-border bg-surface-2/60 px-3 py-2.5 text-xs leading-relaxed text-muted">
          内置因子随产品提供，研究引擎、相关矩阵与策略模板都直接依赖它的代码，不能删除。你只能删除自己/AI 生成后加入库里的因子。
        </div>
      </template>

      <template v-else-if="delBound">
        <div class="rounded-lg border border-danger/30 bg-danger/10 px-3 py-2.5 text-xs leading-relaxed text-down">
          该因子已经作为特征进入了下面的模型 / 策略，删除会让模型训练与策略打分的特征来源失效，因此不能删除。请先删除引用它的保存模型或策略版本，再回来删除因子。
        </div>

        <div v-if="(delUsage?.models ?? []).length" class="mt-4">
          <p class="text-[10px] font-semibold uppercase tracking-wider text-subtle">引用它的模型（{{ (delUsage?.models ?? []).length }}）</p>
          <ul class="mt-1.5 space-y-1">
            <li v-for="mo in delUsage?.models ?? []" :key="mo.id" class="flex items-center gap-2 text-xs text-fg">
              <Boxes :size="13" class="shrink-0 text-subtle" />{{ mo.name }}
            </li>
          </ul>
        </div>

        <div v-if="(delUsage?.strategies ?? []).length" class="mt-4">
          <p class="text-[10px] font-semibold uppercase tracking-wider text-subtle">引用它的策略（{{ (delUsage?.strategies ?? []).length }}）</p>
          <ul class="mt-1.5 space-y-1">
            <li v-for="stg in delUsage?.strategies ?? []" :key="stg.id" class="flex items-center gap-2 text-xs text-fg">
              <FlaskConical :size="13" class="shrink-0 text-subtle" />{{ stg.name }}
            </li>
          </ul>
        </div>
      </template>

      <template v-else>
        <p v-if="delErr" class="rounded-lg border border-down/30 bg-down/10 px-3 py-2 text-xs text-down">{{ delErr }}</p>
        <p class="text-[13px] leading-relaxed text-muted">
          确定要把「{{ delFactor?.name }}」从因子库移除吗？已生成的研究记录会保留，但因子定义将从库中删除且不可恢复。检查确认：该因子当前未被任何保存模型或策略版本引用。
        </p>
      </template>

      <template #footer>
        <div v-if="delBlocked" class="flex justify-end">
          <button class="btn btn-primary" @click="delOpen = false">知道了</button>
        </div>
        <div v-else class="flex items-center justify-end gap-2">
          <button class="btn btn-ghost" :disabled="delBusy || delChecking" @click="delOpen = false">取消</button>
          <button class="btn btn-danger-ghost" :disabled="delBusy || delChecking || !!delErr" @click="confirmRemoveFactor">
            <LoaderCircle v-if="delBusy" :size="14" class="animate-spin" />{{ delBusy ? '删除中…' : '删除因子' }}
          </button>
        </div>
      </template>
    </Modal>

    <!-- 候选因子：右下角浮动按钮（可上下拖动）+ 候选池面板 -->
    <button
      class="fixed z-40 flex h-12 touch-none select-none items-center gap-2 rounded-full bg-[var(--color-primary-fill)] px-4 text-sm font-semibold text-[var(--color-on-primary)] shadow-pop transition-transform hover:brightness-105 active:scale-95"
      :style="fabBottomStyle()"
      :title="`候选因子：点击展开，按住上下拖动可避开分页${candOpen ? '（点击收起）' : ''}`"
      aria-label="候选因子（按住可拖动）"
      @pointerdown="onFabPointerDown"
      @pointermove="onFabPointerMove"
      @pointerup="endFabDrag($event)"
      @pointercancel="cancelFabDrag"
      @lostpointercapture="cancelFabDrag"
      @keydown.enter.prevent="candOpen = !candOpen"
      @keydown.space.prevent="candOpen = !candOpen"
    >
      <span class="relative">
        <ListChecks :size="19" />
        <span v-if="candList.length" class="absolute -right-2 -top-1.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-[var(--color-ai)] px-1 text-[9px] font-bold text-[var(--color-on-primary)]">
          {{ candList.length }}
        </span>
      </span>
      <span class="hidden sm:inline">{{ candOpen ? '收起' : '候选因子' }}</span>
    </button>

    <div
      v-if="candOpen"
      class="fixed z-40 flex w-[min(400px,calc(100vw-2.5rem))] flex-col overflow-hidden rounded-2xl border border-border bg-surface shadow-pop"
      :style="candPanelStyle()"
    >
      <header class="flex items-center gap-2 border-b border-border px-4 py-3">
        <ListChecks :size="15" class="text-primary" />
        <div class="min-w-0 flex-1">
          <p class="text-sm font-semibold text-fg">候选因子池</p>
          <p class="truncate text-[11px] text-subtle">勾选页面上的因子入池，或让 AI 按表现挑选；随后可送「模型训练」当特征</p>
        </div>
        <button class="btn btn-icon !h-7 !w-7 text-muted hover:bg-surface-2" title="收起" @click="candOpen = false"><X :size="14" /></button>
      </header>

      <div class="min-h-0 flex-1 overflow-y-auto px-3 py-2.5">
        <div v-if="!candList.length" class="flex flex-col items-center gap-1 px-3 py-6 text-center">
          <span class="text-lg text-subtle">▤</span>
          <p class="text-xs font-semibold text-fg">候选池还是空的</p>
          <p class="max-w-xs text-[11px] leading-relaxed text-subtle">页面上的因子卡片左端会出现勾选框，点选即加入；也可直接用「AI 选」让模型按实测表现挑选。</p>
        </div>

        <ul v-else class="space-y-1.5">
          <li
            v-for="f in candList"
            :key="f.code"
            class="flex items-center gap-2 rounded-lg border border-border bg-surface-2/40 px-2.5 py-2"
          >
            <span class="min-w-0 flex-1">
              <p class="truncate text-xs font-semibold text-fg">{{ f.name }}</p>
              <p class="truncate font-mono text-[10px] text-subtle">{{ f.is_user ? (f.expression ?? f.code) : f.code }}</p>
            </span>
            <span v-if="candMeasured(results[f.code])" class="num shrink-0 text-[10px] text-muted" :title="`RankIC ${results[f.code].rank_ic ?? '—'} · IC ${results[f.code].ic ?? '—'}`">
              R {{ results[f.code].rank_ic ?? '—' }}
            </span>
            <button
              class="btn btn-icon !h-6 !w-6 !rounded-md text-muted hover:bg-surface hover:text-danger"
              :title="`把「${f.name}」移出候选池`"
              @click="toggleCand(f.code)"
            >
              <X :size="12" />
            </button>
          </li>
        </ul>

        <div v-if="aiPicks.length" class="mt-2 rounded-lg border border-ai/30 bg-ai/5 px-2.5 py-2">
          <p class="flex items-center gap-1 text-[11px] font-semibold text-ai-ink">
            <Sparkles :size="11" />最近一次 AI 挑选理由
          </p>
          <ul class="mt-1.5 space-y-1">
            <li v-for="p in aiPicks" :key="p.code" class="text-[11px] leading-relaxed text-muted">
              <span class="font-semibold text-fg">{{ candMeta(p.code)?.name ?? p.code }}</span>
              <span v-if="p.reason">：{{ p.reason }}</span>
            </li>
          </ul>
        </div>
      </div>

      <footer class="space-y-2 border-t border-border px-3 py-2.5">
        <p v-if="aiBusy && aiPhase" class="flex items-center gap-1.5 text-[11px] text-subtle">
          <LoaderCircle :size="12" class="animate-spin" />{{ aiPhase }}
        </p>
        <div class="flex items-center gap-2">
          <button class="btn btn-soft flex-1 !h-9 text-xs" :disabled="aiBusy" :title="candList.length >= 2 ? '基于候选池实测表现做 AI 挑选' : '暂无手动候选：将对整库前 20 个因子先实测再由 AI 挑选'" @click="aiSelect">
            <LoaderCircle v-if="aiBusy" :size="13" class="animate-spin" />
            <Sparkles v-else :size="13" />
            {{ aiBusy ? 'AI 分析中…' : 'AI 选' }}
          </button>
          <button class="btn btn-primary flex-1 !h-9 text-xs" :disabled="!candList.length" @click="sendToTrain">
            <Send :size="13" />发送至模型训练
          </button>
        </div>
        <div class="flex items-center justify-between text-[10px] text-subtle">
          <span v-if="candList.length">已选 {{ candList.length }} 个 / 上限 12</span>
          <span v-else>AI 选可自动补全候选</span>
          <button v-if="candList.length" class="link !text-[10px]" @click="clearCand">清空候选</button>
        </div>
      </footer>
    </div>
  </div>
</template>
