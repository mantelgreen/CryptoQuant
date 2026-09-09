<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Boxes, FlaskConical, Info, LoaderCircle, RefreshCw, Search, Sparkles } from '@lucide/vue'
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
  removeUserFactor,
  type FactorMeta,
  type FactorResearch,
  type FactorRun,
  type FactorMatrix,
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
            <button class="btn btn-ghost !h-8 !px-2.5 text-xs" @click="openHistory(f)">历史</button>
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
  </div>
</template>
