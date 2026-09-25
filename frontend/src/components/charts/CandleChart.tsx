import { useEffect, useRef } from 'react'
import { createChart, CandlestickSeries, HistogramSeries, LineSeries, type IChartApi, type UTCTimestamp } from 'lightweight-charts'

export type Bar = { time: UTCTimestamp; open: number; high: number; low: number; close: number }
export type Overlay = { name: string; color: string; data: { time: UTCTimestamp; value: number }[]; pane?: number }

const PALETTE = ['#f5a524', '#5aa9ff', '#a78bfa', '#38bdf8', '#fb923c', '#2fbf71']

export function overlayColor(i: number) {
  return PALETTE[i % PALETTE.length]
}

export default function CandleChart({ bars, volume, overlays, height = 420, lineMode = false }: { bars: Bar[]; volume?: { time: UTCTimestamp; value: number; color?: string }[]; overlays?: Overlay[]; height?: number; lineMode?: boolean }) {
  const ref = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)

  useEffect(() => {
    if (!ref.current) return
    const chart = createChart(ref.current, {
      height,
      layout: { background: { color: '#151920' }, textColor: '#8a93a3', fontFamily: 'Inter, -apple-system, system-ui, sans-serif', fontSize: 11, attributionLogo: true, panes: { separatorColor: '#262d38', separatorHoverColor: '#f5a524', enableResize: true } },
      grid: { vertLines: { color: '#1d222b' }, horzLines: { color: '#1d222b' } },
      rightPriceScale: { borderColor: '#262d38' },
      timeScale: { borderColor: '#262d38', timeVisible: false, rightOffset: 4 },
      crosshair: { mode: 0, horzLine: { color: '#8a93a3' }, vertLine: { color: '#8a93a3' } },
    })
    if (lineMode) {
      const s = chart.addSeries(LineSeries, { color: '#f5a524', lineWidth: 2, priceLineVisible: false })
      s.setData(bars.map((b) => ({ time: b.time, value: b.close })))
    } else {
      const s = chart.addSeries(CandlestickSeries, { upColor: '#2fbf71', downColor: '#f0565a', borderVisible: false, wickUpColor: '#2fbf71', wickDownColor: '#f0565a' })
      s.setData(bars)
    }
    if (volume && volume.length) {
      const v = chart.addSeries(HistogramSeries, { priceFormat: { type: 'volume' }, priceScaleId: 'vol' })
      v.priceScale().applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } })
      v.setData(volume)
    }
    let extraPanes = 0
    for (const o of overlays ?? []) {
      const paneIndex = o.pane ?? 0
      const s = chart.addSeries(LineSeries, { color: o.color, lineWidth: 1, priceLineVisible: false, lastValueVisible: true, title: o.name }, paneIndex)
      s.setData(o.data)
      if (paneIndex > 0) extraPanes = Math.max(extraPanes, paneIndex)
    }
    if (extraPanes > 0) {
      const panes = chart.panes()
      panes[0]?.setHeight(Math.round(height * 0.7))
    }
    chart.timeScale().fitContent()
    chartRef.current = chart
    const ro = new ResizeObserver(() => chart.applyOptions({ width: ref.current?.clientWidth ?? 600 }))
    ro.observe(ref.current)
    return () => {
      ro.disconnect()
      chart.remove()
      chartRef.current = null
    }
  }, [bars, volume, overlays, height, lineMode])

  return <div ref={ref} style={{ width: '100%' }} />
}

export function toTime(ts: string): UTCTimestamp {
  return Math.floor(new Date(ts + (ts.endsWith('Z') ? '' : 'Z')).getTime() / 1000) as UTCTimestamp
}
