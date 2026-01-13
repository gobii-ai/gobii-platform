import { useQuery } from '@tanstack/react-query'

import { fetchAgentRoster } from '../api/agents'

export function useAgentRoster(agentId?: string | null) {
  return useQuery({
    queryKey: ['agent-roster', agentId ?? null],
    queryFn: () => fetchAgentRoster(agentId),
    staleTime: 60_000,
    refetchOnWindowFocus: false,
  })
}
