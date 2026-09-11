<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts'
import type { ECharts, EChartsOption } from 'echarts'
import type { MlLearning, MlOosCharts } from '@/api/factor'

// 只依赖诊断图表字段，便于训练页与流水线轮次详情共用
const props = defineProps<{ result: { oos_charts?: MlOosCharts; learning?: MlLearning } }>()

const decilesEl = ref<HTMLDivElement | null>(null)
const scatterEl = ref<HTMLDivElement | null>(null)
const navEl = ref<HTMLDivElement | null>(null)
const learnEl = ref<HTMLDivElement | null>(null)

const charts: Record<string, ECharts | null> = {}
let ro: ResizeObserver | null = null
let mo: MutationObserver | null = null

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || '#000'
}

function fmtPct(v: number, digits = 2): string {
  return `${(v * 100).toFixed(digits)}%`
}
function shortTs(ts: string): string {
  const d = new Date(ts)
  const pad = (n: number) => String(n).padStart(2, '0')
  const md = `${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
  return d.getHours() || d.getMinutes() ? `${md} ${pad(d.getHours())}:${pad(d.getMinutes())}` : md
}
function pearson(xs: number[], ys: number[]): number | null {
  const n = Math.min(xs.length, ys.length)
  if (n < 2) return null
  const mx = xs.reduce((s, v) => s + v, 0) / n
  const my = ys.reduce((s, v) => s + v, 0) / n
  let num = 0
  let dx = 0
  let dy = 0
  for (let i = 0; i < n; i++) {
    const a = xs[i] - mx
    const b = ys[i] - my
    num += a * b
    dx += a * a
    dy += b * b
  }
  return dx && dy ? num / Math.sqrt(dx * dy) : null
}

const scatter = computed(() => props.result.oos_charts?.scatter ?? [])
const deciles = computed(() => props.result.oos_charts?.deciles ?? [])
const nav = computed(() => props.result.oos_charts?.nav)
const learning = computed(() => props.result.learning)
const learnGain = computed(() => {
  const t = learning.value?.train
  if (!t || t.length < 2 || !t[0]) return null
  return (t[0] - t[t.length - 1]) / t[0]
})

const scatterR = computed(() => {
  const p = scatter.value
  if (!p.length) return null
  return pearson(p.map((d) => d.pred), p.map((d) => d.actual))
})
const monoR = computed(() => {
  const d = deciles.value
  if (!d.length) return null
  return pearson(d.map((_, i) => i), d.map((x) => x.mean_actual))
})
const navEnds = computed(() => {
  const n = nav.value
  if (!n) return null
  const last = (arr: number[]) => (arr.length ? arr[arr.length - 1] : null)
  return { q5: last(n.q5), q1: last(n.q1) }
})

const NAV_SERIES = [
  { key: 'q5', name: 'Q5 高预测', cvar: '--chart-up' },
  { key: 'q4', name: 'Q4', cvar: '--chart-series' },
  { key: 'q3', name: 'Q3 中位', cvar: '--chart-benchmark' },
  { key: 'q2', name: 'Q2', cvar: '--color-warn' },
  { key: 'q1', name: 'Q1 低预测', cvar: '--chart-down' },
] as const

function baseAxis(): { axisLine: object; axisTick: object; axisLabel: object } {
  return {
    axisLine: { lineStyle: { color: cssVar('--chart-grid') } },
    axisTick: { show: false },
    axisLabel: { color: cssVar('--chart-axis'), fontSize: 10, fontFamily: 'JetBrains Mono Variable, monospace' },
  }
}
function tooltip(trigger: 'axis' | 'item'): object {
  return {
    trigger,
    backgroundColor: cssVar('--color-surface'),
    borderColor: cssVar('--color-border'),
    textStyle: { color: cssVar('--color-fg'), fontSize: 11, fontFamily: 'JetBrains Mono Variable, monospace' },
  }
}

function decilesOption(): EChartsOption {
  const d = deciles.value
  const up = cssVar('--chart-up')
  const down = cssVar('--chart-down')
  return {
    animationDuration: 400,
    grid: { left: 12, right: 10, top: 24, bottom: 4, containLabel: true },
    tooltip: tooltip('item'),
    xAxis: {
      type: 'category',
      data: d.map((x) => x.label),
      ...baseAxis(),
      axisLabel: { ...baseAxis().axisLabel, interval: 0 },
    },
    yAxis: {
      type: 'value',
      scale: true,
      splitLine: { lineStyle: { color: cssVar('--chart-grid') } },
      axisLabel: { ...baseAxis().axisLabel, formatter: (v: number) => fmtPct(v, 1) },
    },
    series: [
      {
        name: '平均实际收益',
        type: 'bar',
        barMaxWidth: 22,
        data: d.map((x) => ({
          value: x.mean_actual,
          itemStyle: { color: x.mean_actual >= 0 ? up : down, opacity: 0.82, borderRadius: [3, 3, 0, 0] },
          meanPred: x.mean_pred,
          n: x.n,
        })),
        markLine: {
          silent: true,
          symbol: 'none',
          lineStyle: { color: cssVar('--chart-grid'), type: 'dashed', width: 1 },
          data: [{ yAxis: 0 }],
        },
      },
    ],
  }
}

function scatterOption(): EChartsOption {
  const p = scatter.value
  const primary = cssVar('--color-primary')
  const axis = cssVar('--chart-axis')
  const grid = cssVar('--chart-grid')
  const xs = p.map((d) => d.pred)
  const ys = p.map((d) => d.actual)
  const r = scatterR.value
  let trend: Array<[number, number]> = []
  if (xs.length > 1) {
    const n = xs.length
    const mx = xs.reduce((s, v) => s + v, 0) / n
    const my = ys.reduce((s, v) => s + v, 0) / n
    let num = 0
    let den = 0
    for (let i = 0; i < n; i++) {
      num += (xs[i] - mx) * (ys[i] - my)
      den += (xs[i] - mx) * (xs[i] - mx)
    }
    if (den) {
      const slope = num / den
      const x0 = Math.min(...xs)
      const x1 = Math.max(...xs)
      trend = [
        [x0, my + slope * (x0 - mx)],
        [x1, my + slope * (x1 - mx)],
      ]
    }
  }
  return {
    animationDuration: 400,
    grid: { left: 12, right: 10, top: 20, bottom: 4, containLabel: true },
    tooltip: {
      ...tooltip('item'),
      formatter: (param: unknown) => {
        const p = param as { data: { ts: string; value: [number, number] } }
        const { ts: t, value } = p.data
        return `${t}<br/>预测分 <b>${value[0].toFixed(4)}</b><br/>实际收益 <b>${fmtPct(value[1])}</b>`
      },
    },
    xAxis: {
      type: 'value',
      scale: true,
      name: '模型预测分',
      nameTextStyle: { color: axis, fontSize: 10, fontFamily: 'JetBrains Mono Variable, monospace' },
      splitLine: { show: false },
      axisLine: { lineStyle: { color: grid } },
      axisLabel: {
        color: axis,
        fontSize: 10,
        fontFamily: 'JetBrains Mono Variable, monospace',
        formatter: (v: number) => v.toFixed(2),
      },
    },
    yAxis: {
      type: 'value',
      scale: true,
      name: '实际收益',
      nameTextStyle: { color: axis, fontSize: 10, fontFamily: 'JetBrains Mono Variable, monospace' },
      splitLine: { lineStyle: { color: grid } },
      axisLabel: {
        color: axis,
        fontSize: 10,
        fontFamily: 'JetBrains Mono Variable, monospace',
        formatter: (v: number) => fmtPct(v, 1),
      },
    },
    series: [
      {
        name: '样本外点',
        type: 'scatter',
        symbolSize: 5,
        data: p.map((d) => ({ value: [d.pred, d.actual], ts: d.ts })),
        itemStyle: { color: cssVar('--chart-area'), borderColor: primary, borderWidth: 0.6 },
      },
      ...(trend.length
        ? [
            {
              name: '线性趋势',
              type: 'line' as const,
              data: trend,
              showSymbol: false,
              silent: true,
              lineStyle: { color: cssVar('--color-ai'), width: 1.5, type: 'dashed' as const },
            },
          ]
        : []),
    ],
  }
}

function navOption(): EChartsOption {
  const n = nav.value
  const ts = n?.ts ?? []
  const cats = ts.map((t) => shortTs(t))
  const series = NAV_SERIES.map(({ key, name, cvar }) => {
    const arr = n?.[key] ?? []
    return {
      name,
      type: 'line' as const,
      data: arr,
      showSymbol: false,
      smooth: false,
      connectNulls: true,
      lineStyle: { width: key === 'q5' || key === 'q1' ? 1.8 : 1.2, color: cssVar(cvar) },
      itemStyle: { color: cssVar(cvar) },
    }
  })
  return {
    animationDuration: 400,
    grid: { left: 12, right: 14, top: 26, bottom: 4, containLabel: true },
    legend: {
      top: 0,
      left: 0,
      itemWidth: 12,
      itemHeight: 3,
      icon: 'roundRect',
      textStyle: { color: cssVar('--color-subtle'), fontSize: 10 },
    },
    tooltip: {
      ...tooltip('axis'),
      formatter: (params: unknown) => {
        const rows = params as Array<{ seriesName: string; dataIndex: number; value: number }>
        const head = ts[rows[0]?.dataIndex ?? 0] ?? ''
        const lines = rows.map((p) => `${p.seriesName} <b>${Number(p.value).toFixed(3)}</b>`)
        return `${head}<br/>${lines.join('<br/>')}`
      },
    },
    xAxis: { type: 'category', data: cats, boundaryGap: false, ...baseAxis() },
    yAxis: {
      type: 'value',
      scale: true,
      splitLine: { lineStyle: { color: cssVar('--chart-grid') } },
      axisLabel: { ...baseAxis().axisLabel, formatter: (v: number) => v.toFixed(2) },
    },
    series,
  }
}

function learningOption(): EChartsOption {
  const l = learning.value
  const rounds = l?.rounds ?? []
  const train = l?.train ?? []
  const oos = l?.oos ?? []
  const m = Math.min(rounds.length, train.length, oos.length)
  const primary = cssVar('--color-primary')
  const ai = cssVar('--color-ai')
  const best = l?.best_iteration && l.best_iteration > 0 ? l.best_iteration : null
  return {
    animationDuration: 400,
    grid: { left: 12, right: 16, top: 26, bottom: 4, containLabel: true },
    legend: {
      top: 0,
      left: 0,
      itemWidth: 12,
      itemHeight: 3,
      icon: 'roundRect',
      textStyle: { color: cssVar('--color-subtle'), fontSize: 10 },
    },
    tooltip: {
      ...tooltip('axis'),
      valueFormatter: (v: unknown) => Number(v).toExponential(2),
    },
    xAxis: {
      type: 'value',
      name: 'boosting 轮',
      nameTextStyle: { color: cssVar('--chart-axis'), fontSize: 10, fontFamily: 'JetBrains Mono Variable, monospace' },
      splitLine: { show: false },
      axisLine: { lineStyle: { color: cssVar('--chart-grid') } },
      axisLabel: {
        color: cssVar('--chart-axis'),
        fontSize: 10,
        fontFamily: 'JetBrains Mono Variable, monospace',
      },
      min: 1,
    },
    yAxis: {
      type: 'value',
      scale: true,
      name: 'MSE',
      nameTextStyle: { color: cssVar('--chart-axis'), fontSize: 10, fontFamily: 'JetBrains Mono Variable, monospace' },
      splitLine: { lineStyle: { color: cssVar('--chart-grid') } },
      axisLabel: {
        color: cssVar('--chart-axis'),
        fontSize: 10,
        fontFamily: 'JetBrains Mono Variable, monospace',
        formatter: (v: number) => v.toExponential(1),
      },
    },
    series: [
      {
        name: 'train loss',
        type: 'line',
        data: rounds.slice(0, m).map((r, i) => [r, train[i]] as [number, number]),
        showSymbol: false,
        lineStyle: { width: 1.6, color: primary },
        itemStyle: { color: primary },
      },
      {
        name: 'OOS loss',
        type: 'line',
        data: rounds.slice(0, m).map((r, i) => [r, oos[i]] as [number, number]),
        showSymbol: false,
        lineStyle: { width: 1.6, color: ai },
        itemStyle: { color: ai },
        ...(best
          ? {
              markLine: {
                silent: true,
                symbol: 'none',
                lineStyle: { color: ai, type: 'dashed' as const, width: 1 },
                label: {
                  formatter: `best #${best}`,
                  color: ai,
                  fontSize: 9,
                  position: 'insideEndTop' as const,
                },
                data: [{ xAxis: best }],
              },
            }
          : {}),
      },
    ],
  }
}

