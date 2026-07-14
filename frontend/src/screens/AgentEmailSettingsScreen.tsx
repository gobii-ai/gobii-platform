import { useCallback, useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, CheckCircle2, Loader2, Mail, RefreshCw, Unplug } from 'lucide-react'

import {
  fetchAgentEmailSettings,
  saveAgentEmailSettings,
  testAgentEmailSettings,
  updateAgentEmailSettingsAction,
  type AgentEmailSettingsPayload,
  type EmailSettingsSaveRequest,
} from '../api/agentEmailSettings'
import { revokeNativeIntegration, startNativeIntegrationConnect } from '../api/nativeIntegrations'
import { safeErrorMessage } from '../api/safeErrorMessage'
import { storePendingNativeOAuth } from '../components/mcp/NativeIntegrationShared'
import { readStoredConsoleContext } from '../util/consoleContextStorage'

type AgentEmailSettingsScreenProps = {
  agentId: string
  emailSettingsUrl: string
  ensureAccountUrl: string
  testUrl: string
  onBack?: () => void
  onSaved?: (payload: { endpointAddress: string | null }) => void
}

type CustomDraft = {
  address: string
  displayName: string
  smtpHost: string
  smtpPort: string
  smtpSecurity: string
  smtpAuth: string
  smtpUsername: string
  smtpPassword: string
  imapHost: string
  imapPort: string
  imapSecurity: string
  imapAuth: string
  imapUsername: string
  imapPassword: string
  imapFolder: string
  pollIntervalSec: string
}

const inputClassName = 'mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-100'
const primaryButtonClassName = 'inline-flex items-center justify-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-700 disabled:opacity-60'
const secondaryButtonClassName = 'inline-flex items-center justify-center gap-2 rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-semibold text-slate-800 hover:border-blue-300 hover:text-blue-700 disabled:opacity-60'

function draftFromSettings(settings: AgentEmailSettingsPayload): CustomDraft {
  return {
    address: settings.endpoint.address,
    displayName: settings.endpoint.displayName ?? '',
    smtpHost: settings.account.smtpHost,
    smtpPort: settings.account.smtpPort?.toString() ?? '',
    smtpSecurity: settings.account.smtpSecurity,
    smtpAuth: settings.account.smtpAuth === 'oauth2' ? 'login' : settings.account.smtpAuth,
    smtpUsername: settings.account.smtpUsername,
    smtpPassword: '',
    imapHost: settings.account.imapHost,
    imapPort: settings.account.imapPort?.toString() ?? '',
    imapSecurity: settings.account.imapSecurity,
    imapAuth: settings.account.imapAuth === 'oauth2' ? 'login' : settings.account.imapAuth,
    imapUsername: settings.account.imapUsername,
    imapPassword: '',
    imapFolder: settings.account.imapFolder || 'INBOX',
    pollIntervalSec: settings.account.pollIntervalSec?.toString() ?? '120',
  }
}

function settingsRequest(
  settings: AgentEmailSettingsPayload,
  draft: CustomDraft,
  defaultDisplayName: string,
): EmailSettingsSaveRequest {
  return {
    endpointAddress: draft.address,
    connectionMode: 'custom',
    smtpHost: draft.smtpHost,
    smtpPort: draft.smtpPort ? Number(draft.smtpPort) : null,
    smtpSecurity: draft.smtpSecurity,
    smtpAuth: draft.smtpAuth,
    smtpUsername: draft.smtpUsername,
    smtpPassword: draft.smtpPassword || undefined,
    imapHost: draft.imapHost,
    imapPort: draft.imapPort ? Number(draft.imapPort) : null,
    imapSecurity: draft.imapSecurity,
    imapAuth: draft.imapAuth,
    imapUsername: draft.imapUsername,
    imapPassword: draft.imapPassword || undefined,
    imapFolder: draft.imapFolder || 'INBOX',
    isOutboundEnabled: settings.account.isOutboundEnabled,
    isInboundEnabled: settings.account.isInboundEnabled,
    imapIdleEnabled: settings.account.imapIdleEnabled,
    pollIntervalSec: Number(draft.pollIntervalSec || 120),
    displayName: draft.displayName,
    defaultDisplayName,
  }
}

