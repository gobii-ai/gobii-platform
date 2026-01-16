import { useQuery } from '@tanstack/react-query'

import { fetchAgentRoster } from '../api/agents'

export function useAgentRoster(options?: { enabled?: boolean; contextKey?: string; forAgentId?: string }) {
  const enabled = options?.enabled ?? true
  const contextKey = options?.contextKey ?? 'default'
  const forAgentId = options?.forAgentId
  return useQuery({
    queryKey: ['agent-roster', contextKey, forAgentId ?? null],
    queryFn: () => fetchAgentRoster({ forAgentId }),
    staleTime: 60_000,
    refetchOnWindowFocus: false,
    enabled,
  })
}
