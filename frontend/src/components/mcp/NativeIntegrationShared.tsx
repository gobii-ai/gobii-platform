import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, ChevronDown, ChevronRight, FileText, FolderOpen, Loader2, Plug, Table2, Unplug } from 'lucide-react'

import type {
  NativeIntegrationAccessibleFile,
  NativeIntegrationConnectResponse,
  NativeIntegrationPickerTokenResponse,
  NativeIntegrationProvider,
} from '../../api/nativeIntegrations'
import {
  fetchNativeIntegrationFiles,
  fetchNativeIntegrationPickerToken,
  revokeNativeIntegration,
  startNativeIntegrationConnect,
} from '../../api/nativeIntegrations'
import { safeErrorMessage } from '../../api/safeErrorMessage'
import { readStoredConsoleContext } from '../../util/consoleContextStorage'
import type { SettingsSurfaceVariant } from '../common/SettingsSurface'

type GoogleDocsView = {
  setMimeTypes: (mimeTypes: string) => GoogleDocsView
}

type GooglePickerInstance = {
  setVisible: (visible: boolean) => void
}

type GooglePickerBuilder = {
  addView: (view: GoogleDocsView) => GooglePickerBuilder
  setOAuthToken: (token: string) => GooglePickerBuilder
  setDeveloperKey: (key: string) => GooglePickerBuilder
  setAppId: (appId: string) => GooglePickerBuilder
  enableFeature: (feature: string) => GooglePickerBuilder
  setCallback: (callback: (data: Record<string, unknown>) => void) => GooglePickerBuilder
  build: () => GooglePickerInstance
}

type GooglePickerNamespace = {
  Action: { PICKED: string; CANCEL?: string }
  DocsView: new (viewId: string) => GoogleDocsView
  Document: { ID: string; NAME: string; MIME_TYPE: string; URL: string }
  Feature: { MULTISELECT_ENABLED: string }
  PickerBuilder: new () => GooglePickerBuilder
  Response: { ACTION: string; DOCUMENTS: string }
  ViewId: { DOCS: string }
}

declare global {
  interface Window {
    gapi?: {
      load: (apiName: string, config: { callback: () => void }) => void
    }
    google?: {
      picker?: GooglePickerNamespace
    }
  }
}

const GOOGLE_SHEETS_MIME_TYPE = 'application/vnd.google-apps.spreadsheet'
const GOOGLE_DOCS_MIME_TYPE = 'application/vnd.google-apps.document'
const NATIVE_OAUTH_COMPLETE_MESSAGE = 'gobii:native-oauth-complete'
const NATIVE_OAUTH_COMPLETE_PREFIX = 'gobii:native_oauth_complete:'

type NativeOAuthCompleteMessage = {
  type?: unknown
  providerKey?: unknown
  ok?: unknown
  error?: unknown
}

let googlePickerApiPromise: Promise<void> | null = null

const DEFAULT_NATIVE_PROVIDER_TILE_CLASS_NAME = 'inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-emerald-200 bg-emerald-50 text-emerald-700'

const NATIVE_PROVIDER_ICONS: Record<string, { className: string; framedClassName: string; src: string; tileClassName?: string }> = {
  apollo: {
    className: 'h-4 w-4 object-contain',
    framedClassName: 'h-7 w-7 object-contain',
    src: '/static/images/integrations/native/apollo.svg',
    tileClassName: 'inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-slate-200 bg-[#F8FF2C] text-slate-950',
  },
  google_drive: {
    className: 'h-5 w-5 object-contain',
    framedClassName: 'h-5 w-5 object-contain',
    src: '/static/images/integrations/native/google_drive.svg',
  },
  hubspot: {
    className: 'h-5 w-5 object-contain',
    framedClassName: 'h-7 w-7 object-contain',
    src: '/static/images/integrations/native/hubspot.svg',
    tileClassName: 'inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-orange-200 bg-orange-50 text-orange-700',
  },
  discord: {
    className: 'h-5 w-5 object-contain',
    framedClassName: 'h-6 w-6 object-contain',
    src: '/static/images/integrations/native/discord.svg',
    tileClassName: 'inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-indigo-200 bg-indigo-50 text-indigo-700',
  },
  meta_ads: {
    className: 'h-5 w-5 object-contain',
    framedClassName: 'h-6 w-6 object-contain',
    src: '/static/images/integrations/native/meta_ads.svg',
    tileClassName: 'inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-blue-200 bg-blue-50 text-blue-700',
  },
}

