import { useCallback, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, Loader2, Plug, Settings } from 'lucide-react'

import { agentDiscordAppQueryKey, fetchAgentDiscordApp, type AgentDiscordApp } from '../../api/discordNative'
import { disconnectAgentPipedreamApp, fetchAgentPipedreamApps, removeAgentPipedreamApp, startAgentPipedreamAppConnect, type AgentPipedreamAppRow } from '../../api/mcp'
import { fetchNativeIntegrations, type NativeIntegrationProvider } from '../../api/nativeIntegrations'
import { fetchAgentEmailSettings } from '../../api/agentEmailSettings'
import { AgentEmailSettingsScreen } from '../../screens/AgentEmailSettingsScreen'
import {
  PipedreamAppSummaryCell,
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
import { IntegrationConnectionButton } from './IntegrationActionButtons'
import {
  confirmNativeIntegrationDisconnect,
  NativeIntegrationGridRow,
  NativeIntegrationStatusBadge,
  NativeIntegrationSummaryCell,
  openNativeOAuthPopup,
  usesManualNativeIntegrationCredentials,
  useNativeIntegrationConnectMutation,
  useNativeIntegrationDisconnectMutation,
  useNativeIntegrationPickerMutation,
  useNativeIntegrationRefreshEffects,
} from './NativeIntegrationShared'
import { useManualNativeIntegrationConnect } from './useManualNativeIntegrationConnect'
import { DiscordConfigurationScreen, DiscordSummaryCell, useDiscordNativeAgentActions, useDiscordOAuthCompleteRefetch } from './DiscordNativeAppModal'

type AgentPipedreamAppsModalProps = {
  agentId: string
  enablePipedreamApps?: boolean
  nativeIntegrationsUrl?: string | null
  onClose: () => void
}

type AgentAppRow =
  | (AgentPipedreamAppRow & { kind: 'pipedream' })
  | (NativeIntegrationProvider & { kind: 'native' })
  | (AgentDiscordApp & { kind: 'discord' })

type PendingAction = {
  slug: string
  kind: 'connect' | 'disconnect' | 'remove'
} | null

type PendingNativeAction = {
  providerKey: string
  kind: 'connect' | 'disconnect' | 'picker'
} | null

export function AgentPipedreamAppsModal({
  agentId,
  enablePipedreamApps = true,
  nativeIntegrationsUrl = null,
  onClose,
}: AgentPipedreamAppsModalProps) {
  const queryClient = useQueryClient()
  const isMobile = useIsMobile()
  const [searchTerm, setSearchTerm] = useState('')
  const debouncedSearchTerm = useDebouncedValue(searchTerm)
  const [pendingAction, setPendingAction] = useState<PendingAction>(null)
  const [pendingNativeAction, setPendingNativeAction] = useState<PendingNativeAction>(null)
  const [discordConfigureOpen, setDiscordConfigureOpen] = useState(false)
  const [emailConfigureOpen, setEmailConfigureOpen] = useState(false)
  const [statusMessage, setStatusMessage] = useState<PipedreamStatusMessage>(null)
  const setNativeStatusMessage = useCallback((message: string | null) => {
    setStatusMessage(message ? { text: message, tone: 'error' } : null)
  }, [])
  const nativeQueryKey = useMemo(
    () => ['native-integrations', nativeIntegrationsUrl] as const,
    [nativeIntegrationsUrl],
  )
  const discordQueryKey = useMemo(() => agentDiscordAppQueryKey(agentId), [agentId])
  useNativeIntegrationRefreshEffects({ queryKey: nativeQueryKey, onError: (message) => setStatusMessage({ text: message, tone: 'error' }) })
  const handleDiscordError = useCallback((message: string) => {
    setStatusMessage({ text: message, tone: 'error' })
  }, [])
  useDiscordOAuthCompleteRefetch({ agentId, onError: handleDiscordError })
  const {
    connectDiscordAgent,
    saveDiscordAgentSubscriptions,
    pendingDiscordAgentAction,
    isDiscordAgentActionPending,
  } = useDiscordNativeAgentActions({
    onStart: () => setStatusMessage(null),
    onError: handleDiscordError,
  })

  const appsQuery = useQuery({
    queryKey: ['agent-pipedream-apps', agentId, debouncedSearchTerm],
    queryFn: () => fetchAgentPipedreamApps(agentId, debouncedSearchTerm),
    enabled: enablePipedreamApps,
  })
  useWindowFocusRefetch(appsQuery.refetch, enablePipedreamApps)
  const nativeIntegrationsQuery = useQuery({
    queryKey: nativeQueryKey,
    queryFn: () => fetchNativeIntegrations(nativeIntegrationsUrl as string),
    enabled: Boolean(nativeIntegrationsUrl),
  })
  const emailSettingsQuery = useQuery({
    queryKey: ['agent-email-settings', agentId],
    queryFn: () => fetchAgentEmailSettings(`/console/api/agents/${agentId}/email-settings/`),
  })
  const discordAppQuery = useQuery({
    queryKey: discordQueryKey,
    queryFn: () => fetchAgentDiscordApp(agentId),
  })

  const connectMutation = useMutation({
    mutationFn: (app: AgentPipedreamAppRow) => startAgentPipedreamAppConnect(agentId, app.slug),
    onMutate: (app) => {
      setPendingAction({ slug: app.slug, kind: 'connect' })
      setStatusMessage(null)
    },
    onSuccess: (result) => {
      window.open(result.connectUrl, '_blank', 'noopener,noreferrer')
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
    onSuccess: () => {
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
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['pipedream-app-settings'], exact: false })
      void appsQuery.refetch()
    },
    onError: (error) => {
      setStatusMessage({ text: resolvePipedreamAppsErrorMessage(error, 'Unable to remove app.'), tone: 'error' })
    },
    onSettled: () => setPendingAction(null),
  })

  const nativeConnectMutation = useNativeIntegrationConnectMutation({
    setPendingAction: setPendingNativeAction,
    setStatusMessage: setNativeStatusMessage,
    agentId,
  })

  const {
    credentialModal,
    isPending: manualNativeConnectPending,
    openCredentialModal,
  } = useManualNativeIntegrationConnect({
    nativeQueryKey,
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
    onError: (message) => setStatusMessage({ text: message, tone: 'error' }),
    onSettled: () => setPendingNativeAction(null),
  })

  const nativeDisconnectMutation = useNativeIntegrationDisconnectMutation({
    nativeQueryKey,
    setPendingAction: setPendingNativeAction,
    setStatusMessage: setNativeStatusMessage,
    agentId,
  })

  const nativePickerMutation = useNativeIntegrationPickerMutation({
    setPendingAction: setPendingNativeAction,
    setStatusMessage: setNativeStatusMessage,
  })

  const normalizedSearch = debouncedSearchTerm.toLowerCase()
  const connectedEmailProvider = emailSettingsQuery.data?.activeMode === 'oauth'
    ? emailSettingsQuery.data.oauth.provider
    : ''
  const nativeRows = (nativeIntegrationsQuery.data?.providers ?? [])
    .filter((provider) => emailSettingsQuery.data?.activeMode !== 'custom'
      || !['gmail', 'outlook'].includes(provider.providerKey))
    .filter((provider) => !connectedEmailProvider
      || !['gmail', 'outlook'].includes(provider.providerKey)
      || provider.providerKey === connectedEmailProvider)
    .map((provider) => ['gmail', 'outlook'].includes(provider.providerKey)
      ? { ...provider, connected: provider.providerKey === connectedEmailProvider }
      : provider)
    .filter((provider) => !normalizedSearch || [
      provider.providerKey,
      provider.displayName,
      provider.description,
    ].some((value) => value.toLowerCase().includes(normalizedSearch)))
    .map((provider) => ({ ...provider, kind: 'native' as const }))
  const discordRow = discordAppQuery.data
    && (!normalizedSearch || [
      discordAppQuery.data.providerKey,
      discordAppQuery.data.displayName,
      discordAppQuery.data.description,
    ].some((value) => value.toLowerCase().includes(normalizedSearch)))
    ? { ...discordAppQuery.data, kind: 'discord' as const }
    : null
  const apps: AgentAppRow[] = [
    ...nativeRows,
    ...(discordRow ? [discordRow] : []),
    ...(enablePipedreamApps ? (appsQuery.data?.apps ?? []).map((app) => ({ ...app, kind: 'pipedream' as const })) : []),
  ]
  const isBusy = connectMutation.isPending
    || disconnectMutation.isPending
    || removeMutation.isPending
    || nativeConnectMutation.isPending
    || manualNativeConnectPending
    || nativeDisconnectMutation.isPending
    || nativePickerMutation.isPending
    || isDiscordAgentActionPending
  const activeDiscordApp = discordConfigureOpen ? (discordAppQuery.data ?? discordRow) : null
  const pendingDiscordAction = pendingDiscordAgentAction?.agentId === agentId ? pendingDiscordAgentAction.kind : null

  const body = emailConfigureOpen ? (
    <AgentEmailSettingsScreen
      agentId={agentId}
      emailSettingsUrl={`/console/api/agents/${agentId}/email-settings/`}
      ensureAccountUrl={`/console/api/agents/${agentId}/email-settings/ensure-account/`}
      testUrl={`/console/api/agents/${agentId}/email-settings/test/`}
      onBack={() => setEmailConfigureOpen(false)}
      onSaved={() => {
        void emailSettingsQuery.refetch()
        void nativeIntegrationsQuery.refetch()
      }}
    />
  ) : activeDiscordApp ? (
    <DiscordConfigurationScreen
      agentId={agentId}
      app={activeDiscordApp}
      disabled={isBusy}
      pendingDiscordAction={pendingDiscordAction}
      statusMessage={statusMessage}
      onBack={() => {
        setDiscordConfigureOpen(false)
        setStatusMessage(null)
      }}
      onSave={(subscriptions) => saveDiscordAgentSubscriptions(agentId, subscriptions)}
    />
  ) : (
      <div className="space-y-4 p-1">
        <PipedreamStatusBanner statusMessage={statusMessage} />
        <PipedreamSearchInput
          value={searchTerm}
          onChange={setSearchTerm}
          isFetching={appsQuery.isFetching || nativeIntegrationsQuery.isFetching || discordAppQuery.isFetching || emailSettingsQuery.isFetching}
          disabled={isBusy}
        />

        {(enablePipedreamApps && appsQuery.isError) || nativeIntegrationsQuery.isError || discordAppQuery.isError || emailSettingsQuery.isError ? (
          <PipedreamErrorState error={appsQuery.error ?? nativeIntegrationsQuery.error ?? discordAppQuery.error ?? emailSettingsQuery.error} fallback="Unable to load apps." />
        ) : (enablePipedreamApps && appsQuery.isLoading) || nativeIntegrationsQuery.isLoading || discordAppQuery.isLoading || emailSettingsQuery.isLoading ? (
          <PipedreamLoadingState label="Loading apps…" />
        ) : apps.length === 0 ? (
          <PipedreamEmptyState label="No apps matched your search." />
        ) : (
          <PipedreamListFrame isMobile={isMobile}>
            {apps.map((app) => app.kind === 'native' ? (
              ['gmail', 'outlook'].includes(app.providerKey) ? <AgentEmailNativeAppRowItem
                key={`native-${app.providerKey}`}
                provider={app}
                pendingNativeAction={pendingNativeAction}
                disabled={isBusy}
                onConnect={() => nativeConnectMutation.mutate({ provider: app, popup: openNativeOAuthPopup(app) })}
                onConfigure={() => setEmailConfigureOpen(true)}
              /> : <AgentNativeAppRowItem
                key={`native-${app.providerKey}`}
                provider={app}
                pendingNativeAction={pendingNativeAction}
                disabled={isBusy}
                onConnect={() => {
                  if (usesManualNativeIntegrationCredentials(app)) {
                    openCredentialModal(app)
                    return
                  }
                  nativeConnectMutation.mutate({ provider: app, popup: openNativeOAuthPopup(app) })
                }}
                onDisconnect={() => {
                  if (confirmNativeIntegrationDisconnect(app)) {
                    nativeDisconnectMutation.mutate(app)
                  }
                }}
                onPicker={() => nativePickerMutation.mutate(app)}
              />
            ) : app.kind === 'discord' ? (
              <AgentDiscordAppRowItem
                key="native-discord"
                app={app}
                pendingDiscordAction={pendingDiscordAction}
                disabled={isBusy}
                onConnect={() => connectDiscordAgent(agentId)}
                onConfigure={() => {
                  setDiscordConfigureOpen(true)
                  setStatusMessage(null)
                }}
              />
            ) : (
              <AgentPipedreamAppRowItem
                key={`pipedream-${app.slug}`}
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
  )

  return (
    <>
      <PipedreamModalShell
        title={emailConfigureOpen ? 'Configure Email' : activeDiscordApp ? 'Configure Discord' : 'Apps'}
        subtitle={emailConfigureOpen ? 'Choose how this agent sends and receives email.' : activeDiscordApp ? 'Choose the Discord server channels this agent should watch.' : 'Search, connect, and disconnect apps for this agent.'}
        ariaLabel="Manage agent apps"
        onClose={onClose}
      >
        {body}
      </PipedreamModalShell>
      {credentialModal}
    </>
  )
}

function AgentNativeAppRowItem({
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

  return (
    <NativeIntegrationGridRow
      provider={provider}
      pendingKind={pendingKind}
      disabled={disabled}
      onConnect={onConnect}
      onDisconnect={onDisconnect}
      onPicker={onPicker}
    />
  )
}

function AgentEmailNativeAppRowItem({
  provider,
  pendingNativeAction,
  disabled,
  onConnect,
  onConfigure,
}: {
  provider: NativeIntegrationProvider
  pendingNativeAction: PendingNativeAction
  disabled: boolean
  onConnect: () => void
  onConfigure: () => void
}) {
  const pendingKind = pendingNativeAction?.providerKey === provider.providerKey
    ? pendingNativeAction.kind
    : null
  return (
    <div className="px-4 py-3">
      <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_7rem_8rem] sm:items-start">
        <NativeIntegrationSummaryCell provider={provider} badge="native" />
        <NativeIntegrationStatusBadge connected={provider.connected} />
        <div className="flex justify-start sm:justify-end">
          {provider.connected ? (
            <button type="button" className="inline-flex min-w-28 items-center justify-center gap-2 rounded-md border border-blue-200 bg-white px-3 py-2 text-sm font-semibold text-blue-700 hover:bg-blue-50 disabled:opacity-60" disabled={disabled} onClick={onConfigure}><Settings className="h-4 w-4" />Configure</button>
          ) : (
            <IntegrationConnectionButton connected={false} pendingKind={pendingKind} disabled={disabled} onConnect={onConnect} onDisconnect={onConfigure} />
          )}
        </div>
      </div>
    </div>
  )
}

function AgentDiscordAppRowItem({
  app,
  pendingDiscordAction,
  disabled,
  onConnect,
  onConfigure,
}: {
  app: AgentDiscordApp
  pendingDiscordAction: 'connect' | 'save' | null
  disabled: boolean
  onConnect: () => void
  onConfigure: () => void
}) {
  const isPendingConnect = pendingDiscordAction === 'connect'

  return (
    <div className="px-4 py-3">
      <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_7rem_8rem] md:items-start">
        <DiscordSummaryCell app={app} />
        <div>
          {app.subscribed ? (
            <span className="inline-flex items-center gap-1.5 rounded-full border border-emerald-200 bg-emerald-50 px-2.5 py-1 text-xs font-semibold text-emerald-700">
              <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" />
              Subscribed
            </span>
          ) : app.connected ? (
            <span className="inline-flex items-center gap-1.5 rounded-full border border-blue-200 bg-blue-50 px-2.5 py-1 text-xs font-semibold text-blue-700">
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
          {app.connected ? (
            <button
              type="button"
              className="inline-flex min-w-28 items-center justify-center gap-2 rounded-md border border-blue-200 bg-white px-3 py-2 text-sm font-semibold text-blue-700 transition hover:bg-blue-50 disabled:opacity-60"
              onClick={onConfigure}
              disabled={disabled}
            >
              <Settings className="h-4 w-4" aria-hidden="true" />
              Configure
            </button>
          ) : (
            <button
              type="button"
              className="inline-flex min-w-28 items-center justify-center gap-2 rounded-md bg-blue-600 px-3 py-2 text-sm font-semibold text-white transition hover:bg-blue-700 disabled:opacity-60"
              onClick={onConnect}
              disabled={disabled}
            >
              {isPendingConnect ? (
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              ) : (
                <Plug className="h-4 w-4" aria-hidden="true" />
              )}
              Connect
            </button>
          )}
        </div>
      </div>
    </div>
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
        <IntegrationConnectionButton
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
