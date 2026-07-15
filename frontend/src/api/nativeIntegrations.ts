import { jsonFetch, jsonRequest } from './http'

export type NativeIntegrationProviderDTO = {
  provider_key: string
  display_name: string
  description: string
  auth_type: string
  icon: string
  api_hosts: string[]
  scopes: string[]
  connected: boolean
  scope: string
  expires_at: string | null
  connect_url: string
  files_url: string
  picker_token_url: string
  agent_event_url: string
  revoke_url: string
  connection_scope?: 'workspace' | 'agent'
  connected_agent_count?: number
  agent_connections_url?: string
  credential_fields?: NativeIntegrationCredentialFieldDTO[]
  present_credential_fields?: string[]
  missing_credential_fields?: string[]
}

export type NativeIntegrationDocLinkDTO = {
  title: string
  url: string
  description?: string
}

export type NativeIntegrationCredentialFieldDTO = {
  key: string
  name: string
  description?: string
  required?: boolean
  default?: string | null
  how_to_get?: string
  docs?: NativeIntegrationDocLinkDTO[]
}

type NativeIntegrationListResponseDTO = {
  owner_scope: string
  owner_label: string
  providers: NativeIntegrationProviderDTO[]
}

type NativeIntegrationConnectResponseDTO = {
  provider_key: string
  authorization_url: string
  state: string
  expires_at: string
}

type NativeIntegrationManualConnectResponseDTO = {
  provider_key: string
  connected: boolean
  secret_id: string
  present_credential_fields: string[]
  missing_credential_fields: string[]
}

type NativeIntegrationPickerTokenResponseDTO = {
  access_token: string
  developer_key: string
  app_id: string
  scope: string
  expires_at: string | null
}

type NativeIntegrationAccessibleFileDTO = {
  external_id: string
  name: string
  mime_type: string
  web_url: string
}

type NativeIntegrationFilesResponseDTO = {
  provider_key: string
  files: NativeIntegrationAccessibleFileDTO[]
}

export type NativeIntegrationProvider = {
  providerKey: string
  displayName: string
  description: string
  authType: string
  icon: string
  apiHosts: string[]
  scopes: string[]
  connected: boolean
  scope: string
  expiresAt: string | null
  connectUrl: string
  filesUrl: string
  pickerTokenUrl: string
  agentEventUrl: string
  revokeUrl: string
  connectionScope?: 'workspace' | 'agent'
  connectedAgentCount?: number
  agentConnectionsUrl?: string
  credentialFields: NativeIntegrationCredentialField[]
  presentCredentialFields: string[]
  missingCredentialFields: string[]
}

export type NativeIntegrationDocLink = {
  title: string
  url: string
  description: string
}

export type NativeIntegrationCredentialField = {
  key: string
  name: string
  description: string
  required: boolean
  default: string | null
  howToGet: string
  docs: NativeIntegrationDocLink[]
}

export type NativeIntegrationListResponse = {
  ownerScope: string
  ownerLabel: string
  providers: NativeIntegrationProvider[]
}

export type NativeIntegrationConnectResponse = {
  providerKey: string
  authorizationUrl: string
  state: string
  expiresAt: string
}

export type NativeIntegrationManualConnectResponse = {
  providerKey: string
  connected: boolean
  secretId: string
  presentCredentialFields: string[]
  missingCredentialFields: string[]
}

export type NativeIntegrationPickerTokenResponse = {
  accessToken: string
  developerKey: string
  appId: string
  scope: string
  expiresAt: string | null
}

export type NativeIntegrationAccessibleFile = {
  externalId: string
  name: string
  mimeType: string
  webUrl: string
}

export type NativeIntegrationFilesResponse = {
  providerKey: string
  files: NativeIntegrationAccessibleFile[]
}

export const mapNativeIntegrationProvider = (provider: NativeIntegrationProviderDTO): NativeIntegrationProvider => ({
  providerKey: provider.provider_key,
  displayName: provider.display_name,
  description: provider.description ?? '',
  authType: provider.auth_type,
  icon: provider.icon,
  apiHosts: Array.isArray(provider.api_hosts) ? provider.api_hosts.map((host) => String(host)) : [],
  scopes: Array.isArray(provider.scopes) ? provider.scopes.map((scope) => String(scope)) : [],
  connected: Boolean(provider.connected),
  scope: provider.scope ?? '',
  expiresAt: provider.expires_at ?? null,
  connectUrl: provider.connect_url,
  filesUrl: provider.files_url,
  pickerTokenUrl: provider.picker_token_url,
  agentEventUrl: provider.agent_event_url,
  revokeUrl: provider.revoke_url,
  connectionScope: provider.connection_scope === 'agent' ? 'agent' : 'workspace',
  connectedAgentCount: Number(provider.connected_agent_count ?? 0),
  agentConnectionsUrl: provider.agent_connections_url ?? '',
  credentialFields: (provider.credential_fields ?? []).map((field) => ({
    key: field.key,
    name: field.name,
    description: field.description ?? '',
    required: field.required !== false,
    default: field.default ?? null,
    howToGet: field.how_to_get ?? '',
    docs: (field.docs ?? []).map((doc) => ({
      title: doc.title,
      url: doc.url,
      description: doc.description ?? '',
    })),
  })),
  presentCredentialFields: (provider.present_credential_fields ?? []).map(String),
  missingCredentialFields: (provider.missing_credential_fields ?? []).map(String),
})

const mapNativeIntegrationFile = (file: NativeIntegrationAccessibleFileDTO): NativeIntegrationAccessibleFile => ({
  externalId: file.external_id,
  name: file.name,
  mimeType: file.mime_type,
  webUrl: file.web_url,
})