export function useNativeIntegrationRefreshEffects({
  queryKey,
  onError,
}: {
  queryKey: readonly unknown[]
  onError: (message: string) => void
}) {
  const queryClient = useQueryClient()

  useEffect(() => {
    const handleComplete = (payload: NativeOAuthCompleteMessage) => {
      if (!payload || payload.type !== NATIVE_OAUTH_COMPLETE_MESSAGE) {
        return
      }

      queryClient.invalidateQueries({ queryKey })
      if (payload.ok) {
        return
      }
      onError(String(payload.error || 'Unable to complete the native app connection.'))
    }
    const handleMessage = (event: MessageEvent<NativeOAuthCompleteMessage>) => {
      if (event.origin === window.location.origin) {
        handleComplete(event.data)
      }
    }
    const handleStorage = (event: StorageEvent) => {
      if (!event.key?.startsWith(NATIVE_OAUTH_COMPLETE_PREFIX) || !event.newValue) {
        return
      }
      try {
        handleComplete(JSON.parse(event.newValue))
        localStorage.removeItem(event.key)
      } catch (error) {
        console.warn('Invalid native integration OAuth completion payload', error)
      }
    }

    window.addEventListener('message', handleMessage)
    window.addEventListener('storage', handleStorage)
    return () => {
      window.removeEventListener('message', handleMessage)
      window.removeEventListener('storage', handleStorage)
    }
  }, [onError, queryClient, queryKey])

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const result = params.get('native_oauth')
    if (!result) {
      return
    }

    queryClient.invalidateQueries({ queryKey })
    if (result === 'error') {
      onError('Unable to complete the app connection.')
    }
    params.delete('native_oauth')
    const nextSearch = params.toString()
    const nextUrl = `${window.location.pathname}${nextSearch ? `?${nextSearch}` : ''}${window.location.hash}`
    window.history.replaceState(window.history.state, '', nextUrl)
  }, [onError, queryClient, queryKey])
}

export function storePendingNativeOAuth(state: string, payload: Record<string, unknown>) {
  try {
    localStorage.setItem(`gobii:native_oauth_state:${state}`, JSON.stringify(payload))
  } catch (error) {
    console.warn('Failed to persist native integration OAuth state', error)
  }
}

export function nativeOAuthContextPayload(
  provider: NativeIntegrationProvider,
  state: string,
  popup: Window | null,
  agentId?: string | null,
) {
  return {
    providerKey: provider.providerKey,
    agentEventUrl: provider.agentEventUrl,
    agentId: agentId || null,
    returnUrl: window.location.href,
    createdAt: Date.now(),
    context: readStoredConsoleContext(),
    popup: Boolean(popup),
    state,
  }
}

export function openNativeOAuthPopup(provider: NativeIntegrationProvider): Window | null {
  const width = 520
  const height = 720
  const left = Math.max(0, window.screenX + (window.outerWidth - width) / 2)
  const top = Math.max(0, window.screenY + (window.outerHeight - height) / 2)
  const popup = window.open(
    '',
    `gobii-native-oauth-${provider.providerKey}`,
    `popup=yes,width=${width},height=${height},left=${Math.round(left)},top=${Math.round(top)}`,
  )
  if (!popup) {
    return null
  }

  try {
    popup.document.title = `Connect ${provider.displayName}`
    popup.document.body.style.fontFamily = 'system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    popup.document.body.style.margin = '0'
    popup.document.body.style.display = 'grid'
    popup.document.body.style.minHeight = '100vh'
    popup.document.body.style.placeItems = 'center'
    popup.document.body.style.background = '#0f172a'
    popup.document.body.style.color = '#e2e8f0'
    popup.document.body.textContent = `Opening ${provider.displayName}...`
  } catch {
    // Some browsers restrict about:blank popup writes; the OAuth redirect still works.
  }
  return popup
}

