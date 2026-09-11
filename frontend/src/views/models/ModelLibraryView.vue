<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Activity, ArrowRight, Boxes, FlaskConical, LoaderCircle, RefreshCw, Trash2, Wallet } from '@lucide/vue'
import PageHeader from '@/components/ui/PageHeader.vue'
import HelpDrawer, { type PageHelp } from '@/components/ui/HelpDrawer.vue'
import Panel from '@/components/ui/Panel.vue'
import StateView from '@/components/ui/StateView.vue'
import Modal from '@/components/ui/Modal.vue'
import Tip from '@/components/ui/Tip.vue'
import EquityChart from '@/components/chart/EquityChart.vue'
import { useToastStore } from '@/stores/toast'
import { deleteMlModel, getMlUsage, listMlModels, scoreMlModel, type MlModelRow, type MlScore, type MlUsage } from '@/api/factor'
import { createStrategy, createVersion, runBacktest } from '@/api/strategy'
import { listAccounts, createAutoAccount, getAccount, fetchOverview, type SimAccount, type SimDetail, type SimOverviewRow } from '@/api/sim'
import { INTERVALS, MAJOR_SYMBOLS, timeAgo } from '@/utils/format'

const pageHelp: PageHelp = {
  title: '模型库',
  intro: '训练落库的 ML 模型都集中在这里：可打分、一键建策略回测、部署模拟盘并查看绩效。',
  terms: [
    { term: '打分', desc: 'Ridge 用系数 + 最新 K 线标准化因子线性打分；LightGBM 用已保存的树模型对标准化特征打分。均不含未来。' },
    { term: '建策略回测', desc: '以模型打分作择时信号（滚动分位），走标准向量化回测通道，可对比买入持有基准。' },
    { term: '模拟盘绩效', desc: '把模型部署为「策略自动」账户，按最新打分自动开/平仓；这里能看到账户净值曲线与累计收益。' },
  ],
  tips: ['Ridge 仅存线性权重；LightGBM 额外序列化模型文件（MODEL_STORAGE_DIR），打分/回测/模拟盘都走树模型。', '回测前确认本地有对应标的/粒度的 K 线数据。'],
}

const router = useRouter()
const toast = useToastStore()
const models = ref<MlModelRow[]>([])
const loading = ref(true)
const error = ref<string | null>(null)

const scoreBusy = ref<string | null>(null)

async function load() {
  loading.value = true
  error.value = null
  try {
    models.value = await listMlModels()
  } catch (e: unknown) {
    error.value = (e as { message?: string }).message ?? '模型库加载失败'
    toast.error('模型库加载失败', error.value)
  } finally {
    loading.value = false
  }
}

async function scoreModel(m: MlModelRow) {
  scoreBusy.value = m.id
  const days = m.interval.endsWith('d') ? 30 : m.interval.endsWith('h') ? 14 : 3
  const end = new Date()
  const start = new Date(end.getTime() - days * 24 * 3600 * 1000)
  try {
    const sym = m.symbols?.[0] ?? 'BTCUSDT'
    const res: MlScore = await scoreMlModel(m.id, { symbol: sym, start: start.toISOString(), end: end.toISOString() })
    if (!res.found) {
      toast.error('无法打分', res.message ?? '所选区间无可打分样本')
      return
    }
    const note = res.train_window_excluded_to ? `已样本排除训练期（自 ${fmtDay(res.train_window_excluded_to)} 起评估）` : ''
    const aggNote = aggInfoText(m) ? `细粒度增强（${aggInfoText(m)}）` : ''
    toast.info(
      '打分完成',
      `${sym} 最近 ${res.n} bar · 最新分 ${res.last_score ?? '—'}${note ? '；' + note : ''}${aggNote ? '；' + aggNote : ''}`,
    )
  } catch (e: unknown) {
    toast.error('打分失败', (e as { message?: string }).message)
  } finally {
    scoreBusy.value = null
  }
}

// —— 特征因子清单 ——
const featOpen = ref(false)
const featModel = ref<MlModelRow | null>(null)

function openFeats(m: MlModelRow) {
  featModel.value = m
  featOpen.value = true
}

function fmtCoef(v: number): string {
  const s = v >= 0 ? '+' : ''
  return `${s}${v.toFixed(5)}`
}

// —— 删除模型 ——
const delOpen = ref(false)
const delChecking = ref(false)
const delBusy = ref(false)
const delModel = ref<MlModelRow | null>(null)
const delUsage = ref<MlUsage | null>(null)
const delErr = ref('')

