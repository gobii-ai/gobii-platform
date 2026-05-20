import { useMemo } from 'react'
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


echarts.use([LineChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer])

type UsageTrendSectionProps = {
  effectiveRange: DateRangeValue | null
  fallbackRange: DateRangeValue | null
  timezone?: string
  agentIds: string[]
  quotaTotal?: number | null
  quotaUnlimited?: boolean
  embedded?: boolean
}

type TooltipPrimitiveValue = number | string | Date | null | undefined
type TooltipFormatterValue = TooltipPrimitiveValue | TooltipPrimitiveValue[]

const agentSeriesColors = [
  '#38bdf8',
  '#a78bfa',
  '#f472b6',
  '#f59e0b',
  '#22c55e',
  '#ef4444',
  '#06b6d4',
  '#84cc16',
]

export function UsageTrendSection({
  effectiveRange,
  fallbackRange,
  timezone,
  agentIds,
  quotaTotal = null,
  quotaUnlimited = false,
  embedded = false,
}: UsageTrendSectionProps) {
  const baseRange = effectiveRange ?? fallbackRange

  const resolvedMode = useMemo<{ mode: UsageTrendMode; detail: string } | null>(() => {
    if (!baseRange) {
      return null
    }

    const lengthInDays = getRangeLengthInDays(baseRange)
    if (lengthInDays <= 1) {
      return { mode: 'day', detail: 'Cumulative credits' }
    }
    if (lengthInDays <= 7) {
      return { mode: 'week', detail: 'Cumulative credits' }
    }
    return { mode: 'month', detail: 'Cumulative credits' }
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
    () => new Intl.NumberFormat(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 3 }),
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
    const now = new Date()
    let cumulative = 0
    let lastActualIndex = -1
    const actualSeries = trendData.buckets.map((bucket: UsageTrendBucket, index) => {
      const bucketDate = new Date(bucket.timestamp)
      if (bucketDate <= now) {
        cumulative += bucket.current
        lastActualIndex = index
        return cumulative
      }
      return null
    })
    const agentSeries = trendData.agents
      .map((agent, agentIndex) => {
        let agentCumulative = 0
        let hasAgentCredits = false
        const data = trendData.buckets.map((bucket: UsageTrendBucket) => {
          if (new Date(bucket.timestamp) > now) {
            return null
          }
          const bucketCredits = bucket.agents?.[agent.id] ?? 0
          agentCumulative += bucketCredits
          if (agentCumulative > 0) {
            hasAgentCredits = true
          }
          return agentCumulative
        })

        if (!hasAgentCredits) {
          return null
        }

        const color = agentSeriesColors[agentIndex % agentSeriesColors.length]
        const agentLabel = agent.is_deleted ? `${agent.name} (Deleted)` : agent.name
        return {
          name: agentLabel,
          type: 'line' as const,
          smooth: true,
          showSymbol: false,
          emphasis: { focus: 'series' as const },
          z: 2,
          lineStyle: {
            width: 1.8,
            color,
          },
          itemStyle: {
            color,
          },
          data,
        }
      })
      .filter((series): series is NonNullable<typeof series> => series !== null)
    const elapsedBucketCount = Math.max(lastActualIndex + 1, 1)
    const projectedPerBucket = cumulative / elapsedBucketCount
    const projectionSeries = trendData.buckets.map((_bucket, index) => {
      if (lastActualIndex < 0 || index < lastActualIndex) {
        return null
      }
      return cumulative + projectedPerBucket * (index - lastActualIndex)
    })
    const visibleValues = [
      ...actualSeries,
      ...projectionSeries,
      ...agentSeries.flatMap((series) => series.data),
    ].filter((value): value is number => typeof value === 'number' && Number.isFinite(value))
    const visibleMax = Math.max(1, ...visibleValues)

    return {
      color: [
        embedded ? '#e0f2fe' : '#0f172a',
        '#14b8a6',
        ...agentSeriesColors,
      ],
      textStyle: {
        color: embedded ? '#cbd5e1' : '#334155',
      },
      tooltip: {
        trigger: 'axis',
        backgroundColor: embedded ? 'rgba(15, 23, 42, 0.96)' : undefined,
        borderColor: embedded ? 'rgba(148, 163, 184, 0.25)' : undefined,
        textStyle: embedded ? { color: '#f8fafc' } : undefined,
        valueFormatter: (value: TooltipFormatterValue, _dataIndex: number) => {
          const numericValue = Array.isArray(value) ? value[0] : value
          return typeof numericValue === 'number' ? creditFormatter.format(numericValue) : `${numericValue ?? ''}`
        },
      },
      legend: {
        type: 'scroll',
        data: [
          'Credits used',
          'Projected usage',
          ...agentSeries.map((series) => series.name),
        ],
        top: 0,
        textStyle: {
          color: embedded ? '#cbd5e1' : '#334155',
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
          color: embedded ? '#94a3b8' : '#64748b',
        },
        axisLine: {
          lineStyle: {
            color: embedded ? 'rgba(148, 163, 184, 0.25)' : '#cbd5e1',
          },
        },
        axisTick: {
          lineStyle: {
            color: embedded ? 'rgba(148, 163, 184, 0.25)' : '#cbd5e1',
          },
        },
      },
      yAxis: {
        type: 'value',
        min: 0,
        max: Math.ceil(visibleMax * 1.15),
        axisLabel: {
          formatter: (value: number | string) => (typeof value === 'number' ? creditFormatter.format(value) : `${value}`),
          color: embedded ? '#94a3b8' : '#64748b',
        },
        splitLine: {
          lineStyle: {
            color: embedded ? 'rgba(148, 163, 184, 0.14)' : '#e2e8f0',
          },
        },
      },
      series: [
        {
          name: 'Credits used',
          type: 'line',
          smooth: true,
          showSymbol: false,
          emphasis: { focus: 'series' },
          z: 3,
          lineStyle: {
            width: 2.5,
            color: embedded ? '#e0f2fe' : '#0f172a',
          },
          itemStyle: {
            color: embedded ? '#e0f2fe' : '#0f172a',
          },
          areaStyle: {
            opacity: 0.08,
          },
          data: actualSeries,
        },
        {
          name: 'Projected usage',
          type: 'line',
          smooth: true,
          showSymbol: false,
          z: 4,
          lineStyle: {
            width: 2,
            type: 'dotted',
            color: '#14b8a6',
          },
          itemStyle: {
            color: '#14b8a6',
          },
          data: projectionSeries,
        },
        ...agentSeries,
      ],
    }
  }, [creditFormatter, embedded, timezone, trendData])

  const projectionCaption = useMemo(() => {
    if (!trendData || quotaUnlimited || typeof quotaTotal !== 'number' || !Number.isFinite(quotaTotal) || quotaTotal <= 0) {
      return null
    }
    const now = new Date()
    let cumulative = 0
    let lastActualIndex = -1
    trendData.buckets.forEach((bucket, index) => {
      if (new Date(bucket.timestamp) <= now) {
        cumulative += bucket.current
        lastActualIndex = index
      }
    })
    if (lastActualIndex < 0 || cumulative <= 0) {
      return 'Not enough usage yet to project billing-cycle consumption.'
    }
    const projectedPerBucket = cumulative / Math.max(lastActualIndex + 1, 1)
    for (let index = lastActualIndex; index < trendData.buckets.length; index += 1) {
      const projected = cumulative + projectedPerBucket * (index - lastActualIndex)
      if (projected >= quotaTotal) {
        const runoutDate = new Date(trendData.buckets[index].timestamp)
        const label = new Intl.DateTimeFormat(undefined, {
          month: 'short',
          day: 'numeric',
          timeZone: trendData.timezone || timezone,
        }).format(runoutDate)
        return `Projected to run out around ${label} at the current pace.`
      }
    }
    return 'Projected usage stays within the billing-cycle credit limit at the current pace.'
  }, [quotaTotal, quotaUnlimited, timezone, trendData])

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

  const sectionClassName = embedded
    ? 'flex flex-col gap-4 rounded-xl border border-slate-200/20 bg-slate-950/35 p-6'
    : 'gobii-card-base flex flex-col gap-4 p-6'
  const titleClassName = embedded ? 'text-lg font-semibold text-slate-50' : 'text-lg font-semibold text-slate-900'
  const subtitleClassName = embedded ? 'text-sm text-slate-400' : 'text-sm text-slate-500'
  const loadingClassName = embedded
    ? 'flex h-full items-center justify-center text-sm text-slate-400'
    : 'flex h-full items-center justify-center text-sm text-slate-400'
  const errorClassName = embedded
    ? 'flex h-full items-center justify-center text-sm text-rose-300'
    : 'flex h-full items-center justify-center text-sm text-red-600'
  const emptyClassName = embedded ? 'text-slate-400' : 'text-slate-400'

  return (
    <section className={sectionClassName}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className={titleClassName}>Task consumption trend</h2>
          <p className={subtitleClassName}>{trendModeDetail} · Actual and projected credits through billing reset.</p>
        </div>
      </div>
      {projectionCaption ? (
        <p className={subtitleClassName}>{projectionCaption}</p>
      ) : null}
      <div className="h-80 w-full">
        {isLoading ? (
          <div className={loadingClassName}>Loading trends…</div>
        ) : isTrendError && trendErrorMessage ? (
          <div className={errorClassName}>{trendErrorMessage}</div>
        ) : chartOption ? (
          <div className="flex h-full flex-col">
            <div className="flex-1">
              <ReactEChartsCore echarts={echarts} option={chartOption} notMerge lazyUpdate style={{height: '100%', width: '100%'}} />
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
