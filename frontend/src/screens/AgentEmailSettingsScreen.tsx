import { useCallback, useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, CheckCircle2, Mail, ShieldCheck } from 'lucide-react'

import {
  ensureAgentEmailAccount,
  fetchAgentEmailSettings,
  fetchEmailOAuthStatus,
  resetAgentEmailSettingsToDefault,
  revokeEmailOAuth,
  saveAgentEmailSettings,
  startEmailOAuth,
  testAgentEmailSettings,
  type AgentEmailSettingsPayload,
  type EmailSettingsSaveRequest,
} from '../api/agentEmailSettings'
import { HttpError } from '../api/http'
import {
  EMAIL_OAUTH_PROVIDER_CONFIG,
  EMAIL_PROVIDER_OPTIONS,
  getProviderLabel,
  isOAuthProviderKey,
  matchProviderFromDefaults,
  type OAuthProviderKey,
  type ProviderKey,
} from './agentEmailSettingsProviders'
import { EmbeddedAgentShellBackButton } from '../components/agentChat/EmbeddedAgentShellBackButton'
import { SettingsBanner } from '../components/agentSettings/SettingsBanner'
import { InlineStatusBanner } from '../components/common/InlineStatusBanner'
import { getSettingsSurfaceClassName } from '../components/common/SettingsSurface'

type AgentEmailSettingsScreenProps = {
  agentId: string
  emailSettingsUrl: string
  ensureAccountUrl: string
  testUrl: string
  onBack?: () => void
  onSaved?: (payload: { endpointAddress: string | null }) => void
}

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

type DraftUpdater = (updater: (current: DraftState) => DraftState) => void
type DraftTextField = {
  field: keyof DraftState
  label: string
  type?: 'text' | 'number' | 'password'
  min?: number
  placeholder?: string
  helpText?: string | false
  password?: boolean
}
type DraftSelectField = {
  field: keyof DraftState
  label: string
  options: Array<{ value: string; label: string }>
}

const embeddedInputClassName = 'mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm'
const securityOptions = [
  { value: 'starttls', label: 'STARTTLS' },
  { value: 'ssl', label: 'SSL' },
  { value: 'none', label: 'None' },
]
const smtpAuthOptions = [
  { value: 'login', label: 'LOGIN' },
  { value: 'plain', label: 'PLAIN' },
  { value: 'oauth2', label: 'OAuth 2.0' },
  { value: 'none', label: 'None' },
]
const imapAuthOptions = [
  { value: 'login', label: 'LOGIN' },
  { value: 'oauth2', label: 'OAuth 2.0' },
  { value: 'none', label: 'None' },
]

function MailTextField({
  draft,
  updateDraft,
  field,
  label,
  type = 'text',
  min,
  placeholder,
  helpText,
  password = false,
}: DraftTextField & {
  draft: DraftState
  updateDraft: DraftUpdater
}) {
  return (
    <div>
      <label className="text-sm font-semibold text-slate-700">{label}</label>
      <input
        type={type}
        min={min}
        value={String(draft[field])}
        onChange={(event) => {
          const value = event.currentTarget.value
          updateDraft((current) => ({ ...current, [field]: value }))
        }}
        autoComplete={password ? 'new-password' : undefined}
        autoCorrect={password ? 'off' : undefined}
        autoCapitalize={password ? 'none' : undefined}
        spellCheck={password ? false : undefined}
        className={embeddedInputClassName}
        placeholder={placeholder}
      />
      {helpText ? <p className="mt-1 text-xs text-slate-600">{helpText}</p> : null}
    </div>
  )
}

function MailSelectField({
  draft,
  updateDraft,
  field,
  label,
  options,
}: DraftSelectField & {
  draft: DraftState
  updateDraft: DraftUpdater
}) {
  return (
    <div>
      <label className="text-sm font-semibold text-slate-700">{label}</label>
      <select
        value={String(draft[field])}
        onChange={(event) => {
          const value = event.currentTarget.value
          updateDraft((current) => ({ ...current, [field]: value }))
        }}
        className={embeddedInputClassName}
      >
        {options.map((option) => (
          <option key={option.value} value={option.value}>{option.label}</option>
        ))}
      </select>
    </div>
  )
}