export function supportsNativeIntegrationPicker(provider: NativeIntegrationProvider): boolean {
  return provider.providerKey === 'google_drive' && Boolean(provider.pickerTokenUrl)
}

export function usesManualNativeIntegrationCredentials(provider: NativeIntegrationProvider): boolean {
  return provider.authType === 'manual' && provider.credentialFields.length > 0
}

export function confirmNativeIntegrationDisconnect(provider: Pick<NativeIntegrationProvider, 'displayName'>): boolean {
  return window.confirm(
    `Disconnect ${provider.displayName}? This will remove it from every agent in this workspace. Agents will stop using it until you connect it again.`,
  )
}

export function supportsNativeIntegrationFileList(provider: NativeIntegrationProvider): boolean {
  return provider.providerKey === 'google_drive' && provider.connected && Boolean(provider.filesUrl)
}

export function nativeIntegrationFilesQueryKey(provider: NativeIntegrationProvider) {
  return ['native-integration-files', provider.providerKey, provider.filesUrl] as const
}

export function NativeProviderIcon({ framed = false, provider }: { framed?: boolean; provider: NativeIntegrationProvider }) {
  const icon = provider.icon ? NATIVE_PROVIDER_ICONS[provider.icon] : null
  if (icon) {
    const image = <img src={icon.src} alt="" className={framed ? icon.framedClassName : icon.className} loading="lazy" />
    if (provider.icon === 'apollo' && !framed) {
      return (
        <span className="inline-flex h-5 w-5 items-center justify-center rounded bg-[#F8FF2C]">
          {image}
        </span>
      )
    }
    return image
  }
  return <Plug className="h-5 w-5" aria-hidden="true" />
}

export function NativeProviderIconTile({ provider }: { provider: NativeIntegrationProvider }) {
  const icon = provider.icon ? NATIVE_PROVIDER_ICONS[provider.icon] : null
  return (
    <span className={icon?.tileClassName ?? DEFAULT_NATIVE_PROVIDER_TILE_CLASS_NAME}>
      <NativeProviderIcon provider={provider} framed />
    </span>
  )
}

type NativePendingKind = 'connect' | 'disconnect' | 'picker' | null
export type NativeIntegrationPendingOperation = {
  providerKey: string
  kind: NonNullable<NativePendingKind>
} | null

type NativeIntegrationActionFeedback = {
  setPendingAction: (action: NativeIntegrationPendingOperation) => void
  setStatusMessage: (message: string | null) => void
  onError?: (message: string) => void
}

function nativeIntegrationDisplayName(provider: NativeIntegrationProvider): string {
  const fallback = provider.providerKey.replace(/[_-]+/g, ' ').replace(/\b\w/g, (char) => char.toUpperCase())
  return provider.displayName?.trim() || fallback || provider.providerKey
}

function notifyNativeIntegrationError({
  message,
  setStatusMessage,
  onError,
}: {
  message: string
  setStatusMessage: (message: string | null) => void
  onError?: (message: string) => void
}) {
  setStatusMessage(message)
  onError?.(message)
}

export function handleNativeOAuthConnectSuccess({
  payload,
  provider,
  popup,
  agentId,
  closedMessage,
  onClosed,
}: {
  payload: NativeIntegrationConnectResponse
  provider: NativeIntegrationProvider
  popup: Window | null
  agentId?: string | null
  closedMessage?: string
  onClosed?: (message: string) => void
}) {
  storePendingNativeOAuth(payload.state, nativeOAuthContextPayload(provider, payload.state, popup, agentId))
  if (popup && !popup.closed) {
    popup.location.href = payload.authorizationUrl
    popup.focus()
    return
  }
  if (popup?.closed) {
    onClosed?.(closedMessage ?? `Connection window was closed before ${nativeIntegrationDisplayName(provider)} opened.`)
    return
  }
  window.location.href = payload.authorizationUrl
}

