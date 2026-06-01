import { useMemo } from 'react'
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

  const connectMutation = useMutation({
    mutationFn: (provider: NativeIntegrationProvider) => startNativeIntegrationConnect(provider.connectUrl),
    onSuccess: (payload) => {
      storePendingNativeOAuth(payload.state, {
        providerKey: payload.providerKey,
        returnUrl: window.location.href,
        createdAt: Date.now(),
        context: readStoredConsoleContext(),
      })
      window.location.href = payload.authorizationUrl
    },
    onError: (error) => {
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

  const sectionClassName = embedded
    ? 'settings-card-surface settings-card-surface--embedded overflow-hidden rounded-xl border border-slate-200/20'
    : 'gobii-card-base overflow-hidden'
  const headerClassName = embedded
    ? 'flex flex-col gap-3 px-6 py-4 sm:flex-row sm:items-center sm:justify-between'
    : 'flex flex-col gap-3 border-b border-gray-200/70 px-6 py-4 sm:flex-row sm:items-center sm:justify-between'
  const badgeClassName = embedded
    ? 'inline-flex items-center gap-2 rounded-full border border-emerald-300/25 bg-emerald-950/45 px-3 py-1 text-xs font-semibold uppercase tracking-wide text-emerald-100'
    : 'inline-flex items-center gap-2 rounded-full border border-emerald-200 bg-emerald-50 px-3 py-1 text-xs font-semibold uppercase tracking-wide text-emerald-700'
  const titleClassName = embedded ? 'text-lg font-semibold text-slate-50' : 'text-lg font-semibold text-gray-800'
  const captionClassName = embedded ? 'text-sm text-slate-400' : 'text-sm text-gray-600'
  const bodyClassName = embedded ? 'divide-y divide-slate-200/10' : 'divide-y divide-gray-200/70'
  const rowClassName = embedded
    ? 'flex flex-col gap-4 px-6 py-5 sm:flex-row sm:items-center sm:justify-between'
    : 'flex flex-col gap-4 px-6 py-5 sm:flex-row sm:items-center sm:justify-between'
  const iconShellClassName = embedded
    ? 'flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-emerald-300/25 bg-emerald-950/35 text-emerald-100'
    : 'flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-emerald-200 bg-emerald-50 text-emerald-700'
  const providerTitleClassName = embedded ? 'text-sm font-semibold text-slate-100' : 'text-sm font-semibold text-slate-900'
  const providerDescriptionClassName = embedded ? 'text-sm text-slate-400' : 'text-sm text-slate-600'
  const statusClassName = embedded
    ? 'inline-flex items-center gap-1.5 rounded-full border border-slate-200/20 bg-slate-950/25 px-2.5 py-1 text-xs font-semibold text-slate-200'
    : 'inline-flex items-center gap-1.5 rounded-full border border-slate-200 px-2.5 py-1 text-xs font-semibold text-slate-700'
  const connectButtonClassName = embedded
    ? 'inline-flex items-center justify-center gap-2 rounded-lg border border-emerald-300/25 bg-emerald-900/55 px-4 py-2 text-sm font-semibold text-emerald-50 transition hover:border-emerald-200/40 hover:bg-emerald-900/75 disabled:cursor-not-allowed disabled:opacity-60'
    : 'inline-flex items-center justify-center gap-2 rounded-lg bg-emerald-600 px-4 py-2 text-sm font-semibold text-white shadow transition hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-60'
  const disconnectButtonClassName = embedded
    ? 'inline-flex items-center justify-center gap-2 rounded-lg border border-slate-200/20 bg-slate-950/20 px-4 py-2 text-sm font-semibold text-slate-200 transition hover:border-slate-100/35 hover:bg-slate-900/40 disabled:cursor-not-allowed disabled:opacity-60'
    : 'inline-flex items-center justify-center gap-2 rounded-lg border border-slate-200 px-4 py-2 text-sm font-semibold text-slate-700 transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60'
  const pickerButtonClassName = embedded
    ? 'inline-flex items-center justify-center gap-2 rounded-lg border border-emerald-300/25 bg-emerald-900/35 px-4 py-2 text-sm font-semibold text-emerald-50 transition hover:border-emerald-200/40 hover:bg-emerald-900/55 disabled:cursor-not-allowed disabled:opacity-60'
    : 'inline-flex items-center justify-center gap-2 rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-2 text-sm font-semibold text-emerald-700 transition hover:bg-emerald-100 disabled:cursor-not-allowed disabled:opacity-60'
  const loadingClassName = embedded
    ? 'flex items-center gap-2 px-6 py-8 text-sm text-slate-400'
    : 'flex items-center gap-2 px-6 py-8 text-sm text-slate-500'
  const errorClassName = embedded
    ? 'rounded-xl border border-rose-300/25 bg-rose-950/30 px-4 py-3 text-sm text-rose-100'
    : 'rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700'
  const pendingProviderKey =
    (connectMutation.isPending ? connectMutation.variables?.providerKey : null) ??
    (revokeMutation.isPending ? revokeMutation.variables?.providerKey : null) ??
    (pickerMutation.isPending ? pickerMutation.variables?.providerKey : null) ??
    null

  return (
    <section className={sectionClassName}>
      <div className={headerClassName}>
        <div className="space-y-1">
          <div className={badgeClassName}>
            <Plug className="h-3.5 w-3.5" aria-hidden="true" />
            Native Apps
          </div>
          <div>
            <h2 className={titleClassName}>Native apps</h2>
            <p className={captionClassName}>Connect first-party app credentials for agent API calls.</p>
          </div>
        </div>
      </div>

      {integrationsQuery.isLoading ? (
        <div className={loadingClassName}>
          <Loader2 className="h-4 w-4 animate-spin" />
          Loading native apps...
        </div>
      ) : integrationsQuery.isError ? (
        <div className="px-6 py-5">
          <div className={errorClassName}>
            {integrationsQuery.error instanceof Error ? integrationsQuery.error.message : 'Unable to load native apps.'}
          </div>
        </div>
      ) : (
        <div className={bodyClassName}>
          {(integrationsQuery.data?.providers ?? []).map((provider) => {
            const isBusy = pendingProviderKey === provider.providerKey
            return (
              <div key={provider.providerKey}>
                <div className={rowClassName}>
                  <div className="flex min-w-0 gap-3">
                    <div className={iconShellClassName}>
                      <ProviderIcon provider={provider} />
                    </div>
                    <div className="min-w-0 space-y-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <h3 className={providerTitleClassName}>{provider.displayName}</h3>
                        <span className={statusClassName}>
                          {provider.connected ? (
                            <CheckCircle2 className="h-3.5 w-3.5 text-emerald-300" aria-hidden="true" />
                          ) : (
                            <Plug className="h-3.5 w-3.5" aria-hidden="true" />
                          )}
                          {provider.connected ? 'Connected' : 'Not connected'}
                        </span>
                      </div>
                      <p className={providerDescriptionClassName}>{provider.description}</p>
                    </div>
                  </div>
                  <div className="flex shrink-0 flex-wrap gap-2">
                    {provider.connected ? (
                      <>
                        <button
                          type="button"
                          className={pickerButtonClassName}
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
                          className={disconnectButtonClassName}
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
                        className={connectButtonClassName}
                        onClick={() => connectMutation.mutate(provider)}
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