function StatusLine({ enabled, error, label }: { enabled: boolean; error: string; label: string }) {
  return (
    <div className="flex items-start gap-2 text-sm">
      <CheckCircle2 className={`mt-0.5 h-4 w-4 ${error ? 'text-amber-600' : enabled ? 'text-emerald-600' : 'text-slate-400'}`} />
      <div>
        <span className="font-medium text-slate-800">{label}: {enabled ? 'Enabled' : 'Disabled'}</span>
        {error ? <p className="mt-1 text-amber-700">{error}</p> : null}
      </div>
    </div>
  )
}

export function AgentEmailSettingsScreen({
  agentId,
  emailSettingsUrl,
  testUrl,
  onBack,
  onSaved,
}: AgentEmailSettingsScreenProps) {
  const queryClient = useQueryClient()
  const queryKey = useMemo(() => ['agent-email-settings', agentId, emailSettingsUrl], [agentId, emailSettingsUrl])
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [customDraft, setCustomDraft] = useState<CustomDraft | null>(null)
  const [defaultDisplayName, setDefaultDisplayName] = useState('')

  const settingsQuery = useQuery({
    queryKey,
    queryFn: () => fetchAgentEmailSettings(emailSettingsUrl),
    refetchOnWindowFocus: true,
  })
  const settings = settingsQuery.data

  useEffect(() => {
    if (!settings) return
    setCustomDraft(draftFromSettings(settings))
    setDefaultDisplayName(settings.defaultEndpoint.displayName ?? '')
  }, [settings])

  const refresh = useCallback(async () => {
    await queryClient.invalidateQueries({ queryKey })
  }, [queryClient, queryKey])

  useEffect(() => {
    const handleMessage = (event: MessageEvent<{ type?: string; ok?: boolean; error?: string }>) => {
      if (event.origin !== window.location.origin || event.data?.type !== 'gobii:native-oauth-complete') return
      if (!event.data.ok) setError(event.data.error || 'Unable to connect the mailbox.')
      void refresh()
    }
    window.addEventListener('message', handleMessage)
    return () => window.removeEventListener('message', handleMessage)
  }, [refresh])

  const actionMutation = useMutation({
    mutationFn: ({ action, values = {} }: { action: string; values?: Record<string, unknown> }) =>
      updateAgentEmailSettingsAction(emailSettingsUrl, action, values),
    onSuccess: ({ settings: next }) => {
      queryClient.setQueryData(queryKey, next)
      onSaved?.({ endpointAddress: next.endpoint.address || null })
    },
  })

  const saveMutation = useMutation({
    mutationFn: (payload: EmailSettingsSaveRequest) => saveAgentEmailSettings(emailSettingsUrl, payload),
    onSuccess: ({ settings: next }) => {
      queryClient.setQueryData(queryKey, next)
      setNotice('Email settings saved.')
      onSaved?.({ endpointAddress: next.endpoint.address || null })
    },
  })

  const testMutation = useMutation({
    mutationFn: ({ payload, testOutbound, testInbound }: { payload: EmailSettingsSaveRequest; testOutbound: boolean; testInbound: boolean }) => testAgentEmailSettings(testUrl, {
      ...payload,
      testOutbound,
      testInbound,
    }),
    onSuccess: ({ settings: next, ok }, variables) => {
      queryClient.setQueryData(queryKey, next)
      const checked = variables.testOutbound && variables.testInbound
        ? 'Sending and receiving checks'
        : variables.testOutbound ? 'Sending check' : 'Receiving check'
      setNotice(ok ? `${checked} succeeded.` : `${checked} need attention.`)
    },
  })

  const run = useCallback(async (operation: () => Promise<unknown>) => {
    setError('')
    setNotice('')
    try {
      await operation()
    } catch (caught) {
      setError(safeErrorMessage(caught))
    }
  }, [])

  const connect = useCallback((provider: 'gmail' | 'outlook') => {
    if (!settings) return
    void run(async () => {
      const popup = window.open('', `gobii-native-oauth-${provider}`, 'popup=yes,width=520,height=720')
      const url = provider === 'gmail' ? settings.oauth.gmailConnectUrl : settings.oauth.outlookConnectUrl
      const result = await startNativeIntegrationConnect(url, undefined, agentId)
      storePendingNativeOAuth(result.state, {
        providerKey: provider,
        agentId,
        returnUrl: window.location.href,
        popup: Boolean(popup),
        state: result.state,
        createdAt: Date.now(),
        context: readStoredConsoleContext(),
      })
      if (popup) popup.location.href = result.authorizationUrl
      else window.location.href = result.authorizationUrl
    })
  }, [agentId, run, settings])

  if (settingsQuery.isLoading || !settings || !customDraft) {
    return <div className="flex min-h-48 items-center justify-center text-slate-600"><Loader2 className="mr-2 h-5 w-5 animate-spin" /> Loading email settings…</div>
  }
  if (settingsQuery.isError) {
    return <div className="p-5 text-sm text-rose-700">{safeErrorMessage(settingsQuery.error)}</div>
  }

  const busy = actionMutation.isPending || saveMutation.isPending || testMutation.isPending
  const providerLabel = settings.oauth.provider === 'gmail' ? 'Gmail' : settings.oauth.provider === 'outlook' ? 'Outlook' : 'Email OAuth'
  const request = settingsRequest(settings, customDraft, defaultDisplayName)

  return (
    <div className="mx-auto w-full max-w-3xl p-4 sm:p-6">
      <div className="mb-6 flex items-start gap-3">
        {onBack ? <button type="button" onClick={onBack} className="mt-0.5 rounded-lg p-2 text-slate-600 hover:bg-blue-50 hover:text-blue-700" aria-label="Back"><ArrowLeft className="h-5 w-5" /></button> : null}
        <div>
          <h1 className="text-xl font-semibold text-slate-950">Email</h1>
          <p className="mt-1 text-sm text-slate-600">Choose how {settings.agent.name} sends and receives external email.</p>
        </div>
      </div>

      {error ? <div className="mb-4 rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800">{error}</div> : null}
      {notice ? <div className="mb-4 rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800">{notice}</div> : null}

      <section className="mb-8">
        <div className="flex items-center gap-2"><Mail className="h-5 w-5 text-blue-600" /><h2 className="font-semibold text-slate-950">Gobii email address</h2></div>
        <p className="mt-2 text-sm text-slate-600">This address always remains available for sending and receiving.</p>
        <div className="mt-4 grid gap-4 sm:grid-cols-2">
          <label className="text-sm font-medium text-slate-800">Address<input className={inputClassName} value={settings.defaultEndpoint.address} readOnly /></label>
          <label className="text-sm font-medium text-slate-800">Display name<input className={inputClassName} value={defaultDisplayName} onChange={(event) => setDefaultDisplayName(event.target.value)} /></label>
        </div>
        <button type="button" className={`${secondaryButtonClassName} mt-3`} disabled={busy} onClick={() => void run(() => actionMutation.mutateAsync({ action: 'update_display_names', values: { defaultDisplayName, displayName: customDraft.displayName } }))}>Save display name</button>
      </section>

      {settings.activeMode === 'none' ? (
        <section>
          <h2 className="font-semibold text-slate-950">Connect an external mailbox</h2>
          <p className="mt-1 text-sm text-slate-600">No server settings are needed for Gmail or Outlook.</p>
          <div className="mt-4 flex flex-wrap gap-3">
            <button type="button" className={primaryButtonClassName} disabled={busy} onClick={() => connect('gmail')}><img src="/static/images/integrations/native/gmail.svg" alt="" className="h-5 w-5" />Connect Gmail</button>
            <button type="button" className={primaryButtonClassName} disabled={busy} onClick={() => connect('outlook')}><img src="/static/images/integrations/native/outlook.svg" alt="" className="h-5 w-5" />Connect Outlook</button>
            <button type="button" className={secondaryButtonClassName} disabled={busy} onClick={() => void run(() => actionMutation.mutateAsync({ action: 'enable_custom' }))}>Enable custom SMTP/IMAP</button>
          </div>
          {settings.customConfigured ? <p className="mt-3 text-sm text-slate-600">Your previous custom SMTP/IMAP settings are saved and will reappear when enabled.</p> : null}
        </section>
      ) : null}

      {settings.activeMode === 'oauth' ? (
        <section>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div><h2 className="font-semibold text-slate-950">{providerLabel}</h2><p className="mt-1 text-sm text-slate-600">{settings.oauth.mailboxAddress}</p></div>
            <button type="button" className={secondaryButtonClassName} disabled={busy} onClick={() => void run(async () => {
              if (settings.oauth.legacy) await actionMutation.mutateAsync({ action: 'disconnect_legacy_oauth' })
              else if (settings.oauth.revokeUrl) {
                await revokeNativeIntegration(settings.oauth.revokeUrl, undefined, agentId)
                await refresh()
              }
            })}><Unplug className="h-4 w-4" />Disconnect</button>
          </div>
          <label className="mt-5 block text-sm font-medium text-slate-800">Display name<input className={inputClassName} value={customDraft.displayName} onChange={(event) => setCustomDraft({ ...customDraft, displayName: event.target.value })} /></label>
          <div className="mt-5 grid gap-4 sm:grid-cols-2">
            <DirectionToggle label="Send email" checked={settings.account.isOutboundEnabled} disabled={busy} onChange={(checked) => void run(() => actionMutation.mutateAsync({ action: 'update_directions', values: { isOutboundEnabled: checked, isInboundEnabled: settings.account.isInboundEnabled } }))} />
            <DirectionToggle label="Receive email" checked={settings.account.isInboundEnabled} disabled={busy} onChange={(checked) => void run(() => actionMutation.mutateAsync({ action: 'update_directions', values: { isOutboundEnabled: settings.account.isOutboundEnabled, isInboundEnabled: checked } }))} />
          </div>
          <div className="mt-5 space-y-3">
            <StatusLine label="Sending" enabled={settings.account.isOutboundEnabled} error={settings.account.smtpError} />
            <StatusLine label="Receiving" enabled={settings.account.isInboundEnabled} error={settings.account.imapError} />
          </div>
          <div className="mt-5 flex flex-wrap gap-3">
            <button type="button" className={secondaryButtonClassName} disabled={busy} onClick={() => void run(() => testMutation.mutateAsync({ payload: request, testOutbound: true, testInbound: false }))}><RefreshCw className="h-4 w-4" />Retry sending</button>
            <button type="button" className={secondaryButtonClassName} disabled={busy} onClick={() => void run(() => testMutation.mutateAsync({ payload: request, testOutbound: false, testInbound: true }))}><RefreshCw className="h-4 w-4" />Retry receiving</button>
            <button type="button" className={primaryButtonClassName} disabled={busy} onClick={() => void run(() => actionMutation.mutateAsync({ action: 'update_display_names', values: { defaultDisplayName, displayName: customDraft.displayName } }))}>Save display name</button>
          </div>
        </section>
      ) : null}

      {settings.activeMode === 'custom' ? (
        <section>
          <div className="flex flex-wrap items-center justify-between gap-3"><div><h2 className="font-semibold text-slate-950">Custom SMTP/IMAP</h2><p className="mt-1 text-sm text-slate-600">Use the server settings supplied by your email provider.</p></div><button type="button" className={secondaryButtonClassName} disabled={busy} onClick={() => void run(() => actionMutation.mutateAsync({ action: 'disable_custom' }))}>Disable</button></div>
          <div className="mt-5 grid gap-4 sm:grid-cols-2">
            <TextField label="Email address" value={customDraft.address} onChange={(address) => setCustomDraft({ ...customDraft, address })} />
            <TextField label="Display name" value={customDraft.displayName} onChange={(displayName) => setCustomDraft({ ...customDraft, displayName })} />
            <TextField label="SMTP server" value={customDraft.smtpHost} onChange={(smtpHost) => setCustomDraft({ ...customDraft, smtpHost })} />
            <TextField label="SMTP port" type="number" value={customDraft.smtpPort} onChange={(smtpPort) => setCustomDraft({ ...customDraft, smtpPort })} />
            <SelectField label="SMTP security" value={customDraft.smtpSecurity} options={[['starttls', 'STARTTLS'], ['ssl', 'SSL'], ['none', 'None']]} onChange={(smtpSecurity) => setCustomDraft({ ...customDraft, smtpSecurity })} />
            <SelectField label="SMTP authentication" value={customDraft.smtpAuth} options={[['login', 'Login'], ['plain', 'Plain'], ['none', 'None']]} onChange={(smtpAuth) => setCustomDraft({ ...customDraft, smtpAuth })} />
            <TextField label="SMTP username" value={customDraft.smtpUsername} onChange={(smtpUsername) => setCustomDraft({ ...customDraft, smtpUsername })} />
            <TextField label="SMTP password" type="password" value={customDraft.smtpPassword} onChange={(smtpPassword) => setCustomDraft({ ...customDraft, smtpPassword })} placeholder={settings.account.hasSmtpPassword ? 'Saved password' : ''} />
            <TextField label="IMAP server" value={customDraft.imapHost} onChange={(imapHost) => setCustomDraft({ ...customDraft, imapHost })} />
            <TextField label="IMAP port" type="number" value={customDraft.imapPort} onChange={(imapPort) => setCustomDraft({ ...customDraft, imapPort })} />
            <SelectField label="IMAP security" value={customDraft.imapSecurity} options={[['ssl', 'SSL'], ['starttls', 'STARTTLS'], ['none', 'None']]} onChange={(imapSecurity) => setCustomDraft({ ...customDraft, imapSecurity })} />
            <SelectField label="IMAP authentication" value={customDraft.imapAuth} options={[['login', 'Login'], ['none', 'None']]} onChange={(imapAuth) => setCustomDraft({ ...customDraft, imapAuth })} />
            <TextField label="IMAP username" value={customDraft.imapUsername} onChange={(imapUsername) => setCustomDraft({ ...customDraft, imapUsername })} />
            <TextField label="IMAP password" type="password" value={customDraft.imapPassword} onChange={(imapPassword) => setCustomDraft({ ...customDraft, imapPassword })} placeholder={settings.account.hasImapPassword ? 'Saved password' : ''} />
            <TextField label="IMAP folder" value={customDraft.imapFolder} onChange={(imapFolder) => setCustomDraft({ ...customDraft, imapFolder })} />
            <TextField label="Check every (seconds)" type="number" value={customDraft.pollIntervalSec} onChange={(pollIntervalSec) => setCustomDraft({ ...customDraft, pollIntervalSec })} />
          </div>
          <div className="mt-5 grid gap-4 sm:grid-cols-2">
            <DirectionToggle label="Send email" checked={settings.account.isOutboundEnabled} disabled={busy} onChange={(checked) => void run(() => actionMutation.mutateAsync({ action: 'update_directions', values: { isOutboundEnabled: checked, isInboundEnabled: settings.account.isInboundEnabled } }))} />
            <DirectionToggle label="Receive email" checked={settings.account.isInboundEnabled} disabled={busy} onChange={(checked) => void run(() => actionMutation.mutateAsync({ action: 'update_directions', values: { isOutboundEnabled: settings.account.isOutboundEnabled, isInboundEnabled: checked } }))} />
          </div>
          <div className="mt-5 flex flex-wrap gap-3"><button type="button" className={secondaryButtonClassName} disabled={busy} onClick={() => void run(() => testMutation.mutateAsync({ payload: request, testOutbound: true, testInbound: true }))}><RefreshCw className="h-4 w-4" />Test sending and receiving</button><button type="button" className={primaryButtonClassName} disabled={busy} onClick={() => void run(() => saveMutation.mutateAsync(request))}>Save settings</button></div>
        </section>
      ) : null}
    </div>
  )
}

