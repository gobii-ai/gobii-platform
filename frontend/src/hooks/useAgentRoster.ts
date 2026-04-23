import {keepPreviousData, useQuery, useQueryClient} from '@tanstack/react-query'

import { fetchAgentRoster } from '../api/agents'

type UseAgentRosterOptions = {
  enabled?: boolean
  contextKey?: string
  forAgentId?: string
  refetchIntervalMs?: number | false
}

export function useAgentRoster(options?: UseAgentRosterOptions) {
  const queryClient = useQueryClient()
  const enabled = options?.enabled ?? true
  const contextKey = options?.contextKey ?? 'default'
  const forAgentId = options?.forAgentId
  const refetchIntervalMs = options?.refetchIntervalMs ?? false
  const queryKey = ['agent-roster', contextKey] as const

  return useQuery({
    queryKey,
    queryFn: () => {
      const cached = queryClient.getQueryData(queryKey)
      return fetchAgentRoster({ forAgentId: cached ? undefined : forAgentId })
    },
    placeholderData: keepPreviousData,
    staleTime: 60_000,
    refetchOnWindowFocus: false,
    refetchInterval: refetchIntervalMs,
    refetchIntervalInBackground: false,
    enabled,
  })
}
