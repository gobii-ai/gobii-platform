import { useEffect, useMemo } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  CheckCircle2,
  FolderOpen,
  HardDrive,
  Loader2,
  Plug,
  Unplug,
} from 'lucide-react'

import {
  fetchNativeIntegrationPickerToken,
  fetchNativeIntegrations,
  revokeNativeIntegration,
  startNativeIntegrationConnect,
  type NativeIntegrationFileSelection,
  type NativeIntegrationPickerTokenResponse,
  type NativeIntegrationProvider,
} from '../../api/nativeIntegrations'
import { safeErrorMessage } from '../../api/safeErrorMessage'
import { readStoredConsoleContext } from '../../util/consoleContextStorage'

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

const GOOGLE_SHEETS_MIME_TYPE = 'application/vnd.google-apps.spreadsheet'
const GOOGLE_DOCS_MIME_TYPE = 'application/vnd.google-apps.document'
const NATIVE_OAUTH_COMPLETE_MESSAGE = 'gobii:native-oauth-complete'
const NATIVE_OAUTH_COMPLETE_PREFIX = 'gobii:native_oauth_complete:'
const BUTTON_CLASS_NAME =
  'inline-flex items-center justify-center gap-2 rounded-lg px-4 py-2 text-sm font-semibold transition disabled:cursor-not-allowed disabled:opacity-60'
const ROW_CLASS_NAME = 'flex flex-col gap-4 px-6 py-5 sm:flex-row sm:items-center sm:justify-between'

const PANEL_CLASSES = {
  embedded: {
    section: 'settings-card-surface settings-card-surface--embedded overflow-hidden rounded-xl border border-slate-200/20',
    header: 'flex flex-col gap-3 px-6 py-4 sm:flex-row sm:items-center sm:justify-between',
    badge:
      'inline-flex items-center gap-2 rounded-full border border-emerald-300/25 bg-emerald-950/45 px-3 py-1 text-xs font-semibold uppercase tracking-wide text-emerald-100',
    title: 'text-lg font-semibold text-slate-50',
    caption: 'text-sm text-slate-400',
    body: 'divide-y divide-slate-200/10',
    iconShell:
      'flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-emerald-300/25 bg-emerald-950/35 text-emerald-100',
    providerTitle: 'text-sm font-semibold text-slate-100',
    providerDescription: 'text-sm text-slate-400',
    status:
      'inline-flex items-center gap-1.5 rounded-full border border-slate-200/20 bg-slate-950/25 px-2.5 py-1 text-xs font-semibold text-slate-200',
    connectButton: `${BUTTON_CLASS_NAME} border border-emerald-300/25 bg-emerald-900/55 text-emerald-50 hover:border-emerald-200/40 hover:bg-emerald-900/75`,
    disconnectButton: `${BUTTON_CLASS_NAME} border border-slate-200/20 bg-slate-950/20 text-slate-200 hover:border-slate-100/35 hover:bg-slate-900/40`,
    pickerButton: `${BUTTON_CLASS_NAME} border border-emerald-300/25 bg-emerald-900/35 text-emerald-50 hover:border-emerald-200/40 hover:bg-emerald-900/55`,
    loading: 'flex items-center gap-2 px-6 py-8 text-sm text-slate-400',
    error: 'rounded-xl border border-rose-300/25 bg-rose-950/30 px-4 py-3 text-sm text-rose-100',
  },
  standalone: {
    section: 'gobii-card-base overflow-hidden',
    header: 'flex flex-col gap-3 border-b border-gray-200/70 px-6 py-4 sm:flex-row sm:items-center sm:justify-between',
    badge:
      'inline-flex items-center gap-2 rounded-full border border-emerald-200 bg-emerald-50 px-3 py-1 text-xs font-semibold uppercase tracking-wide text-emerald-700',
    title: 'text-lg font-semibold text-gray-800',
    caption: 'text-sm text-gray-600',
    body: 'divide-y divide-gray-200/70',
    iconShell:
      'flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-emerald-200 bg-emerald-50 text-emerald-700',
    providerTitle: 'text-sm font-semibold text-slate-900',
    providerDescription: 'text-sm text-slate-600',
    status:
      'inline-flex items-center gap-1.5 rounded-full border border-slate-200 px-2.5 py-1 text-xs font-semibold text-slate-700',
    connectButton: `${BUTTON_CLASS_NAME} bg-emerald-600 text-white shadow hover:bg-emerald-700`,
    disconnectButton: `${BUTTON_CLASS_NAME} border border-slate-200 text-slate-700 hover:bg-slate-50`,
    pickerButton: `${BUTTON_CLASS_NAME} border border-emerald-200 bg-emerald-50 text-emerald-700 hover:bg-emerald-100`,
    loading: 'flex items-center gap-2 px-6 py-8 text-sm text-slate-500',
    error: 'rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700',
  },
} as const

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

