import { AXIS_STYLE, baseOption, type EChartsOption } from '../charts/EChart'
import { FREQ_WORD, type MatrixResult, type Nums, type PairResult, displayScale, levelUnit, rho } from './words'

// keep the dark tooltip / legend styling of the shared base when an option overrides these keys
const TIP = baseOption.tooltip as object
const LEG = baseOption.legend as object

export const COLOR_A = '#f5a524'
export const COLOR_B = '#5aa9ff'
const day = (s: string) => s.slice(0, 10)
const fmtN = (v: number, d = 2) => v.toLocaleString('en-US', { maximumFractionDigits: d })

/** Both series over time — two stacked panels sharing the time axis (no dual y-axis), or one panel rebased to 100. */
export function overTimeOption(r: PairResult, nameA: string, nameB: string, rebased: boolean): EChartsOption | null {
  const f = r.frame
  const ts = f.ts.map(day)
  const line = (name: string, data: Nums, color: string, idx = 0) => ({ name, type: 'line' as const, data, showSymbol: false, sampling: 'lttb' as const, lineStyle: { width: 1.5, color }, itemStyle: { color }, xAxisIndex: idx, yAxisIndex: idx })
  if (rebased) {
    const rb = (v: Nums) => {
      const first = v.find((x) => x !== null && x > 0)
      return first ? v.map((x) => (x === null ? null : (x / first) * 100)) : null
    }
    const a = rb(f.a)
    const b = rb(f.b)
    if (!a || !b || f.a.some((x) => x !== null && x <= 0) || f.b.some((x) => x !== null && x <= 0)) return null
    return {
      grid: { left: 56, right: 20, top: 30, bottom: 28 },
      legend: { ...LEG, data: [nameA, nameB] },
      tooltip: { ...TIP, trigger: 'axis', valueFormatter: (v) => (typeof v === 'number' ? fmtN(v, 1) : '—') },
      xAxis: { type: 'category', data: ts, boundaryGap: false, ...AXIS_STYLE },
      yAxis: { type: 'value', scale: true, name: 'Start = 100', ...AXIS_STYLE },
      series: [line(nameA, a, COLOR_A), line(nameB, b, COLOR_B)],
    }
  }
  return {
    grid: [
      { left: 64, right: 20, top: 30, height: '36%' },
      { left: 64, right: 20, top: '60%', height: '30%' },
    ],
    legend: { ...LEG, data: [nameA, nameB] },
    tooltip: { ...TIP, trigger: 'axis', valueFormatter: (v) => (typeof v === 'number' ? fmtN(v, 3) : '—') },
    axisPointer: { link: [{ xAxisIndex: 'all' }] },
    xAxis: [
      { type: 'category', data: ts, boundaryGap: false, gridIndex: 0, ...AXIS_STYLE, axisLabel: { show: false } },
      { type: 'category', data: ts, boundaryGap: false, gridIndex: 1, ...AXIS_STYLE },
    ],
    yAxis: [
      { type: 'value', scale: true, gridIndex: 0, name: levelUnit(r.a), ...AXIS_STYLE },
      { type: 'value', scale: true, gridIndex: 1, name: levelUnit(r.b), ...AXIS_STYLE },
    ],
    series: [line(nameA, f.a, COLOR_A, 0), line(nameB, f.b, COLOR_B, 1)],
  }
}

export function rollingOption(r: PairResult): EChartsOption | null {
  const ro = r.rolling
  if (!ro || !ro.ts.length) return null
  const band = ro.hi.map((h, i) => (h === null || ro.lo[i] === null ? null : h - (ro.lo[i] as number)))
  return {
    grid: { left: 44, right: 16, top: 30, bottom: 28 },
    legend: { ...LEG, data: ['Rolling correlation', '95% range'] },
    tooltip: { ...TIP, trigger: 'axis', valueFormatter: (v) => (typeof v === 'number' ? rho(v) : '—') },
    xAxis: { type: 'category', data: ro.ts.map(day), boundaryGap: false, ...AXIS_STYLE },
    yAxis: { type: 'value', min: -1, max: 1, ...AXIS_STYLE },
    series: [
      { name: 'lo', type: 'line', data: ro.lo, stack: 'ci', stackStrategy: 'all', showSymbol: false, lineStyle: { opacity: 0 }, silent: true, tooltip: { show: false } },
      { name: '95% range', type: 'line', data: band, stack: 'ci', stackStrategy: 'all', showSymbol: false, lineStyle: { opacity: 0 }, areaStyle: { color: 'rgba(245,165,36,.14)' }, itemStyle: { color: 'rgba(245,165,36,.4)' }, silent: true, tooltip: { show: false } },
      {
        name: 'Rolling correlation',
        type: 'line',
        data: ro.pearson,
        showSymbol: false,
        sampling: 'lttb',
        lineStyle: { width: 2, color: COLOR_A },
        itemStyle: { color: COLOR_A },
        markLine: { silent: true, symbol: 'none', label: { show: false }, lineStyle: { color: '#626b7a', type: 'dashed' }, data: [{ yAxis: 0 }] },
      },
    ],
  }
}

