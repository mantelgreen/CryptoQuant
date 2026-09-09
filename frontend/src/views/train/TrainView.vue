<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Check, Cpu, Info, LoaderCircle, Sparkles } from '@lucide/vue'
import PageHeader from '@/components/ui/PageHeader.vue'
import HelpDrawer, { type PageHelp } from '@/components/ui/HelpDrawer.vue'
import Panel from '@/components/ui/Panel.vue'
import StateView from '@/components/ui/StateView.vue'
import FactorPicker from '@/components/ui/FactorPicker.vue'
import { useToastStore } from '@/stores/toast'
import {
  listFactors,
  trainMl,
  saveMl,
  type FactorMeta,
  type MlTrainResult,
  type MlTrainArgs,
} from '@/api/factor'
import { INTERVALS, MAJOR_SYMBOLS } from '@/utils/format'

const pageHelp: PageHelp = {
  title: '模型训练（ML 工坊）',
  intro: '把因子研究升级成“可回测的机器学习信号”：配置 → 训练 → 体检 → 落库 → 一键转策略。',
  terms: [
    { term: '时序切分', desc: '按时间先后切 train/test（训练段在后端前加 purge 缓冲），禁止随机洗牌，避免未来信息泄漏。' },
    { term: 'train IC vs OOS IC', desc: '训练集内拟合的 IC 若远高于样本外，说明在记忆噪声；两者差距=过拟合风险。' },
    { term: '相关性过滤', desc: '训练集内两两 |相关| 超阈值即剔除，防止组合里信息冗余。' },
    { term: 'Top−Bottom 价差', desc: '把测试期按预测分高低各取 20%，两组实际收益均值差；>0 表示高分组确实跑赢。' },
    { term: '学习率 / 树参数（LightGBM）', desc: 'learning_rate 决定每棵树的贡献缩减；n_estimators 是树数量；max_depth/num_leaves 控制单棵树复杂度；subsample/colsample 是行/列采样，都可防过拟合。' },
  ],
  tips: ['默认阈值较保守；样本<100 行的结果谨慎采纳。', '保存后可在「模型库」一键“建策略回测”、部署模拟盘。', '小数据优先 Ridge；数据量大、非线性强再用 LightGBM。'],
}

const toast = useToastStore()
const factors = ref<FactorMeta[]>([])
const codes = ref<string[]>([])
const running = ref(false)
const phase = ref('')
const result = ref<MlTrainResult | null>(null)
const args = ref<MlTrainArgs | null>(null)

