import { NativeIntegrationConnectButton, NativeIntegrationInsightPanelFrame, useNativeIntegrationPanelState } from './NativeIntegrationInsightPanel'

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
  const panel = useNativeIntegrationPanelState({
    agentId,
    nativeIntegrationsUrl,
    providerKey: META_ADS_PROVIDER_KEY,
    providerDisplayName: 'Meta Ads',
  })
  const busy = panel.connectPending || panel.pendingAction !== null

  return (
    <>
      <NativeIntegrationInsightPanelFrame
        ariaLabel="Meta Ads"
        providerLabel="Meta Ads"
        provider={panel.provider}
        connected={Boolean(panel.provider?.connected)}
        fallbackIcon={META_ADS_FALLBACK_ICON}
        unavailableMessage={!nativeIntegrationsUrl ? 'Meta Ads setup is unavailable in this workspace.' : null}
        loadingMessage={panel.isLoading ? 'Loading Meta Ads...' : null}
        errorMessage={panel.errorMessage}
        notConfiguredMessage="Meta Ads is not configured."
        title={panel.provider?.connected ? 'Meta Ads connected' : 'Connect Meta Ads'}
        text={panel.provider?.connected
          ? 'This agent can check account access, sync campaign performance, and monitor conversion quality through the Meta Ads tool.'
          : 'Connect Meta Ads so this agent can use the dedicated Meta Ads reporting and diagnostics tool.'}
        actions={!panel.provider?.connected && panel.provider ? (
          <NativeIntegrationConnectButton
            busy={busy}
            pendingAction={panel.pendingAction}
            onClick={() => panel.startConnect(panel.provider!)}
          />
        ) : null}
        statusMessage={panel.statusMessage}
      />
      {panel.credentialModal}
    </>
  )
}
