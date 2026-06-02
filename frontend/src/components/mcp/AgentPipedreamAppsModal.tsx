import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, FolderOpen, Loader2, Plug, Unplug } from 'lucide-react'

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
  NativeProviderIcon,
  NativeIntegrationFilesDisclosure,
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
  const [statusMessage, setStatusMessage] = useState<PipedreamStatusMessage>(null)
  const nativeQueryKey = useMemo(
    () => ['native-integrations', nativeIntegrationsUrl] as const,
    [nativeIntegrationsUrl],
  )
  useNativeIntegrationRefreshEffects({ queryKey: nativeQueryKey, onError: (message) => setStatusMessage({ text: message, tone: 'error' }) })

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

  const normalizedSearch = debouncedSearchTerm.toLowerCase()
  const nativeRows = (nativeIntegrationsQuery.data?.providers ?? [])
    .filter((provider) => !normalizedSearch || [
      provider.providerKey,
      provider.displayName,
      provider.description,
    ].some((value) => value.toLowerCase().includes(normalizedSearch)))
    .map((provider) => ({ ...provider, kind: 'native' as const }))
  const apps: AgentAppRow[] = [
    ...nativeRows,
    ...(enablePipedreamApps ? (appsQuery.data?.apps ?? []).map((app) => ({ ...app, kind: 'pipedream' as const })) : []),
  ]
  const isBusy = connectMutation.isPending
    || disconnectMutation.isPending
    || removeMutation.isPending
    || nativeConnectMutation.isPending
    || nativeDisconnectMutation.isPending
    || nativePickerMutation.isPending

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

        {(enablePipedreamApps && appsQuery.isError) || nativeIntegrationsQuery.isError ? (
          <PipedreamErrorState error={appsQuery.error ?? nativeIntegrationsQuery.error} fallback="Unable to load apps." />
        ) : (enablePipedreamApps && appsQuery.isLoading) || nativeIntegrationsQuery.isLoading ? (
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
      <span className="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-emerald-200 bg-emerald-50 text-emerald-700">
        <NativeProviderIcon provider={provider} />
      </span>
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
