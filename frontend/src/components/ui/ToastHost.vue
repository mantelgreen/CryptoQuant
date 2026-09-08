<script setup lang="ts">
import { CheckCircle2, XCircle, Info, Sparkles, X } from '@lucide/vue'
import { useToastStore } from '@/stores/toast'

const toast = useToastStore()

const toneIcon = {
  success: CheckCircle2,
  error: XCircle,
  info: Info,
  ai: Sparkles,
}
const toneCls = {
  success: 'tone-up',
  error: 'tone-down',
  info: 'tone-primary',
  ai: 'tone-ai',
}
</script>

<template>
  <div class="pointer-events-none fixed right-4 top-4 z-[90] flex w-[min(360px,calc(100vw-2rem))] flex-col gap-2">
    <TransitionGroup name="toast">
      <div
        v-for="t in toast.items"
        :key="t.id"
        class="pointer-events-auto pop panel flex items-start gap-3 px-3.5 py-3 shadow-pop"
        role="status"
      >
        <span class="mt-px flex h-5 w-5 shrink-0 items-center justify-center">
          <component :is="toneIcon[t.tone]" :size="17" :class="toneCls[t.tone]" />
        </span>
        <div class="min-w-0 flex-1">
          <p class="text-[13px] font-semibold leading-snug text-fg">{{ t.title }}</p>
          <p v-if="t.desc" class="mt-0.5 text-xs leading-snug text-muted">{{ t.desc }}</p>
        </div>
        <button class="btn btn-icon h-6 w-6 text-subtle" aria-label="关闭" @click="toast.remove(t.id)">
          <X :size="13" />
        </button>
      </div>
    </TransitionGroup>
  </div>
</template>

<style scoped>
.toast-enter-active,
.toast-leave-active {
  transition: all 0.2s cubic-bezier(0.2, 0.9, 0.3, 1);
}
.toast-enter-from,
.toast-leave-to {
  opacity: 0;
  transform: translateY(-6px) scale(0.98);
}
</style>
