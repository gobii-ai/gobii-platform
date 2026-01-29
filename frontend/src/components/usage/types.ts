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
      unlimited?: boolean
    }
  }
  extra_tasks?: {
    enabled: boolean
  }
}

export type UsageBurnRateSnapshot = {
  scope_type: 'user' | 'organization' | 'agent'
  scope_id: string
  window_minutes: number
  window_start: string
  window_end: string
  window_total: number
  burn_rate_per_hour: number
  burn_rate_per_day: number
  computed_at: string
}

export type UsageBurnRateProjection = {
  tier: string
  multiplier: number
  available: number
  projected_days_remaining: number | null
  window_minutes: number
}

export type UsageBurnRateResponse = {
  snapshot: UsageBurnRateSnapshot | null
  projection: UsageBurnRateProjection | null
  quota: {
    available: number
    total: number
    used: number
    used_pct: number
    unlimited: boolean
  }
  extra_tasks: {
    enabled: boolean
  }
}

export type UsageBurnRateQueryInput = { tier?: string; window?: number }

export type MetricDefinition = {
  id: 'tasks' | 'tasks_per_day' | 'credits' | 'quota'
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
  credits: number
  invocations: number
}

export type UsageToolBreakdownResponse = {
  range: {
    start: string
    end: string
  }
  timezone: string
  total_count: number
  total_credits: number
  total_invocations?: number
  tools: UsageToolBreakdownTool[]
}

export type UsageToolBreakdownQueryInput = { from?: string; to?: string; agents?: string[] }

export type PeriodInfo = {
  label: string
  value: string
  caption: string
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

export type UsageAgentLeaderboardEntry = {
  id: string
  name: string
  tasks_total: number
  tasks_per_day: number
  success_count: number
  error_count: number
  persistent_id?: string | null
}

export type UsageAgentLeaderboardResponse = {
  period: {
    start: string
    end: string
    label: string
    timezone: string
  }
  agents: UsageAgentLeaderboardEntry[]
}

export type UsageAgentLeaderboardQueryInput = UsageSummaryQueryInput
