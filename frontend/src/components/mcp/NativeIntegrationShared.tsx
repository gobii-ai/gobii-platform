import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ChevronDown, ChevronRight, FileText, Loader2, Plug, Table2 } from 'lucide-react'

import type {
  NativeIntegrationAccessibleFile,
  NativeIntegrationPickerTokenResponse,
  NativeIntegrationProvider,
} from '../../api/nativeIntegrations'
import { fetchNativeIntegrationFiles } from '../../api/nativeIntegrations'
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
  slack: {
    className: 'h-5 w-5 object-contain',
    framedClassName: 'h-6 w-6 object-contain',
    src: '/static/images/integrations/native/slack.svg',
    tileClassName: 'inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-fuchsia-200 bg-fuchsia-50 text-fuchsia-700',
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

export function NativeIntegrationFilesDisclosure({ provider }: { provider: NativeIntegrationProvider }) {
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

  return (
    <div className="mt-3 pl-12">
      <button
        type="button"
        className="inline-flex items-center gap-2 text-sm font-semibold text-slate-700 transition hover:text-slate-950"
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
            <div className="inline-flex items-center gap-2 text-sm text-slate-500">
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              Loading files...
            </div>
          ) : filesQuery.isError ? (
            <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
              {safeErrorMessage(filesQuery.error)}
            </div>
          ) : files.length > 0 ? (
            <ul className="space-y-1">
              {files.map((file) => (
                <NativeIntegrationFileItem key={file.externalId} file={file} />
              ))}
            </ul>
          ) : (
            <div className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-600">
              No selected Google Docs or Sheets files found.
            </div>
          )}
        </div>
      ) : null}
    </div>
  )
}

function NativeIntegrationFileItem({ file }: { file: NativeIntegrationAccessibleFile }) {
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
          className="flex min-w-0 items-center gap-2 rounded-md px-2 py-1.5 text-sm text-slate-700 transition hover:text-slate-950"
        >
          {content}
        </a>
      </li>
    )
  }

  return (
    <li className="flex min-w-0 items-center gap-2 rounded-md px-2 py-1.5 text-sm text-slate-700">
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