function TextField({ label, value, onChange, type = 'text', placeholder = '' }: { label: string; value: string; onChange: (value: string) => void; type?: string; placeholder?: string }) {
  return <label className="text-sm font-medium text-slate-800">{label}<input className={inputClassName} type={type} value={value} placeholder={placeholder} onChange={(event) => onChange(event.target.value)} /></label>
}

function SelectField({ label, value, options, onChange }: { label: string; value: string; options: Array<[string, string]>; onChange: (value: string) => void }) {
  return <label className="text-sm font-medium text-slate-800">{label}<select className={inputClassName} value={value} onChange={(event) => onChange(event.target.value)}>{options.map(([optionValue, optionLabel]) => <option key={optionValue} value={optionValue}>{optionLabel}</option>)}</select></label>
}

function DirectionToggle({ label, checked, disabled, onChange }: { label: string; checked: boolean; disabled: boolean; onChange: (checked: boolean) => void }) {
  return <label className="flex cursor-pointer items-center justify-between rounded-lg border border-slate-300 bg-white px-4 py-3 text-sm font-semibold text-slate-800"><span>{label}</span><input type="checkbox" className="h-5 w-5 rounded border-slate-300 text-blue-600" checked={checked} disabled={disabled} onChange={(event) => onChange(event.target.checked)} /></label>
}
