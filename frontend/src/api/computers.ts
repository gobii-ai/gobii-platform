import { jsonFetch, jsonRequest } from './http'

export function getComputerConnectionsUrl(): string | null {
  return document.getElementById('gobii-frontend-root')?.dataset.computerConnectionsUrl || null
}

export type ComputerAgent = {
  id: string
  name: string
  organization_id: string | null
  organization_name: string | null
}

export type ComputerContext = {
  type: 'personal' | 'organization'
  id: string
  can_manage_org_grants: boolean
}

export type ComputerApp = {
  app_key: string
  display_name: string
  type: 'bundled' | 'custom'
  schema_sha256: string
  approved_schema_sha256: string
  approval_state: 'approved' | 'pending_approval' | 'disabled'
  available: boolean
}

export type ComputerDevice = {
  id: string
  display_name: string
  platform: 'macos' | 'windows'
  architecture: string
  client_version: string
  protocol_version: number
  paused: boolean
  online: boolean
  update_required: boolean
  last_seen_at: string | null
  owner: { id: string; display_name: string }
  assignment: {
    agent_id: string
    agent_name: string
    organization_id: string | null
    organization_name: string | null
  } | null
  apps: ComputerApp[]
  permissions: {
    can_manage_device: boolean
  }
}

export type ComputersResponse = {
  enabled: boolean
  downloads?: {
    macos: { url: string }
    windows: { url: string; portable_url: string }
    minimum_version: string
  }
  context?: ComputerContext
  agents?: ComputerAgent[]
  devices?: ComputerDevice[]
}

export type ComputerPairingResponse = {
  enabled: boolean
  pairing: {
    id: string
    display_name: string
    platform: string
    architecture: string
    client_version: string
    protocol_version: number
    apps: Array<{
      key: string
      display_name: string
      schema_sha256: string
      type: 'bundled' | 'custom'
    }>
    expires_at: string
  }
  agents: ComputerAgent[]
}

export function fetchComputers(url: string, agentId?: string | null): Promise<ComputersResponse> {
  const requestUrl = new URL(url, window.location.origin)
  if (agentId) requestUrl.searchParams.set('agent_id', agentId)
  return jsonFetch<ComputersResponse>(requestUrl)
}

export function fetchComputerPairing(url: string, pairingId: string): Promise<ComputerPairingResponse> {
  return jsonFetch<ComputerPairingResponse>(`${url}pairings/${pairingId}/`)
}

export function approveComputerPairing(
  url: string,
  pairingId: string,
  payload: { user_code: string; agent_id: string; selected_app_keys: string[] },
): Promise<{ approved: boolean }> {
  return jsonRequest(`${url}pairings/${pairingId}/`, {
    method: 'POST',
    includeCsrf: true,
    json: payload,
  })
}

export function updateComputer(
  url: string,
  deviceId: string,
  payload: Record<string, unknown>,
): Promise<{ device: ComputerDevice }> {
  return jsonRequest(`${url}${deviceId}/`, {
    method: 'PATCH',
    includeCsrf: true,
    json: payload,
  })
}

export function revokeComputerAssignment(url: string, deviceId: string): Promise<{ revoked: boolean }> {
  return jsonRequest(`${url}${deviceId}/assignment/`, {
    method: 'DELETE',
    includeCsrf: true,
  })
}

export function revokeComputer(url: string, deviceId: string): Promise<{ revoked: boolean }> {
  return jsonRequest(`${url}${deviceId}/`, {
    method: 'DELETE',
    includeCsrf: true,
  })
}
