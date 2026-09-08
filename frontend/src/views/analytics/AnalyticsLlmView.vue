<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Activity, RefreshCw } from '@lucide/vue'
import PageHeader from '@/components/ui/PageHeader.vue'
import HelpDrawer, { type PageHelp } from '@/components/ui/HelpDrawer.vue'
import Panel from '@/components/ui/Panel.vue'
import StateView from '@/components/ui/StateView.vue'
import EquityChart from '@/components/chart/EquityChart.vue'
import { useToastStore } from '@/stores/toast'
import { fetchLlmOverview, fetchLlmTimeseries, type LlmOverview, type LlmTsPoint } from '@/api/analytics'

const pageHelp: PageHelp = {
  title: 'LLM 用量（Token 统计）',
  intro: '汇总所有真实模型调用（AI 工坊、测试连接及后续 Agent）的 token 消耗与成本。',
  terms: [
    { term: 'Token', desc: '大模型的计费单位。每次调用记录 prompt_tokens（输入）与 completion_tokens（输出）。' },
    { term: '成本 cost_usd', desc: '按「模型设置」旁的价格表(llm_costs)单价折算；未配置单价则为 0。' },
    { term: 'caller（调用方）', desc: '记录是哪个能力发起的调用，如 agent.factor_draft / settings.test。' },
    { term: 'latency_ms', desc: '单次请求端到端耗时（毫秒），Ollama 本地模型推理也会计入。' },
  ],
  tips: ['每次真实调用自动落库，无需手动上报。', '想算成本：在 llm_costs 表为 provider+model 维护单价即可。', '本页满足“所有模型调用 token 汇总”需求。'],
}

const toast = useToastStore()
const loading = ref(true)
const overview = ref<LlmOverview | null>(null)
const ts = ref<LlmTsPoint[]>([])
const days = ref(30)

const tsPoints = computed(() => ts.value.map((p) => ({ ts: p.day, value: p.tokens })))
const maxTokens = computed(() => Math.max(...overview.value?.by_model.map((m) => m.tokens) ?? [1], 1))