function renderAll() {
  const opts: Array<[string, ECharts | null, () => EChartsOption]> = [
    ['deciles', charts.deciles, decilesOption],
    ['scatter', charts.scatter, scatterOption],
    ['nav', charts.nav, navOption],
  ]
  if (learning.value && (learning.value.train?.length ?? 0) > 1) {
    opts.push(['learn', charts.learn, learningOption])
  }
  for (const [key, chart, build] of opts) {
    chart?.setOption(build(), true)
  }
}

function initCharts() {
  const refs: Array<[string, HTMLDivElement | null]> = [
    ['deciles', decilesEl.value],
    ['scatter', scatterEl.value],
    ['nav', navEl.value],
    ['learn', learnEl.value],
  ]
  for (const [key, el] of refs) {
    if (!el) continue
    if (charts[key]) continue
    charts[key] = echarts.init(el)
  }
  renderAll()
  ro?.disconnect()
  ro = new ResizeObserver(() => {
    for (const c of Object.values(charts)) c?.resize()
  })
  for (const el of refs.map(([, el]) => el)) {
    if (el) ro.observe(el)
  }
}

onMounted(() => {
  initCharts()
  mo = new MutationObserver(() => renderAll())
  mo.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] })
})

watch(
  () => props.result,
  () => initCharts(),
  { deep: true },
)

