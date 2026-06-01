import { useEffect } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { HardDrive, Plug } from 'lucide-react'

import type {
  NativeIntegrationPickerTokenResponse,
  NativeIntegrationProvider,
} from '../../api/nativeIntegrations'
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

export function nativeOAuthContextPayload(providerKey: string, state: string, popup: Window | null) {
  return {
    providerKey,
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

export function NativeProviderIcon({ provider }: { provider: NativeIntegrationProvider }) {
  if (provider.icon === 'google_drive') {
    return <HardDrive className="h-5 w-5" aria-hidden="true" />
  }
  return <Plug className="h-5 w-5" aria-hidden="true" />
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

export async function openGoogleDrivePicker(token: NativeIntegrationPickerTokenResponse): Promise<number> {
  await loadGooglePickerApi()
  const picker = window.google?.picker
  if (!picker) {
    throw new Error('Google Picker is unavailable.')
  }

  return new Promise((resolve) => {
    let settled = false
    const finish = (selectedCount: number) => {
      if (settled) {
        return
      }
      settled = true
      window.clearTimeout(timeoutId)
      resolve(selectedCount)
    }
    const timeoutId = window.setTimeout(() => finish(0), 5 * 60 * 1000)
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
          finish(countSelectedPickerDocs(docs, picker))
        } else if (picker.Action.CANCEL && action === picker.Action.CANCEL) {
          finish(0)
        } else if (typeof action === 'string' && action) {
          finish(0)
        }
      })
      .build()

    pickerInstance.setVisible(true)
  })
}

function countSelectedPickerDocs(docs: unknown, picker: GooglePickerNamespace): number {
  if (!Array.isArray(docs)) {
    return 0
  }
  return docs.reduce((count, doc) => {
    if (!doc || typeof doc !== 'object') {
      return count
    }
    const rawDoc = doc as Record<string, unknown>
    const externalFileId = String(rawDoc[picker.Document.ID] ?? '').trim()
    const name = String(rawDoc[picker.Document.NAME] ?? '').trim()
    const mimeType = String(rawDoc[picker.Document.MIME_TYPE] ?? '').trim()
    return externalFileId && name && mimeType ? count + 1 : count
  }, 0)
}
