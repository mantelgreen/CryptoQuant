<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts'
import type { ECharts } from 'echarts'

export interface LineSeries {
  name: string
  color: string
  points: Array<{ ts: string; value: number }>
}

const props = withDefaults(
  defineProps<{ series: LineSeries[]; height?: number }>(),
  { height: 320 },
)

const el = ref<HTMLDivElement | null>(null)
let chart: ECharts | null = null
let ro: ResizeObserver | null = null
let mo: MutationObserver | null = null

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || '#000'
}

function render() {
  if (!chart) return
  const axis = cssVar('--chart-axis')
  const grid = cssVar('--chart-grid')
  const surface = cssVar('--color-surface')
  const border = cssVar('--color-border')
  const fg = cssVar('--color-fg')
  const subtle = cssVar('--color-subtle')

  const byTs = new Map<string, Map<string, number | null>>()
  const allTs = new Set<string>()
  for (const s of props.series) {
    for (const p of s.points) {
      allTs.add(p.ts)
      if (!byTs.has(p.ts)) byTs.set(p.ts, new Map())
      byTs.get(p.ts)!.set(s.name, p.value)
    }
  }
  const times = [...allTs].sort()
  const seriesData = props.series.map((s) => ({
    name: s.name,
    type: 'line',
    smooth: 0.2,
    showSymbol: false,
    connectNulls: true,
    lineStyle: { width: 1.8, color: s.color },
    itemStyle: { color: s.color },
    data: times.map((t) => byTs.get(t)?.get(s.name) ?? null),
  }))

  chart.setOption(
    {
      animationDuration: 300,
      grid: { left: 12, right: 16, top: 30, bottom: 6, containLabel: true },
      legend: {
        top: 0,
        textStyle: { color: subtle, fontSize: 11 },
        icon: 'roundRect',
        itemWidth: 12,
        itemHeight: 3,
      },
      tooltip: {
        trigger: 'axis',
        backgroundColor: surface,
        borderColor: border,
        textStyle: { color: fg, fontSize: 11 },
      },
      xAxis: {
        type: 'category',
        data: times,
        boundaryGap: false,
        axisLine: { lineStyle: { color: grid } },
        axisTick: { show: false },
        axisLabel: { color: axis, fontSize: 10, hideOverlap: true, fontFamily: 'JetBrains Mono Variable, monospace' },
      },
      yAxis: {
        type: 'value',
        scale: true,
        splitLine: { lineStyle: { color: grid } },
        axisLabel: { color: axis, fontSize: 10, fontFamily: 'JetBrains Mono Variable, monospace' },
      },
      series: seriesData,
    },
    true,
  )
}

onMounted(() => {
  if (!el.value) return
  chart = echarts.init(el.value)
  render()
  ro = new ResizeObserver(() => chart?.resize())
  if (el.value) ro.observe(el.value)
  mo = new MutationObserver(() => render())
  mo.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] })
})
onBeforeUnmount(() => {
  ro?.disconnect()
  mo?.disconnect()
  chart?.dispose()
  chart = null
})
watch(() => props.series, () => render(), { deep: true })
</script>

<template>
  <div ref="el" class="w-full" :style="{ height: `${height}px` }" />
</template>
