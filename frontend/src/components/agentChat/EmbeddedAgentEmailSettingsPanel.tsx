import { useCallback } from 'react'
import { useQueryClient } from '@tanstack/react-query'

import { AgentEmailSettingsScreen } from '../../screens/AgentEmailSettingsScreen'
import type { AgentRosterEntry } from '../../types/agentRoster'
import { EmbeddedAgentShellPanel } from './EmbeddedAgentShellPanel'

type EmbeddedAgentEmailSettingsPanelProps = {
  agentId: string
  onBack?: () => void
}

type RosterQueryData = {
  agents: AgentRosterEntry[]
}

function isRosterQueryData(value: unknown): value is RosterQueryData {
  if (!value || typeof value !== 'object') {
    return false
  }
  const data = value as { agents?: unknown }
  return Array.isArray(data.agents)
}

function updateRosterAgentEmail(
  current: RosterQueryData | undefined,
  agentId: string,
  endpointAddress: string | null,
): RosterQueryData | undefined {
  if (!isRosterQueryData(current)) {
    return current
  }

  let changed = false
  const nextAgents = current.agents.map((agent) => {
    if (agent.id !== agentId) {
      return agent
    }
    if ((agent.email ?? null) === endpointAddress) {
      return agent
    }
    changed = true
    return {
      ...agent,
      email: endpointAddress,
    }
  })

  if (!changed) {
    return current
  }

  return {
    ...current,
    agents: nextAgents,
  }
}

export function EmbeddedAgentEmailSettingsPanel({
  agentId,
  onBack,
}: EmbeddedAgentEmailSettingsPanelProps) {
  const queryClient = useQueryClient()

  const handleSaved = useCallback((payload: { endpointAddress: string | null }) => {
    queryClient.setQueriesData<RosterQueryData>(
      { queryKey: ['agent-roster'] },
      (current) => updateRosterAgentEmail(current, agentId, payload.endpointAddress),
    )
    void Promise.allSettled([
      queryClient.invalidateQueries({ queryKey: ['agent-roster'], exact: false }),
      queryClient.invalidateQueries({ queryKey: ['agent-quick-settings', agentId], exact: true }),
    ])
  }, [agentId, queryClient])

  return (
    <EmbeddedAgentShellPanel>
      <AgentEmailSettingsScreen
        agentId={agentId}
        emailSettingsUrl={`/console/api/agents/${agentId}/email-settings/`}
        ensureAccountUrl={`/console/api/agents/${agentId}/email-settings/ensure-account/`}
        testUrl={`/console/api/agents/${agentId}/email-settings/test/`}
        variant="embedded"
        onBack={onBack}
        onSaved={handleSaved}
      />
    </EmbeddedAgentShellPanel>
  )
}
