import { useMemo } from 'react'
import EChart, { type EChartsOption } from '../charts/EChart'
import { VIZ, axisCat, axisVal, vizBase } from '../financials/shared'
import { methodName, type FFRow, type Grid, type ModelInfo } from './model'

const fmtPS = (sym: string, v: number | null | undefined, d = 2) =>
  v === null || v === undefined || !Number.isFinite(v) ? '—' : `${sym}${v.toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d })}`

function rowLabel(r: FFRow, models?: ModelInfo[]): string {
  if (r.reference === 'base_dcf') return 'DCF (bear · base · bull)'
  if (r.reference === 'multiples') return 'Price multiples'
  return methodName(r.reference, models)
}

/** Horizontal floating bars (low → high) per method, a dot at the base value, lines for price and fair value. */
export function FootballField({ rows, price, fairValue, sym, models }: { rows: FFRow[]; price: number | null; fairValue: number | null; sym: string; models?: ModelInfo[] }) {
  const option = useMemo<EChartsOption>(() => {
    const items = rows.map((r) => {
      const base = r.base ?? r.low ?? r.high ?? 0
      const low = Math.min(r.low ?? base, base)
      const high = Math.max(r.high ?? base, base)
      return { label: rowLabel(r, models), low, base, high, raw: r }
    })
    const all = items.flatMap((i) => [i.low, i.high]).concat(price ?? [], fairValue ?? [])
    const lo = Math.min(...all)
    const hi = Math.max(...all)
    const pad = (hi - lo) * 0.08 || hi * 0.1 || 1
    const min = Math.max(0, Math.floor((lo - pad) / 10) * 10)
    const max = Math.ceil((hi + pad) / 10) * 10
    const marks: any[] = []
    if (price !== null)
      marks.push({ xAxis: price, lineStyle: { color: VIZ.text, type: 'dashed', width: 1.5 }, label: { formatter: `Price ${fmtPS(sym, price, 0)}`, color: VIZ.text, position: 'start', fontSize: 11 } })
    if (fairValue !== null)
      marks.push({ xAxis: fairValue, lineStyle: { color: VIZ.accent, type: 'solid', width: 2 }, label: { formatter: `Fair value ${fmtPS(sym, fairValue, 0)}`, color: VIZ.accent, position: 'start', fontSize: 11 } })
    return {
      ...vizBase,
      legend: { show: false },
      grid: { left: 190, right: 24, top: 30, bottom: 26 },
      tooltip: {
        ...vizBase.tooltip,
        trigger: 'item',
        formatter: (p: any) => {
          const it = items[p.dataIndex]
          if (!it) return ''
          const single = it.low === it.high
          return `<b>${it.label}</b><br/>${single ? fmtPS(sym, it.base) : `Low ${fmtPS(sym, it.low)} · base ${fmtPS(sym, it.base)} · high ${fmtPS(sym, it.high)}`}`
        },
      },
      xAxis: { type: 'value', min, max, ...axisVal, axisLabel: { ...axisVal.axisLabel, formatter: (v: number) => `${sym}${v}` } },
      yAxis: { type: 'category', data: items.map((i) => i.label), inverse: true, ...axisCat, axisLabel: { color: VIZ.text, fontSize: 12, width: 180, overflow: 'truncate' } },
      series: [
        { type: 'bar', stack: 'range', stackStrategy: 'all', data: items.map((i) => i.low), itemStyle: { color: 'transparent' }, emphasis: { disabled: true }, tooltip: { show: false }, silent: true, barWidth: 16 },
        {
          type: 'bar',
          stack: 'range',
          stackStrategy: 'all',
          data: items.map((i) => i.high - i.low),
          itemStyle: { color: VIZ.blue, borderRadius: 3, opacity: 0.85 },
          barWidth: 16,
          markLine: { symbol: 'none', silent: true, data: marks },
        },
        {
          type: 'scatter',
          data: items.map((i, idx) => [i.base, idx]),
          symbol: 'circle',
          symbolSize: 11,
          itemStyle: { color: VIZ.text, borderColor: VIZ.blue, borderWidth: 2 },
          label: { show: true, position: 'top', distance: 6, color: VIZ.text, fontSize: 11, formatter: (p: any) => fmtPS(sym, p.value[0], 0) },
          z: 5,
        },
      ],
    } as EChartsOption
  }, [rows, price, fairValue, sym, models])
  return <EChart option={option} height={Math.max(170, rows.length * 46 + 70)} />
}