function MailManualSection({
  title,
  textFields,
  selectFields,
  draft,
  updateDraft,
}: {
  title: string
  textFields: DraftTextField[]
  selectFields: DraftSelectField[]
  draft: DraftState
  updateDraft: DraftUpdater
}) {
  return (
    <div>
      <h3 className="text-sm font-semibold text-slate-900">{title}</h3>
      <div className="mt-2 grid gap-4 sm:grid-cols-2">
        {textFields.slice(0, 2).map((field) => <MailTextField key={String(field.field)} {...field} draft={draft} updateDraft={updateDraft} />)}
        {selectFields.map((field) => <MailSelectField key={String(field.field)} {...field} draft={draft} updateDraft={updateDraft} />)}
        {textFields.slice(2).map((field) => <MailTextField key={String(field.field)} {...field} draft={draft} updateDraft={updateDraft} />)}
      </div>
    </div>
  )
}

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

function inferOAuthDirectionSelection(settings: AgentEmailSettingsPayload): {
  isOutboundEnabled: boolean
  isInboundEnabled: boolean
} {
  if (settings.account.isOutboundEnabled || settings.account.isInboundEnabled) {
    return {
      isOutboundEnabled: settings.account.isOutboundEnabled,
      isInboundEnabled: settings.account.isInboundEnabled,
    }
  }

  const oauthProvider = settings.oauth.provider.toLowerCase()
  const scope = settings.oauth.scope.toLowerCase()
  if (!settings.oauth.connected && settings.account.connectionMode !== 'oauth2') {
    return {
      isOutboundEnabled: settings.account.isOutboundEnabled,
      isInboundEnabled: settings.account.isInboundEnabled,
    }
  }

  if (oauthProvider === 'gmail') {
    return {
      isOutboundEnabled: true,
      isInboundEnabled: true,
    }
  }

  return {
    isOutboundEnabled: scope.includes('smtp.send'),
    isInboundEnabled: scope.includes('imap.accessasuser'),
  }
}