onBeforeUnmount(() => {
  ro?.disconnect()
  mo?.disconnect()
  for (const c of Object.values(charts)) c?.dispose()
})
</script>

<template>
  <div class="grid grid-cols-1 gap-3 lg:grid-cols-2">
    <section class="panel p-3">
      <div class="mb-2 flex items-baseline justify-between gap-2">
        <p class="text-[11px] font-semibold uppercase tracking-wider text-subtle">预测分层 · 单调性</p>
        <p class="num text-[10px] text-muted">D1 低预测 → D{{ deciles.length }} 高预测 · 单调 r {{ monoR != null ? monoR.toFixed(2) : '—' }}</p>
      </div>
      <p class="mb-1 text-[10px] leading-snug text-subtle">把样本外按预测分等分，每桶平均“实际未来收益”；桶间越单调递增，分层能力越强。</p>
      <div ref="decilesEl" class="h-52 w-full" />
    </section>

    <section class="panel p-3">
      <div class="mb-2 flex items-baseline justify-between gap-2">
        <p class="text-[11px] font-semibold uppercase tracking-wider text-subtle">预测 vs 实际</p>
        <p class="num text-[10px] text-muted">样本相关 r {{ scatterR != null ? scatterR.toFixed(2) : '—' }} · n={{ scatter.length }}</p>
      </div>
      <p class="mb-1 text-[10px] leading-snug text-subtle">样本外逐样本：横轴模型预测分，纵轴实际收益。点云随横轴抬升、拟合线向上即线性预测力。</p>
      <div ref="scatterEl" class="h-52 w-full" />
    </section>

    <section class="panel p-3">
      <div class="mb-2 flex items-baseline justify-between gap-2">
        <p class="text-[11px] font-semibold uppercase tracking-wider text-subtle">分层累计净值</p>
        <p class="num text-[10px] text-muted">
          Q5 终值 {{ navEnds?.q5 != null ? navEnds.q5.toFixed(3) : '—' }} · Q1 {{ navEnds?.q1 != null ? navEnds.q1.toFixed(3) : '—' }}
        </p>
      </div>
      <p class="mb-1 text-[10px] leading-snug text-subtle">按预测分五等分，各自在时间轴上累计（1+实际收益，仅本组 bar 持仓）；高分组净值应逐步跑赢低分组。</p>
      <div ref="navEl" class="h-56 w-full" />
    </section>

    <section v-if="learning && (learning.train?.length ?? 0) > 1" class="panel p-3">
      <div class="mb-2 flex items-baseline justify-between gap-2">
        <p class="text-[11px] font-semibold uppercase tracking-wider text-subtle">LightGBM 训练曲线</p>
        <p class="num text-[10px] text-muted">
          <template v-if="learning.best_iteration">早停 @ #{{ learning.best_iteration }}（OOS {{ learning.best_oos != null ? learning.best_oos.toExponential(2) : '—' }}）· </template>
          OOS 末段 {{ learning.oos?.[learning.oos.length - 1] != null ? learning.oos[learning.oos.length - 1].toExponential(2) : '—' }}
        </p>
      </div>
      <p class="mb-1 text-[10px] leading-snug text-subtle">每 boosting 轮的 MSE。虚线即早停选中的最优轮（此后 OOS 开始变差）；train 与 OOS 拉开的距离就是过拟合程度。</p>
      <p v-if="learnGain != null && learnGain < 0.05" class="mb-1 rounded-md border border-warn/40 bg-warn/5 px-2 py-1 text-[10px] leading-snug text-warn">
        训练损失几乎无下降（{{ (learnGain * 100).toFixed(1) }}%）：模型未能从这些因子上学到有效信号——通常是因子无预测力、样本不足，或因子被裁剪成了常数。
      </p>
      <div ref="learnEl" class="h-52 w-full" />
    </section>
  </div>
</template>
