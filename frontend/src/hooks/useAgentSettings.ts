import { useQuery } from '@tanstack/react-query'

import { fetchAgentSettings } from '../api/agentSettings'

type UseAgentSettingsOptions = {
  enabled?: boolean
}

export function useAgentSettings(agentId?: string | null, options?: UseAgentSettingsOptions) {
  const enabled = Boolean(agentId) && (options?.enabled ?? true)

  return useQuery({
    queryKey: ['agent-settings', agentId ?? null],
    queryFn: () => fetchAgentSettings(agentId as string),
    staleTime: 60_000,
    refetchOnWindowFocus: false,
    enabled,
  })
}
