import { ConfiguredNativeIntegrationInsightPanel } from './NativeIntegrationInsightPanel'

const META_ADS_PROVIDER_KEY = 'meta_ads'

type MetaAdsInsightPanelProps = {
  agentId?: string | null
  nativeIntegrationsUrl?: string | null
  onOpenApps?: () => void
}

const META_ADS_FALLBACK_ICON = (
  <img src="/static/images/integrations/native/meta_ads.svg" alt="" className="h-5 w-5 object-contain" />
)

export function MetaAdsInsightPanel({ agentId = null, nativeIntegrationsUrl = null }: MetaAdsInsightPanelProps) {
  return (
    <ConfiguredNativeIntegrationInsightPanel
      agentId={agentId}
      nativeIntegrationsUrl={nativeIntegrationsUrl}
      providerKey={META_ADS_PROVIDER_KEY}
      providerLabel="Meta Ads"
      fallbackIcon={META_ADS_FALLBACK_ICON}
      unavailableMessage="Meta Ads setup is unavailable in this workspace."
      loadingMessage="Loading Meta Ads..."
      notConfiguredMessage="Meta Ads is not configured."
      connectedTitle="Meta Ads connected"
      disconnectedTitle="Connect Meta Ads"
      connectedText="This agent can check account access, sync campaign performance, and monitor conversion quality through the Meta Ads tool."
      disconnectedText="Connect Meta Ads so this agent can use the dedicated Meta Ads reporting and diagnostics tool."
      includeCredentialModal
    />
  )
}
