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
      connectedText="This agent can use HubSpot REST APIs for contacts, companies, deals, owners, properties, and CRM workflows."
      disconnectedText="Connect HubSpot so this agent can use HubSpot REST APIs for CRM records and workflows."
    />
  )
}