async function load() {
  loading.value = true
  try {
    const [ov, series] = await Promise.all([fetchLlmOverview(), fetchLlmTimeseries(days.value)])
    overview.value = ov
    ts.value = series
  } catch (e: unknown) {
    toast.error('用量统计加载失败', (e as { message?: string }).message)
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="flex flex-col gap-6">
    <PageHeader
      eyebrow="Analytics · LLM"
      title="LLM 用量"
      desc="所有模型调用（AI 工坊 / 测试 / Agent）的 Token 与成本汇总。"
    >
      <template #actions>
        <HelpDrawer :help="pageHelp" />
        <span class="text-xs text-subtle">数据源：llm_usage（每次调用自动记录）</span>
        <button class="btn btn-ghost" :disabled="loading" @click="load"><RefreshCw :size="15" :class="{ 'animate-spin-slow': loading }" />刷新</button>
      </template>
    </PageHeader>

    <div v-if="!overview || loading" class="panel py-12"><StateView busy title="读取用量…" /></div>
    <template v-else>
      <div class="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <div class="panel px-4 py-3.5">
          <p class="text-[11px] uppercase tracking-wider text-subtle">今日 Tokens</p>
          <p class="num mt-1 text-2xl font-semibold text-fg">{{ overview.today.tokens.toLocaleString() }}</p>
          <p class="mt-0.5 text-[11px] text-muted">{{ overview.today.calls }} 次调用</p>
        </div>
        <div class="panel px-4 py-3.5">
          <p class="text-[11px] uppercase tracking-wider text-subtle">累计 Tokens</p>
          <p class="num mt-1 text-2xl font-semibold text-fg">{{ overview.total.tokens.toLocaleString() }}</p>
          <p class="mt-0.5 text-[11px] text-muted">{{ overview.total.calls }} 次调用</p>
        </div>
        <div class="panel px-4 py-3.5">
          <p class="text-[11px] uppercase tracking-wider text-subtle">成本(USD)</p>
          <p class="num mt-1 text-2xl font-semibold text-fg">${{ overview.total.cost.toFixed(4) }}</p>
          <p class="mt-0.5 text-[11px] text-muted">今日 ${{ overview.today.cost.toFixed(4) }}</p>
        </div>
        <div class="panel px-4 py-3.5">
          <p class="text-[11px] uppercase tracking-wider text-subtle">模型数</p>
          <p class="num mt-1 text-2xl font-semibold text-fg">{{ overview.by_model.length }}</p>
          <p class="mt-0.5 text-[11px] text-muted">调用方 {{ overview.by_caller.length }} 类</p>
        </div>
      </div>

      <div class="grid grid-cols-1 gap-6 xl:grid-cols-3">
        <div class="xl:col-span-2">
          <Panel title="按天 Token 走势" sub="最近每日总 Token（可用底部选择拉长窗口）">
            <template #actions>
              <select v-model.number="days" class="select h-8 w-28 text-xs" @change="load">
                <option :value="7">7 天</option><option :value="30">30 天</option><option :value="90">90 天</option>
              </select>
            </template>
            <EquityChart v-if="tsPoints.length" :points="tsPoints" :height="260" />
            <StateView v-else icon="chart" title="暂无调用记录" desc="在「模型设置」点测试，或在 AI 因子工坊生成一次即可出现数据。" />
          </Panel>

          <div class="mt-6 grid grid-cols-1 gap-6 lg:grid-cols-2">
            <Panel title="按模型" sub="token 与成本分布" :pad="'none'" flush>
              <div v-if="overview.by_model.length" class="space-y-2 p-3">
                <div v-for="m in overview.by_model" :key="m.model" class="flex items-center gap-2 text-xs">
                  <span class="w-32 truncate font-mono text-muted">{{ m.model }}</span>
                  <div class="h-2 min-w-0 flex-1 overflow-hidden rounded-full bg-surface-2">
                    <div class="h-full rounded-full bg-primary" :style="{ width: `${(m.tokens / maxTokens) * 100}%` }" />
                  </div>
                  <span class="num w-20 text-right text-muted">{{ m.tokens.toLocaleString() }} tok</span>
                  <span class="num w-16 text-right text-subtle">${{ m.cost.toFixed(4) }}</span>
                </div>
              </div>
              <StateView v-else icon="empty" title="暂无数据" />
            </Panel>
            <Panel title="按调用方" sub="哪些能力在消耗" :pad="'none'" flush>
              <div v-if="overview.by_caller.length" class="space-y-2 p-3">
                <div v-for="c in overview.by_caller" :key="c.caller" class="flex items-center gap-2 text-xs">
                  <span class="font-mono text-fg">{{ c.caller }}</span>
                  <span class="ml-auto num text-muted">{{ c.calls }} 次 · {{ c.tokens.toLocaleString() }} tok</span>
                </div>
              </div>
              <StateView v-else icon="empty" title="暂无数据" />
            </Panel>
          </div>
        </div>

        <div class="flex flex-col gap-4">
          <Panel title="按天明细" sub="近 {{ days }} 天" :pad="'none'" flush>
            <div class="max-h-80 overflow-auto">
              <table class="tbl">
                <thead>
                  <tr><th>日期</th><th class="text-right">调用</th><th class="text-right">Token</th><th class="text-right">成本</th></tr>
                </thead>
                <tbody>
                  <tr v-for="p in [...ts].reverse()" :key="p.day">
                    <td class="font-mono text-xs text-muted">{{ p.day }}</td>
                    <td class="num tbl-num text-muted">{{ p.calls }}</td>
                    <td class="num tbl-num text-fg">{{ p.tokens.toLocaleString() }}</td>
                    <td class="num tbl-num text-subtle">${{ p.cost.toFixed(4) }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </Panel>
        </div>
      </div>
    </template>
  </div>
</template>
