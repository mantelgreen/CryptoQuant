<script setup lang="ts">
import { computed, ref } from 'vue'
import { Check, Search } from '@lucide/vue'
import type { FactorMeta } from '@/api/factor'
import { MAX_ML_FEATURES } from '@/utils/format'

const props = withDefaults(
  defineProps<{
    factors: FactorMeta[]
    selected: string[]
    max?: number
    ic?: Record<string, number | null>
  }>(),
  { max: MAX_ML_FEATURES },
)

const emit = defineEmits<{
  'update:selected': [value: string[]]
}>()

const q = ref('')

const filtered = computed(() => {
  const kw = q.value.trim().toLowerCase()
  if (!kw) return props.factors
  return props.factors.filter(
    (f) =>
      f.name.toLowerCase().includes(kw) ||
      f.code.toLowerCase().includes(kw) ||
      f.description.toLowerCase().includes(kw) ||
      (f.tags ?? []).some((t) => t.toLowerCase().includes(kw)),
  )
})

const selectedSet = computed(() => new Set(props.selected))

function toggle(code: string) {
  const s = new Set(props.selected)
  if (s.has(code)) {
    s.delete(code)
  } else {
    if (props.max && s.size >= props.max) return
    s.add(code)
  }
  emit('update:selected', [...s])
}

function selectAll() {
  const s = new Set(props.selected)
  for (const f of filtered.value) {
    if (props.max && s.size >= props.max) break
    s.add(f.code)
  }
  emit('update:selected', [...s])
}

function clear() {
  emit('update:selected', [])
}

function icText(code: string): string {
  const v = props.ic?.[code]
  return v != null ? v.toFixed(3) : '—'
}
</script>

<template>
  <div class="flex flex-col">
    <div class="flex items-center gap-2">
      <div class="relative flex-1">
        <Search :size="13" class="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-subtle" />
        <input v-model="q" type="search" placeholder="搜索因子名称 / 代码 / 标签…" class="input !h-8 pl-8 text-xs" />
      </div>
      <button class="chip !h-8 shrink-0 text-xs" @click="selectAll">全选</button>
      <button class="chip !h-8 shrink-0 text-xs" @click="clear">清空</button>
    </div>

    <div class="mt-2 max-h-64 overflow-y-auto overscroll-contain rounded-lg border border-border">
      <div
        v-for="f in filtered"
        :key="f.code"
        role="checkbox"
        :aria-checked="selectedSet.has(f.code)"
        tabindex="0"
        class="flex cursor-pointer items-center gap-2.5 border-b border-border px-3 py-2 transition-colors last:border-b-0 hover:bg-surface-2"
        :class="selectedSet.has(f.code) ? 'bg-surface-2/60' : ''"
        @click="toggle(f.code)"
        @keydown.enter.prevent="toggle(f.code)"
        @keydown.space.prevent="toggle(f.code)"
        @mousedown.prevent
      >
        <span
          class="flex h-4 w-4 shrink-0 items-center justify-center rounded border transition-colors"
          :class="selectedSet.has(f.code) ? 'border-primary bg-primary text-white' : 'border-strong bg-surface'"
        >
          <Check v-if="selectedSet.has(f.code)" :size="11" />
        </span>
        <span class="min-w-0 flex-1">
          <span class="flex items-center gap-1.5">
            <span class="truncate text-[13px] font-medium text-fg">{{ f.name }}</span>
            <span v-if="f.is_user" class="badge tone-ai !py-0">AI</span>
          </span>
          <span class="block truncate font-mono text-[10px] text-subtle">{{ f.is_user ? (f.expression ?? f.code) : f.code }}</span>
        </span>
        <span class="shrink-0 font-mono text-[10px] text-subtle">IC {{ icText(f.code) }}</span>
      </div>
      <div v-if="!filtered.length" class="px-3 py-6 text-center text-xs text-subtle">没有匹配的因子</div>
    </div>

    <p class="mt-1.5 text-[11px] text-subtle">
      已选 <span class="num font-semibold text-fg">{{ selected.length }}</span>
      <template v-if="max"> / {{ max }}</template>
      个因子（支持关键词搜索，因子多了也能快速定位）
    </p>
  </div>
</template>
