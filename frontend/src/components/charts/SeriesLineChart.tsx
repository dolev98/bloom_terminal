import { useEffect, useRef } from 'react'
import { createChart, LineSeries, type IChartApi, type UTCTimestamp } from 'lightweight-charts'

export type LinePoint = { time: UTCTimestamp; value: number }

export default function SeriesLineChart({ data, height = 260, color = '#f5a524', precision = 2 }: { data: LinePoint[]; height?: number; color?: string; precision?: number }) {
  const ref = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)

  useEffect(() => {
    if (!ref.current) return
    const chart = createChart(ref.current, {
      height,
      layout: { background: { color: '#151920' }, textColor: '#8a93a3', fontFamily: 'Inter, -apple-system, system-ui, sans-serif', fontSize: 11, attributionLogo: true },
      grid: { vertLines: { color: '#1d222b' }, horzLines: { color: '#1d222b' } },
      rightPriceScale: { borderColor: '#262d38' },
      timeScale: { borderColor: '#262d38', timeVisible: false },
      crosshair: { horzLine: { color: '#8a93a3' }, vertLine: { color: '#8a93a3' } },
      localization: { priceFormatter: (p: number) => p.toLocaleString('en-US', { maximumFractionDigits: precision }) },
    })
    const series = chart.addSeries(LineSeries, { color, lineWidth: 2, priceLineVisible: false, lastValueVisible: true })
    series.setData(data)
    chart.timeScale().fitContent()
    chartRef.current = chart
    const ro = new ResizeObserver(() => chart.applyOptions({ width: ref.current?.clientWidth ?? 600 }))
    ro.observe(ref.current)
    return () => {
      ro.disconnect()
      chart.remove()
      chartRef.current = null
    }
  }, [data, height, color, precision])

  return <div ref={ref} style={{ width: '100%' }} />
}

export function toLinePoints(ts: string[], values: number[]): LinePoint[] {
  const out: LinePoint[] = []
  let last = -1
  for (let i = 0; i < ts.length; i++) {
    const t = Math.floor(new Date(ts[i] + (ts[i].endsWith('Z') ? '' : 'Z')).getTime() / 1000) as UTCTimestamp
    if (t <= last) continue
    last = t
    if (values[i] === null || values[i] === undefined) continue
    out.push({ time: t, value: values[i] })
  }
  return out
}
