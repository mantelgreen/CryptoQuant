<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { FlaskConical, Plus, ArrowUpRight } from '@lucide/vue'
import { createStrategy, listStrategies, type Strategy } from '@/api/strategy'
import PageHeader from '@/components/ui/PageHeader.vue'
import HelpDrawer, { type PageHelp } from '@/components/ui/HelpDrawer.vue'
import Panel from '@/components/ui/Panel.vue'
import Modal from '@/components/ui/Modal.vue'
import StateView from '@/components/ui/StateView.vue'
import { useToastStore } from '@/stores/toast'
import { timeAgo } from '@/utils/format'

const pageHelp: PageHelp = {
  title: '策略研究 · 列表',
  intro: '策略 = “交易思路”的容器。先建策略外壳，再到详情页为它添加“版本”并回测。',
  terms: [
    { term: '策略（Strategy）', desc: '一个研究对象：名称 + 描述 + 标签。本身不含交易代码，代码放在“版本”里。' },
    { term: '策略版本（Version）', desc: '一套可执行/可回测的具体实现（例如“双均线快10慢30”）。改参数=新建版本，便于 A/B 对比与追溯。' },
    { term: '标签 Tags', desc: '给策略分类的便签（如 趋势/均值回归/BTC），便于日后检索与组合。' },
    { term: '策略化 vs 因子', desc: '因子回答“什么预测收益”；策略回答“何时买卖、仓位多少”。策略可建立在一个或多个被验证的因子上。' },
  ],
  tips: ['新建策略后会自动进入详情页添加 v1。', '卡片点击即进入该策略的“研究台”。', '用「买入持有」模板跑一次可当作基准对照。'],
}

const router = useRouter()
const toast = useToastStore()

const items = ref<Strategy[]>([])
const loading = ref(true)
const error = ref<string | null>(null)
const showCreate = ref(false)
const creating = ref(false)
const form = ref({ name: '', description: '', tags: '' })

async function load() {
  loading.value = true
  error.value = null
  try {
    items.value = await listStrategies()
  } catch (e: unknown) {
    error.value = (e as { message?: string }).message ?? '加载失败'
  } finally {
    loading.value = false
  }
}

async function submit() {
  if (!form.value.name.trim()) return
  creating.value = true
  try {
    const tags = form.value.tags
      .split(/[,，]/)
      .map((t) => t.trim())
      .filter(Boolean)
    const r = await createStrategy({ name: form.value.name.trim(), description: form.value.description.trim() || undefined, tags })
    toast.success('策略已创建', '接下来为其添加一个策略版本并运行回测')
    showCreate.value = false
    form.value = { name: '', description: '', tags: '' }
    router.push(`/strategy/${r.strategy_id}`)
  } catch (e: unknown) {
    toast.error('创建失败', (e as { message?: string }).message)
  } finally {
    creating.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="flex flex-col gap-6">
    <PageHeader
      eyebrow="Research"
      title="策略研究"
      desc="用模板或代码定义一个交易思路，交给回测引擎验证。白盒、可追溯：每个版本、每次回测都可查。"
    >
      <template #actions>
        <HelpDrawer :help="pageHelp" />
        <button class="btn btn-primary" @click="showCreate = true">
          <Plus :size="15" />新建策略
        </button>
      </template>
    </PageHeader>

    <Panel v-if="error" :pad="'none'">
      <StateView icon="error" :title="error" desc="检查后端网关是否可达后重试。">
        <template #action><button class="btn btn-primary" @click="load">重试</button></template>
      </StateView>
    </Panel>

    <template v-else>
      <div v-if="loading" class="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
        <div v-for="i in 6" :key="i" class="panel h-40 animate-pulse">
          <div class="space-y-3 p-5">
            <div class="h-3.5 w-2/5 rounded bg-surface-2" />
            <div class="h-2.5 w-4/5 rounded bg-surface-2" />
            <div class="h-2.5 w-3/5 rounded bg-surface-2" />
          </div>
        </div>
      </div>

      <template v-else-if="items.length">
        <div class="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
          <article
            v-for="s in items"
            :key="s.id"
            class="panel group cursor-pointer p-5 transition-all duration-150 hover:-translate-y-0.5 hover:shadow-pop"
            @click="router.push(`/strategy/${s.id}`)"
          >
            <div class="flex items-start justify-between gap-3">
              <div class="flex h-9 w-9 items-center justify-center rounded-lg tint-primary text-primary-ink dark:text-primary">
                <FlaskConical :size="17" />
              </div>
              <span class="text-[11px] text-subtle">{{ timeAgo(s.created_at) }}</span>
            </div>
            <h3 class="mt-3 flex items-center gap-1 text-[15px] font-semibold text-fg">
              <span class="truncate">{{ s.name }}</span>
              <ArrowUpRight :size="14" class="shrink-0 text-subtle opacity-0 transition-opacity group-hover:opacity-100" />
            </h3>
            <p v-if="s.description" class="mt-1 line-clamp-2 min-h-[2.4em] text-[13px] leading-relaxed text-muted">{{ s.description }}</p>
            <p v-else class="mt-1 min-h-[2.4em] text-[13px] leading-relaxed text-subtle">尚未填写描述</p>
            <div v-if="s.tags?.length" class="mt-3 flex flex-wrap gap-1.5">
              <span v-for="t in s.tags" :key="t" class="badge tone-neutral">{{ t }}</span>
            </div>
          </article>
        </div>
      </template>

      <Panel v-else :pad="'none'">
        <StateView
          icon="flask"
          title="还没有任何策略"
          desc="策略研究从这里开始：新建一个策略，选一个内置模板（双均线 / RSI 均值回归 / 买入持有），回测引擎就会跑出绩效指标与资金曲线。"
        >
          <template #action>
            <button class="btn btn-primary" @click="showCreate = true"><Plus :size="15" />新建第一个策略</button>
          </template>
        </StateView>
      </Panel>
    </template>

    <Modal :open="showCreate" title="新建策略" sub="先建策略外壳，随后在详情页添加策略版本" width="480px" @close="showCreate = false">
      <form class="space-y-4" @submit.prevent="submit">
        <div class="field">
          <label class="label" for="st-name">策略名称</label>
          <input id="st-name" v-model="form.name" class="input" placeholder="如：BTC 双均线趋势" maxlength="120" autofocus />
        </div>
        <div class="field">
          <label class="label" for="st-desc">一句话思路</label>
          <textarea id="st-desc" v-model="form.description" class="input" rows="3" placeholder="用一句话说明这个策略在想什么（将展示在研究台）" />
        </div>
        <div class="field">
          <label class="label" for="st-tags">标签（逗号分隔）</label>
          <input id="st-tags" v-model="form.tags" class="input" placeholder="趋势, 均线, BTC" />
        </div>
      </form>
      <template #footer>
        <div class="flex justify-end gap-2">
          <button class="btn btn-ghost" @click="showCreate = false">取消</button>
          <button class="btn btn-primary" :disabled="creating || !form.name.trim()" @click="submit">
            {{ creating ? '创建中…' : '创建并进入研究' }}
          </button>
        </div>
      </template>
    </Modal>
  </div>
</template>
