import { keepPreviousData, useQuery } from '@tanstack/react-query'

import { fetchAgentRoster } from '../api/agents'
import type { ConsoleContext, StaffViewContext } from '../api/context'

type UseAgentRosterOptions = {
  enabled?: boolean
  context?: ConsoleContext | null
  contextKey?: string
  refetchIntervalMs?: number | false
  forAgentId?: string
  staffContext?: StaffViewContext | null
}

export function useAgentRoster(options?: UseAgentRosterOptions) {
  const enabled = options?.enabled ?? true
  const context = options?.context
  const contextKey = options?.contextKey ?? 'default'
  const refetchIntervalMs = options?.refetchIntervalMs ?? false

  return useQuery({
    queryKey: ['agent-roster', contextKey] as const,
    queryFn: () => fetchAgentRoster({
      context: context ?? undefined,
      forAgentId: options?.forAgentId,
      staffContext: options?.staffContext,
    }),
    placeholderData: keepPreviousData,
    staleTime: 60_000,
    refetchOnWindowFocus: false,
    refetchInterval: refetchIntervalMs,
    refetchIntervalInBackground: false,
    enabled,
  })
}
