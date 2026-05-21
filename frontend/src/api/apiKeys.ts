import { jsonFetch, jsonRequest } from './http'

export type ApiKeyDTO = {
  id: string
  name: string
  prefix: string
  created_by: string | null
  created_at: string | null
  last_used_at: string | null
  revoked_at: string | null
  is_active: boolean
}

export type ApiKeyListResponse = {
  api_keys: ApiKeyDTO[]
  owner_scope: 'user' | 'organization'
  owner_name: string
  can_manage: boolean
  email_verified: boolean
}

export type ApiKeyCreateResponse = {
  api_key: ApiKeyDTO
  raw_key: string
  message: string
}

export type ApiKeyMutationResponse = {
  api_key: ApiKeyDTO
  message: string
}

export function fetchApiKeys(signal?: AbortSignal): Promise<ApiKeyListResponse> {
  return jsonFetch<ApiKeyListResponse>('/console/api/api-keys/', { signal })
}

export function createApiKey(name: string): Promise<ApiKeyCreateResponse> {
  return jsonRequest<ApiKeyCreateResponse>('/console/api/api-keys/', {
    method: 'POST',
    json: { name },
    includeCsrf: true,
  })
}

export function revokeApiKey(id: string): Promise<ApiKeyMutationResponse> {
  return jsonRequest<ApiKeyMutationResponse>(`/console/api/api-keys/${id}/`, {
    method: 'PATCH',
    includeCsrf: true,
  })
}

export function deleteApiKey(id: string): Promise<{ ok: boolean; message: string }> {
  return jsonRequest(`/console/api/api-keys/${id}/`, {
    method: 'DELETE',
    includeCsrf: true,
  })
}
