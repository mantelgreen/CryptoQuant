<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref } from 'vue'
import { Info } from '@lucide/vue'

/**
 * Tip —— 帮助图标 + 自定义浮层 tooltip。
 *
 * 把“系统默认 title 浮层”替换成与设计语言一致的气泡：
 * · 通过 <Teleport to="body"> 渲染，不被父级 overflow/panel 裁剪；
 * · hover / 键盘聚焦 / 点击均可触发；空间不足自动翻转到底部；
 * · 触发图标 hover/聚焦时用遥测青点亮，作为“可读说明”的提示。
 *
 * 用法（纯文本帮助图标）：
 *   <Tip text="说明文字" />
 * 用法（自定义触发器 + 长/富文本，可加 slot）：
 *   <Tip text="说明"><你的图标/元素 /></Tip>
 */
const props = withDefaults(
  defineProps<{
    text: string
    /** 首选方位，空间不足会自动翻转 */
    placement?: 'top' | 'bottom'
    /** 气泡最大宽度（px） */
    maxWidth?: number
  }>(),
  { placement: 'top', maxWidth: 300 }
)

const uid = `cq-tip-${Math.random().toString(36).slice(2, 9)}`
const triggerEl = ref<HTMLElement | null>(null)
const popperEl = ref<HTMLElement | null>(null)
const open = ref(false)
const vis = ref(false)
const pos = ref({ top: 0, left: 0, arrowLeft: 0, placement: 'top' as 'top' | 'bottom' })

let hideTimer: ReturnType<typeof setTimeout> | undefined

function computeLayout() {
  const trig = triggerEl.value
  const pop = popperEl.value
  if (!trig || !pop) return
  const tr = trig.getBoundingClientRect()
  const gap = 8
  const pad = 10
  const w = pop.offsetWidth
  const h = pop.offsetHeight
  let placement: 'top' | 'bottom' = props.placement
  if (placement === 'top' && tr.top - gap - h < pad) placement = 'bottom'
  if (placement === 'bottom' && window.innerHeight - tr.bottom - gap - h < pad) placement = 'top'
  let left = tr.left + tr.width / 2 - w / 2
  left = Math.max(pad, Math.min(left, window.innerWidth - w - pad))
  const top = placement === 'top' ? tr.top - gap - h : tr.bottom + gap
  const arrowLeft = tr.left + tr.width / 2 - left
  pos.value = { top, left, arrowLeft, placement }
}

function openTooltip() {
  if (!props.text) return
  if (hideTimer) clearTimeout(hideTimer)
  open.value = true
  vis.value = false
  void nextTick(() => {
    requestAnimationFrame(() => {
      computeLayout()
      vis.value = true
      window.addEventListener('resize', computeLayout)
      window.addEventListener('scroll', computeLayout, true)
    })
  })
}

function scheduleClose(delay = 160) {
  if (hideTimer) clearTimeout(hideTimer)
  hideTimer = setTimeout(closeTooltip, delay)
}

function keepOpen() {
  if (hideTimer) clearTimeout(hideTimer)
}

function closeTooltip() {
  open.value = false
  vis.value = false
  window.removeEventListener('resize', computeLayout)
  window.removeEventListener('scroll', computeLayout, true)
}

onBeforeUnmount(() => {
  if (hideTimer) clearTimeout(hideTimer)
  closeTooltip()
})
</script>

<template>
  <span
    ref="triggerEl"
    class="cq-tip"
    tabindex="0"
    role="button"
    :aria-expanded="open"
    :aria-describedby="open ? uid : undefined"
    @mouseenter="openTooltip"
    @mouseleave="scheduleClose()"
    @focus="openTooltip"
    @blur="scheduleClose(0)"
    @click="open ? closeTooltip() : openTooltip()"
    @keydown.enter.prevent="open ? closeTooltip() : openTooltip()"
    @keydown.space.prevent="open ? closeTooltip() : openTooltip()"
  >
    <slot><Info :size="12" class="cq-tip-icon" /></slot>

    <Teleport to="body">
      <div
        v-if="open"
        :id="uid"
        ref="popperEl"
        class="cq-tip-popper"
        :class="{ 'is-open': vis }"
        :data-placement="pos.placement"
        role="tooltip"
        :style="{ left: `${pos.left}px`, top: `${pos.top}px`, maxWidth: `${maxWidth}px` }"
        @mouseenter="keepOpen"
        @mouseleave="scheduleClose()"
      >
        <span class="cq-tip-arrow" :style="{ left: `${pos.arrowLeft}px` }" />
        <span class="cq-tip-text">{{ text }}</span>
      </div>
    </Teleport>
  </span>
</template>

<style scoped>
.cq-tip {
  display: inline-flex;
  align-items: center;
  cursor: help;
  color: var(--color-subtle);
  border-radius: 4px;
  outline: none;
}

.cq-tip-icon {
  transition: color 140ms ease;
  flex: none;
}

.cq-tip:hover .cq-tip-icon,
.cq-tip:focus-visible .cq-tip-icon,
.cq-tip[aria-expanded='true'] .cq-tip-icon {
  color: var(--color-primary);
}

/* —— 气泡：冷光“仪表读数”浮层，亮/暗主题下保持一致 —— */
.cq-tip-popper {
  position: fixed;
  z-index: 1200;
  pointer-events: auto;
  box-sizing: border-box;
  padding: 8px 11px;
  border: 1px solid rgba(148, 163, 184, 0.22);
  border-radius: var(--radius-ctl);
  background: #12161f;
  background: color-mix(in srgb, #10141b 96%, #22304a);
  color: #e9edf3;
  font-size: 12px;
  line-height: 1.55;
  font-variant-numeric: tabular-nums;
  box-shadow: var(--shadow-pop), inset 0 1px 0 rgba(255, 255, 255, 0.06);
  opacity: 0;
  transform: translateY(-3px) scale(0.985);
  transition:
    opacity 120ms ease,
    transform 140ms cubic-bezier(0.2, 0.8, 0.3, 1);
}

.cq-tip-popper[data-placement='bottom'] {
  transform: translateY(3px) scale(0.985);
}

.cq-tip-popper.is-open {
  opacity: 1;
  transform: translateY(0) scale(1);
}

.cq-tip-text {
  display: block;
  white-space: pre-line;
  word-break: break-word;
}

.cq-tip-arrow {
  position: absolute;
  left: 0;
  bottom: -4px;
  width: 8px;
  height: 8px;
  background: inherit;
  border-right: 1px solid rgba(148, 163, 184, 0.22);
  border-bottom: 1px solid rgba(148, 163, 184, 0.22);
  transform: rotate(45deg);
}

.cq-tip-popper[data-placement='bottom'] .cq-tip-arrow {
  top: -4px;
  bottom: auto;
  transform: rotate(225deg);
  border-right: none;
  border-left: 1px solid rgba(148, 163, 184, 0.22);
  border-top: 1px solid rgba(148, 163, 184, 0.22);
}
</style>