export function useNativeIntegrationConnectMutation({
  setPendingAction,
  setStatusMessage,
  onError,
  startConnect = (provider) => startNativeIntegrationConnect(provider.connectUrl),
  agentId = null,
  closedMessage,
}: NativeIntegrationActionFeedback & {
  startConnect?: (provider: NativeIntegrationProvider) => Promise<NativeIntegrationConnectResponse>
  agentId?: string | null
  closedMessage?: string
}) {
  return useMutation({
    mutationFn: ({ provider }: { provider: NativeIntegrationProvider; popup: Window | null }) =>
      startConnect(provider),
    onMutate: ({ provider }) => {
      setPendingAction({ providerKey: provider.providerKey, kind: 'connect' })
      setStatusMessage(null)
    },
    onSuccess: (payload, { provider, popup }) => {
      handleNativeOAuthConnectSuccess({
        payload,
        provider,
        popup,
        agentId,
        closedMessage,
        onClosed: (message) => notifyNativeIntegrationError({ message, setStatusMessage, onError }),
      })
    },
    onError: (error, { popup }) => {
      if (popup && !popup.closed) {
        popup.close()
      }
      notifyNativeIntegrationError({ message: safeErrorMessage(error), setStatusMessage, onError })
    },
    onSettled: () => setPendingAction(null),
  })
}

export function useNativeIntegrationDisconnectMutation({
  nativeQueryKey,
  setPendingAction,
  setStatusMessage,
  onError,
  disconnect = (provider) => revokeNativeIntegration(provider.revokeUrl).then(() => provider),
  extraInvalidateQueryKeys = [],
}: NativeIntegrationActionFeedback & {
  nativeQueryKey: readonly unknown[]
  disconnect?: (provider: NativeIntegrationProvider) => Promise<unknown>
  extraInvalidateQueryKeys?: readonly unknown[][]
}) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (provider: NativeIntegrationProvider) => disconnect(provider),
    onMutate: (provider) => {
      setPendingAction({ providerKey: provider.providerKey, kind: 'disconnect' })
      setStatusMessage(null)
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: nativeQueryKey })
      extraInvalidateQueryKeys.forEach((queryKey) => {
        void queryClient.invalidateQueries({ queryKey })
      })
    },
    onError: (error) => {
      notifyNativeIntegrationError({ message: safeErrorMessage(error), setStatusMessage, onError })
    },
    onSettled: () => setPendingAction(null),
  })
}

export function useNativeIntegrationPickerMutation({
  setPendingAction,
  setStatusMessage,
  onError,
  openPicker = openGoogleDrivePicker,
  preparePicker,
}: NativeIntegrationActionFeedback & {
  openPicker?: (token: NativeIntegrationPickerTokenResponse) => Promise<NativeIntegrationAccessibleFile[]>
  preparePicker?: (provider: NativeIntegrationProvider) => Promise<void | (() => void)> | void | (() => void)
}) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (provider: NativeIntegrationProvider) => {
      const cleanup = await preparePicker?.(provider)
      try {
        const token = await fetchNativeIntegrationPickerToken(provider.pickerTokenUrl)
        const selectedFiles = await openPicker(token)
        return { provider, selectedCount: selectedFiles.length }
      } finally {
        cleanup?.()
      }
    },
    onMutate: (provider) => {
      setPendingAction({ providerKey: provider.providerKey, kind: 'picker' })
      setStatusMessage(null)
    },
    onSuccess: ({ provider }) => {
      void queryClient.invalidateQueries({ queryKey: nativeIntegrationFilesQueryKey(provider) })
    },
    onError: (error) => {
      notifyNativeIntegrationError({ message: safeErrorMessage(error), setStatusMessage, onError })
    },
    onSettled: () => setPendingAction(null),
  })
}

