<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Check, Cpu, LoaderCircle, RefreshCw, Sparkles, X } from '@lucide/vue'
import PageHeader from '@/components/ui/PageHeader.vue'
import HelpDrawer, { type PageHelp } from '@/components/ui/HelpDrawer.vue'
import Panel from '@/components/ui/Panel.vue'
import StateView from '@/components/ui/StateView.vue'
import FactorPicker from '@/components/ui/FactorPicker.vue'
import Tip from '@/components/ui/Tip.vue'
import TrainCharts from './TrainCharts.vue'
import { useToastStore } from '@/stores/toast'
import {
  listFactors,
  trainMl,
  saveMl,
  type FactorMeta,
  type MlTrainResult,
  type MlTrainArgs,
} from '@/api/factor'
import { reviewTraining, type TrainReviewResult, type TrainReviewPayload } from '@/api/agent'
import { useTrainHandoffStore } from '@/stores/trainHandoff'
import { MAJOR_SYMBOLS } from '@/utils/format'

const pageHelp: PageHelp = {
  title: '模型训练（ML 工坊）',
  intro: '把因子研究升级成“可回测的机器学习信号”：配置 → 训练 → 体检 → 落库 → 一键转策略。',
  terms: [
    { term: '时序切分', desc: '按时间先后切 train/test（训练段在后端前加 purge 缓冲），禁止随机洗牌，避免未来信息泄漏。' },
    { term: 'train IC vs OOS IC', desc: '训练集内拟合的 IC 若远高于样本外，说明在记忆噪声；两者差距=过拟合风险。' },
    { term: '相关性过滤', desc: '训练集内两两 |相关| 超阈值即剔除，防止组合里信息冗余。' },
    { term: '分层净值 / 十分位（诊断图）', desc: '样本外按预测分等分（十分位柱状）与五等分（分层净值曲线），看平均实际收益是否单调、高分组净值是否跑赢低分组——比单一 IC 更直观地判断“预测真能分层”。' },
    { term: 'LightGBM 训练曲线', desc: '每 boosting 轮记录 train/OOS 的 MSE。OOS 先降后升（或与 train 拉开差距）的位置即过拟合拐点；仅为展示，不影响训练与最终预测。' },
    { term: 'Top−Bottom 价差', desc: '把测试期按预测分高低各取 20%，两组实际收益均值差；>0 表示高分组确实跑赢。' },
    { term: '学习率 / 树参数（LightGBM）', desc: 'learning_rate 决定每棵树的贡献缩减；n_estimators 是树数量；max_depth/num_leaves 控制单棵树复杂度；subsample/colsample 是行/列采样，都可防过拟合。' },
  ],
  tips: ['默认阈值较保守；样本<100 行的结果谨慎采纳。', '保存后可在「模型库」一键“建策略回测”、部署模拟盘。', '小数据优先 Ridge；数据量大、非线性强再用 LightGBM。'],
}

const toast = useToastStore()
const handoff = useTrainHandoffStore()
const factors = ref<FactorMeta[]>([])
const codes = ref<string[]>([])
const running = ref(false)
const phase = ref('')
const result = ref<MlTrainResult | null>(null)
const args = ref<MlTrainArgs | null>(null)
const resInfo = computed(() => (result.value?.found ? result.value : null))

const TRAIN_INTERVALS = ['1m', '3m', '5m', '15m', '30m', '1h', '2h', '4h', '6h', '12h', '1d', '3d', '1w']
const LOOKBACKS = [
  { n: 90, label: '3 月' },
  { n: 180, label: '6 月' },
  { n: 365, label: '1 年' },
  { n: 730, label: '2 年' },
]

function localDateStr(d: Date): string {
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}
function todayStr(): string {
  return localDateStr(new Date())
}

const cfg = ref({
  symbol: 'BTCUSDT',
  interval: '1d',
  startDate: '',
  endDate: '',
  horizon: 1,
  model: 'ridge',
  alpha: 1,
  maxCorr: 0.85,
  testRatio: 0.25,
  learningRate: 0.05,
  nEstimators: 240,
  maxDepth: 3,
  numLeaves: 7,
  subsample: 0.9,
  colsample: 0.9,
})
cfg.value.endDate = todayStr()
cfg.value.startDate = localDateStr(new Date(Date.now() - 365 * 86_400_000))

function setLookback(days: number) {
  const end = new Date()
  const start = new Date(end.getTime() - days * 24 * 3600 * 1000)
  cfg.value.startDate = localDateStr(start)
  cfg.value.endDate = localDateStr(end)
}

function trainingRange(): { start: Date; end: Date } {
  const start = new Date(`${cfg.value.startDate || todayStr()}T00:00:00`)
  const endD = new Date(`${cfg.value.endDate || todayStr()}T00:00:00`)
  endD.setDate(endD.getDate() + 1) // 含所选末日全天，边界取次日零点
  return { start, end: endD }
}

