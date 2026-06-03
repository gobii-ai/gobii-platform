import {
  NativeIntegrationConnectButton,
  NativeIntegrationInsightPanelFrame,
  useNativeIntegrationPanelState,
} from './NativeIntegrationInsightPanel'

const APOLLO_PROVIDER_KEY = 'apollo'

type ApolloInsightPanelProps = {
  nativeIntegrationsUrl?: string | null
}

const APOLLO_FALLBACK_ICON = (
  <span className="inline-flex h-5 w-5 items-center justify-center rounded bg-[#F8FF2C]">
    <img src="/static/images/integrations/native/apollo.svg" alt="" className="h-4 w-4 object-contain" />
  </span>
)

export function ApolloInsightPanel({ nativeIntegrationsUrl = null }: ApolloInsightPanelProps) {
  const panel = useNativeIntegrationPanelState({
    nativeIntegrationsUrl,
    providerKey: APOLLO_PROVIDER_KEY,
    providerDisplayName: 'Apollo',
  })
  const busy = panel.connectPending || panel.pendingAction !== null

  return (
    <NativeIntegrationInsightPanelFrame
      ariaLabel="Apollo"
      providerLabel="Apollo"
      provider={panel.provider}
      connected={Boolean(panel.provider?.connected)}
      fallbackIcon={APOLLO_FALLBACK_ICON}
      unavailableMessage={!nativeIntegrationsUrl ? 'Apollo setup is unavailable in this workspace.' : null}
      loadingMessage={panel.isLoading ? 'Loading Apollo...' : null}
      errorMessage={panel.errorMessage}
      notConfiguredMessage="Apollo is not configured."
      title={panel.provider?.connected ? 'Apollo connected' : 'Connect Apollo'}
      text={panel.provider?.connected
        ? 'This agent can use Apollo REST APIs for lead sourcing, enrichment, sequencing, analytics, and sales intelligence.'
        : 'Connect Apollo so this agent can use Apollo REST APIs for prospecting and enrichment.'}
      actions={!panel.provider?.connected && panel.provider ? (
        <NativeIntegrationConnectButton
          busy={busy}
          pendingAction={panel.pendingAction}
          onClick={() => panel.startConnect(panel.provider!)}
        />
      ) : null}
      statusMessage={panel.statusMessage}
    />
  )
}
