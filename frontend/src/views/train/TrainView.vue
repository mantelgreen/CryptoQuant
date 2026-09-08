<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Cpu, Check, LoaderCircle, RefreshCw, Sparkles } from '@lucide/vue'
import PageHeader from '@/components/ui/PageHeader.vue'
import HelpDrawer, { type PageHelp } from '@/components/ui/HelpDrawer.vue'
import Panel from '@/components/ui/Panel.vue'
import StateView from '@/components/ui/StateView.vue'
import Modal from '@/components/ui/Modal.vue'
import { useToastStore } from '@/stores/toast'
import {
  listFactors,
  trainMl,
  saveMl,
  listMlModels,
  scoreMlModel,
  type FactorMeta,
  type MlTrainResult,
  type MlTrainArgs,
  type MlModelRow,
} from '@/api/factor'
import { createStrategy, createVersion, runBacktest } from '@/api/strategy'
import { INTERVALS, MAJOR_SYMBOLS, fmtTime, timeAgo } from '@/utils/format'

const pageHelp: PageHelp = {
  title: '模型训练（ML 工坊）',
  intro: '把因子研究升级成“可回测的机器学习信号”：配置 → 训练 → 体检 → 落库 → 一键转策略。',
  terms: [
    { term: '时序切分', desc: '按时间先后切 train/test（训练段在后端前加 purge 缓冲），禁止随机洗牌，避免未来信息泄漏。' },
    { term: 'train IC vs OOS IC', desc: '训练集内拟合的 IC 若远高于样本外，说明在记忆噪声；两者差距=过拟合风险。' },
    { term: '相关性过滤', desc: '训练集内两两 |相关| 超阈值即剔除，防止组合里信息冗余。' },
    { term: 'Top−Bottom 价差', desc: '把测试期按预测分高低各取 20%，两组实际收益均值差；>0 表示高分组确实跑赢。' },
  ],
  tips: ['默认阈值较保守；样本<100 行的结果谨慎采纳。', '保存后可在模型库一键“建策略回测”，对比基准。', '小数据优先 Ridge；数据量大、非线性强再用 LightGBM。'],
}

const router = useRouter()
const toast = useToastStore()
const factors = ref<FactorMeta[]>([])
const codes = ref<Set<string>>(new Set())
const running = ref(false)
const phase = ref('')
const result = ref<MlTrainResult | null>(null)
const cfg = ref({
  symbol: 'BTCUSDT',
  interval: '1d',
  days: 365,
  horizon: 1,
  model: 'ridge',
  alpha: 1,
  maxCorr: 0.85,
  testRatio: 0.25,
})
const target = ref('rank_ic>0.02')
const args = ref<MlTrainArgs | null>(null)

const models = ref<MlModelRow[]>([])
const modelsLoading = ref(false)
const scoreBusy = ref<string | null>(null)
const btModel = ref<MlModelRow | null>(null)
const btOpen = ref(false)
const btBusy = ref(false)
const btCfg = ref({ window: 60, days: 30 })

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

function toggle(code: string) {
  const s = new Set(codes.value)
  if (s.has(code)) s.delete(code)
  else if (s.size < 8) s.add(code)
  codes.value = s
}

