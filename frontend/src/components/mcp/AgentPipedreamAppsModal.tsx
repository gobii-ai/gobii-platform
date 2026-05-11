import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2 } from 'lucide-react'

import {
  disconnectAgentPipedreamApp,
  fetchAgentPipedreamApps,
  removeAgentPipedreamApp,
  startAgentPipedreamAppConnect,
  type AgentPipedreamAppRow,
} from '../../api/mcp'
import {
  PipedreamAppSummaryCell,
  PipedreamConnectionButton,
  PipedreamEmptyState,
  PipedreamErrorState,
  PipedreamListFrame,
  PipedreamLoadingState,
  PipedreamModalShell,
  PipedreamRemoveButton,
  PipedreamSearchInput,
  PipedreamStatusBanner,
  resolvePipedreamAppsErrorMessage,
  useDebouncedValue,
  useIsMobile,
  useWindowFocusRefetch,
  type PipedreamStatusMessage,
} from './PipedreamAppsShared'

type AgentPipedreamAppsModalProps = {
  agentId: string
  onClose: () => void
}

type PendingAction = {
  slug: string
  kind: 'connect' | 'disconnect' | 'remove'
} | null

export function AgentPipedreamAppsModal({ agentId, onClose }: AgentPipedreamAppsModalProps) {
  const queryClient = useQueryClient()
  const isMobile = useIsMobile()
  const [searchTerm, setSearchTerm] = useState('')
  const debouncedSearchTerm = useDebouncedValue(searchTerm)
  const [pendingAction, setPendingAction] = useState<PendingAction>(null)
  const [statusMessage, setStatusMessage] = useState<PipedreamStatusMessage>(null)

  const appsQuery = useQuery({
    queryKey: ['agent-pipedream-apps', agentId, debouncedSearchTerm],
    queryFn: () => fetchAgentPipedreamApps(agentId, debouncedSearchTerm),
  })
  useWindowFocusRefetch(appsQuery.refetch)

  const connectMutation = useMutation({
    mutationFn: (app: AgentPipedreamAppRow) => startAgentPipedreamAppConnect(agentId, app.slug),
    onMutate: (app) => {
      setPendingAction({ slug: app.slug, kind: 'connect' })
      setStatusMessage(null)
    },
    onSuccess: (result, app) => {
      window.open(result.connectUrl, '_blank', 'noopener,noreferrer')
      setStatusMessage({ text: `Connect ${app.name} in the new tab, then return here.`, tone: 'info' })
      void queryClient.invalidateQueries({ queryKey: ['pipedream-app-settings'], exact: false })
      void appsQuery.refetch()
    },
    onError: (error) => {
      setStatusMessage({ text: resolvePipedreamAppsErrorMessage(error, 'Unable to start connection.'), tone: 'error' })
    },
    onSettled: () => setPendingAction(null),
  })

  const disconnectMutation = useMutation({
    mutationFn: (app: AgentPipedreamAppRow) => disconnectAgentPipedreamApp(agentId, app.slug),
    onMutate: (app) => {
      setPendingAction({ slug: app.slug, kind: 'disconnect' })
      setStatusMessage(null)
    },
    onSuccess: (_result, app) => {
      setStatusMessage({ text: `${app.name} disconnected.`, tone: 'info' })
      void appsQuery.refetch()
    },
    onError: (error) => {
      setStatusMessage({ text: resolvePipedreamAppsErrorMessage(error, 'Unable to disconnect app.'), tone: 'error' })
    },
    onSettled: () => setPendingAction(null),
  })

  const removeMutation = useMutation({
    mutationFn: (app: AgentPipedreamAppRow) => removeAgentPipedreamApp(agentId, app.slug),
    onMutate: (app) => {
      setPendingAction({ slug: app.slug, kind: 'remove' })
      setStatusMessage(null)
    },
    onSuccess: (_result, app) => {
      setStatusMessage({ text: `${app.name} removed.`, tone: 'info' })
      void queryClient.invalidateQueries({ queryKey: ['pipedream-app-settings'], exact: false })
      void appsQuery.refetch()
    },
    onError: (error) => {
      setStatusMessage({ text: resolvePipedreamAppsErrorMessage(error, 'Unable to remove app.'), tone: 'error' })
    },
    onSettled: () => setPendingAction(null),
  })

  const apps = appsQuery.data?.apps ?? []
  const isBusy = connectMutation.isPending || disconnectMutation.isPending || removeMutation.isPending

  return (
    <PipedreamModalShell
      isMobile={isMobile}
      title="Apps"
      subtitle="Search, connect, and disconnect apps for this agent."
      ariaLabel="Manage agent apps"
      onClose={onClose}
    >
      <div className="space-y-4 p-1">
        <PipedreamStatusBanner statusMessage={statusMessage} />
        <PipedreamSearchInput
          value={searchTerm}
          onChange={setSearchTerm}
          isFetching={appsQuery.isFetching}
          disabled={isBusy}
        />

        {appsQuery.isError ? (
          <PipedreamErrorState error={appsQuery.error} fallback="Unable to load apps." />
        ) : appsQuery.isLoading ? (
          <PipedreamLoadingState label="Loading apps…" />
        ) : apps.length === 0 ? (
          <PipedreamEmptyState label="No apps matched your search." />
        ) : (
          <PipedreamListFrame isMobile={isMobile}>
            {apps.map((app) => (
              <AgentPipedreamAppRowItem
                key={app.slug}
                app={app}
                pendingAction={pendingAction}
                disabled={isBusy}
                onConnect={() => connectMutation.mutate(app)}
                onDisconnect={() => disconnectMutation.mutate(app)}
                onRemove={() => removeMutation.mutate(app)}
              />
            ))}
          </PipedreamListFrame>
        )}
      </div>
    </PipedreamModalShell>
  )
}