const pctLabel = (v: number) => `${(v * 100).toFixed(1)}%`

/** WACC × terminal growth grid. Colour = value vs the current price (red below, green above, grey at the price);
 * the outlined cell is the combination the base case uses. */
export function SensitivityHeatmap({ grid, price, sym }: { grid: Grid; price: number | null; sym: string }) {
  const option = useMemo<EChartsOption>(() => {
    const xs = grid.x.values
    const ys = grid.y.values
    const nearest = (arr: number[], v: number | undefined) => (v === undefined ? -1 : arr.reduce((best, x, i) => (Math.abs(x - v) < Math.abs(arr[best] - v) ? i : best), 0))
    const cx = nearest(xs, grid.center?.wacc)
    const cy = nearest(ys, grid.center?.g)
    const data: any[] = []
    let maxAbs = 0
    grid.values.forEach((row, yi) =>
      row.forEach((v, xi) => {
        if (v === null || v === undefined) return
        const up = price ? v / price - 1 : 0
        maxAbs = Math.max(maxAbs, Math.abs(up))
        const base = xi === cx && yi === cy
        data.push({ value: [xi, yi, v, up], itemStyle: base ? { borderColor: VIZ.accent, borderWidth: 2.5 } : { borderColor: '#151920', borderWidth: 2 } })
      }),
    )
    const m = Math.max(maxAbs, 0.05)
    return {
      ...vizBase,
      legend: { show: false },
      grid: { left: 64, right: 12, top: 12, bottom: 46 },
      tooltip: {
        ...vizBase.tooltip,
        trigger: 'item',
        formatter: (p: any) => {
          const [xi, yi, v, up] = p.value
          const vs = price ? `<br/>${up >= 0 ? '+' : ''}${(up * 100).toFixed(0)}% vs the current price` : ''
          return `Discount rate ${pctLabel(xs[xi])}, terminal growth ${pctLabel(ys[yi])}<br/><b>${fmtPS(sym, v)}</b> per share${vs}${xi === cx && yi === cy ? '<br/><span style="color:#f5a524">Base-case assumptions</span>' : ''}`
        },
      },
      xAxis: { type: 'category', data: xs.map(pctLabel), ...axisCat, name: 'Discount rate (WACC)', nameLocation: 'middle', nameGap: 28, nameTextStyle: { color: VIZ.muted, fontSize: 11 }, splitArea: { show: false } },
      yAxis: { type: 'category', data: ys.map(pctLabel), ...axisCat, name: 'Terminal growth', nameLocation: 'middle', nameGap: 48, nameTextStyle: { color: VIZ.muted, fontSize: 11 } },
      visualMap: { show: false, dimension: 3, min: -m, max: m, inRange: { color: ['#b8383c', VIZ.neutral, '#23915a'] } },
      series: [{ type: 'heatmap', data, label: { show: true, color: VIZ.text, fontSize: 11, formatter: (p: any) => fmtPS(sym, p.value[2], 0) }, emphasis: { itemStyle: { borderColor: VIZ.text, borderWidth: 1 } } }],
    } as EChartsOption
  }, [grid, price, sym])
  return <EChart option={option} height={250} />
}

/** One sentence describing where the current price sits in the sensitivity grid. */
export function sensitivitySentence(grid: Grid, price: number | null, sym: string): string {
  const vals = grid.values.flat().filter((v): v is number => v !== null && v !== undefined)
  if (!vals.length) return ''
  const lo = Math.min(...vals)
  const hi = Math.max(...vals)
  const range = `${fmtPS(sym, lo, 0)}–${fmtPS(sym, hi, 0)}`
  if (!price) return `Across these combinations the DCF value ranges from ${range} per share.`
  const above = vals.filter((v) => v >= price).length
  if (above === 0) return `Every combination shown gives a value below the current price of ${fmtPS(sym, price, 0)} (range ${range}).`
  if (above === vals.length) return `Every combination shown gives a value above the current price of ${fmtPS(sym, price, 0)} (range ${range}).`
  return `${above} of ${vals.length} combinations give a value above the current price of ${fmtPS(sym, price, 0)} (range ${range}).`
}
