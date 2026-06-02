import { useMemo, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { CheckCircle2, FolderOpen, Loader2, Plug } from 'lucide-react'

import {
  fetchNativeIntegrationPickerToken,
  fetchNativeIntegrations,
  startNativeIntegrationConnect,
  type NativeIntegrationProvider,
} from '../../../api/nativeIntegrations'
import { safeErrorMessage } from '../../../api/safeErrorMessage'
import {
  NativeProviderIcon,
  nativeOAuthContextPayload,
  openGoogleDrivePicker,
  openNativeOAuthPopup,
  storePendingNativeOAuth,
  supportsNativeIntegrationPicker,
  useNativeIntegrationRefreshEffects,
} from '../../mcp/NativeIntegrationShared'

const GOOGLE_DRIVE_PROVIDER_KEY = 'google_drive'

type GoogleDriveInsightPanelProps = {
  nativeIntegrationsUrl?: string | null
}

type PendingAction = 'connect' | 'picker' | null

export function GoogleDriveInsightPanel({ nativeIntegrationsUrl = null }: GoogleDriveInsightPanelProps) {
  const [statusMessage, setStatusMessage] = useState<string | null>(null)
  const [pendingAction, setPendingAction] = useState<PendingAction>(null)
  const nativeQueryKey = useMemo(
    () => ['native-integrations', nativeIntegrationsUrl] as const,
    [nativeIntegrationsUrl],
  )

  useNativeIntegrationRefreshEffects({
    queryKey: nativeQueryKey,
    onError: setStatusMessage,
  })

  const nativeIntegrationsQuery = useQuery({
    queryKey: nativeQueryKey,
    queryFn: () => fetchNativeIntegrations(nativeIntegrationsUrl as string),
    enabled: Boolean(nativeIntegrationsUrl),
  })

  const provider = (nativeIntegrationsQuery.data?.providers ?? []).find(
    (candidate) => candidate.providerKey === GOOGLE_DRIVE_PROVIDER_KEY,
  ) ?? null

  const connectMutation = useMutation({
    mutationFn: ({ provider }: { provider: NativeIntegrationProvider; popup: Window | null }) =>
      startNativeIntegrationConnect(provider.connectUrl),
    onMutate: () => {
      setPendingAction('connect')
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
        setStatusMessage('Connection window was closed before Google opened.')
        return
      }
      window.location.href = payload.authorizationUrl
    },
    onError: (error, { popup }) => {
      if (popup && !popup.closed) {
        popup.close()
      }
      setStatusMessage(safeErrorMessage(error))
    },
    onSettled: () => setPendingAction(null),
  })

  const pickerMutation = useMutation({
    mutationFn: async (provider: NativeIntegrationProvider) => {
      const token = await fetchNativeIntegrationPickerToken(provider.pickerTokenUrl)
      const selectedCount = await openGoogleDrivePicker(token)
      return { provider, selectedCount }
    },
    onMutate: () => {
      setPendingAction('picker')
      setStatusMessage(null)
    },
    onSuccess: ({ selectedCount }) => {
      setStatusMessage(selectedCount > 0 ? 'Selected files are now available to this agent.' : null)
    },
    onError: (error) => {
      setStatusMessage(safeErrorMessage(error))
    },
    onSettled: () => setPendingAction(null),
  })

  if (!nativeIntegrationsUrl) {
    return (
      <section className="google-drive-insight-panel" aria-label="Google Drive">
        <GoogleDrivePanelHeader connected={false} />
        <p className="google-drive-insight-panel__text">
          Google Drive setup is unavailable in this workspace.
        </p>
      </section>
    )
  }

  if (nativeIntegrationsQuery.isLoading) {
    return (
      <section className="google-drive-insight-panel" aria-label="Google Drive">
        <GoogleDrivePanelHeader connected={false} />
        <div className="google-drive-insight-panel__inline-status">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          Loading Google Drive…
        </div>
      </section>
    )
  }

  if (nativeIntegrationsQuery.isError || !provider) {
    return (
      <section className="google-drive-insight-panel" aria-label="Google Drive">
        <GoogleDrivePanelHeader connected={false} />
        <p className="google-drive-insight-panel__error">
          {nativeIntegrationsQuery.isError ? safeErrorMessage(nativeIntegrationsQuery.error) : 'Google Drive is not configured.'}
        </p>
      </section>
    )
  }

  const busy = connectMutation.isPending || pickerMutation.isPending || pendingAction !== null
  const pickerEnabled = provider.connected && supportsNativeIntegrationPicker(provider)

  return (
    <section className="google-drive-insight-panel" aria-label="Google Drive">
      <GoogleDrivePanelHeader provider={provider} connected={provider.connected} />
      <div className="google-drive-insight-panel__body">
        <div className="google-drive-insight-panel__copy">
          <p className="google-drive-insight-panel__title">
            {provider.connected ? 'Google Drive connected' : 'Connect Google Drive'}
          </p>
          <p className="google-drive-insight-panel__text">
            {provider.connected
              ? 'Choose Sheets files this agent can read or update.'
              : 'Connect Drive so this agent can work with selected Google Sheets.'}
          </p>
        </div>
        <div className="google-drive-insight-panel__actions">
          {provider.connected ? (
            <button
              type="button"
              className="google-drive-insight-panel__button google-drive-insight-panel__button--secondary"
              onClick={() => pickerMutation.mutate(provider)}
              disabled={busy || !pickerEnabled}
            >
              {pendingAction === 'picker' ? (
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              ) : (
                <FolderOpen className="h-4 w-4" aria-hidden="true" />
              )}
              Select files
            </button>
          ) : (
            <button
              type="button"
              className="google-drive-insight-panel__button"
              onClick={() => connectMutation.mutate({ provider, popup: openNativeOAuthPopup(provider) })}
              disabled={busy}
            >
              {pendingAction === 'connect' ? (
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              ) : (
                <Plug className="h-4 w-4" aria-hidden="true" />
              )}
              Connect
            </button>
          )}
        </div>
      </div>

      {statusMessage ? <p className="google-drive-insight-panel__status">{statusMessage}</p> : null}
    </section>
  )
}

function GoogleDrivePanelHeader({
  provider,
  connected,
}: {
  provider?: NativeIntegrationProvider | null
  connected: boolean
}) {
  return (
    <div className="google-drive-insight-panel__header">
      <span className="google-drive-insight-panel__icon" aria-hidden="true">
        {provider ? (
          <NativeProviderIcon provider={provider} />
        ) : (
          <img src="/static/images/integrations/native/google_drive.svg" alt="" className="h-5 w-5 object-contain" />
        )}
      </span>
      <span className="google-drive-insight-panel__label">Google Drive</span>
      {connected ? (
        <span className="google-drive-insight-panel__connected">
          <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" />
          Connected
        </span>
      ) : null}
    </div>
  )
}
