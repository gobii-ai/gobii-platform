import { ConfiguredNativeIntegrationInsightPanel } from './NativeIntegrationInsightPanel'

const APOLLO_PROVIDER_KEY = 'apollo'

type ApolloInsightPanelProps = {
  agentId?: string | null
  nativeIntegrationsUrl?: string | null
  onOpenApps?: () => void
}

const APOLLO_FALLBACK_ICON = (
  <span className="inline-flex h-5 w-5 items-center justify-center rounded bg-[#F8FF2C]">
    <img src="/static/images/integrations/native/apollo.svg" alt="" className="h-4 w-4 object-contain" />
  </span>
)

export function ApolloInsightPanel({ agentId = null, nativeIntegrationsUrl = null }: ApolloInsightPanelProps) {
  return (
    <ConfiguredNativeIntegrationInsightPanel
      agentId={agentId}
      nativeIntegrationsUrl={nativeIntegrationsUrl}
      providerKey={APOLLO_PROVIDER_KEY}
      providerLabel="Apollo"
      fallbackIcon={APOLLO_FALLBACK_ICON}
      unavailableMessage="Apollo setup is unavailable in this workspace."
      loadingMessage="Loading Apollo..."
      notConfiguredMessage="Apollo is not configured."
      connectedTitle="Apollo connected"
      disconnectedTitle="Connect Apollo"
      connectedText="This agent can use Apollo REST APIs for lead sourcing, enrichment, sequencing, analytics, and sales intelligence."
      disconnectedText="Connect Apollo so this agent can use Apollo REST APIs for prospecting and enrichment."
    />
  )
}
