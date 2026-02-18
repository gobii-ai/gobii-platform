import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { fetchAgentQuickSettings, updateAgentQuickSettings } from '../api/agentQuickSettings'
import type { AgentQuickSettingsUpdatePayload } from '../types/agentQuickSettings'

type UseAgentQuickSettingsOptions = {
  enabled?: boolean
}

export function useAgentQuickSettings(agentId?: string | null, options?: UseAgentQuickSettingsOptions) {
  const enabled = Boolean(agentId) && (options?.enabled ?? true)
  const queryClient = useQueryClient()
  const query = useQuery({
    queryKey: ['agent-quick-settings', agentId ?? null],
    queryFn: () => fetchAgentQuickSettings(agentId as string),
    staleTime: 60_000,
    refetchOnWindowFocus: false,
    enabled,
  })

  const mutation = useMutation({
    mutationFn: (payload: AgentQuickSettingsUpdatePayload) => updateAgentQuickSettings(agentId as string, payload),
    onSuccess: (data) => {
      if (agentId) {
        queryClient.setQueryData(['agent-quick-settings', agentId], data)
      }
    },
  })

  return {
    ...query,
    updateQuickSettings: mutation.mutateAsync,
    updating: mutation.isPending,
  }
}
