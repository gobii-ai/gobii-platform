import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Loader2, Mail, RefreshCw, Unplug } from 'lucide-react'
import { Switch as AriaSwitch } from 'react-aria-components'

import {
  fetchAgentEmailSettings,
  saveAgentEmailSettings,
  testAgentEmailSettings,
  updateAgentEmailSettingsAction,
  type AgentEmailSettingsPayload,
  type EmailSettingsSaveRequest,
} from '../api/agentEmailSettings'
import { revokeNativeIntegration, startNativeIntegrationConnect } from '../api/nativeIntegrations'
import { apiErrorMessages, safeErrorMessage } from '../api/safeErrorMessage'
import { SettingsBanner } from '../components/agentSettings/SettingsBanner'
import { EmbeddedAgentShellBackButton } from '../components/agentChat/EmbeddedAgentShellBackButton'
import { SaveBar } from '../components/common/SaveBar'
import { SettingsSurface, SettingsSurfaceProvider, useSettingsSurfaceVariant, type SettingsSurfaceVariant } from '../components/common/SettingsSurface'
import { storePendingNativeOAuth } from '../components/mcp/NativeIntegrationShared'
import { readStoredConsoleContext } from '../util/consoleContextStorage'

type AgentEmailSettingsScreenProps = {
  agentId: string
  emailSettingsUrl: string
  testUrl: string
  surfaceVariant?: SettingsSurfaceVariant
  onBack?: () => void
  onSaved?: (payload: { endpointAddress: string | null }) => void
}

type EmailSettingsDraft = {
  expectedActiveMode: AgentEmailSettingsPayload['activeMode']
  defaultDisplayName: string
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
  isOutboundEnabled: boolean
  isInboundEnabled: boolean
  imapIdleEnabled: boolean
  pollIntervalSec: string
}

type OAuthConnectionProgress = {
  provider: 'gmail' | 'outlook'
  phase: 'authorizing' | 'finishing'
}

type NativeOAuthCompleteMessage = {
  type?: string
  providerKey?: string
  ok?: boolean
  error?: string
}

const NATIVE_OAUTH_COMPLETE_MESSAGE = 'gobii:native-oauth-complete'
const NATIVE_OAUTH_COMPLETE_PREFIX = 'gobii:native_oauth_complete:'

const standaloneInputClassName = 'mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-100'
const embeddedInputClassName = 'mt-1 w-full rounded-lg border border-slate-200/20 bg-slate-950/45 px-3 py-2 text-sm text-slate-100 placeholder:text-slate-500 focus:border-blue-300/50 focus:outline-none focus:ring-2 focus:ring-blue-300/20'
const standalonePrimaryButtonClassName = 'inline-flex items-center justify-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-blue-700 disabled:opacity-60'
const embeddedPrimaryButtonClassName = 'inline-flex items-center justify-center gap-2 rounded-lg border border-blue-300/30 bg-blue-600/90 px-4 py-2 text-sm font-semibold text-white transition-colors hover:border-blue-200/50 hover:bg-blue-500 disabled:opacity-60'
const standaloneSecondaryButtonClassName = 'inline-flex items-center justify-center gap-2 rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-semibold text-slate-800 transition-colors hover:border-blue-300 hover:text-blue-700 disabled:opacity-60'
const embeddedSecondaryButtonClassName = 'inline-flex items-center justify-center gap-2 rounded-lg border border-slate-200/25 bg-slate-900/35 px-4 py-2 text-sm font-semibold text-slate-100 transition-colors hover:border-slate-100/40 hover:bg-slate-900/55 disabled:opacity-60'
const standaloneDestructiveButtonClassName = 'inline-flex items-center justify-center gap-2 rounded-lg border border-rose-600 bg-rose-600 px-4 py-2 text-sm font-semibold text-white transition-colors hover:border-rose-700 hover:bg-rose-700 disabled:opacity-60'
const embeddedDestructiveButtonClassName = 'inline-flex items-center justify-center gap-2 rounded-lg border border-rose-400/50 bg-rose-600/90 px-4 py-2 text-sm font-semibold text-white transition-colors hover:border-rose-300/70 hover:bg-rose-500 disabled:opacity-60'

