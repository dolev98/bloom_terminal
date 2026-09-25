import { useEffect, useRef } from 'react'
import type { CSSProperties } from 'react'
import * as echarts from 'echarts'
import type { EChartsOption, ECElementEvent } from 'echarts'

export type { EChartsOption }

export const CHART_COLORS = ['#f5a524', '#5aa9ff', '#2fbf71', '#f0565a', '#a78bfa', '#38bdf8']

/** Dark terminal defaults; any key given in `option` overrides the same top-level key here. */
export const baseOption: EChartsOption = {
  backgroundColor: 'transparent',
  color: CHART_COLORS,
  animation: false,
  textStyle: { color: '#8a93a3', fontFamily: 'Inter, -apple-system, system-ui, sans-serif', fontSize: 11 },
  grid: { left: 52, right: 52, top: 30, bottom: 28 },
  tooltip: { trigger: 'axis', backgroundColor: '#1b2029', borderColor: '#343c4a', textStyle: { color: '#e7eaf0', fontSize: 12 } },
  legend: { top: 4, icon: 'roundRect', itemWidth: 12, itemHeight: 4, textStyle: { color: '#b3bac6', fontSize: 11.5 } },
}

export const AXIS_STYLE = {
  axisLine: { lineStyle: { color: '#262d38' } },
  axisLabel: { color: '#8a93a3', fontSize: 11 },
  splitLine: { lineStyle: { color: '#1d222b' } },
}

/** Small reusable ECharts wrapper: init in a ref, apply `option`, resize with ResizeObserver, dispose on unmount. */
export default function EChart({
  option,
  height = 260,
  onClick,
  style,
  notMerge = true,
}: {
  option: EChartsOption
  height?: number | string
  onClick?: (params: ECElementEvent) => void
  style?: CSSProperties
  notMerge?: boolean
}) {
  const ref = useRef<HTMLDivElement>(null)
  const chart = useRef<echarts.ECharts | null>(null)
  const clickRef = useRef(onClick)
  clickRef.current = onClick

  useEffect(() => {
    if (!ref.current) return
    const c = echarts.init(ref.current, undefined, { renderer: 'canvas' })
    chart.current = c
    c.on('click', (p: ECElementEvent) => clickRef.current?.(p))
    const ro = new ResizeObserver(() => c.resize())
    ro.observe(ref.current)
    return () => {
      ro.disconnect()
      c.dispose()
      chart.current = null
    }
  }, [])

  useEffect(() => {
    chart.current?.setOption({ ...baseOption, ...option }, { notMerge })
  }, [option, notMerge])

  return <div ref={ref} style={{ width: '100%', height, ...style }} />
}
