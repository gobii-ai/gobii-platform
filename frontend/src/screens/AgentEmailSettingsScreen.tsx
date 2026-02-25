import { useCallback, useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, CheckCircle2, Mail, ShieldCheck } from 'lucide-react'

import {
  ensureAgentEmailAccount,
  fetchAgentEmailSettings,
  fetchEmailOAuthStatus,
  revokeEmailOAuth,
  saveAgentEmailSettings,
  startEmailOAuth,
  testAgentEmailSettings,
  type AgentEmailSettingsPayload,
  type EmailSettingsSaveRequest,
} from '../api/agentEmailSettings'
import { HttpError } from '../api/http'

type AgentEmailSettingsScreenProps = {
  agentId: string
  emailSettingsUrl: string
  ensureAccountUrl: string
  testUrl: string
}

type ProviderKey = 'gmail' | 'custom'
type ConnectionType = 'oauth' | 'manual'

type DraftState = {
  endpointAddress: string
  provider: ProviderKey | ''
  connectionType: ConnectionType | ''
  isOutboundEnabled: boolean
  isInboundEnabled: boolean
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
  imapIdleEnabled: boolean
  pollIntervalSec: string
}

const oauthScope = 'https://mail.google.com/'
const gmailAuthEndpoint = 'https://accounts.google.com/o/oauth2/v2/auth'
const gmailTokenEndpoint = 'https://oauth2.googleapis.com/token'

function randomString(length: number): string {
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~'
  const values = new Uint8Array(length)
  window.crypto.getRandomValues(values)
  let result = ''
  values.forEach((value) => {
    result += alphabet[value % alphabet.length]
  })
  return result
}

async function sha256(input: string): Promise<ArrayBuffer> {
  const encoder = new TextEncoder()
  return window.crypto.subtle.digest('SHA-256', encoder.encode(input))
}

function base64UrlEncode(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer)
  let binary = ''
  for (let i = 0; i < bytes.length; i += 1) {
    binary += String.fromCharCode(bytes[i])
  }
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

function toPortValue(value: string): number | null {
  const trimmed = value.trim()
  if (!trimmed) {
    return null
  }
  const num = Number(trimmed)
  return Number.isFinite(num) ? num : null
}

function draftFromSettings(settings: AgentEmailSettingsPayload): DraftState {
  const gmailDefaults = settings.providerDefaults.gmail
  const hasMailDirectionSelected = settings.account.isInboundEnabled || settings.account.isOutboundEnabled
  const hasOAuthConfigured =
    settings.oauth.connected
    || settings.account.smtpAuth === 'oauth2'
    || settings.account.imapAuth === 'oauth2'
  const inferredProvider: ProviderKey =
    hasOAuthConfigured
    || settings.oauth.provider.toLowerCase() === 'gmail'
    || settings.account.smtpHost === (gmailDefaults?.smtp_host ?? '')
      ? 'gmail'
      : 'custom'
  const hasConfiguredConnection = hasOAuthConfigured || (hasMailDirectionSelected && Boolean(
    settings.account.exists
    || settings.account.smtpHost
    || settings.account.imapHost,
  ))
  const provider: ProviderKey | '' = hasConfiguredConnection ? inferredProvider : ''
  const connectionType: ConnectionType | '' =
    !hasConfiguredConnection
      ? ''
      : hasOAuthConfigured || settings.account.connectionMode === 'oauth2'
        ? 'oauth'
        : 'manual'

  return {
    endpointAddress: settings.endpoint.address || '',
    provider,
    connectionType,
    isOutboundEnabled: settings.account.isOutboundEnabled,
    isInboundEnabled: settings.account.isInboundEnabled,
    smtpHost: settings.account.smtpHost || '',
    smtpPort: settings.account.smtpPort ? String(settings.account.smtpPort) : '',
    smtpSecurity: settings.account.smtpSecurity || 'starttls',
    smtpAuth: settings.account.smtpAuth || 'login',
    smtpUsername: settings.account.smtpUsername || settings.endpoint.address || '',
    smtpPassword: '',
    imapHost: settings.account.imapHost || '',
    imapPort: settings.account.imapPort ? String(settings.account.imapPort) : '',
    imapSecurity: settings.account.imapSecurity || 'ssl',
    imapAuth: settings.account.imapAuth || 'login',
    imapUsername: settings.account.imapUsername || settings.endpoint.address || '',
    imapPassword: '',
    imapFolder: settings.account.imapFolder || 'INBOX',
    imapIdleEnabled: settings.account.imapIdleEnabled,
    pollIntervalSec: String(settings.account.pollIntervalSec || 120),
  }
}

