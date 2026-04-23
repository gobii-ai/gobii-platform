import { useQuery } from '@tanstack/react-query'

import { fetchAgentInsights } from '../api/agentChat'

export const AGENT_INSIGHTS_STALE_TIME_MS = 5 * 60 * 1000

export function agentInsightsQueryKey(agentId: string | null) {
  return ['agent-insights', agentId] as const
}

export function useAgentInsights(agentId: string | null, options?: { enabled?: boolean }) {
  return useQuery({
    queryKey: agentInsightsQueryKey(agentId),
    queryFn: ({ signal }) => {
      if (!agentId) {
        throw new Error('No agentId')
      }
      return fetchAgentInsights(agentId, { signal })
    },
    enabled: Boolean(agentId) && options?.enabled !== false,
    staleTime: AGENT_INSIGHTS_STALE_TIME_MS,
    refetchOnWindowFocus: false,
  })
}