/** Scatter of the compared changes (A on the vertical axis, B horizontal) with a least-squares line fitted to the dots shown. */
export function scatterOption(r: PairResult, shortA: string, shortB: string): { option: EChartsOption; fit: { slope: number; intercept: number; r2: number } | null; unitA: string; unitB: string } | null {
  const f = r.frame
  const sa = displayScale(r.a)
  const sb = displayScale(r.b)
  const pts: [number, number][] = []
  for (let i = 0; i < f.ts.length; i++) {
    const x = f.b_t[i]
    const y = f.a_t[i]
    if (x !== null && y !== null && Number.isFinite(x) && Number.isFinite(y)) pts.push([x * sb.k, y * sa.k])
  }
  if (pts.length < 3) return null
  const n = pts.length
  const mx = pts.reduce((s, p) => s + p[0], 0) / n
  const my = pts.reduce((s, p) => s + p[1], 0) / n
  let sxx = 0
  let sxy = 0
  let syy = 0
  for (const [x, y] of pts) {
    sxx += (x - mx) ** 2
    sxy += (x - mx) * (y - my)
    syy += (y - my) ** 2
  }
  const fit = sxx > 0 && syy > 0 ? { slope: sxy / sxx, intercept: my - (sxy / sxx) * mx, r2: (sxy * sxy) / (sxx * syy) } : null
  const xs = pts.map((p) => p[0])
  const lo = Math.min(...xs)
  const hi = Math.max(...xs)
  const fw = FREQ_WORD[r.freq] ?? ''
  const option: EChartsOption = {
    grid: { left: 56, right: 18, top: 16, bottom: 44 },
    legend: { show: false },
    tooltip: { ...TIP, trigger: 'item', formatter: (p: any) => (Array.isArray(p.data) && p.seriesName === 'dots' ? `${shortB}: ${fmtN(p.data[0])} ${sb.unit}<br/>${shortA}: ${fmtN(p.data[1])} ${sa.unit}` : '') },
    xAxis: { type: 'value', scale: true, name: `${shortB} — ${fw} change (${sb.unit})`, nameLocation: 'middle', nameGap: 28, ...AXIS_STYLE },
    yAxis: { type: 'value', scale: true, name: `${shortA} (${sa.unit})`, ...AXIS_STYLE },
    series: [
      { name: 'dots', type: 'scatter', data: pts, symbolSize: 5, itemStyle: { color: 'rgba(90,169,255,.5)' }, large: pts.length > 2000 },
      ...(fit ? [{ name: 'fit', type: 'line' as const, data: [[lo, fit.intercept + fit.slope * lo], [hi, fit.intercept + fit.slope * hi]], showSymbol: false, silent: true, lineStyle: { color: COLOR_A, width: 2 } }] : []),
    ],
  }
  return { option, fit, unitA: sa.unit, unitB: sb.unit }
}

export function leadLagOption(r: PairResult): EChartsOption | null {
  const ll = r.lead_lag
  if (!ll) return null
  const band = ll.band ?? 0
  return {
    grid: { left: 44, right: 16, top: 16, bottom: 40 },
    legend: { show: false },
    tooltip: { ...TIP, trigger: 'axis', valueFormatter: (v) => (typeof v === 'number' ? rho(v) : '—') },
    xAxis: { type: 'category', data: ll.lags.map((l) => String(l.lag)), name: 'Lag k (positive = B moves first)', nameLocation: 'middle', nameGap: 26, ...AXIS_STYLE },
    yAxis: { type: 'value', ...AXIS_STYLE },
    series: [
      {
        type: 'bar',
        name: 'Correlation',
        data: ll.lags.map((l) => ({ value: l.corr, itemStyle: { color: l.lag === ll.best_lag ? COLOR_A : '#4b5566', borderRadius: 3 } })),
        markLine: { silent: true, symbol: 'none', label: { show: false }, lineStyle: { color: '#626b7a', type: 'dashed' }, data: [{ yAxis: band }, { yAxis: -band }] },
      },
    ],
  }
}

