import { useEffect, useMemo, useRef } from 'react'
import { useQuery } from '@tanstack/react-query'
import ReactEChartsCore from 'echarts-for-react/lib/core'
import * as echarts from 'echarts/core'
import { LineChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

import type {
  DateRangeValue,
  TrendChartOption,
  UsageTrendBucket,
  UsageTrendMode,
  UsageTrendQueryInput,
  UsageTrendResponse,
} from './types'
import { fetchUsageTrends } from './api'
import { getRangeLengthInDays } from './utils'
import { useEchartsSizeSensor } from '../../hooks/useEchartsSizeSensor'
import { getSettingsSurfaceClassName } from '../common/SettingsSurface'


echarts.use([LineChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer])

const agentSeriesColors = [
  '#2563eb',
  '#f97316',
  '#14b8a6',
  '#6366f1',
  '#ef4444',
  '#0ea5e9',
  '#facc15',
  '#a855f7',
  '#22c55e',
  '#f472b6',
  '#fb7185',
  '#0f766e',
]

type UsageTrendSectionProps = {
  effectiveRange: DateRangeValue | null
  fallbackRange: DateRangeValue | null
  timezone?: string
  agentIds: string[]
}

type TooltipPrimitiveValue = number | string | Date | null | undefined
type TooltipFormatterValue = TooltipPrimitiveValue | TooltipPrimitiveValue[]

export function UsageTrendSection({
  effectiveRange,
  fallbackRange,
  timezone,
  agentIds,
}: UsageTrendSectionProps) {
  const baseRange = effectiveRange ?? fallbackRange
  const chartRef = useRef<ReactEChartsCore>(null)

  const resolvedMode = useMemo<{ mode: UsageTrendMode; detail: string } | null>(() => {
    if (!baseRange) {
      return null
    }

    const lengthInDays = getRangeLengthInDays(baseRange)
    if (lengthInDays <= 1) {
      return { mode: 'day', detail: 'Credits per hour' }
    }
    if (lengthInDays <= 7) {
      return { mode: 'week', detail: 'Credits per day' }
    }
    return { mode: 'month', detail: 'Credits per day' }
  }, [baseRange])

  const trendQueryInput = useMemo<UsageTrendQueryInput | null>(() => {
    if (!baseRange || !resolvedMode) {
      return null
    }

    return {
      mode: resolvedMode.mode,
      from: baseRange.start.toString(),
      to: baseRange.end.toString(),
      agents: agentIds,
    }
  }, [agentIds, baseRange, resolvedMode])

  const agentKey = agentIds.length ? agentIds.slice().sort().join(',') : 'all'

  const creditFormatter = useMemo(
    () => new Intl.NumberFormat(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 }),
    [],
  )

  const {
    data: trendData,
    error: trendError,
    isError: isTrendError,
    isPending: isTrendPending,
  } = useQuery<UsageTrendResponse, Error>({
    queryKey: ['usage-trends', resolvedMode?.mode ?? null, trendQueryInput?.from ?? null, trendQueryInput?.to ?? null, agentKey],
    queryFn: ({ signal }) => fetchUsageTrends(trendQueryInput!, signal),
    enabled: Boolean(trendQueryInput),
    refetchOnWindowFocus: false,
    placeholderData: (previousData) => previousData,
  })

  const trendModeDetail = resolvedMode?.detail ?? ''

  const chartOption = useMemo<TrendChartOption | null>(() => {
    if (!trendData) {
      return null
    }

    const tz = trendData.timezone || timezone
    const dateFormatter =
      trendData.resolution === 'hour'
        ? new Intl.DateTimeFormat(undefined, { hour: 'numeric', timeZone: tz })
        : new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', timeZone: tz })

    const categories = trendData.buckets.map((bucket: UsageTrendBucket) => dateFormatter.format(new Date(bucket.timestamp)))
    const currentSeries = trendData.buckets.map((bucket: UsageTrendBucket) => bucket.current)

    const agentSeries = trendData.agents.map((agent, index) => {
      const color = agentSeriesColors[index % agentSeriesColors.length]
      const data = trendData.buckets.map((bucket: UsageTrendBucket) => bucket.agents?.[agent.id] ?? 0)
      const agentLabel = agent.is_deleted ? `${agent.name} (Deleted)` : agent.name
      return {
        name: agentLabel,
        type: 'line' as const,
        smooth: true,
        showSymbol: false,
        stack: 'currentTotal',
        emphasis: {focus: 'series' as const},
        lineStyle: {
          width: 1.5,
          color,
        },
        itemStyle: {
          color,
        },
        areaStyle: {
          opacity: 0.2,
        },
        data,
      }
    })

    const palette = agentSeries.map((series) => series.itemStyle?.color as string)

    return {
      ...(palette.length ? { color: palette } : {}),
      textStyle: {
        color: '#cbd5e1',
      },
      tooltip: {
        trigger: 'axis',
        backgroundColor: 'rgba(15, 23, 42, 0.96)',
        borderColor: 'rgba(148, 163, 184, 0.25)',
        textStyle: { color: '#f8fafc' },
        valueFormatter: (value: TooltipFormatterValue, _dataIndex: number) => {
          const numericValue = Array.isArray(value) ? value[0] : value
          return typeof numericValue === 'number' ? creditFormatter.format(numericValue) : `${numericValue ?? ''}`
        },
      },
      legend: {
        type: 'scroll',
        data: [
          ...agentSeries.map((series) => series.name),
          'Total credits',
        ],
        top: 0,
        textStyle: {
          color: '#cbd5e1',
        },
      },
      grid: {
        top: 48,
        left: 36,
        right: 24,
        bottom: 36,
      },
      xAxis: {
        type: 'category',
        data: categories,
        boundaryGap: false,
        axisLabel: {
          interval: trendData.resolution === 'hour' ? 2 : 'auto',
          color: '#94a3b8',
        },
        axisLine: {
          lineStyle: {
            color: 'rgba(148, 163, 184, 0.25)',
          },
        },
        axisTick: {
          lineStyle: {
            color: 'rgba(148, 163, 184, 0.25)',
          },
        },
      },
      yAxis: {
        type: 'value',
        min: 0,
        axisLabel: {
          formatter: (value: number | string) => (typeof value === 'number' ? creditFormatter.format(value) : `${value}`),
          color: '#94a3b8',
        },
        splitLine: {
          lineStyle: {
            color: 'rgba(148, 163, 184, 0.14)',
          },
        },
      },
      series: [
        ...agentSeries,
        {
          name: 'Total credits',
          type: 'line',
          smooth: true,
          showSymbol: false,
          emphasis: { focus: 'series' },
          z: 3,
          lineStyle: {
            width: 2.5,
            color: '#e0f2fe',
          },
          itemStyle: {
            color: '#e0f2fe',
          },
          data: currentSeries,
        },
      ],
    }
  }, [creditFormatter, timezone, trendData])

  const hasData = useMemo(() => {
    if (!trendData) {
      return false
    }
    return trendData.buckets.some((bucket: UsageTrendBucket) => {
      if (bucket.current > 0) {
        return true
      }
      if (bucket.agents) {
        return Object.values(bucket.agents).some((value) => value > 0)
      }
      return false
    })
  }, [trendData])

  useEchartsSizeSensor(chartRef, Boolean(chartOption))

  useEffect(() => {
    if (!chartOption) {
      return
    }

    const frame = window.requestAnimationFrame(() => {
      chartRef.current?.getEchartsInstance()?.resize()
    })
    return () => window.cancelAnimationFrame(frame)
  }, [chartOption, hasData])

  const isLoading = Boolean(trendQueryInput) && isTrendPending
  const trendErrorMessage = useMemo(() => {
    if (!isTrendError) {
      return null
    }

    if (trendError instanceof Error) {
      return trendError.message
    }

    return 'Unable to load usage trends right now.'
  }, [isTrendError, trendError])

  const emptyMessage = baseRange
    ? 'No task activity recorded for this window.'
    : 'Select a billing period to view task trends.'

  const sectionClassName = getSettingsSurfaceClassName({
    variant: 'embedded',
    roundedClassName: 'rounded-xl',
    className: 'flex flex-col gap-4 p-6',
  })
  const titleClassName = 'text-lg font-semibold text-slate-50'
  const subtitleClassName = 'text-sm text-slate-400'
  const loadingClassName = 'flex h-full items-center justify-center text-sm text-slate-400'
  const errorClassName = 'flex h-full items-center justify-center text-sm text-rose-300'
  const emptyClassName = 'text-slate-400'

  return (
    <section className={sectionClassName}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className={titleClassName}>Task consumption trend</h2>
          <p className={subtitleClassName}>{trendModeDetail} · Total tasks over time.</p>
        </div>
      </div>
      <div className="h-80 w-full min-w-0 overflow-hidden">
        {isLoading ? (
          <div className={loadingClassName}>Loading trends…</div>
        ) : isTrendError && trendErrorMessage ? (
          <div className={errorClassName}>{trendErrorMessage}</div>
        ) : chartOption ? (
          <div className="flex h-full w-full min-w-0 flex-col">
            <div className="min-h-0 min-w-0 flex-1">
              <ReactEChartsCore
                ref={chartRef}
                echarts={echarts}
                option={chartOption}
                notMerge
                lazyUpdate
                style={{height: '100%', width: '100%', minWidth: 0}}
              />
            </div>
            {!hasData ? (
              <div className={`mt-2 text-center text-xs ${emptyClassName}`}>{emptyMessage}</div>
            ) : null}
          </div>
        ) : (
          <div className={loadingClassName}>
            {emptyMessage}
          </div>
        )}
      </div>
    </section>
  )
}

export type {UsageTrendSectionProps}
