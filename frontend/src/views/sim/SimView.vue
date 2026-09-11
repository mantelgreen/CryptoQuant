<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ArrowDownRight, ArrowUpRight, LoaderCircle, Plus, RefreshCw } from '@lucide/vue'
import PageHeader from '@/components/ui/PageHeader.vue'
import HelpDrawer, { type PageHelp } from '@/components/ui/HelpDrawer.vue'
import Panel from '@/components/ui/Panel.vue'
import Modal from '@/components/ui/Modal.vue'
import StateView from '@/components/ui/StateView.vue'
import EquityChart from '@/components/chart/EquityChart.vue'
import { useToastStore } from '@/stores/toast'
import {
  createAccount,
  fetchOverview,
  getAccount,
  listAccounts,
  placeOrder,
  createAutoAccount,
  tickAutoAccount,
  fetchComparison,
  type SimAccount,
  type SimDetail,
  type SimOverviewRow,
  type CompGroup,
} from '@/api/sim'
import { listMlModels, type MlModelRow } from '@/api/factor'
import Sparkline from '@/components/ui/Sparkline.vue'
import MultiLineChart from '@/components/chart/MultiLineChart.vue'
import { fmtPrice, fmtSigned, timeAgo, MAJOR_SYMBOLS } from '@/utils/format'

const pageHelp: PageHelp = {
  title: '模拟交易 · 手动账户',
  intro: '用真实行情在“纸上”买卖，零风险验证自己的手动交易想法；后续将与策略化/LLM 三路对比。',
  terms: [
    { term: '手动模拟账户', desc: '初始资金 $100,000 的虚拟账户，买卖按请求时刻的币安最新价成交，不花真钱。' },
    { term: '撮合与费用', desc: '市价单=现价立即成交；每笔收 0.1% 手续费（现货 taker），默认不做空。' },
    { term: '持仓 / 浮盈', desc: '持仓=买入数量减卖出数量（加权平均成本）；未实现盈亏=(现价-均价)×数量。' },
    { term: '净值曲线', desc: '净值=现金+持仓市值，每次下单后记一个点；与策略回测用同一张曲线表，未来可同图对比。' },
  ],
  tips: ['先建账户，再用 BTCUSDT 试小额买卖看手续费与净值变化。', '想模拟策略/回测信号 → 先用 ML 模型建策略，再对拍净值。', 'LLM 自动交易与三路对比为下一里程碑。'],
}

const toast = useToastStore()
const accounts = ref<SimAccount[]>([])
const overviewRows = ref<SimOverviewRow[]>([])
const activeId = ref<string>('')
const detail = ref<SimDetail | null>(null)
const loading = ref(false)
const newName = ref('')
const creating = ref(false)
const autoTickBusy = ref(false)
const compGroups = ref<CompGroup[]>([])

const typeMeta: Record<string, { label: string; cls: string }> = {
  manual: { label: '手动', cls: 'tone-neutral' },
  strategy: { label: '策略自动', cls: 'tone-primary' },
  llm: { label: 'LLM', cls: 'tone-ai' },
}
const typeColor: Record<string, string> = { manual: '#16a34a', strategy: '#0e7490', llm: '#c96a2b' }
const overlaySeries = computed(() =>
  compGroups.value
    .filter((g) => g.points.length >= 2)
    .map((g) => ({ name: g.label, color: typeColor[g.type] ?? '#0e7490', points: g.points })),
)

// —— 创建策略自动账户 ——
const autoOpen = ref(false)
const autoModels = ref<MlModelRow[]>([])
const autoLoading = ref(false)
const autoBusy = ref(false)
const autoForm = ref({ name: '', model_id: '', window: 60, enter_q: 0.8, exit_q: 0.2 })

