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

type NativeIntegrationGrantedFileDTO = {
  id: string
  provider_key: string
  external_file_id: string
  name: string
  mime_type: string
  url: string
  last_selected_at: string
  selected_by_id: string | null
}

type NativeIntegrationFilesResponseDTO = {
  provider_key: string
  files: NativeIntegrationGrantedFileDTO[]
}

type NativeIntegrationFilesSaveResponseDTO = NativeIntegrationFilesResponseDTO & {
  upserted_count: number
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

export type NativeIntegrationFileSelection = {
  externalFileId: string
  name: string
  mimeType: string
  url: string
}

export type NativeIntegrationGrantedFile = NativeIntegrationFileSelection & {
  id: string
  providerKey: string
  lastSelectedAt: string
  selectedById: string | null
}

export type NativeIntegrationFilesSaveResponse = {
  providerKey: string
  upsertedCount: number
  files: NativeIntegrationGrantedFile[]
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
  filesUrl: provider.files_url,
  pickerTokenUrl: provider.picker_token_url,
  revokeUrl: provider.revoke_url,
})

const mapGrantedFile = (file: NativeIntegrationGrantedFileDTO): NativeIntegrationGrantedFile => ({
  id: file.id,
  providerKey: file.provider_key,
  externalFileId: file.external_file_id,
  name: file.name,
  mimeType: file.mime_type,
  url: file.url ?? '',
  lastSelectedAt: file.last_selected_at,
  selectedById: file.selected_by_id ?? null,
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

export async function fetchNativeIntegrationFiles(filesUrl: string): Promise<NativeIntegrationGrantedFile[]> {
  const payload = await jsonFetch<NativeIntegrationFilesResponseDTO>(filesUrl)
  return (payload.files ?? []).map(mapGrantedFile)
}

export async function saveNativeIntegrationFiles(
  filesUrl: string,
  files: NativeIntegrationFileSelection[],
): Promise<NativeIntegrationFilesSaveResponse> {
  const payload = await jsonRequest<NativeIntegrationFilesSaveResponseDTO>(filesUrl, {
    method: 'POST',
    includeCsrf: true,
    json: {
      files: files.map((file) => ({
        external_file_id: file.externalFileId,
        name: file.name,
        mime_type: file.mimeType,
        url: file.url,
      })),
    },
  })
  return {
    providerKey: payload.provider_key,
    upsertedCount: payload.upserted_count,
    files: (payload.files ?? []).map(mapGrantedFile),
  }
}

export async function deleteNativeIntegrationFile(fileUrl: string): Promise<{ deleted: boolean }> {
  return jsonRequest<{ deleted: boolean }>(fileUrl, {
    method: 'DELETE',
    includeCsrf: true,
  })
}

export async function revokeNativeIntegration(revokeUrl: string): Promise<{ revoked: boolean }> {
  return jsonRequest<{ revoked: boolean }>(revokeUrl, {
    method: 'POST',
    includeCsrf: true,
    json: {},
  })
}
