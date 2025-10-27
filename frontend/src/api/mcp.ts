import { jsonFetch, jsonRequest } from './http'

type McpServerDTO = {
  id: string
  name: string
  display_name: string
  description: string
  command: string
  command_args: string[]
  url: string
  auth_method: string
  is_active: boolean
  scope: string
  scope_label: string
  updated_at: string
  created_at: string
  oauth_status_url?: string
  oauth_revoke_url?: string
  oauth_pending?: boolean
  oauth_connected?: boolean
}

type McpServerDetailDTO = McpServerDTO & {
  metadata: Record<string, unknown>
  headers: Record<string, string>
  environment: Record<string, string>
  prefetch_apps: string[]
  oauth_status_url?: string
  oauth_revoke_url?: string
}

type McpServerListResponseDTO = {
  owner_scope: string
  owner_label: string
  result_count: number
  servers: McpServerDTO[]
}

type McpServerMutationResponseDTO = {
  server: McpServerDetailDTO
  message?: string
}

type McpServerAssignmentAgentDTO = {
  id: string
  name: string
  description: string
  is_active: boolean
  assigned: boolean
  organization_id?: string | null
  last_interaction_at?: string | null
}

type McpServerAssignmentsResponseDTO = {
  server: {
    id: string
    display_name: string
    scope: string
    scope_label: string
  }
  agents: McpServerAssignmentAgentDTO[]
  total_agents: number
  assigned_count: number
  message?: string
}

export type McpServer = {
  id: string
  name: string
  displayName: string
  description: string
  command: string
  commandArgs: string[]
  url: string
  authMethod: string
  isActive: boolean
  scope: string
  scopeLabel: string
  updatedAt: string
  createdAt: string
  oauthStatusUrl?: string
  oauthRevokeUrl?: string
  oauthPending: boolean
  oauthConnected: boolean
}

export type McpServerListResponse = {
  ownerScope: string
  ownerLabel: string
  resultCount: number
  servers: McpServer[]
}

export type McpServerDetail = McpServer & {
  metadata: Record<string, unknown>
  headers: Record<string, string>
  environment: Record<string, string>
  prefetchApps: string[]
  oauthStatusUrl?: string
  oauthRevokeUrl?: string
}

export type McpServerPayload = {
  display_name: string
  name?: string
  url: string
  auth_method: string
  is_active: boolean
  headers: Record<string, string>
  metadata?: Record<string, unknown>
  environment?: Record<string, unknown>
  command?: string
  command_args?: string[]
}

export type McpServerAssignmentAgent = {
  id: string
  name: string
  description: string
  isActive: boolean
  assigned: boolean
  organizationId: string | null
  lastInteractionAt: string | null
}

export type McpServerAssignmentResponse = {
  server: {
    id: string
    displayName: string
    scope: string
    scopeLabel: string
  }
  agents: McpServerAssignmentAgent[]
  totalAgents: number
  assignedCount: number
  message?: string
}

const mapServer = (server: McpServerDTO): McpServer => ({
  id: server.id,
  name: server.name,
  displayName: server.display_name,
  description: server.description ?? '',
  command: server.command ?? '',
  commandArgs: Array.isArray(server.command_args)
    ? server.command_args.map((arg) => (arg == null ? '' : String(arg)))
    : [],
  url: server.url ?? '',
  authMethod: server.auth_method,
  isActive: server.is_active,
  scope: server.scope,
  scopeLabel: server.scope_label ?? server.scope,
  updatedAt: server.updated_at ?? '',
  createdAt: server.created_at ?? server.updated_at ?? '',
  oauthStatusUrl: server.oauth_status_url,
  oauthRevokeUrl: server.oauth_revoke_url,
  oauthPending: Boolean(server.oauth_pending),
  oauthConnected: Boolean(server.oauth_connected),
})

const mapServerDetail = (server: McpServerDetailDTO): McpServerDetail => ({
  ...mapServer(server),
  metadata: server.metadata ?? {},
  headers: server.headers ?? {},
  environment: server.environment ?? {},
  prefetchApps: Array.isArray(server.prefetch_apps) ? server.prefetch_apps : [],
  oauthStatusUrl: server.oauth_status_url,
  oauthRevokeUrl: server.oauth_revoke_url,
})

const mapAssignments = (payload: McpServerAssignmentsResponseDTO): McpServerAssignmentResponse => ({
  server: {
    id: payload.server.id,
    displayName: payload.server.display_name,
    scope: payload.server.scope,
    scopeLabel: payload.server.scope_label,
  },
  agents: (payload.agents ?? []).map((agent) => ({
    id: agent.id,
    name: agent.name,
    description: agent.description ?? '',
    isActive: agent.is_active,
    assigned: Boolean(agent.assigned),
    organizationId: agent.organization_id ?? null,
    lastInteractionAt: agent.last_interaction_at ?? null,
  })),
  totalAgents: payload.total_agents ?? 0,
  assignedCount: payload.assigned_count ?? 0,
  message: payload.message,
})

export async function fetchMcpServers(listUrl: string): Promise<McpServerListResponse> {
  const payload = await jsonFetch<McpServerListResponseDTO>(listUrl)
  return {
    ownerScope: payload.owner_scope,
    ownerLabel: payload.owner_label,
    resultCount: payload.result_count,
    servers: (payload.servers ?? []).map(mapServer),
  }
}

export async function fetchMcpServerDetail(detailUrl: string): Promise<McpServerDetail> {
  const payload = await jsonFetch<{ server: McpServerDetailDTO }>(detailUrl)
  return mapServerDetail(payload.server)
}

export async function createMcpServer(listUrl: string, payload: McpServerPayload): Promise<McpServerDetail> {
  const response = await jsonRequest<McpServerMutationResponseDTO>(listUrl, {
    method: 'POST',
    includeCsrf: true,
    json: payload,
  })
  return mapServerDetail(response.server)
}

export async function updateMcpServer(detailUrl: string, payload: McpServerPayload): Promise<McpServerDetail> {
  const response = await jsonRequest<McpServerMutationResponseDTO>(detailUrl, {
    method: 'PATCH',
    includeCsrf: true,
    json: payload,
  })
  return mapServerDetail(response.server)
}

export async function deleteMcpServer(detailUrl: string): Promise<void> {
  await jsonRequest(detailUrl, {
    method: 'DELETE',
    includeCsrf: true,
  })
}

export async function fetchMcpServerAssignments(assignmentsUrl: string): Promise<McpServerAssignmentResponse> {
  const payload = await jsonFetch<McpServerAssignmentsResponseDTO>(assignmentsUrl)
  return mapAssignments(payload)
}

export async function updateMcpServerAssignments(assignmentsUrl: string, agentIds: string[]): Promise<McpServerAssignmentResponse> {
  const payload = await jsonRequest<McpServerAssignmentsResponseDTO>(assignmentsUrl, {
    method: 'POST',
    includeCsrf: true,
    json: { agent_ids: agentIds },
  })
  return mapAssignments(payload)
}