const delBlocked = computed(() => {
  const u = delUsage.value
  return !!u && (u.strategies.length > 0 || u.sim_accounts.length > 0)
})

async function requestDelete(m: MlModelRow) {
  delModel.value = m
  delUsage.value = null
  delErr.value = ''
  delChecking.value = true
  delOpen.value = true
  try {
    delUsage.value = await getMlUsage(m.id)
  } catch (e: unknown) {
    delErr.value = (e as { message?: string }).message ?? '检查引用失败'
  } finally {
    delChecking.value = false
  }
}

async function confirmDelete() {
  const m = delModel.value
  if (!m || delBusy.value || delBlocked.value) return
  delBusy.value = true
  delErr.value = ''
  try {
    await deleteMlModel(m.id)
    delOpen.value = false
    toast.success('模型已删除', m.name)
    await load()
  } catch (e: unknown) {
    const payload = e as { code?: string; message?: string; detail?: MlUsage }
    if (payload.code === 'CONFLICT' && payload.detail) {
      delUsage.value = payload.detail
    } else {
      delErr.value = payload.message ?? '删除失败'
    }
  } finally {
    delBusy.value = false
  }
}

// —— 回测 ——
const btOpen = ref(false)
const btModel = ref<MlModelRow | null>(null)
const btBusy = ref(false)
const btCfg = ref({ symbol: 'BTCUSDT', days: 300, window: 60, enterQ: 0.8, exitQ: 0.2 })

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
  if (btExcluded.value?.blocked) {
    toast.error('暂无样本外数据', '该标的训练至最新，训练结束后没有可用于回测的数据')
    return
  }
  btBusy.value = true
  const end = new Date()
  const start = new Date(end.getTime() - btCfg.value.days * 24 * 3600 * 1000)
  const features = (m.features ?? []).map((f) => ({
    code: f.code,
    coef: f.coef,
    mean: f.mean,
    std: f.std,
    expression: f.expression,
  }))
  const params: Record<string, unknown> = {
    template_id: 'ml_score',
    model_id: m.id,
    model_type: m.model_type,
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
    toast.success('策略已创建并开始回测', '进入策略详情查看指标卡 / 资金曲线')
    btOpen.value = false
    router.push(`/strategy/${str.strategy_id}`)
  } catch (e: unknown) {
    toast.error('创建策略失败', (e as { message?: string }).message)
  } finally {
    btBusy.value = false
  }
}

// —— 模拟盘 ——
const paperOpen = ref(false)
const paperModel = ref<MlModelRow | null>(null)
const paperAccounts = ref<Array<SimAccount & { last_equity?: number | null; orders?: number }>>([])
const paperLoading = ref(false)
const paperDetail = ref<{ id: string; detail?: SimDetail; busy: boolean } | null>(null)
const paperDetailId = ref<string | null>(null)

const newPaper = ref({ symbol: 'BTCUSDT', interval: '1d', window: 60, enter_q: 0.8, exit_q: 0.2 })

async function openPaper(m: MlModelRow) {
  paperModel.value = m
  newPaper.value = { symbol: m.symbols?.[0] ?? 'BTCUSDT', interval: m.interval, window: 60, enter_q: 0.8, exit_q: 0.2 }
  paperDetail.value = null
  paperDetailId.value = null
  paperOpen.value = true
  await loadPaperAccounts()
}

async function loadPaperAccounts() {
  const m = paperModel.value
  if (!m) return
  paperLoading.value = true
  try {
    const [accts, ov] = await Promise.all([listAccounts(), fetchOverview()])
    const ovById = new Map(ov.map((r) => [r.id, r]))
    paperAccounts.value = accts
      .filter((a) => a.account_type === 'strategy' && (a.config?.model_id === m.id))
      .map((a) => {
        const o = ovById.get(a.id) as SimOverviewRow | undefined
        return { ...a, last_equity: o?.last_equity, orders: o?.orders }
      })
  } catch {
    paperAccounts.value = []
  } finally {
    paperLoading.value = false
  }
}

async function togglePaperDetail(id: string) {
  if (paperDetailId.value === id) {
    paperDetailId.value = null
    paperDetail.value = null
    return
  }
  paperDetailId.value = id
  paperDetail.value = { id, busy: true }
  try {
    const d = await getAccount(id)
    paperDetail.value = { id, detail: d, busy: false }
  } catch (e: unknown) {
    toast.error('读取账户失败', (e as { message?: string }).message)
    paperDetail.value = null
    paperDetailId.value = null
  }
}

