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
  revoke_url: string
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
  revokeUrl: string
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
  revokeUrl: provider.revoke_url,
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
): Promise<NativeIntegrationConnectResponse> {
  const payload = await jsonRequest<NativeIntegrationConnectResponseDTO>(connectUrl, {
    method: 'POST',
    includeCsrf: true,
    csrfToken,
    json: {},
  })
  return {
    providerKey: payload.provider_key,
    authorizationUrl: payload.authorization_url,
    state: payload.state,
    expiresAt: payload.expires_at,
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

export async function revokeNativeIntegration(revokeUrl: string, csrfToken?: string): Promise<{ revoked: boolean }> {
  return jsonRequest<{ revoked: boolean }>(revokeUrl, {
    method: 'POST',
    includeCsrf: true,
    csrfToken,
    json: {},
  })
}
