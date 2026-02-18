import { jsonFetch, jsonRequest } from './http'
import type { ConsoleContext } from './context'
import type { AgentRosterEntry } from '../types/agentRoster'
import type { LlmIntelligenceConfig } from '../types/llmIntelligence'

export type UpdateAgentPayload = {
  preferred_llm_tier?: string
}

export type CreateAgentResponse = {
  agent_id: string
  agent_name: string
  agent_email?: string | null
}

type AgentRosterPayload = {
  context: ConsoleContext
  requested_agent_status?: 'deleted' | 'missing' | null
  llmIntelligence?: LlmIntelligenceConfig | null
  agents: {
    id: string
    name: string
    avatar_url: string | null
    display_color_hex: string | null
    is_active: boolean
    mini_description: string
    short_description: string
    is_org_owned: boolean
    is_collaborator: boolean
    can_manage_agent: boolean
    can_manage_collaborators: boolean
    audit_url?: string | null
    preferred_llm_tier: string | null
    email: string | null
    sms: string | null
  }[]
}

export async function fetchAgentRoster(
  options: { forAgentId?: string } = {},
): Promise<{
  context: ConsoleContext
  agents: AgentRosterEntry[]
  requestedAgentStatus?: 'deleted' | 'missing' | null
  llmIntelligence?: LlmIntelligenceConfig | null
}> {
  const query = options.forAgentId ? `?for_agent=${encodeURIComponent(options.forAgentId)}` : ''
  const payload = await jsonFetch<AgentRosterPayload>(`/console/api/agents/roster/${query}`)
  const agents = payload.agents.map((agent) => ({
    id: agent.id,
    name: agent.name,
    avatarUrl: agent.avatar_url,
    displayColorHex: agent.display_color_hex,
    isActive: agent.is_active,
    miniDescription: agent.mini_description,
    shortDescription: agent.short_description,
    auditUrl: agent.audit_url ?? null,
    isOrgOwned: agent.is_org_owned,
    isCollaborator: agent.is_collaborator,
    canManageAgent: agent.can_manage_agent,
    canManageCollaborators: agent.can_manage_collaborators,
    preferredLlmTier: agent.preferred_llm_tier,
    email: agent.email,
    sms: agent.sms,
  }))
  return {
    context: payload.context,
    agents,
    requestedAgentStatus: payload.requested_agent_status ?? null,
    llmIntelligence: payload.llmIntelligence,
  }
}

export function updateAgent(agentId: string, payload: UpdateAgentPayload): Promise<void> {
  return jsonRequest(`/console/api/agents/${agentId}/`, {
    method: 'PATCH',
    json: payload,
    includeCsrf: true,
  })
}

export async function createAgent(message: string, preferredLlmTier?: string, charterOverride?: string | null): Promise<CreateAgentResponse> {
  const payload: Record<string, string | undefined> = { message, preferred_llm_tier: preferredLlmTier }
  if (charterOverride) {
    payload.charter_override = charterOverride
  }
  return jsonFetch<CreateAgentResponse>('/console/api/agents/create/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export function leaveCollaboration(agentId: string): Promise<void> {
  return jsonRequest(`/console/api/agents/${agentId}/collaboration/leave/`, {
    method: 'POST',
    includeCsrf: true,
  })
}
