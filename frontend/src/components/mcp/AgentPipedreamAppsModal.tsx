import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, CheckCircle2, FolderOpen, Hash, Loader2, Plug, Save, Settings, Unplug } from 'lucide-react'

import {
  agentDiscordAppQueryKey,
  fetchAgentDiscordApp,
  fetchAgentDiscordGuildChannels,
  startAgentDiscordConnect,
  updateAgentDiscordSubscriptions,
  type AgentDiscordApp,
  type DiscordChannel,
  type DiscordGuild,
  type DiscordSubscription,
  type DiscordSubscriptionSelection,
} from '../../api/discordNative'
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
  NativeIntegrationFilesDisclosure,
  NativeProviderIconTile,
  nativeIntegrationFilesQueryKey,
  nativeOAuthContextPayload,
  openGoogleDrivePicker,
  openNativeOAuthPopup,
  storePendingNativeOAuth,
  supportsNativeIntegrationPicker,
  useNativeIntegrationRefreshEffects,
} from './NativeIntegrationShared'

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

type PendingDiscordAction = 'connect' | 'save' | null

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
  const [pendingDiscordAction, setPendingDiscordAction] = useState<PendingDiscordAction>(null)
  const [discordConfigureOpen, setDiscordConfigureOpen] = useState(false)
  const [statusMessage, setStatusMessage] = useState<PipedreamStatusMessage>(null)
  const nativeQueryKey = useMemo(
    () => ['native-integrations', nativeIntegrationsUrl] as const,
    [nativeIntegrationsUrl],
  )
  const discordQueryKey = useMemo(() => agentDiscordAppQueryKey(agentId), [agentId])
  useNativeIntegrationRefreshEffects({ queryKey: nativeQueryKey, onError: (message) => setStatusMessage({ text: message, tone: 'error' }) })

  useEffect(() => {
    const handleDiscordOAuthComplete = (event: MessageEvent<{ type?: unknown; status?: unknown }>) => {
      if (event.origin !== window.location.origin || event.data?.type !== 'gobii:discord_oauth_complete') {
        return
      }
      void queryClient.invalidateQueries({ queryKey: discordQueryKey })
      void queryClient.invalidateQueries({ queryKey: ['agent-roster'], exact: false })
      if (event.data.status !== 'success') {
        setStatusMessage({ text: 'Unable to complete the Discord connection.', tone: 'error' })
      }
    }
    window.addEventListener('message', handleDiscordOAuthComplete)
    return () => window.removeEventListener('message', handleDiscordOAuthComplete)
  }, [discordQueryKey, queryClient])

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
    onSuccess: (payload, { popup }) => {
      storePendingNativeOAuth(payload.state, nativeOAuthContextPayload(payload.providerKey, payload.state, popup))
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
      const selectedCount = await openGoogleDrivePicker(token)
      return { provider, selectedCount }
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

  const discordConnectMutation = useMutation({
    mutationFn: () => startAgentDiscordConnect(agentId),
    onMutate: () => {
      setPendingDiscordAction('connect')
      setStatusMessage(null)
    },
    onSuccess: (result) => {
      void queryClient.invalidateQueries({ queryKey: discordQueryKey })
      void queryClient.invalidateQueries({ queryKey: ['agent-roster'], exact: false })
      window.open(result.connectUrl, '_blank', 'noopener,noreferrer')
    },
    onError: (error) => {
      setStatusMessage({ text: safeErrorMessage(error), tone: 'error' })
    },
    onSettled: () => setPendingDiscordAction(null),
  })

  const discordSubscriptionsMutation = useMutation({
    mutationFn: (subscriptions: DiscordSubscriptionSelection[]) => updateAgentDiscordSubscriptions(agentId, subscriptions),
    onMutate: () => {
      setPendingDiscordAction('save')
      setStatusMessage(null)
    },
    onSuccess: (app) => {
      queryClient.setQueryData(discordQueryKey, app)
      void queryClient.invalidateQueries({ queryKey: discordQueryKey })
      void queryClient.invalidateQueries({ queryKey: ['agent-roster'], exact: false })
    },
    onError: (error) => {
      setStatusMessage({ text: safeErrorMessage(error), tone: 'error' })
    },
    onSettled: () => setPendingDiscordAction(null),
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
  const apps: AgentAppRow[] = [
    ...nativeRows,
    ...(discordRow ? [discordRow] : []),
    ...(enablePipedreamApps ? (appsQuery.data?.apps ?? []).map((app) => ({ ...app, kind: 'pipedream' as const })) : []),
  ]
  const isBusy = connectMutation.isPending
    || disconnectMutation.isPending
    || removeMutation.isPending
    || nativeConnectMutation.isPending
    || nativeDisconnectMutation.isPending
    || nativePickerMutation.isPending
    || discordConnectMutation.isPending
    || discordSubscriptionsMutation.isPending
  const activeDiscordApp = discordConfigureOpen ? (discordAppQuery.data ?? discordRow) : null

  const body = activeDiscordApp ? (
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
      onSave={(subscriptions) => discordSubscriptionsMutation.mutate(subscriptions)}
    />
  ) : (
      <div className="space-y-4 p-1">
        <PipedreamStatusBanner statusMessage={statusMessage} />
        <PipedreamSearchInput
          value={searchTerm}
          onChange={setSearchTerm}
          isFetching={appsQuery.isFetching || nativeIntegrationsQuery.isFetching || discordAppQuery.isFetching}
          disabled={isBusy}
        />

        {(enablePipedreamApps && appsQuery.isError) || nativeIntegrationsQuery.isError || discordAppQuery.isError ? (
          <PipedreamErrorState error={appsQuery.error ?? nativeIntegrationsQuery.error ?? discordAppQuery.error} fallback="Unable to load apps." />
        ) : (enablePipedreamApps && appsQuery.isLoading) || nativeIntegrationsQuery.isLoading || discordAppQuery.isLoading ? (
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
                onDisconnect={() => nativeDisconnectMutation.mutate(app)}
                onPicker={() => nativePickerMutation.mutate(app)}
              />
            ) : app.kind === 'discord' ? (
              <AgentDiscordAppRowItem
                key="native-discord"
                app={app}
                pendingDiscordAction={pendingDiscordAction}
                disabled={isBusy}
                onConnect={() => discordConnectMutation.mutate()}
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
    <PipedreamModalShell
      title={activeDiscordApp ? 'Configure Discord' : 'Apps'}
      subtitle={activeDiscordApp ? 'Choose the Discord server channels this agent should watch.' : 'Search, connect, and disconnect apps for this agent.'}
      ariaLabel="Manage agent apps"
      onClose={onClose}
    >
      {body}
    </PipedreamModalShell>
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
    <div className="px-4 py-3">
      <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_7rem_8rem_8rem] sm:items-start">
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

function discordSubscriptionKey(guildId: string, channelId: string): string {
  return `${guildId}:${channelId}`
}

function activeDiscordSelections(app: AgentDiscordApp): Record<string, DiscordSubscriptionSelection> {
  return Object.fromEntries(
    app.subscriptions
      .filter((subscription) => subscription.status === 'active')
      .map((subscription) => [
        discordSubscriptionKey(subscription.guildId, subscription.channelId),
        {
          guildId: subscription.guildId,
          channelId: subscription.channelId,
          channelName: subscription.channelName,
        },
      ]),
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
  pendingDiscordAction: PendingDiscordAction
  disabled: boolean
  onConnect: () => void
  onConfigure: () => void
}) {
  const isPendingConnect = pendingDiscordAction === 'connect'

  return (
    <div className="px-4 py-3">
      <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_7rem_8rem_8rem] md:items-start">
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
          ) : null}
        </div>
        <div className="flex justify-start md:justify-end">
          {app.connected ? (
            null
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

export function DiscordConfigurationScreen({
  agentId,
  app,
  disabled,
  pendingDiscordAction,
  statusMessage,
  onBack,
  onSave,
}: {
  agentId: string
  app: AgentDiscordApp
  disabled: boolean
  pendingDiscordAction: PendingDiscordAction
  statusMessage: PipedreamStatusMessage
  onBack: () => void
  onSave: (subscriptions: DiscordSubscriptionSelection[]) => void
}) {
  const [selectedSubscriptions, setSelectedSubscriptions] = useState<Record<string, DiscordSubscriptionSelection>>(
    () => activeDiscordSelections(app),
  )
  const initialSelections = useMemo(() => activeDiscordSelections(app), [app.subscriptions])

  useEffect(() => {
    setSelectedSubscriptions(initialSelections)
  }, [initialSelections])

  const selectedKeys = useMemo(() => Object.keys(selectedSubscriptions).sort(), [selectedSubscriptions])
  const initialKeys = useMemo(() => Object.keys(initialSelections).sort(), [initialSelections])
  const hasChanges = selectedKeys.join('|') !== initialKeys.join('|')
  const isPendingSave = pendingDiscordAction === 'save'

  const toggleChannel = (channel: DiscordChannel) => {
    const key = discordSubscriptionKey(channel.guildId, channel.channelId)
    setSelectedSubscriptions((current) => {
      if (current[key]) {
        const next = { ...current }
        delete next[key]
        return next
      }
      return {
        ...current,
        [key]: {
          guildId: channel.guildId,
          channelId: channel.channelId,
          channelName: channel.channelName,
        },
      }
    })
  }

  const saveDisabled = disabled || isPendingSave || !hasChanges

  return (
    <div className="space-y-4 p-1">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <button
          type="button"
          className="inline-flex w-fit items-center gap-2 rounded-md border border-slate-200 bg-white px-3 py-2 text-sm font-semibold text-slate-700 transition hover:bg-slate-50 disabled:opacity-60"
          onClick={onBack}
          disabled={disabled}
        >
          <ArrowLeft className="h-4 w-4" aria-hidden="true" />
          Back
        </button>
        <button
          type="button"
          className="inline-flex min-w-28 items-center justify-center gap-2 rounded-md bg-blue-600 px-3 py-2 text-sm font-semibold text-white transition hover:bg-blue-700 disabled:opacity-60"
          onClick={() => onSave(Object.values(selectedSubscriptions))}
          disabled={saveDisabled}
        >
          {isPendingSave ? (
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          ) : (
            <Save className="h-4 w-4" aria-hidden="true" />
          )}
          Save
        </button>
      </div>

      <DiscordSummaryCell app={app} />
      <PipedreamStatusBanner statusMessage={statusMessage} />

      {app.guilds.length > 0 ? (
        <div className="space-y-3">
          {app.guilds.map((guild) => (
            <DiscordGuildChannelSection
              key={guild.guildId}
              agentId={agentId}
              guild={guild}
              subscriptions={app.subscriptions}
              selectedSubscriptions={selectedSubscriptions}
              disabled={disabled}
              onToggleChannel={toggleChannel}
            />
          ))}
        </div>
      ) : (
        <PipedreamEmptyState label="No Discord servers are connected yet." />
      )}
    </div>
  )
}

function DiscordSummaryCell({ app }: { app: AgentDiscordApp }) {
  const detail = app.connected
    ? `${app.guildCount} ${app.guildCount === 1 ? 'server' : 'servers'} connected; ${app.activeSubscriptionCount} ${app.activeSubscriptionCount === 1 ? 'channel' : 'channels'} subscribed.`
    : app.description
  return (
    <div className="flex min-w-0 items-center gap-3">
      <span className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-indigo-200 bg-indigo-50 text-indigo-700">
        <img src="/static/images/integrations/native/discord.svg" alt="" className="h-6 w-6 object-contain" loading="lazy" />
      </span>
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <p className="truncate text-sm font-semibold text-slate-900">{app.displayName}</p>
          <span className="rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-emerald-700">
            Native
          </span>
        </div>
        <p className="mt-1 line-clamp-2 text-sm text-slate-600">{detail}</p>
      </div>
    </div>
  )
}

function DiscordGuildChannelSection({
  agentId,
  guild,
  subscriptions,
  selectedSubscriptions,
  disabled,
  onToggleChannel,
}: {
  agentId: string
  guild: DiscordGuild
  subscriptions: DiscordSubscription[]
  selectedSubscriptions: Record<string, DiscordSubscriptionSelection>
  disabled: boolean
  onToggleChannel: (channel: DiscordChannel) => void
}) {
  const channelsQuery = useQuery({
    queryKey: ['agent-discord-channels', agentId, guild.guildId],
    queryFn: () => fetchAgentDiscordGuildChannels(agentId, guild.guildId),
  })
  const activeSubscriptionChannels = subscriptions
    .filter((subscription) => subscription.status === 'active' && subscription.guildId === guild.guildId)
    .map((subscription): DiscordChannel => ({
      guildId: subscription.guildId,
      guildName: subscription.guildName,
      channelId: subscription.channelId,
      channelName: subscription.channelName,
      label: `${subscription.guildName} / #${subscription.channelName || subscription.channelId}`,
    }))
  const channelsByKey = new Map<string, DiscordChannel>()
  for (const channel of [...activeSubscriptionChannels, ...(channelsQuery.data?.channels ?? [])]) {
    channelsByKey.set(discordSubscriptionKey(channel.guildId, channel.channelId), channel)
  }
  const channels = Array.from(channelsByKey.values()).sort((a, b) => a.channelName.localeCompare(b.channelName))

  return (
    <section className="rounded-lg border border-indigo-100 bg-white px-3 py-3" aria-label={`${guild.name} Discord channels`}>
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold text-slate-900">{guild.name}</p>
          <p className="text-xs text-slate-500">Choose channels that should wake this agent.</p>
        </div>
        {channelsQuery.isLoading ? <Loader2 className="h-4 w-4 animate-spin text-indigo-600" aria-hidden="true" /> : null}
      </div>
      {channelsQuery.data?.status === 'action_required' ? (
        <div className="mt-3 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
          <p>{channelsQuery.data.message || 'The Gobii Discord bot needs access to list channels in this server.'}</p>
          {channelsQuery.data.botInviteUrl ? (
            <a href={channelsQuery.data.botInviteUrl} target="_blank" rel="noreferrer" className="mt-2 inline-flex font-semibold text-amber-900 underline">
              Invite bot
            </a>
          ) : null}
        </div>
      ) : channelsQuery.isError ? (
        <div className="mt-3 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
          {safeErrorMessage(channelsQuery.error)}
        </div>
      ) : channels.length > 0 ? (
        <div className="mt-3 grid gap-2 sm:grid-cols-2">
          {channels.map((channel) => {
            const key = discordSubscriptionKey(channel.guildId, channel.channelId)
            const checked = Boolean(selectedSubscriptions[key])
            return (
              <label
                key={key}
                className="flex min-w-0 items-center gap-2 rounded-md border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 transition hover:border-indigo-200 hover:text-slate-950"
              >
                <input
                  type="checkbox"
                  className="h-4 w-4 rounded border-slate-300 text-indigo-600 focus:ring-indigo-500"
                  checked={checked}
                  onChange={() => onToggleChannel(channel)}
                  disabled={disabled}
                />
                <Hash className="h-3.5 w-3.5 shrink-0 text-slate-400" aria-hidden="true" />
                <span className="truncate">{channel.channelName || channel.channelId}</span>
              </label>
            )
          })}
        </div>
      ) : channelsQuery.isLoading ? null : (
        <p className="mt-3 text-sm text-slate-600">No text channels were found for this server.</p>
      )}
    </section>
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