export async function fetchNativeIntegrations(listUrl: string): Promise<NativeIntegrationListResponse> {
  const payload = await jsonFetch<NativeIntegrationListResponseDTO>(listUrl)
  return {
    ownerScope: payload.owner_scope,
    ownerLabel: payload.owner_label,
    providers: (payload.providers ?? []).map(mapNativeIntegrationProvider),
  }
}

export async function startNativeIntegrationConnect(
  connectUrl: string,
  csrfToken?: string,
  agentId?: string,
): Promise<NativeIntegrationConnectResponse> {
  const payload = await jsonRequest<NativeIntegrationConnectResponseDTO>(connectUrl, {
    method: 'POST',
    includeCsrf: true,
    csrfToken,
    json: agentId ? { agent_id: agentId } : {},
  })
  return {
    providerKey: payload.provider_key,
    authorizationUrl: payload.authorization_url,
    state: payload.state,
    expiresAt: payload.expires_at,
  }
}

export async function saveNativeIntegrationCredentials(
  connectUrl: string,
  credentials: Record<string, string | null>,
  csrfToken?: string,
): Promise<NativeIntegrationManualConnectResponse> {
  const payload = await jsonRequest<NativeIntegrationManualConnectResponseDTO>(connectUrl, {
    method: 'POST',
    includeCsrf: true,
    csrfToken,
    json: { credentials },
  })
  return {
    providerKey: payload.provider_key,
    connected: Boolean(payload.connected),
    secretId: payload.secret_id,
    presentCredentialFields: (payload.present_credential_fields ?? []).map(String),
    missingCredentialFields: (payload.missing_credential_fields ?? []).map(String),
  }
}

export async function fetchNativeIntegrationPickerToken(
  pickerTokenUrl: string,
): Promise<NativeIntegrationPickerTokenResponse> {
  const payload = await jsonFetch<NativeIntegrationPickerTokenResponseDTO>(pickerTokenUrl)
  return {
    accessToken: payload.access_token,
    developerKey: payload.developer_key,
    appId: payload.app_id,
    scope: payload.scope,
    expiresAt: payload.expires_at,
  }
}

export async function fetchNativeIntegrationFiles(filesUrl: string): Promise<NativeIntegrationFilesResponse> {
  const payload = await jsonFetch<NativeIntegrationFilesResponseDTO>(filesUrl)
  return {
    providerKey: payload.provider_key,
    files: (payload.files ?? []).map(mapNativeIntegrationFile),
  }
}

export async function revokeNativeIntegration(revokeUrl: string, csrfToken?: string, agentId?: string): Promise<{ revoked: boolean }> {
  return jsonRequest<{ revoked: boolean }>(revokeUrl, {
    method: 'POST',
    includeCsrf: true,
    csrfToken,
    json: agentId ? { agent_id: agentId } : {},
  })
}

export type NativeIntegrationAgentConnection = {
  agentId: string
  agentName: string
  provider: string
  mailboxAddress: string
  connected: boolean
  activeMode: 'none' | 'custom' | 'oauth'
  sendEnabled: boolean
  receiveEnabled: boolean
  smtpLastOkAt: string | null
  smtpError: string
  imapLastOkAt: string | null
  imapError: string
  gobiiAddress: string
  settingsUrl: string
}

export async function fetchNativeIntegrationAgentConnections(url: string): Promise<NativeIntegrationAgentConnection[]> {
  const payload = await jsonFetch<{ agents?: Array<Record<string, unknown>> }>(url)
  return (payload.agents ?? []).map((agent) => ({
    agentId: String(agent.agent_id ?? ''),
    agentName: String(agent.agent_name ?? ''),
    provider: String(agent.provider ?? ''),
    mailboxAddress: String(agent.mailbox_address ?? ''),
    connected: Boolean(agent.connected),
    activeMode: (agent.active_mode === 'custom' || agent.active_mode === 'oauth') ? agent.active_mode : 'none',
    sendEnabled: Boolean(agent.send_enabled),
    receiveEnabled: Boolean(agent.receive_enabled),
    smtpLastOkAt: typeof agent.smtp_last_ok_at === 'string' ? agent.smtp_last_ok_at : null,
    smtpError: String(agent.smtp_error ?? ''),
    imapLastOkAt: typeof agent.imap_last_ok_at === 'string' ? agent.imap_last_ok_at : null,
    imapError: String(agent.imap_error ?? ''),
    gobiiAddress: String(agent.gobii_address ?? ''),
    settingsUrl: String(agent.settings_url ?? ''),
  }))
}

export async function recordNativeIntegrationAgentEvent({
  agentEventUrl,
  agentId,
  eventType,
  files = [],
  csrfToken,
}: {
  agentEventUrl: string
  agentId: string
  eventType: 'connected' | 'files_selected'
  files?: NativeIntegrationAccessibleFile[]
  csrfToken?: string
}): Promise<{ recorded: boolean; stepId: string }> {
  const payload = await jsonRequest<{ recorded: boolean; step_id: string }>(agentEventUrl, {
    method: 'POST',
    includeCsrf: true,
    csrfToken,
    json: {
      agent_id: agentId,
      event_type: eventType,
      files: files.map((file) => ({
        external_id: file.externalId,
        name: file.name,
        mime_type: file.mimeType,
        web_url: file.webUrl,
      })),
    },
  })
  return {
    recorded: Boolean(payload.recorded),
    stepId: payload.step_id,
  }
}
