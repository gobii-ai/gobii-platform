import { NativeIntegrationConnectButton, NativeIntegrationInsightPanelFrame, useNativeIntegrationPanelState } from './NativeIntegrationInsightPanel'

const HUBSPOT_PROVIDER_KEY = 'hubspot'

type HubSpotInsightPanelProps = {
  agentId?: string | null
  nativeIntegrationsUrl?: string | null
  onOpenApps?: () => void
}

const HUBSPOT_FALLBACK_ICON = (
  <img src="/static/images/integrations/native/hubspot.svg" alt="" className="h-5 w-5 object-contain" />
)

export function HubSpotInsightPanel({ agentId = null, nativeIntegrationsUrl = null }: HubSpotInsightPanelProps) {
  const panel = useNativeIntegrationPanelState({
    agentId,
    nativeIntegrationsUrl,
    providerKey: HUBSPOT_PROVIDER_KEY,
    providerDisplayName: 'HubSpot',
  })
  const busy = panel.connectPending || panel.pendingAction !== null

  return (
    <NativeIntegrationInsightPanelFrame
      ariaLabel="HubSpot"
      providerLabel="HubSpot"
      provider={panel.provider}
      connected={Boolean(panel.provider?.connected)}
      fallbackIcon={HUBSPOT_FALLBACK_ICON}
      unavailableMessage={!nativeIntegrationsUrl ? 'HubSpot setup is unavailable in this workspace.' : null}
      loadingMessage={panel.isLoading ? 'Loading HubSpot...' : null}
      errorMessage={panel.errorMessage}
      notConfiguredMessage="HubSpot is not configured."
      title={panel.provider?.connected ? 'HubSpot connected' : 'Connect HubSpot'}
      text={panel.provider?.connected
        ? 'This agent can use HubSpot REST APIs for contacts, companies, deals, owners, properties, and CRM workflows.'
        : 'Connect HubSpot so this agent can use HubSpot REST APIs for CRM records and workflows.'}
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
