import { jsonFetch, jsonRequest } from './http'

export type Secret = {
  id: string
  name: string
  key: string
  description: string
  secret_type: 'credential' | 'env_var'
  domain_pattern: string | null
  visibility: 'agent' | 'global'
  requested: boolean
  created_at: string | null
  updated_at: string | null
  agent_id: string | null
  agent_name: string | null
}

export type GlobalSecretsResponse = {
  secrets: Secret[]
}

export type AgentSecretsResponse = {
  agent_secrets: Secret[]
  global_secrets: Secret[]
  agent: { id: string; name: string }
}

export type SecretPayload = {
  secret_type: string
  domain_pattern?: string
  name: string
  description?: string
  value: string
  visibility?: 'agent' | 'global'
}

export type SecretUpdatePayload = {
  name?: string
  description?: string
  secret_type?: string
  domain_pattern?: string
  value?: string
}

// Global secrets
export function fetchGlobalSecrets(apiUrl: string, signal?: AbortSignal): Promise<GlobalSecretsResponse> {
  return jsonFetch<GlobalSecretsResponse>(apiUrl, { signal })
}

export function createGlobalSecret(apiUrl: string, payload: SecretPayload) {
  return jsonRequest<{ secret: Secret }>(apiUrl, {
    method: 'POST',
    includeCsrf: true,
    json: payload,
  })
}

export function updateGlobalSecret(apiUrl: string, secretId: string, payload: SecretUpdatePayload) {
  return jsonRequest<{ secret: Secret }>(`${apiUrl}${secretId}/`, {
    method: 'PUT',
    includeCsrf: true,
    json: payload,
  })
}

export function deleteGlobalSecret(apiUrl: string, secretId: string) {
  return jsonRequest<{ ok: boolean }>(`${apiUrl}${secretId}/`, {
    method: 'DELETE',
    includeCsrf: true,
  })
}

// Agent secrets
export function fetchAgentSecrets(apiUrl: string, signal?: AbortSignal): Promise<AgentSecretsResponse> {
  return jsonFetch<AgentSecretsResponse>(apiUrl, { signal })
}

export function createAgentSecret(apiUrl: string, payload: SecretPayload) {
  return jsonRequest<{ secret: Secret }>(apiUrl, {
    method: 'POST',
    includeCsrf: true,
    json: payload,
  })
}

export function updateAgentSecret(apiUrl: string, secretId: string, payload: SecretUpdatePayload) {
  return jsonRequest<{ secret: Secret }>(`${apiUrl}${secretId}/`, {
    method: 'PUT',
    includeCsrf: true,
    json: payload,
  })
}

export function deleteAgentSecret(apiUrl: string, secretId: string) {
  return jsonRequest<{ ok: boolean }>(`${apiUrl}${secretId}/`, {
    method: 'DELETE',
    includeCsrf: true,
  })
}

export function promoteAgentSecret(apiUrl: string, secretId: string) {
  return jsonRequest<{ secret: Secret }>(`${apiUrl}${secretId}/promote/`, {
    method: 'POST',
    includeCsrf: true,
  })
}