const creatingPaper = ref(false)
async function submitPaper() {
  const m = paperModel.value
  if (!m || creatingPaper.value) return
  creatingPaper.value = true
  try {
    const acct = await createAutoAccount({
      name: `${m.name}-模拟盘`,
      symbol: newPaper.value.symbol.trim().toUpperCase(),
      interval: newPaper.value.interval,
      model_id: m.id,
      window: newPaper.value.window,
      enter_q: newPaper.value.enter_q,
      exit_q: newPaper.value.exit_q,
    })
    toast.success('模拟盘账户已创建', `${acct.name} · 到「模拟交易」或定时任务驱动自动决策`)
    await loadPaperAccounts()
  } catch (e: unknown) {
    toast.error('创建模拟盘失败', (e as { message?: string }).message)
  } finally {
    creatingPaper.value = false
  }
}

function pnlTone(v: number | null | undefined): string {
  if (v == null) return 'text-muted'
  return v > 0 ? 'text-up' : v < 0 ? 'text-down' : 'text-muted'
}

function fmtDay(v: string | null | undefined): string {
  if (!v) return '—'
  return String(v).slice(0, 10)
}

type TestWindow = { symbol: string; interval: string; start: string; end: string; bars: number }

function shortSyms(syms: string[] | undefined): string {
  const s = (syms ?? []).filter(Boolean)
  return s.length ? s.join('/') : '—'
}

/** 训练数据行文本：币种 · 粒度 · 起止 · 训练根数 */
function trainText(m: MlModelRow): string {
  const d = m.dataset
  const win =
    d?.start && d?.end
      ? `${fmtDay(d.start)} ~ ${fmtDay(d.end)}`
      : m.train_start
        ? `${fmtDay(m.train_start)} ~ ${fmtDay(m.train_end)}`
        : '区间未记录'
  return `${shortSyms(m.symbols ?? [])} · ${d?.interval ?? m.interval} · ${win} · ${m.n_train} 根`
}

/** 测试集行文本：币种 · 粒度 · 真实测试段起止 · 测试根数（样本外） */
function testText(m: MlModelRow): string {
  const tw = (m.dataset?.test_windows ?? []) as TestWindow[]
  if (tw.length) {
    const syms = [...new Set(tw.map((x) => x.symbol))]
    const start = tw.map((x) => x.start).sort()[0]
    const end = tw.map((x) => x.end).sort().at(-1)
    const bars = tw.reduce((a, b) => a + (b.bars || 0), 0)
    return `${shortSyms(syms)} · ${tw[0].interval} · ${fmtDay(start)} ~ ${fmtDay(end)} · ${bars} 根`
  }
  const ratio = m.dataset?.test_ratio
  const tail = ratio != null ? `末尾 ${(ratio * 100).toFixed(0)}%` : '测试段起始未记录'
  return `${shortSyms(m.symbols ?? [])} · ${m.dataset?.interval ?? m.interval} · ${tail} · ${m.n_test} 根`
}

/** 训练集行的解释：窗口含义 + 样本排除行为 */
function trainTipText(m: MlModelRow): string {
  const win =
    (m.dataset?.start && m.dataset?.end)
      ? `${fmtDay(m.dataset.start)} ~ ${fmtDay(m.dataset.end)}`
      : m.train_start
        ? `${fmtDay(m.train_start)} ~ ${fmtDay(m.train_end)}`
        : '未记录'
  return `保存模型时记录的样本窗口（币种 · 粒度 · ${win}）。模型拟合见过这段数据，因此在模型库对该训练标的做回测/打分时会自动样本排除：评估从训练窗口结束之后才开始，避免样本内虚高。`
}

/** 测试集行的解释：OOS 语义 + 各币测试段明细 */
function testTipText(m: MlModelRow): string {
  const tw = (m.dataset?.test_windows ?? []) as TestWindow[]
  const head =
    'OOS = Out-of-Sample（样本外）。测试集是训练时从末尾专门留出、不参与拟合的一段，OOS IC / RankIC / 命中 / 价差都在这段上测得，用来检验模型对未见数据的泛化能力。'
  if (!tw.length) return head + '（该模型较老，未记录每个币的测试段起止时间）'
  const lines = tw.map((x) => `${x.symbol} · ${x.interval} · ${fmtDay(x.start)} ~ ${fmtDay(x.end)} · ${x.bars} 根`)
  return head + '\n' + lines.join('\n')
}

const btTrained = computed(() => {
  const m = btModel.value
  const sym = btCfg.value.symbol.trim().toUpperCase()
  return !!m && (m.symbols ?? []).includes(sym)
})

