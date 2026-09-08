<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Clock3, Plus, RefreshCw, Trash2 } from '@lucide/vue'
import { createSchedule, deleteSchedule, listSchedules, type Schedule } from '@/api/data'
import PageHeader from '@/components/ui/PageHeader.vue'
import HelpDrawer, { type PageHelp } from '@/components/ui/HelpDrawer.vue'
import Panel from '@/components/ui/Panel.vue'
import StateView from '@/components/ui/StateView.vue'
import { useToastStore } from '@/stores/toast'
import { timeAgo, INTERVALS } from '@/utils/format'

const pageHelp: PageHelp = {
  title: '定时下载（cron + beat）',
  intro: '把 K 线“保鲜”：按 cron 周期自动增量同步，保证研究始终基于新鲜数据。',
  terms: [
    { term: 'cron 表达式', desc: '5 段：分 时 日 月 周。例：*/5 * * * * = 每 5 分钟；0 0 * * * = 每天 00:00；0 9 * * 1 = 每周一 09:00。' },
    { term: 'beat 调度器', desc: '后台守护进程，每分钟扫一次启用的定时任务，命中 cron 就派发增量同步。未启动则定时任务不会真正执行。' },
    { term: 'ARQ', desc: '本项目用的异步任务队列（Redis 做 broker）。下载、增量、回测都在 worker 里异步跑，不阻塞网关。' },
    { term: '增量同步', desc: '对列表里的每个交易对，从本地最后一根 K 线续到当前时间，属于轻量轮询式维护。' },
    { term: '启用的含义', desc: '列表只显示 enabled 的任务；删除即刻生效。建议频率与 interval 匹配：1m 数据最密每小时/每 5 分钟即可。' },
  ],
  tips: ['先建一条下载任务把“历史”补齐，再加定时做“增量保鲜”。', 'cron 按服务器时区解释（当前按 UTC）。', '跑重了就删掉重建，不影响历史数据。'],
}

const toast = useToastStore()

const form = ref({
  name: '',
  cron: '*/5 * * * *',
  symbolsText: 'BTCUSDT,ETHUSDT',
  interval: '1m',
})

const items = ref<Schedule[]>([])
const submitting = ref(false)
const loading = ref(false)
const removing = ref<string | null>(null)

const symbols = computed(() =>
  form.value.symbolsText.split(',').map((s) => s.trim().toUpperCase()).filter(Boolean),
)

async function load(showSpinner = true) {
  if (showSpinner) loading.value = true
  try {
    items.value = await listSchedules()
  } catch (e: unknown) {
    toast.error('定时任务加载失败', (e as { message?: string }).message)
  } finally {
    loading.value = false
  }
}

async function submit() {
  if (!form.value.name || !symbols.value.length) {
    toast.error('信息不完整', '请填写名称与至少一个交易对')
    return
  }
  submitting.value = true
  try {
    const r = await createSchedule({
      name: form.value.name,
      cron: form.value.cron,
      symbols: symbols.value,
      interval: form.value.interval,
      enabled: true,
    })
    toast.success('定时任务已创建', `schedule ${r.schedule_id.slice(0, 8)}… 每小时自动执行`)
    form.value.name = ''
    form.value.symbolsText = 'BTCUSDT,ETHUSDT'
    await load()
  } catch (e: unknown) {
    toast.error('创建失败', (e as { message?: string }).message)
  } finally {
    submitting.value = false
  }
}

async function remove(s: Schedule) {
  if (!window.confirm(`删除定时任务「${s.name}」？此操作不可撤销。`)) return
  removing.value = s.id
  try {
    await deleteSchedule(s.id)
    toast.success('已删除', s.name)
    await load()
  } catch (e: unknown) {
    toast.error('删除失败', (e as { message?: string }).message)
  } finally {
    removing.value = null
  }
}

function focusName() {
  ;(document.querySelector('#sch-name') as HTMLInputElement | null)?.focus()
}

onMounted(() => load())
</script>

