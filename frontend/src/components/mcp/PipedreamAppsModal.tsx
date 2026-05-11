import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Users } from 'lucide-react'

import {
  disconnectAgentPipedreamApp,
  fetchPipedreamAppAgentConnections,
  searchPipedreamApps,
  startAgentPipedreamAppConnect,
  updatePipedreamAppSettings,
  type AgentPipedreamAppSource,
  type PipedreamAppAgentConnection,
  type PipedreamAppSettings,
  type PipedreamAppSummary,
} from '../../api/mcp'
import {
  AgentConnectionAvatar,
  PipedreamAppIcon,
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

type PipedreamAppsModalProps = {
  settingsUrl: string
  searchUrl: string
  initialSettings: PipedreamAppSettings
  onClose: () => void
  onSuccess: (message: string) => void
  onError: (message: string) => void
}

type WorkspacePipedreamAppRow = PipedreamAppSummary & {
  source: AgentPipedreamAppSource
}

type PendingAppAction = {
  slug: string
  kind: 'remove'
} | null

type PendingAgentAction = {
  agentId: string
  kind: 'connect' | 'disconnect'
} | null

export function PipedreamAppsModal({
  settingsUrl,
  searchUrl,
  initialSettings,
  onClose,
  onSuccess,
  onError,
}: PipedreamAppsModalProps) {
  const queryClient = useQueryClient()
  const settingsQueryKey = useMemo(() => ['pipedream-app-settings', settingsUrl] as const, [settingsUrl])
  const isMobile = useIsMobile()
  const [searchTerm, setSearchTerm] = useState('')
  const debouncedSearchTerm = useDebouncedValue(searchTerm)
  const [settings, setSettings] = useState(initialSettings)
  const [activeApp, setActiveApp] = useState<WorkspacePipedreamAppRow | null>(null)
  const [pendingAppAction, setPendingAppAction] = useState<PendingAppAction>(null)
  const [pendingAgentAction, setPendingAgentAction] = useState<PendingAgentAction>(null)
  const [statusMessage, setStatusMessage] = useState<PipedreamStatusMessage>(null)

  useEffect(() => {
    setSettings(initialSettings)
  }, [initialSettings])

  useEffect(() => {
    setActiveApp(null)
    setStatusMessage(null)
  }, [settingsUrl])

  const searchQuery = useQuery({
    queryKey: ['pipedream-app-search', searchUrl, debouncedSearchTerm],
    queryFn: () => searchPipedreamApps(searchUrl, debouncedSearchTerm),
    enabled: debouncedSearchTerm.length > 0 && activeApp === null,
  })

  const activeAppSlug = activeApp?.slug ?? ''
  const connectionsQueryKey = useMemo(
    () => ['pipedream-app-agent-connections', settingsUrl, activeAppSlug] as const,
    [activeAppSlug, settingsUrl],
  )
  const connectionsQuery = useQuery({
    queryKey: connectionsQueryKey,
    queryFn: () => fetchPipedreamAppAgentConnections(activeAppSlug),
    enabled: activeAppSlug.length > 0,
  })
  useWindowFocusRefetch(connectionsQuery.refetch, activeAppSlug.length > 0)

  const platformSlugSet = useMemo(
    () => new Set(settings.platformApps.map((app) => app.slug)),
    [settings.platformApps],
  )
  const selectedSlugSet = useMemo(
    () => new Set(settings.selectedApps.map((app) => app.slug)),
    [settings.selectedApps],
  )

  const rows = useMemo<WorkspacePipedreamAppRow[]>(() => {
    const visibleApps = debouncedSearchTerm ? (searchQuery.data ?? []) : settings.effectiveApps
    return visibleApps.map((app) => ({
      ...app,
      source: platformSlugSet.has(app.slug)
        ? 'built_in'
        : selectedSlugSet.has(app.slug)
          ? 'added'
          : 'available',
    }))
  }, [debouncedSearchTerm, platformSlugSet, searchQuery.data, selectedSlugSet, settings.effectiveApps])

  const removeMutation = useMutation({
    mutationFn: (app: WorkspacePipedreamAppRow) => {
      const nextSelectedSlugs = settings.selectedApps
        .map((selectedApp) => selectedApp.slug)
        .filter((slug) => slug !== app.slug)
      return updatePipedreamAppSettings(settingsUrl, nextSelectedSlugs)
    },
    onMutate: (app) => {
      setPendingAppAction({ slug: app.slug, kind: 'remove' })
      setStatusMessage(null)
    },
    onSuccess: (updatedSettings, app) => {
      setSettings(updatedSettings)
      queryClient.setQueryData(settingsQueryKey, updatedSettings)
      setStatusMessage({ text: `${app.name} removed.`, tone: 'info' })
      onSuccess(updatedSettings.message ?? 'Apps updated.')
    },
    onError: (error) => {
      const message = resolvePipedreamAppsErrorMessage(error, 'Unable to remove app.')
      setStatusMessage({ text: message, tone: 'error' })
      onError(message)
    },
    onSettled: () => setPendingAppAction(null),
  })

  const connectMutation = useMutation({
    mutationFn: ({ agent, app }: { agent: PipedreamAppAgentConnection; app: WorkspacePipedreamAppRow }) =>
      startAgentPipedreamAppConnect(agent.agentId, app.slug),
    onMutate: ({ agent }) => {
      setPendingAgentAction({ agentId: agent.agentId, kind: 'connect' })
      setStatusMessage(null)
    },
    onSuccess: (result, { app, agent }) => {
      window.open(result.connectUrl, '_blank', 'noopener,noreferrer')
      setSettings((current) => {
        if (current.selectedApps.some((selectedApp) => selectedApp.slug === result.app.slug)) {
          return current
        }
        return {
          ...current,
          selectedApps: [...current.selectedApps, result.app],
          effectiveApps: current.effectiveApps.some((effectiveApp) => effectiveApp.slug === result.app.slug)
            ? current.effectiveApps
            : [...current.effectiveApps, result.app],
        }
      })
      setStatusMessage({ text: `Connect ${app.name} for ${agent.name} in the new tab, then return here.`, tone: 'info' })
      void queryClient.invalidateQueries({ queryKey: ['pipedream-app-settings'], exact: false })
      void queryClient.invalidateQueries({ queryKey: ['pipedream-app-agent-connections', settingsUrl, app.slug] })
    },
    onError: (error) => {
      const message = resolvePipedreamAppsErrorMessage(error, 'Unable to start connection.')
      setStatusMessage({ text: message, tone: 'error' })
      onError(message)
    },
    onSettled: () => setPendingAgentAction(null),
  })

  const disconnectMutation = useMutation({
    mutationFn: ({ agent, app }: { agent: PipedreamAppAgentConnection; app: WorkspacePipedreamAppRow }) =>
      disconnectAgentPipedreamApp(agent.agentId, app.slug),
    onMutate: ({ agent }) => {
      setPendingAgentAction({ agentId: agent.agentId, kind: 'disconnect' })
      setStatusMessage(null)
    },
    onSuccess: (_result, { app, agent }) => {
      setStatusMessage({ text: `${app.name} disconnected from ${agent.name}.`, tone: 'info' })
      void queryClient.invalidateQueries({ queryKey: ['pipedream-app-agent-connections', settingsUrl, app.slug] })
    },
    onError: (error) => {
      const message = resolvePipedreamAppsErrorMessage(error, 'Unable to disconnect app.')
      setStatusMessage({ text: message, tone: 'error' })
      onError(message)
    },
    onSettled: () => setPendingAgentAction(null),
  })

  const isBusy = removeMutation.isPending || connectMutation.isPending || disconnectMutation.isPending
  const body = activeApp ? (
    <AppConnectionsScreen
      app={activeApp}
      agents={connectionsQuery.data?.agents ?? []}
      isLoading={connectionsQuery.isLoading}
      isFetching={connectionsQuery.isFetching}
      isError={connectionsQuery.isError}
      error={connectionsQuery.error}
      isBusy={isBusy || connectionsQuery.isFetching}
      pendingAgentAction={pendingAgentAction}
      statusMessage={statusMessage}
      onBack={() => {
        setActiveApp(null)
        setStatusMessage(null)
      }}
      onConnect={(agent) => connectMutation.mutate({ agent, app: activeApp })}
      onDisconnect={(agent) => disconnectMutation.mutate({ agent, app: activeApp })}
    />
  ) : (
    <AppListScreen
      apps={rows}
      searchTerm={searchTerm}
      isLoading={searchQuery.isLoading}
      isFetching={searchQuery.isFetching}
      isError={searchQuery.isError}
      error={searchQuery.error}
      isBusy={isBusy}
      isMobile={isMobile}
      pendingAppAction={pendingAppAction}
      statusMessage={statusMessage}
      onSearchTermChange={setSearchTerm}
      onManageConnections={(app) => {
        setActiveApp(app)
        setStatusMessage(null)
      }}
      onRemove={(app) => removeMutation.mutate(app)}
    />
  )

  return (
    <PipedreamModalShell
      isMobile={isMobile}
      title={activeApp ? 'Manage connections' : 'Manage integrations'}
      subtitle={activeApp ? `${activeApp.name} connections across agents.` : 'Search apps and manage agent connections.'}
      onClose={onClose}
    >
      {body}
    </PipedreamModalShell>
  )
}

function AppListScreen({
  apps,
  searchTerm,
  isLoading,
  isFetching,
  isError,
  error,
  isBusy,
  isMobile,
  pendingAppAction,
  statusMessage,
  onSearchTermChange,
  onManageConnections,
  onRemove,
}: {
  apps: WorkspacePipedreamAppRow[]
  searchTerm: string
  isLoading: boolean
  isFetching: boolean
  isError: boolean
  error: unknown
  isBusy: boolean
  isMobile: boolean
  pendingAppAction: PendingAppAction
  statusMessage: PipedreamStatusMessage
  onSearchTermChange: (term: string) => void
  onManageConnections: (app: WorkspacePipedreamAppRow) => void
  onRemove: (app: WorkspacePipedreamAppRow) => void
}) {
  return (
    <div className="space-y-4 p-1">
      <PipedreamStatusBanner statusMessage={statusMessage} />
      <PipedreamSearchInput
        value={searchTerm}
        onChange={onSearchTermChange}
        isFetching={isFetching}
        disabled={isBusy}
      />

      {isError ? (
        <PipedreamErrorState error={error} fallback="Unable to load apps." />
      ) : isLoading ? (
        <PipedreamLoadingState label="Loading apps…" />
      ) : apps.length === 0 ? (
        <PipedreamEmptyState label="No apps matched your search." />
      ) : (
        <PipedreamListFrame isMobile={isMobile}>
          {apps.map((app) => (
            <PipedreamAppRowItem
              key={app.slug}
              app={app}
              pendingAppAction={pendingAppAction}
              disabled={isBusy}
              onManageConnections={() => onManageConnections(app)}
              onRemove={() => onRemove(app)}
            />
          ))}
        </PipedreamListFrame>
      )}
    </div>
  )
}

function PipedreamAppRowItem({
  app,
  pendingAppAction,
  disabled,
  onManageConnections,
  onRemove,
}: {
  app: WorkspacePipedreamAppRow
  pendingAppAction: PendingAppAction
  disabled: boolean
  onManageConnections: () => void
  onRemove: () => void
}) {
  const isPendingRemove = pendingAppAction?.slug === app.slug && pendingAppAction.kind === 'remove'
  const removeDisabled = disabled || app.source !== 'added'
  const removeTitle = app.source === 'built_in'
    ? 'Built-in apps cannot be removed'
    : app.source === 'available'
      ? 'Add this app before removing it'
      : 'Remove app'

  return (
    <div className="grid gap-3 px-4 py-3 md:grid-cols-[minmax(0,1fr)_12rem_7rem] md:items-center">
      <PipedreamAppSummaryCell app={app} />
      <div className="flex justify-start md:justify-end">
        <button
          type="button"
          className="inline-flex min-w-44 items-center justify-center gap-2 rounded-md bg-blue-600 px-3 py-2 text-sm font-semibold text-white transition hover:bg-blue-700 disabled:opacity-60"
          onClick={onManageConnections}
          disabled={disabled}
        >
          <Users className="h-4 w-4" aria-hidden="true" />
          Manage Connections
        </button>
      </div>
      <div className="flex justify-start md:justify-end">
        <PipedreamRemoveButton
          isPending={isPendingRemove}
          disabled={removeDisabled}
          title={removeTitle}
          onClick={onRemove}
        />
      </div>
    </div>
  )
}

function AppConnectionsScreen({
  app,
  agents,
  isLoading,
  isFetching,
  isError,
  error,
  isBusy,
  pendingAgentAction,
  statusMessage,
  onBack,
  onConnect,
  onDisconnect,
}: {
  app: WorkspacePipedreamAppRow
  agents: PipedreamAppAgentConnection[]
  isLoading: boolean
  isFetching: boolean
  isError: boolean
  error: unknown
  isBusy: boolean
  pendingAgentAction: PendingAgentAction
  statusMessage: PipedreamStatusMessage
  onBack: () => void
  onConnect: (agent: PipedreamAppAgentConnection) => void
  onDisconnect: (agent: PipedreamAppAgentConnection) => void
}) {
  return (
    <div className="space-y-4 p-1">
      <button
        type="button"
        className="inline-flex items-center gap-2 rounded-md border border-slate-200 bg-white px-3 py-2 text-sm font-semibold text-slate-700 transition hover:bg-slate-50 disabled:opacity-60"
        onClick={onBack}
        disabled={isBusy}
      >
        <ArrowLeft className="h-4 w-4" aria-hidden="true" />
        Back
      </button>

      <div className="flex items-center gap-3">
        <PipedreamAppIcon app={app} />
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold text-slate-900">{app.name}</p>
          <p className="text-sm text-slate-600">{isFetching ? 'Refreshing connections…' : 'Connected agents are shown first.'}</p>
        </div>
      </div>

      <PipedreamStatusBanner statusMessage={statusMessage} />

      {isError ? (
        <PipedreamErrorState error={error} fallback="Unable to load agent connections." />
      ) : isLoading ? (
        <PipedreamLoadingState label="Loading agents…" />
      ) : agents.length === 0 ? (
        <PipedreamEmptyState label="No agents found." />
      ) : (
        <div className="overflow-hidden rounded-lg border border-slate-200 bg-white">
          <div className="divide-y divide-slate-200">
            {agents.map((agent) => (
              <AgentConnectionRow
                key={agent.agentId}
                agent={agent}
                pendingAgentAction={pendingAgentAction}
                disabled={isBusy}
                onConnect={() => onConnect(agent)}
                onDisconnect={() => onDisconnect(agent)}
              />
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function AgentConnectionRow({
  agent,
  pendingAgentAction,
  disabled,
  onConnect,
  onDisconnect,
}: {
  agent: PipedreamAppAgentConnection
  pendingAgentAction: PendingAgentAction
  disabled: boolean
  onConnect: () => void
  onDisconnect: () => void
}) {
  const isPending = pendingAgentAction?.agentId === agent.agentId
  const pendingKind = isPending ? pendingAgentAction?.kind : null

  return (
    <div className="grid gap-3 px-4 py-3 md:grid-cols-[minmax(0,1fr)_8rem] md:items-center">
      <div className="flex min-w-0 items-center gap-3">
        <AgentConnectionAvatar agent={agent} />
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold text-slate-900">{agent.name}</p>
        </div>
      </div>
      <div className="flex justify-start md:justify-end">
        <PipedreamConnectionButton
          connected={agent.connected}
          pendingKind={pendingKind}
          disabled={disabled}
          onConnect={onConnect}
          onDisconnect={onDisconnect}
        />
      </div>
    </div>
  )
}