export function NativeIntegrationSummaryCell({
  provider,
  descriptionClassName,
  showNativeBadge = true,
  showConnectedBadge = false,
  surface = 'standalone',
}: {
  provider: NativeIntegrationProvider
  descriptionClassName?: string
  showNativeBadge?: boolean
  showConnectedBadge?: boolean
  surface?: SettingsSurfaceVariant
}) {
  const resolvedDescriptionClassName = descriptionClassName ?? (
    surface === 'embedded' ? 'mt-1 line-clamp-2 text-sm text-slate-400' : 'mt-1 line-clamp-2 text-sm text-slate-600'
  )
  const titleClassName = surface === 'embedded' ? 'text-slate-100' : 'text-slate-900'
  const nativeBadgeClassName = surface === 'embedded'
    ? 'border-emerald-300/25 bg-emerald-950/45 text-emerald-200'
    : 'border-emerald-200 bg-emerald-50 text-emerald-700'
  const connectedBadgeClassName = surface === 'embedded'
    ? 'border-emerald-300/25 bg-emerald-950/45 text-emerald-200'
    : 'border-emerald-200 bg-emerald-50 text-emerald-700'
  return (
    <div className="flex min-w-0 items-center gap-3">
      <NativeProviderIconTile provider={provider} />
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <p className={`truncate text-sm font-semibold ${titleClassName}`}>{provider.displayName}</p>
          {showNativeBadge ? (
            <span className={`rounded-full border px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide ${nativeBadgeClassName}`}>
              Native
            </span>
          ) : null}
          {showConnectedBadge && provider.connected ? (
            <span className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-semibold ${connectedBadgeClassName}`}>
              <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" />
              Connected
            </span>
          ) : null}
        </div>
        {provider.description ? <p className={resolvedDescriptionClassName}>{provider.description}</p> : null}
      </div>
    </div>
  )
}

export function NativeIntegrationStatusBadge({
  connected,
  surface = 'standalone',
}: {
  connected: boolean
  surface?: SettingsSurfaceVariant
}) {
  if (connected) {
    const connectedClassName = surface === 'embedded'
      ? 'border-emerald-300/25 bg-emerald-950/45 text-emerald-200'
      : 'border-emerald-200 bg-emerald-50 text-emerald-700'
    return (
      <span className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-semibold ${connectedClassName}`}>
        <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" />
        Connected
      </span>
    )
  }
  const workspaceClassName = surface === 'embedded'
    ? 'border-slate-200/20 bg-slate-950/20 text-slate-400'
    : 'border-slate-200 text-slate-500'
  return (
    <span className={`inline-flex rounded-full border px-2.5 py-1 text-xs font-semibold ${workspaceClassName}`}>
      Workspace
    </span>
  )
}

export function NativeIntegrationPickerButton({
  pendingKind,
  disabled,
  onClick,
  minWidth = true,
  surface = 'standalone',
}: {
  pendingKind: NativePendingKind
  disabled: boolean
  onClick: () => void
  minWidth?: boolean
  surface?: SettingsSurfaceVariant
}) {
  const className = surface === 'embedded'
    ? 'border-sky-300/25 bg-sky-950/20 text-sky-100 hover:border-sky-200/40 hover:bg-sky-900/40'
    : 'border-blue-200 bg-white text-blue-700 hover:bg-blue-50'
  return (
    <button
      type="button"
      className={[
        `inline-flex items-center justify-center gap-2 rounded-md border px-3 py-2 text-sm font-semibold transition disabled:opacity-60 ${className}`,
        minWidth ? 'min-w-28' : '',
      ].filter(Boolean).join(' ')}
      onClick={onClick}
      disabled={disabled}
    >
      {pendingKind === 'picker' ? (
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
      ) : (
        <FolderOpen className="h-4 w-4" aria-hidden="true" />
      )}
      Select Files
    </button>
  )
}

