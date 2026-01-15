import { useQuery } from '@tanstack/react-query'

import { fetchAgentRoster } from '../api/agents'

export function useAgentRoster(options?: { enabled?: boolean; contextKey?: string }) {
  const enabled = options?.enabled ?? true
  const contextKey = options?.contextKey ?? 'default'
  return useQuery({
    queryKey: ['agent-roster', contextKey],
    queryFn: fetchAgentRoster,
    staleTime: 60_000,
    refetchOnWindowFocus: false,
    enabled,
  })
}