const _MS = { m: 60_000, h: 3_600_000, d: 86_400_000, w: 604_800_000 } as const
function barMs(interval: string): number {
  const m = /^(\d+)([mhdw])$/.exec(interval)
  return m ? +m[1] * (_MS[m[2] as keyof typeof _MS] ?? 0) : 0
}
function fmtDur(ms: number): string {
  if (ms % 86_400_000 === 0) {
    const d = ms / 86_400_000
    return d >= 7 ? `${Math.round((d / 7) * 10) / 10} 周` : `${d} 天`
  }
  if (ms % 3_600_000 === 0) return `${ms / 3_600_000} 小时`
  return `${ms / 60_000} 分钟`
}
const minuteHeavy = computed(() => ['1m', '3m'].includes(cfg.value.interval))
const horizonHint = computed(() => {
  const ms = barMs(cfg.value.interval) * cfg.value.horizon
  return ms ? `${cfg.value.horizon} × ${cfg.value.interval} ≈ ${fmtDur(ms)}` : ''
})

const target = ref('rank_ic>0.02')

const isRidge = computed(() => cfg.value.model === 'ridge')

const mName = ref('')
const saving = ref(false)

const checkedList = computed(() => {
  const r = result.value
  const gapOk = r ? (r.overfit_gap ?? 99) < 0.06 : null
  const signOk = r ? Math.sign(r.oos_ic ?? 0) * Math.sign(r.oos_rank_ic ?? 0) >= 0 : null
  const spreadOk = r ? r.spread > 0 : null
  const samplesOk = r ? r.n_test >= 40 : null
  return [
    { label: 'purged 时序切分（无未来函数）', ok: Boolean(r) },
    { label: `相关性过滤（阈值 ${cfg.value.maxCorr}）`, ok: Boolean(r) },
    { label: '正则化（Ridge α / LightGBM 深度·子采样）', ok: Boolean(r) },
    { label: 'train−OOS IC 差距 < 0.06（防记忆噪声）', ok: gapOk },
    { label: 'OOS IC 与 RankIC 同号（信号方向稳定）', ok: signOk },
    { label: 'Top−Bottom 价差 > 0（高分确实跑赢）', ok: spreadOk },
    { label: `测试样本 ≥ 40 行（${r ? `${r.n_test} 行` : '—'}）`, ok: samplesOk },
  ]
})

async function run() {
  if (running.value || codes.value.length < 2) return
  const { start, end } = trainingRange()
  if (end <= start) {
    toast.error('时间范围无效', '结束日期需晚于开始日期')
    return
  }
  running.value = true
  result.value = null
  args.value = null
  aiReview.value = null
  aiFeatCount.value = 0
  phase.value = '正在读取本地 K 线…'
  const a: MlTrainArgs = {
    codes: [...codes.value],
    symbols: [cfg.value.symbol.trim().toUpperCase()],
    interval: cfg.value.interval,
    start: start.toISOString(),
    end: end.toISOString(),
    horizon: cfg.value.horizon,
    test_ratio: cfg.value.testRatio,
    alpha: cfg.value.alpha,
    max_corr: cfg.value.maxCorr,
    model: cfg.value.model,
    learning_rate: cfg.value.learningRate,
    n_estimators: cfg.value.nEstimators,
    max_depth: cfg.value.maxDepth,
    num_leaves: cfg.value.numLeaves,
    subsample: cfg.value.subsample,
    colsample_bytree: cfg.value.colsample,
  }
  args.value = a
  phase.value = '特征标准化 + 去冗余 + 训练中…'
  try {
    const r = await trainMl(a)
    result.value = r
    if (r.found) {
      phase.value = '训练完成'
      const pass = r.spread > 0 && Math.sign(r.oos_ic ?? 0) * Math.sign(r.oos_rank_ic ?? 0) >= 0
      toast.info(pass ? '训练完成，方向一致' : '训练完成，注意体检项', `OOS RankIC ${r.oos_rank_ic ?? '—'}`)
    } else {
      toast.error('训练失败', r.message)
    }
  } catch (e: unknown) {
    toast.error('训练异常', (e as { message?: string }).message)
  } finally {
    running.value = false
  }
}

async function saveModel() {
  if (!args.value || !result.value?.found || saving.value || !mName.value.trim()) return
  saving.value = true
  try {
    const saved = await saveMl({ ...args.value, name: mName.value.trim() })
    mName.value = ''
    toast.success('模型已保存到模型库', `${saved.name}`)
  } catch (e: unknown) {
    toast.error('保存失败', (e as { message?: string }).message)
  } finally {
    saving.value = false
  }
}

function pnl(v: number): string {
  return v > 0 ? 'text-up' : v < 0 ? 'text-down' : 'text-muted'
}

