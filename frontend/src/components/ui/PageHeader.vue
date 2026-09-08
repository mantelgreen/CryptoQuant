<script setup lang="ts">
defineProps<{
  eyebrow?: string
  title: string
  desc?: string
}>()
</script>

<template>
  <div class="flex flex-wrap items-end justify-between gap-3">
    <div class="min-w-0">
      <p v-if="eyebrow" class="eyebrow mb-1.5">{{ eyebrow }}</p>
      <h1 class="h1">{{ title }}</h1>
      <p v-if="desc" class="mt-1.5 max-w-2xl text-[13px] leading-relaxed text-muted">{{ desc }}</p>
    </div>
    <div v-if="$slots.actions" class="flex shrink-0 flex-wrap items-center gap-2">
      <slot name="actions" />
    </div>
  </div>
</template>
