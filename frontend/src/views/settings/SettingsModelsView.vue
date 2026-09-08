<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { LoaderCircle, Plus, RefreshCw, Trash2 } from '@lucide/vue'
import PageHeader from '@/components/ui/PageHeader.vue'
import HelpDrawer, { type PageHelp } from '@/components/ui/HelpDrawer.vue'
import Panel from '@/components/ui/Panel.vue'
import Modal from '@/components/ui/Modal.vue'
import StateView from '@/components/ui/StateView.vue'
import { useToastStore } from '@/stores/toast'
import {
  listProviders,
  createProvider,
  updateProvider,
  deleteProvider,
  testProvider,
  type LlmProvider,
  type ProviderInput,
  type LlmTestResult,
} from '@/api/llm'
import { timeAgo } from '@/utils/format'

const pageHelp: PageHelp = {
  title: '模型设置（LLM）',
  intro: '配置大模型供应商：Base URL、API Key、默认模型与采样参数。这些将用于 Agent 因子研究 / 策略生成 / 自动交易与用量统计。',
  terms: [
    { term: 'Base URL', desc: '兼容 OpenAI Chat 协议的 API 地址。如 OpenAI=https://api.openai.com/v1、DeepSeek=https://api.deepseek.com/v1、Ollama=http://localhost:11434/v1。' },
    { term: 'API Key', desc: '供应商密钥。仅保存于后端数据库；界面始终掩码显示（生产建议改用环境变量/密钥托管）。' },
    { term: '默认模型', desc: '星标供应商即默认；未指定模型时系统使用它的 model 与参数。' },
    { term: 'temperature / max_tokens', desc: 'temperature 越低越确定（研究用 0~0.3）；max_tokens 限制单次回复长度。' },
  ],
  tips: ['密钥填过一次后编辑时留空即保持原值。', '先配置至少一个“默认”供应商，Agent 调用才能生效。', '用量与成本将按此处配置的模型在「LLM 用量」统计。'],
}

const toast = useToastStore()
const PRESET_URL: Record<string, string> = {
  openai: 'https://api.openai.com/v1',
  deepseek: 'https://api.deepseek.com/v1',
  anthropic: 'https://api.anthropic.com',
  moonshot: 'https://api.moonshot.cn/v1',
  ollama: 'http://localhost:11434/v1',
}
const items = ref<LlmProvider[]>([])
const loading = ref(true)
const error = ref<string | null>(null)
const testBusy = ref<string | null>(null)
const showModal = ref(false)
const editing = ref<LlmProvider | null>(null)
const busy = ref(false)
const form = ref<ProviderInput>(emptyForm())

function emptyForm(): ProviderInput {
  return { name: '', provider: 'deepseek', base_url: PRESET_URL.deepseek, api_key: '', model: '', temperature: 0.2, max_tokens: 2000, enabled: true, is_default: false }
}