function describeHttpError(error: unknown): string {
  if (!(error instanceof HttpError)) {
    return error instanceof Error ? error.message : 'Something went wrong.'
  }
  if (typeof error.body === 'string' && error.body.trim()) {
    return error.body
  }
  if (error.body && typeof error.body === 'object') {
    const body = error.body as Record<string, unknown>
    const rawError = body.error
    if (typeof rawError === 'string' && rawError) {
      return rawError
    }
    const errors = body.errors
    if (errors && typeof errors === 'object') {
      for (const value of Object.values(errors as Record<string, unknown>)) {
        if (Array.isArray(value) && value.length > 0 && typeof value[0] === 'string') {
          return value[0]
        }
      }
    }
  }
  return `${error.status} ${error.statusText}`
}

function applyGmailDefaults(draft: DraftState, settings: AgentEmailSettingsPayload): DraftState {
  const gmail = settings.providerDefaults.gmail
  if (!gmail) {
    return draft
  }
  const oauthMode = draft.connectionType === 'oauth'
  return {
    ...draft,
    smtpHost: gmail.smtp_host,
    smtpPort: String(gmail.smtp_port),
    smtpSecurity: gmail.smtp_security,
    smtpAuth: oauthMode ? 'oauth2' : 'login',
    smtpUsername: draft.smtpUsername || draft.endpointAddress,
    imapHost: gmail.imap_host,
    imapPort: String(gmail.imap_port),
    imapSecurity: gmail.imap_security,
    imapAuth: oauthMode ? 'oauth2' : 'login',
    imapUsername: draft.imapUsername || draft.endpointAddress,
    imapFolder: draft.imapFolder || 'INBOX',
  }
}

function syncUsernamesWithEndpoint(current: DraftState, endpointAddress: string): DraftState {
  const previousEndpoint = current.endpointAddress.trim()
  const nextEndpoint = endpointAddress.trim()
  const shouldUpdateSmtpUsername = !current.smtpUsername.trim() || current.smtpUsername.trim() === previousEndpoint
  const shouldUpdateImapUsername = !current.imapUsername.trim() || current.imapUsername.trim() === previousEndpoint
  return {
    ...current,
    endpointAddress,
    smtpUsername: shouldUpdateSmtpUsername && nextEndpoint ? nextEndpoint : current.smtpUsername,
    imapUsername: shouldUpdateImapUsername && nextEndpoint ? nextEndpoint : current.imapUsername,
  }
}

function buildSavePayload(draft: DraftState): EmailSettingsSaveRequest {
  const oauthMode = draft.provider === 'gmail' && draft.connectionType === 'oauth'
  return {
    endpointAddress: draft.endpointAddress.trim(),
    connectionMode: oauthMode ? 'oauth2' : 'custom',
    oauthProvider: oauthMode ? 'gmail' : '',
    smtpHost: draft.smtpHost.trim(),
    smtpPort: toPortValue(draft.smtpPort),
    smtpSecurity: draft.smtpSecurity,
    smtpAuth: oauthMode ? 'oauth2' : draft.smtpAuth,
    smtpUsername: draft.smtpUsername.trim(),
    smtpPassword: draft.smtpPassword.trim(),
    imapHost: draft.imapHost.trim(),
    imapPort: toPortValue(draft.imapPort),
    imapSecurity: draft.imapSecurity,
    imapAuth: oauthMode ? 'oauth2' : draft.imapAuth,
    imapUsername: draft.imapUsername.trim(),
    imapPassword: draft.imapPassword.trim(),
    imapFolder: draft.imapFolder.trim() || 'INBOX',
    isOutboundEnabled: draft.isOutboundEnabled,
    isInboundEnabled: draft.isInboundEnabled,
    imapIdleEnabled: draft.imapIdleEnabled,
    pollIntervalSec: Number(draft.pollIntervalSec || '120'),
  }
}

