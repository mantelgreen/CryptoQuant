<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref } from 'vue'
import { ArrowRight, Bot, Check, Copy, FlaskConical, Info, LoaderCircle, Plus, RefreshCw, Sparkles, User } from '@lucide/vue'
import PageHeader from '@/components/ui/PageHeader.vue'
import HelpDrawer, { type PageHelp } from '@/components/ui/HelpDrawer.vue'
import Panel from '@/components/ui/Panel.vue'
import { useToastStore } from '@/stores/toast'
import { demoFactors } from '@/mock/factors'
import { verifyExpression, researchFactors, trainMl, type VerifyResult, type FactorResearch, type MlTrainResult } from '@/api/factor'
import { generateFactorDraft } from '@/api/agent'
import { joinFactor } from '@/api/factor'

const FACTOR_CODES = ['momentum_12_1', 'rsi_oversold_rev', 'vol_squeeze', 'obv_slope', 'day_range_pos', 'volume_ratio']

const pageHelp: PageHelp = {
  title: 'AI 因子工坊',
  intro: '用自然语言描述一个市场规律，把它变成“可实测的因子表达式”。生成对话为示例，实测链路已真实可用。',
  terms: [
    { term: '因子表达式', desc: '直接对 K 线列运算：如 (close - close.shift(10))/close 表示 10 期动量。系统会用 AST 白名单校验再实测。' },
    { term: 'AST 白名单 / 沙箱', desc: '只放行 K 线列与受限方法（shift/rolling/mean/diff/pct_change…），禁止 import/任意函数，恶意代码会被拦截，保证研究安全。' },
    { term: '实测 IC', desc: '草稿会被送到沙箱，在 BTCUSDT 1d 上跑 IC/RankIC。不通过的写法会给出明确原因（如方法不在白名单）。' },
    { term: 'AgentScope（规划）', desc: '大模型 Agent 框架，规划中用于真正“自动产出”因子；当前对话草稿为示例，待接入后生成结果走同一条沙箱+实测+入库链路。' },
    { term: '状态 draft→active', desc: '草稿需经你确认后再成为正式因子库成员；AI 生成内容全程可追溯。' },
  ],
  tips: ['先用“研究目标”给 AI 定边界，回答会更聚焦。', '点「实测 IC」比口头判断靠谱——统计说了算。', '想快速出可用写法，可参考因子库内置因子的公式风格。'],
}

const toast = useToastStore()

interface Draft {
  name: string
  code: string
  desc: string
}
interface Msg {
  role: 'user' | 'ai'
  text?: string
  draft?: Draft
  hint?: string
  verify?: VerifyResult
}

const thread = ref<Msg[]>([
  {
    role: 'ai',
    hint: '● 蓝色为数据，橙色为智能 —— 这里是因子工坊，把模糊的想法变成可回测的代码。',
  },
  {
    role: 'ai',
    text: '你好，我是因子研究员。告诉我你想捕捉的行情特征（例如“低波动后突破”“超跌反弹”），或者直接给一个目标：如“年化 ≥ 20%、回撤 ≤ 15%”。我会用你配置的模型生成因子草稿，校验后供你入库。',
  },
])
const input = ref('')
const typing = ref(false)
const busy = ref(false)
const suggestions = ['抓 BTC 急跌后的超跌反弹', '低波动后的方向突破', '资金净流入持续加速的币种', '目标：年化≥20%，回撤≤15%']

async function send(text?: string) {
  const content = (text ?? input.value).trim()
  if (!content || typing.value || busy.value) return
  input.value = ''
  thread.value.push({ role: 'user', text: content })
  typing.value = true
  scrollBottom()

  try {
    const gd = await generateFactorDraft(content)
    thread.value.push({
      role: 'ai',
      text: `基于「${content.slice(0, 18)}」由 ${gd.model} 生成${gd.description ? `：${gd.description}` : ''}`,
      draft: { name: gd.name, code: gd.code, desc: gd.description || '' },
      hint: '真实 LLM 草稿 · 可点「实测 IC」进白名单沙箱验证',
    })
  } catch (e: unknown) {
    const msg = (e as { message?: string }).message ?? '未知错误'
    thread.value.push({
      role: 'ai',
      text: '生成失败：请先到「模型设置」配置并启用默认供应商（支持本地 Ollama）。',
      hint: msg,
    })
  } finally {
    typing.value = false
    scrollBottom()
  }
}