// —— AI 解析：浮动按钮 → 大模型诊断训练结果；效果不理想时精筛特征并一键重训 ——
const aiOpen = ref(false)
const aiBusy = ref(false)
const aiReview = ref<TrainReviewResult | null>(null)
const aiFeatCount = ref(0)

function factorLabel(code: string): string {
  return factors.value.find((f) => f.code === code)?.name ?? code
}
function pearsonIdx(xs: number[], ys: number[]): number | null {
  const n = Math.min(xs.length, ys.length)
  if (n < 2) return null
  const mx = xs.reduce((s, v) => s + v, 0) / n
  const my = ys.reduce((s, v) => s + v, 0) / n
  let num = 0
  let dx = 0
  let dy = 0
  for (let i = 0; i < n; i++) {
    const a = xs[i] - mx
    const b = ys[i] - my
    num += a * b
    dx += a * a
    dy += b * b
  }
  return dx && dy ? num / Math.sqrt(dx * dy) : null
}
function last(arr?: number[]): number | null {
  return arr?.length ? arr[arr.length - 1] : null
}

function buildReviewPayload(r: MlTrainResult): TrainReviewPayload {
  const oc = r.oos_charts
  const dec = oc?.deciles ?? []
  const mono = dec.length > 1 ? pearsonIdx(dec.map((_, i) => i), dec.map((d) => d.mean_actual)) : null
  const nav = oc?.nav
  const learn = r.learning
  const oosLoss = learn?.oos ?? []
  const minLoss = oosLoss.length ? Math.min(...oosLoss) : null
  return {
    symbol: (cfg.value.symbol || '').trim().toUpperCase() || '?',
    interval: r.interval,
    horizon: r.horizon,
    model: r.model,
    engine: r.engine,
    n_train: r.n_train,
    n_test: r.n_test,
    metrics: {
      train_ic: r.train_ic,
      oos_ic: r.oos_ic,
      oos_rank_ic: r.oos_rank_ic,
      overfit_gap: r.overfit_gap,
      hit_rate: r.hit_rate,
      spread: r.spread,
      n_features_kept: r.n_features_kept,
    },
    checks: checkedList.value.map((c) => ({ label: c.label, ok: c.ok })),
    features: r.features.map((f) => ({ code: f.code, name: f.name, coef: f.coef, ic_oos: f.ic_oos, rank_ic_oos: f.rank_ic_oos })),
    removed: (r.feature_filter?.removed ?? []).map((x) => ({ code: x.code, name: x.name, dropped_because: x.dropped_because })),
    oos_charts: { monotonic_r: mono, q5_end: nav ? last(nav.q5) : null, q1_end: nav ? last(nav.q1) : null },
    learning: learn
      ? {
          rounds: learn.rounds.length,
          train_final: last(learn.train),
          oos_final: last(oosLoss),
          oos_min: minLoss,
          oos_min_round: minLoss != null ? oosLoss.indexOf(minLoss) + 1 : null,
        }
      : undefined,
  }
}

const aiGradeMeta = computed(() => {
  const g = aiReview.value?.grade ?? 'warn'
  const meta: Record<string, { tone: string; label: string }> = {
    ok: { tone: 'tone-up', label: '效果达标' },
    warn: { tone: 'tone-warn', label: '一般 · 可优化' },
    bad: { tone: 'tone-down', label: '不理想 · 建议精筛' },
  }
  return meta[g]
})
const aiRefinable = computed(() => {
  const rev = aiReview.value
  return !!rev && rev.grade !== 'ok' && rev.keep.length >= 2 && (rev.drop?.length ?? 0) > 0
})

async function aiAnalyze() {
  if (running.value || aiBusy.value) return
  const r = result.value
  if (!r?.found) {
    toast.error('请先完成训练', '点「开始训练」得到训练结果后，才能让 AI 解析诊断')
    return
  }
  aiBusy.value = true
  aiReview.value = null
  try {
    aiReview.value = await reviewTraining(buildReviewPayload(r))
    aiFeatCount.value = r.features.length
    const g = aiReview.value.grade
    const head = g === 'ok' ? 'AI 诊断：效果达标' : g === 'warn' ? 'AI 诊断：效果一般' : 'AI 诊断：效果不理想'
    toast.ai(head, aiReview.value.fallback ? '模型未返回可用 JSON，已按规则兜底' : `特征 ${aiFeatCount.value} 个 → 建议保留 ${aiReview.value.keep.length} 个`)
  } catch (e: unknown) {
    toast.error('AI 解析失败', (e as { message?: string }).message)
  } finally {
    aiBusy.value = false
  }
}

