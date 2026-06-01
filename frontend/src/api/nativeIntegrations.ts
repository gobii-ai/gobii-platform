import { jsonFetch, jsonRequest } from './http'

type NativeIntegrationProviderDTO = {
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

export type NativeIntegrationFileSelection = {
  externalFileId: string
  name: string
  mimeType: string
  url: string
}

const mapProvider = (provider: NativeIntegrationProviderDTO): NativeIntegrationProvider => ({
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
  pickerTokenUrl: provider.picker_token_url,
  revokeUrl: provider.revoke_url,
})

export async function fetchNativeIntegrations(listUrl: string): Promise<NativeIntegrationListResponse> {
  const payload = await jsonFetch<NativeIntegrationListResponseDTO>(listUrl)
  return {
    ownerScope: payload.owner_scope,
    ownerLabel: payload.owner_label,
    providers: (payload.providers ?? []).map(mapProvider),
  }
}

export async function startNativeIntegrationConnect(connectUrl: string): Promise<NativeIntegrationConnectResponse> {
  const payload = await jsonRequest<NativeIntegrationConnectResponseDTO>(connectUrl, {
    method: 'POST',
    includeCsrf: true,
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

export async function revokeNativeIntegration(revokeUrl: string): Promise<{ revoked: boolean }> {
  return jsonRequest<{ revoked: boolean }>(revokeUrl, {
    method: 'POST',
    includeCsrf: true,
    json: {},
  })
}