async function regenerateLastDraft() {
  const last = thread.value[thread.value.length - 1]
  if (!last?.draft || busy.value) return
  const prompt = [...thread.value].reverse().find((m) => m.role === 'user')?.text
  if (!prompt) return
  busy.value = true
  try {
    const gd = await generateFactorDraft(prompt)
    last.draft = { name: gd.name, code: gd.code, desc: gd.description || '' }
    last.verify = undefined
    last.hint = `重写版（${gd.model}）· 可「实测 IC」验证`
    toast.ai('已用模型重写', gd.name)
  } catch (e: unknown) {
    toast.error('重写失败', (e as { message?: string }).message)
  } finally {
    busy.value = false
  }
}

async function addDraft(msg: Msg) {
  if (!msg.draft) return
  try {
    const res = await joinFactor({
      name: msg.draft.name,
      description: msg.draft.desc,
      expression: msg.draft.code,
      source_model: msg.text?.match(/由 ([^\s]+) 生成/)?.[1] ?? undefined,
    })
    toast.success('已加入因子库', `${msg.draft.name}（draft）可在因子库实测/管理`)
  } catch (e: unknown) {
    toast.error('加入失败', (e as { message?: string }).message)
  }
}
function copyDraft(msg: Msg) {
  if (!msg.draft) return
  navigator.clipboard?.writeText(msg.draft.code).then(
    () => toast.info('代码已复制', msg.draft?.name),
    () => toast.error('复制失败', '请手动复制'),
  )
}

const verifyingKey = ref<number | null>(null)
async function verifyDraft(idx: number, msg: Msg) {
  if (!msg.draft || verifyingKey.value !== null) return
  verifyingKey.value = idx
  try {
    const end = new Date()
    const start = new Date(end.getTime() - 365 * 24 * 3600 * 1000)
    const res = await verifyExpression({
      expression: msg.draft.code,
      symbol: 'BTCUSDT',
      interval: '1d',
      start: start.toISOString(),
      end: end.toISOString(),
    })
    msg.verify = res
    if (res.ok) toast.info('实测完成', `IC ${res.ic ?? '—'} · RankIC ${res.rank_ic ?? '—'}（n=${res.n_total}）`)
    else toast.error('校验未通过', res.errors?.[0] ?? res.message)
  } catch (e: unknown) {
    toast.error('实测失败', (e as { message?: string }).message)
  } finally {
    verifyingKey.value = null
  }
}

// —— 目标驱动：自动选因子 → ML 训练（真实链路，示例用 BTCUSDT 1d）——
const objRunning = ref(false)
const objResult = ref<MlTrainResult | null>(null)
const objPicked = ref<string[]>([])

async function runObjective() {
  if (objRunning.value) return
  objRunning.value = true
  objResult.value = null
  objPicked.value = []
  const end = new Date()
  const start = new Date(end.getTime() - 400 * 24 * 3600 * 1000)
  try {
    const reses: FactorResearch[] = await researchFactors({
      codes: FACTOR_CODES,
      symbols: ['BTCUSDT'],
      interval: '1d',
      start: start.toISOString(),
      end: end.toISOString(),
    })
    const usable = reses.filter((r) => r.found && r.rank_ic != null)
    const picked = [...usable]
      .sort((a, b) => Math.abs(b.rank_ic as number) - Math.abs(a.rank_ic as number))
      .slice(0, 3)
    if (picked.length < 2) {
      toast.error('样本不足', '无法选出 ≥2 个可用因子，请先补充数据')
      return
    }
    objPicked.value = picked.map((r) => r.code)
    const ml = await trainMl({
      codes: objPicked.value,
      symbols: ['BTCUSDT'],
      interval: '1d',
      start: start.toISOString(),
      end: end.toISOString(),
      horizon: 1,
      test_ratio: 0.25,
      alpha: 1,
    })
    objResult.value = ml
    if (ml.found && ml.spread > 0 && (ml.oos_rank_ic ?? 0) > 0.01) {
      toast.success('目标初步可达', `已选 ${objPicked.value.length} 个因子，OOS RankIC ${ml.oos_rank_ic}`)
    } else {
      toast.info('本轮未达标', '建议扩大样本或更换因子后重试')
    }
  } catch (e: unknown) {
    toast.error('研究失败', (e as { message?: string }).message)
  } finally {
    objRunning.value = false
  }
}