function draftFromSettings(settings: AgentEmailSettingsPayload): EmailSettingsDraft {
  return {
    expectedActiveMode: settings.activeMode,
    defaultDisplayName: settings.defaultEndpoint.displayName ?? '',
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
    isOutboundEnabled: settings.account.isOutboundEnabled,
    isInboundEnabled: settings.account.isInboundEnabled,
    imapIdleEnabled: settings.account.imapIdleEnabled,
    pollIntervalSec: settings.account.pollIntervalSec?.toString() ?? '120',
  }
}

function settingsRequest(draft: EmailSettingsDraft): EmailSettingsSaveRequest {
  return {
    expectedActiveMode: draft.expectedActiveMode,
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
    isOutboundEnabled: draft.isOutboundEnabled,
    isInboundEnabled: draft.isInboundEnabled,
    imapIdleEnabled: draft.imapIdleEnabled,
    pollIntervalSec: Number(draft.pollIntervalSec || 120),
    displayName: draft.displayName,
    defaultDisplayName: draft.defaultDisplayName,
  }
}

function draftsMatch(left: EmailSettingsDraft | null, right: EmailSettingsDraft | null): boolean {
  return JSON.stringify(left) === JSON.stringify(right)
}

export function AgentEmailSettingsScreen({
  agentId,
  emailSettingsUrl,
  testUrl,
  surfaceVariant,
  onBack,
  onSaved,
}: AgentEmailSettingsScreenProps) {
  const inheritedSurfaceVariant = useSettingsSurfaceVariant()
  const surface = surfaceVariant ?? inheritedSurfaceVariant
  const embedded = surface === 'embedded'
  const inputClassName = embedded ? embeddedInputClassName : standaloneInputClassName
  const primaryButtonClassName = embedded ? embeddedPrimaryButtonClassName : standalonePrimaryButtonClassName
  const secondaryButtonClassName = embedded ? embeddedSecondaryButtonClassName : standaloneSecondaryButtonClassName
  const destructiveButtonClassName = embedded ? embeddedDestructiveButtonClassName : standaloneDestructiveButtonClassName
  const headingClassName = embedded ? 'font-semibold text-slate-100' : 'font-semibold text-slate-950'
  const bodyClassName = embedded ? 'text-sm text-slate-400' : 'text-sm text-slate-600'
  const labelClassName = embedded ? 'text-sm font-medium text-slate-200' : 'text-sm font-medium text-slate-800'
  const queryClient = useQueryClient()
  const queryKey = useMemo(() => ['agent-email-settings', agentId, emailSettingsUrl], [agentId, emailSettingsUrl])
  const [error, setError] = useState('')
  const [saveError, setSaveError] = useState('')
  const [notice, setNotice] = useState('')
  const [draft, setDraft] = useState<EmailSettingsDraft | null>(null)
  const [baseline, setBaseline] = useState<EmailSettingsDraft | null>(null)
  const [oauthConnectionProgress, setOAuthConnectionProgress] = useState<OAuthConnectionProgress | null>(null)
  const oauthPopupRef = useRef<Window | null>(null)
  const oauthProviderRef = useRef<'gmail' | 'outlook' | null>(null)
  const draftDirtyRef = useRef(false)

  const settingsQuery = useQuery({
    queryKey,
    queryFn: () => fetchAgentEmailSettings(emailSettingsUrl),
    refetchOnWindowFocus: true,
  })
  const settings = settingsQuery.data
  const hasChanges = !draftsMatch(draft, baseline)
  draftDirtyRef.current = hasChanges

  useEffect(() => {
    if (!settings) return
    const nextBaseline = draftFromSettings(settings)
    setBaseline(nextBaseline)
    setDraft((current) => draftDirtyRef.current ? current : nextBaseline)
  }, [settings])

  const refresh = useCallback(async () => {
    const next = await fetchAgentEmailSettings(emailSettingsUrl)
    queryClient.setQueryData(queryKey, next)
    return next
  }, [emailSettingsUrl, queryClient, queryKey])

  useEffect(() => {
    const handleComplete = (payload: NativeOAuthCompleteMessage) => {
      if (payload.type !== NATIVE_OAUTH_COMPLETE_MESSAGE || payload.providerKey !== oauthProviderRef.current) return
      if (!payload.ok) {
        oauthPopupRef.current = null
        oauthProviderRef.current = null
        setOAuthConnectionProgress(null)
        setError(payload.error || 'Unable to connect the mailbox.')
        return
      }
      setOAuthConnectionProgress((current) => current ? { ...current, phase: 'finishing' } : null)
      void refresh()
        .catch((caught) => setError(safeErrorMessage(caught)))
        .finally(() => {
          oauthPopupRef.current = null
          oauthProviderRef.current = null
          setOAuthConnectionProgress(null)
        })
    }
    const handleMessage = (event: MessageEvent<NativeOAuthCompleteMessage>) => {
      if (event.origin === window.location.origin) handleComplete(event.data)
    }
    const handleStorage = (event: StorageEvent) => {
      if (!event.key?.startsWith(NATIVE_OAUTH_COMPLETE_PREFIX) || !event.newValue) return
      try {
        handleComplete(JSON.parse(event.newValue))
        localStorage.removeItem(event.key)
      } catch (caught) {
        console.warn('Invalid native integration OAuth completion payload', caught)
      }
    }
    window.addEventListener('message', handleMessage)
    window.addEventListener('storage', handleStorage)
    return () => {
      window.removeEventListener('message', handleMessage)
      window.removeEventListener('storage', handleStorage)
    }
  }, [refresh])

  useEffect(() => {
    if (oauthConnectionProgress?.phase !== 'authorizing') return
    let focusTimer: number | null = null
    const handleFocus = () => {
      focusTimer = window.setTimeout(() => {
        const provider = oauthProviderRef.current
        if (!provider) return
        void refresh()
          .then((nextSettings) => {
            if (nextSettings.activeMode === 'oauth' && nextSettings.oauth.provider === provider) {
              oauthPopupRef.current = null
              oauthProviderRef.current = null
            }
            setOAuthConnectionProgress(null)
          })
          .catch((caught) => {
            setError(safeErrorMessage(caught))
            setOAuthConnectionProgress(null)
          })
      }, 750)
    }
    window.addEventListener('focus', handleFocus)
    return () => {
      window.removeEventListener('focus', handleFocus)
      if (focusTimer !== null) window.clearTimeout(focusTimer)
    }
  }, [oauthConnectionProgress?.phase, refresh])

  const actionMutation = useMutation({
    mutationFn: ({ action, values = {} }: { action: string; values?: Record<string, unknown> }) =>
      updateAgentEmailSettingsAction(emailSettingsUrl, action, values),
    onSuccess: ({ settings: next }) => {
      const nextBaseline = draftFromSettings(next)
      queryClient.setQueryData(queryKey, next)
      setBaseline(nextBaseline)
      setDraft(nextBaseline)
      draftDirtyRef.current = false
      setSaveError('')
      onSaved?.({ endpointAddress: next.endpoint.address || null })
    },
  })

  const saveMutation = useMutation({
    mutationFn: (payload: EmailSettingsSaveRequest) => saveAgentEmailSettings(emailSettingsUrl, payload),
    onSuccess: ({ settings: next }) => {
      const nextBaseline = draftFromSettings(next)
      queryClient.setQueryData(queryKey, next)
      setBaseline(nextBaseline)
      setDraft(nextBaseline)
      draftDirtyRef.current = false
      setSaveError('')
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
    onSuccess: ({ ok }, variables) => {
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

  const handleSave = useCallback(async () => {
    if (!draft) return
    setSaveError('')
    setNotice('')
    try {
      await saveMutation.mutateAsync(settingsRequest(draft))
    } catch (caught) {
      setSaveError(apiErrorMessages(caught).join(' '))
    }
  }, [draft, saveMutation])

  const handleCancel = useCallback(() => {
    if (!baseline) return
    setDraft(baseline)
    draftDirtyRef.current = false
    setSaveError('')
    setNotice('')
  }, [baseline])

  const connect = useCallback((provider: 'gmail' | 'outlook') => {
    if (!settings) return
    oauthProviderRef.current = provider
    setOAuthConnectionProgress({ provider, phase: 'authorizing' })
    void run(async () => {
      try {
        const popup = window.open('', `gobii-native-oauth-${provider}`, 'popup=yes,width=520,height=720')
        oauthPopupRef.current = popup
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
      } catch (caught) {
        oauthPopupRef.current?.close()
        oauthPopupRef.current = null
        oauthProviderRef.current = null
        setOAuthConnectionProgress(null)
        throw caught
      }
    })
  }, [agentId, run, settings])

  if (settingsQuery.isError) {
    return <div className="p-5 text-sm text-rose-700">{safeErrorMessage(settingsQuery.error)}</div>
  }
  if (settingsQuery.isLoading || !settings || !draft || !baseline) {
    return <div className="flex min-h-48 items-center justify-center text-slate-600"><Loader2 className="mr-2 h-5 w-5 animate-spin" /> Loading email settings…</div>
  }

  const busy = actionMutation.isPending || saveMutation.isPending || testMutation.isPending || oauthConnectionProgress !== null
  const modeActionDisabled = busy || hasChanges
  const providerLabel = settings.oauth.provider === 'gmail' ? 'Gmail' : settings.oauth.provider === 'outlook' ? 'Outlook' : 'Email OAuth'
  const providerIconPath = settings.oauth.provider === 'gmail'
    ? '/static/images/integrations/native/gmail.svg'
    : settings.oauth.provider === 'outlook' ? '/static/images/integrations/native/outlook.svg' : null
  const request = settingsRequest(draft)
  const connectingProviderLabel = oauthConnectionProgress?.provider === 'gmail' ? 'Gmail' : 'Outlook'

  return (
    <SettingsSurfaceProvider variant={surface}>
      <div className={embedded ? 'space-y-6 pb-6' : `mx-auto w-full max-w-3xl space-y-6 p-4 sm:p-6 ${hasChanges ? 'pb-32 sm:pb-32' : ''}`}>
      {embedded ? (
        <SettingsBanner
          variant="embedded"
          leading={<EmbeddedAgentShellBackButton onClick={onBack} ariaLabel="Back to settings" />}
          eyebrow="Email settings"
          title="Email"
          subtitle={`Choose how ${settings.agent.name} sends and receives external email.`}
        />
      ) : (
        <div className="flex items-start gap-3">
          {onBack ? <button type="button" onClick={onBack} className="mt-0.5 rounded-lg p-2 text-slate-600 hover:bg-blue-50 hover:text-blue-700" aria-label="Back"><ArrowLeft className="h-5 w-5" /></button> : null}
          <div>
            <h1 className="text-xl font-semibold text-slate-950">Email</h1>
            <p className="mt-1 text-sm text-slate-600">Choose how {settings.agent.name} sends and receives external email.</p>
          </div>
        </div>
      )}

      {error ? <div className={embedded ? 'rounded-xl border border-rose-300/25 bg-rose-950/30 px-4 py-3 text-sm text-rose-100' : 'rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800'}>{error}</div> : null}
      {notice ? <div className={embedded ? 'rounded-xl border border-emerald-300/25 bg-emerald-950/30 px-4 py-3 text-sm text-emerald-100' : 'rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800'}>{notice}</div> : null}
      {oauthConnectionProgress ? (
        <div className={embedded ? 'flex items-start gap-3 rounded-xl border border-blue-300/25 bg-blue-950/30 px-4 py-3 text-sm text-blue-100' : 'flex items-start gap-3 rounded-lg border border-blue-200 bg-blue-50 px-4 py-3 text-sm text-blue-900'} role="status" aria-live="polite">
          <Loader2 className="mt-0.5 h-4 w-4 shrink-0 animate-spin" aria-hidden="true" />
          <div>
            <p className="font-semibold">{oauthConnectionProgress.phase === 'finishing' ? `Finishing ${connectingProviderLabel} setup…` : `Connecting ${connectingProviderLabel}…`}</p>
            <p className={embedded ? 'mt-1 text-blue-200/80' : 'mt-1 text-blue-800'}>{oauthConnectionProgress.phase === 'finishing' ? 'Saving the mailbox and refreshing its sending and receiving status.' : 'Complete authorization in the popup. Mailbox setup can take a moment after approval.'}</p>
          </div>
        </div>
      ) : null}

      <SettingsSurface variant={surface} as="section" padding="lg" shadowClassName={embedded ? 'shadow-none' : undefined}>
        <div className="flex items-center gap-2"><Mail className={embedded ? 'h-5 w-5 text-blue-300' : 'h-5 w-5 text-blue-600'} /><h2 className={headingClassName}>Gobii email address</h2></div>
        <p className={`mt-2 ${bodyClassName}`}>This address always remains available for sending and receiving.</p>
        <div className="mt-4 grid gap-4 sm:grid-cols-2">
          <label className={labelClassName}>Address<input className={inputClassName} value={settings.defaultEndpoint.address} readOnly /></label>
          <label className={labelClassName}>Display name<input className={inputClassName} value={draft.defaultDisplayName} disabled={busy} onChange={(event) => setDraft({ ...draft, defaultDisplayName: event.target.value })} /></label>
        </div>
      </SettingsSurface>

      {settings.activeMode === 'none' ? (
        <SettingsSurface variant={surface} as="section" padding="lg" shadowClassName={embedded ? 'shadow-none' : undefined}>
          <h2 className={headingClassName}>Connect an external mailbox</h2>
          <p className={`mt-1 ${bodyClassName}`}>No server settings are needed for Gmail or Outlook.</p>
          <div className="mt-4 flex flex-wrap gap-3">
            <button type="button" className={primaryButtonClassName} disabled={modeActionDisabled} onClick={() => connect('gmail')}>{oauthConnectionProgress?.provider === 'gmail' ? <Loader2 className="h-5 w-5 animate-spin" aria-hidden="true" /> : <img src="/static/images/integrations/native/gmail.svg" alt="" className="h-5 w-5" />}{oauthConnectionProgress?.provider === 'gmail' ? 'Connecting Gmail…' : 'Connect Gmail'}</button>
            <button type="button" className={primaryButtonClassName} disabled={modeActionDisabled} onClick={() => connect('outlook')}>{oauthConnectionProgress?.provider === 'outlook' ? <Loader2 className="h-5 w-5 animate-spin" aria-hidden="true" /> : <img src="/static/images/integrations/native/outlook.svg" alt="" className="h-5 w-5" />}{oauthConnectionProgress?.provider === 'outlook' ? 'Connecting Outlook…' : 'Connect Outlook'}</button>
            <button type="button" className={secondaryButtonClassName} disabled={modeActionDisabled} onClick={() => void run(() => actionMutation.mutateAsync({ action: 'enable_custom' }))}>Enable custom SMTP/IMAP</button>
          </div>
          {settings.customConfigured ? <p className={`mt-3 ${bodyClassName}`}>Your previous custom SMTP/IMAP settings are saved and will reappear when enabled.</p> : null}
        </SettingsSurface>
      ) : null}

      {settings.activeMode === 'oauth' ? (
        <SettingsSurface variant={surface} as="section" padding="lg" shadowClassName={embedded ? 'shadow-none' : undefined}>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-3">
              {providerIconPath ? <img src={providerIconPath} alt="" className="h-6 w-6 shrink-0" /> : null}
              <div><h2 className={headingClassName}>{providerLabel}</h2><p className={`mt-1 ${bodyClassName}`}>{settings.oauth.mailboxAddress}</p></div>
            </div>
            <button type="button" className={destructiveButtonClassName} disabled={modeActionDisabled} onClick={() => void run(async () => {
              if (settings.oauth.legacy) await actionMutation.mutateAsync({ action: 'disconnect_legacy_oauth' })
              else if (settings.oauth.revokeUrl) {
                await revokeNativeIntegration(settings.oauth.revokeUrl, undefined, agentId)
                await refresh()
              }
            })}><Unplug className="h-4 w-4" />Disconnect</button>
          </div>
          <label className={`mt-5 block ${labelClassName}`}>Display name<input className={inputClassName} value={draft.displayName} disabled={busy} onChange={(event) => setDraft({ ...draft, displayName: event.target.value })} /></label>
          <div className="mt-5 grid gap-4 sm:grid-cols-2">
            <DirectionToggle embedded={embedded} label="Send email" checked={draft.isOutboundEnabled} disabled={busy} onChange={(isOutboundEnabled) => setDraft({ ...draft, isOutboundEnabled })} />
            <DirectionToggle embedded={embedded} label="Receive email" checked={draft.isInboundEnabled} disabled={busy} onChange={(isInboundEnabled) => setDraft({ ...draft, isInboundEnabled })} />
          </div>
        </SettingsSurface>
      ) : null}

      {settings.activeMode === 'custom' ? (
        <SettingsSurface variant={surface} as="section" padding="lg" shadowClassName={embedded ? 'shadow-none' : undefined}>
          <div className="flex flex-wrap items-center justify-between gap-3"><div><h2 className={headingClassName}>Custom SMTP/IMAP</h2><p className={`mt-1 ${bodyClassName}`}>Use the server settings supplied by your email provider.</p></div><button type="button" className={secondaryButtonClassName} disabled={modeActionDisabled} onClick={() => void run(() => actionMutation.mutateAsync({ action: 'disable_custom' }))}>Disable</button></div>
          <div className="mt-5 grid gap-4 sm:grid-cols-2">
            <TextField label="Email address" value={draft.address} disabled={busy} onChange={(address) => setDraft({ ...draft, address })} />
            <TextField label="Display name" value={draft.displayName} disabled={busy} onChange={(displayName) => setDraft({ ...draft, displayName })} />
            <TextField label="SMTP server" value={draft.smtpHost} disabled={busy} onChange={(smtpHost) => setDraft({ ...draft, smtpHost })} />
            <TextField label="SMTP port" type="number" value={draft.smtpPort} disabled={busy} onChange={(smtpPort) => setDraft({ ...draft, smtpPort })} />
            <SelectField label="SMTP security" value={draft.smtpSecurity} disabled={busy} options={[['starttls', 'STARTTLS'], ['ssl', 'SSL'], ['none', 'None']]} onChange={(smtpSecurity) => setDraft({ ...draft, smtpSecurity })} />
            <SelectField label="SMTP authentication" value={draft.smtpAuth} disabled={busy} options={[['login', 'Login'], ['plain', 'Plain'], ['none', 'None']]} onChange={(smtpAuth) => setDraft({ ...draft, smtpAuth })} />
            <TextField label="SMTP username" value={draft.smtpUsername} disabled={busy} onChange={(smtpUsername) => setDraft({ ...draft, smtpUsername })} />
            <TextField label="SMTP password" type="password" value={draft.smtpPassword} disabled={busy} onChange={(smtpPassword) => setDraft({ ...draft, smtpPassword })} placeholder={settings.account.hasSmtpPassword ? 'Saved password' : ''} />
            <TextField label="IMAP server" value={draft.imapHost} disabled={busy} onChange={(imapHost) => setDraft({ ...draft, imapHost })} />
            <TextField label="IMAP port" type="number" value={draft.imapPort} disabled={busy} onChange={(imapPort) => setDraft({ ...draft, imapPort })} />
            <SelectField label="IMAP security" value={draft.imapSecurity} disabled={busy} options={[['ssl', 'SSL'], ['starttls', 'STARTTLS'], ['none', 'None']]} onChange={(imapSecurity) => setDraft({ ...draft, imapSecurity })} />
            <SelectField label="IMAP authentication" value={draft.imapAuth} disabled={busy} options={[['login', 'Login'], ['none', 'None']]} onChange={(imapAuth) => setDraft({ ...draft, imapAuth })} />
            <TextField label="IMAP username" value={draft.imapUsername} disabled={busy} onChange={(imapUsername) => setDraft({ ...draft, imapUsername })} />
            <TextField label="IMAP password" type="password" value={draft.imapPassword} disabled={busy} onChange={(imapPassword) => setDraft({ ...draft, imapPassword })} placeholder={settings.account.hasImapPassword ? 'Saved password' : ''} />
            <TextField label="IMAP folder" value={draft.imapFolder} disabled={busy} onChange={(imapFolder) => setDraft({ ...draft, imapFolder })} />
            <TextField label="Check every (seconds)" type="number" value={draft.pollIntervalSec} disabled={busy} onChange={(pollIntervalSec) => setDraft({ ...draft, pollIntervalSec })} />
          </div>
          <div className="mt-5 grid gap-4 sm:grid-cols-2">
            <DirectionToggle label="Send email" checked={draft.isOutboundEnabled} disabled={busy} onChange={(isOutboundEnabled) => setDraft({ ...draft, isOutboundEnabled })} />
            <DirectionToggle label="Receive email" checked={draft.isInboundEnabled} disabled={busy} onChange={(isInboundEnabled) => setDraft({ ...draft, isInboundEnabled })} />
          </div>
          <div className="mt-5 flex flex-wrap gap-3"><button type="button" className={secondaryButtonClassName} disabled={busy} onClick={() => void run(() => testMutation.mutateAsync({ payload: request, testOutbound: true, testInbound: true }))}><RefreshCw className="h-4 w-4" />Test sending and receiving</button></div>
        </SettingsSurface>
      ) : null}

      <SaveBar
        id="agent-email-settings-save-bar"
        visible={hasChanges}
        onCancel={handleCancel}
        onSave={handleSave}
        busy={saveMutation.isPending}
        error={saveError}
        variant={surface}
        placement={embedded ? 'sticky' : 'fixed'}
      />
      </div>
    </SettingsSurfaceProvider>
  )
}

function TextField({ label, value, onChange, type = 'text', placeholder = '', disabled = false }: { label: string; value: string; onChange: (value: string) => void; type?: string; placeholder?: string; disabled?: boolean }) {
  const embedded = useSettingsSurfaceVariant() === 'embedded'
  return <label className={embedded ? 'text-sm font-medium text-slate-200' : 'text-sm font-medium text-slate-800'}>{label}<input className={embedded ? embeddedInputClassName : standaloneInputClassName} type={type} value={value} placeholder={placeholder} disabled={disabled} onChange={(event) => onChange(event.target.value)} /></label>
}

function SelectField({ label, value, options, onChange, disabled = false }: { label: string; value: string; options: Array<[string, string]>; onChange: (value: string) => void; disabled?: boolean }) {
  const embedded = useSettingsSurfaceVariant() === 'embedded'
  return <label className={embedded ? 'text-sm font-medium text-slate-200' : 'text-sm font-medium text-slate-800'}>{label}<select className={embedded ? embeddedInputClassName : standaloneInputClassName} value={value} disabled={disabled} onChange={(event) => onChange(event.target.value)}>{options.map(([optionValue, optionLabel]) => <option key={optionValue} value={optionValue}>{optionLabel}</option>)}</select></label>
}

function DirectionToggle({ label, checked, disabled, onChange, embedded: embeddedOverride }: { label: string; checked: boolean; disabled: boolean; onChange: (checked: boolean) => void; embedded?: boolean }) {
  const inheritedEmbedded = useSettingsSurfaceVariant() === 'embedded'
  const embedded = embeddedOverride ?? inheritedEmbedded
  const className = embedded
    ? 'flex cursor-pointer items-center justify-between rounded-lg border border-slate-200/20 bg-slate-950/30 px-4 py-3 text-sm font-semibold text-slate-200'
    : 'flex cursor-pointer items-center justify-between rounded-lg border border-slate-300 bg-white px-4 py-3 text-sm font-semibold text-slate-800'
  return (
    <AriaSwitch
      isSelected={checked}
      isDisabled={disabled}
      onChange={onChange}
      className={`${className} group focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-300/70 data-[disabled]:cursor-not-allowed data-[disabled]:opacity-60`}
    >
      <span>{label}</span>
      <span
        aria-hidden="true"
        className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full p-1 transition-colors group-data-[selected]:bg-blue-600 ${embedded ? 'bg-slate-600' : 'bg-slate-300'}`}
      >
        <span className="h-4 w-4 rounded-full bg-white shadow-sm transition-transform group-data-[selected]:translate-x-5" />
      </span>
    </AriaSwitch>
  )
}