const cfg = ref({
  symbol: 'BTCUSDT',
  interval: '1d',
  days: 365,
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
  if (cfg.value.model !== 'ridge') {
    toast.error('暂不支持保存', '当前仅 Ridge 线性模型可落库打分/回测；LightGBM 可研究但暂不能保存')
    return
  }
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

onMounted(async () => {
  try {
    factors.value = await listFactors()
  } catch {
    factors.value = []
  }
  codes.value = factors.value.slice(0, 4).map((f) => f.code)
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
              <label class="label" for="t-sym">交易对 <Info :size="12" class="inline text-subtle" title="训练与测试使用的 K 线标的（单标的）" /></label>
              <input id="t-sym" v-model="cfg.symbol" list="t-syms" class="input font-mono uppercase" spellcheck="false" />
              <datalist id="t-syms"><option v-for="s in MAJOR_SYMBOLS" :key="s" :value="s" /></datalist>
            </div>
            <div class="field">
              <label class="label" for="t-int">K 线粒度 <Info :size="12" class="inline text-subtle" title="特征与标签的计算频率，越细信号越密但噪声也越大" /></label>
              <select id="t-int" v-model="cfg.interval" class="select font-mono">
                <option v-for="i in INTERVALS.filter((x) => ['15m', '30m', '1h', '1d'].includes(x))" :key="i" :value="i">{{ i }}</option>
              </select>
            </div>
            <div class="field">
              <label class="label" for="t-days">回看窗口 <Info :size="12" class="inline text-subtle" title="样本时间跨度；越长样本越足，但可能包含过时行情" /></label>
              <select id="t-days" v-model.number="cfg.days" class="select">
                <option :value="180">180 天</option><option :value="365">1 年</option><option :value="730">2 年</option>
              </select>
            </div>
            <div class="field">
              <label class="label" for="t-hz">预测窗口(h) <Info :size="12" class="inline text-subtle" title="用未来几根 K 线收益作标签；越大越难预测、换仓越慢" /></label>
              <select id="t-hz" v-model.number="cfg.horizon" class="select"><option :value="1">1</option><option :value="3">3</option><option :value="5">5</option></select>
            </div>
            <div class="field">
              <label class="label" for="t-model">模型 <Info :size="12" class="inline text-subtle" title="Ridge 线性稳健、可保存复用；LightGBM 捕捉非线性但暂不可保存" /></label>
              <select id="t-model" v-model="cfg.model" class="select"><option value="ridge">Ridge 线性</option><option value="gbdt">LightGBM</option></select>
            </div>
            <div class="field">
              <label class="label" for="t-ratio">测试集比例 <Info :size="12" class="inline text-subtle" title="按时间末尾切出的样本外比例，用于无未来函数评价" /></label>
              <select id="t-ratio" v-model.number="cfg.testRatio" class="select"><option :value="0.2">20%</option><option :value="0.25">25%</option><option :value="0.3">30%</option></select>
            </div>
            <div class="field">
              <label class="label" :for="'t-alpha'">{{ isRidge ? '正则 α' : 'L2 叶子权重' }} <Info :size="12" class="inline text-subtle" :title="isRidge ? 'Ridge 惩罚系数，越大越强约束、越防过拟合' : 'LightGBM 叶子权重的 L2 正则项'" /></label>
              <input id="t-alpha" v-model.number="cfg.alpha" type="number" min="0" step="0.1" class="input font-mono" />
            </div>
            <div class="field">
              <label class="label" for="t-corr">相关性过滤阈值 <Info :size="12" class="inline text-subtle" title="特征两两 |相关| 超过该值则剔除较弱者，去冗余" /></label>
              <input id="t-corr" v-model.number="cfg.maxCorr" type="number" min="0.3" max="0.99" step="0.05" class="input font-mono" />
            </div>
          </div>

          <!-- LightGBM 专属超参 -->
          <template v-if="!isRidge">
            <p class="text-[11px] font-semibold uppercase tracking-wider text-subtle">LightGBM 超参数</p>
            <div class="grid grid-cols-2 gap-3">
              <div class="field">
                <label class="label" for="t-lr">学习率 <Info :size="12" class="inline text-subtle" title="每棵树贡献的缩减系数；越小越稳健、需更多树" /></label>
                <input id="t-lr" v-model.number="cfg.learningRate" type="number" min="0.001" max="1" step="0.01" class="input font-mono" />
              </div>
              <div class="field">
                <label class="label" for="t-ne">树数量 <Info :size="12" class="inline text-subtle" title="n_estimators：迭代的树数量，越大拟合越强、越易过拟合" /></label>
                <input id="t-ne" v-model.number="cfg.nEstimators" type="number" min="10" max="5000" step="10" class="input font-mono" />
              </div>
              <div class="field">
                <label class="label" for="t-md">最大深度 <Info :size="12" class="inline text-subtle" title="max_depth：单棵树最大深度，控制交互复杂度；-1 表示不限制" /></label>
                <input id="t-md" v-model.number="cfg.maxDepth" type="number" min="-1" max="32" step="1" class="input font-mono" />
              </div>
              <div class="field">
                <label class="label" for="t-nl">叶子数 <Info :size="12" class="inline text-subtle" title="num_leaves：每棵树叶子节点数，越大模型容量越大" /></label>
                <input id="t-nl" v-model.number="cfg.numLeaves" type="number" min="2" max="256" step="1" class="input font-mono" />
              </div>
              <div class="field">
                <label class="label" for="t-ss">行采样 <Info :size="12" class="inline text-subtle" title="subsample：每轮训练随机用多少比例样本，防过拟合" /></label>
                <input id="t-ss" v-model.number="cfg.subsample" type="number" min="0.1" max="1" step="0.05" class="input font-mono" />
              </div>
              <div class="field">
                <label class="label" for="t-cs">列采样 <Info :size="12" class="inline text-subtle" title="colsample_bytree：每棵树随机用多少比例特征，防过拟合" /></label>
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
            <label class="label" for="t-target">目标（体检通过线） <Info :size="12" class="inline text-subtle" title="用于提示本轮结果是否达到期望，不影响训练本身" /></label>
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

          <Panel title="保存与下一步" sub="把体检通过的模型落库，去「模型库」一键转策略回测 / 部署模拟盘">
            <div class="flex gap-2">
              <input v-model="mName" class="input h-9 flex-1 font-mono text-xs" placeholder="模型名称，如 btc-1d-ridge" maxlength="120" />
              <button class="btn btn-primary !h-9" :disabled="saving || !mName.trim()" @click="saveModel"><LoaderCircle v-if="saving" :size="14" class="animate-spin" />保存模型</button>
            </div>
            <p v-if="!isRidge" class="mt-2 text-[11px] text-warn">LightGBM 模型暂不可保存/打分/回测，仅用于研究对比；如需落库请切换 Ridge。</p>
          </Panel>
        </template>
      </div>
    </div>
  </div>
</template>