function aiRetrain() {
  const rev = aiReview.value
  if (!rev || !rev.keep?.length || running.value) return
  const keep = rev.keep.filter((c) => factors.value.some((f) => f.code === c))
  if (keep.length < 2) {
    toast.error('精筛后因子不足 2 个', '无法按 AI 建议重训，请改用手动勾选特征')
    return
  }
  codes.value = keep
  aiOpen.value = false
  toast.info('已套用 AI 精筛特征', `用保留的 ${keep.length} 个因子重新训练`)
  void run()
}

onMounted(async () => {
  try {
    factors.value = await listFactors()
  } catch {
    factors.value = []
  }
  // 支持从「因子库 · 候选因子池」带参跳转：一次消费 store 里的候选因子并预选为特征
  const preset = handoff
    .consume()
    .map((c) => c.trim())
    .filter(Boolean)
  const known = new Set(factors.value.map((f) => f.code))
  const valid = preset.filter((c) => known.has(c)).slice(0, 12)
  if (valid.length) {
    codes.value = valid
    const dropped = preset.length - valid.length
    toast.info('已载入候选因子', `从候选池预选了 ${valid.length} 个特征因子${dropped ? `（${dropped} 个不在库中已忽略）` : ''}`)
  } else {
    codes.value = factors.value.slice(0, 4).map((f) => f.code)
  }
})
</script>