async function load() {
  loading.value = true
  error.value = null
  try {
    items.value = await listProviders()
  } catch (e: unknown) {
    error.value = (e as { message?: string }).message ?? '加载失败'
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editing.value = null
  form.value = { ...emptyForm(), is_default: items.value.length === 0 }
  showModal.value = true
}

function openEdit(p: LlmProvider) {
  editing.value = p
  form.value = {
    name: p.name,
    provider: p.provider,
    base_url: p.base_url ?? '',
    api_key: '',
    model: p.model,
    temperature: p.temperature,
    max_tokens: p.max_tokens,
    enabled: p.enabled,
    is_default: p.is_default,
  }
  showModal.value = true
}

function onProviderChange() {
  if (!form.value.base_url || !Object.values(PRESET_URL).includes(form.value.base_url)) {
    form.value.base_url = PRESET_URL[form.value.provider] ?? ''
  }
}

async function save() {
  if (busy.value || !form.value.name.trim() || !form.value.model.trim()) return
  busy.value = true
  try {
    if (editing.value) {
      await updateProvider(editing.value.id, form.value)
      toast.success('已保存', form.value.name)
    } else {
      await createProvider(form.value)
      toast.success('已添加供应商', form.value.name)
    }
    showModal.value = false
    await load()
  } catch (e: unknown) {
    toast.error('保存失败', (e as { message?: string }).message)
  } finally {
    busy.value = false
  }
}

async function setDefault(p: LlmProvider) {
  try {
    await updateProvider(p.id, { is_default: true })
    toast.success('已设为默认', p.name)
    await load()
  } catch (e: unknown) {
    toast.error('操作失败', (e as { message?: string }).message)
  }
}

async function remove(p: LlmProvider) {
  if (!window.confirm(`删除供应商「${p.name}」？`)) return
  try {
    await deleteProvider(p.id)
    toast.success('已删除', p.name)
    await load()
  } catch (e: unknown) {
    toast.error('删除失败', (e as { message?: string }).message)
  }
}

async function testProv(p: LlmProvider) {
  if (testBusy.value) return
  testBusy.value = p.id
  try {
    const r: LlmTestResult = await testProvider(p.id)
    toast.success('模型调用成功', `${r.model} · ${r.usage?.total_tokens ?? '?'} tokens · ${r.latency_ms}ms · 回复：${r.reply || '（空，可能均为推理 token）'}`)
  } catch (e: unknown) {
    toast.error('模型调用失败', (e as { message?: string }).message)
  } finally {
    testBusy.value = null
  }
}

onMounted(load)
</script>

<template>
  <div class="flex flex-col gap-6">
    <PageHeader
      eyebrow="Settings · LLM"
      title="模型设置"
      desc="管理 LLM 供应商与默认模型：Base URL、API Key、采样参数。供 AI 工坊 / Agent 自动交易使用。"
    >
      <template #actions>
        <HelpDrawer :help="pageHelp" />
        <button class="btn btn-ghost" :disabled="loading" @click="load"><RefreshCw :size="15" :class="{ 'animate-spin-slow': loading }" />刷新</button>
        <button class="btn btn-primary" @click="openCreate"><Plus :size="15" />添加供应商</button>
      </template>
    </PageHeader>

    <Panel v-if="error" :pad="'none'">
      <StateView icon="error" :title="error" desc="网关不可达或迁移未应用（python -m alembic upgrade head）。">
        <template #action><button class="btn btn-primary" @click="load">重试</button></template>
      </StateView>
    </Panel>

    <template v-else>
      <div v-if="loading && !items.length" class="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
        <div v-for="i in 3" :key="i" class="panel h-40 animate-pulse"><div class="space-y-3 p-5"><div class="h-3.5 w-2/5 rounded bg-surface-2" /><div class="h-2.5 w-4/5 rounded bg-surface-2" /><div class="h-2.5 w-3/5 rounded bg-surface-2" /></div></div>
      </div>

      <div v-else-if="items.length" class="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
        <div v-for="p in items" :key="p.id" class="panel flex flex-col p-5">
          <div class="flex items-center gap-2">
            <span class="badge" :class="p.enabled ? 'tone-up' : 'tone-neutral'">{{ p.enabled ? '启用' : '停用' }}</span>
            <span v-if="p.is_default" class="badge tone-primary">默认</span>
            <span class="ml-auto font-mono text-[11px] text-subtle">{{ p.provider }}</span>
          </div>
          <h3 class="mt-3 text-base font-semibold text-fg">{{ p.name }}</h3>
          <p class="mt-1 font-mono text-xs text-primary-ink dark:text-primary">{{ p.model }}</p>
          <div class="mt-2 space-y-1 text-[11px] text-muted">
            <p class="truncate">Base URL：<span class="font-mono">{{ p.base_url || '—' }}</span></p>
            <p class="truncate">Key：<span class="font-mono">{{ p.api_key || '未配置' }}</span></p>
            <p class="num">temp {{ p.temperature }} · max_tokens {{ p.max_tokens }} · {{ timeAgo(p.created_at) }}</p>
          </div>
          <div class="mt-4 flex gap-2 border-t border-border pt-3">
            <button class="btn btn-ghost !h-8 flex-1 text-xs" @click="openEdit(p)">编辑</button>
            <button class="btn btn-ghost !h-8 flex-1 text-xs" :disabled="testBusy !== null" @click="testProv(p)">
              <LoaderCircle v-if="testBusy === p.id" :size="12" class="animate-spin" />测试
            </button>
            <button v-if="!p.is_default" class="btn btn-soft !h-8 flex-1 text-xs" @click="setDefault(p)">设为默认</button>
            <button class="btn btn-icon !h-8 w-8 text-subtle hover:text-danger" :title="`删除 ${p.name}`" @click="remove(p)"><Trash2 :size="14" /></button>
          </div>
        </div>
      </div>

      <Panel v-else :pad="'none'">
        <StateView icon="bot" title="还没有配置任何供应商" desc="先添加一个兼容 OpenAI 协议的模型（如 DeepSeek / OpenAI / Ollama），星标设为默认即可给 Agent 使用。">
          <template #action><button class="btn btn-primary" @click="openCreate"><Plus :size="15" />添加第一个供应商</button></template>
        </StateView>
      </Panel>
    </template>

    <Modal :open="showModal" :title="editing ? `编辑 · ${editing.name}` : '添加模型供应商'" sub="API Key 仅存后端数据库，界面掩码显示" width="560px" @close="showModal = false">
      <div class="space-y-4">
        <div class="grid grid-cols-2 gap-3">
          <div class="field">
            <label class="label" for="lm-name">名称</label>
            <input id="lm-name" v-model="form.name" class="input" placeholder="如 deepseek-main" />
          </div>
          <div class="field">
            <label class="label" for="lm-provider">供应商</label>
            <select id="lm-provider" v-model="form.provider" class="select" @change="onProviderChange">
              <option value="deepseek">DeepSeek</option>
              <option value="openai">OpenAI</option>
              <option value="moonshot">Moonshot</option>
              <option value="anthropic">Anthropic</option>
              <option value="ollama">Ollama(本地)</option>
              <option value="custom">自定义</option>
            </select>
          </div>
        </div>
        <div class="field">
          <label class="label" for="lm-url">Base URL</label>
          <input id="lm-url" v-model="form.base_url" class="input font-mono" placeholder="https://..." spellcheck="false" />
        </div>
        <div class="grid grid-cols-2 gap-3">
          <div class="field">
            <label class="label" for="lm-key">API Key</label>
            <input id="lm-key" v-model="form.api_key" type="password" class="input font-mono" :placeholder="editing ? '留空则不修改' : 'sk-...'" autocomplete="off" />
          </div>
          <div class="field">
            <label class="label" for="lm-model">默认模型</label>
            <input id="lm-model" v-model="form.model" class="input font-mono" placeholder="deepseek-chat / gpt-4o-mini" spellcheck="false" />
          </div>
        </div>
        <div class="grid grid-cols-3 gap-3">
          <div class="field">
            <label class="label" for="lm-temp">temperature</label>
            <input id="lm-temp" v-model.number="form.temperature" type="number" step="0.1" min="0" max="2" class="input font-mono" />
          </div>
          <div class="field">
            <label class="label" for="lm-tok">max_tokens</label>
            <input id="lm-tok" v-model.number="form.max_tokens" type="number" min="1" class="input font-mono" />
          </div>
          <div class="field">
            <label class="label">启用 / 默认</label>
            <div class="flex h-[34px] items-center gap-4 px-1 text-xs text-muted">
              <label class="flex items-center gap-1.5"><input v-model="form.enabled" type="checkbox" class="accent-[var(--color-primary)]" />启用</label>
              <label class="flex items-center gap-1.5"><input v-model="form.is_default" type="checkbox" class="accent-[var(--color-primary)]" />设为默认</label>
            </div>
          </div>
        </div>
      </div>
      <template #footer>
        <div class="flex justify-end gap-2">
          <button class="btn btn-ghost" @click="showModal = false">取消</button>
          <button class="btn btn-primary" :disabled="busy || !form.name.trim() || !form.model.trim()" @click="save">
            {{ busy ? '保存中…' : '保存' }}
          </button>
        </div>
      </template>
    </Modal>
  </div>
</template>