const chatBody = ref<HTMLElement | null>(null)
function scrollBottom() {
  nextTick(() => {
    chatBody.value?.scrollTo({ top: chatBody.value.scrollHeight, behavior: 'smooth' })
  })
}

onBeforeUnmount(() => {
  /* typing timers are short-lived */
})
</script>

<template>
  <div class="flex flex-col gap-6">
    <PageHeader
      eyebrow="AI Factor Lab"
      title="AI 因子工坊"
      desc="用自然语言描述一个市场规律，让 Agent 起草因子代码；你负责校验、迭代与决定是否入库。"
    >
      <template #actions>
        <HelpDrawer :help="pageHelp" />
        <router-link to="/factor" class="btn btn-ghost">因子库</router-link>
        <span class="badge tone-ai"><Sparkles :size="11" />AgentScope 待接入</span>
      </template>
    </PageHeader>

    <div class="grid grid-cols-1 gap-6 xl:grid-cols-3">
      <!-- 对话主区 -->
      <div class="panel flex max-h-[78vh] flex-col xl:col-span-2">
        <div ref="chatBody" class="flex-1 space-y-5 overflow-y-auto px-5 py-5">
          <div
            v-for="(m, i) in thread"
            :key="i"
            class="flex gap-3"
            :class="m.role === 'user' ? 'flex-row-reverse' : ''"
          >
            <span
              class="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg"
              :class="m.role === 'ai' ? 'tint-ai text-ai-ink' : 'tint-primary text-primary-ink dark:text-primary'"
            >
              <Bot v-if="m.role === 'ai'" :size="14" />
              <User v-else :size="14" />
            </span>

            <div class="max-w-[82%] min-w-0" :class="m.role === 'user' ? 'flex flex-col items-end' : ''">
              <div
                class="rounded-2xl px-4 py-2.5 text-sm leading-relaxed"
                :class="m.role === 'user' ? 'rounded-tr-md' : 'rounded-tl-md bg-surface-2 text-fg'"
                :style="m.role === 'user' ? { backgroundColor: 'var(--color-primary-fill)', color: 'var(--color-on-primary)' } : undefined"
              >
                <p>{{ m.text }}</p>
                <p v-if="m.hint" class="mt-1 text-[11px] opacity-70">{{ m.hint }}</p>
              </div>

              <!-- 因子草稿卡片 -->
              <div v-if="m.draft" class="mt-2 w-full overflow-hidden rounded-xl border border-border bg-surface shadow-panel">
                <div class="flex items-center gap-2 border-b border-border px-3.5 py-2.5">
                  <FlaskConical :size="14" class="text-ai" />
                  <span class="text-[13px] font-semibold text-fg">{{ m.draft.name }}</span>
                  <span class="badge tone-ai ml-auto">AI 草稿</span>
                </div>
                <div class="border-b border-border bg-inset px-3.5 py-2 font-mono text-xs text-fg">
                  <span class="text-subtle">code </span>{{ m.draft.code }}
                </div>
                <div class="flex flex-wrap items-center gap-2 px-3.5 py-2.5">
                  <button class="btn btn-soft !h-8 !px-3 text-xs" :disabled="busy" @click="regenerateLastDraft">
                    <LoaderCircle v-if="busy" :size="13" class="animate-spin" />
                    <RefreshCw v-else :size="13" />再调一版
                  </button>
                  <button class="btn btn-ghost !h-8 !px-3 text-xs" @click="copyDraft(m)"><Copy :size="13" />复制</button>
                  <button class="btn btn-ghost !h-8 !px-3 text-xs" :disabled="verifyingKey !== null" @click="verifyDraft(i, m)">
                    <LoaderCircle v-if="verifyingKey === i" :size="13" class="animate-spin" />
                    <FlaskConical v-else :size="13" />实测 IC
                  </button>
                  <button class="btn btn-primary !h-8 !px-3 text-xs" @click="addDraft(m)"><Check :size="13" />加入因子库</button>
                </div>
                <div v-if="m.verify" class="border-t border-border px-3.5 py-2">
                  <p v-if="m.verify.ok" class="num text-[11px] text-muted">
                    实测（BTCUSDT 1d）：IC <span :class="(m.verify.ic ?? 0) >= 0 ? 'text-up' : 'text-down'">{{ m.verify.ic ?? '—' }}</span>
                    · RankIC {{ m.verify.rank_ic ?? '—' }} · n={{ m.verify.n_total }}
                  </p>
                  <p v-if="m.verify.ok && m.verify.message" class="mt-1 text-[11px] text-warn">{{ m.verify.message }}</p>
                  <p v-else-if="!m.verify.ok" class="text-[11px] text-down">校验未通过：{{ m.verify.errors?.[0] ?? m.verify.message }}</p>
                </div>
              </div>
            </div>
          </div>

          <div v-if="typing" class="flex gap-3">
            <span class="flex h-7 w-7 items-center justify-center rounded-lg tint-ai text-ai-ink">
              <Bot :size="14" />
            </span>
            <div class="flex items-center gap-1 rounded-2xl rounded-tl-md bg-surface-2 px-4 py-3">
              <span class="h-1.5 w-1.5 animate-bounce rounded-full bg-muted" style="animation-delay: 0ms" />
              <span class="h-1.5 w-1.5 animate-bounce rounded-full bg-muted" style="animation-delay: 120ms" />
              <span class="h-1.5 w-1.5 animate-bounce rounded-full bg-muted" style="animation-delay: 240ms" />
            </div>
          </div>
        </div>

        <!-- 输入区 -->
        <div class="border-t border-border p-3.5">
          <div class="mb-2.5 flex flex-wrap gap-1.5 px-0.5">
            <button v-for="s in suggestions" :key="s" class="chip !h-7 text-xs" @click="input = s">{{ s }}</button>
          </div>
          <form class="flex items-end gap-2" @submit.prevent="send()">
            <textarea
              v-model="input"
              rows="1"
              class="input min-h-[42px] flex-1 resize-none py-2.5"
              placeholder="描述想捕捉的行情特征，或直接给目标指标…"
              @keydown.enter.exact.prevent="send()"
            />
            <button class="btn btn-primary h-[42px] shrink-0 px-4" type="submit" :disabled="!input.trim() || typing">
              <LoaderCircle v-if="typing" :size="15" class="animate-spin" />
              <ArrowRight v-else :size="16" />
              {{ typing ? '研究中' : '生成' }}
            </button>
          </form>
        </div>
      </div>

      <!-- 右栏：研究目标 + 相关因子 -->
      <div class="flex flex-col gap-4">
        <Panel title="研究目标" sub="目标驱动的多因子研究（示例）">
          <div class="space-y-2.5">
            <div class="flex items-center justify-between rounded-lg border border-border px-3 py-2">
              <span class="text-xs text-muted">目标年化</span>
              <span class="num font-semibold text-fg">≥ 20%</span>
            </div>
            <div class="flex items-center justify-between rounded-lg border border-border px-3 py-2">
              <span class="text-xs text-muted">最大回撤</span>
              <span class="num font-semibold text-fg">≤ 15%</span>
            </div>
            <div class="flex items-center justify-between rounded-lg border border-border px-3 py-2">
              <span class="text-xs text-muted">研究样本</span>
              <span class="num font-mono text-xs font-semibold text-fg">BTC · 1h · 3y</span>
            </div>
            <div class="flex items-center justify-between rounded-lg border border-border px-3 py-2">
              <span class="text-xs text-muted">预算</span>
              <span class="num font-semibold text-fg">≤ 30 轮迭代</span>
            </div>
            <button class="btn btn-primary w-full" :disabled="objRunning" @click="runObjective">
              <LoaderCircle v-if="objRunning" :size="15" class="animate-spin" />
              <Sparkles v-else :size="15" />
              {{ objRunning ? '研究中…' : '开始多因子研究（自动选因子）' }}
            </button>
          </div>

          <div v-if="objResult" class="mt-3 space-y-2.5 border-t border-border pt-3">
            <p class="text-xs text-muted">
              自动选中因子：<span v-for="f in objResult.features" :key="f.code" class="mr-1.5 font-mono text-[11px] text-fg">{{ f.name }}</span>
            </p>
            <div class="grid grid-cols-3 gap-2">
              <div class="rounded-lg bg-surface-2 px-2.5 py-2">
                <p class="text-[10px] uppercase tracking-wider text-subtle">OOS RankIC</p>
                <p class="num text-sm font-semibold" :class="(objResult.oos_rank_ic ?? 0) > 0 ? 'text-up' : 'text-down'">{{ objResult.oos_rank_ic ?? '—' }}</p>
              </div>
              <div class="rounded-lg bg-surface-2 px-2.5 py-2">
                <p class="text-[10px] uppercase tracking-wider text-subtle">命中率</p>
                <p class="num text-sm font-semibold text-fg">{{ ((objResult.hit_rate ?? 0) * 100).toFixed(1) }}%</p>
              </div>
              <div class="rounded-lg bg-surface-2 px-2.5 py-2">
                <p class="text-[10px] uppercase tracking-wider text-subtle">Top−Btm</p>
                <p class="num text-sm font-semibold" :class="objResult.spread > 0 ? 'text-up' : 'text-down'">{{ objResult.spread.toFixed(5) }}</p>
              </div>
            </div>
            <p class="badge" :class="objResult.spread > 0 && (objResult.oos_rank_ic ?? 0) > 0.01 ? 'tone-up' : 'tone-warn'">
              {{ objResult.spread > 0 && (objResult.oos_rank_ic ?? 0) > 0.01 ? '目标初步可达（样本外为正）' : '未达目标：扩大样本或换因子再试' }}
            </p>
          </div>
        </Panel>

        <Panel title="相关因子" sub="与当前草稿相似的候选（pgvector 检索预览）">
          <ul class="space-y-2">
            <li v-for="f in demoFactors.slice(0, 4)" :key="f.id" class="flex items-center gap-2.5">
              <span class="flex h-6 w-6 shrink-0 items-center justify-center rounded-md border border-border bg-surface-2 font-display text-[9px] font-bold text-muted">
                {{ f.id.slice(1) }}
              </span>
              <div class="min-w-0 flex-1">
                <p class="truncate text-[13px] font-medium text-fg">{{ f.name }}</p>
                <p class="text-[10px] text-subtle">RankIC {{ f.rankIc.toFixed(3) }} · {{ f.author }}</p>
              </div>
              <button class="btn btn-icon h-7 w-7 text-subtle hover:text-primary" title="加入研究组合" aria-label="加入" @click="toast.info('已加入候选池（示例）', f.name)">
                <Plus :size="14" />
              </button>
            </li>
          </ul>
        </Panel>

        <div class="flex items-start gap-2.5 rounded-xl px-4 py-3" style="background: color-mix(in srgb, var(--color-ai) 7%, transparent); border: 1px solid color-mix(in srgb, var(--color-ai) 28%, transparent)">
          <Info :size="14" class="mt-0.5 shrink-0 text-ai-ink" />
          <p class="text-xs leading-relaxed text-muted">对话与生成链路示例：生产环境接入 AgentScope 后，因子草稿将经过沙箱校验（AST 白名单 + 子进程 + 时间/内存限制）再入库。</p>
        </div>
      </div>
    </div>
  </div>
</template>