<template>
  <div class="flex flex-col gap-6">
    <PageHeader
      eyebrow="ML Lab"
      title="模型训练"
      desc="因子 → 特征工程 → 时序切分 → 模型训练 → 过拟合体检 → 落库，一键转成策略回测。"
    >
      <template #actions>
        <HelpDrawer :help="pageHelp" />
        <router-link to="/models" class="btn btn-ghost">模型库</router-link>
        <router-link to="/factor/ai-lab" class="btn btn-soft"><Sparkles :size="15" />AI 因子工坊</router-link>
      </template>
    </PageHeader>

    <div class="grid grid-cols-1 gap-6 xl:grid-cols-3">
      <!-- 训练配置 -->
      <div>
        <Panel title="训练配置" sub="数据 + 特征 + 模型 + 正则 + 目标">
        <div class="space-y-4">
          <div class="grid grid-cols-2 gap-3">
            <div class="field">
              <label class="label" for="t-sym">交易对 <Tip text="训练与测试使用的 K 线标的（单标的）" /></label>
              <input id="t-sym" v-model="cfg.symbol" list="t-syms" class="input font-mono uppercase" spellcheck="false" />
              <datalist id="t-syms"><option v-for="s in MAJOR_SYMBOLS" :key="s" :value="s" /></datalist>
            </div>
            <div class="field">
              <label class="label" for="t-int">K 线粒度 <Tip text="特征与标签的计算频率，越细信号越密、样本越多，但噪声与算力也越大；需本地有该粒度或更细的 K 线（更细会自动聚合）" /></label>
              <select id="t-int" v-model="cfg.interval" class="select font-mono">
                <option v-for="i in TRAIN_INTERVALS" :key="i" :value="i">{{ i }}</option>
              </select>
              <p v-if="minuteHeavy" class="mt-1 text-[10px] leading-snug text-warn">分钟级样本量大：单标的单次最多取 5 万根，训练/因子计算更慢，建议把日期范围缩短或换更粗粒度。</p>
            </div>

            <div class="field col-span-2">
              <label class="label">数据时间范围 <Tip text="用具体起止日期圈定样本（含结束日全天）。保存模型后，模型库回测/打分会自动排除这段区间做样本外评估，所以起点越早、可评估的新数据越多" /></label>
              <div class="grid grid-cols-2 gap-2">
                <input id="t-start" v-model="cfg.startDate" type="date" class="input font-mono" :max="cfg.endDate" aria-label="开始日期" />
                <input id="t-end" v-model="cfg.endDate" type="date" class="input font-mono" :min="cfg.startDate" aria-label="结束日期" />
              </div>
              <div class="mt-2 flex flex-wrap items-center gap-1.5">
                <span class="text-[10px] text-subtle">快捷范围</span>
                <button v-for="p in LOOKBACKS" :key="p.n" class="chip !h-6 !px-2 !text-[11px]" @click="setLookback(p.n)">近 {{ p.label }}</button>
              </div>
            </div>
            <div class="field">
              <label class="label" for="t-hz">预测窗口 <Tip text="用未来几根「当前粒度」的 K 线收益作标签；N 越大越难预测、换仓越慢。它是根数而非固定时长（下方会显示折算时长）" /></label>
              <select id="t-hz" v-model.number="cfg.horizon" class="select">
                <option :value="1">1</option><option :value="2">2</option><option :value="3">3</option><option :value="5">5</option><option :value="10">10</option>
              </select>
              <p class="mt-1 truncate font-mono text-[10px] text-subtle">{{ horizonHint }}</p>
            </div>
            <div class="field">
              <label class="label" for="t-model">模型 <Tip text="Ridge 线性稳健、可保存复用；LightGBM 捕捉非线性，保存时序列化模型文件供打分/回测" /></label>
              <select id="t-model" v-model="cfg.model" class="select"><option value="ridge">Ridge 线性</option><option value="gbdt">LightGBM</option></select>
            </div>
            <div class="field">
              <label class="label" for="t-ratio">测试集比例 <Tip text="按时间末尾切出的样本外比例，用于无未来函数评价" /></label>
              <select id="t-ratio" v-model.number="cfg.testRatio" class="select"><option :value="0.2">20%</option><option :value="0.25">25%</option><option :value="0.3">30%</option></select>
            </div>
            <div class="field">
              <label class="label" :for="'t-alpha'">{{ isRidge ? '正则 α' : 'L2 叶子权重' }} <Tip :text="isRidge ? 'Ridge 惩罚系数，越大越强约束、越防过拟合' : 'LightGBM 叶子权重的 L2 正则项'" /></label>
              <input id="t-alpha" v-model.number="cfg.alpha" type="number" min="0" step="0.1" class="input font-mono" />
            </div>
            <div class="field">
              <label class="label" for="t-corr">相关性过滤阈值 <Tip text="特征两两 |相关| 超过该值则剔除较弱者，去冗余" /></label>
              <input id="t-corr" v-model.number="cfg.maxCorr" type="number" min="0.3" max="0.99" step="0.05" class="input font-mono" />
            </div>
          </div>

          <!-- LightGBM 专属超参 -->
          <template v-if="!isRidge">
            <p class="text-[11px] font-semibold uppercase tracking-wider text-subtle">LightGBM 超参数</p>
            <div class="grid grid-cols-2 gap-3">
              <div class="field">
                <label class="label" for="t-lr">学习率 <Tip text="每棵树贡献的缩减系数；越小越稳健、需更多树" /></label>
                <input id="t-lr" v-model.number="cfg.learningRate" type="number" min="0.001" max="1" step="0.01" class="input font-mono" />
              </div>
              <div class="field">
                <label class="label" for="t-ne">树数量 <Tip text="n_estimators：迭代的树数量，越大拟合越强、越易过拟合" /></label>
                <input id="t-ne" v-model.number="cfg.nEstimators" type="number" min="10" max="5000" step="10" class="input font-mono" />
              </div>
              <div class="field">
                <label class="label" for="t-md">最大深度 <Tip text="max_depth：单棵树最大深度，控制交互复杂度；-1 表示不限制" /></label>
                <input id="t-md" v-model.number="cfg.maxDepth" type="number" min="-1" max="32" step="1" class="input font-mono" />
              </div>
              <div class="field">
                <label class="label" for="t-nl">叶子数 <Tip text="num_leaves：每棵树叶子节点数，越大模型容量越大" /></label>
                <input id="t-nl" v-model.number="cfg.numLeaves" type="number" min="2" max="256" step="1" class="input font-mono" />
              </div>
              <div class="field">
                <label class="label" for="t-ss">行采样 <Tip text="subsample：每轮训练随机用多少比例样本，防过拟合" /></label>
                <input id="t-ss" v-model.number="cfg.subsample" type="number" min="0.1" max="1" step="0.05" class="input font-mono" />
              </div>
              <div class="field">
                <label class="label" for="t-cs">列采样 <Tip text="colsample_bytree：每棵树随机用多少比例特征，防过拟合" /></label>
                <input id="t-cs" v-model.number="cfg.colsample" type="number" min="0.1" max="1" step="0.05" class="input font-mono" />
              </div>
            </div>
          </template>

          <div>
            <p class="mb-2 text-xs font-semibold uppercase tracking-wider text-subtle">
              特征因子（已通过 IC 筛选更佳，可搜索多选）
            </p>
            <FactorPicker v-model:selected="codes" :factors="factors" :max="12" />
          </div>

          <div class="field">
            <label class="label" for="t-target">目标（体检通过线） <Tip text="用于提示本轮结果是否达到期望，不影响训练本身" /></label>
            <select id="t-target" v-model="target" class="select">
              <option value="rank_ic>0.02">OOS RankIC &gt; 0.02 且方向一致</option>
              <option value="spread>0">Top−Bottom 价差 &gt; 0</option>
              <option value="hit>0.52">命中率 &gt; 52%</option>
            </select>
          </div>

          <button class="btn btn-primary w-full" :disabled="running || codes.length < 2" @click="run">
            <LoaderCircle v-if="running" :size="15" class="animate-spin" /><Cpu v-else :size="15" />
            {{ running ? phase || '训练中…' : '开始训练' }}
          </button>
          <p class="text-[11px] leading-relaxed text-subtle">切分规则：每标的按时间先后取后 test_ratio 为测试段，训练/测试间保留 purge（horizon 根）缓冲，禁止随机洗牌。</p>
        </div>
      </Panel>
      </div>

      <!-- 结果/体检 -->
      <div class="flex flex-col gap-6 xl:col-span-2">
        <StateView v-if="!result" icon="spark" title="等待训练" desc="完成配置后点「开始训练」，这里会展示指标、图表与过拟合体检。" />
        <template v-else-if="!result.found">
          <Panel><StateView icon="error" :title="result.message ?? '训练失败'" /></Panel>
        </template>
        <template v-else>
          <Panel title="样本外指标" sub="样本外 = 模型从未见过的后段数据">
            <div class="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
              <div class="panel px-3 py-2.5"><p class="text-[10px] uppercase tracking-wider text-subtle">OOS IC <Tip text="OOS = Out-of-Sample（样本外）。IC：模型在训练时留出的样本外测试段上，预测值与实际未来收益的 Pearson 相关；绝对值越高预测越强，0 表示无预测力" /></p><p class="num mt-1 text-base font-semibold" :class="pnl(result.oos_ic ?? 0)">{{ result.oos_ic ?? '—' }}</p></div>
              <div class="panel px-3 py-2.5"><p class="text-[10px] uppercase tracking-wider text-subtle">OOS RankIC <Tip text="OOS = Out-of-Sample（样本外）。RankIC：样本外测试段上预测排序与实际收益排序的 Spearman 相关，衡量排序/择时能力，对离群值比 IC 更稳健" /></p><p class="num mt-1 text-base font-semibold" :class="pnl(result.oos_rank_ic ?? 0)">{{ result.oos_rank_ic ?? '—' }}</p></div>
              <div class="panel px-3 py-2.5"><p class="text-[10px] uppercase tracking-wider text-subtle">train IC <Tip text="模型在训练数据本身上的拟合 IC。通常高于样本外；若高出一大截，说明在“记住”训练期噪声，详见右下过拟合差距" /></p><p class="num mt-1 text-base font-semibold text-fg">{{ result.train_ic ?? '—' }}</p></div>
              <div class="panel px-3 py-2.5"><p class="text-[10px] uppercase tracking-wider text-subtle">过拟合差距 <Tip text="|train IC − OOS IC|：训练与样本外表现相差越小越好；&lt;0.06 视为可接受，≥0.06 提示模型可能在记忆噪声" /></p><p class="num mt-1 text-base font-semibold" :class="(result.overfit_gap ?? 9) < 0.06 ? 'text-up' : 'text-down'">{{ result.overfit_gap ?? '—' }}</p></div>
              <div class="panel px-3 py-2.5"><p class="text-[10px] uppercase tracking-wider text-subtle">命中率 <Tip text="方向命中率：预测涨/跌方向与实际方向一致的比例；&gt;50% 说明方向判断优于抛硬币" /></p><p class="num mt-1 text-base font-semibold text-fg">{{ (result.hit_rate * 100).toFixed(1) }}%</p></div>
              <div class="panel px-3 py-2.5"><p class="text-[10px] uppercase tracking-wider text-subtle">Top−Bottom <Tip text="把测试期按预测分取最高/最低各 20% 两组，比较实际平均收益；&gt;0 表示预测高分组的未来收益确实更高（多头信号有效）" /></p><p class="num mt-1 text-base font-semibold" :class="pnl(result.spread)">{{ result.spread.toFixed(5) }}</p></div>
            </div>

            <!-- 切分可视化 -->
            <div class="mt-4">
              <p class="mb-1.5 text-[11px] font-semibold uppercase tracking-wider text-subtle">时序切分</p>
              <div class="flex h-3 overflow-hidden rounded-full bg-surface-2">
                <div class="bg-primary" :style="{ width: `${(result.n_train / (result.n_train + result.n_test)) * 100}%` }" />
                <div class="bg-ai" :style="{ width: `${(result.n_test / (result.n_train + result.n_test)) * 100}%` }" />
              </div>
              <div class="mt-1 flex justify-between font-mono text-[10px] text-subtle"><span>train {{ result.n_train }}（蓝）</span><span>test {{ result.n_test }}（AI 橙）· purge={{ result.horizon }}</span></div>
            </div>

            <!-- 过拟合体检 -->
            <div class="mt-4">
              <p class="mb-1.5 text-[11px] font-semibold uppercase tracking-wider text-subtle">过拟合 / 组合防护体检</p>
              <ul class="grid grid-cols-1 gap-1.5 sm:grid-cols-2">
                <li v-for="c in checkedList" :key="c.label" class="flex items-center gap-2 text-xs">
                  <span class="badge" :class="c.ok === true ? 'tone-up' : c.ok === false ? 'tone-down' : 'tone-neutral'">
                    <Check v-if="c.ok === true" :size="10" />{{ c.ok === true ? '通过' : c.ok === false ? '未达' : '待跑' }}
                  </span>
                  <span class="text-muted">{{ c.label }}</span>
                </li>
              </ul>
            </div>
          </Panel>

          <Panel title="训练诊断图表" sub="样本外逐样本：预测分层单调性 · 预测 vs 实际 · 分层累计净值 · 树模型训练损失">
            <TrainCharts :result="result" />
          </Panel>

          <Panel title="特征重要性 / 系数" sub="对标准化特征：|系数| 越大影响越强；并列出单因子样本外 IC">
            <div class="space-y-2">
              <div v-for="f in result.features" :key="f.code" class="flex items-center gap-3">
                <span class="w-32 truncate text-xs text-fg">{{ f.name }}</span>
                <div class="h-2.5 min-w-0 flex-1 overflow-hidden rounded-full bg-surface-2">
                  <div class="h-full rounded-full" :class="f.coef >= 0 ? 'bg-up' : 'bg-down'" :style="{ width: `${Math.min(Math.abs(f.coef) / Math.max(...result.features.map((x) => Math.abs(x.coef)), 1e-9) * 100, 100)}%` }" />
                </div>
                <span class="num w-24 text-right text-xs text-muted">coef {{ f.coef.toFixed(5) }}</span>
                <span class="num w-20 text-right text-xs" :class="pnl(f.ic_oos ?? 0)">IC {{ f.ic_oos ?? '—' }}</span>
              </div>
            </div>
            <p v-if="result.feature_filter?.removed?.length" class="mt-2 text-[11px] text-warn">
              已剔除冗余（|相关|&gt;{{ result.feature_filter.max_corr }}）：{{ result.feature_filter.removed.map((r) => r.code).join('、') }}
            </p>
            <p class="mt-1.5 text-[11px] text-subtle">引擎：{{ result.engine }} · 保留 {{ result.n_features_kept }} 个特征 · 样本 train {{ result.n_train }} / test {{ result.n_test }}</p>
          </Panel>

          <Panel title="保存与下一步" sub="把体检通过的模型落库，去「模型库」一键转策略回测 / 部署模拟盘">
            <div class="flex gap-2">
              <input v-model="mName" class="input h-9 flex-1 font-mono text-xs" placeholder="模型名称，如 btc-1d-gbdt" maxlength="120" />
              <button class="btn btn-primary !h-9" :disabled="saving || !mName.trim()" @click="saveModel"><LoaderCircle v-if="saving" :size="14" class="animate-spin" />保存模型</button>
            </div>
            <p class="mt-2 text-[11px] text-subtle">保存后到「模型库」可打分、一键建策略回测、部署模拟盘。LightGBM 树模型以序列化文件落盘，Ridge 仅存线性权重。</p>
          </Panel>
        </template>
      </div>
    </div>

    <!-- AI 解析：右下角浮动按钮 + 解析详情面板 -->
    <button
      class="fixed bottom-5 right-5 z-40 flex h-12 items-center gap-2 rounded-full bg-[var(--color-primary-fill)] px-4 text-sm font-semibold text-[var(--color-on-primary)] shadow-pop transition-transform hover:brightness-105 active:scale-95"
      :title="result?.found ? (aiOpen ? '收起 AI 解析' : '由大模型诊断训练结果；不理想时精筛特征并一键重训') : '先训练出结果，再由 AI 解析'"
      @click="result?.found ? (aiOpen = !aiOpen) : toast.error('请先完成训练', '点「开始训练」得到训练结果后，再让 AI 解析诊断')"
    >
      <span class="relative">
        <Sparkles :size="19" />
        <span v-if="aiReview" class="absolute -right-2 -top-1.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-ai px-1 text-[9px] font-bold text-[var(--color-on-primary)]">AI</span>
      </span>
      <span class="hidden sm:inline">{{ aiOpen ? '收起' : 'AI 解析' }}</span>
    </button>

    <div
      v-if="aiOpen"
      class="fixed bottom-20 right-5 z-40 flex w-[min(430px,calc(100vw-2.5rem))] flex-col overflow-hidden rounded-2xl border border-border bg-surface shadow-pop"
      style="max-height: min(600px, calc(100vh - 6.5rem))"
    >
      <header class="flex items-center gap-2 border-b border-border px-4 py-3">
        <Sparkles :size="15" class="text-ai" />
        <div class="min-w-0 flex-1">
          <p class="text-sm font-semibold text-fg">AI 训练解析</p>
          <p class="truncate text-[11px] text-subtle">大模型诊断本轮训练：指标体检 → 结论 → 不理想时精筛特征并一键重训</p>
        </div>
        <button class="btn btn-icon !h-7 !w-7 text-muted hover:bg-surface-2" title="收起" @click="aiOpen = false"><X :size="14" /></button>
      </header>

      <div class="min-h-0 flex-1 space-y-3 overflow-y-auto px-4 py-3">
        <template v-if="!resInfo">
          <StateView icon="spark" title="还没有可解析的训练结果" desc="先完成「训练配置」并点「开始训练」，拿到样本外指标后这里会出现诊断与精筛建议。" />
        </template>

        <template v-else>
          <template v-if="aiBusy">
            <div class="flex flex-col items-center gap-2 py-10 text-center">
              <LoaderCircle :size="22" class="animate-spin text-primary" />
              <p class="text-xs font-medium text-fg">AI 正在诊断训练结果…</p>
              <p class="text-[11px] text-subtle">会综合 IC/RankIC、过拟合差距、体检项与分层表现给出结论</p>
            </div>
          </template>

          <template v-else-if="aiReview">
            <div class="flex flex-wrap items-center gap-2">
              <span class="badge" :class="aiGradeMeta.tone">{{ aiGradeMeta.label }}</span>
              <span class="text-[11px] text-subtle">{{ resInfo.engine }} · {{ resInfo.n_train }} train / {{ resInfo.n_test }} test</span>
            </div>

            <div v-if="aiReview.review" class="rounded-lg border border-border bg-surface-2/50 px-3 py-2.5">
              <p class="mb-1 text-[10px] font-semibold uppercase tracking-wider text-subtle">诊断结论</p>
              <p class="whitespace-pre-line text-xs leading-relaxed text-fg">{{ aiReview.review }}</p>
            </div>

            <div v-if="aiReview.advice" class="rounded-lg border border-ai/30 bg-ai/5 px-3 py-2.5">
              <p class="flex items-center gap-1 text-[10px] font-semibold uppercase tracking-wider text-ai-ink">
                <Sparkles :size="11" />AI 建议下一步
              </p>
              <p class="mt-1 text-xs leading-relaxed text-muted">{{ aiReview.advice }}</p>
            </div>

            <!-- 精筛建议 -->
            <div v-if="aiRefinable" class="space-y-2 rounded-lg border border-warn/30 bg-warn/5 px-3 py-2.5">
              <p class="text-[10px] font-semibold uppercase tracking-wider text-warn">特征精筛（本轮 {{ aiFeatCount }} 个 → 建议保留 {{ aiReview.keep.length }} 个）</p>
              <div>
                <p class="mb-1 text-[11px] font-semibold text-fg">建议保留</p>
                <div class="flex flex-wrap gap-1.5">
                  <span v-for="c in aiReview.keep" :key="c" class="badge tone-up">{{ factorLabel(c) }}</span>
                </div>
              </div>
              <div v-if="aiReview.drop.length">
                <p class="mb-1 text-[11px] font-semibold text-down">建议剔除（{{ aiReview.drop.length }}）</p>
                <ul class="space-y-1">
                  <li v-for="d in aiReview.drop" :key="d.code" class="flex items-start gap-1.5 text-[11px] leading-relaxed text-muted">
                    <X :size="11" class="mt-0.5 shrink-0 text-down" />
                    <span><span class="font-semibold text-fg">{{ factorLabel(d.code) }}</span><span v-if="d.reason">：{{ d.reason }}</span></span>
                  </li>
                </ul>
              </div>
              <button class="btn btn-primary !h-8 w-full text-xs" :disabled="running" @click="aiRetrain">
                <LoaderCircle v-if="running" :size="13" class="animate-spin" />
                <Cpu v-else :size="13" />
                {{ running ? '训练中…' : `用保留的 ${aiReview.keep.length} 个因子重新训练` }}
              </button>
            </div>

            <div v-else class="rounded-lg border border-up/30 bg-up/5 px-3 py-2 text-[11px] leading-relaxed text-up">
              效果达标（或不建议继续删因子）：本轮可直接「保存模型」→ 到模型库一键建策略回测 / 部署模拟盘。
            </div>

            <button class="btn btn-ghost !h-8 w-full text-xs" :disabled="aiBusy" @click="aiAnalyze">
              <RefreshCw v-if="!aiBusy" :size="12" />重新解析
            </button>
          </template>

          <template v-else>
            <div class="flex flex-col gap-2.5">
              <p class="text-xs leading-relaxed text-muted">
                基于 <b class="text-fg">{{ resInfo.engine }}</b> 这轮结果（{{ resInfo.n_train }} train / {{ resInfo.n_test }} test，保留 {{ resInfo.n_features_kept }} 个特征）让大模型做诊断：
                效果不理想时它会给出<b class="text-fg">特征精筛</b>——保留哪些、剔除哪些，并可一键用精筛后的因子重新训练。
              </p>
              <button class="btn btn-primary !h-9 w-full" @click="aiAnalyze">
                <Sparkles :size="14" />生成 AI 解析
              </button>
            </div>
          </template>
        </template>
      </div>
    </div>
  </div>
</template>