export function NativeIntegrationConnectionButton({
  connected,
  pendingKind,
  disabled,
  onConnect,
  onDisconnect,
  disconnectTone = 'danger',
  minWidth = true,
  surface = 'standalone',
}: {
  connected: boolean
  pendingKind: NativePendingKind
  disabled: boolean
  onConnect: () => void
  onDisconnect: () => void
  disconnectTone?: 'danger' | 'neutral'
  minWidth?: boolean
  surface?: SettingsSurfaceVariant
}) {
  if (connected) {
    const connectedClassName = surface === 'embedded'
      ? disconnectTone === 'danger'
        ? 'border-rose-300/25 bg-rose-950/20 text-rose-200 hover:border-rose-200/40 hover:bg-rose-900/35'
        : 'border-slate-200/20 bg-slate-950/20 text-slate-300 hover:border-slate-100/35 hover:bg-slate-900/40'
      : disconnectTone === 'danger'
        ? 'border-red-200 bg-white text-red-700 hover:bg-red-50'
        : 'border-slate-200 bg-white text-slate-700 hover:bg-slate-50'
    return (
      <button
        type="button"
        className={[
          `inline-flex items-center justify-center gap-2 rounded-md border px-3 py-2 text-sm font-semibold transition disabled:opacity-60 ${connectedClassName}`,
          minWidth ? 'min-w-28' : '',
        ].filter(Boolean).join(' ')}
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
    )
  }

  const connectClassName = surface === 'embedded'
    ? 'border border-sky-300/25 bg-sky-900/55 text-sky-50 hover:border-sky-200/40 hover:bg-sky-900/75'
    : 'bg-blue-600 text-white hover:bg-blue-700'
  return (
    <button
      type="button"
      className={[
        `inline-flex items-center justify-center gap-2 rounded-md px-3 py-2 text-sm font-semibold transition disabled:opacity-60 ${connectClassName}`,
        minWidth ? 'min-w-28' : '',
      ].filter(Boolean).join(' ')}
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
  )
}

export function NativeIntegrationActionButtons({
  provider,
  pendingKind,
  disabled,
  onConnect,
  onDisconnect,
  onPicker,
  disconnectTone,
  minWidth = true,
  surface = 'standalone',
}: {
  provider: NativeIntegrationProvider
  pendingKind: NativePendingKind
  disabled: boolean
  onConnect: () => void
  onDisconnect: () => void
  onPicker: () => void
  disconnectTone?: 'danger' | 'neutral'
  minWidth?: boolean
  surface?: SettingsSurfaceVariant
}) {
  const pickerEnabled = provider.connected && supportsNativeIntegrationPicker(provider)
  return (
    <>
      {pickerEnabled ? (
        <NativeIntegrationPickerButton
          pendingKind={pendingKind}
          disabled={disabled}
          onClick={onPicker}
          minWidth={minWidth}
          surface={surface}
        />
      ) : null}
      <NativeIntegrationConnectionButton
        connected={provider.connected}
        pendingKind={pendingKind}
        disabled={disabled}
        onConnect={onConnect}
        onDisconnect={onDisconnect}
        disconnectTone={disconnectTone}
        minWidth={minWidth}
        surface={surface}
      />
    </>
  )
}

export function NativeIntegrationGridRow({
  provider,
  pendingKind,
  disabled,
  onConnect,
  onDisconnect,
  onPicker,
  gridClassName = 'grid gap-3 sm:grid-cols-[minmax(0,1fr)_7rem_8rem_8rem] sm:items-start',
  showStatusColumn = true,
  showNativeBadge = true,
  showConnectedBadge = false,
  surface = 'standalone',
}: {
  provider: NativeIntegrationProvider
  pendingKind: NativePendingKind
  disabled: boolean
  onConnect: () => void
  onDisconnect: () => void
  onPicker: () => void
  gridClassName?: string
  showStatusColumn?: boolean
  showNativeBadge?: boolean
  showConnectedBadge?: boolean
  surface?: SettingsSurfaceVariant
}) {
  const pickerEnabled = provider.connected && supportsNativeIntegrationPicker(provider)
  return (
    <div className="px-4 py-3">
      <div className={gridClassName}>
        <NativeIntegrationSummaryCell
          provider={provider}
          showNativeBadge={showNativeBadge}
          showConnectedBadge={showConnectedBadge}
          surface={surface}
        />
        {showStatusColumn ? (
          <div>
            <NativeIntegrationStatusBadge connected={provider.connected} surface={surface} />
          </div>
        ) : null}
        <div className="flex justify-start md:justify-end">
          {pickerEnabled ? (
            <NativeIntegrationPickerButton pendingKind={pendingKind} disabled={disabled} onClick={onPicker} surface={surface} />
          ) : null}
        </div>
        <div className="flex justify-start md:justify-end">
          <NativeIntegrationConnectionButton
            connected={provider.connected}
            pendingKind={pendingKind}
            disabled={disabled}
            onConnect={onConnect}
            onDisconnect={onDisconnect}
            surface={surface}
          />
        </div>
      </div>
      <NativeIntegrationFilesDisclosure provider={provider} surface={surface} />
    </div>
  )
}

