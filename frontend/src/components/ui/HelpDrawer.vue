<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import { X } from '@lucide/vue'

export interface HelpTerm {
  term: string
  desc: string
}

export interface PageHelp {
  title: string
  intro?: string
  terms?: HelpTerm[]
  tips?: string[]
}

const props = withDefaults(defineProps<{ help: PageHelp }>(), {})
const open = ref(false)

function onKey(e: KeyboardEvent) {
  if (e.key === 'Escape') open.value = false
}

watch(open, (v) => {
  document.body.style.overflow = v ? 'hidden' : ''
  if (v) document.addEventListener('keydown', onKey)
  else document.removeEventListener('keydown', onKey)
})
onBeforeUnmount(() => {
  document.body.style.overflow = ''
  document.removeEventListener('keydown', onKey)
})
</script>

<template>
  <button class="btn btn-ghost !h-8 px-2.5 text-xs" aria-label="本页帮助" :title="`${help.title} · 帮助`" @click="open = true">
    <span class="flex h-4 w-4 items-center justify-center rounded-full border border-current text-[10px] font-bold">?</span>
    帮助
  </button>

  <Teleport to="body">
    <Transition name="fade">
      <div v-if="open" class="fixed inset-0 z-[80] bg-black/40 lg:hidden" @click="open = false" />
    </Transition>
    <Transition name="drawer">
      <aside
        v-if="open"
        class="fixed inset-y-0 right-0 z-[81] flex w-[min(400px,calc(100vw-2rem))] flex-col border-l border-border bg-surface shadow-pop"
        role="dialog"
        aria-label="本页帮助"
      >
        <header class="flex items-start gap-3 border-b border-border px-5 py-4">
          <div class="min-w-0 flex-1">
            <p class="eyebrow mb-1">本页指南 · 术语科普</p>
            <h2 class="h1 text-lg">{{ help.title }}</h2>
            <p v-if="help.intro" class="mt-2 text-xs leading-relaxed text-muted">{{ help.intro }}</p>
          </div>
          <button class="btn btn-icon h-8 w-8 shrink-0 text-subtle hover:text-fg" aria-label="关闭帮助" @click="open = false">
            <X :size="16" />
          </button>
        </header>

        <div class="min-h-0 flex-1 overflow-y-auto px-5 py-4">
          <div v-if="help.terms?.length">
            <p class="mb-2 text-[11px] font-semibold uppercase tracking-[0.14em] text-subtle">术语</p>
            <div class="space-y-3">
              <div v-for="t in help.terms" :key="t.term" class="rounded-xl border border-border bg-surface-2/40 px-3.5 py-2.5">
                <p class="font-mono text-[12px] font-semibold text-primary-ink dark:text-primary">{{ t.term }}</p>
                <p class="mt-1 text-xs leading-relaxed text-muted">{{ t.desc }}</p>
              </div>
            </div>
          </div>

          <div v-if="help.tips?.length" class="mt-5">
            <p class="mb-2 text-[11px] font-semibold uppercase tracking-[0.14em] text-subtle">怎么用</p>
            <ul class="space-y-1.5">
              <li v-for="(tip, i) in help.tips" :key="i" class="flex gap-2 text-xs leading-relaxed text-muted">
                <span class="text-primary">{{ i + 1 }}.</span>
                <span>{{ tip }}</span>
              </li>
            </ul>
          </div>

          <p class="mt-6 text-center text-[10px] text-subtle">数据与指标口径均为平台自算，请结合自身判断使用。</p>
        </div>
      </aside>
    </Transition>
  </Teleport>
</template>

<style scoped>
.drawer-enter-active,
.drawer-leave-active {
  transition: transform 0.22s cubic-bezier(0.2, 0.8, 0.3, 1);
}
.drawer-enter-from,
.drawer-leave-to {
  transform: translateX(100%);
}
</style>
