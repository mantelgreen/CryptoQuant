<script setup lang="ts">
import { computed } from 'vue'
import { ChevronLeft, ChevronRight } from '@lucide/vue'

const props = withDefaults(
  defineProps<{
    total: number
    page: number
    pageSize: number
    sizes?: number[]
  }>(),
  { sizes: () => [9, 12, 24] },
)

const emit = defineEmits<{
  'update:page': [page: number]
  'update:pageSize': [size: number]
}>()

const pages = computed(() => Math.max(1, Math.ceil(props.total / props.pageSize)))

const items = computed<Array<number | 'gap'>>(() => {
  const p = props.page
  const n = pages.value
  if (n <= 7) {
    return Array.from({ length: n }, (_, i) => i + 1)
  }
  const set = new Set<number>([1, n])
  for (let d = -2; d <= 2; d++) {
    const v = p + d
    if (v >= 1 && v <= n) set.add(v)
  }
  const arr = [...set].sort((a, b) => a - b)
  const out: Array<number | 'gap'> = []
  let prev = 0
  for (const x of arr) {
    if (x - prev > 1) out.push('gap')
    out.push(x)
    prev = x
  }
  return out
})

function goto(p: number) {
  if (p >= 1 && p <= pages.value && p !== props.page) emit('update:page', p)
}

function changeSize(size: number) {
  if (size !== props.pageSize) emit('update:pageSize', size)
}
</script>

<template>
  <div class="flex flex-wrap items-center justify-between gap-2 px-1">
    <p class="text-[11px] text-subtle">
      共 <span class="num text-fg">{{ total }}</span> 条 · 第 <span class="num text-fg">{{ page }}</span> / <span class="num text-fg">{{ pages }}</span> 页
    </p>

    <div class="flex items-center gap-1">
      <button class="btn btn-icon !h-7 !w-7 text-muted" :disabled="page <= 1" title="上一页" aria-label="上一页" @click="goto(page - 1)">
        <ChevronLeft :size="15" />
      </button>

      <template v-for="(it, i) in items" :key="i">
        <button v-if="it !== 'gap'" class="chip !h-7 !min-w-7 !px-2" :class="{ 'is-active': it === page }" @click="goto(it)">
          {{ it }}
        </button>
        <span v-else class="px-0.5 text-subtle">…</span>
      </template>

      <button class="btn btn-icon !h-7 !w-7 text-muted" :disabled="page >= pages" title="下一页" aria-label="下一页" @click="goto(page + 1)">
        <ChevronRight :size="15" />
      </button>

      <label class="ml-1 flex items-center gap-1 text-[11px] text-subtle">
        每页
        <select class="select !h-7 !w-auto !rounded-md !px-6 !py-0 text-xs" :value="pageSize" @change="changeSize(Number(($event.target as HTMLSelectElement).value))">
          <option v-for="s in sizes" :key="s" :value="s">{{ s }}</option>
        </select>
        条
      </label>
    </div>
  </div>
</template>