function AgentPipedreamAppRowItem({
  app,
  pendingAction,
  disabled,
  onConnect,
  onDisconnect,
  onRemove,
}: {
  app: AgentPipedreamAppRow
  pendingAction: PendingAction
  disabled: boolean
  onConnect: () => void
  onDisconnect: () => void
  onRemove: () => void
}) {
  const isPending = pendingAction?.slug === app.slug
  const pendingKind = isPending ? pendingAction?.kind : null
  const removeDisabled = disabled || app.source !== 'added'
  const removeTitle = app.source === 'built_in'
    ? 'Built-in apps cannot be removed'
    : app.source === 'available'
      ? 'Connect or add this app before removing it'
      : 'Remove app'

  return (
    <div className="grid gap-3 px-4 py-3 md:grid-cols-[minmax(0,1fr)_7rem_8rem_7rem] md:items-center">
      <PipedreamAppSummaryCell app={app} />
      <div>
        {app.connected ? (
          <span className="inline-flex items-center gap-1.5 rounded-full border border-emerald-200 bg-emerald-50 px-2.5 py-1 text-xs font-semibold text-emerald-700">
            <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" />
            Connected
          </span>
        ) : (
          <span className="inline-flex rounded-full border border-slate-200 px-2.5 py-1 text-xs font-semibold text-slate-500">
            Not connected
          </span>
        )}
      </div>
      <div className="flex justify-start md:justify-end">
        <PipedreamConnectionButton
          connected={app.connected}
          pendingKind={pendingKind === 'connect' || pendingKind === 'disconnect' ? pendingKind : null}
          disabled={disabled}
          onConnect={onConnect}
          onDisconnect={onDisconnect}
        />
      </div>
      <div className="flex justify-start md:justify-end">
        <PipedreamRemoveButton
          isPending={pendingKind === 'remove'}
          disabled={removeDisabled}
          title={removeTitle}
          onClick={onRemove}
        />
      </div>
    </div>
  )
}
