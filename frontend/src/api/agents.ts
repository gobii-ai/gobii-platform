import { jsonFetch, jsonRequest } from './http'
import type { ConsoleContext } from './context'
import type { AgentRosterEntry } from '../types/agentRoster'

export type UpdateAgentPayload = {
  preferred_llm_tier?: string
}

export type CreateAgentResponse = {
  agent_id: string
  agent_name: string
}

type AgentRosterPayload = {
  context: ConsoleContext
  agents: {
    id: string
    name: string
    avatar_url: string | null
    display_color_hex: string | null
    is_active: boolean
    short_description: string
    is_org_owned: boolean
  }[]
}

export async function fetchAgentRoster(options: { forAgentId?: string } = {}): Promise<{ context: ConsoleContext; agents: AgentRosterEntry[] }> {
  const query = options.forAgentId ? `?for_agent=${encodeURIComponent(options.forAgentId)}` : ''
  const payload = await jsonFetch<AgentRosterPayload>(`/console/api/agents/roster/${query}`)
  const agents = payload.agents.map((agent) => ({
    id: agent.id,
    name: agent.name,
    avatarUrl: agent.avatar_url,
    displayColorHex: agent.display_color_hex,
    isActive: agent.is_active,
    shortDescription: agent.short_description,
    isOrgOwned: agent.is_org_owned,
  }))
  return { context: payload.context, agents }
}

export function updateAgent(agentId: string, payload: UpdateAgentPayload): Promise<void> {
  return jsonRequest(`/console/api/agents/${agentId}/`, {
    method: 'PATCH',
    json: payload,
    includeCsrf: true,
  })
}

export async function createAgent(message: string): Promise<CreateAgentResponse> {
  return jsonFetch<CreateAgentResponse>('/console/api/agents/create/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message }),
  })
}