export function betaOption(r: PairResult): EChartsOption | null {
  const rb = r.rolling_beta
  if (!rb || !rb.ts.length) return null
  return {
    grid: { left: 52, right: 16, top: 16, bottom: 28 },
    legend: { show: false },
    tooltip: { ...TIP, trigger: 'axis', valueFormatter: (v) => (typeof v === 'number' ? fmtN(v, 4) : '—') },
    xAxis: { type: 'category', data: rb.ts.map(day), boundaryGap: false, ...AXIS_STYLE },
    yAxis: { type: 'value', scale: true, ...AXIS_STYLE },
    series: [{ name: 'Rolling beta', type: 'line', data: rb.beta, showSymbol: false, sampling: 'lttb', lineStyle: { width: 1.5, color: COLOR_B }, itemStyle: { color: COLOR_B } }],
  }
}

export function spreadOption(r: PairResult): EChartsOption | null {
  const c = r.cointegration
  if (!c || !c.ts?.length) return null
  return {
    grid: { left: 44, right: 16, top: 16, bottom: 28 },
    legend: { show: false },
    tooltip: { ...TIP, trigger: 'axis', valueFormatter: (v) => (typeof v === 'number' ? fmtN(v, 2) : '—') },
    xAxis: { type: 'category', data: c.ts.map(day), boundaryGap: false, ...AXIS_STYLE },
    yAxis: { type: 'value', ...AXIS_STYLE },
    series: [
      {
        name: 'Gap vs normal (z)',
        type: 'line',
        data: c.zscore,
        showSymbol: false,
        sampling: 'lttb',
        lineStyle: { width: 1.5, color: COLOR_B },
        itemStyle: { color: COLOR_B },
        markLine: { silent: true, symbol: 'none', label: { show: false }, lineStyle: { color: '#626b7a', type: 'dashed' }, data: [{ yAxis: 2 }, { yAxis: -2 }, { yAxis: 0 }] },
      },
    ],
  }
}

/** Correlation matrix with plain names on both axes, reordered so clusters sit together. */
export function matrixOption(m: MatrixResult, name: (id: string) => string): { option: EChartsOption; order: number[] } {
  const n = m.labels.length
  const order = m.order && m.order.length === n ? m.order : m.labels.map((_, i) => i)
  const labels = order.map((i) => name(m.labels[i]))
  const short = (s: string) => (s.length > 26 ? `${s.slice(0, 25)}…` : s)
  const data: [number, number, number | null][] = []
  order.forEach((ri, y) => order.forEach((ci, x) => data.push([x, y, m.matrix[ri]?.[ci] ?? null])))
  return {
    order,
    option: {
      grid: { left: 190, right: 20, top: 10, bottom: 150 },
      legend: { show: false },
      tooltip: {
        ...TIP,
        trigger: 'item',
        formatter: (p: any) => {
          const [x, y, v] = p.data as [number, number, number | null]
          return `${labels[y]}<br/>vs ${labels[x]}<br/><b>ρ = ${v === null ? 'n/a' : rho(v)}</b>`
        },
      },
      xAxis: { type: 'category', data: labels.map(short), axisLabel: { rotate: 55, color: '#8a93a3', fontSize: 10, interval: 0 }, axisLine: AXIS_STYLE.axisLine, splitArea: { show: false } },
      yAxis: { type: 'category', data: labels.map(short), inverse: true, axisLabel: { color: '#8a93a3', fontSize: 10, interval: 0 }, axisLine: AXIS_STYLE.axisLine },
      visualMap: { min: -1, max: 1, calculable: false, orient: 'horizontal', left: 'center', bottom: 0, itemHeight: 180, text: ['move together', 'move opposite'], textStyle: { color: '#8a93a3' }, inRange: { color: ['#5aa9ff', '#2a303b', '#f5a524'] } },
      series: [
        {
          type: 'heatmap',
          data: data.map((d) => [d[0], d[1], d[2] === null ? '-' : d[2]]),
          label: { show: n <= 16, color: '#e7eaf0', fontSize: 9, formatter: (p: any) => (typeof p.data[2] === 'number' ? rho(p.data[2]) : '') },
          itemStyle: { borderColor: '#151920', borderWidth: 1 },
          emphasis: { itemStyle: { borderColor: '#f5a524', borderWidth: 1.5 } },
        },
      ],
    },
  }
}