async function openAuto() {
  autoOpen.value = true
  autoLoading.value = true
  autoModels.value = []
  try {
    autoModels.value = await listMlModels()
    if (autoModels.value.length) {
      autoForm.value.model_id = autoModels.value[0].id
      autoForm.value.name = `auto-${(autoModels.value[0].symbols?.[0] ?? 'btc').toLowerCase()}`
    }
  } catch {
    toast.error('读取模型失败，请先在模型训练页保存模型')
  } finally {
    autoLoading.value = false
  }
}

async function submitAuto() {
  const m = autoModels.value.find((x) => x.id === autoForm.value.model_id)
  if (!m || autoBusy.value) return
  autoBusy.value = true
  try {
    const sym = m.symbols?.[0] ?? 'BTCUSDT'
    const acct = await createAutoAccount({
      name: autoForm.value.name.trim() || `${sym}-auto`,
      symbol: sym,
      interval: m.interval,
      model_id: m.id,
      window: autoForm.value.window,
      enter_q: autoForm.value.enter_q,
      exit_q: autoForm.value.exit_q,
    })
    toast.success('策略自动账户已创建', `${acct.name}（${m.interval} · ${m.name}）`)
    autoOpen.value = false
    await loadAccounts()
    activeId.value = acct.id
    await loadDetail()
  } catch (e: unknown) {
    toast.error('创建失败', (e as { message?: string }).message)
  } finally {
    autoBusy.value = false
  }
}

async function tickSelected() {
  if (!activeId.value || autoTickBusy.value) return
  autoTickBusy.value = true
  try {
    const r = await tickAutoAccount(activeId.value)
    if (r.action === 'hold' || r.action === 'skip') {
      const th = r.rule?.type === 'quantile' ? ` · 分位 ${r.rule.q_lo}~${r.rule.q_hi}` : ''
      toast.info('自动决策', `${r.action} · score ${r.score ?? r.message ?? ''}${th}`)
    } else toast.success(r.action === 'open' ? '自动开仓' : '自动平仓', `成交 @ ${r.order?.fill_price}`)
    await loadDetail()
    await loadAccounts()
  } catch (e: unknown) {
    toast.error('自动执行失败', (e as { message?: string }).message)
  } finally {
    autoTickBusy.value = false
  }
}

const sumTotal = computed(() => {
  let cash = 0
  let equity = 0
  for (const r of overviewRows.value) {
    cash += r.current_cash
    equity += r.last_equity ?? r.current_cash
  }
  return { cash: round2(cash), equity: round2(equity), orders: overviewRows.value.reduce((s, r) => s + r.orders, 0) }
})
function round2(v: number): number {
  return Math.round(v * 100) / 100
}

const orderForm = ref({ symbol: 'BTCUSDT', side: 'buy' as 'buy' | 'sell', qty: 0.01 })
const ordering = ref(false)

const activeAccount = computed(() => accounts.value.find((a) => a.id === activeId.value) ?? null)
const equityPoints = computed(() =>
  (detail.value?.equity ?? []).map((p) => ({ ts: p.ts, value: p.equity })),
)
const unrealized = computed(() =>
  (detail.value?.positions ?? []).reduce((s, p) => s + p.unrealized_pnl, 0),
)

async function loadAccounts() {
  accounts.value = await listAccounts()
  try {
    overviewRows.value = await fetchOverview()
  } catch {
    overviewRows.value = []
  }
  try {
    compGroups.value = await fetchComparison()
  } catch {
    compGroups.value = []
  }
  if (!activeId.value && accounts.value.length) activeId.value = accounts.value[0].id
  if (activeId.value) await loadDetail()
}

async function loadDetail() {
  if (!activeId.value) {
    detail.value = null
    return
  }
  loading.value = true
  try {
    detail.value = await getAccount(activeId.value)
  } catch (e: unknown) {
    toast.error('账户加载失败', (e as { message?: string }).message)
  } finally {
    loading.value = false
  }
}

async function selectAccount(id: string) {
  activeId.value = id
  await loadDetail()
}

