import { useCallback } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Loader2 } from 'lucide-react'

import { useAgentSettings } from '../../hooks/useAgentSettings'
import { AgentSettingsWorkspace, type AgentSettingsWorkspaceSavePayload } from '../../screens/AgentDetailScreen'
import { useAgentChatStore } from '../../stores/agentChatStore'
import type { AgentOrganization } from '../../types/agentSettings'
import { EmbeddedAgentShellPanel } from './EmbeddedAgentShellPanel'

type EmbeddedAgentSettingsPanelProps = {
  agentId: string
  onBack?: () => void
  onDeleted?: () => void
  onOpenSecrets?: () => void
  onOpenEmailSettings?: () => void
  onOpenFiles?: () => void
  onReassigned?: (payload: {
    context?: { type: string; id: string; name?: string | null }
    redirect?: string | null
    organization?: AgentOrganization
  }) => void
}

type RosterQueryData = {
  agents: Array<{
    id: string
    name: string
    avatarUrl: string | null
    displayColorHex: string | null
    preferredLlmTier?: string | null
  }>
}

function isRosterQueryData(value: unknown): value is RosterQueryData {
  if (!value || typeof value !== 'object') {
    return false
  }
  const data = value as { agents?: unknown }
  return Array.isArray(data.agents)
}

function updateRosterIdentity(
  current: RosterQueryData | undefined,
  payload: AgentSettingsWorkspaceSavePayload,
): RosterQueryData | undefined {
  if (!isRosterQueryData(current)) {
    return current
  }

  let changed = false
  const nextAgents = current.agents.map((agent) => {
    if (agent.id !== payload.agentId) {
      return agent
    }
    changed = true
    return {
      ...agent,
      name: payload.agentName,
      avatarUrl: payload.agentAvatarUrl,
      displayColorHex: payload.agentColorHex,
      preferredLlmTier: payload.preferredLlmTier,
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

export function EmbeddedAgentSettingsPanel({
  agentId,
  onBack,
  onDeleted,
  onReassigned,
  onOpenSecrets,
  onOpenEmailSettings,
  onOpenFiles,
}: EmbeddedAgentSettingsPanelProps) {
  const queryClient = useQueryClient()
  const { data, isLoading, error, refetch } = useAgentSettings(agentId, { enabled: Boolean(agentId) })

  const handleSaved = useCallback((payload: AgentSettingsWorkspaceSavePayload) => {
    useAgentChatStore.getState().updateAgentIdentity({
      agentId: payload.agentId,
      agentName: payload.agentName,
      agentColorHex: payload.agentColorHex,
      agentAvatarUrl: payload.agentAvatarUrl,
    })
    queryClient.setQueriesData<RosterQueryData>(
      { queryKey: ['agent-roster'] },
      (current) => updateRosterIdentity(current, payload),
    )
    void Promise.allSettled([
      refetch(),
      queryClient.invalidateQueries({ queryKey: ['agent-roster'], exact: false }),
      queryClient.invalidateQueries({ queryKey: ['agent-quick-settings', payload.agentId], exact: true }),
      queryClient.invalidateQueries({ queryKey: ['agent-addons', payload.agentId], exact: true }),
    ])
  }, [queryClient, refetch])

  if (isLoading) {
    return (
      <EmbeddedAgentShellPanel>
        <div className="flex min-h-[18rem] items-center justify-center px-4 py-10 text-sm text-slate-200/80">
          <div className="flex flex-col items-center gap-3 text-center">
            <Loader2 className="h-6 w-6 animate-spin text-slate-300/70" aria-hidden="true" />
            <p>Loading full agent settings…</p>
          </div>
        </div>
      </EmbeddedAgentShellPanel>
    )
  }

  if (error || !data) {
    return (
      <EmbeddedAgentShellPanel>
        <div className="rounded-2xl border border-red-400/30 bg-red-950/30 px-4 py-4 text-sm text-red-100">
          <div className="flex items-start gap-3">
            <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-red-300" aria-hidden="true" />
            <div>
              <p className="font-medium">Unable to load full settings.</p>
              <p className="mt-1 text-red-200/80">Refresh the sidebar or try opening this agent again.</p>
            </div>
          </div>
        </div>
      </EmbeddedAgentShellPanel>
    )
  }

  return (
    <EmbeddedAgentShellPanel>
      <AgentSettingsWorkspace
        key={data.agent.id}
        initialData={data}
        variant="embedded"
        onBack={onBack}
        onSaved={handleSaved}
        onDeleted={onDeleted}
        onReassigned={onReassigned}
        onOpenSecrets={onOpenSecrets}
        onOpenEmailSettings={onOpenEmailSettings}
        onOpenFiles={onOpenFiles}
      />
    </EmbeddedAgentShellPanel>
  )
}
