import { ConfiguredNativeIntegrationInsightPanel } from './NativeIntegrationInsightPanel'

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
  return (
    <ConfiguredNativeIntegrationInsightPanel
      agentId={agentId}
      nativeIntegrationsUrl={nativeIntegrationsUrl}
      providerKey={HUBSPOT_PROVIDER_KEY}
      providerLabel="HubSpot"
      fallbackIcon={HUBSPOT_FALLBACK_ICON}
      unavailableMessage="HubSpot setup is unavailable in this workspace."
      loadingMessage="Loading HubSpot..."
      notConfiguredMessage="HubSpot is not configured."
      connectedTitle="HubSpot connected"
      disconnectedTitle="Connect HubSpot"
      connectedText="This agent can use HubSpot for the CRM objects and workflows available to your connected account."
      disconnectedText="Connect HubSpot so this agent can use your available CRM records and workflows."
    />
  )
}