async function makeAccount() {
  if (!newName.value.trim() || creating.value) return
  creating.value = true
  try {
    const acct = await createAccount(newName.value.trim())
    newName.value = ''
    await loadAccounts()
    activeId.value = acct.id
    await loadDetail()
    toast.success('账户已创建', `初始资金 $100,000`)
  } catch (e: unknown) {
    toast.error('创建失败', (e as { message?: string }).message)
  } finally {
    creating.value = false
  }
}

async function submitOrder() {
  if (!activeId.value || ordering.value) return
  ordering.value = true
  try {
    const r = await placeOrder(activeId.value, {
      symbol: orderForm.value.symbol.trim().toUpperCase(),
      side: orderForm.value.side,
      qty: orderForm.value.qty,
    })
    toast.success(
      orderForm.value.side === 'buy' ? '买入成交' : '卖出成交',
      `${orderForm.value.symbol.toUpperCase()} @ ${fmtPrice(r.fill_price)} · 净值 $${r.equity.toFixed(2)}`,
    )
    await loadDetail()
  } catch (e: unknown) {
    toast.error('下单失败', (e as { message?: string }).message)
  } finally {
    ordering.value = false
  }
}

function pnlTone(v: number): string {
  return v > 0 ? 'text-up' : v < 0 ? 'text-down' : 'text-muted'
}

onMounted(loadAccounts)
</script>

