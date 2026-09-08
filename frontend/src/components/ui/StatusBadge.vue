<script setup lang="ts">
import { computed } from 'vue'
import { jobMeta } from '@/utils/format'

const props = withDefaults(
  defineProps<{
    status: string
    pulse?: boolean
  }>(),
  { pulse: true },
)

const meta = computed(() => jobMeta(props.status))
</script>

<template>
  <span class="badge" :class="meta.tone">
    <span v-if="pulse && status === 'running'" class="badge-dot animate-pulse" />
    <span v-else class="badge-dot opacity-70" />
    {{ meta.label }}
  </span>
</template>
