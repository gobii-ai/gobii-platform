import type {
  UsageAgentsResponse,
  UsageSummaryQueryInput,
  UsageSummaryResponse,
  UsageBurnRateQueryInput,
  UsageBurnRateResponse,
  UsageTrendQueryInput,
  UsageTrendResponse,
  UsageToolBreakdownQueryInput,
  UsageToolBreakdownResponse,
  UsageAgentLeaderboardQueryInput,
  UsageAgentLeaderboardResponse,
} from './types'
import { jsonFetch } from '../../api/http'

export const fetchUsageSummary = async (params: UsageSummaryQueryInput, signal: AbortSignal): Promise<UsageSummaryResponse> => {
  const search = new URLSearchParams()

  if (params.from) {
    search.set('from', params.from)
  }

  if (params.to) {
    search.set('to', params.to)
  }

  if (params.agents?.length) {
    params.agents.forEach((agentId) => {
      search.append('agent', agentId)
    })
  }

  const suffix = search.toString()
  return jsonFetch<UsageSummaryResponse>(`/console/api/usage/summary/${suffix ? `?${suffix}` : ''}`, {
    method: 'GET',
    signal,
  })
}

export const fetchUsageBurnRate = async (params: UsageBurnRateQueryInput, signal: AbortSignal): Promise<UsageBurnRateResponse> => {
  const search = new URLSearchParams()

  if (params.tier) {
    search.set('tier', params.tier)
  }

  if (params.window) {
    search.set('window', `${params.window}`)
  }

  const suffix = search.toString()
  return jsonFetch<UsageBurnRateResponse>(`/console/api/usage/burn-rate/${suffix ? `?${suffix}` : ''}`, {
    method: 'GET',
    signal,
  })
}

export const fetchUsageTrends = async (params: UsageTrendQueryInput, signal: AbortSignal): Promise<UsageTrendResponse> => {
  const search = new URLSearchParams()
  search.set('mode', params.mode)

  if (params.from) {
    search.set('from', params.from)
  }

  if (params.to) {
    search.set('to', params.to)
  }

  if (params.agents?.length) {
    params.agents.forEach((agentId) => {
      search.append('agent', agentId)
    })
  }

  return jsonFetch<UsageTrendResponse>(`/console/api/usage/trends/?${search.toString()}`, {
    method: 'GET',
    signal,
  })
}

export const fetchUsageAgents = async (signal: AbortSignal): Promise<UsageAgentsResponse> => {
  return jsonFetch<UsageAgentsResponse>('/console/api/usage/agents/', {
    method: 'GET',
    signal,
  })
}

export const fetchUsageToolBreakdown = async (
  params: UsageToolBreakdownQueryInput,
  signal: AbortSignal,
): Promise<UsageToolBreakdownResponse> => {
  const search = new URLSearchParams()

  if (params.from) {
    search.set('from', params.from)
  }

  if (params.to) {
    search.set('to', params.to)
  }

  if (params.agents?.length) {
    params.agents.forEach((agentId) => {
      search.append('agent', agentId)
    })
  }

  const suffix = search.toString()
  return jsonFetch<UsageToolBreakdownResponse>(`/console/api/usage/tools/${suffix ? `?${suffix}` : ''}`, {
    method: 'GET',
    signal,
  })
}

export const fetchUsageAgentLeaderboard = async (
  params: UsageAgentLeaderboardQueryInput,
  signal: AbortSignal,
): Promise<UsageAgentLeaderboardResponse> => {
  const search = new URLSearchParams()

  if (params.from) {
    search.set('from', params.from)
  }

  if (params.to) {
    search.set('to', params.to)
  }

  if (params.agents?.length) {
    params.agents.forEach((agentId) => {
      search.append('agent', agentId)
    })
  }

  const suffix = search.toString()
  return jsonFetch<UsageAgentLeaderboardResponse>(`/console/api/usage/agents/leaderboard/${suffix ? `?${suffix}` : ''}`, {
    method: 'GET',
    signal,
  })
}
