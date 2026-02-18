import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { fetchAgentAddons, updateAgentAddons } from '../api/agentAddons'
import type { AgentAddonsUpdatePayload } from '../types/agentAddons'

type UseAgentAddonsOptions = {
  enabled?: boolean
}

export function useAgentAddons(agentId?: string | null, options?: UseAgentAddonsOptions) {
  const enabled = Boolean(agentId) && (options?.enabled ?? true)
  const queryClient = useQueryClient()
  const query = useQuery({
    queryKey: ['agent-addons', agentId ?? null],
    queryFn: () => fetchAgentAddons(agentId as string),
    staleTime: 60_000,
    refetchOnWindowFocus: false,
    enabled,
  })

  const mutation = useMutation({
    mutationFn: (payload: AgentAddonsUpdatePayload) => updateAgentAddons(agentId as string, payload),
    onSuccess: (data) => {
      if (agentId) {
        queryClient.setQueryData(['agent-addons', agentId], data)
      }
    },
  })

  return {
    ...query,
    updateAddons: mutation.mutateAsync,
    updating: mutation.isPending,
  }
}