function draftFromSettings(settings: AgentEmailSettingsPayload): DraftState {
  const directionSelection = inferOAuthDirectionSelection(settings)
  const hasMailDirectionSelected = directionSelection.isInboundEnabled || directionSelection.isOutboundEnabled
  const hasOAuthConfigured =
    settings.oauth.connected
    || settings.account.smtpAuth === 'oauth2'
    || settings.account.imapAuth === 'oauth2'
  const oauthProvider = settings.oauth.provider.toLowerCase()
  const inferredProvider: ProviderKey = isOAuthProviderKey(oauthProvider)
    ? oauthProvider
    : (matchProviderFromDefaults(settings.providerDefaults, settings.account.smtpHost) ?? 'custom')
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
  const providerDefaults = isOAuthProviderKey(provider) ? settings.providerDefaults[provider] : undefined
  const oauthIdentity = connectionType === 'oauth' ? settings.endpoint.address || '' : ''

  return {
    endpointAddress: settings.endpoint.address || '',
    provider,
    connectionType,
    isOutboundEnabled: directionSelection.isOutboundEnabled,
    isInboundEnabled: directionSelection.isInboundEnabled,
    smtpHost: connectionType === 'oauth' && providerDefaults ? providerDefaults.smtp_host : settings.account.smtpHost || '',
    smtpPort: connectionType === 'oauth' && providerDefaults ? String(providerDefaults.smtp_port) : settings.account.smtpPort ? String(settings.account.smtpPort) : '',
    smtpSecurity: connectionType === 'oauth' && providerDefaults ? providerDefaults.smtp_security : settings.account.smtpSecurity || 'starttls',
    smtpAuth: connectionType === 'oauth' ? 'oauth2' : settings.account.smtpAuth || 'login',
    smtpUsername: oauthIdentity || settings.account.smtpUsername || settings.endpoint.address || '',
    smtpPassword: '',
    imapHost: connectionType === 'oauth' && providerDefaults ? providerDefaults.imap_host : settings.account.imapHost || '',
    imapPort: connectionType === 'oauth' && providerDefaults ? String(providerDefaults.imap_port) : settings.account.imapPort ? String(settings.account.imapPort) : '',
    imapSecurity: connectionType === 'oauth' && providerDefaults ? providerDefaults.imap_security : settings.account.imapSecurity || 'ssl',
    imapAuth: connectionType === 'oauth' ? 'oauth2' : settings.account.imapAuth || 'login',
    imapUsername: oauthIdentity || settings.account.imapUsername || settings.endpoint.address || '',
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

function applyProviderDefaults(
  draft: DraftState,
  settings: AgentEmailSettingsPayload,
  provider: ProviderKey,
): DraftState {
  if (!isOAuthProviderKey(provider)) {
    return draft
  }
  const defaults = settings.providerDefaults[provider]
  if (!defaults) {
    return draft
  }
  const oauthMode = draft.connectionType === 'oauth'
  return {
    ...draft,
    smtpHost: defaults.smtp_host,
    smtpPort: String(defaults.smtp_port),
    smtpSecurity: defaults.smtp_security,
    smtpAuth: oauthMode ? 'oauth2' : 'login',
    smtpUsername: draft.smtpUsername || draft.endpointAddress,
    imapHost: defaults.imap_host,
    imapPort: String(defaults.imap_port),
    imapSecurity: defaults.imap_security,
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

function buildSavePayload(draft: DraftState, previousEndpointAddress: string): EmailSettingsSaveRequest {
  const oauthMode = isOAuthProviderKey(draft.provider) && draft.connectionType === 'oauth'
  const oauthIdentity = draft.endpointAddress.trim()
  return {
    endpointAddress: oauthIdentity,
    previousEndpointAddress: previousEndpointAddress.trim(),
    connectionMode: oauthMode ? 'oauth2' : 'custom',
    oauthProvider: oauthMode ? draft.provider : '',
    smtpHost: draft.smtpHost.trim(),
    smtpPort: toPortValue(draft.smtpPort),
    smtpSecurity: draft.smtpSecurity,
    smtpAuth: oauthMode ? 'oauth2' : draft.smtpAuth,
    smtpUsername: oauthMode ? oauthIdentity : draft.smtpUsername.trim(),
    smtpPassword: draft.smtpPassword.trim(),
    imapHost: draft.imapHost.trim(),
    imapPort: toPortValue(draft.imapPort),
    imapSecurity: draft.imapSecurity,
    imapAuth: oauthMode ? 'oauth2' : draft.imapAuth,
    imapUsername: oauthMode ? oauthIdentity : draft.imapUsername.trim(),
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
  onBack,
  onSaved,
}: AgentEmailSettingsScreenProps) {
  const helpButtonClassName = 'inline-flex w-full items-center justify-center rounded-lg border border-blue-300/40 bg-blue-950/20 px-3 py-2 text-sm font-semibold text-blue-100 sm:w-auto'
  const selectionCardClassName = 'rounded-lg border border-blue-300/30 bg-blue-950/20 p-3 text-sm text-slate-100'
  const infoCalloutClassName = 'rounded-lg border border-blue-300/30 bg-blue-950/20 px-4 py-3 text-sm text-blue-100'
  const warningCalloutClassName = 'rounded-lg border border-amber-300/30 bg-amber-950/20 px-3 py-2 text-sm text-amber-100'
  const neutralCardClassName = 'rounded-lg border border-slate-300/70 bg-slate-900/40 p-3 text-sm text-slate-200'
  const oauthPrimaryButtonClassName = 'rounded-lg border border-blue-300/40 bg-blue-950/20 px-3 py-2 text-sm font-semibold text-blue-100'
  const oauthSecondaryButtonClassName = 'rounded-lg border border-slate-300/70 bg-transparent px-3 py-2 text-sm font-semibold text-slate-100'
  const primaryActionButtonClassName = 'rounded-lg bg-slate-100 px-4 py-2 text-sm font-semibold text-slate-900 disabled:opacity-60'
  const secondaryActionButtonClassName = 'rounded-lg border border-rose-300/40 bg-rose-950/20 px-4 py-2 text-sm font-semibold text-rose-100 disabled:opacity-60'
  const settingsCardClassName = getSettingsSurfaceClassName({ variant: 'embedded', shadowClassName: 'shadow-none' })
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
  const [isResetPending, setIsResetPending] = useState(false)

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
  const resetMutation = useMutation({
    mutationFn: (url: string) => resetAgentEmailSettingsToDefault(url),
    onSuccess: (response) => {
      queryClient.setQueryData(queryKey, response.settings)
      setErrorBanner(null)
      setIsResetPending(false)
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
  const defaultEmailDomainLabel = settings?.defaultEmailDomain ? `@${settings.defaultEmailDomain}` : 'default Gobii'
  const oauthProvider = draft && isOAuthProviderKey(draft.provider) ? draft.provider : null
  const oauthProviderConfig = oauthProvider ? EMAIL_OAUTH_PROVIDER_CONFIG[oauthProvider] : null
  const oauthProviderLabel = oauthProviderConfig?.label ?? getProviderLabel(settings?.oauth.provider || '')

  useEffect(() => {
    if (!settings) {
      return
    }
    setIsResetPending(false)
    const nextDraft = draftFromSettings(settings)
    setDraft((current) => {
      if (!current) {
        return nextDraft
      }
      const serverHasDirectionSelection = nextDraft.isInboundEnabled || nextDraft.isOutboundEnabled
      const keepLocalDirectionSelection =
        !serverHasDirectionSelection && (current.isInboundEnabled || current.isOutboundEnabled)
      return {
        ...nextDraft,
        isInboundEnabled: keepLocalDirectionSelection ? current.isInboundEnabled : nextDraft.isInboundEnabled,
        isOutboundEnabled: keepLocalDirectionSelection ? current.isOutboundEnabled : nextDraft.isOutboundEnabled,
        provider: keepLocalDirectionSelection && !nextDraft.provider ? current.provider : nextDraft.provider,
        connectionType:
          keepLocalDirectionSelection && !nextDraft.connectionType
            ? current.connectionType
            : nextDraft.connectionType,
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
      setBanner('OAuth connected. You can now save settings.')
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
  const oauthRequired = Boolean(draft && isOAuthProviderKey(draft.provider) && draft.connectionType === 'oauth')
  const hasSavedSmtpPassword = Boolean(settings?.account.hasSmtpPassword)
  const hasSavedImapPassword = Boolean(settings?.account.hasImapPassword)
  const setupValid = hasAddress && hasMailDirection && hasProvider && hasConnectionType
  const canSubmit = isResetPending || (setupValid && (!oauthRequired || oauthConnected))

  const updateDraft = useCallback((updater: (current: DraftState) => DraftState) => {
    if (isResetPending) {
      setBanner(null)
    }
    setIsResetPending(false)
    setDraft((current) => (current ? updater(current) : current))
  }, [isResetPending])

  const launchOAuth = useCallback(async (
    resolvedSettings: AgentEmailSettingsPayload,
    provider: OAuthProviderKey,
  ) => {
    if (!resolvedSettings.account.id) {
      throw new Error('Email account was not created yet.')
    }
    const providerConfig = EMAIL_OAUTH_PROVIDER_CONFIG[provider]
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
      provider,
      scope: providerConfig.scope,
      token_endpoint: providerConfig.tokenEndpoint,
      use_gobii_app: true,
      redirect_uri: callbackUrl,
      state,
      code_verifier: codeVerifier,
      code_challenge: codeChallenge,
      code_challenge_method: 'S256',
      metadata: {
        provider,
        authorization_endpoint: providerConfig.authorizationEndpoint,
        token_endpoint: providerConfig.tokenEndpoint,
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
      scope: providerConfig.scope,
      state: stateKey,
      code_challenge: codeChallenge,
      code_challenge_method: 'S256',
    })
    for (const [key, value] of Object.entries(providerConfig.authorizationParams)) {
      params.set(key, value)
    }
    const loginHint = draft?.endpointAddress.trim()
    if (loginHint) {
      params.set('login_hint', loginHint)
    }
    popup.location.href = `${providerConfig.authorizationEndpoint}?${params.toString()}`
    popup.focus()
  }, [draft?.endpointAddress])

  const handleConnectOAuth = useCallback(async () => {
    if (!draft || !oauthProvider) {
      return
    }
    setErrorBanner(null)
    setBanner(null)
    try {
      const ensured = await ensureAccountMutation.mutateAsync({ endpointAddress: draft.endpointAddress.trim() })
      const nextSettings = ensured.settings
      setPendingOAuthSettings(nextSettings)
      if (EMAIL_OAUTH_PROVIDER_CONFIG[oauthProvider].guidanceTitle) {
        setGuidanceAck(false)
        setGuidanceError(null)
        setShowGuidance(true)
        return
      }
      await launchOAuth(nextSettings, oauthProvider)
    } catch (error) {
      setErrorBanner(describeHttpError(error))
    }
  }, [draft, ensureAccountMutation, launchOAuth, oauthProvider])

  const handleContinueFromGuidance = useCallback(async () => {
    if (!guidanceAck) {
      setGuidanceError('Check the box before continuing.')
      return
    }
    if (!pendingOAuthSettings) {
      setGuidanceError('Unable to start OAuth right now. Please try again.')
      return
    }
    if (!oauthProvider) {
      setGuidanceError('Choose an OAuth provider before continuing.')
      return
    }
    setShowGuidance(false)
    setGuidanceError(null)
    try {
      await launchOAuth(pendingOAuthSettings, oauthProvider)
    } catch (error) {
      setErrorBanner(error instanceof Error ? error.message : 'Unable to start OAuth.')
    }
  }, [guidanceAck, launchOAuth, oauthProvider, pendingOAuthSettings])

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
    if (!draft || !settings) {
      return
    }
    setBanner(null)
    setErrorBanner(null)
    try {
      if (isResetPending) {
        const response = await resetMutation.mutateAsync(emailSettingsUrl)
        setPendingOAuthSettings(null)
        setShowGuidance(false)
        setGuidanceAck(false)
        setGuidanceError(null)
        setTestResults({})
        const restoredAddress = response.settings.endpoint.address || response.settings.defaultEndpoint.address
        setBanner(
          restoredAddress
            ? `Reverted to default email settings (${restoredAddress}).`
            : 'Reverted to default email settings.',
        )
        onSaved?.({ endpointAddress: restoredAddress || null })
        return
      }

      const payload = buildSavePayload(draft, settings.endpoint.address)
      const testResponse = await testMutation.mutateAsync({
        ...payload,
        testOutbound: draft.isOutboundEnabled,
        testInbound: draft.isInboundEnabled,
      })
      if (!testResponse.ok) {
        return
      }
      const saveResponse = await saveMutation.mutateAsync(payload)
      const savedAddress = saveResponse.settings.endpoint.address || saveResponse.settings.defaultEndpoint.address || null
      onSaved?.({ endpointAddress: savedAddress })
      setBanner(savedAddress ? `Email settings saved for ${savedAddress}.` : 'Email settings saved.')
    } catch (error) {
      setErrorBanner(describeHttpError(error))
    }
  }, [draft, emailSettingsUrl, isResetPending, onSaved, resetMutation, saveMutation, settings, testMutation])

  const handleResetToDefault = useCallback(() => {
    if (!settings) {
      return
    }
    const confirmed = window.confirm(
      'Prepare revert to default Gobii email settings? This will uncheck inbound/outbound now. Click Save Settings to apply the revert.',
    )
    if (!confirmed) {
      return
    }
    setBanner(null)
    setErrorBanner(null)
    const defaultEndpointAddress = settings.defaultEndpoint.address
    if (!settings.defaultEndpoint.exists || !defaultEndpointAddress) {
      setErrorBanner('Default Gobii email is not configured for this workspace.')
      return
    }
    setPendingOAuthSettings(null)
    setShowGuidance(false)
    setGuidanceAck(false)
    setGuidanceError(null)
    setTestResults({})
    setDraft((current) => {
      if (!current) {
        return current
      }
      return {
        ...current,
        endpointAddress: defaultEndpointAddress,
        isOutboundEnabled: false,
        isInboundEnabled: false,
        provider: '',
        connectionType: '',
        smtpPassword: '',
        imapPassword: '',
      }
    })
    setIsResetPending(true)
    setBanner(`Revert prepared. Click Save Settings to apply and switch to ${defaultEndpointAddress}.`)
  }, [settings])

  if (settingsQuery.error && !settings) {
    return (
      <div className={`${settingsCardClassName} p-6 text-sm text-amber-100`}>
        Failed to load email settings. {describeHttpError(settingsQuery.error)}
      </div>
    )
  }

  if (settingsQuery.isLoading || !settings || !draft) {
    return (
      <div className={`${settingsCardClassName} p-6 text-sm text-slate-200`}>
        Loading email settings...
      </div>
    )
  }

  const smtpPasswordPlaceholder = hasSavedSmtpPassword && !draft.smtpPassword
    ? 'Saved password on file. Enter new value to replace.'
    : 'App password or account password'
  const imapPasswordPlaceholder = hasSavedImapPassword && !draft.imapPassword
    ? 'Saved password on file. Enter new value to replace.'
    : 'App password or account password'
  const smtpTextFields: DraftTextField[] = [
    { field: 'smtpHost', label: 'SMTP Host' },
    { field: 'smtpPort', label: 'SMTP Port', type: 'number' },
    { field: 'smtpUsername', label: 'SMTP Username' },
    {
      field: 'smtpPassword',
      label: 'SMTP Password',
      type: 'password',
      password: true,
      placeholder: smtpPasswordPlaceholder,
      helpText: hasSavedSmtpPassword && 'Password is already stored. Leave blank to keep it.',
    },
  ]
  const imapTextFields: DraftTextField[] = [
    { field: 'imapHost', label: 'IMAP Host' },
    { field: 'imapPort', label: 'IMAP Port', type: 'number' },
    { field: 'imapUsername', label: 'IMAP Username' },
    {
      field: 'imapPassword',
      label: 'IMAP Password',
      type: 'password',
      password: true,
      placeholder: imapPasswordPlaceholder,
      helpText: hasSavedImapPassword && 'Password is already stored. Leave blank to keep it.',
    },
    { field: 'imapFolder', label: 'IMAP Folder' },
    { field: 'pollIntervalSec', label: 'Poll Interval (sec)', type: 'number', min: 30 },
  ]

  return (
    <div className="space-y-5">
      <SettingsBanner
        variant="embedded"
        leading={<EmbeddedAgentShellBackButton onClick={onBack} ariaLabel="Back to settings" />}
        eyebrow="Email settings"
        title={settings.agent.name}
        actions={(
          <a href={settings.agent.helpUrl} target="_blank" rel="noreferrer" className={helpButtonClassName}>
            Help
          </a>
        )}
      />

      {banner && <InlineStatusBanner variant="success" surface="embedded">{banner}</InlineStatusBanner>}
      {errorBanner && (
        <InlineStatusBanner variant="warning" surface="embedded" icon={AlertTriangle}>
          <span>{errorBanner}</span>
        </InlineStatusBanner>
      )}

      <div className={`${settingsCardClassName} p-5`}>
        <div className="space-y-4">
            <div className="rounded-lg border border-blue-300/30 bg-blue-950/20 px-4 py-3">
              <p className="text-sm font-semibold text-slate-100">Regular Gobii Address</p>
              <p className="mt-1 text-sm text-slate-100">
                {settings.defaultEndpoint.exists ? settings.defaultEndpoint.address : 'Not configured'}
              </p>
              <p className="mt-1 text-xs text-slate-400">
                This `{defaultEmailDomainLabel}` address stays active for inbound messages.
              </p>
            </div>
            <div>
              <label className="text-sm font-semibold text-slate-700">Custom Transport Address</label>
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
                This address is used for custom SMTP/IMAP send and receive behavior.
              </p>
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
              <label className={selectionCardClassName}>
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
              <label className={selectionCardClassName}>
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
              <div className={infoCalloutClassName}>
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
                      return isOAuthProviderKey(provider) ? applyProviderDefaults(next, settings, provider) : next
                    })
                  }}
                  className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
                >
                  <option value="" disabled>
                    Select provider
                  </option>
                  {EMAIL_PROVIDER_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </div>
            )}

            {hasMailDirection && !hasProvider && (
              <div className={infoCalloutClassName}>
                Select a provider to continue.
              </div>
            )}

            {hasMailDirection && draft.provider !== 'custom' && hasProvider && (
              <div className="space-y-2">
                <p className="text-sm font-semibold text-slate-700">Connection Type</p>
                <div className="grid gap-3 sm:grid-cols-2">
                  <button
                    type="button"
                    onClick={() =>
                      updateDraft((current) => {
                        if (!isOAuthProviderKey(current.provider)) {
                          return current
                        }
                        return applyProviderDefaults({ ...current, connectionType: 'oauth' }, settings, current.provider)
                      })
                    }
                    className={`rounded-lg border p-3 text-left text-sm ${
                      draft.connectionType === 'oauth'
                        ? 'border-blue-200 bg-blue-900/30 text-blue-50'
                        : 'border-slate-300/70 bg-transparent text-slate-200'
                    }`}
                  >
                    <div className="font-semibold">OAuth (recommended)</div>
                    <div className="mt-1 text-slate-300">Connect using {oauthProviderLabel} OAuth and skip manual secrets.</div>
                  </button>
                  <button
                    type="button"
                    onClick={() =>
                      updateDraft((current) => {
                        if (!isOAuthProviderKey(current.provider)) {
                          return current
                        }
                        return applyProviderDefaults({ ...current, connectionType: 'manual' }, settings, current.provider)
                      })
                    }
                    className={`rounded-lg border p-3 text-left text-sm ${
                      draft.connectionType === 'manual'
                        ? 'border-blue-200 bg-blue-900/30 text-blue-50'
                        : 'border-slate-300/70 bg-transparent text-slate-200'
                    }`}
                  >
                    <div className="font-semibold">Manual SMTP/IMAP</div>
                    <div className="mt-1 text-slate-300">Use an app password.</div>
                  </button>
                </div>
              </div>
            )}

            {hasMailDirection && draft.provider !== 'custom' && hasProvider && !hasConnectionType && (
              <div className={infoCalloutClassName}>
                Choose a connection type to continue.
              </div>
            )}

            {hasMailDirection && oauthProvider && draft.connectionType === 'oauth' && oauthProviderConfig && (
              <div className="rounded-lg border border-blue-300/30 bg-blue-950/20 p-4 space-y-3">
                <div className="space-y-2 text-sm">
                  <div className="inline-flex items-center gap-2 text-slate-200">
                    {oauthConnected ? <CheckCircle2 className="h-4 w-4 text-emerald-300" /> : <Mail className="h-4 w-4 text-blue-300" />}
                    <span>{oauthConnected ? `${oauthProviderConfig.label} OAuth connected` : `${oauthProviderConfig.label} OAuth connection required before saving`}</span>
                  </div>
                  {oauthProvider === 'microsoft' && (
                    <p className="text-slate-700">
                      Gobii will open Microsoft sign-in in a popup. Tenant consent, authenticated SMTP, or mailbox IMAP settings can still block delivery even after OAuth succeeds.
                    </p>
                  )}
                  <div className="flex flex-wrap gap-2">
                    <button
                      type="button"
                      onClick={handleConnectOAuth}
                      className={oauthPrimaryButtonClassName}
                      disabled={ensureAccountMutation.isPending}
                    >
                      {ensureAccountMutation.isPending ? 'Preparing...' : `Connect ${oauthProviderConfig.label} OAuth`}
                    </button>
                    <button
                      type="button"
                      onClick={handleDisconnectOAuth}
                      className={oauthSecondaryButtonClassName}
                      disabled={!oauthConnected}
                    >
                      Disconnect OAuth
                    </button>
                  </div>
                </div>
              </div>
            )}

            {hasMailDirection && draft.provider === 'gmail' && draft.connectionType === 'manual' && (
              <div className={neutralCardClassName}>
                <div className="inline-flex items-center gap-2 font-semibold text-slate-100">
                  <ShieldCheck className="h-4 w-4 text-blue-300" />
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
                      className="font-semibold text-blue-300 underline"
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
                  <MailManualSection
                    title="Outbound SMTP"
                    textFields={smtpTextFields}
                    selectFields={[
                      { field: 'smtpSecurity', label: 'SMTP Security', options: securityOptions },
                      { field: 'smtpAuth', label: 'SMTP Auth Mode', options: smtpAuthOptions },
                    ]}
                    draft={draft}
                    updateDraft={updateDraft}
                  />
                )}

                {draft.isInboundEnabled && (
                  <div>
                    <MailManualSection
                      title="Inbound IMAP"
                      textFields={imapTextFields}
                      selectFields={[
                        { field: 'imapSecurity', label: 'IMAP Security', options: securityOptions },
                        { field: 'imapAuth', label: 'IMAP Auth Mode', options: imapAuthOptions },
                      ]}
                      draft={draft}
                      updateDraft={updateDraft}
                    />
                    <div className="mt-4 grid gap-4 sm:grid-cols-2">
                      <label className="sm:col-span-2 rounded-lg border border-blue-300/30 bg-blue-950/20 px-3 py-2 text-sm text-slate-100">
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
              <div className={warningCalloutClassName}>
                Connect {oauthProviderLabel} OAuth before saving.
              </div>
            )}

            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                onClick={handleSave}
                disabled={testMutation.isPending || saveMutation.isPending || resetMutation.isPending || !canSubmit}
                className={primaryActionButtonClassName}
              >
                {testMutation.isPending || saveMutation.isPending || resetMutation.isPending
                  ? 'Saving...'
                  : isResetPending
                    ? 'Apply Revert'
                    : 'Save Settings'}
              </button>
              <button
                type="button"
                onClick={() => void handleResetToDefault()}
                disabled={testMutation.isPending || saveMutation.isPending || resetMutation.isPending}
                className={secondaryActionButtonClassName}
              >
                {resetMutation.isPending ? 'Reverting...' : 'Revert to Default Email'}
              </button>
            </div>
            {((testResults.smtp && !testResults.smtp.ok) || (testResults.imap && !testResults.imap.ok)) && (
              <div className="grid gap-3 sm:grid-cols-2">
                {testResults.smtp && !testResults.smtp.ok && (
                  <div className="rounded-lg border border-amber-300/30 bg-amber-950/20 p-3 text-sm text-amber-100">
                    <div className="font-semibold">SMTP failed</div>
                    <div className="mt-1">{testResults.smtp.error}</div>
                  </div>
                )}
                {testResults.imap && !testResults.imap.ok && (
                  <div className="rounded-lg border border-amber-300/30 bg-amber-950/20 p-3 text-sm text-amber-100">
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
              <h2 className="text-xl font-semibold text-slate-900">{oauthProviderConfig?.guidanceTitle ?? 'Before you continue'}</h2>
              <p className="mt-2 text-sm text-slate-700">
                {oauthProviderConfig?.guidanceBody ?? 'Review the provider guidance before continuing.'}
              </p>
              {oauthProviderConfig?.guidanceImage && (
                <img
                  src={oauthProviderConfig.guidanceImage}
                  alt={`${oauthProviderConfig.label} OAuth guidance`}
                  className="mt-3 w-full rounded-lg border border-slate-200"
                />
              )}
              <label className="mt-3 inline-flex items-start gap-2 text-sm text-slate-800">
                <input type="checkbox" checked={guidanceAck} onChange={(event) => setGuidanceAck(event.currentTarget.checked)} className="mt-0.5 rounded" />
                <span>{oauthProviderConfig?.guidanceConfirmLabel ?? 'I understand how to proceed.'}</span>
              </label>
              {guidanceError && <p className="mt-2 text-xs text-amber-700">{guidanceError}</p>}
              <div className="mt-4 flex justify-end gap-2">
                <button type="button" onClick={() => setShowGuidance(false)} className={oauthSecondaryButtonClassName}>
                  Cancel
                </button>
                <button type="button" onClick={() => void handleContinueFromGuidance()} className={oauthPrimaryButtonClassName}>
                  {oauthProviderConfig?.guidanceContinueLabel ?? 'Continue'}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
