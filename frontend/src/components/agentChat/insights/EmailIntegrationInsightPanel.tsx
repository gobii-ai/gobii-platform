import { useQuery } from '@tanstack/react-query'
import { Settings } from 'lucide-react'

import { fetchAgentEmailSettings } from '../../../api/agentEmailSettings'
import { safeErrorMessage } from '../../../api/safeErrorMessage'

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

  return (
    <section className="p-4" aria-label={`${label} email`}>
      <div className="flex items-start gap-3">
        <img src={`/static/images/integrations/native/${provider}.svg`} alt="" className="mt-0.5 h-6 w-6 object-contain" />
        <div className="min-w-0 flex-1">
          <h3 className="font-semibold text-slate-100">{label}</h3>
          {settingsQuery.isLoading ? <p className="mt-1 text-sm text-slate-400">Loading mailbox…</p> : null}
          {settingsQuery.isError ? <p className="mt-1 text-sm text-rose-300">{safeErrorMessage(settingsQuery.error)}</p> : null}
          {connected ? (
            <>
              <p className="mt-1 truncate text-sm text-slate-300">{settings.oauth.mailboxAddress}</p>
              <div className="mt-3 grid gap-2 text-sm text-slate-300 sm:grid-cols-2">
                <p>Sending: {settings.account.isOutboundEnabled ? 'Enabled' : 'Disabled'}{settings.account.smtpError ? ` — ${settings.account.smtpError}` : ''}</p>
                <p>Receiving: {settings.account.isInboundEnabled ? 'Enabled' : 'Disabled'}{settings.account.imapError ? ` — ${settings.account.imapError}` : ''}</p>
              </div>
            </>
          ) : !settingsQuery.isLoading ? <p className="mt-1 text-sm text-slate-400">This mailbox is no longer connected.</p> : null}
          {onOpenApps ? <button type="button" className="mt-4 inline-flex items-center gap-2 rounded-md border border-blue-300/40 bg-blue-950/20 px-3 py-2 text-sm font-semibold text-blue-100" onClick={onOpenApps}><Settings className="h-4 w-4" />Manage Email</button> : null}
        </div>
      </div>
    </section>
  )
}

export function GmailInsightPanel(props: Omit<EmailIntegrationInsightPanelProps, 'provider'>) {
  return <EmailIntegrationInsightPanel {...props} provider="gmail" />
}

export function OutlookInsightPanel(props: Omit<EmailIntegrationInsightPanelProps, 'provider'>) {
  return <EmailIntegrationInsightPanel {...props} provider="outlook" />
}
