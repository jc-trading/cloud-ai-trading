<template>
  <div class="equity-overlay">
    <svg v-if="chart.lines.length" :viewBox="`0 0 ${W} ${height}`" class="equity-svg">
      <line :x1="PAD_L" :x2="W" :y1="chart.yOf(chart.ymax)" :y2="chart.yOf(chart.ymax)" class="grid" />
      <line :x1="PAD_L" :x2="W" :y1="chart.yOf(chart.ymin)" :y2="chart.yOf(chart.ymin)" class="grid" />
      <line v-if="chart.ymin <= 100 && 100 <= chart.ymax" :x1="PAD_L" :x2="W" :y1="chart.yOf(100)" :y2="chart.yOf(100)"
            class="base" />
      <text :x="PAD_L - 6" :y="chart.yOf(chart.ymax) + 4" text-anchor="end" class="label">{{ chart.ymax.toFixed(0) }}</text>
      <text :x="PAD_L - 6" :y="chart.yOf(chart.ymin)" text-anchor="end" class="label">{{ chart.ymin.toFixed(0) }}</text>
      <text v-if="chart.ymin < 100 && 100 < chart.ymax" :x="PAD_L - 6" :y="chart.yOf(100) + 4" text-anchor="end" class="label">100</text>
      <text :x="PAD_L" :y="height - 4" class="label">{{ chart.firstDate }}</text>
      <text :x="W" :y="height - 4" text-anchor="end" class="label">{{ chart.lastDate }}</text>
      <polyline v-for="l in chart.lines" :key="l.id" :points="l.points" fill="none"
                :stroke="l.color" stroke-width="1.5" vector-effect="non-scaling-stroke" />
    </svg>
    <div v-if="chart.lines.length" class="legend">
      <span v-for="l in chart.lines" :key="l.id" class="legend-item">
        <i :style="{ background: l.color }"></i>{{ l.label }}
      </span>
    </div>
    <p v-for="label in chart.skipped" :key="label" class="jd-src">not enough data: {{ label }}</p>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  series: { type: Array, default: () => [] },
  height: { type: Number, default: 260 },
})

const W = 800
const PAD_L = 44
const PAD_B = 20
const PAD_T = 8
const MAX_POINTS = 600
const COLORS = ['var(--jd-cyan)', 'var(--jd-purple)', 'var(--jd-green)', 'var(--jd-yellow)',
  'var(--jd-orange)', 'var(--jd-red)', 'var(--jd-teal)', 'var(--jd-text-muted)']

const toTime = (d) => {
  const [y, m, day] = String(d).slice(0, 10).split('-').map(Number)
  return Date.UTC(y, m - 1, day)
}

/**
 * Rebases every usable series to 100 at its first point, thins it to at most
 * ~600 points, and maps all of them onto one shared time axis and value axis.
 * Series with fewer than 2 points or a non-positive first value are skipped
 * (division by zero would erase the line) and reported by label.
 */
const chart = computed(() => {
  const skipped = []
  const prepared = []
  props.series.forEach((s, idx) => {
    const n = s.values?.length || 0
    if (n < 2 || !(s.values[0] > 0)) {
      skipped.push(s.label)
      return
    }
    const step = n > MAX_POINTS ? Math.ceil(n / MAX_POINTS) : 1
    const pts = []
    for (let i = 0; i < n; i++) {
      if (i % step === 0 || i === n - 1) pts.push([toTime(s.dates[i]), (s.values[i] / s.values[0]) * 100, s.dates[i]])
    }
    prepared.push({ id: s.id ?? idx, label: s.label, color: COLORS[idx % COLORS.length], pts })
  })
  const all = prepared.flatMap((p) => p.pts)
  if (!all.length) return { lines: [], skipped }
  let tmin = Infinity, tmax = -Infinity, ymin = Infinity, ymax = -Infinity
  let firstDate = '', lastDate = ''
  for (const [t, v, d] of all) {
    if (t < tmin) { tmin = t; firstDate = String(d).slice(0, 10) }
    if (t > tmax) { tmax = t; lastDate = String(d).slice(0, 10) }
    if (v < ymin) ymin = v
    if (v > ymax) ymax = v
  }
  if (ymin === ymax) { ymin -= 1; ymax += 1 }
  const tspan = tmax - tmin || 1
  const plotH = props.height - PAD_B - PAD_T
  const xOf = (t) => PAD_L + ((t - tmin) / tspan) * (W - PAD_L)
  const yOf = (v) => PAD_T + (1 - (v - ymin) / (ymax - ymin)) * plotH
  const lines = prepared.map((p) => ({
    ...p,
    points: p.pts.map(([t, v]) => `${xOf(t).toFixed(1)},${yOf(v).toFixed(1)}`).join(' '),
  }))
  return { lines, skipped, ymin, ymax, yOf, firstDate, lastDate }
})
</script>

<style scoped>
.equity-svg { display: block; width: 100%; height: auto; }
.grid { stroke: var(--jd-line-2); stroke-width: 1; vector-effect: non-scaling-stroke; }
.base { stroke: var(--jd-text-faint); stroke-width: 1; stroke-dasharray: 4 4; vector-effect: non-scaling-stroke; }
.label { font-family: var(--jd-mono); font-size: 10px; fill: var(--jd-text-faint); }
.legend { display: flex; flex-wrap: wrap; gap: 14px; margin-top: 8px; font-size: 12px; color: var(--jd-text-muted); }
.legend-item { display: inline-flex; align-items: center; gap: 6px; }
.legend-item i { width: 12px; height: 3px; border-radius: 2px; display: inline-block; }
</style>
