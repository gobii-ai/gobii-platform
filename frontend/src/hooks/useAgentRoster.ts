import { useQuery } from '@tanstack/react-query'

import { fetchAgentRoster } from '../api/agents'

export function useAgentRoster() {
  return useQuery({
    queryKey: ['agent-roster'],
    queryFn: fetchAgentRoster,
    staleTime: 60_000,
    refetchOnWindowFocus: false,
  })
}
