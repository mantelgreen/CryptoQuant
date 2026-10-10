<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Database, Plus, RefreshCw } from '@lucide/vue'
import { createDownload, listDownloads, type DownloadJob } from '@/api/data'
import PageHeader from '@/components/ui/PageHeader.vue'
import HelpDrawer, { type PageHelp } from '@/components/ui/HelpDrawer.vue'
import Panel from '@/components/ui/Panel.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import StateView from '@/components/ui/StateView.vue'
import { useToastStore } from '@/stores/toast'
import { fmtCompact, fmtStamp, INTERVALS, MAJOR_SYMBOLS, timeAgo } from '@/utils/format'

const pageHelp: PageHelp = {
  title: 'K 线下载（历史数据）',
  intro: '把币安历史行情批量拉进本地时序库，是一切研究（因子/回测）的原料来源。',
  terms: [
    { term: '全量 vs 增量', desc: '全量=按所选起止区间完整拉取；增量=从库里最后一根 K 线自动续到“现在”（无历史则回补近 30 天）。' },
    { term: 'interval 颗粒度', desc: '数据周期：1m/5m/15m/1h/1d…。建议先下 1m 底库，15m/1h/1d 可由平台按时间桶自动聚合。' },
    { term: '新增 / 覆盖', desc: '新增=本次任务真正插入的新行数（已存在的按主键去重跳过，故重复区间常显示 0）；覆盖=该起止区间本地已入库的总根数。1 根 = 一个周期的 OHLCV。' },
    { term: 'gaps 缺口', desc: '币安按每页最多 1000 根返回，若发现相邻两根时间不连续会记录缺口区间，便于重补。' },
    { term: '币安限速（权重）', desc: 'Binance 按“权重/分钟”计数（默认软上限 4500，单次 klines 记 2）。平台内置令牌桶 + 自适应退避，避免触发封 IP。' },
    { term: 'TimescaleDB', desc: '时序数据库。K 线按时间自动切成 7 天的 chunk（桶），写入/查询自动路由，另有 1m→5m/15m/1h/1d 的连续聚合(CA)物化视图。' },
  ],
  tips: ['任务由后台 ARQ worker 执行：worker 未启动则任务只排队不执行。', '下载任务详情里的失败原因通常=区间无数据或网络，按提示到「定时任务/下载」补即可。', '想长期自动更新请在「定时任务」页配置 cron 增量。'],
}

const toast = useToastStore()

const form = ref({
  symbol: 'BTCUSDT',
  interval: '1m',
  mode: 'full' as 'full' | 'incremental',
  start: '',
  end: '',
})