export function AgentEmailSettingsScreen({
  agentId,
  emailSettingsUrl,
  ensureAccountUrl,
  testUrl,
}: AgentEmailSettingsScreenProps) {
  const queryClient = useQueryClient()
  const queryKey = useMemo(() => ['agent-email-settings', agentId, emailSettingsUrl], [agentId, emailSettingsUrl])
  const [draft, setDraft] = useState<DraftState | null>(null)
  const [banner, setBanner] = useState<string | null>(null)
  const [errorBanner, setErrorBanner] = useState<string | null>(null)
  const [showGuidance, setShowGuidance] = useState(false)
  const [guidanceAck, setGuidanceAck] = useState(false)
  const [guidanceError, setGuidanceError] = useState<string | null>(null)
  const [pendingOAuthSettings, setPendingOAuthSettings] = useState<AgentEmailSettingsPayload | null>(null)
  const [testResults, setTestResults] = useState<{ smtp?: { ok: boolean; error: string }; imap?: { ok: boolean; error: string } }>({})

  const settingsQuery = useQuery({
    queryKey,
    queryFn: () => fetchAgentEmailSettings(emailSettingsUrl),
    refetchOnWindowFocus: false,
  })

  const ensureAccountMutation = useMutation({
    mutationFn: (payload: { endpointAddress: string }) => ensureAgentEmailAccount(ensureAccountUrl, payload),
    onSuccess: (response) => {
      queryClient.setQueryData(queryKey, response.settings)
      setPendingOAuthSettings(response.settings)
    },
  })

  const saveMutation = useMutation({
    mutationFn: (payload: EmailSettingsSaveRequest) => saveAgentEmailSettings(emailSettingsUrl, payload),
    onSuccess: (response) => {
      queryClient.setQueryData(queryKey, response.settings)
      setErrorBanner(null)
    },
  })

  const testMutation = useMutation({
    mutationFn: (payload: EmailSettingsSaveRequest & { testOutbound: boolean; testInbound: boolean }) =>
      testAgentEmailSettings(testUrl, payload),
    onSuccess: (response) => {
      setTestResults({
        smtp: response.results.smtp ?? undefined,
        imap: response.results.imap ?? undefined,
      })
      if (!response.ok) {
        setErrorBanner('One or more tests failed. Review the errors below.')
      } else {
        setBanner('Connection test succeeded.')
        setErrorBanner(null)
      }
    },
  })

  const settings = settingsQuery.data

  useEffect(() => {
    if (!settings) {
      return
    }
    const nextDraft = draftFromSettings(settings)
    setDraft((current) => {
      if (!current) {
        return nextDraft
      }
      return {
        ...nextDraft,
        smtpPassword: current.smtpPassword,
        imapPassword: current.imapPassword,
      }
    })
  }, [settings])

  useEffect(() => {
    const handleStorage = (event: StorageEvent) => {
      if (!event.key || !event.key.startsWith('gobii:email_oauth_complete')) {
        return
      }
      queryClient.invalidateQueries({ queryKey })
      if (settings?.oauth.statusUrl) {
        void fetchEmailOAuthStatus(settings.oauth.statusUrl)
      }
      setBanner('Gmail OAuth connected. You can now save settings.')
      setErrorBanner(null)
    }
    window.addEventListener('storage', handleStorage)
    return () => window.removeEventListener('storage', handleStorage)
  }, [queryClient, queryKey, settings?.oauth.statusUrl])

  const oauthConnected = Boolean(settings?.oauth.connected)
  const hasAddress = Boolean(draft?.endpointAddress.includes('@'))
  const hasMailDirection = Boolean(draft && (draft.isInboundEnabled || draft.isOutboundEnabled))
  const hasProvider = Boolean(draft?.provider)
  const hasConnectionType = Boolean(draft?.connectionType)
  const oauthRequired = Boolean(draft && draft.provider === 'gmail' && draft.connectionType === 'oauth')
  const hasSavedSmtpPassword = Boolean(settings?.account.hasSmtpPassword)
  const hasSavedImapPassword = Boolean(settings?.account.hasImapPassword)
  const setupValid = hasAddress && hasMailDirection && hasProvider && hasConnectionType
  const canSubmit = setupValid && (!oauthRequired || oauthConnected)

  const updateDraft = useCallback((updater: (current: DraftState) => DraftState) => {
    setDraft((current) => (current ? updater(current) : current))
  }, [])

  const launchOAuth = useCallback(async (resolvedSettings: AgentEmailSettingsPayload) => {
    if (!resolvedSettings.account.id) {
      throw new Error('Email account was not created yet.')
    }
    const popup = window.open('', '_blank')
    if (!popup) {
      throw new Error('Allow pop-ups to continue OAuth.')
    }
    const state = randomString(32)
    const codeVerifier = randomString(64)
    const codeChallenge = base64UrlEncode(await sha256(codeVerifier))
    const callbackUrl = new URL(resolvedSettings.oauth.callbackPath, window.location.origin).toString()

    const session = await startEmailOAuth(resolvedSettings.oauth.startUrl, {
      account_id: resolvedSettings.account.id,
      provider: 'gmail',
      scope: oauthScope,
      token_endpoint: gmailTokenEndpoint,
      use_gobii_app: true,
      redirect_uri: callbackUrl,
      state,
      code_verifier: codeVerifier,
      code_challenge: codeChallenge,
      code_challenge_method: 'S256',
      metadata: {
        provider: 'gmail',
        authorization_endpoint: gmailAuthEndpoint,
        token_endpoint: gmailTokenEndpoint,
        sasl_mechanism: 'XOAUTH2',
      },
    })

    const stateKey = session.state || state
    localStorage.setItem(
      `gobii:email_oauth_state:${stateKey}`,
      JSON.stringify({
        sessionId: session.session_id,
        accountId: resolvedSettings.account.id,
        returnUrl: window.location.pathname,
      }),
    )

    const params = new URLSearchParams({
      response_type: 'code',
      client_id: session.client_id,
      redirect_uri: callbackUrl,
      scope: oauthScope,
      state: stateKey,
      code_challenge: codeChallenge,
      code_challenge_method: 'S256',
      access_type: 'offline',
      prompt: 'consent',
    })
    popup.location.href = `${gmailAuthEndpoint}?${params.toString()}`
    popup.focus()
  }, [])

  const handleConnectOAuth = useCallback(async () => {
    if (!draft) {
      return
    }
    setErrorBanner(null)
    setBanner(null)
    try {
      const ensured = await ensureAccountMutation.mutateAsync({ endpointAddress: draft.endpointAddress.trim() })
      const nextSettings = ensured.settings
      setPendingOAuthSettings(nextSettings)
      setGuidanceAck(false)
      setGuidanceError(null)
      setShowGuidance(true)
    } catch (error) {
      setErrorBanner(describeHttpError(error))
    }
  }, [draft, ensureAccountMutation])

  const handleContinueFromGuidance = useCallback(async () => {
    if (!guidanceAck) {
      setGuidanceError('Check the box before continuing.')
      return
    }
    if (!pendingOAuthSettings) {
      setGuidanceError('Unable to start OAuth right now. Please try again.')
      return
    }
    setShowGuidance(false)
    setGuidanceError(null)
    try {
      await launchOAuth(pendingOAuthSettings)
    } catch (error) {
      setErrorBanner(error instanceof Error ? error.message : 'Unable to start OAuth.')
    }
  }, [guidanceAck, launchOAuth, pendingOAuthSettings])

  const handleDisconnectOAuth = useCallback(async () => {
    if (!settings?.oauth.revokeUrl) {
      return
    }
    try {
      await revokeEmailOAuth(settings.oauth.revokeUrl)
      await queryClient.invalidateQueries({ queryKey })
      setBanner('OAuth credentials removed.')
      setErrorBanner(null)
    } catch (error) {
      setErrorBanner(describeHttpError(error))
    }
  }, [queryClient, queryKey, settings?.oauth.revokeUrl])

  const handleSave = useCallback(async () => {
    if (!draft) {
      return
    }
    const payload = buildSavePayload(draft)
    setBanner(null)
    setErrorBanner(null)
    try {
      const testResponse = await testMutation.mutateAsync({
        ...payload,
        testOutbound: draft.isOutboundEnabled,
        testInbound: draft.isInboundEnabled,
      })
      if (!testResponse.ok) {
        return
      }
      const saveResponse = await saveMutation.mutateAsync(payload)
      const nextUrl = saveResponse.settings.agent.backUrl || settings?.agent.backUrl
      if (nextUrl) {
        window.location.assign(nextUrl)
      }
    } catch (error) {
      setErrorBanner(describeHttpError(error))
    }
  }, [draft, saveMutation, settings?.agent.backUrl, testMutation])

  if (settingsQuery.error && !settings) {
    return (
      <div className="rounded-xl bg-amber-50 p-6 text-sm text-amber-800">
        Failed to load email settings. {describeHttpError(settingsQuery.error)}
      </div>
    )
  }

  if (settingsQuery.isLoading || !settings || !draft) {
    return <div className="rounded-xl bg-white p-6 text-sm text-slate-700">Loading email settings...</div>
  }

  return (
    <div className="space-y-5">
      <div className="rounded-xl bg-white p-6 shadow-sm">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-2xl font-semibold text-slate-900">Agent Email Setup</h1>
            <p className="mt-1 text-sm text-slate-600">
              {settings.agent.name}: configure, test, and save in one flow.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <a href={settings.agent.helpUrl} target="_blank" rel="noreferrer" className="rounded-lg bg-blue-600 px-3 py-2 text-sm font-semibold text-white">
              Help
            </a>
            <a href={settings.agent.backUrl} className="rounded-lg border border-blue-200 px-3 py-2 text-sm font-semibold text-blue-700">
              Back to Agent
            </a>
          </div>
        </div>
      </div>

      {banner && <div className="rounded-xl bg-emerald-50 px-4 py-3 text-sm text-emerald-800">{banner}</div>}
      {errorBanner && (
        <div className="rounded-xl bg-amber-50 px-4 py-3 text-sm text-amber-800 inline-flex items-start gap-2">
          <AlertTriangle className="mt-0.5 h-4 w-4" aria-hidden="true" />
          <span>{errorBanner}</span>
        </div>
      )}

      <div className="rounded-xl bg-white p-5 shadow-sm">
        <div className="space-y-4">
            <div>
              <label className="text-sm font-semibold text-slate-700">Agent Email Address</label>
                <input
                  type="email"
                  value={draft.endpointAddress}
                  onChange={(event) => {
                    const endpointAddress = event.currentTarget.value
                    updateDraft((current) => syncUsernamesWithEndpoint(current, endpointAddress))
                  }}
                  className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
                />
              <p className="mt-1 text-xs text-slate-600">
                This is the email address your agent uses to send and receive emails.
              </p>
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
              <label className="rounded-lg border border-blue-200 p-3 text-sm">
                <input
                  type="checkbox"
                  checked={draft.isOutboundEnabled}
                  onChange={(event) => {
                    const isOutboundEnabled = event.currentTarget.checked
                    updateDraft((current) => {
                      const hasSelection = isOutboundEnabled || current.isInboundEnabled
                      return {
                        ...current,
                        isOutboundEnabled,
                        provider: hasSelection ? current.provider : '',
                        connectionType: hasSelection ? current.connectionType : '',
                      }
                    })
                  }}
                  className="mr-2 rounded"
                />
                Enable outbound (SMTP)
              </label>
              <label className="rounded-lg border border-blue-200 p-3 text-sm">
                <input
                  type="checkbox"
                  checked={draft.isInboundEnabled}
                  onChange={(event) => {
                    const isInboundEnabled = event.currentTarget.checked
                    updateDraft((current) => {
                      const hasSelection = current.isOutboundEnabled || isInboundEnabled
                      return {
                        ...current,
                        isInboundEnabled,
                        provider: hasSelection ? current.provider : '',
                        connectionType: hasSelection ? current.connectionType : '',
                      }
                    })
                  }}
                  className="mr-2 rounded"
                />
                Enable inbound (IMAP)
              </label>
            </div>

            {!hasMailDirection && (
              <div className="rounded-lg bg-blue-50 px-4 py-3 text-sm text-blue-900">
                Choose inbound and/or outbound first.
              </div>
            )}

            {hasMailDirection && (
              <div>
                <label className="text-sm font-semibold text-slate-700">Provider</label>
                <select
                  value={draft.provider}
                  onChange={(event) => {
                    const provider = event.currentTarget.value as ProviderKey
                    updateDraft((current) => {
                      const next: DraftState = {
                        ...current,
                        provider,
                        connectionType: provider === 'custom' ? 'manual' : '',
                      }
                      if (provider === 'custom' && current.endpointAddress.trim()) {
                        if (!next.smtpUsername.trim()) {
                          next.smtpUsername = current.endpointAddress.trim()
                        }
                        if (!next.imapUsername.trim()) {
                          next.imapUsername = current.endpointAddress.trim()
                        }
                      }
                      return provider === 'gmail' ? applyGmailDefaults(next, settings) : next
                    })
                  }}
                  className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
                >
                  <option value="" disabled>
                    Select provider
                  </option>
                  <option value="gmail">Gmail</option>
                  <option value="custom">Other provider</option>
                </select>
              </div>
            )}

            {hasMailDirection && !hasProvider && (
              <div className="rounded-lg bg-blue-50 px-4 py-3 text-sm text-blue-900">
                Select a provider to continue.
              </div>
            )}

            {hasMailDirection && draft.provider === 'gmail' && (
              <div className="space-y-2">
                <p className="text-sm font-semibold text-slate-700">Connection Type</p>
                <div className="grid gap-3 sm:grid-cols-2">
                  <button
                    type="button"
                    onClick={() =>
                      updateDraft((current) =>
                        applyGmailDefaults({ ...current, connectionType: 'oauth' }, settings),
                      )
                    }
                    className={`rounded-lg border p-3 text-left text-sm ${
                      draft.connectionType === 'oauth' ? 'border-blue-500 bg-blue-50 text-blue-900' : 'border-blue-200'
                    }`}
                  >
                    <div className="font-semibold">OAuth (recommended)</div>
                    <div className="mt-1 text-slate-700">Connect using Google OAuth and skip app passwords.</div>
                  </button>
                  <button
                    type="button"
                    onClick={() =>
                      updateDraft((current) =>
                        applyGmailDefaults({ ...current, connectionType: 'manual' }, settings),
                      )
                    }
                    className={`rounded-lg border p-3 text-left text-sm ${
                      draft.connectionType === 'manual' ? 'border-blue-500 bg-blue-50 text-blue-900' : 'border-blue-200'
                    }`}
                  >
                    <div className="font-semibold">Manual SMTP/IMAP</div>
                    <div className="mt-1 text-slate-700">Use an app password.</div>
                  </button>
                </div>
              </div>
            )}

            {hasMailDirection && draft.provider === 'gmail' && !hasConnectionType && (
              <div className="rounded-lg bg-blue-50 px-4 py-3 text-sm text-blue-900">
                Choose a connection type to continue.
              </div>
            )}

            {hasMailDirection && draft.provider === 'gmail' && draft.connectionType === 'oauth' && (
              <div className="rounded-lg bg-blue-50 p-4 space-y-3">
                <div className="space-y-2 text-sm">
                  <div className="inline-flex items-center gap-2 text-slate-700">
                    {oauthConnected ? <CheckCircle2 className="h-4 w-4 text-emerald-600" /> : <Mail className="h-4 w-4 text-blue-700" />}
                    <span>{oauthConnected ? 'Gmail OAuth connected' : 'OAuth connection required before saving'}</span>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <button
                      type="button"
                      onClick={handleConnectOAuth}
                      className="rounded-lg bg-blue-600 px-3 py-2 text-sm font-semibold text-white"
                      disabled={ensureAccountMutation.isPending}
                    >
                      {ensureAccountMutation.isPending ? 'Preparing...' : 'Connect Gmail OAuth'}
                    </button>
                    <button
                      type="button"
                      onClick={handleDisconnectOAuth}
                      className="rounded-lg border border-blue-300 px-3 py-2 text-sm font-semibold text-blue-800"
                      disabled={!oauthConnected}
                    >
                      Disconnect OAuth
                    </button>
                  </div>
                </div>
              </div>
            )}

            {hasMailDirection && draft.provider === 'gmail' && draft.connectionType === 'manual' && (
              <div className="rounded-lg bg-white p-3 text-sm text-slate-700">
                <div className="inline-flex items-center gap-2 font-semibold text-slate-900">
                  <ShieldCheck className="h-4 w-4 text-blue-700" />
                  Gmail app password checklist
                </div>
                <ol className="mt-2 list-decimal list-inside space-y-1">
                  <li>Enable 2-Step Verification on your Google account.</li>
                  <li>
                    Create an{' '}
                    <a
                      href="https://myaccount.google.com/apppasswords"
                      target="_blank"
                      rel="noreferrer"
                      className="font-semibold text-blue-700 underline"
                    >
                      App Password
                    </a>
                    {' '}for Mail.
                  </li>
                  <li>Use that 16-character app password below for SMTP/IMAP.</li>
                </ol>
              </div>
            )}

            {hasMailDirection && hasProvider && draft.connectionType === 'manual' && (
              <div className="space-y-4">
                {draft.isOutboundEnabled && (
                  <div>
                    <h3 className="text-sm font-semibold text-slate-900">Outbound SMTP</h3>
                    <div className="mt-2 grid gap-4 sm:grid-cols-2">
                      <div>
                        <label className="text-sm font-semibold text-slate-700">SMTP Host</label>
                        <input
                          type="text"
                          value={draft.smtpHost}
                          onChange={(event) => {
                            const smtpHost = event.currentTarget.value
                            updateDraft((current) => ({ ...current, smtpHost }))
                          }}
                          className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
                        />
                      </div>
                      <div>
                        <label className="text-sm font-semibold text-slate-700">SMTP Port</label>
                        <input
                          type="number"
                          value={draft.smtpPort}
                          onChange={(event) => {
                            const smtpPort = event.currentTarget.value
                            updateDraft((current) => ({ ...current, smtpPort }))
                          }}
                          className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
                        />
                      </div>
                      <div>
                        <label className="text-sm font-semibold text-slate-700">SMTP Security</label>
                        <select
                          value={draft.smtpSecurity}
                          onChange={(event) => {
                            const smtpSecurity = event.currentTarget.value
                            updateDraft((current) => ({ ...current, smtpSecurity }))
                          }}
                          className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
                        >
                          <option value="starttls">STARTTLS</option>
                          <option value="ssl">SSL</option>
                          <option value="none">None</option>
                        </select>
                      </div>
                      <div>
                        <label className="text-sm font-semibold text-slate-700">SMTP Auth Mode</label>
                        <select
                          value={draft.smtpAuth}
                          onChange={(event) => {
                            const smtpAuth = event.currentTarget.value
                            updateDraft((current) => ({ ...current, smtpAuth }))
                          }}
                          className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
                        >
                          <option value="login">LOGIN</option>
                          <option value="plain">PLAIN</option>
                          <option value="oauth2">OAuth 2.0</option>
                          <option value="none">None</option>
                        </select>
                      </div>
                      <div>
                        <label className="text-sm font-semibold text-slate-700">SMTP Username</label>
                        <input
                          type="text"
                          value={draft.smtpUsername}
                          onChange={(event) => {
                            const smtpUsername = event.currentTarget.value
                            updateDraft((current) => ({ ...current, smtpUsername }))
                          }}
                          className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
                        />
                      </div>
                      <div>
                        <label className="text-sm font-semibold text-slate-700">SMTP Password</label>
                        <input
                          type="password"
                          value={draft.smtpPassword}
                          onChange={(event) => {
                            const smtpPassword = event.currentTarget.value
                            updateDraft((current) => ({ ...current, smtpPassword }))
                          }}
                          autoComplete="new-password"
                          autoCorrect="off"
                          autoCapitalize="none"
                          spellCheck={false}
                          className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
                          placeholder={
                            hasSavedSmtpPassword && !draft.smtpPassword
                              ? 'Saved password on file. Enter new value to replace.'
                              : 'App password or account password'
                          }
                        />
                        {hasSavedSmtpPassword && (
                          <p className="mt-1 text-xs text-slate-600">Password is already stored. Leave blank to keep it.</p>
                        )}
                      </div>
                    </div>
                  </div>
                )}

                {draft.isInboundEnabled && (
                  <div>
                    <h3 className="text-sm font-semibold text-slate-900">Inbound IMAP</h3>
                    <div className="mt-2 grid gap-4 sm:grid-cols-2">
                      <div>
                        <label className="text-sm font-semibold text-slate-700">IMAP Host</label>
                        <input
                          type="text"
                          value={draft.imapHost}
                          onChange={(event) => {
                            const imapHost = event.currentTarget.value
                            updateDraft((current) => ({ ...current, imapHost }))
                          }}
                          className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
                        />
                      </div>
                      <div>
                        <label className="text-sm font-semibold text-slate-700">IMAP Port</label>
                        <input
                          type="number"
                          value={draft.imapPort}
                          onChange={(event) => {
                            const imapPort = event.currentTarget.value
                            updateDraft((current) => ({ ...current, imapPort }))
                          }}
                          className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
                        />
                      </div>
                      <div>
                        <label className="text-sm font-semibold text-slate-700">IMAP Security</label>
                        <select
                          value={draft.imapSecurity}
                          onChange={(event) => {
                            const imapSecurity = event.currentTarget.value
                            updateDraft((current) => ({ ...current, imapSecurity }))
                          }}
                          className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
                        >
                          <option value="ssl">SSL</option>
                          <option value="starttls">STARTTLS</option>
                          <option value="none">None</option>
                        </select>
                      </div>
                      <div>
                        <label className="text-sm font-semibold text-slate-700">IMAP Auth Mode</label>
                        <select
                          value={draft.imapAuth}
                          onChange={(event) => {
                            const imapAuth = event.currentTarget.value
                            updateDraft((current) => ({ ...current, imapAuth }))
                          }}
                          className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
                        >
                          <option value="login">LOGIN</option>
                          <option value="oauth2">OAuth 2.0</option>
                          <option value="none">None</option>
                        </select>
                      </div>
                      <div>
                        <label className="text-sm font-semibold text-slate-700">IMAP Username</label>
                        <input
                          type="text"
                          value={draft.imapUsername}
                          onChange={(event) => {
                            const imapUsername = event.currentTarget.value
                            updateDraft((current) => ({ ...current, imapUsername }))
                          }}
                          className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
                        />
                      </div>
                      <div>
                        <label className="text-sm font-semibold text-slate-700">IMAP Password</label>
                        <input
                          type="password"
                          value={draft.imapPassword}
                          onChange={(event) => {
                            const imapPassword = event.currentTarget.value
                            updateDraft((current) => ({ ...current, imapPassword }))
                          }}
                          autoComplete="new-password"
                          autoCorrect="off"
                          autoCapitalize="none"
                          spellCheck={false}
                          className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
                          placeholder={
                            hasSavedImapPassword && !draft.imapPassword
                              ? 'Saved password on file. Enter new value to replace.'
                              : 'App password or account password'
                          }
                        />
                        {hasSavedImapPassword && (
                          <p className="mt-1 text-xs text-slate-600">Password is already stored. Leave blank to keep it.</p>
                        )}
                      </div>
                      <div>
                        <label className="text-sm font-semibold text-slate-700">IMAP Folder</label>
                        <input
                          type="text"
                          value={draft.imapFolder}
                          onChange={(event) => {
                            const imapFolder = event.currentTarget.value
                            updateDraft((current) => ({ ...current, imapFolder }))
                          }}
                          className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
                        />
                      </div>
                      <div>
                        <label className="text-sm font-semibold text-slate-700">Poll Interval (sec)</label>
                        <input
                          type="number"
                          min={30}
                          value={draft.pollIntervalSec}
                          onChange={(event) => {
                            const pollIntervalSec = event.currentTarget.value
                            updateDraft((current) => ({ ...current, pollIntervalSec }))
                          }}
                          className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
                        />
                      </div>
                      <label className="sm:col-span-2 rounded-lg border border-blue-200 px-3 py-2 text-sm text-slate-700">
                        <input
                          type="checkbox"
                          checked={draft.imapIdleEnabled}
                          onChange={(event) => {
                            const imapIdleEnabled = event.currentTarget.checked
                            updateDraft((current) => ({ ...current, imapIdleEnabled }))
                          }}
                          className="mr-2 rounded"
                        />
                        Enable IMAP IDLE (low-latency triggers)
                      </label>
                    </div>
                  </div>
                )}
              </div>
            )}

            {oauthRequired && !oauthConnected && (
              <div className="rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-800">
                Connect Gmail OAuth before saving.
              </div>
            )}

            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                onClick={handleSave}
                disabled={testMutation.isPending || saveMutation.isPending || !canSubmit}
                className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-white disabled:opacity-60"
              >
                {testMutation.isPending || saveMutation.isPending ? 'Saving...' : 'Save Settings'}
              </button>
            </div>
            {((testResults.smtp && !testResults.smtp.ok) || (testResults.imap && !testResults.imap.ok)) && (
              <div className="grid gap-3 sm:grid-cols-2">
                {testResults.smtp && !testResults.smtp.ok && (
                  <div className="rounded-lg bg-amber-50 p-3 text-sm text-amber-800">
                    <div className="font-semibold">SMTP failed</div>
                    <div className="mt-1">{testResults.smtp.error}</div>
                  </div>
                )}
                {testResults.imap && !testResults.imap.ok && (
                  <div className="rounded-lg bg-amber-50 p-3 text-sm text-amber-800">
                    <div className="font-semibold">IMAP failed</div>
                    <div className="mt-1">{testResults.imap.error}</div>
                  </div>
                )}
              </div>
            )}
          </div>
      </div>

      {showGuidance && (
        <div className="fixed inset-0 z-50">
          <div className="absolute inset-0 bg-slate-900/60" />
          <div className="relative flex min-h-full items-center justify-center px-4 py-8">
            <div className="w-full max-w-2xl rounded-xl bg-white p-5 shadow-lg">
              <h2 className="text-xl font-semibold text-slate-900">Before you continue to Google</h2>
              <p className="mt-2 text-sm text-slate-700">
                If Google shows an unverified-app warning, click <strong>Advanced</strong>, then <strong>Go to Gobii (unsafe)</strong>.
              </p>
              <img
                src="/static/images/email/google-oauth-advanced-warning.png"
                alt="Google warning screen with Advanced highlighted"
                className="mt-3 w-full rounded-lg border border-slate-200"
              />
              <label className="mt-3 inline-flex items-start gap-2 text-sm text-slate-800">
                <input type="checkbox" checked={guidanceAck} onChange={(event) => setGuidanceAck(event.currentTarget.checked)} className="mt-0.5 rounded" />
                <span>I understand how to proceed.</span>
              </label>
              {guidanceError && <p className="mt-2 text-xs text-amber-700">{guidanceError}</p>}
              <div className="mt-4 flex justify-end gap-2">
                <button type="button" onClick={() => setShowGuidance(false)} className="rounded-lg border border-blue-300 px-3 py-2 text-sm font-semibold text-blue-800">
                  Cancel
                </button>
                <button type="button" onClick={() => void handleContinueFromGuidance()} className="rounded-lg bg-blue-600 px-3 py-2 text-sm font-semibold text-white">
                  Continue to Google
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
