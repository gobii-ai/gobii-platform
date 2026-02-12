import { jsonFetch, jsonRequest } from './http'

export type AgentEmailSettingsPayload = {
  agent: {
    id: string
    name: string
    backUrl: string
    helpUrl: string
  }
  providerDefaults: {
    gmail?: {
      smtp_host: string
      smtp_port: number
      smtp_security: string
      imap_host: string
      imap_port: number
      imap_security: string
    }
  }
  endpoint: {
    address: string
    exists: boolean
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
  }
  oauth: {
    connected: boolean
    provider: string
    scope: string
    expiresAt: string | null
    callbackPath: string
    startUrl: string
    statusUrl: string | null
    revokeUrl: string | null
  }
}

export type EmailSettingsSaveRequest = {
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
  settings: AgentEmailSettingsPayload
}

export async function fetchAgentEmailSettings(url: string): Promise<AgentEmailSettingsPayload> {
  return jsonFetch<AgentEmailSettingsPayload>(url)
}

export async function ensureAgentEmailAccount(
  url: string,
  payload: { endpointAddress: string },
): Promise<{ ok: boolean; settings: AgentEmailSettingsPayload }> {
  return jsonRequest<{ ok: boolean; settings: AgentEmailSettingsPayload }>(url, {
    method: 'POST',
    includeCsrf: true,
    json: payload,
  })
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

export async function startEmailOAuth(
  startUrl: string,
  payload: Record<string, unknown>,
): Promise<{
  session_id: string
  state: string
  expires_at: string
  has_existing_credentials: boolean
  client_id: string
}> {
  return jsonRequest(startUrl, {
    method: 'POST',
    includeCsrf: true,
    json: payload,
  })
}

export async function fetchEmailOAuthStatus(statusUrl: string): Promise<{
  connected: boolean
  expires_at?: string | null
  scope?: string
  provider?: string
}> {
  return jsonFetch(statusUrl)
}

export async function revokeEmailOAuth(revokeUrl: string): Promise<{ revoked: boolean; detail?: string }> {
  return jsonRequest(revokeUrl, {
    method: 'POST',
    includeCsrf: true,
  })
}
