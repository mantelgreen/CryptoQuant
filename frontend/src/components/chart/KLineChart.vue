<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  createChart,
  type IChartApi,
  type ISeriesApi,
  type CandlestickData,
  type Time,
  ColorType,
} from 'lightweight-charts'
import type { KlineBar } from '@/api/data'

const props = withDefaults(
  defineProps<{
    bars: KlineBar[]
    height?: number
    latest?: { t: number; o: number; h: number; l: number; c: number; v?: number; closed?: boolean }
  }>(),
  { height: 420 },
)

const container = ref<HTMLDivElement | null>(null)
let chart: IChartApi | null = null
let series: ISeriesApi<'Candlestick'> | null = null
let ro: ResizeObserver | null = null
let obs: MutationObserver | null = null

function cssVar(name: string): string {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  return v || '#000000'
}

function palette() {
  return {
    surface: cssVar('--color-surface'),
    up: cssVar('--color-up'),
    down: cssVar('--color-down'),
    grid: cssVar('--chart-grid'),
    cross: cssVar('--chart-cross'),
    axis: cssVar('--chart-axis'),
    accent: cssVar('--color-primary'),
  }
}

function toSeries(): CandlestickData[] {
  return props.bars.map((b) => ({
    time: (new Date(b.open_time).getTime() / 1000) as Time,
    open: b.open,
    high: b.high,
    low: b.low,
    close: b.close,
  }))
}

function liveBar(): CandlestickData | null {
  const l = props.latest
  if (!l || !l.t) return null
  return {
    time: Math.round(l.t / 1000) as Time,
    open: l.o,
    high: l.h,
    low: l.l,
    close: l.c,
  }
}

function refresh() {
  if (!series) return
  series.setData(toSeries())
  const live = liveBar()
  if (live && props.bars.length) {
    const lastMs = new Date(props.bars[props.bars.length - 1].open_time).getTime()
    if (Number(live.time) >= Math.round(lastMs / 1000)) series.update(live)
  }
}

function onLatestChange() {
  if (!series) return
  const live = liveBar()
  if (!live) return
  const data = series.data()
  const lastTime = data.length ? Number(data[data.length - 1].time) : 0
  if (lastTime && Number(live.time) < lastTime) return
  series.update(live)
}

function applyTheme() {
  if (!chart) return
  const p = palette()
  chart.applyOptions({
    layout: {
      background: { type: ColorType.Solid, color: p.surface },
      textColor: p.axis,
      fontFamily: "'JetBrains Mono Variable', ui-monospace, monospace",
    },
    grid: {
      vertLines: { color: p.grid },
      horzLines: { color: p.grid },
    },
    crosshair: {
      vertLine: { color: p.cross, labelBackgroundColor: p.accent },
      horzLine: { color: p.cross, labelBackgroundColor: p.accent },
    },
  })
  series?.applyOptions({
    upColor: p.up,
    downColor: p.down,
    borderVisible: false,
    wickUpColor: p.up,
    wickDownColor: p.down,
  })
}

function build() {
  if (!container.value) return
  const p = palette()
  chart = createChart(container.value, {
    width: container.value.clientWidth,
    height: props.height,
    layout: {
      background: { type: ColorType.Solid, color: p.surface },
      textColor: p.axis,
      fontFamily: "'JetBrains Mono Variable', ui-monospace, monospace",
    },
    grid: {
      vertLines: { color: p.grid },
      horzLines: { color: p.grid },
    },
    timeScale: { timeVisible: true, secondsVisible: false, rightOffset: 4, borderColor: p.grid },
    rightPriceScale: { borderColor: p.grid },
  })
  series = chart.addCandlestickSeries({
    upColor: p.up,
    downColor: p.down,
    borderVisible: false,
    wickUpColor: p.up,
    wickDownColor: p.down,
  })
  series.setData(toSeries())

  ro = new ResizeObserver(() => {
    if (chart && container.value) {
      chart.applyOptions({ width: container.value.clientWidth })
    }
  })
  ro.observe(container.value)

  obs = new MutationObserver(() => applyTheme())
  obs.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] })
}

function teardown() {
  ro?.disconnect()
  ro = null
  obs?.disconnect()
  obs = null
  if (chart) {
    chart.remove()
    chart = null
    series = null
  }
}

onMounted(build)
onBeforeUnmount(teardown)
watch(() => props.bars, () => refresh(), { deep: false })
watch(
  () => props.latest,
  () => onLatestChange(),
  { deep: true },
)
</script>

<template>
  <div ref="container" class="w-full" :style="{ height: `${height}px` }" />
</template>
