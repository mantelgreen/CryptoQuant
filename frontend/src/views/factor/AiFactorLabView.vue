<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { ArrowRight, Bot, Check, CheckSquare, ChevronRight, Copy, Cpu, Dices, Download, FlaskConical, Info, LoaderCircle, RefreshCw, Search, Sparkles, User } from '@lucide/vue'
import PageHeader from '@/components/ui/PageHeader.vue'
import HelpDrawer, { type PageHelp } from '@/components/ui/HelpDrawer.vue'
import StateView from '@/components/ui/StateView.vue'
import { useToastStore } from '@/stores/toast'
import {
  researchFactors,
  trainMl,
  joinFactor,
  verifyExpression,
  type FactorResearch,
  type MlTrainResult,
} from '@/api/factor'
import { generateFactorDraft, generateFactorIdea } from '@/api/agent'

const pageHelp: PageHelp = {
  title: 'AI 因子工坊',
  intro: '用自然语言描述市场规律，让 Agent 起草因子代码；通过校验的因子进入「生成记录」，勾选后做多因子研究，再一键导入因子库。',
  terms: [
    { term: '生成记录', desc: '所有通过沙箱校验的因子草稿都会列在这里，可勾选参与多因子研究，也可单独「实测 IC」或「入库」。' },
    { term: '多因子研究', desc: '对勾选的因子批量跑 IC/RankIC，自动筛选出高绩效因子生成推荐，可直接入库；多因子模型训练是可选的第二步，用于看组合样本外表现。' },
    { term: '一键导入', desc: '研究后推荐出的高绩效因子，可一键批量加入因子库（draft 状态），再到因子库实测/管理。' },
    { term: 'AST 白名单 / 沙箱', desc: '放行 K 线列、受限方法与内置指标（macd/rsi/atr/obv/布林/KDJ/均线…），禁止 import/任意函数，保证研究安全。' },
  ],
  tips: ['先用“研究目标”给 AI 定边界，回答会更聚焦。', '没思路点「AI 随机需求」生成灵感。', '多因子研究只看勾选的因子，记得先在生成记录里勾选。'],
}

const toast = useToastStore()

interface Draft {
  name: string
  code: string
  desc: string
  verified?: boolean
  verifyErrors?: string[]
  verifyWarnings?: string[]
}
interface Msg {
  role: 'user' | 'ai'
  text?: string
  draft?: Draft
  hint?: string
}

interface GenFactor {
  key: number
  name: string
  code: string
  description: string
  source_model?: string
  verified: boolean
  verifyErrors?: string[]
  verifyWarnings?: string[]
  ic: number | null
  rank_ic: number | null
  n_total: number | null
  message?: string
  verifying: boolean
  imported: boolean
  selected: boolean
}

const thread = ref<Msg[]>([
  { role: 'ai', hint: '● 蓝色为数据，橙色为智能 —— 这里是因子工坊，把模糊的想法变成可回测的代码。' },
  { role: 'ai', text: '你好，我是因子研究员。告诉我你想捕捉的行情特征，或直接给目标（如“年化≥20%、回撤≤15%”）。通过校验的因子会进入右侧「生成记录」，勾选后即可多因子研究。' },
])
const input = ref('')
const typing = ref(false)
const busy = ref(false)
const ideaLoading = ref(false)
const suggestions = ['抓 BTC 急跌后的超跌反弹', '低波动后的方向突破', '资金净流入持续加速的币种', '目标：年化≥20%，回撤≤15%']

let genKey = 0
const generated = ref<GenFactor[]>([])

function pushGenerated(draft: Draft, sourceModel?: string) {
  if (!draft.verified) return
  const dup = generated.value.find((g) => g.code === draft.code)
  if (dup) return
  generated.value.unshift({
    key: ++genKey,
    name: draft.name,
    code: draft.code,
    description: draft.desc || '',
    source_model: sourceModel,
    verified: true,
    verifyErrors: draft.verifyErrors,
    verifyWarnings: draft.verifyWarnings,
    ic: null,
    rank_ic: null,
    n_total: null,
    verifying: false,
    imported: false,
    selected: true,
  })
}

