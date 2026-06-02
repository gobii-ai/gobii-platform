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

const APOLLO_PROVIDER_KEY = 'apollo'

type ApolloInsightPanelProps = {
  nativeIntegrationsUrl?: string | null
}

type PendingAction = 'connect' | null

export function ApolloInsightPanel({ nativeIntegrationsUrl = null }: ApolloInsightPanelProps) {
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
    (candidate) => candidate.providerKey === APOLLO_PROVIDER_KEY,
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
        setStatusMessage('Connection window was closed before Apollo opened.')
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

  if (!nativeIntegrationsUrl) {
    return (
      <section className="google-drive-insight-panel" aria-label="Apollo">
        <ApolloPanelHeader connected={false} />
        <p className="google-drive-insight-panel__text">
          Apollo setup is unavailable in this workspace.
        </p>
      </section>
    )
  }

  if (nativeIntegrationsQuery.isLoading) {
    return (
      <section className="google-drive-insight-panel" aria-label="Apollo">
        <ApolloPanelHeader connected={false} />
        <div className="google-drive-insight-panel__inline-status">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          Loading Apollo...
        </div>
      </section>
    )
  }

  if (nativeIntegrationsQuery.isError || !provider) {
    return (
      <section className="google-drive-insight-panel" aria-label="Apollo">
        <ApolloPanelHeader connected={false} />
        <p className="google-drive-insight-panel__error">
          {nativeIntegrationsQuery.isError ? safeErrorMessage(nativeIntegrationsQuery.error) : 'Apollo is not configured.'}
        </p>
      </section>
    )
  }

  const busy = connectMutation.isPending || pendingAction !== null

  return (
    <section className="google-drive-insight-panel" aria-label="Apollo">
      <ApolloPanelHeader provider={provider} connected={provider.connected} />
      <div className="google-drive-insight-panel__body">
        <div className="google-drive-insight-panel__copy">
          <p className="google-drive-insight-panel__title">
            {provider.connected ? 'Apollo connected' : 'Connect Apollo'}
          </p>
          <p className="google-drive-insight-panel__text">
            {provider.connected
              ? 'This agent can use Apollo REST APIs for lead sourcing, enrichment, sequencing, analytics, and sales intelligence.'
              : 'Connect Apollo so this agent can use Apollo REST APIs for prospecting and enrichment.'}
          </p>
        </div>
        <div className="google-drive-insight-panel__actions">
          {!provider.connected ? (
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
          ) : null}
        </div>
      </div>

      {statusMessage ? <p className="google-drive-insight-panel__status">{statusMessage}</p> : null}
    </section>
  )
}

function ApolloPanelHeader({
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
          <span className="inline-flex h-5 w-5 items-center justify-center rounded bg-[#F8FF2C]">
            <img src="/static/images/integrations/native/apollo.svg" alt="" className="h-4 w-4 object-contain" />
          </span>
        )}
      </span>
      <span className="google-drive-insight-panel__label">Apollo</span>
      {connected ? (
        <span className="google-drive-insight-panel__connected">
          <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" />
          Connected
        </span>
      ) : null}
    </div>
  )
}
