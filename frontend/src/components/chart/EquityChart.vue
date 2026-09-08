<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts'
import type { ECharts } from 'echarts'

const props = withDefaults(
  defineProps<{
    points: Array<{ ts: string; value: number }>
    baseline?: number
    height?: number
  }>(),
  { height: 300 },
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
  const grid = cssVar('--chart-grid')
  const axis = cssVar('--chart-axis')
  const accent = cssVar('--color-primary')
  const area = cssVar('--chart-area')
  const p = props.points

  chart.setOption(
    {
      animationDuration: 500,
      grid: { left: 12, right: 14, top: 14, bottom: 6, containLabel: true },
      tooltip: {
        trigger: 'axis',
        backgroundColor: cssVar('--color-surface'),
        borderColor: cssVar('--color-border'),
        textStyle: { color: cssVar('--color-fg'), fontSize: 12, fontFamily: 'JetBrains Mono Variable, monospace' },
        valueFormatter: (v: unknown) => Number(v).toLocaleString('en-US', { maximumFractionDigits: 2 }),
      },
      xAxis: {
        type: 'category',
        boundaryGap: false,
        data: p.map((d) => d.ts),
        axisLine: { lineStyle: { color: grid } },
        axisTick: { show: false },
        axisLabel: { color: axis, fontSize: 10, fontFamily: 'JetBrains Mono Variable, monospace' },
      },
      yAxis: {
        type: 'value',
        scale: true,
        splitLine: { lineStyle: { color: grid } },
        axisLabel: { color: axis, fontSize: 10, fontFamily: 'JetBrains Mono Variable, monospace' },
      },
      series: [
        {
          type: 'line',
          data: p.map((d) => d.value),
          showSymbol: false,
          smooth: 0.15,
          lineStyle: { width: 1.8, color: accent },
          itemStyle: { color: accent },
          areaStyle: { color: area },
          markLine:
            props.baseline != null
              ? {
                  silent: true,
                  symbol: 'none',
                  label: { show: true, formatter: '初始资金', color: axis, fontSize: 10, position: 'insideEndTop' },
                  lineStyle: { color: grid, type: 'dashed', width: 1 },
                  data: [{ yAxis: props.baseline }],
                }
              : undefined,
        },
      ],
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

watch(
  () => props.points,
  () => render(),
)
</script>

<template>
  <div ref="el" class="w-full" :style="{ height: `${height}px` }" />
</template>
