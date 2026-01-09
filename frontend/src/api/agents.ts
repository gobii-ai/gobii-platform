import { jsonFetch, jsonRequest } from './http'
import type { AgentRosterEntry } from '../types/agentRoster'

export type UpdateAgentPayload = {
  preferred_llm_tier?: string
}

type AgentRosterPayload = {
  agents: {
    id: string
    name: string
    avatar_url: string | null
    display_color_hex: string | null
    is_active: boolean
  }[]
}

export async function fetchAgentRoster(): Promise<AgentRosterEntry[]> {
  const payload = await jsonFetch<AgentRosterPayload>('/console/api/agents/roster/')
  return payload.agents.map((agent) => ({
    id: agent.id,
    name: agent.name,
    avatarUrl: agent.avatar_url,
    displayColorHex: agent.display_color_hex,
    isActive: agent.is_active,
  }))
}

export function updateAgent(agentId: string, payload: UpdateAgentPayload): Promise<void> {
  return jsonRequest(`/console/api/agents/${agentId}/`, {
    method: 'PATCH',
    json: payload,
    includeCsrf: true,
  })
}
