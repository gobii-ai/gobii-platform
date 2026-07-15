import { jsonFetch, jsonRequest } from './http'

export type EmailProviderDefaults = {
  smtp_host: string
  smtp_port: number
  smtp_security: string
  imap_host: string
  imap_port: number
  imap_security: string
}

export type AgentEmailSettingsPayload = {
  agent: {
    id: string
    name: string
    backUrl: string
    helpUrl: string
  }
  providerDefaults: Record<string, EmailProviderDefaults | undefined>
  defaultEmailDomain: string
  endpoint: {
    address: string
    exists: boolean
    displayName: string
    readOnly: boolean
  }
  defaultEndpoint: {
    address: string
    exists: boolean
    isInboundAliasActive: boolean
    displayName: string
  }
  account: {
    id: string | null
    exists: boolean
    smtpHost: string
    smtpPort: number | null
    smtpSecurity: string
    smtpAuth: string
    smtpUsername: string
    hasSmtpPassword: boolean
    imapHost: string
    imapPort: number | null
    imapSecurity: string
    imapAuth: string
    imapUsername: string
    hasImapPassword: boolean
    imapFolder: string
    isOutboundEnabled: boolean
    isInboundEnabled: boolean
    imapIdleEnabled: boolean
    pollIntervalSec: number
    connectionMode: 'custom' | 'oauth2'
    connectionLastOkAt: string | null
    connectionError: string
    smtpLastOkAt: string | null
    smtpError: string
    imapLastOkAt: string | null
    imapError: string
  }
  oauth: {
    connected: boolean
    provider: string
    legacy: boolean
    mailboxAddress: string
    scope: string
    expiresAt: string | null
    callbackPath: string
    startUrl: string | null
    statusUrl: string | null
    revokeUrl: string | null
    gmailConnectUrl: string
    gmailRevokeUrl: string
    outlookConnectUrl: string
    outlookRevokeUrl: string
  }
  activeMode: 'none' | 'custom' | 'oauth'
  customConfigured: boolean
  customEnabled: boolean
}

export type EmailSettingsSaveRequest = {
  expectedActiveMode: AgentEmailSettingsPayload['activeMode']
  endpointAddress: string
  connectionMode: 'custom' | 'oauth2'
  oauthProvider?: string
  smtpHost: string
  smtpPort: number | null
  smtpSecurity: string
  smtpAuth: string
  smtpUsername: string
  smtpPassword?: string
  imapHost: string
  imapPort: number | null
  imapSecurity: string
  imapAuth: string
  imapUsername: string
  imapPassword?: string
  imapFolder: string
  isOutboundEnabled: boolean
  isInboundEnabled: boolean
  imapIdleEnabled: boolean
  pollIntervalSec: number
  displayName?: string
  defaultDisplayName?: string
}

export type EmailSettingsTestRequest = EmailSettingsSaveRequest & {
  testOutbound: boolean
  testInbound: boolean
}

export type EmailSettingsTestResponse = {
  ok: boolean
  results: {
    smtp: { ok: boolean; error: string } | null
    imap: { ok: boolean; error: string } | null
  }
}

export async function fetchAgentEmailSettings(url: string): Promise<AgentEmailSettingsPayload> {
  return jsonFetch<AgentEmailSettingsPayload>(url)
}

export async function saveAgentEmailSettings(
  url: string,
  payload: EmailSettingsSaveRequest,
): Promise<{ ok: boolean; settings: AgentEmailSettingsPayload }> {
  return jsonRequest<{ ok: boolean; settings: AgentEmailSettingsPayload }>(url, {
    method: 'POST',
    includeCsrf: true,
    json: payload,
  })
}

export async function updateAgentEmailSettingsAction(
  url: string,
  action: string,
  values: Record<string, unknown> = {},
): Promise<{ ok: boolean; settings: AgentEmailSettingsPayload }> {
  return jsonRequest(url, {
    method: 'POST',
    includeCsrf: true,
    json: { action, ...values },
  })
}

export async function testAgentEmailSettings(
  url: string,
  payload: EmailSettingsTestRequest,
): Promise<EmailSettingsTestResponse> {
  return jsonRequest<EmailSettingsTestResponse>(url, {
    method: 'POST',
    includeCsrf: true,
    json: payload,
  })
}