const btTrainEnd = computed<Date | null>(() => {
  const v = btModel.value?.train_end
  if (!v) return null
  const d = new Date(v)
  return Number.isNaN(d.getTime()) ? null : d
})

const btExcluded = computed(() => {
  const te = btTrainEnd.value
  if (!btTrained.value || !te) return null
  const end = new Date()
  const start = new Date(end.getTime() - (btCfg.value.days || 0) * 24 * 3600 * 1000)
  if (te >= end) return { blocked: true, remainingDays: 0 }
  const effStart = te > start ? te : start
  const remainingDays = Math.max(0, Math.round((end.getTime() - effStart.getTime()) / 86400000))
  return { blocked: false, remainingDays }
})

function pnlText(a: SimAccount & { last_equity?: number | null }): string {
  const eq = a.last_equity
  if (eq == null) return '—'
  const pct = ((eq - a.initial_cash) / a.initial_cash) * 100
  return `${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%`
}

function equityPointsOf(d: SimDetail | undefined) {
  if (!d) return []
  return (d.equity ?? []).map((p) => ({ ts: new Date(p.ts).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }), value: p.equity }))
}

function modelMetric(m: MlModelRow, key: 'oos_ic' | 'oos_rank_ic' | 'hit_rate' | 'spread'): string {
  const v = m.metrics?.[key]
  if (v == null) return '—'
  if (key === 'hit_rate') return `${(v * 100).toFixed(1)}%`
  return v.toFixed(key === 'spread' ? 5 : 4)
}

const AGG_LABELS: Record<string, string> = {
  'agg:rv': '已实现波动率',
  'agg:vol_imb': '量能不平衡',
  'agg:taker_ratio': '主动买占比',
}
function aggInfoText(m: MlModelRow): string {
  const fine = m.dataset?.fine_interval
  if (!fine) return ''
  const codes = m.dataset?.agg_features ?? []
  const labels = codes.map((c) => AGG_LABELS[c] ?? c.replace('agg:', '')).filter(Boolean)
  return `${fine} → ${labels.length ? labels.join(' / ') : '—'}`
}

onMounted(load)
</script>

