import { useCallback, useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, CheckCircle2, FolderOpen, Loader2, Plug, Unplug, Users } from 'lucide-react'

import {
  agentDiscordAppQueryKey,
  fetchAgentDiscordApp,
} from '../../api/discordNative'
import { fetchAgentRoster } from '../../api/agents'
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
  fetchNativeIntegrationPickerToken,
  fetchNativeIntegrations,
  revokeNativeIntegration,
  saveNativeIntegrationCredentials,
  startNativeIntegrationConnect,
  type NativeIntegrationProvider,
} from '../../api/nativeIntegrations'
import { safeErrorMessage } from '../../api/safeErrorMessage'
import {
  DISCORD_NATIVE_PROVIDER_KEY,
  withDiscordNativeProvider,
} from './DiscordNativeShared'
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
import {
  confirmNativeIntegrationDisconnect,
  NativeIntegrationFilesDisclosure,
  NativeProviderIconTile,
  nativeIntegrationFilesQueryKey,
  nativeOAuthContextPayload,
  openGoogleDrivePicker,
  openNativeOAuthPopup,
  storePendingNativeOAuth,
  supportsNativeIntegrationPicker,
  usesManualNativeIntegrationCredentials,
  useNativeIntegrationRefreshEffects,
} from './NativeIntegrationShared'
import { NativeIntegrationCredentialFormModal } from './NativeIntegrationCredentialFormModal'
import {
  agentHasDiscordNative,
  DiscordAgentConnectionsScreen,
  DiscordConfigurationScreen,
  useDiscordNativeAgentActions,
  useDiscordNativeDisconnect,
  useDiscordOAuthCompleteRefetch,
} from './DiscordNativeAppModal'

type PipedreamAppsModalProps = {
  settingsUrl: string | null
  searchUrl: string | null
  nativeIntegrationsUrl?: string | null
  initialSettings: PipedreamAppSettings
  onClose: () => void
  onError: (message: string) => void
}

type WorkspacePipedreamAppRow = PipedreamAppSummary & {
  source: AgentPipedreamAppSource
}

type WorkspaceAppRow =
  | (WorkspacePipedreamAppRow & { kind: 'pipedream' })
  | (NativeIntegrationProvider & { kind: 'native' })
  | (NativeIntegrationProvider & { kind: 'discord' })

type PendingAppAction = {
  slug: string
  kind: 'remove'
} | null

type PendingNativeAction = {
  providerKey: string
  kind: 'connect' | 'disconnect' | 'picker'
} | null

type PendingAgentAction = {
  agentId: string
  kind: 'connect' | 'disconnect'
} | null

