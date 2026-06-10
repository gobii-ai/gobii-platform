import { useQuery } from '@tanstack/react-query'
import { Settings } from 'lucide-react'

import {
  agentDiscordAppQueryKey,
  fetchAgentDiscordApp,
} from '../../../api/discordNative'
import type { NativeIntegrationProvider } from '../../../api/nativeIntegrations'
import { safeErrorMessage } from '../../../api/safeErrorMessage'
import { NativeIntegrationInsightPanelFrame } from './NativeIntegrationInsightPanel'

type DiscordInsightPanelProps = {
  agentId?: string | null
  nativeIntegrationsUrl?: string | null
  onOpenApps?: () => void
}

const DISCORD_FALLBACK_ICON = (
  <img src="/static/images/integrations/native/discord.svg" alt="" className="h-5 w-5 object-contain" />
)

const DISCORD_PROVIDER: NativeIntegrationProvider = {
  providerKey: 'discord',
  displayName: 'Discord',
  description: 'Connect Discord servers and subscribe this agent to selected channels.',
  authType: 'oauth2',
  icon: 'discord',
  apiHosts: [],
  scopes: [],
  connected: false,
  scope: '',
  expiresAt: null,
  connectUrl: '',
  filesUrl: '',
  pickerTokenUrl: '',
  agentEventUrl: '',
  revokeUrl: '',
}

export function DiscordInsightPanel({ agentId = null, onOpenApps }: DiscordInsightPanelProps) {
  const appQuery = useQuery({
    queryKey: agentId ? agentDiscordAppQueryKey(agentId) : ['agent-discord-app', null],
    queryFn: () => fetchAgentDiscordApp(agentId as string),
    enabled: Boolean(agentId),
  })
  const app = appQuery.data ?? null
  const provider = app ? { ...DISCORD_PROVIDER, connected: app.connected } : DISCORD_PROVIDER

  return (
    <NativeIntegrationInsightPanelFrame
      ariaLabel="Discord"
      providerLabel="Discord"
      provider={provider}
      connected={Boolean(app?.connected)}
      fallbackIcon={DISCORD_FALLBACK_ICON}
      unavailableMessage={!agentId ? 'Discord setup is unavailable for this agent.' : null}
      loadingMessage={appQuery.isLoading ? 'Loading Discord...' : null}
      errorMessage={appQuery.isError ? safeErrorMessage(appQuery.error) : null}
      notConfiguredMessage="Discord is not configured."
      title={app?.subscribed ? 'Discord channels subscribed' : app?.connected ? 'Discord connected' : 'Connect Discord'}
      text={app?.subscribed
        ? `${app.activeSubscriptionCount} ${app.activeSubscriptionCount === 1 ? 'channel is' : 'channels are'} subscribed for inbound Discord messages.`
        : app?.connected
          ? 'Choose the Discord channels that should wake this agent.'
          : 'Connect Discord so this agent can receive and reply to selected server channels.'}
      actions={onOpenApps ? (
        <button
          type="button"
          className="google-drive-insight-panel__button"
          onClick={onOpenApps}
        >
          <Settings className="h-4 w-4" aria-hidden="true" />
          Configure
        </button>
      ) : null}
    />
  )
}