<template>
  <div class="flex flex-col gap-6">
    <PageHeader
      eyebrow="ML Lab"
      title="模型库"
      desc="训练落库的模型都在这：打分、一键建策略回测、部署模拟盘并查看绩效。"
    >
      <template #actions>
        <HelpDrawer :help="pageHelp" />
        <button class="btn btn-ghost" @click="load"><RefreshCw :size="15" />刷新</button>
        <router-link to="/train" class="btn btn-primary"><Activity :size="15" />去训练</router-link>
      </template>
    </PageHeader>

    <Panel v-if="error" :pad="'none'">
      <StateView icon="error" :title="error" desc="后端模型接口未就绪，请确认已重启网关。">
        <template #action><button class="btn btn-primary" @click="load"><RefreshCw :size="15" />重试</button></template>
      </StateView>
    </Panel>

    <template v-else>
      <div v-if="loading" class="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
        <div v-for="i in 6" :key="i" class="panel h-36 animate-pulse" />
      </div>

      <StateView v-else-if="!models.length" icon="empty" title="还没有模型" desc="到「模型训练」跑出结果并保存后，模型会出现在这里。">
        <template #action><router-link to="/train" class="btn btn-primary">去训练模型</router-link></template>
      </StateView>

      <div v-else class="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
        <article v-for="m in models" :key="m.id" class="group panel flex h-[320px] flex-col overflow-hidden p-5">
          <div class="flex items-center gap-2">
            <div class="min-w-0 flex-1">
              <h3 class="truncate text-[15px] font-semibold text-fg">{{ m.name }}</h3>
              <p class="mt-0.5 truncate font-mono text-[11px] text-subtle">{{ m.model_type }} · {{ m.interval }} · 预测 {{ m.horizon }}h · {{ timeAgo(m.created_at) }}</p>
            </div>
            <span class="badge tone-primary shrink-0">{{ m.model_type === 'gbdt' ? 'LightGBM' : 'Ridge' }}</span>
            <button
              class="rounded-md p-1 text-subtle opacity-0 transition-colors hover:bg-danger/10 hover:text-danger focus-visible:opacity-100 group-hover:opacity-100"
              :aria-label="`删除模型 ${m.name}`"
              @click="requestDelete(m)"
            >
              <Trash2 :size="13" />
            </button>
          </div>

          <div class="mt-4 grid grid-cols-4 gap-2">
            <div>
              <p class="whitespace-nowrap text-[10px] uppercase tracking-wider text-subtle">OOS IC <Tip text="OOS = Out-of-Sample（样本外）。IC：样本外测试段上模型预测值与实际未来收益的 Pearson 相关；绝对值越高预测越强，0 表示无预测力" /></p>
              <p class="num mt-1 text-sm font-semibold" :class="pnlTone(m.metrics?.oos_ic ?? 0)">{{ modelMetric(m, 'oos_ic') }}</p>
            </div>
            <div>
              <p class="whitespace-nowrap text-[10px] uppercase tracking-wider text-subtle">RankIC <Tip text="OOS = Out-of-Sample（样本外）。RankIC：样本外测试段上预测排序与实际收益排序的 Spearman 相关，衡量选股/择时排序能力，对离群值比 IC 更稳健" /></p>
              <p class="num mt-1 text-sm font-semibold" :class="pnlTone(m.metrics?.oos_rank_ic ?? 0)">{{ modelMetric(m, 'oos_rank_ic') }}</p>
            </div>
            <div>
              <p class="whitespace-nowrap text-[10px] uppercase tracking-wider text-subtle">命中 <Tip text="方向命中率：预测涨/跌方向与实际方向一致的比例；>50% 说明方向判断优于抛硬币" /></p>
              <p class="num mt-1 text-sm font-semibold text-fg">{{ modelMetric(m, 'hit_rate') }}</p>
            </div>
            <div>
              <p class="whitespace-nowrap text-[10px] uppercase tracking-wider text-subtle">价差 <Tip text="测试段把预测分最高的 20% 与最低的 20% 分组，两者实际平均收益之差；&gt;0 表示高分组确实跑赢低分组（多头信号有效）" /></p>
              <p class="num mt-1 text-sm font-semibold" :class="pnlTone(m.metrics?.spread ?? 0)">{{ modelMetric(m, 'spread') }}</p>
            </div>
          </div>

          <div class="mt-3 flex min-w-0 items-center gap-1.5">
            <button
              class="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-border bg-surface-2/60 px-2.5 py-1.5 text-[11px] font-medium text-muted transition-colors hover:border-primary/40 hover:text-fg"
              :aria-label="`查看 ${(m.features ?? []).length} 个特征因子`"
              @click="openFeats(m)"
            >
              <Boxes :size="12" class="text-subtle" />使用 {{ (m.features ?? []).length }} 个因子
            </button>
            <span
              v-if="aggInfoText(m)"
              class="min-w-0 flex-1 truncate rounded-md border border-ai/30 bg-ai/5 px-2 py-1 text-[10px] text-ai-ink"
              :title="aggInfoText(m)"
            >
              细粒度增强 · <span class="font-mono">{{ aggInfoText(m) }}</span>
            </span>
          </div>

          <div class="flex-1" />

          <div class="min-w-0">
            <div class="flex items-center gap-1.5">
              <span class="text-[10px] font-semibold uppercase tracking-wider text-subtle">训练</span>
              <Tip :text="trainTipText(m)" class="shrink-0 self-center" />
            </div>
            <p class="mt-1 truncate font-mono text-[11px] text-subtle">{{ trainText(m) }}</p>
          </div>
          <div class="mt-2 min-w-0">
            <div class="flex items-center gap-1.5">
              <span class="text-[10px] font-semibold uppercase tracking-wider text-subtle">测试</span>
              <Tip :text="testTipText(m)" class="shrink-0 self-center" />
            </div>
            <p class="mt-1 truncate font-mono text-[11px] text-subtle">{{ testText(m) }}</p>
          </div>

          <div class="mt-3 flex items-center gap-2 border-t border-border pt-3">
            <button class="btn btn-primary !h-8 flex-1 text-xs" @click="openBt(m)"><FlaskConical :size="13" />回测</button>
            <button class="btn btn-ghost !h-8 flex-1 text-xs" @click="openPaper(m)"><Wallet :size="13" />模拟盘</button>
            <button class="btn btn-ghost !h-8 text-xs" :disabled="scoreBusy === m.id" @click="scoreModel(m)">
              <LoaderCircle v-if="scoreBusy === m.id" :size="13" class="animate-spin" />打分
            </button>
          </div>
        </article>
      </div>
    </template>

    <!-- 回测弹窗 -->
    <Modal :open="btOpen" :title="`用模型建策略回测 · ${btModel?.name ?? ''}`" sub="以该模型打分作择时信号，走标准回测通道（含 SSE 实时进度）" width="520px" @close="btOpen = false">
      <div class="space-y-4">
        <div class="grid grid-cols-2 gap-4">
          <div class="field">
            <label class="label" for="mlbt-sym">交易对</label>
            <input id="mlbt-sym" v-model="btCfg.symbol" list="mlbt-syms" class="input font-mono uppercase" spellcheck="false" />
            <datalist id="mlbt-syms"><option v-for="s in MAJOR_SYMBOLS" :key="s" :value="s" /></datalist>
          </div>
          <div class="field">
            <label class="label">颗粒度</label>
            <div class="flex h-[34px] items-center rounded-[9px] border border-border bg-surface px-3 font-mono text-sm text-fg">{{ btModel?.interval }}</div>
          </div>
        </div>
        <div class="grid grid-cols-3 gap-3">
          <div class="field">
            <label class="label" for="mlbt-days">回看天数</label>
            <input id="mlbt-days" v-model.number="btCfg.days" type="number" min="30" class="input" />
          </div>
          <div class="field">
            <label class="label" for="mlbt-window">滚动窗口</label>
            <input id="mlbt-window" v-model.number="btCfg.window" type="number" min="20" class="input" />
          </div>
          <div class="field">
            <label class="label">q 入 / 离场分位</label>
            <div class="flex h-[34px] items-center gap-1 rounded-[9px] border border-border bg-surface px-2 font-mono text-xs text-muted">
              <input v-model.number="btCfg.enterQ" type="number" step="0.05" min="0.5" max="0.99" class="w-full bg-transparent outline-none" />
              <span>/</span>
              <input v-model.number="btCfg.exitQ" type="number" step="0.05" min="0.01" max="0.5" class="w-full bg-transparent outline-none" />
            </div>
          </div>
        </div>
        <p class="text-xs leading-relaxed text-subtle">
          打分高于滚动 {{ btCfg.enterQ }} 分位 → 满仓做多；低于 {{ btCfg.exitQ }} 分位 → 清仓。只用 ≤t 信息，无未来函数。可与「买入持有」基准对比。
        </p>
        <p v-if="btExcluded?.blocked" class="rounded-lg border border-danger/30 bg-danger/10 px-2.5 py-2 text-[11px] leading-relaxed text-danger">
          该标的训练至 {{ fmtDay(btModel?.train_end) }}，训练结束后已无样本外数据可回测。请：① 等新数据到账；② 换一个未参与训练的标的；③ 缩短训练期后重新保存模型。
        </p>
        <p v-else-if="btExcluded" class="rounded-lg border border-warn/30 bg-warn/10 px-2.5 py-2 text-[11px] leading-relaxed text-warn">
          该标的参与过训练（至 {{ fmtDay(btModel?.train_end) }}）：样本排除后实际回测窗口约 {{ btExcluded.remainingDays }} 天。若过短，请增大「回看天数」或缩短训练期后重新保存。
        </p>
      </div>
      <template #footer>
        <div class="flex items-center justify-end gap-2">
          <button class="btn btn-ghost" @click="btOpen = false">取消</button>
          <button class="btn btn-primary" :disabled="btBusy || !btCfg.symbol.trim() || btExcluded?.blocked" @click="runBt">
            <LoaderCircle v-if="btBusy" :size="15" class="animate-spin" />{{ btBusy ? '创建并回测中…' : '创建策略并回测' }}
          </button>
        </div>
      </template>
    </Modal>

    <!-- 模拟盘弹窗 -->
    <Modal :open="paperOpen" :title="`模拟盘绩效 · ${paperModel?.name ?? ''}`" sub="部署为「策略自动」账户后，按模型最新打分自动开/平仓；这里查看净值与收益" width="720px" @close="paperOpen = false">
      <div class="space-y-4">
        <!-- 新建 -->
        <div class="rounded-xl border border-border bg-surface-2/40 p-3.5">
          <p class="mb-2.5 text-xs font-semibold uppercase tracking-wider text-subtle">部署新模拟盘账户</p>
          <div class="grid grid-cols-2 gap-3">
            <div class="field">
              <label class="label" for="pp-sym">交易对</label>
              <input id="pp-sym" v-model="newPaper.symbol" class="input font-mono uppercase" spellcheck="false" />
            </div>
            <div class="field">
              <label class="label" for="pp-int">粒度</label>
              <select id="pp-int" v-model="newPaper.interval" class="select font-mono">
                <option v-for="i in INTERVALS.filter((x) => ['15m', '30m', '1h', '4h', '1d'].includes(x))" :key="i" :value="i">{{ i }}</option>
              </select>
            </div>
            <div class="field">
              <label class="label" for="pp-window">滚动窗口</label>
              <input id="pp-window" v-model.number="newPaper.window" type="number" step="1" min="20" max="240" class="input font-mono" />
            </div>
            <div class="field">
              <label class="label" for="pp-enter-q">入场分位</label>
              <input id="pp-enter-q" v-model.number="newPaper.enter_q" type="number" step="0.05" min="0.5" max="0.95" class="input font-mono" />
            </div>
            <div class="field">
              <label class="label" for="pp-exit-q">离场分位</label>
              <input id="pp-exit-q" v-model.number="newPaper.exit_q" type="number" step="0.05" min="0.05" max="0.5" class="input font-mono" />
            </div>
          </div>
          <button class="btn btn-primary mt-3 w-full" :disabled="creatingPaper" @click="submitPaper">
            <LoaderCircle v-if="creatingPaper" :size="14" class="animate-spin" />{{ creatingPaper ? '创建中…' : '创建账户' }}
          </button>
          <p class="mt-2 text-[11px] text-subtle">与回测同一套规则：score 高于滚动入场分位做多、低于离场分位平仓；由「定时任务」周期性驱动（也可在模拟交易页手动触发一次）。</p>
        </div>

        <!-- 已有账户绩效 -->
        <div>
          <p class="mb-2 text-xs font-semibold uppercase tracking-wider text-subtle">已部署账户（{{ paperAccounts.length }}）</p>
          <div v-if="paperLoading" class="py-6"><StateView busy title="读取账户…" /></div>
          <StateView v-else-if="!paperAccounts.length" icon="empty" title="尚未部署" desc="该模型还没有模拟盘账户，用上方表单部署一个。" />
          <div v-else class="space-y-2">
            <div v-for="a in paperAccounts" :key="a.id" class="rounded-xl border border-border bg-surface-2/40">
              <div class="flex flex-wrap items-center gap-3 px-3.5 py-2.5">
                <div class="min-w-0 flex-1">
                  <p class="text-sm font-semibold text-fg">{{ a.name }}</p>
                  <p class="mt-0.5 text-[11px] text-subtle">{{ a.config?.symbol }} · {{ a.config?.interval }} · 入 {{ a.config?.enter }} / 出 {{ a.config?.exit }}</p>
                </div>
                <div class="text-right">
                  <p class="num text-sm font-semibold" :class="pnlTone((a.last_equity ?? 0) - a.initial_cash)">{{ pnlText(a) }}</p>
                  <p class="text-[10px] text-subtle">最新净值 {{ a.last_equity != null ? a.last_equity.toLocaleString('en-US', { maximumFractionDigits: 2 }) : '—' }}</p>
                </div>
                <button class="btn btn-ghost !h-8 text-xs" @click="togglePaperDetail(a.id)">
                  {{ paperDetailId === a.id ? '收起' : '净值曲线' }}
                </button>
              </div>
              <div v-if="paperDetailId === a.id && paperDetail?.id === a.id" class="border-t border-border px-3.5 py-3">
                <div v-if="paperDetail.busy" class="py-4"><StateView busy title="读取净值…" /></div>
                <template v-else-if="paperDetail.detail">
                  <EquityChart v-if="equityPointsOf(paperDetail.detail).length" :points="equityPointsOf(paperDetail.detail)" :baseline="a.initial_cash" :height="180" />
                  <p v-else class="text-xs text-subtle">暂无净值记录。</p>
                  <p class="mt-1.5 text-[11px] text-subtle">
                    总权益 ${{ paperDetail.detail.total_equity.toLocaleString('en-US', { maximumFractionDigits: 2 }) }} · 持仓 {{ paperDetail.detail.positions.length }} · 成交 {{ paperDetail.detail.orders.length }} 笔
                  </p>
                </template>
              </div>
            </div>
          </div>
        </div>

        <div class="flex items-center gap-2">
          <router-link to="/sim" class="btn btn-ghost !h-8 text-xs">去模拟交易页<ArrowRight :size="13" /></router-link>
        </div>
      </div>
    </Modal>

    <!-- 特征因子清单弹窗 -->
    <Modal
      :open="featOpen"
      :title="`特征因子 · ${featModel?.name ?? ''}`"
      :sub="`${(featModel?.features ?? []).length} 个输入因子 · ${featModel?.model_type === 'gbdt' ? 'LightGBM' : 'Ridge'} · ${featModel?.interval}，右列为模型对该因子的 ${featModel?.model_type === 'gbdt' ? '重要性' : '线性系数'}（特征已标准化）`"
      width="600px"
      @close="featOpen = false"
    >
      <div v-if="!(featModel?.features ?? []).length" class="py-10 text-center text-xs text-subtle">该模型没有记录特征。</div>
      <ul v-else class="divide-y divide-border">
        <li v-for="f in featModel?.features ?? []" :key="f.code" class="flex items-center gap-3 py-2.5">
          <div class="min-w-0 flex-1">
            <p class="truncate text-[13px] font-medium text-fg">{{ f.name || f.code }}</p>
            <p class="mt-0.5 truncate font-mono text-[10px] text-subtle">{{ f.expression ? `表达式：${f.expression}` : f.code }}</p>
          </div>
          <div class="shrink-0 text-right">
            <p class="num text-xs font-semibold" :class="pnlTone(f.coef)">{{ fmtCoef(f.coef) }}</p>
            <p class="text-[9px] uppercase tracking-wider text-subtle">{{ featModel?.model_type === 'gbdt' ? 'importance' : 'coef' }}</p>
          </div>
        </li>
      </ul>
    </Modal>

    <!-- 删除模型确认 / 被引用警告 -->
    <Modal
      :open="delOpen"
      :title="delBlocked ? `无法删除 · ${delModel?.name ?? ''}` : `删除模型 · ${delModel?.name ?? ''}`"
      :sub="delBlocked
        ? '该模型正被策略 / 模拟盘账户引用'
        : `${delModel?.model_type === 'gbdt' ? 'LightGBM（含序列化模型文件）' : 'Ridge'} · 删除后不可恢复`"
      width="520px"
      @close="delOpen = false"
    >
      <div v-if="delChecking" class="py-10"><StateView busy title="检查引用…" /></div>

      <template v-else-if="delBlocked">
        <div class="rounded-lg border border-danger/30 bg-danger/10 px-3 py-2.5 text-xs leading-relaxed text-down">
          该模型正在为下面的策略 / 模拟盘提供打分信号，删除会让它们失效，因此暂时不能删除。请先删除引用它的策略版本或解绑模拟盘账户，再回来删除模型。
        </div>

        <div v-if="(delUsage?.strategies ?? []).length" class="mt-4">
          <p class="text-[10px] font-semibold uppercase tracking-wider text-subtle">引用它的策略</p>
          <ul class="mt-1.5 space-y-1">
            <li v-for="s in delUsage?.strategies ?? []" :key="s.id" class="flex items-center gap-2 text-xs text-fg">
              <FlaskConical :size="13" class="shrink-0 text-subtle" />{{ s.name }}
            </li>
          </ul>
        </div>

        <div v-if="(delUsage?.sim_accounts ?? []).length" class="mt-4">
          <p class="text-[10px] font-semibold uppercase tracking-wider text-subtle">引用它的模拟盘账户</p>
          <ul class="mt-1.5 space-y-1">
            <li v-for="a in delUsage?.sim_accounts ?? []" :key="a.id" class="flex items-center gap-2 text-xs text-fg">
              <Wallet :size="13" class="shrink-0 text-subtle" />{{ a.name }}
            </li>
          </ul>
        </div>
      </template>

      <template v-else>
        <p v-if="delErr" class="rounded-lg border border-down/30 bg-down/10 px-3 py-2 text-xs text-down">{{ delErr }}</p>
        <p class="text-[13px] leading-relaxed text-muted">
          确定要删除模型“{{ delModel?.name }}”吗？模型记录{{ delModel?.model_type === 'gbdt' ? '及磁盘上的序列化模型文件' : '' }}将一并移除，且不可恢复。检查确认：该模型当前未被任何策略或模拟盘引用。
        </p>
      </template>

      <template #footer>
        <div v-if="delBlocked" class="flex justify-end">
          <button class="btn btn-primary" @click="delOpen = false">知道了</button>
        </div>
        <div v-else class="flex items-center justify-end gap-2">
          <button class="btn btn-ghost" :disabled="delBusy || delChecking" @click="delOpen = false">取消</button>
          <button class="btn btn-danger-ghost" :disabled="delBusy || delChecking || !!delErr" @click="confirmDelete">
            <LoaderCircle v-if="delBusy" :size="14" class="animate-spin" />{{ delBusy ? '删除中…' : '删除模型' }}
          </button>
        </div>
      </template>
    </Modal>
  </div>
</template>
