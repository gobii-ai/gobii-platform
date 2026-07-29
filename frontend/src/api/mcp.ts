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
  allow_commands: boolean
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

type McpServerTestToolDTO = {
  full_name: string
  tool_name: string
  server_name: string
  description: string
  parameters: Record<string, unknown>
}

type McpServerTestResponseDTO = {
  status: 'ok' | 'error'
  message: string
  sandboxed?: boolean
  agent?: {
    id: string
    name: string
  } | null
  tools?: McpServerTestToolDTO[]
  details?: Record<string, unknown>
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

type PipedreamAppSummaryDTO = {
  slug: string
  name: string
  description: string
  icon_url: string
}

type PipedreamAppSettingsDTO = {
  owner_scope: string
  owner_label: string
  platform_apps: PipedreamAppSummaryDTO[]
  selected_apps: PipedreamAppSummaryDTO[]
  effective_apps: PipedreamAppSummaryDTO[]
  message?: string
}

type PipedreamAppSearchResponseDTO = {
  results: PipedreamAppSummaryDTO[]
}

type AgentPipedreamAppRowDTO = PipedreamAppSummaryDTO & {
  source: 'built_in' | 'added' | 'available'
  connected: boolean
  account_ids: string[]
}

type AgentPipedreamAppsResponseDTO = {
  agent_id: string
  owner_scope: string
  owner_label: string
  query: string
  apps: AgentPipedreamAppRowDTO[]
}

type AgentPipedreamConnectResponseDTO = {
  app: PipedreamAppSummaryDTO
  connect_url: string
  selected_app_slugs: string[]
}

type AgentPipedreamDisconnectResponseDTO = {
  app_slug: string
  connected: boolean
  deleted_count: number
}

type AgentPipedreamRemoveResponseDTO = {
  app_slug: string
  removed: boolean
  selected_app_slugs: string[]
}

type PipedreamAppAgentConnectionDTO = {
  agent_id: string
  name: string
  avatar_url: string
  connected: boolean
  account_ids: string[]
}

type PipedreamAppAgentConnectionsResponseDTO = {
  app_slug: string
  agents: PipedreamAppAgentConnectionDTO[]
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
  allowCommands: boolean
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
  prefetch_apps?: string[]
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

export type McpServerTestTool = {
  fullName: string
  toolName: string
  serverName: string
  description: string
  parameters: Record<string, unknown>
}

export type McpServerTestResponse = {
  status: 'ok' | 'error'
  message: string
  sandboxed: boolean
  agent: {
    id: string
    name: string
  } | null
  tools: McpServerTestTool[]
  details: Record<string, unknown>
}

export type PipedreamAppSummary = {
  slug: string
  name: string
  description: string
  iconUrl: string
}

export type PipedreamAppSettings = {
  ownerScope: string
  ownerLabel: string
  platformApps: PipedreamAppSummary[]
  selectedApps: PipedreamAppSummary[]
  effectiveApps: PipedreamAppSummary[]
  message?: string
}

export type AgentPipedreamAppSource = 'built_in' | 'added' | 'available'

export type AgentPipedreamAppRow = PipedreamAppSummary & {
  source: AgentPipedreamAppSource
  connected: boolean
  accountIds: string[]
}

export type AgentPipedreamAppsResponse = {
  agentId: string
  ownerScope: string
  ownerLabel: string
  query: string
  apps: AgentPipedreamAppRow[]
}

export type AgentPipedreamConnectResponse = {
  app: PipedreamAppSummary
  connectUrl: string
  selectedAppSlugs: string[]
}

export type AgentPipedreamDisconnectResponse = {
  appSlug: string
  connected: boolean
  deletedCount: number
}

export type AgentPipedreamRemoveResponse = {
  appSlug: string
  removed: boolean
  selectedAppSlugs: string[]
}

export type PipedreamAppAgentConnection = {
  agentId: string
  name: string
  avatarUrl: string
  connected: boolean
  accountIds: string[]
}

export type PipedreamAppAgentConnectionsResponse = {
  appSlug: string
  agents: PipedreamAppAgentConnection[]
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

const mapTestTool = (tool: McpServerTestToolDTO): McpServerTestTool => ({
  fullName: tool.full_name ?? '',
  toolName: tool.tool_name ?? '',
  serverName: tool.server_name ?? '',
  description: tool.description ?? '',
  parameters: tool.parameters ?? {},
})

const mapTestResponse = (payload: McpServerTestResponseDTO): McpServerTestResponse => ({
  status: payload.status,
  message: payload.message ?? '',
  sandboxed: Boolean(payload.sandboxed),
  agent: payload.agent ? { id: payload.agent.id, name: payload.agent.name } : null,
  tools: (payload.tools ?? []).map(mapTestTool),
  details: payload.details ?? {},
})

export const mapPipedreamApp = (app: PipedreamAppSummaryDTO): PipedreamAppSummary => ({
  slug: app.slug ?? '',
  name: app.name ?? app.slug ?? '',
  description: app.description ?? '',
  iconUrl: app.icon_url ?? '',
})

const mapAgentPipedreamAppRow = (app: AgentPipedreamAppRowDTO): AgentPipedreamAppRow => ({
  ...mapPipedreamApp(app),
  source: app.source,
  connected: Boolean(app.connected),
  accountIds: Array.isArray(app.account_ids) ? app.account_ids.map((id) => String(id)) : [],
})

const mapPipedreamAppAgentConnection = (
  agent: PipedreamAppAgentConnectionDTO,
): PipedreamAppAgentConnection => ({
  agentId: agent.agent_id,
  name: agent.name ?? '',
  avatarUrl: agent.avatar_url ?? '',
  connected: Boolean(agent.connected),
  accountIds: Array.isArray(agent.account_ids) ? agent.account_ids.map((id) => String(id)) : [],
})

const mapPipedreamSettings = (payload: PipedreamAppSettingsDTO): PipedreamAppSettings => ({
  ownerScope: payload.owner_scope,
  ownerLabel: payload.owner_label,
  platformApps: (payload.platform_apps ?? []).map(mapPipedreamApp),
  selectedApps: (payload.selected_apps ?? []).map(mapPipedreamApp),
  effectiveApps: (payload.effective_apps ?? []).map(mapPipedreamApp),
  message: payload.message,
})

export async function fetchMcpServers(listUrl: string): Promise<McpServerListResponse> {
  const payload = await jsonFetch<McpServerListResponseDTO>(listUrl)
  return {
    ownerScope: payload.owner_scope,
    ownerLabel: payload.owner_label,
    allowCommands: Boolean(payload.allow_commands),
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

export async function testMcpServer(testUrl: string, agentId?: string | null): Promise<McpServerTestResponse> {
  const payload = await jsonRequest<McpServerTestResponseDTO>(testUrl, {
    method: 'POST',
    includeCsrf: true,
    json: agentId ? { agent_id: agentId } : {},
  })
  return mapTestResponse(payload)
}

export async function fetchPipedreamAppSettings(settingsUrl: string): Promise<PipedreamAppSettings> {
  const payload = await jsonFetch<PipedreamAppSettingsDTO>(settingsUrl)
  return mapPipedreamSettings(payload)
}

export async function updatePipedreamAppSettings(
  settingsUrl: string,
  selectedAppSlugs: string[],
): Promise<PipedreamAppSettings> {
  const payload = await jsonRequest<PipedreamAppSettingsDTO>(settingsUrl, {
    method: 'PATCH',
    includeCsrf: true,
    json: { selected_app_slugs: selectedAppSlugs },
  })
  return mapPipedreamSettings(payload)
}

export async function searchPipedreamApps(searchUrl: string, query: string): Promise<PipedreamAppSummary[]> {
  const normalizedQuery = query.trim()
  if (!normalizedQuery) {
    return []
  }
  const url = new URL(searchUrl, window.location.origin)
  url.searchParams.set('q', normalizedQuery)
  const payload = await jsonFetch<PipedreamAppSearchResponseDTO>(url.toString())
  return (payload.results ?? []).map(mapPipedreamApp)
}

function agentPipedreamAppsUrl(agentId: string): string {
  return `/console/api/agents/${agentId}/pipedream/apps/`
}

export async function fetchAgentPipedreamApps(
  agentId: string,
  query: string,
): Promise<AgentPipedreamAppsResponse> {
  const url = new URL(agentPipedreamAppsUrl(agentId), window.location.origin)
  const normalizedQuery = query.trim()
  if (normalizedQuery) {
    url.searchParams.set('q', normalizedQuery)
  }
  const payload = await jsonFetch<AgentPipedreamAppsResponseDTO>(url.toString())
  return {
    agentId: payload.agent_id,
    ownerScope: payload.owner_scope,
    ownerLabel: payload.owner_label,
    query: payload.query ?? '',
    apps: (payload.apps ?? []).map(mapAgentPipedreamAppRow),
  }
}

export async function startAgentPipedreamAppConnect(
  agentId: string,
  appSlug: string,
): Promise<AgentPipedreamConnectResponse> {
  const payload = await jsonRequest<AgentPipedreamConnectResponseDTO>(
    `${agentPipedreamAppsUrl(agentId)}${encodeURIComponent(appSlug)}/connect/`,
    {
      method: 'POST',
      includeCsrf: true,
      json: {},
    },
  )
  return {
    app: mapPipedreamApp(payload.app),
    connectUrl: payload.connect_url,
    selectedAppSlugs: Array.isArray(payload.selected_app_slugs)
      ? payload.selected_app_slugs.map((slug) => String(slug))
      : [],
  }
}

export async function disconnectAgentPipedreamApp(
  agentId: string,
  appSlug: string,
): Promise<AgentPipedreamDisconnectResponse> {
  const payload = await jsonRequest<AgentPipedreamDisconnectResponseDTO>(
    `${agentPipedreamAppsUrl(agentId)}${encodeURIComponent(appSlug)}/connection/`,
    {
      method: 'DELETE',
      includeCsrf: true,
    },
  )
  return {
    appSlug: payload.app_slug,
    connected: Boolean(payload.connected),
    deletedCount: payload.deleted_count ?? 0,
  }
}

export async function removeAgentPipedreamApp(
  agentId: string,
  appSlug: string,
): Promise<AgentPipedreamRemoveResponse> {
  const payload = await jsonRequest<AgentPipedreamRemoveResponseDTO>(
    `${agentPipedreamAppsUrl(agentId)}${encodeURIComponent(appSlug)}/`,
    {
      method: 'DELETE',
      includeCsrf: true,
    },
  )
  return {
    appSlug: payload.app_slug,
    removed: Boolean(payload.removed),
    selectedAppSlugs: Array.isArray(payload.selected_app_slugs)
      ? payload.selected_app_slugs.map((slug) => String(slug))
      : [],
  }
}

export async function fetchPipedreamAppAgentConnections(
  appSlug: string,
): Promise<PipedreamAppAgentConnectionsResponse> {
  const payload = await jsonFetch<PipedreamAppAgentConnectionsResponseDTO>(
    `/console/api/mcp/pipedream/apps/${encodeURIComponent(appSlug)}/agents/`,
  )
  return {
    appSlug: payload.app_slug,
    agents: (payload.agents ?? []).map(mapPipedreamAppAgentConnection),
  }
}
