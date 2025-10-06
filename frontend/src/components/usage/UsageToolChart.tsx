import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import ReactEChartsCore from 'echarts-for-react/lib/core'
import * as echarts from 'echarts/core'
import { PieChart } from 'echarts/charts'
import { LegendComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

import type {
  DateRangeValue,
  ToolChartOption,
  UsageToolBreakdownQueryInput,
  UsageToolBreakdownResponse,
} from './types'
import { fetchUsageToolBreakdown } from './api'
import { getSharedToolMetadata, USAGE_SKIP_TOOL_NAMES } from '../tooling/toolMetadata'

echarts.use([PieChart, LegendComponent, TooltipComponent, CanvasRenderer])

const toolPalette = [
  '#2563eb',
  '#f97316',
  '#10b981',
  '#a855f7',
  '#facc15',
  '#ef4444',
  '#0ea5e9',
  '#22c55e',
  '#ec4899',
  '#6366f1',
]

type ChartSegment = {
  key: string
  label: string
  value: number
}

type UsageToolChartProps = {
  effectiveRange: DateRangeValue | null
  fallbackRange: DateRangeValue | null
  agentIds: string[]
  timezone?: string
}

const numberFormatter = new Intl.NumberFormat()

export function UsageToolChart({ effectiveRange, fallbackRange, agentIds, timezone }: UsageToolChartProps) {
  const baseRange = effectiveRange ?? fallbackRange

  const queryInput = useMemo<UsageToolBreakdownQueryInput | null>(() => {
    if (!baseRange) {
      return null
    }
    return {
      from: baseRange.start.toString(),
      to: baseRange.end.toString(),
      agents: agentIds,
    }
  }, [agentIds, baseRange])

  const agentKey = agentIds.length ? agentIds.slice().sort().join(',') : 'all'

  const {
    data: toolData,
    error: toolError,
    isError,
    isPending,
  } = useQuery<UsageToolBreakdownResponse, Error>({
    queryKey: ['usage-tool-breakdown', queryInput?.from ?? null, queryInput?.to ?? null, agentKey],
    queryFn: ({ signal }) => fetchUsageToolBreakdown(queryInput!, signal),
    enabled: Boolean(queryInput),
    refetchOnWindowFocus: false,
    placeholderData: (previous) => previous,
  })

  const processedSegments = useMemo<ChartSegment[]>(() => {
    if (!toolData) {
      return []
    }

    const segments = new Map<string, ChartSegment>()
    let otherCount = 0

    for (const entry of toolData.tools) {
      if (!entry || entry.count <= 0) {
        continue
      }

      const rawName = entry.name ?? ''
      const normalized = rawName.toLowerCase()
      const metadata = getSharedToolMetadata(rawName)
      const shouldSkip = USAGE_SKIP_TOOL_NAMES.has(normalized) || metadata.skip

      if (shouldSkip) {
        otherCount += entry.count
        continue
      }

      const key = metadata.name || rawName || 'default'
      const label = metadata.label || (rawName ? rawName.replaceAll('_', ' ') : 'Other')
      const existing = segments.get(key)
      if (existing) {
        existing.value += entry.count
      } else {
        segments.set(key, { key, label, value: entry.count })
      }
    }

    if (otherCount > 0) {
      const existingOther = segments.get('other')
      if (existingOther) {
        existingOther.value += otherCount
      } else {
        segments.set('other', { key: 'other', label: 'Other', value: otherCount })
      }
    }

    return Array.from(segments.values()).sort((a, b) => b.value - a.value)
  }, [toolData])

  const chartOption = useMemo<ToolChartOption | null>(() => {
    if (!processedSegments.length) {
      return null
    }

    const data = processedSegments.map((segment, index) => ({
      value: segment.value,
      name: segment.label,
      itemStyle: {
        color: toolPalette[index % toolPalette.length],
      },
    }))

    return {
      tooltip: {
        trigger: 'item',
        formatter: ({ name, value, percent }) => {
          const count = typeof value === 'number' ? value : Number(value)
          const pct = typeof percent === 'number' ? percent : Number(percent)
          return `${name}<br />${numberFormatter.format(count)} tasks (${pct.toFixed(1)}%)`
        },
      },
      legend: {
        type: 'scroll',
        orient: 'vertical',
        right: 0,
        top: 'middle',
        align: 'left',
      },
      series: [
        {
          name: 'Tool usage',
          type: 'pie',
          radius: ['40%', '70%'],
          center: ['40%', '50%'],
          avoidLabelOverlap: false,
          itemStyle: {
            borderRadius: 6,
            borderColor: '#fff',
            borderWidth: 1,
          },
          label: {
            show: true,
            formatter: '{b}: {d}%',
          },
          labelLine: {
            show: true,
          },
          data,
        },
      ],
    }
  }, [processedSegments])

  const isLoading = Boolean(queryInput) && isPending

  const errorMessage = useMemo(() => {
    if (!isError) {
      return null
    }
    if (toolError instanceof Error) {
      return toolError.message
    }
    return 'Unable to load tool usage right now.'
  }, [isError, toolError])

  const emptyMessage = baseRange
    ? 'No tool activity recorded for this window.'
    : 'Select a billing period to view tool usage.'

  const summaryRange = useMemo(() => {
    if (!toolData) {
      return null
    }
    const tz = toolData.timezone || timezone
    const formatter = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeZone: tz })
    const start = formatter.format(new Date(toolData.range.start))
    const end = formatter.format(new Date(toolData.range.end))
    return start === end ? start : `${start} – ${end}`
  }, [timezone, toolData])

  const displayedTotal = processedSegments.reduce((acc, segment) => acc + segment.value, 0)

  return (
    <section className="flex flex-col gap-4 rounded-xl border border-slate-200 bg-white px-5 py-4 shadow-sm">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-slate-900">Tool usage breakdown</h2>
          <p className="text-sm text-slate-500">
            Task share by tool{summaryRange ? ` · ${summaryRange}` : ''}
          </p>
        </div>
        {toolData ? (
          <div className="rounded-md bg-slate-50 px-3 py-1 text-sm text-slate-600">
            Total tasks: <span className="font-medium text-slate-900">{numberFormatter.format(displayedTotal)}</span>
          </div>
        ) : null}
      </div>
      <div className="h-80 w-full">
        {isLoading ? (
          <div className="flex h-full items-center justify-center text-sm text-slate-400">Loading tool usage…</div>
        ) : isError && errorMessage ? (
          <div className="flex h-full items-center justify-center text-sm text-red-600">{errorMessage}</div>
        ) : chartOption ? (
          <div className="flex h-full flex-col">
            <div className="flex-1">
              <ReactEChartsCore echarts={echarts} option={chartOption} notMerge lazyUpdate style={{ height: '100%', width: '100%' }} />
            </div>
            {processedSegments.length === 0 ? (
              <div className="mt-2 text-center text-xs text-slate-400">{emptyMessage}</div>
            ) : null}
          </div>
        ) : (
          <div className="flex h-full items-center justify-center text-sm text-slate-400">{emptyMessage}</div>
        )}
      </div>
    </section>
  )
}

export type { UsageToolChartProps }
