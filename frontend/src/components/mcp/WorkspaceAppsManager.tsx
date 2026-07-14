import { useCallback, useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Loader2, Unplug, Users } from 'lucide-react'

import { agentDiscordAppQueryKey, fetchAgentDiscordApp } from '../../api/discordNative'
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
import { fetchNativeIntegrations, type NativeIntegrationProvider } from '../../api/nativeIntegrations'
import type { SettingsSurfaceVariant } from '../common/SettingsSurface'
import { DISCORD_NATIVE_PROVIDER_KEY, withDiscordNativeProvider } from './DiscordNativeShared'
import {
  AgentConnectionAvatar,
  PipedreamAppIcon,
  PipedreamAppSummaryCell,
  PipedreamConnectionButton,
  PipedreamEmptyState,
  PipedreamErrorState,
  PipedreamListFrame,
  PipedreamLoadingState,
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
  NativeIntegrationGridRow,
  NativeIntegrationSummaryCell,
  openNativeOAuthPopup,
  usesManualNativeIntegrationCredentials,
  useNativeIntegrationConnectMutation,
  useNativeIntegrationDisconnectMutation,
  useNativeIntegrationPickerMutation,
  useNativeIntegrationRefreshEffects,
} from './NativeIntegrationShared'
import { useManualNativeIntegrationConnect } from './useManualNativeIntegrationConnect'
import {
  agentHasDiscordNative,
  BackButton,
  DiscordAgentConnectionsScreen,
  DiscordConfigurationScreen,
  useDiscordNativeAgentActions,
  useDiscordNativeDisconnect,
  useDiscordOAuthCompleteRefetch,
} from './DiscordNativeAppModal'

