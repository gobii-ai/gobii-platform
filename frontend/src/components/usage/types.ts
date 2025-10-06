import type { DateValue } from '@internationalized/date'
import type { EChartsOption } from 'echarts'

export type DateRangeValue = { start: DateValue; end: DateValue }

export type UsageSummaryQueryInput = { from?: string; to?: string; agents?: string[] }
export type UsageSummaryQueryKey = ['usage-summary', UsageSummaryQueryInput]

export type UsageSummaryResponse = {
  period: {
    start: string
    end: string
    label: string
    timezone: string
  }
  context: {
    type: 'personal' | 'organization'
    id: string
    name: string
  }
  metrics: {
    tasks: {
      count: number
      completed: number
      in_progress: number
      pending: number
      failed: number
      cancelled: number
    }
    credits: {
      total: number
      unit: string
    }
    quota: {
      available: number
      total: number
      used: number
      used_pct: number
    }
  }
}

export type MetricDefinition = {
  id: 'tasks' | 'credits' | 'quota'
  label: string
  baseCaption: string
}

export type MetricCard = {
  id: MetricDefinition['id']
  label: string
  value: string
  caption: string
  valueClasses: string
  progressPct?: number
  progressClass?: string
}

export type UsageTrendMode = 'day' | 'week' | 'month'

export type UsageTrendBucket = {
  timestamp: string
  current: number
  previous: number
  agents: Record<string, number>
}

export type UsageTrendResponse = {
  mode: UsageTrendMode
  resolution: 'hour' | 'day'
  timezone: string
  current_period: {
    start: string
    end: string
  }
  previous_period: {
    start: string
    end: string
  }
  buckets: UsageTrendBucket[]
  agents: UsageAgent[]
}

export type UsageTrendQueryInput = { mode: UsageTrendMode; from?: string; to?: string; agents?: string[] }

export type UsageToolBreakdownTool = {
  name: string
  count: number
}

export type UsageToolBreakdownResponse = {
  range: {
    start: string
    end: string
  }
  timezone: string
  total: number
  tools: UsageToolBreakdownTool[]
}

export type UsageToolBreakdownQueryInput = { from?: string; to?: string; agents?: string[] }

export type PeriodInfo = {
  label: string
  value: string
  caption: string
}

export type TrendModeOption = {
  value: UsageTrendMode
  label: string
  detail: string
}

export type TrendChartOption = EChartsOption
export type ToolChartOption = EChartsOption

export type UsageAgent = {
  id: string
  name: string
}

export type UsageAgentsResponse = {
  agents: UsageAgent[]
}