export function NativeIntegrationFilesDisclosure({
  provider,
  surface = 'standalone',
}: {
  provider: NativeIntegrationProvider
  surface?: SettingsSurfaceVariant
}) {
  const [expanded, setExpanded] = useState(false)
  const fileListEnabled = supportsNativeIntegrationFileList(provider)
  const filesQuery = useQuery({
    queryKey: nativeIntegrationFilesQueryKey(provider),
    queryFn: () => fetchNativeIntegrationFiles(provider.filesUrl),
    enabled: expanded && fileListEnabled,
  })

  if (!fileListEnabled) {
    return null
  }

  const files = filesQuery.data?.files ?? []
  const disclosureClassName = surface === 'embedded'
    ? 'text-slate-300 hover:text-slate-100'
    : 'text-slate-700 hover:text-slate-950'
  const loadingClassName = surface === 'embedded' ? 'text-slate-400' : 'text-slate-500'
  const errorClassName = surface === 'embedded'
    ? 'border-rose-300/25 bg-rose-950/35 text-rose-200'
    : 'border-red-200 bg-red-50 text-red-700'
  const emptyClassName = surface === 'embedded'
    ? 'border-slate-200/20 bg-slate-950/30 text-slate-400'
    : 'border-slate-200 bg-white text-slate-600'

  return (
    <div className="mt-3 pl-12">
      <button
        type="button"
        className={`inline-flex items-center gap-2 text-sm font-semibold transition ${disclosureClassName}`}
        onClick={() => setExpanded((current) => !current)}
        aria-expanded={expanded}
      >
        {expanded ? (
          <ChevronDown className="h-4 w-4" aria-hidden="true" />
        ) : (
          <ChevronRight className="h-4 w-4" aria-hidden="true" />
        )}
        Accessible files
      </button>
      {expanded ? (
        <div className="mt-3">
          {filesQuery.isLoading ? (
            <div className={`inline-flex items-center gap-2 text-sm ${loadingClassName}`}>
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              Loading files...
            </div>
          ) : filesQuery.isError ? (
            <div className={`rounded-lg border px-3 py-2 text-sm ${errorClassName}`}>
              {safeErrorMessage(filesQuery.error)}
            </div>
          ) : files.length > 0 ? (
            <ul className="space-y-1">
              {files.map((file) => (
                <NativeIntegrationFileItem key={file.externalId} file={file} surface={surface} />
              ))}
            </ul>
          ) : (
            <div className={`rounded-lg border px-3 py-2 text-sm ${emptyClassName}`}>
              No selected Google Docs or Sheets files found.
            </div>
          )}
        </div>
      ) : null}
    </div>
  )
}

function NativeIntegrationFileItem({
  file,
  surface = 'standalone',
}: {
  file: NativeIntegrationAccessibleFile
  surface?: SettingsSurfaceVariant
}) {
  const textClassName = surface === 'embedded' ? 'text-slate-300 hover:text-slate-100' : 'text-slate-700 hover:text-slate-950'
  const icon = file.mimeType === GOOGLE_SHEETS_MIME_TYPE
    ? <Table2 className="h-4 w-4 text-emerald-700" aria-hidden="true" />
    : <FileText className="h-4 w-4 text-blue-700" aria-hidden="true" />
  const content = (
    <>
      {icon}
      <span className="truncate">{file.name}</span>
    </>
  )

  if (file.webUrl) {
    return (
      <li>
        <a
          href={file.webUrl}
          target="_blank"
          rel="noreferrer"
          className={`flex min-w-0 items-center gap-2 rounded-md px-2 py-1.5 text-sm transition ${textClassName}`}
        >
          {content}
        </a>
      </li>
    )
  }

  return (
    <li className={`flex min-w-0 items-center gap-2 rounded-md px-2 py-1.5 text-sm ${textClassName}`}>
      {content}
    </li>
  )
}

