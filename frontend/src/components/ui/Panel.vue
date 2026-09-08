<script setup lang="ts">
withDefaults(
  defineProps<{
    title?: string
    sub?: string
    pad?: 'md' | 'none' | 'sm'
    flush?: boolean
  }>(),
  { pad: 'md', flush: false },
)
</script>

<template>
  <section class="panel overflow-hidden">
    <header v-if="title || sub || $slots.actions || $slots.head" class="flex items-center gap-3 border-b border-border px-4 py-3">
      <div class="min-w-0 flex-1">
        <div class="flex items-center gap-2">
          <h3 class="panel-title">{{ title }}</h3>
          <slot name="head" />
        </div>
        <p v-if="sub" class="mt-0.5 text-xs leading-snug text-subtle">{{ sub }}</p>
      </div>
      <div v-if="$slots.actions" class="flex shrink-0 items-center gap-2">
        <slot name="actions" />
      </div>
    </header>
    <div
      class="min-w-0"
      :class="{
        'px-4 py-4': pad === 'md',
        'p-0': pad === 'none',
        'px-3 py-3': pad === 'sm',
      }"
    >
      <slot />
    </div>
  </section>
</template>