export function PipedreamAppsModal({
  settingsUrl,
  searchUrl,
  nativeIntegrationsUrl = null,
  initialSettings,
  onClose,
  onError,
}: PipedreamAppsModalProps) {
  const queryClient = useQueryClient()
  const settingsQueryKey = useMemo(() => ['pipedream-app-settings', settingsUrl] as const, [settingsUrl])
  const isMobile = useIsMobile()
  const [searchTerm, setSearchTerm] = useState('')
  const debouncedSearchTerm = useDebouncedValue(searchTerm)
  const [settings, setSettings] = useState(initialSettings)
  const [activeApp, setActiveApp] = useState<WorkspacePipedreamAppRow | null>(null)
  const [discordConnectionsOpen, setDiscordConnectionsOpen] = useState(false)
  const [activeDiscordAgentId, setActiveDiscordAgentId] = useState<string | null>(null)
  const [pendingAppAction, setPendingAppAction] = useState<PendingAppAction>(null)
  const [pendingNativeAction, setPendingNativeAction] = useState<PendingNativeAction>(null)
  const [pendingAgentAction, setPendingAgentAction] = useState<PendingAgentAction>(null)
  const [credentialProvider, setCredentialProvider] = useState<NativeIntegrationProvider | null>(null)
  const [nativeDeepLinkHandled, setNativeDeepLinkHandled] = useState(false)
  const [statusMessage, setStatusMessage] = useState<PipedreamStatusMessage>(null)
  const nativeQueryKey = useMemo(
    () => ['native-integrations', nativeIntegrationsUrl] as const,
    [nativeIntegrationsUrl],
  )
  useNativeIntegrationRefreshEffects({ queryKey: nativeQueryKey, onError })
  const handleDiscordError = useCallback((message: string) => {
    setStatusMessage({ text: message, tone: 'error' })
    onError(message)
  }, [onError])
  useDiscordOAuthCompleteRefetch({ onError: handleDiscordError })
  const {
    connectDiscordAgent,
    saveDiscordAgentSubscriptions,
    pendingDiscordAgentAction,
    isDiscordAgentActionPending,
  } = useDiscordNativeAgentActions({
    onStart: () => setStatusMessage(null),
    onError: handleDiscordError,
  })

  useEffect(() => {
    setSettings(initialSettings)
  }, [initialSettings])

  useEffect(() => {
    setActiveApp(null)
    setDiscordConnectionsOpen(false)
    setActiveDiscordAgentId(null)
    setStatusMessage(null)
  }, [settingsUrl])

  const searchQuery = useQuery({
    queryKey: ['pipedream-app-search', searchUrl, debouncedSearchTerm],
    queryFn: () => searchPipedreamApps(searchUrl as string, debouncedSearchTerm),
    enabled: Boolean(searchUrl) && debouncedSearchTerm.length > 0 && activeApp === null,
  })

  const nativeIntegrationsQuery = useQuery({
    queryKey: nativeQueryKey,
    queryFn: () => fetchNativeIntegrations(nativeIntegrationsUrl as string),
    enabled: Boolean(nativeIntegrationsUrl) && activeApp === null,
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
  const agentRosterQuery = useQuery({
    queryKey: ['agent-roster'],
    queryFn: () => fetchAgentRoster(),
    enabled: Boolean(nativeIntegrationsUrl) && activeApp === null,
  })
  useWindowFocusRefetch(agentRosterQuery.refetch, discordConnectionsOpen && activeDiscordAgentId === null)
  const activeDiscordAppQueryKey = useMemo(
    () => activeDiscordAgentId ? agentDiscordAppQueryKey(activeDiscordAgentId) : ['agent-discord-app', null] as const,
    [activeDiscordAgentId],
  )
  const activeDiscordAppQuery = useQuery({
    queryKey: activeDiscordAppQueryKey,
    queryFn: () => fetchAgentDiscordApp(activeDiscordAgentId as string),
    enabled: Boolean(activeDiscordAgentId),
  })

  const platformSlugSet = useMemo(
    () => new Set(settings.platformApps.map((app) => app.slug)),
    [settings.platformApps],
  )
  const selectedSlugSet = useMemo(
    () => new Set(settings.selectedApps.map((app) => app.slug)),
    [settings.selectedApps],
  )
  const discordConnected = useMemo(
    () => (agentRosterQuery.data?.agents ?? []).some(agentHasDiscordNative),
    [agentRosterQuery.data?.agents],
  )

  const rows = useMemo<WorkspaceAppRow[]>(() => {
    const visibleApps = debouncedSearchTerm ? (searchQuery.data ?? []) : settings.effectiveApps
    const normalizedSearch = debouncedSearchTerm.toLowerCase()
    const nativeRows = withDiscordNativeProvider(nativeIntegrationsQuery.data?.providers ?? [])
      .filter((provider) => {
        if (!normalizedSearch) {
          return true
        }
        return [
          provider.providerKey,
          provider.displayName,
          provider.description,
        ].some((value) => value.toLowerCase().includes(normalizedSearch))
      })
      .map((provider) => ({
        ...provider,
        connected: provider.providerKey === DISCORD_NATIVE_PROVIDER_KEY ? discordConnected : provider.connected,
        kind: provider.providerKey === DISCORD_NATIVE_PROVIDER_KEY ? 'discord' as const : 'native' as const,
      }))
    const pipedreamRows = visibleApps.map((app) => {
      const source: AgentPipedreamAppSource = platformSlugSet.has(app.slug)
        ? 'built_in'
        : selectedSlugSet.has(app.slug)
          ? 'added'
          : 'available'
      return {
        ...app,
        kind: 'pipedream' as const,
        source,
      }
    })
    return [...nativeRows, ...pipedreamRows]
  }, [
    debouncedSearchTerm,
    discordConnected,
    nativeIntegrationsQuery.data?.providers,
    platformSlugSet,
    searchQuery.data,
    selectedSlugSet,
    settings.effectiveApps,
  ])

  const removeMutation = useMutation({
    mutationFn: (app: WorkspacePipedreamAppRow) => {
      const nextSelectedSlugs = settings.selectedApps
        .map((selectedApp) => selectedApp.slug)
        .filter((slug) => slug !== app.slug)
      if (!settingsUrl) {
        throw new Error('Pipedream app settings are unavailable.')
      }
      return updatePipedreamAppSettings(settingsUrl, nextSelectedSlugs)
    },
    onMutate: (app) => {
      setPendingAppAction({ slug: app.slug, kind: 'remove' })
      setStatusMessage(null)
    },
    onSuccess: (updatedSettings) => {
      setSettings(updatedSettings)
      queryClient.setQueryData(settingsQueryKey, updatedSettings)
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
    onSuccess: (result, { app }) => {
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
    onSuccess: (_result, { app }) => {
      void queryClient.invalidateQueries({ queryKey: ['pipedream-app-agent-connections', settingsUrl, app.slug] })
    },
    onError: (error) => {
      const message = resolvePipedreamAppsErrorMessage(error, 'Unable to disconnect app.')
      setStatusMessage({ text: message, tone: 'error' })
      onError(message)
    },
    onSettled: () => setPendingAgentAction(null),
  })

  const nativeConnectMutation = useMutation({
    mutationFn: ({ provider }: { provider: NativeIntegrationProvider; popup: Window | null }) =>
      startNativeIntegrationConnect(provider.connectUrl),
    onMutate: ({ provider }) => {
      setPendingNativeAction({ providerKey: provider.providerKey, kind: 'connect' })
      setStatusMessage(null)
    },
    onSuccess: (payload, { provider, popup }) => {
      storePendingNativeOAuth(payload.state, nativeOAuthContextPayload(provider, payload.state, popup))
      if (popup && !popup.closed) {
        popup.location.href = payload.authorizationUrl
        popup.focus()
        return
      }
      if (popup?.closed) {
        const message = 'Connection window was closed before Google opened.'
        setStatusMessage({ text: message, tone: 'error' })
        onError(message)
        return
      }
      window.location.href = payload.authorizationUrl
    },
    onError: (error, { popup }) => {
      if (popup && !popup.closed) {
        popup.close()
      }
      const message = safeErrorMessage(error)
      setStatusMessage({ text: message, tone: 'error' })
      onError(message)
    },
    onSettled: () => setPendingNativeAction(null),
  })

  const nativeCredentialMutation = useMutation({
    mutationFn: ({ provider, credentials }: { provider: NativeIntegrationProvider; credentials: Record<string, string | null> }) =>
      saveNativeIntegrationCredentials(provider.connectUrl, credentials),
    onMutate: ({ provider }) => {
      setPendingNativeAction({ providerKey: provider.providerKey, kind: 'connect' })
      setStatusMessage(null)
    },
    onSuccess: (payload, { provider }) => {
      setStatusMessage({
        text: payload.connected
          ? `${provider.displayName} is connected.`
          : `Saved ${provider.displayName}. Add the remaining required credentials to finish setup.`,
      })
      void queryClient.invalidateQueries({ queryKey: nativeQueryKey })
      void queryClient.invalidateQueries({ queryKey: ['agent-roster'], exact: false })
    },
    onError: (error) => {
      const message = safeErrorMessage(error)
      setStatusMessage({ text: message, tone: 'error' })
      onError(message)
    },
    onSettled: () => setPendingNativeAction(null),
  })

  const nativeRevokeMutation = useMutation({
    mutationFn: (provider: NativeIntegrationProvider) => revokeNativeIntegration(provider.revokeUrl).then(() => provider),
    onMutate: (provider) => {
      setPendingNativeAction({ providerKey: provider.providerKey, kind: 'disconnect' })
      setStatusMessage(null)
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: nativeQueryKey })
    },
    onError: (error) => {
      const message = safeErrorMessage(error)
      setStatusMessage({ text: message, tone: 'error' })
      onError(message)
    },
    onSettled: () => setPendingNativeAction(null),
  })

  const discordDisconnectMutation = useDiscordNativeDisconnect({
    onMutate: () => {
      setPendingNativeAction({ providerKey: DISCORD_NATIVE_PROVIDER_KEY, kind: 'disconnect' })
      setStatusMessage(null)
    },
    onSuccess: () => {
      setDiscordConnectionsOpen(false)
      setActiveDiscordAgentId(null)
      void queryClient.invalidateQueries({ queryKey: ['agent-roster'], exact: false })
      void queryClient.invalidateQueries({ queryKey: ['agent-discord-app'], exact: false })
    },
    onError: handleDiscordError,
    onSettled: () => setPendingNativeAction(null),
  })

  const nativePickerMutation = useMutation({
    mutationFn: async (provider: NativeIntegrationProvider) => {
      const token = await fetchNativeIntegrationPickerToken(provider.pickerTokenUrl)
      const selectedFiles = await openGoogleDrivePicker(token)
      return { provider, selectedCount: selectedFiles.length }
    },
    onMutate: (provider) => {
      setPendingNativeAction({ providerKey: provider.providerKey, kind: 'picker' })
      setStatusMessage(null)
    },
    onSuccess: ({ provider }) => {
      void queryClient.invalidateQueries({ queryKey: nativeIntegrationFilesQueryKey(provider) })
    },
    onError: (error) => {
      const message = safeErrorMessage(error)
      setStatusMessage({ text: message, tone: 'error' })
      onError(message)
    },
    onSettled: () => setPendingNativeAction(null),
  })

  const isBusy = removeMutation.isPending
    || connectMutation.isPending
    || disconnectMutation.isPending
    || nativeConnectMutation.isPending
    || nativeCredentialMutation.isPending
    || nativeRevokeMutation.isPending
    || nativePickerMutation.isPending
    || discordDisconnectMutation.isPending
    || isDiscordAgentActionPending
  const activeDiscordAgent = activeDiscordAgentId
    ? (agentRosterQuery.data?.agents ?? []).find((agent) => agent.id === activeDiscordAgentId) ?? null
    : null
  const body = activeDiscordAgentId ? (
    activeDiscordAppQuery.isError ? (
      <div className="space-y-4 p-1">
        <button
          type="button"
          className="inline-flex items-center gap-2 rounded-md border border-slate-200 bg-white px-3 py-2 text-sm font-semibold text-slate-700 transition hover:bg-slate-50 disabled:opacity-60"
          onClick={() => {
            setActiveDiscordAgentId(null)
            setStatusMessage(null)
          }}
          disabled={isBusy}
        >
          <ArrowLeft className="h-4 w-4" aria-hidden="true" />
          Back
        </button>
        <PipedreamErrorState error={activeDiscordAppQuery.error} fallback="Unable to load Discord configuration." />
      </div>
    ) : activeDiscordAppQuery.isLoading || !activeDiscordAppQuery.data ? (
      <div className="space-y-4 p-1">
        <button
          type="button"
          className="inline-flex items-center gap-2 rounded-md border border-slate-200 bg-white px-3 py-2 text-sm font-semibold text-slate-700 transition hover:bg-slate-50 disabled:opacity-60"
          onClick={() => {
            setActiveDiscordAgentId(null)
            setStatusMessage(null)
          }}
          disabled={isBusy}
        >
          <ArrowLeft className="h-4 w-4" aria-hidden="true" />
          Back
        </button>
        <PipedreamLoadingState label="Loading Discord configuration…" />
      </div>
    ) : (
      <DiscordConfigurationScreen
        agentId={activeDiscordAgentId}
        app={activeDiscordAppQuery.data}
        disabled={isBusy}
        pendingDiscordAction={pendingDiscordAgentAction?.agentId === activeDiscordAgentId ? pendingDiscordAgentAction.kind : null}
        statusMessage={statusMessage}
        onBack={() => {
          setActiveDiscordAgentId(null)
          setStatusMessage(null)
        }}
        onSave={(subscriptions) => saveDiscordAgentSubscriptions(activeDiscordAgentId, subscriptions)}
      />
    )
  ) : discordConnectionsOpen ? (
    <DiscordAgentConnectionsScreen
      agents={agentRosterQuery.data?.agents ?? []}
      isLoading={agentRosterQuery.isLoading}
      isFetching={agentRosterQuery.isFetching}
      isError={agentRosterQuery.isError}
      error={agentRosterQuery.error}
      isBusy={isBusy || agentRosterQuery.isFetching}
      pendingDiscordAgentAction={pendingDiscordAgentAction}
      statusMessage={statusMessage}
      onBack={() => {
        setDiscordConnectionsOpen(false)
        setStatusMessage(null)
      }}
      onConnect={(agent) => connectDiscordAgent(agent.id)}
      onConfigure={(agent) => {
        setActiveDiscordAgentId(agent.id)
        setStatusMessage(null)
      }}
    />
  ) : activeApp ? (
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
      isLoading={searchQuery.isLoading || nativeIntegrationsQuery.isLoading || agentRosterQuery.isLoading}
      isFetching={searchQuery.isFetching || nativeIntegrationsQuery.isFetching || agentRosterQuery.isFetching}
      isError={searchQuery.isError || nativeIntegrationsQuery.isError || agentRosterQuery.isError}
      error={searchQuery.error ?? nativeIntegrationsQuery.error ?? agentRosterQuery.error}
      isBusy={isBusy}
      isMobile={isMobile}
      pendingAppAction={pendingAppAction}
      pendingNativeAction={pendingNativeAction}
      statusMessage={statusMessage}
      onSearchTermChange={setSearchTerm}
      onManageConnections={(app) => {
        setActiveApp(app)
        setStatusMessage(null)
      }}
      onManageDiscordConnections={() => {
        setDiscordConnectionsOpen(true)
        setStatusMessage(null)
      }}
      onRemove={(app) => removeMutation.mutate(app)}
      onNativeConnect={(provider) => {
        if (usesManualNativeIntegrationCredentials(provider)) {
          setCredentialProvider(provider)
          return
        }
        nativeConnectMutation.mutate({ provider, popup: openNativeOAuthPopup(provider) })
      }}
      onNativeDisconnect={(provider) => {
        if (confirmNativeIntegrationDisconnect(provider)) {
          nativeRevokeMutation.mutate(provider)
        }
      }}
      onDiscordDisconnect={(provider) => {
        if (confirmNativeIntegrationDisconnect(provider)) {
          discordDisconnectMutation.mutate()
        }
      }}
      onNativePicker={(provider) => nativePickerMutation.mutate(provider)}
    />
  )

  useEffect(() => {
    if (nativeDeepLinkHandled || nativeIntegrationsQuery.isLoading || activeApp || discordConnectionsOpen || activeDiscordAgentId) {
      return
    }
    const params = new URLSearchParams(window.location.search)
    if (params.get('connect') !== '1') {
      return
    }
    const providerKey = params.get('provider')
    if (!providerKey) {
      return
    }
    const provider = (nativeIntegrationsQuery.data?.providers ?? []).find(
      (candidate) => candidate.providerKey === providerKey,
    )
    if (!provider) {
      return
    }
    setNativeDeepLinkHandled(true)
    if (usesManualNativeIntegrationCredentials(provider)) {
      setCredentialProvider(provider)
    } else {
      nativeConnectMutation.mutate({ provider, popup: openNativeOAuthPopup(provider) })
    }
  }, [
    activeApp,
    activeDiscordAgentId,
    discordConnectionsOpen,
    nativeConnectMutation,
    nativeDeepLinkHandled,
    nativeIntegrationsQuery.data?.providers,
    nativeIntegrationsQuery.isLoading,
  ])

  return (
    <>
      <PipedreamModalShell
        title={activeDiscordAgentId ? 'Configure Discord' : discordConnectionsOpen || activeApp ? 'Manage connections' : 'Manage integrations'}
        subtitle={
          activeDiscordAgentId
            ? `Choose Discord channels for ${activeDiscordAgent?.name ?? 'this agent'}.`
            : discordConnectionsOpen
              ? 'Configure Discord for each agent.'
              : activeApp
                ? `${activeApp.name} connections across agents.`
                : 'Search apps and manage agent connections.'
        }
        onClose={onClose}
      >
        {body}
      </PipedreamModalShell>
      {credentialProvider ? (
        <NativeIntegrationCredentialFormModal
          provider={credentialProvider}
          onClose={() => setCredentialProvider(null)}
          onSubmit={(credentials) => nativeCredentialMutation.mutateAsync({ provider: credentialProvider, credentials })}
        />
      ) : null}
    </>
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
  pendingNativeAction,
  statusMessage,
  onSearchTermChange,
  onManageConnections,
  onManageDiscordConnections,
  onRemove,
  onNativeConnect,
  onNativeDisconnect,
  onDiscordDisconnect,
  onNativePicker,
}: {
  apps: WorkspaceAppRow[]
  searchTerm: string
  isLoading: boolean
  isFetching: boolean
  isError: boolean
  error: unknown
  isBusy: boolean
  isMobile: boolean
  pendingAppAction: PendingAppAction
  pendingNativeAction: PendingNativeAction
  statusMessage: PipedreamStatusMessage
  onSearchTermChange: (term: string) => void
  onManageConnections: (app: WorkspacePipedreamAppRow) => void
  onManageDiscordConnections: () => void
  onRemove: (app: WorkspacePipedreamAppRow) => void
  onNativeConnect: (provider: NativeIntegrationProvider) => void
  onNativeDisconnect: (provider: NativeIntegrationProvider) => void
  onDiscordDisconnect: (provider: NativeIntegrationProvider) => void
  onNativePicker: (provider: NativeIntegrationProvider) => void
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
          {apps.map((app) => app.kind === 'native' ? (
            <NativeAppRowItem
              key={`native-${app.providerKey}`}
              provider={app}
              pendingNativeAction={pendingNativeAction}
              disabled={isBusy}
              onConnect={() => onNativeConnect(app)}
              onDisconnect={() => onNativeDisconnect(app)}
              onPicker={() => onNativePicker(app)}
            />
          ) : app.kind === 'discord' ? (
            <WorkspaceDiscordAppRowItem
              key="native-discord"
              provider={app}
              pendingNativeAction={pendingNativeAction}
              disabled={isBusy}
              onManageConnections={onManageDiscordConnections}
              onDisconnect={() => onDiscordDisconnect(app)}
            />
          ) : (
            <PipedreamAppRowItem
              key={`pipedream-${app.slug}`}
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

function WorkspaceDiscordAppRowItem({
  provider,
  pendingNativeAction,
  disabled,
  onManageConnections,
  onDisconnect,
}: {
  provider: NativeIntegrationProvider
  pendingNativeAction: PendingNativeAction
  disabled: boolean
  onManageConnections: () => void
  onDisconnect: () => void
}) {
  const isPendingDisconnect = pendingNativeAction?.providerKey === provider.providerKey && pendingNativeAction.kind === 'disconnect'

  return (
    <div className="grid gap-3 px-4 py-3 md:grid-cols-[minmax(0,1fr)_12rem_8rem] md:items-center">
      <NativeIntegrationSummaryCell provider={provider} />
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
        {provider.connected ? (
          <button
            type="button"
            className="inline-flex min-w-28 items-center justify-center gap-2 rounded-md border border-red-200 bg-white px-3 py-2 text-sm font-semibold text-red-700 transition hover:bg-red-50 disabled:opacity-60"
            onClick={onDisconnect}
            disabled={disabled}
          >
            {isPendingDisconnect ? (
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            ) : (
              <Unplug className="h-4 w-4" aria-hidden="true" />
            )}
            Disconnect
          </button>
        ) : null}
      </div>
    </div>
  )
}

function NativeAppRowItem({
  provider,
  pendingNativeAction,
  disabled,
  onConnect,
  onDisconnect,
  onPicker,
}: {
  provider: NativeIntegrationProvider
  pendingNativeAction: PendingNativeAction
  disabled: boolean
  onConnect: () => void
  onDisconnect: () => void
  onPicker: () => void
}) {
  const isPending = pendingNativeAction?.providerKey === provider.providerKey
  const pendingKind = isPending ? pendingNativeAction?.kind : null
  const pickerEnabled = provider.connected && supportsNativeIntegrationPicker(provider)

  return (
    <div className="px-4 py-3">
      <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_8rem_8rem_8rem] sm:items-start">
        <NativeIntegrationSummaryCell provider={provider} />
        <div>
          {provider.connected ? (
            <span className="inline-flex items-center gap-1.5 rounded-full border border-emerald-200 bg-emerald-50 px-2.5 py-1 text-xs font-semibold text-emerald-700">
              <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" />
              Connected
            </span>
          ) : (
            <span className="inline-flex rounded-full border border-slate-200 px-2.5 py-1 text-xs font-semibold text-slate-500">
              Workspace
            </span>
          )}
        </div>
        <div className="flex justify-start md:justify-end">
          {pickerEnabled ? (
            <button
              type="button"
              className="inline-flex min-w-28 items-center justify-center gap-2 rounded-md border border-blue-200 bg-white px-3 py-2 text-sm font-semibold text-blue-700 transition hover:bg-blue-50 disabled:opacity-60"
              onClick={onPicker}
              disabled={disabled}
            >
              {pendingKind === 'picker' ? (
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              ) : (
                <FolderOpen className="h-4 w-4" aria-hidden="true" />
              )}
              Select Files
            </button>
          ) : null}
        </div>
        <div className="flex justify-start md:justify-end">
          {provider.connected ? (
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
      <NativeIntegrationFilesDisclosure provider={provider} />
    </div>
  )
}

function NativeIntegrationSummaryCell({ provider }: { provider: NativeIntegrationProvider }) {
  return (
    <div className="flex min-w-0 items-center gap-3">
      <NativeProviderIconTile provider={provider} />
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <p className="truncate text-sm font-semibold text-slate-900">{provider.displayName}</p>
          <span className="rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-emerald-700">
            Native
          </span>
        </div>
        {provider.description ? <p className="mt-1 line-clamp-2 text-sm text-slate-600">{provider.description}</p> : null}
      </div>
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