async function run() {
  if (running.value || codes.value.size < 2) return
  running.value = true
  result.value = null
  args.value = null
  phase.value = '正在读取本地 K 线…'
  const end = new Date()
  const start = new Date(end.getTime() - cfg.value.days * 24 * 3600 * 1000)
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

const mName = ref('')
const saving = ref(false)
async function saveModel() {
  if (!args.value || !result.value?.found || saving.value || !mName.value.trim()) return
  saving.value = true
  try {
    const saved = await saveMl({ ...args.value, name: mName.value.trim() })
    mName.value = ''
    toast.success('模型已保存到模型库', `${saved.name}`)
    await openModels()
  } catch (e: unknown) {
    toast.error('保存失败', (e as { message?: string }).message)
  } finally {
    saving.value = false
  }
}

async function loadModels() {
  modelsLoading.value = true
  try {
    models.value = await listMlModels()
  } catch {
    models.value = []
  } finally {
    modelsLoading.value = false
  }
}
async function openModels() {
  await loadModels()
}

async function scoreModel(m: MlModelRow) {
  scoreBusy.value = m.id
  const days = m.interval.endsWith('d') ? 30 : m.interval.endsWith('h') ? 14 : 3
  const end = new Date()
  const start = new Date(end.getTime() - days * 24 * 3600 * 1000)
  try {
    const sym = m.symbols?.[0] ?? 'BTCUSDT'
    const res = await scoreMlModel(m.id, { symbol: sym, start: start.toISOString(), end: end.toISOString() })
    toast.info('打分完成', `${sym} 最近 ${res.n} bar · 最新分 ${res.last_score ?? '—'}`)
  } catch (e: unknown) {
    toast.error('打分失败', (e as { message?: string }).message)
  } finally {
    scoreBusy.value = null
  }
}

function openBt(m: MlModelRow) {
  btModel.value = m
  btCfg.value = { window: 60, days: m.interval.endsWith('d') ? 300 : m.interval.endsWith('h') ? 30 : 7 }
  btOpen.value = true
}
async function runBt() {
  const m = btModel.value
  if (!m || btBusy.value) return
  btBusy.value = true
  const end = new Date()
  const start = new Date(end.getTime() - btCfg.value.days * 24 * 3600 * 1000)
  const features = (m.features ?? []).map((f) => ({ code: f.code, coef: f.coef, mean: f.mean, std: f.std }))
  const params: Record<string, unknown> = {
    template_id: 'ml_score',
    features,
    window: btCfg.value.window,
    enter_q: 0.8,
    exit_q: 0.2,
  }
  try {
    const sym = m.symbols?.[0] ?? 'BTCUSDT'
    const str = await createStrategy({ name: `${m.name}-bt`, description: `ML 模型「${m.name}」驱动的择时策略。`, tags: ['ML'] })
    const ver = await createVersion(str.strategy_id, { template_id: 'ml_score', params })
    await runBacktest({ strategy_id: str.strategy_id, version_id: ver.version_id, symbol: sym, interval: m.interval, start: start.toISOString(), end: end.toISOString(), params })
    btOpen.value = false
    toast.success('策略已创建并开始回测', '请到策略研究查看')
    router.push(`/strategy/${str.strategy_id}`)
  } catch (e: unknown) {
    toast.error('建策略失败', (e as { message?: string }).message)
  } finally {
    btBusy.value = false
  }
}

function pnl(v: number): string {
  return v > 0 ? 'text-up' : v < 0 ? 'text-down' : 'text-muted'
}

onMounted(async () => {
  try {
    factors.value = await listFactors()
  } catch {
    factors.value = []
  }
  codes.value = new Set(factors.value.slice(0, 4).map((f) => f.code))
  await loadModels()
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
        <button class="btn btn-ghost" :disabled="modelsLoading" @click="openModels"><RefreshCw :size="15" :class="{ 'animate-spin-slow': modelsLoading }" />模型库</button>
        <router-link to="/factor/ai-lab" class="btn btn-soft"><Sparkles :size="15" />AI 因子工坊</router-link>
      </template>
    </PageHeader>

    <div class="grid grid-cols-1 gap-6 xl:grid-cols-3">
      <!-- 训练配置 -->
      <Panel title="训练配置" sub="数据 + 特征 + 模型 + 正则 + 目标">
        <div class="space-y-4">
          <div class="grid grid-cols-2 gap-3">
            <div class="field">
              <label class="label" for="t-sym">交易对</label>
              <input id="t-sym" v-model="cfg.symbol" list="t-syms" class="input font-mono uppercase" spellcheck="false" />
              <datalist id="t-syms"><option v-for="s in MAJOR_SYMBOLS" :key="s" :value="s" /></datalist>
            </div>
            <div class="field">
              <label class="label" for="t-int">K 线粒度</label>
              <select id="t-int" v-model="cfg.interval" class="select font-mono">
                <option v-for="i in INTERVALS.filter((x) => ['15m', '30m', '1h', '1d'].includes(x))" :key="i" :value="i">{{ i }}</option>
              </select>
            </div>
            <div class="field">
              <label class="label" for="t-days">回看窗口</label>
              <select id="t-days" v-model.number="cfg.days" class="select">
                <option :value="180">180 天</option><option :value="365">1 年</option><option :value="730">2 年</option>
              </select>
            </div>
            <div class="field">
              <label class="label" for="t-hz">预测窗口(h)</label>
              <select id="t-hz" v-model.number="cfg.horizon" class="select"><option :value="1">1</option><option :value="3">3</option><option :value="5">5</option></select>
            </div>
            <div class="field">
              <label class="label" for="t-model">模型</label>
              <select id="t-model" v-model="cfg.model" class="select"><option value="ridge">Ridge 线性</option><option value="gbdt">LightGBM</option></select>
            </div>
            <div class="field">
              <label class="label">测试集比例</label>
              <select v-model.number="cfg.testRatio" class="select"><option :value="0.2">20%</option><option :value="0.25">25%</option><option :value="0.3">30%</option></select>
            </div>
            <div class="field">
              <label class="label" :for="'t-alpha'">{{ cfg.model === 'ridge' ? '正则 α' : 'L2 叶子权重' }}</label>
              <input id="t-alpha" v-model.number="cfg.alpha" type="number" min="0" step="0.1" class="input font-mono" />
            </div>
            <div class="field">
              <label class="label" for="t-corr">相关性过滤阈值</label>
              <input id="t-corr" v-model.number="cfg.maxCorr" type="number" min="0.3" max="0.99" step="0.05" class="input font-mono" />
            </div>
          </div>

          <div>
            <p class="mb-2 text-xs font-semibold uppercase tracking-wider text-subtle">特征因子（2~8，建议已通过 IC 筛选）</p>
            <div class="flex flex-wrap gap-1.5">
              <button v-for="f in factors" :key="f.code" class="chip" :class="{ 'is-active': codes.has(f.code) }" @click="toggle(f.code)">{{ f.name }}</button>
            </div>
          </div>

          <div class="field">
            <label class="label">目标（体检通过线）</label>
            <select v-model="target" class="select">
              <option value="rank_ic>0.02">OOS RankIC &gt; 0.02 且方向一致</option>
              <option value="spread>0">Top−Bottom 价差 &gt; 0</option>
              <option value="hit>0.52">命中率 &gt; 52%</option>
            </select>
          </div>

          <button class="btn btn-primary w-full" :disabled="running || codes.size < 2" @click="run">
            <LoaderCircle v-if="running" :size="15" class="animate-spin" /><Cpu v-else :size="15" />
            {{ running ? phase || '训练中…' : '开始训练' }}
          </button>
          <p class="text-[11px] leading-relaxed text-subtle">切分规则：每标的按时间先后取后 test_ratio 为测试段，训练/测试间保留 purge（horizon 根）缓冲，禁止随机洗牌。</p>
        </div>
      </Panel>

      <!-- 结果/体检 -->
      <div class="flex flex-col gap-6 xl:col-span-2">
        <StateView v-if="!result" icon="spark" title="等待训练" desc="完成配置后点「开始训练」，这里会展示指标、图表与过拟合体检。" />
        <template v-else-if="!result.found">
          <Panel><StateView icon="error" :title="result.message ?? '训练失败'" /></Panel>
        </template>
        <template v-else>
          <Panel title="样本外指标" sub="样本外 = 模型从未见过的后段数据">
            <div class="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
              <div class="panel px-3 py-2.5"><p class="text-[10px] uppercase tracking-wider text-subtle">OOS IC</p><p class="num mt-1 text-base font-semibold" :class="pnl(result.oos_ic ?? 0)">{{ result.oos_ic ?? '—' }}</p></div>
              <div class="panel px-3 py-2.5"><p class="text-[10px] uppercase tracking-wider text-subtle">OOS RankIC</p><p class="num mt-1 text-base font-semibold" :class="pnl(result.oos_rank_ic ?? 0)">{{ result.oos_rank_ic ?? '—' }}</p></div>
              <div class="panel px-3 py-2.5"><p class="text-[10px] uppercase tracking-wider text-subtle">train IC</p><p class="num mt-1 text-base font-semibold text-fg">{{ result.train_ic ?? '—' }}</p></div>
              <div class="panel px-3 py-2.5"><p class="text-[10px] uppercase tracking-wider text-subtle">过拟合差距</p><p class="num mt-1 text-base font-semibold" :class="(result.overfit_gap ?? 9) < 0.06 ? 'text-up' : 'text-down'">{{ result.overfit_gap ?? '—' }}</p></div>
              <div class="panel px-3 py-2.5"><p class="text-[10px] uppercase tracking-wider text-subtle">命中率</p><p class="num mt-1 text-base font-semibold text-fg">{{ (result.hit_rate * 100).toFixed(1) }}%</p></div>
              <div class="panel px-3 py-2.5"><p class="text-[10px] uppercase tracking-wider text-subtle">Top−Bottom</p><p class="num mt-1 text-base font-semibold" :class="pnl(result.spread)">{{ result.spread.toFixed(5) }}</p></div>
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

          <Panel title="保存与下一步" sub="把体检通过的模型落库，去“模型库”一键转策略回测">
            <div class="flex gap-2">
              <input v-model="mName" class="input h-9 flex-1 font-mono text-xs" placeholder="模型名称，如 btc-1d-lgb" maxlength="120" />
              <button class="btn btn-primary !h-9" :disabled="saving || !mName.trim()" @click="saveModel"><LoaderCircle v-if="saving" :size="14" class="animate-spin" />保存模型</button>
            </div>
          </Panel>
        </template>
      </div>
    </div>

    <!-- 模型库 -->
    <Panel title="模型库" sub="已保存的 Ridge 模型（可打分、一键转策略回测）">
      <div v-if="modelsLoading" class="py-6"><StateView busy title="读取模型…" /></div>
      <StateView v-else-if="!models.length" icon="empty" title="还没有模型" desc="训练通过后填写名称即可保存到库。" />
      <div v-else class="grid grid-cols-1 gap-2 lg:grid-cols-2">
        <div v-for="m in models" :key="m.id" class="flex flex-wrap items-center gap-3 rounded-xl border border-border bg-surface-2/40 px-3.5 py-2.5">
          <div class="min-w-0 flex-1">
            <p class="text-sm font-semibold text-fg">{{ m.name }}</p>
            <p class="num mt-0.5 text-[11px] text-subtle">{{ m.interval }} · h{{ m.horizon }} · train {{ m.n_train }} · {{ timeAgo(m.created_at) }}</p>
            <p class="num mt-0.5 text-[11px] text-muted">OOS IC {{ m.metrics?.oos_ic ?? '—' }} · RankIC {{ m.metrics?.oos_rank_ic ?? '—' }}</p>
          </div>
          <div class="flex shrink-0 items-center gap-2">
            <button class="btn btn-ghost !h-8 text-xs" :disabled="scoreBusy === m.id" @click="scoreModel(m)">打分</button>
            <button class="btn btn-primary !h-8 text-xs" @click="openBt(m)">建策略回测</button>
          </div>
        </div>
      </div>
    </Panel>

    <Modal :open="btOpen" :title="`用模型建策略 · ${btModel?.name ?? ''}`" :sub="`${btModel?.interval} · 因果滚动分位择时`" width="480px" @close="btOpen = false">
      <div class="space-y-4">
        <div class="grid grid-cols-2 gap-3">
          <div class="field"><label class="label" for="bb-window">滚动窗口</label><input id="bb-window" v-model.number="btCfg.window" type="number" min="20" class="input" /></div>
          <div class="field"><label class="label" for="bb-days">回看天数</label><input id="bb-days" v-model.number="btCfg.days" type="number" min="30" class="input" /></div>
        </div>
        <p class="text-xs text-subtle">策略跑在 {{ btModel?.symbols?.[0] ?? 'BTCUSDT' }} · {{ btModel?.interval }}，评分高于滚动 q80 做多、低于 q20 平仓。</p>
      </div>
      <template #footer>
        <div class="flex justify-end gap-2">
          <button class="btn btn-ghost" @click="btOpen = false">取消</button>
          <button class="btn btn-primary" :disabled="btBusy" @click="runBt"><LoaderCircle v-if="btBusy" :size="14" class="animate-spin" />创建并回测</button>
        </div>
      </template>
    </Modal>
  </div>
</template>
