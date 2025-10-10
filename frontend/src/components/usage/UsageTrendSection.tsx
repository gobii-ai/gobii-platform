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

export function UsageTrendSection({
  effectiveRange,
  fallbackRange,
  timezone,
  agentIds,
}: UsageTrendSectionProps) {
  const baseRange = effectiveRange ?? fallbackRange

  const resolvedMode = useMemo<{ mode: UsageTrendMode; detail: string } | null>(() => {
    if (!baseRange) {
      return null
    }

    const lengthInDays = getRangeLengthInDays(baseRange)
    if (lengthInDays <= 1) {
      return { mode: 'day', detail: 'Tasks per hour' }
    }
    if (lengthInDays <= 7) {
      return { mode: 'week', detail: 'Tasks per day' }
    }
    return { mode: 'month', detail: 'Tasks per day' }
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
      return {
        name: agent.name,
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
      tooltip: {
        trigger: 'axis',
      },
      legend: {
        type: 'scroll',
        data: [
          ...agentSeries.map((series) => series.name),
          'Total tasks',
        ],
        top: 0,
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
        },
      },
      yAxis: {
        type: 'value',
        min: 0,
        axisLabel: {
          formatter: '{value}',
        },
      },
      series: [
        ...agentSeries,
        {
          name: 'Total tasks',
          type: 'line',
          smooth: true,
          showSymbol: false,
          emphasis: { focus: 'series' },
          z: 3,
          lineStyle: {
            width: 2.5,
            color: '#0f172a',
          },
          itemStyle: {
            color: '#0f172a',
          },
          data: currentSeries,
        },
      ],
    }
  }, [timezone, trendData])

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

  return (
    <section className="gobii-card-base flex flex-col gap-4 p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-slate-900">Task consumption trend</h2>
          <p className="text-sm text-slate-500">{trendModeDetail} · Total tasks over time.</p>
        </div>
      </div>
      <div className="h-80 w-full">
        {isLoading ? (
          <div className="flex h-full items-center justify-center text-sm text-slate-400">Loading trends…</div>
        ) : isTrendError && trendErrorMessage ? (
          <div className="flex h-full items-center justify-center text-sm text-red-600">{trendErrorMessage}</div>
        ) : chartOption ? (
          <div className="flex h-full flex-col">
            <div className="flex-1">
              <ReactEChartsCore echarts={echarts} option={chartOption} notMerge lazyUpdate style={{height: '100%', width: '100%'}} />
            </div>
            {!hasData ? (
              <div className="mt-2 text-center text-xs text-slate-400">{emptyMessage}</div>
            ) : null}
          </div>
        ) : (
          <div className="flex h-full items-center justify-center text-sm text-slate-400">
            {emptyMessage}
          </div>
        )}
      </div>
    </section>
  )
}

export type {UsageTrendSectionProps}
