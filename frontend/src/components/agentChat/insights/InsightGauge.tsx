import ReactEChartsCore from 'echarts-for-react/lib/core'
import * as echarts from 'echarts/core'
import { GaugeChart } from 'echarts/charts'
import { CanvasRenderer } from 'echarts/renderers'
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import * as sizeSensor from 'size-sensor'

echarts.use([GaugeChart, CanvasRenderer])

type InsightGaugeProps = {
  value: number
  max: number
  size?: number
  color?: string
  gradientColors?: [string, string]
  trackColor?: string
  thickness?: number
  animate?: boolean
  showGlow?: boolean
  label?: string
  sublabel?: string
}

export function InsightGauge({
  value,
  max,
  size = 120,
  color = '#10b981',
  gradientColors,
  trackColor = 'rgba(0, 0, 0, 0.06)',
  thickness = 12,
  animate = true,
  showGlow = true,
  label,
  sublabel,
}: InsightGaugeProps) {
  const chartRef = useRef<ReactEChartsCore>(null)
  const [displayValue, setDisplayValue] = useState(animate ? 0 : value)

  useLayoutEffect(() => {
    const element = (chartRef.current as unknown as { ele?: HTMLElement })?.ele
    if (!element) return
    // Pre-bind size-sensor so echarts-for-react cleanup doesn't crash on fast unmounts.
    sizeSensor.bind(element, () => {})
  }, [])

  useEffect(() => {
    if (animate) {
      // Small delay to trigger animation after mount
      const timer = setTimeout(() => {
        setDisplayValue(value)
      }, 100)
      return () => clearTimeout(timer)
    } else {
      setDisplayValue(value)
    }
  }, [value, animate])

  const percent = Math.min((displayValue / max) * 100, 100)

  const option = {
    series: [
      {
        type: 'gauge',
        startAngle: 220,
        endAngle: -40,
        min: 0,
        max: 100,
        splitNumber: 0,
        pointer: { show: false },
        progress: {
          show: true,
          overlap: false,
          roundCap: true,
          clip: false,
          itemStyle: gradientColors
            ? {
                color: new echarts.graphic.LinearGradient(0, 0, 1, 0, [
                  { offset: 0, color: gradientColors[0] },
                  { offset: 1, color: gradientColors[1] },
                ]),
                shadowColor: showGlow ? gradientColors[1] : 'transparent',
                shadowBlur: showGlow ? 12 : 0,
              }
            : {
                color: color,
                shadowColor: showGlow ? color : 'transparent',
                shadowBlur: showGlow ? 12 : 0,
              },
        },
        axisLine: {
          lineStyle: {
            width: thickness,
            color: [[1, trackColor]],
          },
          roundCap: true,
        },
        axisTick: { show: false },
        splitLine: { show: false },
        axisLabel: { show: false },
        title: {
          show: Boolean(label),
          offsetCenter: [0, sublabel ? '-5%' : '10%'],
          color: '#1e293b',
          fontSize: Math.max(12, size * 0.11),
          fontWeight: 700,
          fontFamily: 'inherit',
        },
        detail: {
          show: Boolean(sublabel),
          offsetCenter: [0, '25%'],
          color: '#64748b',
          fontSize: Math.max(10, size * 0.09),
          fontWeight: 500,
          fontFamily: 'inherit',
          formatter: () => sublabel || '',
        },
        data: [
          {
            value: percent,
            name: label || '',
            title: { show: Boolean(label) },
            detail: { show: Boolean(sublabel) },
          },
        ],
        animationDuration: animate ? 1400 : 0,
        animationEasing: 'cubicOut',
      },
    ],
  }

  return (
    <div className="insight-gauge" style={{ width: size, height: size }}>
      <ReactEChartsCore
        ref={chartRef}
        echarts={echarts}
        option={option}
        style={{ width: '100%', height: '100%' }}
        opts={{ renderer: 'canvas' }}
        notMerge={true}
      />
    </div>
  )
}
