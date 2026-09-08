<script setup lang="ts">
import { onBeforeUnmount, onMounted, watch } from 'vue'
import { X } from '@lucide/vue'

const props = withDefaults(
  defineProps<{
    open: boolean
    title?: string
    sub?: string
    width?: string
  }>(),
  { width: '560px' },
)

const emit = defineEmits<{ close: [] }>()

function onKey(e: KeyboardEvent) {
  if (e.key === 'Escape') emit('close')
}
function onOverlay(e: MouseEvent) {
  if (e.target === e.currentTarget) emit('close')
}

watch(
  () => props.open,
  (v) => {
    document.body.style.overflow = v ? 'hidden' : ''
    if (v) document.addEventListener('keydown', onKey)
    else document.removeEventListener('keydown', onKey)
  },
)
onMounted(() => {
  if (props.open) document.addEventListener('keydown', onKey)
})
onBeforeUnmount(() => {
  document.removeEventListener('keydown', onKey)
  document.body.style.overflow = ''
})
</script>

<template>
  <Teleport to="body">
    <Transition name="fade">
      <div
        v-if="open"
        class="fixed inset-0 z-[80] flex items-end justify-center bg-black/40 backdrop-blur-[2px] sm:items-center sm:p-5"
        @mousedown="onOverlay"
      >
        <div
          class="panel flex max-h-[88vh] w-full flex-col overflow-hidden shadow-pop"
          :style="{ maxWidth: width }"
          role="dialog"
          aria-modal="true"
        >
          <header v-if="title || sub || $slots.head || $slots.actions" class="flex items-start gap-3 border-b border-border px-5 py-4">
            <div class="min-w-0 flex-1">
              <div class="flex items-center gap-2">
                <h3 class="text-[15px] font-semibold text-fg">{{ title }}</h3>
                <slot name="head" />
              </div>
              <p v-if="sub" class="mt-1 text-xs leading-snug text-subtle">{{ sub }}</p>
            </div>
            <slot name="actions" />
            <button class="btn btn-icon h-8 w-8 shrink-0 text-subtle hover:text-fg" aria-label="关闭" @click="emit('close')">
              <X :size="15" />
            </button>
          </header>
          <div class="min-h-0 flex-1 overflow-y-auto px-5 py-4">
            <slot />
          </div>
          <footer v-if="$slots.footer" class="border-t border-border px-5 py-3.5">
            <slot name="footer" />
          </footer>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>
