import type { Dispatch, ReactNode, SetStateAction } from 'react'
import { useMemo, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { CheckCircle2, Loader2, Plug } from 'lucide-react'

import {
  fetchNativeIntegrations,
  startNativeIntegrationConnect,
  type NativeIntegrationProvider,
} from '../../../api/nativeIntegrations'
import { safeErrorMessage } from '../../../api/safeErrorMessage'
import {
  NativeProviderIcon,
  nativeOAuthContextPayload,
  openNativeOAuthPopup,
  storePendingNativeOAuth,
  useNativeIntegrationRefreshEffects,
} from '../../mcp/NativeIntegrationShared'

export type NativeIntegrationPendingAction = string | null

type NativeIntegrationPanelState = {
  provider: NativeIntegrationProvider | null
  isLoading: boolean
  errorMessage: string | null
  statusMessage: string | null
  pendingAction: NativeIntegrationPendingAction
  connectPending: boolean
  setStatusMessage: Dispatch<SetStateAction<string | null>>
  setPendingAction: Dispatch<SetStateAction<NativeIntegrationPendingAction>>
  startConnect: (provider: NativeIntegrationProvider) => void
}

export function useNativeIntegrationPanelState({
  nativeIntegrationsUrl,
  providerKey,
  providerDisplayName,
}: {
  nativeIntegrationsUrl?: string | null
  providerKey: string
  providerDisplayName: string
}): NativeIntegrationPanelState {
  const [statusMessage, setStatusMessage] = useState<string | null>(null)
  const [pendingAction, setPendingAction] = useState<NativeIntegrationPendingAction>(null)
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
    (candidate) => candidate.providerKey === providerKey,
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
        setStatusMessage(`Connection window was closed before ${providerDisplayName} opened.`)
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

  return {
    provider,
    isLoading: nativeIntegrationsQuery.isLoading,
    errorMessage: nativeIntegrationsQuery.isError
      ? safeErrorMessage(nativeIntegrationsQuery.error)
      : null,
    statusMessage,
    pendingAction,
    connectPending: connectMutation.isPending,
    setStatusMessage,
    setPendingAction,
    startConnect: (provider) => connectMutation.mutate({ provider, popup: openNativeOAuthPopup(provider) }),
  }
}

type NativeIntegrationInsightPanelFrameProps = {
  ariaLabel: string
  providerLabel: string
  provider?: NativeIntegrationProvider | null
  connected: boolean
  fallbackIcon: ReactNode
  unavailableMessage?: string | null
  loadingMessage?: string | null
  errorMessage?: string | null
  notConfiguredMessage: string
  title?: string | null
  text?: string | null
  actions?: ReactNode
  statusMessage?: string | null
}

export function NativeIntegrationInsightPanelFrame({
  ariaLabel,
  providerLabel,
  provider = null,
  connected,
  fallbackIcon,
  unavailableMessage = null,
  loadingMessage = null,
  errorMessage = null,
  notConfiguredMessage,
  title = null,
  text = null,
  actions = null,
  statusMessage = null,
}: NativeIntegrationInsightPanelFrameProps) {
  if (unavailableMessage) {
    return (
      <section className="google-drive-insight-panel" aria-label={ariaLabel}>
        <NativeIntegrationPanelHeader
          provider={provider}
          providerLabel={providerLabel}
          connected={false}
          fallbackIcon={fallbackIcon}
        />
        <p className="google-drive-insight-panel__text">{unavailableMessage}</p>
      </section>
    )
  }

  if (loadingMessage) {
    return (
      <section className="google-drive-insight-panel" aria-label={ariaLabel}>
        <NativeIntegrationPanelHeader
          provider={provider}
          providerLabel={providerLabel}
          connected={false}
          fallbackIcon={fallbackIcon}
        />
        <div className="google-drive-insight-panel__inline-status">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          {loadingMessage}
        </div>
      </section>
    )
  }

  if (errorMessage || !provider) {
    return (
      <section className="google-drive-insight-panel" aria-label={ariaLabel}>
        <NativeIntegrationPanelHeader
          provider={provider}
          providerLabel={providerLabel}
          connected={false}
          fallbackIcon={fallbackIcon}
        />
        <p className="google-drive-insight-panel__error">{errorMessage ?? notConfiguredMessage}</p>
      </section>
    )
  }

  return (
    <section className="google-drive-insight-panel" aria-label={ariaLabel}>
      <NativeIntegrationPanelHeader
        provider={provider}
        providerLabel={providerLabel}
        connected={connected}
        fallbackIcon={fallbackIcon}
      />
      <div className="google-drive-insight-panel__body">
        <div className="google-drive-insight-panel__copy">
          {title ? <p className="google-drive-insight-panel__title">{title}</p> : null}
          {text ? <p className="google-drive-insight-panel__text">{text}</p> : null}
        </div>
        {actions ? <div className="google-drive-insight-panel__actions">{actions}</div> : null}
      </div>

      {statusMessage ? <p className="google-drive-insight-panel__status">{statusMessage}</p> : null}
    </section>
  )
}

export function NativeIntegrationConnectButton({
  busy,
  pendingAction,
  onClick,
}: {
  busy: boolean
  pendingAction: NativeIntegrationPendingAction
  onClick: () => void
}) {
  return (
    <button
      type="button"
      className="google-drive-insight-panel__button"
      onClick={onClick}
      disabled={busy}
    >
      {pendingAction === 'connect' ? (
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
      ) : (
        <Plug className="h-4 w-4" aria-hidden="true" />
      )}
      Connect
    </button>
  )
}

function NativeIntegrationPanelHeader({
  provider,
  providerLabel,
  connected,
  fallbackIcon,
}: {
  provider?: NativeIntegrationProvider | null
  providerLabel: string
  connected: boolean
  fallbackIcon: ReactNode
}) {
  return (
    <div className="google-drive-insight-panel__header">
      <span className="google-drive-insight-panel__icon" aria-hidden="true">
        {provider ? <NativeProviderIcon provider={provider} /> : fallbackIcon}
      </span>
      <span className="google-drive-insight-panel__label">{providerLabel}</span>
      {connected ? (
        <span className="google-drive-insight-panel__connected">
          <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" />
          Connected
        </span>
      ) : null}
    </div>
  )
}
