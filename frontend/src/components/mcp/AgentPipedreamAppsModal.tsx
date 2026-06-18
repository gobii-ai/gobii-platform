import { useCallback, useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Check, Copy, ExternalLink, FolderOpen, Plug, RefreshCw, Settings, Unplug } from 'lucide-react'

import {
  agentDiscordAppQueryKey,
  fetchAgentDiscordApp,
  type AgentDiscordApp,
} from '../../api/discordNative'
import {
  agentTelegramAppQueryKey,
  disconnectAgentTelegram,
  fetchAgentTelegramApp,
  startAgentTelegramConnect,
  syncAgentTelegramProfile,
  type AgentTelegramApp,
} from '../../api/telegramNative'
import {
  disconnectAgentPipedreamApp,
  fetchAgentPipedreamApps,
  removeAgentPipedreamApp,
  startAgentPipedreamAppConnect,
  type AgentPipedreamAppRow,
} from '../../api/mcp'
import {
  fetchNativeIntegrationPickerToken,
  fetchNativeIntegrations,
  revokeNativeIntegration,
  startNativeIntegrationConnect,
  type NativeIntegrationProvider,
} from '../../api/nativeIntegrations'
import { safeErrorMessage } from '../../api/safeErrorMessage'
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
import {
  confirmNativeIntegrationDisconnect,
  NativeIntegrationActionButton,
  NativeConnectionStatusPill,
  NativeIntegrationFilesDisclosure,
  NativeIntegrationRow,
  NativeIntegrationSummaryCell,
  NativeProviderIconTile,
  nativeIntegrationFilesQueryKey,
  nativeOAuthContextPayload,
  openGoogleDrivePicker,
  openNativeOAuthPopup,
  storePendingNativeOAuth,
  supportsNativeIntegrationPicker,
  useNativeIntegrationRefreshEffects,
} from './NativeIntegrationShared'
import {
  DiscordConfigurationScreen,
  DiscordSummaryCell,
  useDiscordNativeAgentActions,
  useDiscordOAuthCompleteRefetch,
} from './DiscordNativeAppModal'
import { openTelegramHandoff, telegramAppUrlForWebUrl } from './TelegramNativeShared'

type AgentPipedreamAppsModalProps = {
  agentId: string
  enablePipedreamApps?: boolean
  nativeIntegrationsUrl?: string | null
  initialTarget?: AgentPipedreamAppsInitialTarget | null
  onClose: () => void
}

export type AgentPipedreamAppsInitialTarget = {
  kind: 'native'
  providerKey: string
}

type AgentAppRow =
  | (AgentPipedreamAppRow & { kind: 'pipedream' })
  | (NativeIntegrationProvider & { kind: 'native' })
  | (AgentDiscordApp & { kind: 'discord' })
  | (AgentTelegramApp & { kind: 'telegram' })

type PendingAction = {
  slug: string
  kind: 'connect' | 'disconnect' | 'remove'
} | null

export type PendingNativeAction = {
  providerKey: string
  kind: 'connect' | 'disconnect' | 'picker' | 'sync'
} | null