type WorkspaceAppsManagerProps = {
  settingsUrl: string | null
  searchUrl: string | null
  nativeIntegrationsUrl?: string | null
  initialNativeProviderKey?: string | null
  initialNativeConnect?: boolean
  initialSettings: PipedreamAppSettings
  onError: (message: string) => void
  surface?: SettingsSurfaceVariant
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

export function WorkspaceAppsManager({
  settingsUrl,
  searchUrl,
  nativeIntegrationsUrl = null,
  initialNativeProviderKey = null,
  initialNativeConnect = false,
  initialSettings,
  onError,
  surface = 'standalone',
}: WorkspaceAppsManagerProps) {
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
  const [initialNativeConnectHandled, setInitialNativeConnectHandled] = useState(false)
  const [statusMessage, setStatusMessage] = useState<PipedreamStatusMessage>(null)
  const setNativeStatusMessage = useCallback((message: string | null) => {
    setStatusMessage(message ? { text: message, tone: 'error' } : null)
  }, [])
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

  const nativeConnectMutation = useNativeIntegrationConnectMutation({
    setPendingAction: setPendingNativeAction,
    setStatusMessage: setNativeStatusMessage,
    onError,
  })

  const {
    credentialModal,
    isPending: manualNativeConnectPending,
    openCredentialModal,
  } = useManualNativeIntegrationConnect({
    nativeQueryKey,
    extraInvalidateQueryKeys: [['agent-roster']],
    onMutate: (provider) => {
      setPendingNativeAction({ providerKey: provider.providerKey, kind: 'connect' })
      setStatusMessage(null)
    },
    onSuccess: (payload, provider) => {
      setStatusMessage({
        text: payload.connected
          ? `${provider.displayName} is connected.`
          : `Saved ${provider.displayName}. Add the remaining required credentials to finish setup.`,
      })
    },
    onError: (message) => {
      setStatusMessage({ text: message, tone: 'error' })
      onError(message)
    },
    onSettled: () => setPendingNativeAction(null),
  })

  const nativeRevokeMutation = useNativeIntegrationDisconnectMutation({
    nativeQueryKey,
    setPendingAction: setPendingNativeAction,
    setStatusMessage: setNativeStatusMessage,
    onError,
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

  const nativePickerMutation = useNativeIntegrationPickerMutation({
    setPendingAction: setPendingNativeAction,
    setStatusMessage: setNativeStatusMessage,
    onError,
  })

  const isBusy = removeMutation.isPending
    || connectMutation.isPending
    || disconnectMutation.isPending
    || nativeConnectMutation.isPending
    || manualNativeConnectPending
    || nativeRevokeMutation.isPending
    || nativePickerMutation.isPending
    || discordDisconnectMutation.isPending
    || isDiscordAgentActionPending
  const body = activeDiscordAgentId ? (
    activeDiscordAppQuery.isError ? (
      <div className="space-y-4 p-1">
        <BackButton
          surface={surface}
          onClick={() => {
            setActiveDiscordAgentId(null)
            setStatusMessage(null)
          }}
          disabled={isBusy}
        />
        <PipedreamErrorState error={activeDiscordAppQuery.error} fallback="Unable to load Discord configuration." surface={surface} />
      </div>
    ) : activeDiscordAppQuery.isLoading || !activeDiscordAppQuery.data ? (
      <div className="space-y-4 p-1">
        <BackButton
          surface={surface}
          onClick={() => {
            setActiveDiscordAgentId(null)
            setStatusMessage(null)
          }}
          disabled={isBusy}
        />
        <PipedreamLoadingState label="Loading Discord configuration…" surface={surface} />
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
        surface={surface}
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
      surface={surface}
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
      surface={surface}
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
          openCredentialModal(provider)
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
      surface={surface}
    />
  )

  useEffect(() => {
    if (
      initialNativeConnectHandled
      || !initialNativeConnect
      || !initialNativeProviderKey
      || nativeIntegrationsQuery.isLoading
      || activeApp
      || discordConnectionsOpen
      || activeDiscordAgentId
    ) {
      return
    }
    const provider = (nativeIntegrationsQuery.data?.providers ?? []).find(
      (candidate) => candidate.providerKey === initialNativeProviderKey,
    )
    if (!provider) {
      return
    }
    setInitialNativeConnectHandled(true)
    if (usesManualNativeIntegrationCredentials(provider)) {
      openCredentialModal(provider)
    } else {
      nativeConnectMutation.mutate({ provider, popup: openNativeOAuthPopup(provider) })
    }
  }, [
    activeApp,
    activeDiscordAgentId,
    discordConnectionsOpen,
    initialNativeConnect,
    initialNativeConnectHandled,
    initialNativeProviderKey,
    nativeConnectMutation,
    nativeIntegrationsQuery.data?.providers,
    nativeIntegrationsQuery.isLoading,
    openCredentialModal,
  ])

  return (
    <>
      {body}
      {credentialModal}
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
  surface,
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
  surface: SettingsSurfaceVariant
}) {
  return (
    <div className="space-y-4 p-1">
      <PipedreamStatusBanner statusMessage={statusMessage} surface={surface} />
      <PipedreamSearchInput
        value={searchTerm}
        onChange={onSearchTermChange}
        isFetching={isFetching}
        disabled={isBusy}
        surface={surface}
      />

      {isError ? (
        <PipedreamErrorState error={error} fallback="Unable to load apps." surface={surface} />
      ) : isLoading ? (
        <PipedreamLoadingState label="Loading apps…" surface={surface} />
      ) : apps.length === 0 ? (
        <PipedreamEmptyState label="No apps matched your search." surface={surface} />
      ) : (
        <PipedreamListFrame isMobile={isMobile} constrainHeight={false} surface={surface}>
          {apps.map((app) => app.kind === 'native' ? (
            <NativeAppRowItem
              key={`native-${app.providerKey}`}
              provider={app}
              pendingNativeAction={pendingNativeAction}
              disabled={isBusy}
              onConnect={() => onNativeConnect(app)}
              onDisconnect={() => onNativeDisconnect(app)}
              onPicker={() => onNativePicker(app)}
              surface={surface}
            />
          ) : app.kind === 'discord' ? (
            <WorkspaceDiscordAppRowItem
              key="native-discord"
              provider={app}
              pendingNativeAction={pendingNativeAction}
              disabled={isBusy}
              onManageConnections={onManageDiscordConnections}
              onDisconnect={() => onDiscordDisconnect(app)}
              surface={surface}
            />
          ) : (
            <PipedreamAppRowItem
              key={`pipedream-${app.slug}`}
              app={app}
              pendingAppAction={pendingAppAction}
              disabled={isBusy}
              onManageConnections={() => onManageConnections(app)}
              onRemove={() => onRemove(app)}
              surface={surface}
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
  surface,
}: {
  provider: NativeIntegrationProvider
  pendingNativeAction: PendingNativeAction
  disabled: boolean
  onManageConnections: () => void
  onDisconnect: () => void
  surface: SettingsSurfaceVariant
}) {
  const isPendingDisconnect = pendingNativeAction?.providerKey === provider.providerKey && pendingNativeAction.kind === 'disconnect'
  const manageClassName = surface === 'embedded'
    ? 'border border-sky-300/25 bg-sky-900/55 text-sky-50 hover:border-sky-200/40 hover:bg-sky-900/75'
    : 'bg-blue-600 text-white hover:bg-blue-700'
  const disconnectClassName = surface === 'embedded'
    ? 'border-rose-300/25 bg-rose-950/20 text-rose-200 hover:border-rose-200/40 hover:bg-rose-900/35'
    : 'border-red-200 bg-white text-red-700 hover:bg-red-50'

  return (
    <div className="grid gap-3 px-4 py-3 md:grid-cols-[minmax(0,1fr)_12rem_8rem] md:items-center">
      <NativeIntegrationSummaryCell provider={provider} surface={surface} />
      <div className="flex justify-start md:justify-end">
        <button
          type="button"
          className={`inline-flex min-w-28 items-center justify-center gap-2 rounded-md px-3 py-2 text-sm font-semibold transition disabled:opacity-60 ${manageClassName}`}
          onClick={onManageConnections}
          disabled={disabled}
        >
          <Users className="h-4 w-4" aria-hidden="true" />
          Manage
        </button>
      </div>
      <div className="flex justify-start md:justify-end">
        {provider.connected ? (
          <button
            type="button"
            className={`inline-flex min-w-28 items-center justify-center gap-2 rounded-md border px-3 py-2 text-sm font-semibold transition disabled:opacity-60 ${disconnectClassName}`}
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
  surface,
}: {
  provider: NativeIntegrationProvider
  pendingNativeAction: PendingNativeAction
  disabled: boolean
  onConnect: () => void
  onDisconnect: () => void
  onPicker: () => void
  surface: SettingsSurfaceVariant
}) {
  const isPending = pendingNativeAction?.providerKey === provider.providerKey
  const pendingKind = isPending ? pendingNativeAction?.kind : null

  return (
    <NativeIntegrationGridRow
      provider={provider}
      pendingKind={pendingKind}
      disabled={disabled}
      onConnect={onConnect}
      onDisconnect={onDisconnect}
      onPicker={onPicker}
      gridClassName="grid gap-3 sm:grid-cols-[minmax(0,1fr)_8rem_8rem_8rem] sm:items-start"
      surface={surface}
    />
  )
}

function PipedreamAppRowItem({
  app,
  pendingAppAction,
  disabled,
  onManageConnections,
  onRemove,
  surface,
}: {
  app: WorkspacePipedreamAppRow
  pendingAppAction: PendingAppAction
  disabled: boolean
  onManageConnections: () => void
  onRemove: () => void
  surface: SettingsSurfaceVariant
}) {
  const isPendingRemove = pendingAppAction?.slug === app.slug && pendingAppAction.kind === 'remove'
  const removeDisabled = disabled || app.source !== 'added'
  const removeTitle = app.source === 'built_in'
    ? 'Built-in apps cannot be removed'
    : app.source === 'available'
      ? 'Add this app before removing it'
      : 'Remove app'
  const manageClassName = surface === 'embedded'
    ? 'border border-sky-300/25 bg-sky-900/55 text-sky-50 hover:border-sky-200/40 hover:bg-sky-900/75'
    : 'bg-blue-600 text-white hover:bg-blue-700'

  return (
    <div className="grid gap-3 px-4 py-3 md:grid-cols-[minmax(0,1fr)_12rem_8rem] md:items-center">
      <PipedreamAppSummaryCell app={app} surface={surface} />
      <div className="flex justify-start md:justify-end">
        <button
          type="button"
          className={`inline-flex min-w-28 items-center justify-center gap-2 rounded-md px-3 py-2 text-sm font-semibold transition disabled:opacity-60 ${manageClassName}`}
          onClick={onManageConnections}
          disabled={disabled}
        >
          <Users className="h-4 w-4" aria-hidden="true" />
          Manage
        </button>
      </div>
      <div className="flex justify-start md:justify-end">
        <PipedreamRemoveButton
          isPending={isPendingRemove}
          disabled={removeDisabled}
          title={removeTitle}
          onClick={onRemove}
          surface={surface}
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
  surface,
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
  surface: SettingsSurfaceVariant
}) {
  const titleClassName = surface === 'embedded' ? 'text-slate-100' : 'text-slate-900'
  const descriptionClassName = surface === 'embedded' ? 'text-slate-400' : 'text-slate-600'
  return (
    <div className="space-y-4 p-1">
      <BackButton onClick={onBack} disabled={isBusy} surface={surface} />

      <div className="flex items-center gap-3">
        <PipedreamAppIcon app={app} surface={surface} />
        <div className="min-w-0">
          <p className={`truncate text-sm font-semibold ${titleClassName}`}>{app.name}</p>
          <p className={`text-sm ${descriptionClassName}`}>{isFetching ? 'Refreshing connections…' : 'Connected agents are shown first.'}</p>
        </div>
      </div>

      <PipedreamStatusBanner statusMessage={statusMessage} surface={surface} />

      {isError ? (
        <PipedreamErrorState error={error} fallback="Unable to load agent connections." surface={surface} />
      ) : isLoading ? (
        <PipedreamLoadingState label="Loading agents…" surface={surface} />
      ) : agents.length === 0 ? (
        <PipedreamEmptyState label="No agents found." surface={surface} />
      ) : (
        <PipedreamListFrame isMobile={false} constrainHeight={false} surface={surface}>
          {agents.map((agent) => (
            <AgentConnectionRow
              key={agent.agentId}
              agent={agent}
              pendingAgentAction={pendingAgentAction}
              disabled={isBusy}
              onConnect={() => onConnect(agent)}
              onDisconnect={() => onDisconnect(agent)}
              surface={surface}
            />
          ))}
        </PipedreamListFrame>
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
  surface,
}: {
  agent: PipedreamAppAgentConnection
  pendingAgentAction: PendingAgentAction
  disabled: boolean
  onConnect: () => void
  onDisconnect: () => void
  surface: SettingsSurfaceVariant
}) {
  const isPending = pendingAgentAction?.agentId === agent.agentId
  const pendingKind = isPending ? pendingAgentAction?.kind : null

  return (
    <div className="grid gap-3 px-4 py-3 md:grid-cols-[minmax(0,1fr)_8rem] md:items-center">
      <div className="flex min-w-0 items-center gap-3">
        <AgentConnectionAvatar agent={agent} surface={surface} />
        <div className="min-w-0">
          <p className={`truncate text-sm font-semibold ${surface === 'embedded' ? 'text-slate-100' : 'text-slate-900'}`}>{agent.name}</p>
        </div>
      </div>
      <div className="flex justify-start md:justify-end">
        <PipedreamConnectionButton
          connected={agent.connected}
          pendingKind={pendingKind}
          disabled={disabled}
          onConnect={onConnect}
          onDisconnect={onDisconnect}
          surface={surface}
        />
      </div>
    </div>
  )
}
