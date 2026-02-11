import {keepPreviousData, useQuery} from '@tanstack/react-query'

import { fetchAgentRoster } from '../api/agents'

type UseAgentRosterOptions = {
  enabled?: boolean
  contextKey?: string
  forAgentId?: string
  refetchIntervalMs?: number | false
}

export function useAgentRoster(options?: UseAgentRosterOptions) {
  const enabled = options?.enabled ?? true
  const contextKey = options?.contextKey ?? 'default'
  const forAgentId = options?.forAgentId
  const refetchIntervalMs = options?.refetchIntervalMs ?? false
  return useQuery({
    queryKey: ['agent-roster', contextKey, forAgentId ?? null],
    queryFn: () => fetchAgentRoster({ forAgentId }),
    placeholderData: keepPreviousData,
    staleTime: 60_000,
    refetchOnWindowFocus: false,
    refetchInterval: refetchIntervalMs,
    refetchIntervalInBackground: false,
    enabled,
  })
}