async function genIdea() {
  if (ideaLoading.value) return
  ideaLoading.value = true
  try {
    const r = await generateFactorIdea()
    input.value = r.idea
    toast.ai('随机需求已生成', r.idea)
  } catch (e: unknown) {
    toast.error('灵感生成失败', (e as { message?: string }).message)
  } finally {
    ideaLoading.value = false
  }
}

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
      draft: {
        name: gd.name,
        code: gd.code,
        desc: gd.description || '',
        verified: gd.verified,
        verifyErrors: gd.verify_errors,
        verifyWarnings: gd.verify_warnings,
      },
      hint: gd.verified ? '已通过沙箱预校验，已加入右侧「生成记录」' : '未通过沙箱预校验，见下方错误',
    })
    pushGenerated(
      { name: gd.name, code: gd.code, desc: gd.description || '', verified: gd.verified, verifyErrors: gd.verify_errors, verifyWarnings: gd.verify_warnings },
      gd.model,
    )
  } catch (e: unknown) {
    const msg = (e as { message?: string }).message ?? '未知错误'
    thread.value.push({
      role: 'ai',
      text: `生成失败：${msg}`,
      hint:
        /思维|推理|未返回|max_tokens|输出为空/.test(msg)
          ? '若是思考型模型（DeepSeek v4 / Kimi / Qwen / R1），请到「模型设置」勾选“关闭思维模式”或调大 max_tokens 后重试。'
          : '若提示未配置供应商，请先到「模型设置」配置并启用默认供应商。',
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
    last.draft = {
      name: gd.name,
      code: gd.code,
      desc: gd.description || '',
      verified: gd.verified,
      verifyErrors: gd.verify_errors,
      verifyWarnings: gd.verify_warnings,
    }
    last.hint = gd.verified ? `重写版（${gd.model}）· 已通过沙箱预校验，已加入生成记录` : `重写版（${gd.model}）· 未通过沙箱预校验`
    pushGenerated(
      { name: gd.name, code: gd.code, desc: gd.description || '', verified: gd.verified, verifyErrors: gd.verify_errors, verifyWarnings: gd.verify_warnings },
      gd.model,
    )
    toast.ai('已用模型重写', gd.name)
  } catch (e: unknown) {
    const msg = (e as { message?: string }).message ?? '未知错误'
    toast.error('重写失败', /思维|推理|未返回|max_tokens|输出为空/.test(msg) ? `${msg}（可在「模型设置」勾选关闭思维模式）` : msg)
  } finally {
    busy.value = false
  }
}

function copyDraft(msg: Msg) {
  if (!msg.draft) return
  navigator.clipboard?.writeText(msg.draft.code).then(
    () => toast.info('代码已复制', msg.draft?.name),
    () => toast.error('复制失败', '请手动复制'),
  )
}

// —— 生成记录：实测 IC / 入库 ——
async function verifyGen(g: GenFactor) {
  if (g.verifying) return
  g.verifying = true
  g.ic = null
  g.rank_ic = null
  g.n_total = null
  g.message = undefined
  try {
    const end = new Date()
    const start = new Date(end.getTime() - 365 * 24 * 3600 * 1000)
    // 草稿/生成因子尚未入库，必须用表达式实测接口（不落库）
    const r = await verifyExpression({
      expression: g.code,
      symbol: 'BTCUSDT',
      interval: '1d',
      start: start.toISOString(),
      end: end.toISOString(),
    })
    if (r.found) {
      g.ic = r.ic
      g.rank_ic = r.rank_ic
      g.n_total = r.n_total
      g.message = r.message
      if (r.ic != null) {
        toast.info('实测完成', `${g.name} · IC ${r.ic} · RankIC ${r.rank_ic ?? '—'}`)
      } else {
        toast.info('实测完成（IC 无区分度）', r.message ?? '窗口内无有效 IC，详见卡片提示')
      }
    } else {
      g.message = r.message ?? r.errors?.[0] ?? '实测失败'
      toast.error('实测未通过', g.message)
    }
  } catch (e: unknown) {
    toast.error('实测失败', (e as { message?: string }).message)
  } finally {
    g.verifying = false
  }
}

async function importGen(g: GenFactor) {
  if (g.imported) return
  try {
    await joinFactor({ name: g.name, description: g.description, expression: g.code, source_model: g.source_model })
    g.imported = true
    toast.success('已加入因子库', `${g.name}（draft）`)
  } catch (e: unknown) {
    toast.error('加入失败', (e as { message?: string }).message)
  }
}

function toggleSelect(g: GenFactor) {
  g.selected = !g.selected
}

function recordOf(m: Msg): GenFactor | undefined {
  if (!m.draft) return undefined
  return generated.value.find((g) => g.code === m.draft!.code)
}

function numTone(v: number | null | undefined): string {
  return (v ?? 0) >= 0 ? 'text-up' : 'text-down'
}
function fmtNum(v: number | null | undefined): string {
  return v == null ? '—' : (v >= 0 ? '+' : '') + v.toFixed(3)
}
const selectedCount = computed(() => generated.value.filter((g) => g.selected).length)

// —— 生成记录：搜索 / 阶段筛选 / 行展开（记录会持续累积）——
type RecStage = 'pending' | 'measured' | 'imported'
type RecFilter = 'all' | RecStage
const recQ = ref('')
const recFilter = ref<RecFilter>('all')
const recOpen = ref<Set<number>>(new Set())
const labTab = ref<'records' | 'research'>('records')

// —— 左对话 / 右研究台 可拖拽分栏 ——
const isWide = ref(false)
const chatFrac = ref(0.34)
const dragging = ref(false)
const splitRoot = ref<HTMLElement | null>(null)
const chatWpx = computed(() => {
  if (!isWide.value || !splitRoot.value) return 0
  return Math.max(280, Math.round(splitRoot.value.clientWidth * chatFrac.value))
})
function clampFrac(v: number) {
  return Math.min(0.6, Math.max(0.22, v))
}
let _splitCleanup: (() => void) | null = null
onMounted(() => {
  const mq = window.matchMedia('(min-width: 1280px)')
  const apply = () => (isWide.value = mq.matches)
  apply()
  mq.addEventListener('change', apply)
  _splitCleanup = () => mq.removeEventListener('change', apply)
})
onBeforeUnmount(() => _splitCleanup?.())

function splitStart(e: PointerEvent) {
  if (!isWide.value || !splitRoot.value) return
  dragging.value = true
  document.body.style.cursor = 'col-resize'
  document.body.style.userSelect = 'none'
  splitRoot.value.setPointerCapture?.(e.pointerId)
  window.addEventListener('pointermove', splitMove)
  window.addEventListener('pointerup', splitEnd, { once: true })
}
function splitMove(e: PointerEvent) {
  if (!dragging.value || !splitRoot.value) return
  const rect = splitRoot.value.getBoundingClientRect()
  const frac = (e.clientX - rect.left) / rect.width
  chatFrac.value = clampFrac(frac)
}
function splitEnd() {
  dragging.value = false
  document.body.style.cursor = ''
  document.body.style.userSelect = ''
  window.removeEventListener('pointermove', splitMove)
}
function splitNudge(delta: number) {
  chatFrac.value = clampFrac(chatFrac.value + delta)
}

function stageOf(g: GenFactor): RecStage {
  if (g.imported) return 'imported'
  if (g.ic != null || g.n_total != null) return 'measured'
  return 'pending'
}
const recTabs: { key: RecFilter; label: string }[] = [
  { key: 'all', label: '全部' },
  { key: 'pending', label: '待实测' },
  { key: 'measured', label: '已实测' },
  { key: 'imported', label: '已入库' },
]
const recCounts = computed<Record<RecFilter, number>>(() => {
  const all = generated.value
  return {
    all: all.length,
    pending: all.filter((g) => stageOf(g) === 'pending').length,
    measured: all.filter((g) => stageOf(g) === 'measured').length,
    imported: all.filter((g) => g.imported).length,
  }
})
const recVisible = computed(() => {
  const kw = recQ.value.trim().toLowerCase()
  return generated.value.filter((g) => {
    if (recFilter.value !== 'all' && stageOf(g) !== recFilter.value) return false
    if (!kw) return true
    return (
      g.name.toLowerCase().includes(kw) ||
      g.code.toLowerCase().includes(kw) ||
      (g.description ?? '').toLowerCase().includes(kw) ||
      (g.source_model ?? '').toLowerCase().includes(kw)
    )
  })
})
const shownAllSelected = computed(() => recVisible.value.length > 0 && recVisible.value.every((g) => g.selected))
function toggleSelectShown() {
  const target = !shownAllSelected.value
  for (const g of recVisible.value) g.selected = target
}
function isOpen(g: GenFactor): boolean {
  return recOpen.value.has(g.key)
}
function toggleOpen(g: GenFactor) {
  const next = new Set(recOpen.value)
  if (next.has(g.key)) next.delete(g.key)
  else next.add(g.key)
  recOpen.value = next
}
function isMeasured(g: GenFactor): boolean {
  return g.ic != null || g.n_total != null
}
function copyCode(g: GenFactor) {
  navigator.clipboard?.writeText(g.code).then(
    () => toast.info('代码已复制', g.name),
    () => toast.error('复制失败', '请手动复制'),
  )
}

// —— 多因子研究（对勾选的生成因子）——
// 第一步“因子体检”只批量算 IC/RankIC → 筛出高绩效因子 → 推荐入库，不训练模型；
// 多因子模型训练是第二步“可选项”，仅当你想要组合级样本外评价时再跑。
const objCfg = ref({ symbol: 'BTCUSDT', interval: '1d', days: 400 })
const objRunning = ref(false)
const objSearched = ref(false)
const objBlocked = ref<string[]>([])
const recommended = ref<GenFactor[]>([])
const recommendedNote = ref('')

const mlRunning = ref(false)
const mlPhase = ref('')
const objResult = ref<MlTrainResult | null>(null)

interface ObjStep {
  label: string
  state: 'pending' | 'active' | 'done'
}
const objSteps = ref<ObjStep[]>([
  { label: '批量体检选中因子的 IC/RankIC', state: 'pending' },
  { label: '按表现筛选因子', state: 'pending' },
  { label: '生成推荐', state: 'pending' },
])
function setStep(idx: number, state: ObjStep['state']) {
  objSteps.value = objSteps.value.map((s, i) => (i === idx ? { ...s, state } : s))
}

const objVerdict = computed(() => {
  const r = objResult.value
  if (!r) return null
  if (!r.found) return { tone: 'tone-warn', text: r.message ?? '样本不足或训练失败' }
  const ok = r.spread > 0 && (r.oos_rank_ic ?? 0) > 0.01
  return ok
    ? { tone: 'tone-up', text: '样本外为正：因子组合初步有效，建议回测验证' }
    : { tone: 'tone-warn', text: '未达目标：扩大样本或更换因子再试' }
})

async function runObjective() {
  if (objRunning.value || mlRunning.value) return
  const candidates = generated.value.filter((g) => g.selected)
  if (candidates.length < 2) {
    toast.error('请先勾选因子', '至少勾选 2 个「生成记录」中的因子再开始因子体检')
    return
  }
  objRunning.value = true
  objSearched.value = false
  objResult.value = null
  recommended.value = []
  recommendedNote.value = ''
  objBlocked.value = []
  objSteps.value = objSteps.value.map((s) => ({ ...s, state: 'pending' }))
  const end = new Date()
  const start = new Date(end.getTime() - objCfg.value.days * 24 * 3600 * 1000)
  try {
    setStep(0, 'active')
    const reses: FactorResearch[] = await researchFactors({
      codes: candidates.map((g) => g.code),
      symbols: [objCfg.value.symbol.trim().toUpperCase()],
      interval: objCfg.value.interval,
      start: start.toISOString(),
      end: end.toISOString(),
    })
    // 把体检结果回填到生成记录，并挑选“有信号”的因子
    const byCode = new Map(candidates.map((g) => [g.code, g]))
    const usable: Array<{ g: GenFactor; score: number }> = []
    const blocked: string[] = []
    for (const r of reses) {
      const g = byCode.get(r.code)
      if (!g) continue
      g.message = r.message
      if (!r.found) {
        blocked.push(`${g.name}：${r.message ?? '计算失败'}`)
        continue
      }
      g.ic = r.ic
      g.rank_ic = r.rank_ic
      g.n_total = r.n_total
      if (r.ic != null || r.rank_ic != null) {
        usable.push({ g, score: Math.abs(r.rank_ic ?? r.ic ?? 0) })
      } else {
        blocked.push(`${g.name}：${r.message ?? '窗口内几乎恒定，无有效 IC/RankIC'}`)
      }
    }
    setStep(0, 'done')
    objBlocked.value = blocked
    if (!usable.length) {
      toast.error('没有可用因子', '所选因子在本窗口都算不出有效 IC/RankIC，原因见下方研究结果')
      objSearched.value = true
      setStep(2, 'done')
      return
    }
    setStep(1, 'active')
    const sorted = [...usable].sort((a, b) => b.score - a.score)
    const strong = sorted.filter((x) => x.score >= 0.02)
    recommended.value = (strong.length ? strong : sorted.slice(0, 3)).map((x) => x.g)
    recommendedNote.value = strong.length
      ? ''
      : '本轮没有达到 |RankIC/IC| ≥ 0.02 的因子，暂按表现前 3 名推荐，仅供参照。'
    setStep(1, 'done')
    setStep(2, 'done')
    objSearched.value = true
    toast.success('因子体检完成', `可用 ${usable.length} 个，已推荐 ${recommended.value.length} 个高绩效因子，可一键入库`)
  } catch (e: unknown) {
    toast.error('体检失败', (e as { message?: string }).message)
    setStep(2, 'done')
  } finally {
    objRunning.value = false
  }
}

async function trainRecommended() {
  if (mlRunning.value || objRunning.value) return
  const set = recommended.value
  if (set.length < 2) {
    toast.error('推荐不足 2 个', '多因子模型至少需要 2 个因子，先在「生成记录」勾选并完成因子体检')
    return
  }
  mlRunning.value = true
  objResult.value = null
  mlPhase.value = '正在加载 K 线与训练多因子模型…'
  const end = new Date()
  const start = new Date(end.getTime() - objCfg.value.days * 24 * 3600 * 1000)
  try {
    const ml = await trainMl({
      codes: set.map((g) => g.code).slice(0, 8),
      symbols: [objCfg.value.symbol.trim().toUpperCase()],
      interval: objCfg.value.interval,
      start: start.toISOString(),
      end: end.toISOString(),
      horizon: 1,
      test_ratio: 0.25,
      alpha: 1,
    })
    objResult.value = ml
    if (ml.found && ml.spread > 0 && (ml.oos_rank_ic ?? 0) > 0.01) {
      toast.success('模型训练完成', `组合样本外为正，OOS RankIC ${ml.oos_rank_ic}`)
    } else {
      toast.info('模型训练完成（未达标）', ml.message ?? '建议扩大样本或更换因子')
    }
  } catch (e: unknown) {
    toast.error('训练失败', (e as { message?: string }).message)
  } finally {
    mlRunning.value = false
    mlPhase.value = ''
  }
}

async function importRecommended() {
  const targets = recommended.value.filter((g) => !g.imported)
  if (!targets.length) {
    toast.info('已全部导入', '推荐因子都已在因子库中')
    return
  }
  let ok = 0
  for (const g of targets) {
    try {
      await joinFactor({ name: g.name, description: g.description, expression: g.code, source_model: g.source_model })
      g.imported = true
      ok += 1
    } catch {
      /* 单个失败不中断，最后汇总 */
    }
  }
  if (ok) toast.success('一键导入完成', `已将 ${ok} 个高绩效因子加入因子库`)
  else toast.error('导入失败', '未能导入任何因子，请检查后端')
}

const chatBody = ref<HTMLElement | null>(null)
function scrollBottom() {
  nextTick(() => {
    chatBody.value?.scrollTo({ top: chatBody.value.scrollHeight, behavior: 'smooth' })
  })
}
</script>

<template>
  <div class="flex flex-col gap-6">
    <PageHeader
      eyebrow="AI Factor Lab"
      title="AI 因子工坊"
      desc="用自然语言描述市场规律，让 Agent 起草因子；通过校验的进入「生成记录」，勾选后多因子研究，高绩效因子一键入库。"
    >
      <template #actions>
        <HelpDrawer :help="pageHelp" />
        <router-link to="/factor" class="btn btn-ghost">因子库</router-link>
        <span class="badge tone-ai"><Sparkles :size="11" />AgentScope 待接入</span>
      </template>
    </PageHeader>
    <div ref="splitRoot" class="flex flex-col gap-6 xl:flex-row xl:items-stretch xl:gap-0">

      <!-- 对话主区（默认约 1/3，可拖拽调整） -->
      <div
        class="panel flex max-h-[78vh] min-w-0 shrink-0 flex-col"
        :style="isWide ? { width: chatWpx + 'px' } : undefined"
      >
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
                <div
                  v-if="m.draft.verified === false"
                  class="border-b border-border bg-inset px-3.5 py-1.5 text-[11px] text-down"
                >
                  沙箱预校验未通过：{{ m.draft.verifyErrors?.[0] ?? '未知' }}
                </div>
                <div
                  v-else-if="m.draft.verified"
                  class="border-b border-border bg-inset px-3.5 py-1.5 text-[11px] text-up"
                >
                  沙箱预校验通过，已加入「生成记录」<span v-if="m.draft.verifyWarnings?.length">（{{ m.draft.verifyWarnings[0] }}）</span>
                </div>
                <div class="flex flex-wrap items-center gap-2 px-3.5 py-2.5">
                  <button class="btn btn-soft !h-8 !px-3 text-xs" :disabled="busy" @click="regenerateLastDraft">
                    <LoaderCircle v-if="busy" :size="13" class="animate-spin" />
                    <RefreshCw v-else :size="13" />再调一版
                  </button>
                  <button class="btn btn-ghost !h-8 !px-3 text-xs" @click="copyDraft(m)"><Copy :size="13" />复制</button>

                  <!-- 实测 IC（与右侧生成记录同一因子、数值同步） -->
                  <span v-if="recordOf(m)" class="ml-auto flex items-center gap-1.5">
                    <button
                      v-if="recordOf(m)!.ic == null && recordOf(m)!.n_total == null"
                      class="btn btn-ghost !h-8 !px-2.5 text-xs"
                      :title="recordOf(m)!.message ?? '对 BTCUSDT/1d/近一年样本实测 IC 与 RankIC'"
                      :disabled="recordOf(m)!.verifying || busy"
                      @click="verifyGen(recordOf(m)!)"
                    >
                      <LoaderCircle v-if="recordOf(m)!.verifying" :size="12" class="animate-spin" />
                      <FlaskConical v-else :size="12" />
                      {{ recordOf(m)!.verifying ? '实测中…' : '实测 IC' }}
                    </button>
                    <template v-else>
                      <span
                        class="flex items-center gap-1.5 rounded-lg border px-2 py-1.5 text-[11px]"
                        :class="recordOf(m)!.ic == null ? 'border-border bg-surface-2/40' : 'border-border bg-surface-2/60'"
                        :title="recordOf(m)!.message ?? undefined"
                      >
                        <span class="text-subtle">实测 IC</span>
                        <span class="num font-semibold" :class="numTone(recordOf(m)!.ic)">{{ fmtNum(recordOf(m)!.ic) }}</span>
                        <span class="text-subtle">RankIC</span>
                        <span class="num font-semibold" :class="numTone(recordOf(m)!.rank_ic)">{{ fmtNum(recordOf(m)!.rank_ic) }}</span>
                        <span class="num text-subtle">n={{ recordOf(m)!.n_total ?? '—' }}</span>
                        <span v-if="recordOf(m)!.ic == null && recordOf(m)!.message" class="text-warn">{{ recordOf(m)!.message }}</span>
                      </span>
                      <button
                        class="btn btn-icon !h-8 !w-8 text-subtle"
                        :title="recordOf(m)!.message ? '上次实测未通过：' + recordOf(m)!.message : '重新实测'"
                        :disabled="recordOf(m)!.verifying"
                        @click="verifyGen(recordOf(m)!)"
                      >
                        <LoaderCircle v-if="recordOf(m)!.verifying" :size="12" class="animate-spin" />
                        <RefreshCw v-else :size="12" />
                      </button>
                    </template>
                  </span>
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
            <button class="chip !h-7 text-xs" :disabled="ideaLoading" @click="genIdea">
              <LoaderCircle v-if="ideaLoading" :size="12" class="animate-spin" />
              <Dices v-else :size="12" />
              AI 随机需求
            </button>
          </div>
          <form class="flex items-end gap-2" @submit.prevent="send()">
            <textarea
              v-model="input"
              rows="1"
              class="input min-h-[42px] flex-1 resize-none py-2.5"
              placeholder="描述想捕捉的行情特征，或直接给目标指标；没思路可点「AI 随机需求」…"
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

      <!-- 分隔手柄：拖拽调整左右宽度（xl 起显示） -->
      <div
        v-if="isWide"
        class="group flex shrink-0 cursor-col-resize touch-none select-none items-center px-3"
        role="separator"
        aria-orientation="vertical"
        tabindex="0"
        :aria-valuenow="Math.round(chatFrac * 100)"
        aria-valuemin="22"
        aria-valuemax="60"
        title="拖拽调整对话与研究台宽度"
        @pointerdown.prevent="splitStart"
        @keydown.left.prevent="splitNudge(-0.02)"
        @keydown.right.prevent="splitNudge(0.02)"
      >
        <div
          class="h-full w-[3px] rounded-full transition-colors"
          :class="dragging ? 'bg-primary' : 'bg-border group-hover:bg-primary/60'"
        />
      </div>

      <!-- 右区：因子研究台（生成记录 + 多因子研究 = 一张卡，占 2/3） -->
      <section class="panel flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
        <header class="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-border px-3 py-2">
          <nav class="flex items-center gap-0.5 rounded-lg bg-surface-2 p-0.5" role="tablist" aria-label="研究台功能">
            <button
              role="tab"
              :aria-selected="labTab === 'records'"
              class="flex h-7 items-center gap-1.5 rounded-md px-3 text-xs font-medium transition-colors"
              :class="labTab === 'records' ? 'bg-surface text-fg shadow-sm' : 'text-muted hover:text-fg'"
              @click="labTab = 'records'"
            >
              生成记录
              <span class="num text-[10px] opacity-80">{{ generated.length }}</span>
            </button>
            <button
              role="tab"
              :aria-selected="labTab === 'research'"
              class="flex h-7 items-center gap-1.5 rounded-md px-3 text-xs font-medium transition-colors"
              :class="labTab === 'research' ? 'bg-surface text-fg shadow-sm' : 'text-muted hover:text-fg'"
              @click="labTab = 'research'"
            >
              多因子研究
              <span v-if="selectedCount >= 2" class="h-1.5 w-1.5 rounded-full bg-up" title="已选因子可研究" />
            </button>
          </nav>
          <div class="ml-auto hidden items-center text-[11px] text-subtle sm:flex">
            <template v-if="labTab === 'records'">
              已勾选 <span class="num text-fg">{{ selectedCount }}</span> 个
              <span v-if="selectedCount >= 2" class="ml-1.5 text-up">可开始多因子研究</span>
            </template>
            <template v-else-if="objResult">
              <span class="badge" :class="objVerdict?.tone">{{ objVerdict?.text }}</span>
            </template>
            <template v-else>勾选因子 → 因子体检 → 推荐 → 入库（模型训练可选）</template>
          </div>
        </header>

        <!-- 功能页 1：生成记录 -->
        <div v-show="labTab === 'records'" class="flex min-h-0 flex-col">
          <StateView v-if="!generated.length" icon="spark" title="还没有生成记录" desc="在左侧描述行情特征生成因子，通过沙箱校验后会自动出现在这里。" />

          <template v-else>
            <!-- 工具条：搜索 + 计数筛选 + 全选 -->
            <div class="border-b border-border px-3 py-2">
              <div class="flex items-center gap-1.5">
                <div class="relative min-w-0 flex-1">
                  <Search :size="12" class="pointer-events-none absolute left-2 top-1/2 -translate-y-1/2 text-subtle" />
                  <input v-model="recQ" type="search" placeholder="搜名称 / 代码 / 模型" class="input !h-7 !rounded-md pl-7 text-xs" />
                </div>
                <button class="btn btn-ghost !h-7 !px-2 text-[11px]" :disabled="!generated.length || !recVisible.length" @click="toggleSelectShown">
                  <CheckSquare :size="12" />{{ shownAllSelected ? '取消全选' : '全选' }}
                </button>
              </div>
              <div class="mt-1.5 flex flex-wrap items-center gap-1">
                <button
                  v-for="t in recTabs"
                  :key="t.key"
                  class="chip !h-6 !px-2 text-[11px]"
                  :class="recFilter === t.key ? 'is-active' : ''"
                  @click="recFilter = t.key"
                >
                  {{ t.label }}
                  <span class="num opacity-80">{{ recCounts[t.key] }}</span>
                </button>
              </div>
            </div>

            <div v-if="!recVisible.length" class="flex flex-col items-center gap-2 px-3 py-6 text-center">
              <p class="text-xs text-subtle">没有符合条件的记录</p>
              <button class="btn btn-ghost !h-7 !px-2 text-[11px]" @click="recQ = ''; recFilter = 'all'">查看全部</button>
            </div>
            <ul v-else class="min-h-0 max-h-[56vh] space-y-1.5 overflow-y-auto overscroll-contain p-3 xl:max-h-[62vh]">
              <li
                v-for="g in recVisible"
                :key="g.key"
                class="overflow-hidden rounded-lg border bg-surface transition-colors"
                :class="g.selected ? 'border-primary/50' : 'border-border hover:border-strong'"
              >
                <div class="flex items-center gap-2 px-2.5 py-1.5">
                  <label class="flex h-6 w-4 shrink-0 cursor-pointer items-center justify-center">
                    <input type="checkbox" class="accent-[var(--color-primary)]" :checked="g.selected" @change="toggleSelect(g)" />
                  </label>
                  <button class="min-w-0 flex-1 py-0.5 text-left" @click="toggleOpen(g)">
                    <span class="flex items-center gap-1.5">
                      <span class="truncate text-xs font-semibold text-fg">{{ g.name }}</span>
                      <span v-if="g.imported" class="badge tone-up !h-[15px] !px-1 text-[9px]">已入库</span>
                      <span v-else-if="isMeasured(g)" class="badge tone-primary !h-[15px] !px-1 text-[9px]">已实测</span>
                      <ChevronRight :size="11" class="ml-auto shrink-0 text-subtle transition-transform duration-200" :class="isOpen(g) ? 'rotate-90' : ''" />
                    </span>
                    <span class="mt-0.5 flex items-center gap-2">
                      <span class="min-w-0 flex-1 truncate font-mono text-[10px] text-subtle" :title="g.code">{{ g.code }}</span>
                      <span v-if="isMeasured(g)" class="num shrink-0 text-[10px]" :title="g.message ?? '实测 IC / RankIC'">
                        IC <span :class="numTone(g.ic)">{{ fmtNum(g.ic) }}</span>
                        <span class="text-subtle"> / R</span> <span :class="numTone(g.rank_ic)">{{ fmtNum(g.rank_ic) }}</span>
                      </span>
                    </span>
                  </button>
                  <span class="flex shrink-0 items-center gap-1">
                    <button
                      v-if="!g.imported"
                      class="btn btn-icon !h-6 !w-6 !rounded-md text-muted"
                      :class="isMeasured(g) ? '' : 'border border-border'"
                      :title="isMeasured(g) ? '重新实测' : '实测 IC'"
                      :disabled="g.verifying"
                      @click="verifyGen(g)"
                    >
                      <LoaderCircle v-if="g.verifying" :size="11" class="animate-spin" />
                      <FlaskConical v-else :size="11" />
                    </button>
                    <button
                      v-if="!g.imported"
                      class="btn btn-icon !h-6 !w-6 !rounded-md border border-border text-muted hover:border-strong hover:text-fg"
                      title="加入因子库"
                      @click="importGen(g)"
                    >
                      <Download :size="11" />
                    </button>
                  </span>
                </div>

                <transition name="pop">
                  <div v-if="isOpen(g)" class="border-t border-border px-2.5 py-2">
                    <p v-if="g.description" class="text-[11px] leading-relaxed text-muted">{{ g.description }}</p>
                    <p v-if="g.message" class="mt-1 text-[11px] leading-relaxed" :class="g.ic == null ? 'text-warn' : 'text-subtle'">{{ g.message }}</p>
                    <div class="mt-2 rounded-md bg-inset px-2.5 py-2 font-mono text-[10.5px] leading-relaxed text-fg">{{ g.code }}</div>
                    <div class="mt-2 flex flex-wrap items-center gap-1.5">
                      <button v-if="!g.imported" class="btn btn-ghost !h-7 !px-2.5 text-[11px]" :disabled="g.verifying" @click="verifyGen(g)">
                        <LoaderCircle v-if="g.verifying" :size="11" class="animate-spin" /><FlaskConical v-else :size="11" />
                        {{ g.verifying ? '实测中…' : isMeasured(g) ? '重新实测' : '实测 IC' }}
                      </button>
                      <button v-if="!g.imported" class="btn btn-soft !h-7 !px-2.5 text-[11px]" @click="importGen(g)"><Download :size="11" />入库</button>
                      <span v-else class="badge tone-up"><Check :size="10" />已入库</span>
                      <span v-if="g.source_model" class="ml-auto text-[10px] text-subtle">模型 {{ g.source_model }}</span>
                      <button class="btn btn-icon !h-7 !w-7 text-subtle" title="复制代码" @click="copyCode(g)"><Copy :size="11" /></button>
                    </div>
                  </div>
                </transition>
              </li>
            </ul>
          </template>
        </div>

        <!-- 功能页 2：多因子研究（体检 → 推荐 → 可选模型训练） -->
        <div v-show="labTab === 'research'" class="min-h-0 flex-1 px-4 py-3.5">
          <div class="space-y-3">
            <div class="grid grid-cols-1 gap-2.5 sm:grid-cols-3">
              <div class="field">
                <label class="label" for="obj-sym">标的</label>
                <input id="obj-sym" v-model="objCfg.symbol" class="input font-mono uppercase" spellcheck="false" />
              </div>
              <div class="field">
                <label class="label" for="obj-int">粒度</label>
                <select id="obj-int" v-model="objCfg.interval" class="select font-mono">
                  <option value="15m">15m</option><option value="30m">30m</option><option value="1h">1h</option><option value="1d">1d</option>
                </select>
              </div>
              <div class="field">
                <label class="label" for="obj-days">样本天数</label>
                <input id="obj-days" v-model.number="objCfg.days" type="number" min="90" step="30" class="input font-mono" />
              </div>
            </div>

            <button class="btn btn-primary w-full" :disabled="objRunning || mlRunning || selectedCount < 2" @click="runObjective">
              <LoaderCircle v-if="objRunning" :size="15" class="animate-spin" />
              <CheckSquare v-else :size="15" />
              {{ objRunning ? '体检中…' : `开始因子体检（已选 ${selectedCount} 个）` }}
            </button>
            <p v-if="selectedCount < 2" class="text-[11px] text-subtle">请先在「生成记录」勾选至少 2 个因子。</p>

            <!-- 体检步骤进度 -->
            <div v-if="objRunning || objSearched" class="space-y-1.5">
              <ul class="space-y-1">
                <li v-for="s in objSteps" :key="s.label" class="flex items-center gap-2 text-xs">
                  <span
                    class="flex h-4 w-4 shrink-0 items-center justify-center rounded-full border text-[9px]"
                    :class="s.state === 'done' ? 'border-up text-up' : s.state === 'active' ? 'border-primary text-primary' : 'border-border text-subtle'"
                  >
                    <Check v-if="s.state === 'done'" :size="10" />
                    <LoaderCircle v-else-if="s.state === 'active'" :size="10" class="animate-spin" />
                    <span v-else>·</span>
                  </span>
                  <span :class="s.state === 'pending' ? 'text-subtle' : 'text-fg'">{{ s.label }}</span>
                </li>
              </ul>
            </div>

            <!-- 未纳入推荐的因子及原因（研究结果明细，不进 toast） -->
            <div v-if="objSearched && objBlocked.length" class="rounded-lg border border-border bg-surface-2/50 px-3 py-2">
              <p class="text-[11px] font-semibold text-fg">未纳入推荐的因子（{{ objBlocked.length }}）</p>
              <ul class="mt-1.5 space-y-1">
                <li v-for="(b, i) in objBlocked" :key="i" class="text-[11px] leading-relaxed text-warn">{{ b }}</li>
              </ul>
            </div>

            <!-- 推荐因子 -->
            <div v-if="recommended.length" class="space-y-2 border-t border-border pt-3">
              <div class="flex flex-wrap items-center justify-between gap-2">
                <p class="text-xs font-semibold text-fg">推荐的高绩效因子（{{ recommended.length }}）</p>
                <button class="btn btn-soft !h-7 !px-2.5 text-[11px]" @click="importRecommended">
                  <Download :size="12" />一键导入因子库
                </button>
              </div>
              <div v-for="g in recommended" :key="g.key" class="flex items-center gap-2 rounded-lg border border-border bg-surface-2/40 px-2.5 py-2">
                <span class="min-w-0 flex-1 truncate text-xs text-fg">{{ g.name }}</span>
                <span class="num shrink-0 font-mono text-[11px]" :class="numTone(g.rank_ic ?? g.ic)">
                  {{ g.rank_ic != null ? 'RankIC' : 'IC' }} {{ fmtNum(g.rank_ic ?? g.ic) }}
                </span>
                <button class="btn btn-ghost !h-7 !px-2 text-[11px]" :disabled="g.imported" @click="importGen(g)">
                  <Check v-if="g.imported" :size="12" />{{ g.imported ? '已入库' : '入库' }}
                </button>
              </div>
              <p v-if="recommendedNote" class="text-[11px] text-warn">{{ recommendedNote }}</p>
            </div>

            <!-- 可选的模型级评价（不影响入库） -->
            <div v-if="objSearched" class="space-y-2 border-t border-border pt-3">
              <div class="flex flex-wrap items-center justify-between gap-2">
                <div class="min-w-0 flex-1">
                  <p class="text-xs font-semibold text-fg">模型级评价（可选）</p>
                  <p class="text-[11px] text-subtle">用推荐因子训练多因子模型，看组合样本外表现；想直接入库可跳过这一步。</p>
                </div>
                <button
                  class="btn btn-ghost !h-7 !px-2.5 text-[11px]"
                  :disabled="mlRunning || recommended.length < 2"
                  @click="trainRecommended"
                >
                  <LoaderCircle v-if="mlRunning" :size="12" class="animate-spin" />
                  <Cpu v-else :size="12" />
                  {{ mlRunning ? '训练中…' : objResult ? '重新训练模型' : '训练多因子模型' }}
                </button>
              </div>
              <p v-if="recommended.length && recommended.length < 2" class="text-[11px] text-subtle">推荐因子不足 2 个，暂无法训练模型。</p>
              <p v-if="mlRunning && mlPhase" class="text-[11px] text-subtle">{{ mlPhase }}</p>

              <!-- 训练结果 -->
              <div v-if="objResult" class="space-y-2.5 pt-1">
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
                <p v-if="objVerdict" class="badge" :class="objVerdict.tone">{{ objVerdict.text }}</p>
              </div>
            </div>
          </div>

          <p class="mt-3.5 flex items-start gap-1.5 border-t border-border pt-2.5 text-[11px] leading-relaxed text-subtle">
            <Info :size="13" class="mt-0.5 shrink-0 text-ai-ink" />
            <span>因子体检只批量计算 IC/RankIC 并推荐，无需训练模型即可入库；模型级评价可选，用于查看因子组合的样本外表现。</span>
          </p>
        </div>
      </section>
    </div>
  </div>
</template>
