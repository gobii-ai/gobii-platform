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

  // The roster scoped to a specific agent comes back with that agent's own context, which can
  // differ from the one the key describes. Leaving forAgentId out of the key filed that response
  // under the wrong entry, and the caller then discarded it as a context mismatch.
  const forAgentId = options?.forAgentId ?? null

  return useQuery({
    queryKey: ['agent-roster', contextKey, forAgentId] as const,
    queryFn: ({ signal }) => fetchAgentRoster({
      context: context ?? undefined,
      forAgentId: forAgentId ?? undefined,
      signal,
      staffContext: options?.staffContext,
    }),
    placeholderData: keepPreviousData,
    staleTime: 60_000,
    refetchOnWindowFocus: true,
    refetchOnReconnect: true,
    refetchInterval: refetchIntervalMs,
    refetchIntervalInBackground: false,
    enabled,
  })
}
