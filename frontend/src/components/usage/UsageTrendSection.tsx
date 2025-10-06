import {useMemo} from 'react'
import {useQuery} from '@tanstack/react-query'
import ReactEChartsCore from 'echarts-for-react/lib/core'
import * as echarts from 'echarts/core'
import {LineChart} from 'echarts/charts'
import {GridComponent, LegendComponent, TooltipComponent} from 'echarts/components'
import {CanvasRenderer} from 'echarts/renderers'
import {Button} from 'react-aria-components'

import type {
  DateRangeValue,
  TrendChartOption,
  TrendModeOption,
  UsageTrendBucket,
  UsageTrendMode,
  UsageTrendQueryInput,
  UsageTrendResponse,
} from './types'
import {fetchUsageTrends} from './api'


echarts.use([LineChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer])

const trendModes: TrendModeOption[] = [
  {value: 'day', label: 'Day', detail: 'Tasks per hour'},
  {value: 'week', label: 'Week', detail: 'Tasks per day'},
  {value: 'month', label: 'Month', detail: 'Tasks per day'},
]

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
  trendMode: UsageTrendMode
  onTrendModeChange: (mode: UsageTrendMode) => void
  effectiveRange: DateRangeValue | null
  fallbackRange: DateRangeValue | null
  timezone?: string
  agentIds: string[]
}

export function UsageTrendSection({
  trendMode,
  onTrendModeChange,
  effectiveRange,
  fallbackRange,
  timezone,
  agentIds,
}: UsageTrendSectionProps) {
  const baseRange = effectiveRange ?? fallbackRange

  const trendQueryInput = useMemo<UsageTrendQueryInput | null>(() => {
    if (!baseRange) {
      return null
    }

    const baseEnd = baseRange.end
    const baseStart = baseRange.start

    const clampStart = (candidate: DateRangeValue['start']) =>
      candidate.compare(baseStart) < 0 ? baseStart : candidate

    switch (trendMode) {
      case 'day':
        return {
          mode: 'day',
          from: baseEnd.toString(),
          to: baseEnd.toString(),
          agents: agentIds,
        }
      case 'week': {
        const candidate = baseEnd.subtract({days: 6})
        const windowStart = clampStart(candidate)
        return {
          mode: 'week',
          from: windowStart.toString(),
          to: baseEnd.toString(),
          agents: agentIds,
        }
      }
      case 'month': {
        const candidate = baseEnd.subtract({days: 29})
        const windowStart = clampStart(candidate)
        return {
          mode: 'month',
          from: windowStart.toString(),
          to: baseEnd.toString(),
          agents: agentIds,
        }
      }
      default:
        return null
    }
  }, [agentIds, baseRange, trendMode])

  const agentKey = agentIds.length ? agentIds.slice().sort().join(',') : 'all'

  const {
    data: trendData,
    error: trendError,
    isError: isTrendError,
    isPending: isTrendPending,
  } = useQuery<UsageTrendResponse, Error>({
    queryKey: ['usage-trends', trendMode, trendQueryInput?.from ?? null, trendQueryInput?.to ?? null, agentKey],
    queryFn: ({signal}) => fetchUsageTrends(trendQueryInput!, signal),
    enabled: Boolean(trendQueryInput),
    refetchOnWindowFocus: false,
    placeholderData: (previousData) => previousData,
  })

  const trendModeDetail = useMemo(() => {
    const active = trendModes.find((mode) => mode.value === trendMode)
    return active?.detail ?? ''
  }, [trendMode])

  const chartOption = useMemo<TrendChartOption | null>(() => {
    if (!trendData) {
      return null
    }

    const tz = trendData.timezone || timezone
    const dateFormatter = trendData.resolution === 'hour'
      ? new Intl.DateTimeFormat(undefined, {hour: 'numeric', timeZone: tz})
      : new Intl.DateTimeFormat(undefined, {month: 'short', day: 'numeric', timeZone: tz})

    const categories = trendData.buckets.map((bucket: UsageTrendBucket) =>
      dateFormatter.format(new Date(bucket.timestamp)),
    )
    const currentSeries = trendData.buckets.map((bucket: UsageTrendBucket) => bucket.current)
    const previousSeries = trendData.buckets.map((bucket: UsageTrendBucket) => bucket.previous)

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
      ...(palette.length ? {color: palette} : {}),
      tooltip: {
        trigger: 'axis',
      },
      legend: {
        type: 'scroll',
        data: [
          ...agentSeries.map((series) => series.name),
          'Total (current period)',
          'Total (previous period)',
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
          name: 'Total (current period)',
          type: 'line',
          smooth: true,
          showSymbol: false,
          emphasis: {focus: 'series'},
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
        {
          name: 'Total (previous period)',
          type: 'line',
          smooth: true,
          showSymbol: false,
          lineStyle: {
            type: 'dashed',
            color: '#94a3b8',
          },
          itemStyle: {
            color: '#94a3b8',
          },
          data: previousSeries,
        },
      ],
    }
  }, [timezone, trendData])

  const hasData = useMemo(() => {
    if (!trendData) {
      return false
    }
    return trendData.buckets.some((bucket: UsageTrendBucket) => bucket.current > 0 || bucket.previous > 0)
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
    <section className="flex flex-col gap-4 rounded-xl border border-slate-200 bg-white px-5 py-4 shadow-sm">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-slate-900">Task consumption trend</h2>
          <p className="text-sm text-slate-500">{trendModeDetail} · Compared with previous period.</p>
        </div>
        <div className="flex items-center gap-2">
          <div className="flex items-center gap-1 rounded-md border border-slate-200 bg-slate-50 p-1">
            {trendModes.map((mode) => {
              const isActive = trendMode === mode.value
              return (
                <Button
                  key={mode.value}
                  onPress={() => onTrendModeChange(mode.value)}
                  className={`rounded-md px-3 py-1.5 text-sm font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-1 ${
                    isActive ? 'bg-white text-blue-600 shadow-sm' : 'text-slate-600 hover:bg-white hover:text-slate-800'
                  }`}
                >
                  {mode.label}
                </Button>
              )
            })}
          </div>
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
