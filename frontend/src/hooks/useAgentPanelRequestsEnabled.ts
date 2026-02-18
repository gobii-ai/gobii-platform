import { useMemo } from 'react'

import type { AgentRosterEntry } from '../types/agentRoster'

type UseAgentPanelRequestsEnabledOptions = {
  activeAgentId: string | null
  isNewAgent: boolean
  rosterLoading: boolean
  allowAgentRefresh: boolean
  rosterAgents: AgentRosterEntry[]
}

export function useAgentPanelRequestsEnabled({
  activeAgentId,
  isNewAgent,
  rosterLoading,
  allowAgentRefresh,
  rosterAgents,
}: UseAgentPanelRequestsEnabledOptions): {
  activeAgentInRoster: boolean
  allowAgentPanelRequests: boolean
} {
  return useMemo(() => {
    const activeAgentInRoster = Boolean(
      activeAgentId && rosterAgents.some((agent) => agent.id === activeAgentId),
    )
    const allowAgentPanelRequests = Boolean(
      allowAgentRefresh
        && !isNewAgent
        && !rosterLoading
        && activeAgentInRoster,
    )
    return { activeAgentInRoster, allowAgentPanelRequests }
  }, [activeAgentId, allowAgentRefresh, isNewAgent, rosterAgents, rosterLoading])
}
