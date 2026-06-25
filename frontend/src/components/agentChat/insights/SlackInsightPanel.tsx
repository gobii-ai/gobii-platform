import { useQuery } from '@tanstack/react-query'
import { Settings } from 'lucide-react'

import {
  agentSlackAppQueryKey,
  fetchAgentSlackApp,
} from '../../../api/slackNative'
import type { NativeIntegrationProvider } from '../../../api/nativeIntegrations'
import { safeErrorMessage } from '../../../api/safeErrorMessage'
import { NativeIntegrationInsightPanelFrame } from './NativeIntegrationInsightPanel'

type SlackInsightPanelProps = {
  agentId?: string | null
  nativeIntegrationsUrl?: string | null
  onOpenApps?: () => void
}

const SLACK_FALLBACK_ICON = (
  <img src="/static/images/integrations/native/slack.svg" alt="" className="h-5 w-5 object-contain" />
)

const SLACK_PROVIDER: NativeIntegrationProvider = {
  providerKey: 'slack',
  displayName: 'Slack',
  description: 'Connect Slack workspaces and subscribe this agent to selected channels.',
  authType: 'oauth2',
  icon: 'slack',
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

export function SlackInsightPanel({ agentId = null, onOpenApps }: SlackInsightPanelProps) {
  const appQuery = useQuery({
    queryKey: agentId ? agentSlackAppQueryKey(agentId) : ['agent-slack-app', null],
    queryFn: () => fetchAgentSlackApp(agentId as string),
    enabled: Boolean(agentId),
  })
  const app = appQuery.data ?? null
  const provider = app ? { ...SLACK_PROVIDER, connected: app.connected } : SLACK_PROVIDER

  return (
    <NativeIntegrationInsightPanelFrame
      ariaLabel="Slack"
      providerLabel="Slack"
      provider={provider}
      connected={Boolean(app?.connected)}
      fallbackIcon={SLACK_FALLBACK_ICON}
      unavailableMessage={!agentId ? 'Slack setup is unavailable for this agent.' : null}
      loadingMessage={appQuery.isLoading ? 'Loading Slack...' : null}
      errorMessage={appQuery.isError ? safeErrorMessage(appQuery.error) : null}
      notConfiguredMessage="Slack is not configured."
      title={app?.subscribed ? 'Slack channels subscribed' : app?.connected ? 'Slack connected' : 'Connect Slack'}
      text={app?.subscribed
        ? `${app.activeSubscriptionCount} ${app.activeSubscriptionCount === 1 ? 'channel is' : 'channels are'} subscribed. Replies use display-level agent identity, not separate mentionable Slack bots.`
        : app?.connected
          ? 'Choose the Slack channels that should wake this agent. Replies can show the agent name per message.'
          : 'Connect Slack so this agent can receive and reply to selected public or private channels.'}
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
