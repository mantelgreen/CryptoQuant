<script setup lang="ts">
import { LoaderCircle } from '@lucide/vue'

withDefaults(
  defineProps<{
    icon?: 'empty' | 'search' | 'database' | 'spark' | 'bot' | 'refresh' | 'error' | 'chart' | 'clock' | 'flask' | 'wallet'
    title?: string
    desc?: string
    busy?: boolean
  }>(),
  { busy: false },
)

const iconMap = {
  empty: '〇',
  search: '⌕',
  database: '▤',
  spark: '✦',
  bot: '◈',
  refresh: '⟳',
  error: '!',
  chart: '∿',
  clock: '◷',
  flask: '⚗',
  wallet: '◈',
} as const
</script>

<template>
  <div class="flex flex-col items-center justify-center px-6 py-14 text-center">
    <div class="flex h-12 w-12 items-center justify-center rounded-2xl border border-border bg-surface-2">
      <LoaderCircle v-if="busy" :size="20" class="animate-spin text-primary" />
      <span v-else class="font-display text-lg text-subtle">{{ iconMap[icon ?? 'empty'] }}</span>
    </div>
    <p v-if="title" class="mt-4 text-sm font-semibold text-fg">{{ title }}</p>
    <p v-if="desc" class="mt-1.5 max-w-sm text-xs leading-relaxed text-muted">{{ desc }}</p>
    <div v-if="$slots.action" class="mt-5">
      <slot name="action" />
    </div>
  </div>
</template>
