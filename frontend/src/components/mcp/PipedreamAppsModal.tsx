import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Loader2, Plug, Search, Sparkles, Trash2, Unplug, Users } from 'lucide-react'

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
import { AgentChatMobileSheet } from '../agentChat/AgentChatMobileSheet'
import { Modal } from '../common/Modal'
import { PipedreamAppIcon, resolvePipedreamAppsErrorMessage } from './PipedreamAppsShared'

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

type StatusMessage = {
  text: string
  tone: 'info' | 'error'
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
  const [isMobile, setIsMobile] = useState(false)
  const [searchTerm, setSearchTerm] = useState('')
  const [debouncedSearchTerm, setDebouncedSearchTerm] = useState('')
  const [settings, setSettings] = useState(initialSettings)
  const [activeApp, setActiveApp] = useState<WorkspacePipedreamAppRow | null>(null)
  const [pendingAppAction, setPendingAppAction] = useState<PendingAppAction>(null)
  const [pendingAgentAction, setPendingAgentAction] = useState<PendingAgentAction>(null)
  const [statusMessage, setStatusMessage] = useState<StatusMessage>(null)

  useEffect(() => {
    setSettings(initialSettings)
  }, [initialSettings])

  useEffect(() => {
    const checkMobile = () => setIsMobile(window.innerWidth < 768)
    checkMobile()
    window.addEventListener('resize', checkMobile)
    return () => window.removeEventListener('resize', checkMobile)
  }, [])

  useEffect(() => {
    const timeoutId = window.setTimeout(() => setDebouncedSearchTerm(searchTerm.trim()), 250)
    return () => window.clearTimeout(timeoutId)
  }, [searchTerm])

  const searchQuery = useQuery({
    queryKey: ['pipedream-app-search', searchUrl, debouncedSearchTerm],
    queryFn: () => searchPipedreamApps(searchUrl, debouncedSearchTerm),
    enabled: debouncedSearchTerm.length > 0 && activeApp === null,
  })

  const activeAppSlug = activeApp?.slug ?? ''
  const connectionsQuery = useQuery({
    queryKey: ['pipedream-app-agent-connections', activeAppSlug],
    queryFn: () => fetchPipedreamAppAgentConnections(activeAppSlug),
    enabled: activeAppSlug.length > 0,
  })

  useEffect(() => {
    const handleFocus = () => {
      if (activeAppSlug) {
        void connectionsQuery.refetch()
      }
    }
    window.addEventListener('focus', handleFocus)
    return () => window.removeEventListener('focus', handleFocus)
  }, [activeAppSlug, connectionsQuery])

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
        const nextSettings = {
          ...current,
          selectedApps: [...current.selectedApps, result.app],
          effectiveApps: current.effectiveApps.some((effectiveApp) => effectiveApp.slug === result.app.slug)
            ? current.effectiveApps
            : [...current.effectiveApps, result.app],
        }
        return nextSettings
      })
      setStatusMessage({ text: `Connect ${app.name} for ${agent.name} in the new tab, then return here.`, tone: 'info' })
      void queryClient.invalidateQueries({ queryKey: ['pipedream-app-settings'], exact: false })
      void queryClient.invalidateQueries({ queryKey: ['pipedream-app-agent-connections', app.slug] })
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
      void queryClient.invalidateQueries({ queryKey: ['pipedream-app-agent-connections', app.slug] })
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
      isBusy={isBusy}
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

  const title = activeApp ? 'Manage connections' : 'Manage integrations'
  const subtitle = activeApp
    ? `${activeApp.name} connections across agents.`
    : 'Search apps and manage agent connections.'

  if (isMobile) {
    return (
      <AgentChatMobileSheet
        open
        onClose={onClose}
        title={title}
        subtitle={subtitle}
        icon={Sparkles}
        ariaLabel={title}
        bodyPadding={false}
      >
        <div className="h-full min-h-0 overflow-y-auto overscroll-contain px-4 pb-6 pt-4">
          {body}
        </div>
      </AgentChatMobileSheet>
    )
  }

  return (
    <Modal
      title={title}
      subtitle={subtitle}
      onClose={onClose}
      widthClass="sm:max-w-5xl"
      icon={Sparkles}
      iconBgClass="bg-blue-100"
      iconColorClass="text-blue-700"
    >
      {body}
    </Modal>
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
  statusMessage: StatusMessage
  onSearchTermChange: (term: string) => void
  onManageConnections: (app: WorkspacePipedreamAppRow) => void
  onRemove: (app: WorkspacePipedreamAppRow) => void
}) {
  return (
    <div className="space-y-4 p-1">
      <StatusBanner statusMessage={statusMessage} />

      <label className="relative block text-sm text-slate-500">
        <span className="pointer-events-none absolute inset-y-0 left-3 flex items-center">
          {isFetching ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" aria-hidden="true" />}
        </span>
        <input
          type="search"
          className="w-full rounded-lg border border-slate-300 bg-white py-3 pl-10 pr-3 text-sm text-slate-800 shadow-sm focus:border-blue-500 focus:outline-none focus:ring-blue-500"
          placeholder="Search apps"
          value={searchTerm}
          onChange={(event) => onSearchTermChange(event.target.value)}
          disabled={isBusy}
        />
      </label>

      {isError ? (
        <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {resolvePipedreamAppsErrorMessage(error, 'Unable to load apps.')}
        </div>
      ) : isLoading ? (
        <div className="flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-4 py-5 text-sm text-slate-600">
          <Loader2 className="h-4 w-4 animate-spin" />
          Loading apps…
        </div>
      ) : apps.length === 0 ? (
        <div className="rounded-lg border border-slate-200 bg-white px-4 py-5 text-sm text-slate-600">
          No apps matched your search.
        </div>
      ) : (
        <div className={`overflow-hidden rounded-lg border border-slate-200 bg-white ${isMobile ? '' : 'max-h-[28rem] overflow-y-auto'}`}>
          <div className="divide-y divide-slate-200">
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
          </div>
        </div>
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
      <div className="flex min-w-0 items-center gap-3">
        <PipedreamAppIcon app={app} />
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold text-slate-900">{app.name}</p>
          {app.description ? <p className="mt-1 line-clamp-2 text-sm text-slate-600">{app.description}</p> : null}
        </div>
      </div>
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
        <button
          type="button"
          className="inline-flex min-w-24 items-center justify-center gap-2 rounded-md border border-slate-200 bg-white px-3 py-2 text-sm font-semibold text-slate-700 transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-45"
          onClick={onRemove}
          disabled={removeDisabled}
          title={removeTitle}
        >
          {isPendingRemove ? (
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          ) : (
            <Trash2 className="h-4 w-4" aria-hidden="true" />
          )}
          Remove
        </button>
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
  statusMessage: StatusMessage
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
          {isFetching ? (
            <p className="text-sm text-slate-600">Refreshing connections…</p>
          ) : (
            <p className="text-sm text-slate-600">Connected agents are shown first.</p>
          )}
        </div>
      </div>

      <StatusBanner statusMessage={statusMessage} />

      {isError ? (
        <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {resolvePipedreamAppsErrorMessage(error, 'Unable to load agent connections.')}
        </div>
      ) : isLoading ? (
        <div className="flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-4 py-5 text-sm text-slate-600">
          <Loader2 className="h-4 w-4 animate-spin" />
          Loading agents…
        </div>
      ) : agents.length === 0 ? (
        <div className="rounded-lg border border-slate-200 bg-white px-4 py-5 text-sm text-slate-600">
          No agents found.
        </div>
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
        <AgentAvatar agent={agent} />
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold text-slate-900">{agent.name}</p>
        </div>
      </div>
      <div className="flex justify-start md:justify-end">
        {agent.connected ? (
          <button
            type="button"
            className="inline-flex min-w-28 items-center justify-center gap-2 rounded-md border border-red-200 bg-white px-3 py-2 text-sm font-semibold text-red-700 transition hover:bg-red-50 disabled:opacity-60"
            onClick={onDisconnect}
            disabled={disabled}
          >
            {pendingKind === 'disconnect' ? (
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            ) : (
              <Unplug className="h-4 w-4" aria-hidden="true" />
            )}
            Disconnect
          </button>
        ) : (
          <button
            type="button"
            className="inline-flex min-w-28 items-center justify-center gap-2 rounded-md bg-blue-600 px-3 py-2 text-sm font-semibold text-white transition hover:bg-blue-700 disabled:opacity-60"
            onClick={onConnect}
            disabled={disabled}
          >
            {pendingKind === 'connect' ? (
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            ) : (
              <Plug className="h-4 w-4" aria-hidden="true" />
            )}
            Connect
          </button>
        )}
      </div>
    </div>
  )
}

function AgentAvatar({ agent }: { agent: PipedreamAppAgentConnection }) {
  if (agent.avatarUrl) {
    return (
      <img
        src={agent.avatarUrl}
        alt=""
        className="h-9 w-9 rounded-full border border-slate-200 bg-white object-cover"
        loading="lazy"
      />
    )
  }

  return (
    <span className="inline-flex h-9 w-9 items-center justify-center rounded-full border border-slate-200 bg-white text-xs font-semibold uppercase text-slate-700">
      {agent.name.slice(0, 2)}
    </span>
  )
}

function StatusBanner({ statusMessage }: { statusMessage: StatusMessage }) {
  if (!statusMessage) {
    return null
  }
  const classes = statusMessage.tone === 'error'
    ? 'border-red-200 bg-red-50 text-red-700'
    : 'border-blue-200 bg-blue-50 text-blue-800'
  return (
    <div className={`rounded-lg border px-4 py-3 text-sm ${classes}`}>
      {statusMessage.text}
    </div>
  )
}
