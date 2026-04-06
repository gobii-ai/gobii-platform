import { jsonFetch, jsonRequest } from './http'

export type SecretType = 'credential' | 'env_var'

export interface PersistentAgentSecret {
  id: string
  agent?: string | null
  user?: number | null
  organization?: string | null
  name: string
  secret_type: SecretType
  domain_pattern: string
  requested: boolean
  is_global: boolean
  value?: string // Write only
}

export type CreateSecretPayload = {
  name: string
  secret_type: SecretType
  domain_pattern?: string
  value: string
  is_global?: boolean
  agent?: string | null
}

export type UpdateSecretPayload = {
  name?: string
  secret_type?: SecretType
  domain_pattern?: string
  value?: string
  is_global?: boolean
}

export async function fetchAgentSecrets(agentId: string): Promise<PersistentAgentSecret[]> {
  return jsonFetch(`/api/v1/secrets/?agent_id=${agentId}`)
}

export async function fetchGlobalSecrets(): Promise<PersistentAgentSecret[]> {
  return jsonFetch('/api/v1/secrets/')
}

export async function createSecret(payload: CreateSecretPayload): Promise<PersistentAgentSecret> {
  return jsonRequest('/api/v1/secrets/', {
    method: 'POST',
    json: payload,
    includeCsrf: true,
  })
}

export async function updateSecret(id: string, payload: UpdateSecretPayload): Promise<PersistentAgentSecret> {
  return jsonRequest(`/api/v1/secrets/${id}/`, {
    method: 'PATCH',
    json: payload,
    includeCsrf: true,
  })
}

export async function deleteSecret(id: string): Promise<void> {
  return jsonRequest(`/api/v1/secrets/${id}/`, {
    method: 'DELETE',
    includeCsrf: true,
  })
}
