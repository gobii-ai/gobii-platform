import { jsonFetch, jsonRequest } from './http'

// ---- DTOs (match backend JSON) ----

export type SecretDTO = {
  id: string
  name: string
  key: string
  secret_type: 'credential' | 'env_var'
  domain_pattern: string
  description: string
  source: 'agent' | 'global'
}

export type RequestedSecretDTO = {
  id: string
  name: string
  key: string
  secret_type: 'credential' | 'env_var'
  domain_pattern: string
  description: string
  created_at: string | null
}

export type GlobalSecretListResponse = {
  secrets: SecretDTO[]
  owner_scope: string
}

export type AgentSecretListResponse = {
  agent_secrets: SecretDTO[]
  global_secrets: SecretDTO[]
  requested_secrets: RequestedSecretDTO[]
}

export type SecretMutationResponse = {
  secret: SecretDTO
  message: string
}

export type CreateSecretPayload = {
  name: string
  secret_type: 'credential' | 'env_var'
  domain_pattern?: string
  value: string
  description?: string
  is_global?: boolean
}

export type UpdateSecretPayload = {
  name?: string
  secret_type?: 'credential' | 'env_var'
  domain_pattern?: string
  value?: string
  description?: string
}

// ---- Global Secrets ----

export function fetchGlobalSecrets(listUrl: string, signal?: AbortSignal): Promise<GlobalSecretListResponse> {
  return jsonFetch<GlobalSecretListResponse>(listUrl, { signal })
}

export function createGlobalSecret(listUrl: string, data: CreateSecretPayload): Promise<SecretMutationResponse> {
  return jsonRequest<SecretMutationResponse>(listUrl, {
    method: 'POST',
    json: data,
    includeCsrf: true,
  })
}

export function updateGlobalSecret(detailUrl: string, data: UpdateSecretPayload): Promise<SecretMutationResponse> {
  return jsonRequest<SecretMutationResponse>(detailUrl, {
    method: 'PATCH',
    json: data,
    includeCsrf: true,
  })
}

export function deleteGlobalSecret(detailUrl: string): Promise<{ ok: boolean; message: string }> {
  return jsonRequest(detailUrl, {
    method: 'DELETE',
    includeCsrf: true,
  })
}

// ---- Agent Secrets ----

export function fetchAgentSecrets(listUrl: string, signal?: AbortSignal): Promise<AgentSecretListResponse> {
  return jsonFetch<AgentSecretListResponse>(listUrl, { signal })
}

export function createAgentSecret(listUrl: string, data: CreateSecretPayload): Promise<SecretMutationResponse> {
  return jsonRequest<SecretMutationResponse>(listUrl, {
    method: 'POST',
    json: data,
    includeCsrf: true,
  })
}

export function updateAgentSecret(detailUrl: string, data: UpdateSecretPayload): Promise<SecretMutationResponse> {
  return jsonRequest<SecretMutationResponse>(detailUrl, {
    method: 'PATCH',
    json: data,
    includeCsrf: true,
  })
}

export function deleteAgentSecret(detailUrl: string): Promise<{ ok: boolean; message: string }> {
  return jsonRequest(detailUrl, {
    method: 'DELETE',
    includeCsrf: true,
  })
}

export function promoteAgentSecret(promoteUrl: string): Promise<SecretMutationResponse> {
  return jsonRequest<SecretMutationResponse>(promoteUrl, {
    method: 'POST',
    includeCsrf: true,
  })
}