async function loadGooglePickerApi(): Promise<void> {
  if (googlePickerApiPromise) {
    return googlePickerApiPromise
  }
  googlePickerApiPromise = new Promise((resolve, reject) => {
    const loadPicker = () => {
      if (!window.gapi) {
        reject(new Error('Google Picker failed to load.'))
        return
      }
      window.gapi.load('picker', {
        callback: () => resolve(),
      })
    }

    if (window.gapi) {
      loadPicker()
      return
    }

    const existingScript = document.querySelector<HTMLScriptElement>('script[data-google-api-script="true"]')
    if (existingScript) {
      existingScript.addEventListener('load', loadPicker, { once: true })
      existingScript.addEventListener('error', () => reject(new Error('Google Picker failed to load.')), { once: true })
      return
    }

    const script = document.createElement('script')
    script.src = 'https://apis.google.com/js/api.js'
    script.async = true
    script.defer = true
    script.dataset.googleApiScript = 'true'
    script.onload = loadPicker
    script.onerror = () => reject(new Error('Google Picker failed to load.'))
    document.head.appendChild(script)
  })
  return googlePickerApiPromise
}

export async function openGoogleDrivePicker(token: NativeIntegrationPickerTokenResponse): Promise<NativeIntegrationAccessibleFile[]> {
  await loadGooglePickerApi()
  const picker = window.google?.picker
  if (!picker) {
    throw new Error('Google Picker is unavailable.')
  }

  return new Promise((resolve) => {
    let settled = false
    const finish = (selectedFiles: NativeIntegrationAccessibleFile[]) => {
      if (settled) {
        return
      }
      settled = true
      window.clearTimeout(timeoutId)
      resolve(selectedFiles)
    }
    const timeoutId = window.setTimeout(() => finish([]), 5 * 60 * 1000)
    const view = new picker.DocsView(picker.ViewId.DOCS).setMimeTypes(
      `${GOOGLE_SHEETS_MIME_TYPE},${GOOGLE_DOCS_MIME_TYPE}`,
    )
    const pickerInstance = new picker.PickerBuilder()
      .addView(view)
      .setOAuthToken(token.accessToken)
      .setDeveloperKey(token.developerKey)
      .setAppId(token.appId)
      .enableFeature(picker.Feature.MULTISELECT_ENABLED)
      .setCallback((data) => {
        const action = data[picker.Response.ACTION]
        if (action === picker.Action.PICKED) {
          const docs = data[picker.Response.DOCUMENTS]
          finish(normalizeSelectedPickerDocs(docs, picker))
        } else if (picker.Action.CANCEL && action === picker.Action.CANCEL) {
          finish([])
        } else if (typeof action === 'string' && action) {
          finish([])
        }
      })
      .build()

    pickerInstance.setVisible(true)
  })
}

function normalizeSelectedPickerDocs(
  docs: unknown,
  picker: GooglePickerNamespace,
): NativeIntegrationAccessibleFile[] {
  if (!Array.isArray(docs)) {
    return []
  }
  return docs.reduce<NativeIntegrationAccessibleFile[]>((files, doc) => {
    if (!doc || typeof doc !== 'object') {
      return files
    }
    const rawDoc = doc as Record<string, unknown>
    const externalFileId = String(rawDoc[picker.Document.ID] ?? '').trim()
    const name = String(rawDoc[picker.Document.NAME] ?? '').trim()
    const mimeType = String(rawDoc[picker.Document.MIME_TYPE] ?? '').trim()
    const webUrl = String(rawDoc[picker.Document.URL] ?? '').trim()
    if (externalFileId && name && mimeType) {
      files.push({
        externalId: externalFileId,
        name,
        mimeType,
        webUrl,
      })
    }
    return files
  }, [])
}