<template>
  <div class="flex flex-col gap-6">
    <PageHeader
      eyebrow="Paper Trading"
      title="模拟交易 · 手动"
      desc="真实行情 · 纸面成交 · 零风险。先练手感，再对比策略。"
    >
      <template #actions>
        <HelpDrawer :help="pageHelp" />
        <button v-if="detail && detail.account.account_type === 'strategy'" class="btn btn-soft" :disabled="autoTickBusy" @click="tickSelected">
          <LoaderCircle v-if="autoTickBusy" :size="14" class="animate-spin" />执行一次自动决策
        </button>
        <button class="btn btn-ghost" @click="openAuto">策略自动账户</button>
        <button class="btn btn-ghost" :disabled="loading" @click="loadDetail"><RefreshCw :size="15" :class="{ 'animate-spin-slow': loading }" />刷新</button>
      </template>
    </PageHeader>

    <div v-if="overviewRows.length" class="grid grid-cols-2 gap-3 sm:grid-cols-4">
      <div class="panel px-4 py-3">
        <p class="text-[11px] uppercase tracking-wider text-subtle">账户数</p>
        <p class="num mt-1 text-xl font-semibold text-fg">{{ overviewRows.length }}</p>
      </div>
      <div class="panel px-4 py-3">
        <p class="text-[11px] uppercase tracking-wider text-subtle">可用现金合计</p>
        <p class="num mt-1 text-xl font-semibold text-fg">${{ sumTotal.cash.toLocaleString(undefined, { maximumFractionDigits: 0 }) }}</p>
      </div>
      <div class="panel px-4 py-3">
        <p class="text-[11px] uppercase tracking-wider text-subtle">最新总净值</p>
        <p class="num mt-1 text-xl font-semibold" :class="sumTotal.equity >= overviewRows.reduce((s, r) => s + r.initial_cash, 0) ? 'text-up' : 'text-down'">
          ${{ sumTotal.equity.toLocaleString(undefined, { maximumFractionDigits: 0 }) }}
        </p>
      </div>
      <div class="panel px-4 py-3">
        <p class="text-[11px] uppercase tracking-wider text-subtle">累计成交</p>
        <p class="num mt-1 text-xl font-semibold text-fg">{{ sumTotal.orders }}</p>
      </div>
    </div>

    <div class="flex flex-wrap items-center gap-2">
      <button
        v-for="a in accounts"
        :key="a.id"
        class="chip !h-10 px-3"
        :class="{ 'is-active': activeId === a.id }"
        @click="selectAccount(a.id)"
      >
        <span>{{ a.name }}</span>
        <span class="badge" :class="typeMeta[a.account_type]?.cls ?? 'tone-neutral'">{{ typeMeta[a.account_type]?.label ?? a.account_type }}</span>
        <span class="num text-[11px] text-muted">${{ a.current_cash.toLocaleString() }}</span>
      </button>
      <span class="w-px self-stretch bg-border" />
      <div class="flex items-center gap-2">
        <input v-model="newName" class="input h-10 w-48" placeholder="新账户名称…" maxlength="80" @keydown.enter="makeAccount" />
        <button class="btn btn-primary !h-10" :disabled="creating || !newName.trim()" @click="makeAccount">
          <Plus :size="15" />建账户
        </button>
      </div>
    </div>

    <StateView v-if="!loading && !accounts.length" icon="wallet" title="还没有模拟账户" desc="创建一个 $100,000 的纸面账户开始手动交易。">
      <template #action>
        <button class="btn btn-primary" @click="makeAccount"><Plus :size="15" />创建账户</button>
      </template>
    </StateView>

    <template v-else-if="detail">
      <div class="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <div class="panel px-4 py-3.5">
          <p class="text-[11px] uppercase tracking-wider text-subtle">账户净值</p>
          <p class="num mt-1 text-2xl font-semibold text-fg">${{ detail.total_equity.toFixed(2) }}</p>
        </div>
        <div class="panel px-4 py-3.5">
          <p class="text-[11px] uppercase tracking-wider text-subtle">可用现金</p>
          <p class="num mt-1 text-2xl font-semibold text-fg">${{ detail.account.current_cash.toFixed(2) }}</p>
        </div>
        <div class="panel px-4 py-3.5">
          <p class="text-[11px] uppercase tracking-wider text-subtle">未实现盈亏</p>
          <p class="num mt-1 text-2xl font-semibold" :class="pnlTone(unrealized)">
            {{ unrealized >= 0 ? '+' : '' }}{{ unrealized.toFixed(2) }}
          </p>
        </div>
        <div class="panel px-4 py-3.5">
          <p class="text-[11px] uppercase tracking-wider text-subtle">持仓数 / 成交</p>
          <p class="num mt-1 text-2xl font-semibold text-fg">{{ detail.positions.length }} / {{ detail.orders.length }}</p>
        </div>
      </div>

      <div class="grid grid-cols-1 gap-6 xl:grid-cols-5">
        <div class="flex flex-col gap-6 xl:col-span-2">
          <Panel title="下单" :sub="`${activeAccount?.name} · 现价市价撮合 · 手续费 0.1%`">
            <div class="grid grid-cols-2 gap-3">
              <div class="field">
                <label class="label" for="o-sym">交易对</label>
                <input id="o-sym" v-model="orderForm.symbol" list="o-syms" class="input font-mono uppercase" spellcheck="false" />
                <datalist id="o-syms">
                  <option v-for="s in MAJOR_SYMBOLS" :key="s" :value="s" />
                </datalist>
              </div>
              <div class="field">
                <label class="label" for="o-qty">数量</label>
                <input id="o-qty" v-model.number="orderForm.qty" type="number" min="0" step="any" class="input font-mono" />
              </div>
            </div>
            <div class="mt-3 flex gap-1.5">
              <button class="chip !h-9 flex-1 !border-0" :class="{ 'is-active': orderForm.side === 'buy' }" @click="orderForm.side = 'buy'">买入</button>
              <button class="chip !h-9 flex-1 !border-0" :class="{ 'is-active': orderForm.side === 'sell' }" @click="orderForm.side = 'sell'">卖出</button>
            </div>
            <button
              class="btn mt-3 w-full !h-10"
              :class="orderForm.side === 'buy' ? 'btn-primary' : 'btn-danger-ghost'"
              :disabled="ordering || !activeId || !orderForm.symbol || !(orderForm.qty > 0)"
              @click="submitOrder"
            >
              {{ ordering ? '撮合中…' : orderForm.side === 'buy' ? '买入成交' : '卖出成交' }}
            </button>
          </Panel>

          <Panel title="持仓" :sub="'现价估值'" :pad="'none'" flush>
            <div v-if="!detail.positions.length" class="py-4"><StateView icon="empty" title="暂无持仓" desc="买入后这里会按现价实时估值。" /></div>
            <div v-else class="overflow-x-auto">
              <table class="tbl">
                <thead>
                  <tr><th>交易对</th><th class="text-right">数量</th><th class="text-right">均价</th><th class="text-right">现价</th><th class="text-right">市值</th><th class="text-right">浮盈</th></tr>
                </thead>
                <tbody>
                  <tr v-for="p in detail.positions" :key="p.symbol">
                    <td class="font-semibold text-fg">{{ p.symbol }}</td>
                    <td class="num tbl-num text-muted">{{ p.qty.toFixed(4) }}</td>
                    <td class="num tbl-num text-muted">{{ fmtPrice(p.avg) }}</td>
                    <td class="num tbl-num text-fg">{{ fmtPrice(p.mark) }}</td>
                    <td class="num tbl-num text-fg">${{ p.value.toFixed(2) }}</td>
                    <td class="num tbl-num font-semibold" :class="pnlTone(p.unrealized_pnl)">
                      <span class="flex items-center justify-end gap-1">
                        <component :is="p.unrealized_pnl >= 0 ? ArrowUpRight : ArrowDownRight" :size="12" />
                        {{ fmtSigned(p.unrealized_pnl, 2) }}
                      </span>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </Panel>
        </div>

        <div class="flex flex-col gap-6 xl:col-span-3">
          <Panel title="净值曲线" sub="每次下单后记录 · 与回测同表同图对比（后续三路对比用）">
            <EquityChart v-if="equityPoints.length" :points="equityPoints" :baseline="detail.account.initial_cash" :height="300" />
            <StateView v-else icon="chart" title="还没有净值点" desc="完成第一笔成交后曲线出现。" />
          </Panel>

          <Panel title="成交记录" :pad="'none'" flush>
            <div v-if="!detail.orders.length" class="py-4"><StateView icon="empty" title="暂无成交" /></div>
            <div v-else class="max-h-80 overflow-auto">
              <table class="tbl">
                <thead>
                  <tr><th>时间</th><th class="text-right">方向</th><th class="text-right">交易对</th><th class="text-right">成交价</th><th class="text-right">数量</th><th class="text-right">手续费</th></tr>
                </thead>
                <tbody>
                  <tr v-for="o in detail.orders" :key="o.id">
                    <td class="tbl-sub font-mono">{{ timeAgo(o.created_at) }}</td>
                    <td><span class="badge" :class="o.side === 'buy' ? 'tone-up' : 'tone-down'">{{ o.side === 'buy' ? '买入' : '卖出' }}</span></td>
                    <td class="num tbl-num text-fg">{{ o.symbol }}</td>
                    <td class="num tbl-num text-fg">{{ fmtPrice(o.filled_price ?? o.price) }}</td>
                    <td class="num tbl-num text-muted">{{ o.qty.toFixed(4) }}</td>
                    <td class="num tbl-num text-subtle">{{ o.fee.toFixed(4) }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </Panel>
        </div>
      </div>
    </template>

    <!-- 三路对比 -->
    <Panel v-if="overlaySeries.length" title="三路对比 · 净值叠加" sub="手动 / 策略自动 / LLM 的净值同轴叠加（按最近记录时间对齐）">
      <MultiLineChart :series="overlaySeries" :height="300" />
    </Panel>

    <Panel v-if="compGroups.length" title="分路快照" sub="按账户类型聚合的最近净值" :pad="'none'" flush>
      <div class="grid grid-cols-1 gap-px bg-border md:grid-cols-3">
        <div v-for="g in compGroups" :key="g.type" class="bg-surface px-4 py-4">
          <div class="flex items-center gap-2">
            <span class="badge" :class="typeMeta[g.type]?.cls ?? 'tone-neutral'">{{ g.label }}</span>
            <span class="ml-auto text-xs text-subtle">{{ g.accounts.map((a) => a.name).join('、') }}</span>
          </div>
          <p class="num mt-2 text-2xl font-semibold" :class="g.latest != null && g.latest >= 0 ? 'text-fg' : 'text-muted'">
            {{ g.latest != null ? '$' + g.latest.toLocaleString(undefined, { maximumFractionDigits: 0 }) : '—' }}
          </p>
          <div v-if="g.points.length >= 2" class="mt-2">
            <Sparkline :points="g.points.slice(-90).map((p) => p.value)" :width="240" :height="36" />
          </div>
          <p v-else class="mt-2 text-[11px] text-subtle">暂无净值点（先成交或执行一次自动决策）</p>
        </div>
      </div>
    </Panel>

    <!-- 创建策略自动账户 -->
    <Modal :open="autoOpen" title="策略自动账户（ML 驱动）" sub="选一个已保存模型，成为该币的纸面自动交易者" width="600px" @close="autoOpen = false">
      <div v-if="autoLoading" class="py-8"><StateView busy title="读取模型…" /></div>
      <div v-else-if="!autoModels.length" class="py-6"><StateView icon="empty" title="还没有模型" desc="请先在「模型训练」保存模型，再来创建自动账户。" /></div>
      <template v-else>
        <p class="mb-2 text-xs font-semibold uppercase tracking-wider text-subtle">选择模型（自动沿用其 interval 与标的）</p>
        <div class="grid grid-cols-1 gap-1.5 sm:grid-cols-2">
          <button
            v-for="m in autoModels"
            :key="m.id"
            class="flex items-center gap-2 rounded-lg border px-3 py-2 text-left text-xs"
            :class="autoForm.model_id === m.id ? 'border-primary tint-faint' : 'border-border'"
            @click="autoForm.model_id = m.id; autoForm.name = `auto-${(m.symbols?.[0] ?? 'btc').toLowerCase()}`"
          >
            <span class="min-w-0 flex-1">
              <span class="block truncate font-semibold text-fg">{{ m.name }}</span>
              <span class="num text-[10px] text-subtle">{{ m.interval }} · h{{ m.horizon }} · {{ (m.symbols ?? []).join(', ') }}</span>
            </span>
          </button>
        </div>
        <div class="mt-4 grid grid-cols-3 gap-3">
          <div class="field sm:col-span-1"><label class="label" for="an-name">账户名</label><input id="an-name" v-model="autoForm.name" class="input font-mono" maxlength="80" /></div>
          <div class="field"><label class="label" for="an-window">滚动窗口</label><input id="an-window" v-model.number="autoForm.window" type="number" step="1" min="20" max="240" class="input font-mono" /></div>
          <div class="field"><label class="label" for="an-enter-q">入场分位</label><input id="an-enter-q" v-model.number="autoForm.enter_q" type="number" step="0.05" min="0.5" max="0.95" class="input font-mono" /></div>
        </div>
        <div class="mt-3 grid grid-cols-3 gap-3">
          <div class="field"><label class="label" for="an-exit-q">离场分位</label><input id="an-exit-q" v-model.number="autoForm.exit_q" type="number" step="0.05" min="0.05" max="0.5" class="input font-mono" /></div>
        </div>
        <p class="mt-2 text-[11px] text-subtle">与回测同一套规则：score 高于滚动 enter_q 分位做多、低于 exit_q 分位平仓（现价撮合、0.1% 手续费）。</p>
      </template>
      <template #footer>
        <div class="flex justify-end gap-2">
          <button class="btn btn-ghost" @click="autoOpen = false">取消</button>
          <button class="btn btn-primary" :disabled="autoBusy || !autoModels.length || !autoForm.model_id" @click="submitAuto">
            <LoaderCircle v-if="autoBusy" :size="14" class="animate-spin" />创建账户
          </button>
        </div>
      </template>
    </Modal>
  </div>
</template>
