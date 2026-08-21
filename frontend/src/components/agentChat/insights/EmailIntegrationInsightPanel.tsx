import { useQuery } from '@tanstack/react-query'
import { Settings } from 'lucide-react'

import { fetchAgentEmailSettings } from '../../../api/agentEmailSettings'
import { safeErrorMessage } from '../../../api/safeErrorMessage'
import { NativeIntegrationInsightPanelFrame } from './NativeIntegrationInsightPanel'

type EmailIntegrationInsightPanelProps = {
  agentId?: string | null
  onOpenApps?: () => void
  provider: 'gmail' | 'outlook'
}

function EmailIntegrationInsightPanel({ agentId = null, onOpenApps, provider }: EmailIntegrationInsightPanelProps) {
  const settingsQuery = useQuery({
    queryKey: ['agent-email-settings', agentId],
    queryFn: () => fetchAgentEmailSettings(`/console/api/agents/${agentId}/email-settings/`),
    enabled: Boolean(agentId),
  })
  const settings = settingsQuery.data
  const connected = settings?.activeMode === 'oauth' && settings.oauth.provider === provider
  const label = provider === 'gmail' ? 'Gmail' : 'Outlook'
  const directionSummary = connected
    ? `Sending ${settings.account.isOutboundEnabled ? 'enabled' : 'disabled'} · Receiving ${settings.account.isInboundEnabled ? 'enabled' : 'disabled'}`
    : null
  const healthErrors = connected
    ? [
        settings.account.smtpError ? `Sending: ${settings.account.smtpError}` : null,
        settings.account.imapError ? `Receiving: ${settings.account.imapError}` : null,
      ].filter((message): message is string => Boolean(message)).join(' · ')
    : ''

  return (
    <NativeIntegrationInsightPanelFrame
      ariaLabel={`${label} email`}
      providerLabel={label}
      configured={connected}
      connected={connected}
      fallbackIcon={(
        <img
          src={`/static/images/integrations/native/${provider}.svg`}
          alt=""
          className="h-5 w-5 object-contain"
        />
      )}
      unavailableMessage={!agentId ? `${label} setup is unavailable for this agent.` : null}
      loadingMessage={settingsQuery.isLoading ? 'Loading mailbox…' : null}
      errorMessage={settingsQuery.isError ? safeErrorMessage(settingsQuery.error) : null}
      notConfiguredMessage="This mailbox is no longer connected."
      title={connected ? settings.oauth.mailboxAddress : null}
      text={directionSummary}
      actions={connected && onOpenApps ? (
        <button type="button" className="google-drive-insight-panel__button" onClick={onOpenApps}>
          <Settings className="h-4 w-4" aria-hidden="true" />
          Manage Email
        </button>
      ) : null}
      statusMessage={healthErrors || null}
      statusTone="error"
    />
  )
}

export function GmailInsightPanel(props: Omit<EmailIntegrationInsightPanelProps, 'provider'>) {
  return <EmailIntegrationInsightPanel {...props} provider="gmail" />
}

export function OutlookInsightPanel(props: Omit<EmailIntegrationInsightPanelProps, 'provider'>) {
  return <EmailIntegrationInsightPanel {...props} provider="outlook" />
}