<template>
  <div class="flex flex-col gap-6">
    <PageHeader
      eyebrow="Data Library"
      title="定时下载"
      desc="用 cron 表达式把 K 线增量同步排成规律任务：每小时、每天、每周末，交给后台 beat 调度。"
    >
      <template #actions>
        <HelpDrawer :help="pageHelp" />
        <button class="btn btn-ghost" :disabled="loading" @click="load()">
          <RefreshCw :size="15" :class="{ 'animate-spin-slow': loading }" />刷新
        </button>
      </template>
    </PageHeader>

    <Panel title="新增定时任务" sub="cron 采用 5 段式：分 时 日 月 周">
      <form class="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-6" @submit.prevent="submit">
        <div class="field md:col-span-2">
          <label class="label" for="sch-name">任务名称</label>
          <input id="sch-name" v-model="form.name" class="input" placeholder="如：每 5 分钟增量 BTC/ETH" />
        </div>
        <div class="field">
          <label class="label" for="sch-cron">Cron 表达式</label>
          <input id="sch-cron" v-model="form.cron" class="input font-mono" spellcheck="false" />
        </div>
        <div class="field md:col-span-2">
          <label class="label" for="sch-symbols">交易对（逗号分隔）</label>
          <input id="sch-symbols" v-model="form.symbolsText" class="input font-mono uppercase" spellcheck="false" />
        </div>
        <div class="field">
          <label class="label" for="sch-interval">颗粒度</label>
          <select id="sch-interval" v-model="form.interval" class="select font-mono">
            <option v-for="i in INTERVALS" :key="i" :value="i">{{ i }}</option>
          </select>
        </div>
        <div class="flex items-end md:col-span-2">
          <button type="submit" class="btn btn-primary" :disabled="submitting">
            <Plus :size="15" />{{ submitting ? '创建中…' : '添加定时任务' }}
          </button>
        </div>
      </form>
      <div class="mt-3 flex flex-wrap gap-1.5 text-xs text-subtle">
        <span class="mr-1 leading-7">预设：</span>
        <button v-for="preset in [
          { label: '每 5 分钟', cron: '*/5 * * * *' },
          { label: '每小时', cron: '0 * * * *' },
          { label: '每天 00:00', cron: '0 0 * * *' },
        ]" :key="preset.cron" type="button" class="chip !h-7" @click="form.cron = preset.cron">{{ preset.label }}</button>
      </div>
    </Panel>

    <Panel title="已启用任务" sub="由调度器（beat）统一触发；列表为当前启用的全部规则" :pad="'none'" flush>
      <div v-if="loading && !items.length" class="py-16"><StateView busy title="读取定时任务…" /></div>
      <div v-else-if="!items.length" class="py-6">
        <StateView
          icon="clock"
          title="还没有定时任务"
          desc="把高频更新的币种加进增量计划，历史库就始终新鲜。"
        >
          <template #action>
            <button class="btn btn-primary" @click="focusName">
              <Clock3 :size="15" />新建一个
            </button>
          </template>
        </StateView>
      </div>
      <div v-else class="overflow-x-auto">
        <table class="tbl">
          <thead>
            <tr>
              <th>名称</th>
              <th class="text-right">Cron</th>
              <th class="text-right">交易对</th>
              <th class="text-right">粒度</th>
              <th class="text-right">创建时间</th>
              <th class="text-right" aria-label="操作" />
            </tr>
          </thead>
          <tbody>
            <tr v-for="s in items" :key="s.id">
              <td class="font-semibold text-fg">{{ s.name }}</td>
              <td class="num tbl-num text-xs text-muted">{{ s.cron }}</td>
              <td class="tbl-num">
                <span v-for="sym in s.symbols" :key="sym" class="mr-1 inline-flex rounded border border-border bg-surface-2 px-1.5 py-0.5 font-mono text-[11px] text-muted">{{ sym }}</span>
              </td>
              <td class="num tbl-num text-muted">{{ s.interval }}</td>
              <td class="tbl-num tbl-sub">{{ timeAgo(s.created_at) }}</td>
              <td class="tbl-num">
                <button class="btn btn-icon h-7 w-7 text-subtle hover:text-danger" :disabled="removing === s.id" :title="`删除 ${s.name}`" aria-label="删除" @click="remove(s)">
                  <Trash2 :size="14" />
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </Panel>
  </div>
</template>
