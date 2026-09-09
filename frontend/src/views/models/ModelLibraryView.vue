<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Activity, ArrowRight, FlaskConical, LoaderCircle, RefreshCw, Wallet } from '@lucide/vue'
import PageHeader from '@/components/ui/PageHeader.vue'
import HelpDrawer, { type PageHelp } from '@/components/ui/HelpDrawer.vue'
import Panel from '@/components/ui/Panel.vue'
import StateView from '@/components/ui/StateView.vue'
import Modal from '@/components/ui/Modal.vue'
import EquityChart from '@/components/chart/EquityChart.vue'
import { useToastStore } from '@/stores/toast'
import { listMlModels, scoreMlModel, type MlModelRow, type MlScore } from '@/api/factor'
import { createStrategy, createVersion, runBacktest } from '@/api/strategy'
import { listAccounts, createAutoAccount, getAccount, fetchOverview, type SimAccount, type SimDetail, type SimOverviewRow } from '@/api/sim'
import { INTERVALS, MAJOR_SYMBOLS, timeAgo } from '@/utils/format'

const pageHelp: PageHelp = {
  title: '模型库',
  intro: '训练落库的 Ridge 模型都集中在这里：可打分、一键建策略回测、部署模拟盘并查看绩效。',
  terms: [
    { term: '打分', desc: '用模型系数 + 最新 K 线标准化因子，输出最新一 bar 的择时分数（线性加权，不含未来）。' },
    { term: '建策略回测', desc: '以模型打分作择时信号（滚动分位），走标准向量化回测通道，可对比买入持有基准。' },
    { term: '模拟盘绩效', desc: '把模型部署为「策略自动」账户，按最新打分自动开/平仓；这里能看到账户净值曲线与累计收益。' },
  ],
  tips: ['LightGBM 模型暂不可保存打分/回测，仅 Ridge 落库。', '回测前确认本地有对应标的/粒度的 K 线数据。'],
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
    toast.info('打分完成', `${sym} 最近 ${res.n} bar · 最新分 ${res.last_score ?? '—'}`)
  } catch (e: unknown) {
    toast.error('打分失败', (e as { message?: string }).message)
  } finally {
    scoreBusy.value = null
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

const newPaper = ref({ symbol: 'BTCUSDT', interval: '1d', enter: 0.02, exit: -0.02 })

async function openPaper(m: MlModelRow) {
  paperModel.value = m
  newPaper.value = { symbol: m.symbols?.[0] ?? 'BTCUSDT', interval: m.interval, enter: 0.02, exit: -0.02 }
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
      enter: newPaper.value.enter,
      exit: newPaper.value.exit,
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
        <article v-for="m in models" :key="m.id" class="panel flex flex-col p-5">
          <div class="flex items-center gap-2">
            <div class="min-w-0 flex-1">
              <h3 class="truncate text-[15px] font-semibold text-fg">{{ m.name }}</h3>
              <p class="font-mono text-[11px] text-subtle">{{ m.model_type }} · {{ m.interval }} · 预测 {{ m.horizon }}h · {{ timeAgo(m.created_at) }}</p>
            </div>
            <span class="badge tone-primary">Ridge</span>
          </div>

          <div class="mt-4 grid grid-cols-4 gap-2">
            <div>
              <p class="text-[10px] uppercase tracking-wider text-subtle">OOS IC</p>
              <p class="num mt-1 text-sm font-semibold" :class="pnlTone(m.metrics?.oos_ic ?? 0)">{{ modelMetric(m, 'oos_ic') }}</p>
            </div>
            <div>
              <p class="text-[10px] uppercase tracking-wider text-subtle">RankIC</p>
              <p class="num mt-1 text-sm font-semibold" :class="pnlTone(m.metrics?.oos_rank_ic ?? 0)">{{ modelMetric(m, 'oos_rank_ic') }}</p>
            </div>
            <div>
              <p class="text-[10px] uppercase tracking-wider text-subtle">命中</p>
              <p class="num mt-1 text-sm font-semibold text-fg">{{ modelMetric(m, 'hit_rate') }}</p>
            </div>
            <div>
              <p class="text-[10px] uppercase tracking-wider text-subtle">价差</p>
              <p class="num mt-1 text-sm font-semibold" :class="pnlTone(m.metrics?.spread ?? 0)">{{ modelMetric(m, 'spread') }}</p>
            </div>
          </div>

          <div class="mt-3 flex flex-wrap gap-1.5">
            <span v-for="f in (m.features ?? []).slice(0, 5)" :key="f.code" class="badge tone-neutral">{{ f.code }}</span>
            <span v-if="(m.features ?? []).length > 5" class="badge tone-neutral">+{{ m.features.length - 5 }}</span>
          </div>

          <p class="mt-3 text-[11px] text-subtle">{{ (m.symbols ?? []).join(', ') }} · train {{ m.n_train }} / test {{ m.n_test }}</p>

          <div class="mt-4 flex items-center gap-2 border-t border-border pt-3">
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
              <label class="label" for="pp-enter">入场分阈值</label>
              <input id="pp-enter" v-model.number="newPaper.enter" type="number" step="0.01" class="input font-mono" />
            </div>
            <div class="field">
              <label class="label" for="pp-exit">离场分阈值</label>
              <input id="pp-exit" v-model.number="newPaper.exit" type="number" step="0.01" class="input font-mono" />
            </div>
          </div>
          <button class="btn btn-primary mt-3 w-full" :disabled="creatingPaper" @click="submitPaper">
            <LoaderCircle v-if="creatingPaper" :size="14" class="animate-spin" />{{ creatingPaper ? '创建中…' : '创建账户' }}
          </button>
          <p class="mt-2 text-[11px] text-subtle">得分 &gt; 入场阈值做多、&lt; 离场阈值平仓；由「定时任务」周期性驱动（也可在模拟交易页手动触发一次）。</p>
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
  </div>
</template>
