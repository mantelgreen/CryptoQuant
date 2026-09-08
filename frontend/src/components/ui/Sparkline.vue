<script setup lang="ts">
import { computed, useId } from 'vue'
import { cls } from '@/utils/format'

const props = withDefaults(
  defineProps<{
    points: number[]
    width?: number
    height?: number
    strokeWidth?: number
  }>(),
  { width: 96, height: 28, strokeWidth: 1.6 },
)

const uid = useId()

function toPath(): string {
  const pts = props.points
  if (pts.length < 2) return ''
  let min = Math.min(...pts)
  let max = Math.max(...pts)
  if (max === min) {
    max = min + 1
    min = min - 1
  }
  const pad = 2
  const span = max - min
  const stepX = (props.width - pad * 2) / (pts.length - 1)
  return pts
    .map((p, i) => {
      const x = pad + i * stepX
      const y = pad + (max - p) / span * (props.height - pad * 2)
      return `${i === 0 ? 'M' : 'L'}${x.toFixed(2)},${y.toFixed(2)}`
    })
    .join(' ')
}

const areaPath = computed(() => {
  const pts = props.points
  if (pts.length < 2) return ''
  const p = toPath()
  const x1 = 2
  const x2 = props.width - 2
  const yB = props.height - 2
  return `${p} L${x2},${yB} L${x1},${yB} Z`
})

const trendUp = computed(() => {
  const pts = props.points
  return pts.length >= 2 && pts[pts.length - 1] >= pts[0]
})
const color = computed(() => (trendUp.value ? 'var(--color-up)' : 'var(--color-down)'))
const areaColor = computed(() => (trendUp.value ? 'rgba(14,159,110,0.14)' : 'rgba(217,58,68,0.14)'))

const stroke = computed(() => cls(props.points[0] === undefined ? 0 : props.points[props.points.length - 1] - props.points[0]))
</script>

<template>
  <svg
    :width="width"
    :height="height"
    :viewBox="`0 0 ${width} ${height}`"
    fill="none"
    class="block"
    aria-hidden="true"
    preserveAspectRatio="none"
  >
    <defs>
      <linearGradient :id="`sp-${uid}`" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" :stop-color="color" stop-opacity="0.32" />
        <stop offset="1" :stop-color="color" stop-opacity="0" />
      </linearGradient>
    </defs>
    <path v-if="areaPath" :d="areaPath" :fill="`url(#sp-${uid})`" />
    <path :d="toPath()" :stroke="color" :stroke-width="strokeWidth" stroke-linejoin="round" stroke-linecap="round" />
  </svg>
</template>