export function AgentPipedreamAppsModal({
  agentId,
  enablePipedreamApps = true,
  nativeIntegrationsUrl = null,
  initialTarget = null,
  onClose,
}: AgentPipedreamAppsModalProps) {
  const queryClient = useQueryClient()
  const isMobile = useIsMobile()
  const [searchTerm, setSearchTerm] = useState('')
  const debouncedSearchTerm = useDebouncedValue(searchTerm)
  const [pendingAction, setPendingAction] = useState<PendingAction>(null)
  const [pendingNativeAction, setPendingNativeAction] = useState<PendingNativeAction>(null)
  const [discordConfigureOpen, setDiscordConfigureOpen] = useState(
    initialTarget?.kind === 'native' && initialTarget.providerKey === 'discord',
  )
  const [telegramConfigureOpen, setTelegramConfigureOpen] = useState(
    initialTarget?.kind === 'native' && initialTarget.providerKey === 'telegram',
  )
  const [telegramProvisioningPending, setTelegramProvisioningPending] = useState(false)
  const [statusMessage, setStatusMessage] = useState<PipedreamStatusMessage>(null)
  const nativeQueryKey = useMemo(
    () => ['native-integrations', nativeIntegrationsUrl] as const,
    [nativeIntegrationsUrl],
  )
  const discordQueryKey = useMemo(() => agentDiscordAppQueryKey(agentId), [agentId])
  const telegramQueryKey = useMemo(() => agentTelegramAppQueryKey(agentId), [agentId])
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
  const discordAppQuery = useQuery({
    queryKey: discordQueryKey,
    queryFn: () => fetchAgentDiscordApp(agentId),
  })
  const telegramAppQuery = useQuery({
    queryKey: telegramQueryKey,
    queryFn: () => fetchAgentTelegramApp(agentId),
    refetchInterval: telegramProvisioningPending ? 2000 : false,
  })

  useEffect(() => {
    if (!telegramProvisioningPending || !telegramAppQuery.data?.connected) {
      return
    }
    setTelegramProvisioningPending(false)
    setStatusMessage(null)
  }, [telegramAppQuery.data?.connected, telegramProvisioningPending])

  const telegramConnectMutation = useMutation({
    mutationFn: () => startAgentTelegramConnect(agentId),
    onMutate: () => {
      setPendingNativeAction({ providerKey: 'telegram', kind: 'connect' })
      setStatusMessage(null)
    },
    onSuccess: (payload) => {
      void queryClient.setQueryData(telegramQueryKey, payload.app)
      const url = payload.userLinked ? payload.createBotUrl : payload.managerLinkUrl
      if (url) {
        openTelegramHandoff(url)
      }
      setTelegramProvisioningPending(payload.userLinked)
      setStatusMessage({
        text: payload.userLinked
          ? 'Waiting for Telegram to finish creating this agent bot. Gobii will update this screen automatically.'
          : 'Telegram account linking opened. After linking, click Connect again to create the agent bot.',
        tone: 'info',
      })
    },
    onError: (error) => {
      setStatusMessage({ text: safeErrorMessage(error), tone: 'error' })
    },
    onSettled: () => setPendingNativeAction(null),
  })

  const telegramSyncMutation = useMutation({
    mutationFn: () => syncAgentTelegramProfile(agentId),
    onMutate: () => {
      setPendingNativeAction({ providerKey: 'telegram', kind: 'sync' })
      setStatusMessage(null)
    },
    onSuccess: (app) => {
      void queryClient.setQueryData(telegramQueryKey, app)
    },
    onError: (error) => {
      setStatusMessage({ text: safeErrorMessage(error), tone: 'error' })
    },
    onSettled: () => setPendingNativeAction(null),
  })

  const telegramDisconnectMutation = useMutation({
    mutationFn: () => disconnectAgentTelegram(agentId),
    onMutate: () => {
      setPendingNativeAction({ providerKey: 'telegram', kind: 'disconnect' })
      setStatusMessage(null)
    },
    onSuccess: (app) => {
      void queryClient.setQueryData(telegramQueryKey, app)
    },
    onError: (error) => {
      setStatusMessage({ text: safeErrorMessage(error), tone: 'error' })
    },
    onSettled: () => setPendingNativeAction(null),
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
        setStatusMessage({ text: 'Connection window was closed before Google opened.', tone: 'error' })
        return
      }
      window.location.href = payload.authorizationUrl
    },
    onError: (error, { popup }) => {
      if (popup && !popup.closed) {
        popup.close()
      }
      setStatusMessage({ text: safeErrorMessage(error), tone: 'error' })
    },
    onSettled: () => setPendingNativeAction(null),
  })

  const nativeDisconnectMutation = useMutation({
    mutationFn: (provider: NativeIntegrationProvider) => revokeNativeIntegration(provider.revokeUrl).then(() => provider),
    onMutate: (provider) => {
      setPendingNativeAction({ providerKey: provider.providerKey, kind: 'disconnect' })
      setStatusMessage(null)
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: nativeQueryKey })
    },
    onError: (error) => {
      setStatusMessage({ text: safeErrorMessage(error), tone: 'error' })
    },
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
      setStatusMessage({ text: safeErrorMessage(error), tone: 'error' })
    },
    onSettled: () => setPendingNativeAction(null),
  })

  const normalizedSearch = debouncedSearchTerm.toLowerCase()
  const nativeRows = (nativeIntegrationsQuery.data?.providers ?? [])
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
  const telegramRow = telegramAppQuery.data
    && (!normalizedSearch || [
      telegramAppQuery.data.providerKey,
      telegramAppQuery.data.displayName,
      telegramAppQuery.data.description,
      telegramAppQuery.data.botUsername,
    ].some((value) => value.toLowerCase().includes(normalizedSearch)))
    ? { ...telegramAppQuery.data, kind: 'telegram' as const }
    : null
  const apps: AgentAppRow[] = [
    ...nativeRows,
    ...(discordRow ? [discordRow] : []),
    ...(telegramRow ? [telegramRow] : []),
    ...(enablePipedreamApps ? (appsQuery.data?.apps ?? []).map((app) => ({ ...app, kind: 'pipedream' as const })) : []),
  ]
  const isBusy = connectMutation.isPending
    || disconnectMutation.isPending
    || removeMutation.isPending
    || nativeConnectMutation.isPending
    || nativeDisconnectMutation.isPending
    || nativePickerMutation.isPending
    || isDiscordAgentActionPending
    || telegramConnectMutation.isPending
    || telegramSyncMutation.isPending
    || telegramDisconnectMutation.isPending
  const activeDiscordApp = discordConfigureOpen ? (discordAppQuery.data ?? discordRow) : null
  const activeTelegramApp = telegramConfigureOpen ? (telegramAppQuery.data ?? telegramRow) : null
  const pendingDiscordAction = pendingDiscordAgentAction?.agentId === agentId ? pendingDiscordAgentAction.kind : null

  const body = discordConfigureOpen ? (
    discordAppQuery.isError ? (
      <ConfigureLoadState
        disabled={isBusy}
        error={discordAppQuery.error}
        errorFallback="Unable to load Discord configuration."
        loadingLabel="Loading Discord configuration..."
        onBack={() => {
          setDiscordConfigureOpen(false)
          setStatusMessage(null)
        }}
      />
    ) : !activeDiscordApp ? (
      <ConfigureLoadState
        disabled={isBusy}
        loadingLabel="Loading Discord configuration..."
        onBack={() => {
          setDiscordConfigureOpen(false)
          setStatusMessage(null)
        }}
      />
    ) : (
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
    )
  ) : telegramConfigureOpen ? (
    telegramAppQuery.isError ? (
      <ConfigureLoadState
        disabled={isBusy}
        error={telegramAppQuery.error}
        errorFallback="Unable to load Telegram configuration."
        loadingLabel="Loading Telegram configuration..."
        onBack={() => {
          setTelegramConfigureOpen(false)
          setStatusMessage(null)
        }}
      />
    ) : !activeTelegramApp ? (
      <ConfigureLoadState
        disabled={isBusy}
        loadingLabel="Loading Telegram configuration..."
        onBack={() => {
          setTelegramConfigureOpen(false)
          setStatusMessage(null)
        }}
      />
    ) : (
      <TelegramConfigurationScreen
        app={activeTelegramApp}
        disabled={isBusy}
        pendingNativeAction={pendingNativeAction}
        statusMessage={statusMessage}
        onBack={() => {
          setTelegramConfigureOpen(false)
          setStatusMessage(null)
        }}
        onConnect={() => telegramConnectMutation.mutate()}
        onSync={() => telegramSyncMutation.mutate()}
        onDisconnect={() => {
          if (window.confirm('Disconnect Telegram for this agent? The agent bot will stop receiving Telegram messages.')) {
            telegramDisconnectMutation.mutate()
          }
        }}
      />
    )
  ) : (
      <div className="space-y-4 p-1">
        <PipedreamStatusBanner statusMessage={statusMessage} />
        <PipedreamSearchInput
          value={searchTerm}
          onChange={setSearchTerm}
          isFetching={appsQuery.isFetching || nativeIntegrationsQuery.isFetching || discordAppQuery.isFetching || telegramAppQuery.isFetching}
          disabled={isBusy}
        />

        {(enablePipedreamApps && appsQuery.isError) || nativeIntegrationsQuery.isError || discordAppQuery.isError || telegramAppQuery.isError ? (
          <PipedreamErrorState error={appsQuery.error ?? nativeIntegrationsQuery.error ?? discordAppQuery.error ?? telegramAppQuery.error} fallback="Unable to load apps." />
        ) : (enablePipedreamApps && appsQuery.isLoading) || nativeIntegrationsQuery.isLoading || discordAppQuery.isLoading || telegramAppQuery.isLoading ? (
          <PipedreamLoadingState label="Loading apps…" />
        ) : apps.length === 0 ? (
          <PipedreamEmptyState label="No apps matched your search." />
        ) : (
          <PipedreamListFrame isMobile={isMobile}>
            {apps.map((app) => app.kind === 'native' ? (
              <AgentNativeAppRowItem
                key={`native-${app.providerKey}`}
                provider={app}
                pendingNativeAction={pendingNativeAction}
                disabled={isBusy}
                onConnect={() => nativeConnectMutation.mutate({ provider: app, popup: openNativeOAuthPopup(app) })}
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
            ) : app.kind === 'telegram' ? (
              <AgentTelegramAppListRowItem
                key="native-telegram"
                app={app}
                disabled={isBusy}
                onConfigure={() => {
                  setTelegramConfigureOpen(true)
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
    <PipedreamModalShell
      title={activeDiscordApp ? 'Configure Discord' : activeTelegramApp ? 'Configure Telegram' : 'Apps'}
      subtitle={
        activeDiscordApp
          ? 'Choose the Discord server channels this agent should watch.'
          : activeTelegramApp
            ? 'Create and manage this agent Telegram bot.'
            : 'Search, connect, and disconnect apps for this agent.'
      }
      ariaLabel="Manage agent apps"
      onClose={onClose}
    >
      {body}
    </PipedreamModalShell>
  )
}

function ConfigureLoadState({
  disabled,
  error = null,
  errorFallback,
  loadingLabel,
  onBack,
}: {
  disabled: boolean
  error?: unknown
  errorFallback?: string
  loadingLabel: string
  onBack: () => void
}) {
  return (
    <div className="space-y-4 p-1">
      <button
        type="button"
        className="inline-flex items-center gap-2 rounded-md border border-slate-200 bg-white px-3 py-2 text-sm font-semibold text-slate-700 transition hover:bg-slate-50 disabled:opacity-60"
        onClick={onBack}
        disabled={disabled}
      >
        <ArrowLeft className="h-4 w-4" aria-hidden="true" />
        Back
      </button>
      {error ? (
        <PipedreamErrorState error={error} fallback={errorFallback ?? 'Unable to load configuration.'} />
      ) : (
        <PipedreamLoadingState label={loadingLabel} />
      )}
    </div>
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
  const pickerEnabled = provider.connected && supportsNativeIntegrationPicker(provider)

  return (
    <NativeIntegrationRow
      summary={<NativeIntegrationSummaryCell provider={provider} />}
      status={<NativeConnectionStatusPill connected={provider.connected} disconnectedLabel="Workspace" />}
      gridClassName="grid gap-3 md:grid-cols-[minmax(0,1fr)_8rem_12rem_8rem] md:items-start"
      actions={[
        pickerEnabled ? (
          <NativeIntegrationActionButton
            label="Select Files"
            icon={FolderOpen}
            pending={pendingKind === 'picker'}
            disabled={disabled}
            onClick={onPicker}
          />
        ) : null,
        provider.connected ? (
          <NativeIntegrationActionButton
            label="Disconnect"
            icon={Unplug}
            pending={pendingKind === 'disconnect'}
            disabled={disabled}
            tone="danger"
            onClick={onDisconnect}
          />
        ) : (
          <NativeIntegrationActionButton
            label="Connect"
            icon={Plug}
            pending={pendingKind === 'connect'}
            disabled={disabled}
            tone="primary"
            onClick={onConnect}
          />
        ),
      ]}
    >
      <NativeIntegrationFilesDisclosure provider={provider} />
    </NativeIntegrationRow>
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
    <NativeIntegrationRow
      summary={<DiscordSummaryCell app={app} />}
      status={<NativeConnectionStatusPill connected={app.connected} />}
      actions={[
        null,
        app.connected ? (
          <NativeIntegrationActionButton label="Configure" icon={Settings} disabled={disabled} onClick={onConfigure} />
        ) : (
          <NativeIntegrationActionButton
            label="Connect"
            icon={Plug}
            pending={isPendingConnect}
            disabled={disabled}
            tone="primary"
            onClick={onConnect}
          />
        ),
      ]}
    />
  )
}

function AgentTelegramAppListRowItem({
  app,
  disabled,
  onConfigure,
}: {
  app: AgentTelegramApp
  disabled: boolean
  onConfigure: () => void
}) {
  const statusLabel = app.status === 'pending'
      ? 'Pending'
      : app.status === 'configuration_error'
        ? 'Setup error'
        : 'Not connected'

  return (
    <NativeIntegrationRow
      summary={<TelegramSummaryCell app={app} />}
      status={(
        <NativeConnectionStatusPill
          connected={app.connected}
          disconnectedLabel={statusLabel}
          error={app.status === 'configuration_error'}
        />
      )}
      actions={[
        null,
        <NativeIntegrationActionButton label="Configure" icon={Settings} disabled={disabled} onClick={onConfigure} />,
      ]}
    />
  )
}

export function TelegramConfigurationScreen({
  app,
  disabled,
  pendingNativeAction,
  statusMessage,
  onBack,
  onConnect,
  onSync,
  onDisconnect,
}: {
  app: AgentTelegramApp
  disabled: boolean
  pendingNativeAction: PendingNativeAction
  statusMessage: PipedreamStatusMessage
  onBack: () => void
  onConnect: () => void
  onSync: () => void
  onDisconnect: () => void
}) {
  return (
    <div className="space-y-4 p-1">
      <button
        type="button"
        className="inline-flex items-center gap-2 rounded-md border border-slate-200 bg-white px-3 py-2 text-sm font-semibold text-slate-700 transition hover:bg-slate-50 disabled:opacity-60"
        onClick={onBack}
        disabled={disabled}
      >
        <ArrowLeft className="h-4 w-4" aria-hidden="true" />
        Back
      </button>
      <PipedreamStatusBanner statusMessage={statusMessage} />
      <div className="overflow-hidden rounded-lg border border-slate-200 bg-white">
        <AgentTelegramAppRowItem
          app={app}
          pendingNativeAction={pendingNativeAction}
          disabled={disabled}
          onConnect={onConnect}
          onSync={onSync}
          onDisconnect={onDisconnect}
        />
      </div>
    </div>
  )
}

export function AgentTelegramAppRowItem({
  app,
  pendingNativeAction,
  disabled,
  onConnect,
  onSync,
  onDisconnect,
}: {
  app: AgentTelegramApp
  pendingNativeAction: PendingNativeAction
  disabled: boolean
  onConnect: () => void
  onSync: () => void
  onDisconnect: () => void
}) {
  const isPending = pendingNativeAction?.providerKey === app.providerKey
  const pendingKind = isPending ? pendingNativeAction?.kind : null
  const statusLabel = app.status === 'pending'
      ? 'Pending'
      : app.status === 'configuration_error'
        ? 'Setup error'
        : 'Not connected'

  return (
    <NativeIntegrationRow
      summary={<TelegramSummaryCell app={app} />}
      status={(
        <NativeConnectionStatusPill
          connected={app.connected}
          disconnectedLabel={statusLabel}
          error={app.status === 'configuration_error'}
        />
      )}
      gridClassName="grid gap-3 md:grid-cols-[minmax(0,1fr)_8rem_16rem] md:items-start"
      actions={[
        app.connected ? (
          <div className="flex flex-wrap justify-start gap-2 md:justify-end">
            <NativeIntegrationActionButton
              label="Sync"
              icon={RefreshCw}
              pending={pendingKind === 'sync'}
              disabled={disabled}
              minWidthClassName="min-w-24"
              onClick={onSync}
            />
            <NativeIntegrationActionButton
              label="Disconnect"
              icon={Unplug}
              pending={pendingKind === 'disconnect'}
              disabled={disabled}
              tone="danger"
              onClick={onDisconnect}
            />
          </div>
        ) : (
          <NativeIntegrationActionButton
            label={app.userLinked ? 'Create Bot' : 'Connect'}
            icon={app.userLinked ? ExternalLink : Plug}
            pending={pendingKind === 'connect'}
            disabled={disabled || app.status === 'configuration_error'}
            tone="primary"
            onClick={onConnect}
          />
        ),
      ]}
    >
      <TelegramConnectFallback app={app} />
    </NativeIntegrationRow>
  )
}

function telegramStartCommand(managerLinkUrl: string): string {
  if (!managerLinkUrl) {
    return ''
  }
  try {
    const parsed = new URL(managerLinkUrl)
    const token = parsed.searchParams.get('start')?.trim()
    return token ? `/start ${token}` : ''
  } catch {
    return ''
  }
}

async function copyTelegramFallbackText(value: string): Promise<void> {
  if (!value) {
    return
  }
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(value)
    return
  }
  const textarea = document.createElement('textarea')
  textarea.value = value
  textarea.setAttribute('readonly', '')
  textarea.style.position = 'fixed'
  textarea.style.left = '-9999px'
  document.body.appendChild(textarea)
  textarea.select()
  document.execCommand('copy')
  document.body.removeChild(textarea)
}

function TelegramConnectFallback({ app }: { app: AgentTelegramApp }) {
  const [copied, setCopied] = useState<'command' | 'link' | null>(null)
  const linkUrl = app.userLinked ? app.createBotUrl : app.managerLinkUrl
  const appUrl = telegramAppUrlForWebUrl(linkUrl)
  const command = app.userLinked ? '' : telegramStartCommand(app.managerLinkUrl)
  if (app.connected || app.status === 'configuration_error' || !linkUrl) {
    return null
  }

  const copyValue = async (kind: 'command' | 'link', value: string) => {
    await copyTelegramFallbackText(value)
    setCopied(kind)
    window.setTimeout(() => setCopied((current) => (current === kind ? null : current)), 1800)
  }

  return (
    <div className="mt-3 rounded-md border border-blue-100 bg-blue-50 px-3 py-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm font-semibold text-blue-950">
          {app.userLinked ? 'Create the managed bot' : 'Telegram Web fallback'}
        </p>
        <div className="flex flex-wrap gap-2">
          {appUrl ? (
            <a
              className="inline-flex items-center gap-1.5 rounded-md border border-blue-200 bg-white px-2.5 py-1.5 text-xs font-semibold text-blue-700 transition hover:bg-blue-100"
              href={appUrl}
              rel="noreferrer"
            >
              <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
              Open App
            </a>
          ) : null}
          <a
            className="inline-flex items-center gap-1.5 rounded-md border border-blue-200 bg-white px-2.5 py-1.5 text-xs font-semibold text-blue-700 transition hover:bg-blue-100"
            href={linkUrl}
            target="_blank"
            rel="noreferrer"
          >
            <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
            Open Web
          </a>
          <button
            type="button"
            className="inline-flex items-center gap-1.5 rounded-md border border-blue-200 bg-white px-2.5 py-1.5 text-xs font-semibold text-blue-700 transition hover:bg-blue-100"
            onClick={() => copyValue('link', linkUrl)}
          >
            {copied === 'link' ? <Check className="h-3.5 w-3.5" aria-hidden="true" /> : <Copy className="h-3.5 w-3.5" aria-hidden="true" />}
            {copied === 'link' ? 'Copied' : 'Copy Link'}
          </button>
        </div>
      </div>
      {command ? (
        <div className="mt-2 flex flex-col gap-2 sm:flex-row sm:items-center">
          <code className="min-w-0 flex-1 overflow-x-auto rounded-md border border-blue-200 bg-white px-2.5 py-2 text-xs font-semibold text-blue-950">
            {command}
          </code>
          <button
            type="button"
            className="inline-flex items-center justify-center gap-1.5 rounded-md border border-blue-200 bg-white px-2.5 py-2 text-xs font-semibold text-blue-700 transition hover:bg-blue-100"
            onClick={() => copyValue('command', command)}
          >
            {copied === 'command' ? <Check className="h-3.5 w-3.5" aria-hidden="true" /> : <Copy className="h-3.5 w-3.5" aria-hidden="true" />}
            {copied === 'command' ? 'Copied' : 'Copy Command'}
          </button>
        </div>
      ) : null}
      {!app.userLinked ? (
        <p className="mt-2 text-xs text-blue-900">Open Telegram Web, find the manager bot, and send the copied command.</p>
      ) : null}
    </div>
  )
}

function TelegramSummaryCell({ app }: { app: AgentTelegramApp }) {
  return (
    <div className="flex min-w-0 items-center gap-3">
      <NativeProviderIconTile provider={{
        providerKey: app.providerKey,
        displayName: app.displayName,
        description: app.description,
        authType: 'custom',
        icon: app.icon,
        apiHosts: ['telegram.org'],
        scopes: [],
        connected: app.connected,
        scope: 'personal',
        expiresAt: null,
        connectUrl: '',
        filesUrl: '',
        pickerTokenUrl: '',
        agentEventUrl: '',
        revokeUrl: '',
      }} />
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <p className="truncate text-sm font-semibold text-slate-900">{app.displayName}</p>
          <span className="rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-emerald-700">
            Native
          </span>
        </div>
        {app.connected ? (
          <p className="mt-1 line-clamp-2 text-sm text-slate-600">
            @{app.botUsername} · {app.activeChatCount} known chat{app.activeChatCount === 1 ? '' : 's'}
          </p>
        ) : app.error ? (
          <p className="mt-1 line-clamp-2 text-sm text-red-600">{app.error}</p>
        ) : (
          <p className="mt-1 line-clamp-2 text-sm text-slate-600">{app.description}</p>
        )}
        {app.profileSyncStatus === 'error' && app.profileSyncError ? (
          <p className="mt-1 line-clamp-1 text-xs text-red-600">{app.profileSyncError}</p>
        ) : null}
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
    <div className="grid gap-3 px-4 py-3 md:grid-cols-[minmax(0,1fr)_7rem_8rem_8rem] md:items-center">
      <PipedreamAppSummaryCell app={app} />
      <div>
        <NativeConnectionStatusPill connected={app.connected} />
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