let googlePickerApiPromise: Promise<void> | null = null

type NativeAppsPanelProps = {
  listUrl: string
  onSuccess: (message: string) => void
  onError: (message: string) => void
  embedded?: boolean
}

type NativeConnectVariables = {
  provider: NativeIntegrationProvider
  popup: Window | null
}

type NativeOAuthCompleteMessage = {
  type?: unknown
  providerKey?: unknown
  ok?: unknown
  error?: unknown
}

export function NativeAppsPanel({
  listUrl,
  onSuccess,
  onError,
  embedded = false,
}: NativeAppsPanelProps) {
  const queryClient = useQueryClient()
  const queryKey = useMemo(() => ['native-integrations', listUrl] as const, [listUrl])
  const integrationsQuery = useQuery({
    queryKey,
    queryFn: () => fetchNativeIntegrations(listUrl),
  })

  useEffect(() => {
    const handleComplete = (payload: NativeOAuthCompleteMessage) => {
      if (!payload || payload.type !== NATIVE_OAUTH_COMPLETE_MESSAGE) {
        return
      }

      queryClient.invalidateQueries({ queryKey })
      if (payload.ok) {
        const provider = integrationsQuery.data?.providers.find(
          (candidate) => candidate.providerKey === payload.providerKey,
        )
        onSuccess(`${provider?.displayName ?? 'Native app'} connected.`)
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
  }, [integrationsQuery.data?.providers, onError, onSuccess, queryClient, queryKey])

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const result = params.get('native_oauth')
    if (!result) {
      return
    }

    queryClient.invalidateQueries({ queryKey })
    if (result === 'success') {
      onSuccess('Native app connected.')
    } else if (result === 'error') {
      onError('Unable to complete the native app connection.')
    }
    params.delete('native_oauth')
    const nextSearch = params.toString()
    const nextUrl = `${window.location.pathname}${nextSearch ? `?${nextSearch}` : ''}${window.location.hash}`
    window.history.replaceState(window.history.state, '', nextUrl)
  }, [onError, onSuccess, queryClient, queryKey])

  const connectMutation = useMutation({
    mutationFn: ({ provider }: NativeConnectVariables) => startNativeIntegrationConnect(provider.connectUrl),
    onSuccess: (payload, { popup }) => {
      storePendingNativeOAuth(payload.state, {
        providerKey: payload.providerKey,
        returnUrl: window.location.href,
        createdAt: Date.now(),
        context: readStoredConsoleContext(),
        popup: Boolean(popup),
      })
      if (popup && !popup.closed) {
        popup.location.href = payload.authorizationUrl
        popup.focus()
        return
      }
      if (popup?.closed) {
        onError('Connection window was closed before Google opened.')
        return
      }
      window.location.href = payload.authorizationUrl
    },
    onError: (error, { popup }) => {
      if (popup && !popup.closed) {
        popup.close()
      }
      onError(safeErrorMessage(error))
    },
  })

  const revokeMutation = useMutation({
    mutationFn: (provider: NativeIntegrationProvider) => revokeNativeIntegration(provider.revokeUrl).then(() => provider),
    onSuccess: (provider) => {
      queryClient.invalidateQueries({ queryKey })
      onSuccess(`${provider.displayName} disconnected.`)
    },
    onError: (error) => {
      onError(safeErrorMessage(error))
    },
  })

  const pickerMutation = useMutation({
    mutationFn: async (provider: NativeIntegrationProvider) => {
      const token = await fetchNativeIntegrationPickerToken(provider.pickerTokenUrl)
      const selectedFiles = await openGoogleDrivePicker(token)
      return { provider, selectedCount: selectedFiles.length }
    },
    onSuccess: ({ provider, selectedCount }) => {
      if (selectedCount > 0) {
        onSuccess(
          `${selectedCount} Google Drive file${selectedCount === 1 ? '' : 's'} selected for ${provider.displayName}.`,
        )
      }
    },
    onError: (error) => {
      onError(safeErrorMessage(error))
    },
  })

  const classes = embedded ? PANEL_CLASSES.embedded : PANEL_CLASSES.standalone
  const pendingProviderKey =
    (connectMutation.isPending ? connectMutation.variables?.provider.providerKey : null) ??
    (revokeMutation.isPending ? revokeMutation.variables?.providerKey : null) ??
    (pickerMutation.isPending ? pickerMutation.variables?.providerKey : null) ??
    null

  return (
    <section className={classes.section}>
      <div className={classes.header}>
        <div className="space-y-1">
          <div className={classes.badge}>
            <Plug className="h-3.5 w-3.5" aria-hidden="true" />
            Native Apps
          </div>
          <div>
            <h2 className={classes.title}>Native apps</h2>
            <p className={classes.caption}>Connect first-party app credentials for agent API calls.</p>
          </div>
        </div>
      </div>

      {integrationsQuery.isLoading ? (
        <div className={classes.loading}>
          <Loader2 className="h-4 w-4 animate-spin" />
          Loading native apps...
        </div>
      ) : integrationsQuery.isError ? (
        <div className="px-6 py-5">
          <div className={classes.error}>
            {integrationsQuery.error instanceof Error ? integrationsQuery.error.message : 'Unable to load native apps.'}
          </div>
        </div>
      ) : (
        <div className={classes.body}>
          {(integrationsQuery.data?.providers ?? []).map((provider) => {
            const isBusy = pendingProviderKey === provider.providerKey
            return (
              <div key={provider.providerKey}>
                <div className={ROW_CLASS_NAME}>
                  <div className="flex min-w-0 gap-3">
                    <div className={classes.iconShell}>
                      <ProviderIcon provider={provider} />
                    </div>
                    <div className="min-w-0 space-y-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <h3 className={classes.providerTitle}>{provider.displayName}</h3>
                        <span className={classes.status}>
                          {provider.connected ? (
                            <CheckCircle2 className="h-3.5 w-3.5 text-emerald-300" aria-hidden="true" />
                          ) : (
                            <Plug className="h-3.5 w-3.5" aria-hidden="true" />
                          )}
                          {provider.connected ? 'Connected' : 'Not connected'}
                        </span>
                      </div>
                      <p className={classes.providerDescription}>{provider.description}</p>
                    </div>
                  </div>
                  <div className="flex shrink-0 flex-wrap gap-2">
                    {provider.connected ? (
                      <>
                        <button
                          type="button"
                          className={classes.pickerButton}
                          onClick={() => pickerMutation.mutate(provider)}
                          disabled={isBusy}
                        >
                          {isBusy && pickerMutation.variables?.providerKey === provider.providerKey ? (
                            <Loader2 className="h-4 w-4 animate-spin" />
                          ) : (
                            <FolderOpen className="h-4 w-4" />
                          )}
                          Choose files
                        </button>
                        <button
                          type="button"
                          className={classes.disconnectButton}
                          onClick={() => revokeMutation.mutate(provider)}
                          disabled={isBusy}
                        >
                          {isBusy && revokeMutation.variables?.providerKey === provider.providerKey ? (
                            <Loader2 className="h-4 w-4 animate-spin" />
                          ) : (
                            <Unplug className="h-4 w-4" />
                          )}
                          Disconnect
                        </button>
                      </>
                    ) : (
                      <button
                        type="button"
                        className={classes.connectButton}
                        onClick={() => connectMutation.mutate({ provider, popup: openNativeOAuthPopup(provider) })}
                        disabled={isBusy}
                      >
                        {isBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plug className="h-4 w-4" />}
                        Connect
                      </button>
                    )}
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      )}
    </section>
  )
}

function ProviderIcon({ provider }: { provider: NativeIntegrationProvider }) {
  if (provider.icon === 'google_drive') {
    return <HardDrive className="h-5 w-5" aria-hidden="true" />
  }
  return <Plug className="h-5 w-5" aria-hidden="true" />
}

function storePendingNativeOAuth(state: string, payload: Record<string, unknown>) {
  try {
    localStorage.setItem(`gobii:native_oauth_state:${state}`, JSON.stringify(payload))
  } catch (error) {
    console.warn('Failed to persist native integration OAuth state', error)
  }
}

function openNativeOAuthPopup(provider: NativeIntegrationProvider): Window | null {
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

function loadGooglePickerApi(): Promise<void> {
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

async function openGoogleDrivePicker(token: NativeIntegrationPickerTokenResponse): Promise<NativeIntegrationFileSelection[]> {
  await loadGooglePickerApi()
  const picker = window.google?.picker
  if (!picker) {
    throw new Error('Google Picker is unavailable.')
  }

  return new Promise((resolve) => {
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
          if (!Array.isArray(docs)) {
            resolve([])
            return
          }
          resolve(
            docs.flatMap((doc) => {
              if (!doc || typeof doc !== 'object') {
                return []
              }
              const rawDoc = doc as Record<string, unknown>
              const externalFileId = String(rawDoc[picker.Document.ID] ?? '').trim()
              const name = String(rawDoc[picker.Document.NAME] ?? '').trim()
              const mimeType = String(rawDoc[picker.Document.MIME_TYPE] ?? '').trim()
              const url = String(rawDoc[picker.Document.URL] ?? '').trim()
              if (!externalFileId || !name || !mimeType) {
                return []
              }
              return [{ externalFileId, name, mimeType, url }]
            }),
          )
        } else if (picker.Action.CANCEL && action === picker.Action.CANCEL) {
          resolve([])
        }
      })
      .build()

    pickerInstance.setVisible(true)
  })
}
