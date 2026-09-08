<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts'
import type { ECharts } from 'echarts'

interface MinutePoint {
  ts: number
  price: number
  vol: number
}

const props = withDefaults(
  defineProps<{
    points: MinutePoint[]
    avg: number[]
    height?: number
  }>(),
  { height: 340 },
)

const el = ref<HTMLDivElement | null>(null)
let chart: ECharts | null = null
let ro: ResizeObserver | null = null
let mo: MutationObserver | null = null

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || '#000'
}

function fmtLabel(ts: number): string {
  const d = new Date(ts)
  const p = (x: number) => String(x).padStart(2, '0')
  return `${p(d.getHours())}:${p(d.getMinutes())}`
}

function num(v: unknown, digits: number): string {
  return Number(v ?? 0).toFixed(digits)
}

function render() {
  if (!chart) return
  const grid = cssVar('--chart-grid')
  const axis = cssVar('--chart-axis')
  const accent = cssVar('--color-primary')
  const up = cssVar('--color-up')
  const down = cssVar('--color-down')
  const area = cssVar('--chart-area')
  const ai = cssVar('--color-ai-ink')
  const surface = cssVar('--color-surface')
  const border = cssVar('--color-border')
  const fg = cssVar('--color-fg')
  const subtle = cssVar('--color-subtle')

  const pts = props.points
  const labels = pts.map((p) => fmtLabel(p.ts))
  const prices = pts.map((p) => p.price)
  const vols = pts.map((p) => p.vol)
  const volColors = pts.map((p, i) => (i === 0 ? accent : p.price >= pts[i - 1].price ? up : down))
  const basePrice = prices.length ? prices[0] : 0
  const maxV = Math.max(...vols, 1)
  const digits = basePrice >= 1000 ? 2 : basePrice >= 1 ? 4 : 6

  chart.setOption(
    {
      animationDuration: 250,
      grid: [
        { left: 8, right: 66, top: 12, height: '60%' },
        { left: 8, right: 66, top: '76%', height: '14%' },
      ],
      tooltip: {
        trigger: 'axis',
        backgroundColor: surface,
        borderColor: border,
        textStyle: { color: fg, fontSize: 12, fontFamily: 'JetBrains Mono Variable, monospace' },
        formatter: (params: unknown) => {
          const arr = params as Array<{ seriesName?: string; dataIndex?: number; value?: number }>
          const idx = arr[0]?.dataIndex ?? 0
          const p = pts[idx]
          if (!p) return ''
          const avg = props.avg[idx]
          const row = (t: string, v: string, c?: string) =>
            `<div style="display:flex;justify-content:space-between;gap:16px"><span style="color:${subtle}">${t}</span><span>${c ? `<b style="color:${c}">` : ''}${v}${c ? '</b>' : ''}</span></div>`
          const c = p.price >= basePrice ? up : down
          return `<div style="font-size:12px">${fmtLabel(p.ts)}</div>${row('价格', num(p.price, digits), c)}${row('均价', num(avg ?? p.price, digits))}${row('成交量', p.vol.toFixed(2))}`
        },
      },
      xAxis: [
        {
          type: 'category', data: labels, boundaryGap: false, gridIndex: 0,
          axisLine: { lineStyle: { color: grid } }, axisTick: { show: false },
          axisLabel: { color: axis, fontSize: 10, fontFamily: 'JetBrains Mono Variable, monospace', hideOverlap: true },
        },
        {
          type: 'category', data: labels, boundaryGap: false, gridIndex: 1,
          axisLine: { lineStyle: { color: grid } }, axisTick: { show: false }, axisLabel: { show: false },
        },
      ],
      yAxis: [
        {
          type: 'value', scale: true, gridIndex: 0, position: 'right', splitNumber: 4,
          splitLine: { lineStyle: { color: grid } },
          axisLabel: { color: axis, fontSize: 10, fontFamily: 'JetBrains Mono Variable, monospace', formatter: (v: number) => v.toFixed(digits) },
        },
        {
          type: 'value', gridIndex: 1, show: false, min: 0, max: maxV * 1.1, splitLine: { show: false },
        },
      ],
      dataZoom: [{ type: 'inside', xAxisIndex: [0, 1], filterMode: 'none' }],
      series: [
        {
          name: '价格', type: 'line', data: prices, showSymbol: false, smooth: 0.35,
          lineStyle: { width: 1.6, color: accent }, itemStyle: { color: accent },
          areaStyle: { color: area },
          markLine:
            basePrice > 0
              ? {
                  silent: true, symbol: 'none',
                  label: { show: true, formatter: '今开', color: axis, fontSize: 10, position: 'insideEndTop' },
                  lineStyle: { color: grid, type: 'dashed', width: 1 },
                  data: [{ yAxis: basePrice }],
                }
              : undefined,
        },
        {
          name: '均价', type: 'line', data: props.avg, showSymbol: false, smooth: 0.35,
          lineStyle: { width: 1.2, type: 'dashed', color: ai }, itemStyle: { color: ai }, symbol: 'none',
        },
        {
          name: '量', type: 'bar', xAxisIndex: 1, yAxisIndex: 1, data: vols, silent: true, barMaxWidth: 5,
          itemStyle: { color: (p: { dataIndex: number }) => volColors[p.dataIndex] ?? accent, opacity: 0.75 },
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

watch(() => props.points, () => render(), { deep: false })
</script>

<template>
  <div ref="el" class="w-full" :style="{ height: `${height}px` }" />
</template>