function localDatetime(d: Date): string {
  const p = (x: number) => String(x).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`
}

function defaultRange() {
  const e = new Date()
  const s = new Date(e.getTime() - 7 * 24 * 3600 * 1000)
  form.value.start = localDatetime(s)
  form.value.end = localDatetime(e)
}

const jobs = ref<DownloadJob[]>([])
const submitting = ref(false)
const loading = ref(false)
let pollTimer = 0

const busy = computed(() => jobs.value.some((j) => j.status === 'running' || j.status === 'pending'))

function schedulePoll() {
  if (pollTimer) return
  pollTimer = window.setInterval(() => void load(false), 3000)
}
function stopPoll() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = 0
  }
}
watch(busy, (b) => (b ? schedulePoll() : stopPoll()))

async function load(showSpinner = true) {
  if (showSpinner) loading.value = true
  try {
    jobs.value = await listDownloads()
  } catch (e: unknown) {
    toast.error('任务列表加载失败', (e as { message?: string }).message)
  } finally {
    loading.value = false
  }
}

async function submit() {
  const start = new Date(form.value.start)
  const end = new Date(form.value.end)
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) {
    toast.error('时间范围无效', '开始与结束时间都需要填写')
    return
  }
  if (end <= start) {
    toast.error('时间范围无效', '结束时间须晚于开始时间')
    return
  }
  submitting.value = true
  try {
    const r = await createDownload({
      symbol: form.value.symbol.trim().toUpperCase(),
      interval: form.value.interval,
      start: new Date(form.value.start).toISOString(),
      end: new Date(form.value.end).toISOString(),
      mode: form.value.mode,
    })
    toast.success('下载任务已提交', `job ${r.job_id.slice(0, 8)}… 已进入队列`)
    await load()
    defaultRange()
  } catch (e: unknown) {
    const msg = (e as { message?: string }).message ?? '提交失败'
    toast.error('创建任务失败', msg)
  } finally {
    submitting.value = false
  }
}

function bars(j: DownloadJob): string {
  return j.downloaded_bars ? fmtCompact(j.downloaded_bars) : '0'
}
function covered(j: DownloadJob): string {
  return j.covered_bars ? fmtCompact(j.covered_bars) : '0'
}
function errMsg(j: DownloadJob): string {
  return j.error ? (String(j.error).length > 60 ? String(j.error).slice(0, 60) + '…' : String(j.error)) : ''
}

function focusForm() {
  ;(document.querySelector('#dl-symbol') as HTMLInputElement | null)?.focus()
}

onMounted(async () => {
  defaultRange()
  await load()
  if (busy.value) schedulePoll()
})
onBeforeUnmount(stopPoll)
</script>

<template>
  <div class="flex flex-col gap-6">
    <PageHeader
      eyebrow="Data Library"
      title="K 线下载"
      desc="把币安历史行情按交易对 × 颗粒度批量拉入本地 TimescaleDB。全量或增量、支持断点续传，自动规避限速。"
    >
      <template #actions>
        <HelpDrawer :help="pageHelp" />
        <button class="btn btn-ghost" :disabled="loading" @click="load()">
          <RefreshCw :size="15" :class="{ 'animate-spin-slow': loading }" />
          刷新
        </button>
      </template>
    </PageHeader>

    <Panel title="新建下载任务" sub="时间范围用本地时区填写，提交后转 UTC 入库">
      <form class="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-6" @submit.prevent="submit">
        <div class="field">
          <label class="label" for="dl-symbol">交易对</label>
          <input id="dl-symbol" v-model="form.symbol" list="major-symbols" class="input font-mono uppercase" placeholder="BTCUSDT" spellcheck="false" />
          <datalist id="major-symbols">
            <option v-for="s in MAJOR_SYMBOLS" :key="s" :value="s" />
          </datalist>
        </div>
        <div class="field">
          <label class="label" for="dl-interval">颗粒度</label>
          <select id="dl-interval" v-model="form.interval" class="select font-mono">
            <option v-for="i in INTERVALS" :key="i" :value="i">{{ i }}</option>
          </select>
        </div>
        <div class="field">
          <label class="label" for="dl-start">开始时间</label>
          <input id="dl-start" v-model="form.start" type="datetime-local" class="input" :max="form.end" />
        </div>
        <div class="field">
          <label class="label" for="dl-end">结束时间</label>
          <input id="dl-end" v-model="form.end" type="datetime-local" class="input" :min="form.start" />
        </div>
        <div class="field">
          <label class="label">模式</label>
          <div class="flex h-[34px] items-center gap-1 rounded-[9px] border border-border bg-surface p-0.5">
            <button type="button" class="chip !h-7 !flex-1 !border-0" :class="{ 'is-active': form.mode === 'full' }" @click="form.mode = 'full'">
              全量
            </button>
            <button type="button" class="chip !h-7 !flex-1 !border-0" :class="{ 'is-active': form.mode === 'incremental' }" @click="form.mode = 'incremental'">
              增量
            </button>
          </div>
        </div>
        <div class="flex items-end">
          <button type="submit" class="btn btn-primary w-full xl:w-auto" :disabled="submitting || !form.symbol || !form.start || !form.end">
            <Plus :size="15" />{{ submitting ? '提交中…' : '创建任务' }}
          </button>
        </div>
      </form>
      <p class="mt-3 text-xs leading-relaxed text-subtle">
        {{ form.mode === 'full' ? '全量：按时间窗逐页拉取，缺根会写入 gaps 诊断。' : '增量：从库里最后一根自动续到当前，无需填起止时间（仍按给定窗口执行）。' }}
        限速按 4500 weight/min 软上限 + 自适应退避，不会触发封 IP。
      </p>
    </Panel>

    <Panel title="下载记录" :sub="busy ? '有任务执行中，每 3 秒自动刷新' : '本地与币安同步的数据历史'" :pad="'none'" flush>
      <div v-if="loading && !jobs.length" class="py-16"><StateView busy title="读取任务…" /></div>
      <div v-else-if="!jobs.length" class="py-6">
        <StateView
          icon="database"
          title="还没有下载任务"
          desc="从上表选择币对与时间范围，一键把历史 K 线搬进本地库。"
        >
          <template #action>
            <button class="btn btn-primary" @click="focusForm"><Database :size="15" />开始第一个任务</button>
          </template>
        </StateView>
      </div>
      <div v-else class="overflow-x-auto">
        <table class="tbl">
          <thead>
            <tr>
              <th>Job</th>
              <th>交易对</th>
              <th>粒度</th>
              <th class="text-right">状态</th>
              <th class="text-right" title="本次任务真正新插入的行数（已存在的会去重跳过）">新增</th>
              <th class="text-right" title="该起止区间本地已入库的 K 线根数">覆盖</th>
              <th class="text-right">起止区间</th>
              <th class="text-right">创建</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="j in jobs" :key="j.id">
              <td class="font-mono text-xs text-subtle">{{ j.id.slice(0, 8) }}</td>
              <td><span class="font-semibold text-fg">{{ j.symbol }}</span></td>
              <td class="font-mono text-xs text-muted">{{ j.interval }}</td>
              <td>
                <div class="flex items-center justify-end gap-2">
                  <StatusBadge :status="j.status" />
                  <span v-if="j.status === 'failed' && errMsg(j)" class="max-w-[180px] truncate text-xs text-down" :title="String(j.error)">{{ errMsg(j) }}</span>
                </div>
              </td>
              <td class="num tbl-num text-subtle">{{ bars(j) }}</td>
              <td class="num tbl-num font-medium text-fg">{{ covered(j) }}</td>
              <td class="tbl-num text-xs text-subtle">
                {{ fmtStamp(j.start_time) }}<span class="mx-1 text-strong">→</span>{{ fmtStamp(j.end_time) }}
              </td>
              <td class="tbl-num tbl-sub">{{ timeAgo(j.created_at) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </Panel>
  </div>
</template>
