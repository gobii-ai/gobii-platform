import type { AgentAddonsResponse, AgentAddonsUpdatePayload } from '../types/agentAddons'
import { jsonFetch, jsonRequest } from './http'

export async function fetchAgentAddons(agentId: string): Promise<AgentAddonsResponse> {
  return jsonFetch<AgentAddonsResponse>(`/console/api/agents/${agentId}/addons/`)
}

export async function updateAgentAddons(
  agentId: string,
  payload: AgentAddonsUpdatePayload,
): Promise<AgentAddonsResponse> {
  return jsonRequest(`/console/api/agents/${agentId}/addons/`, {
    method: 'POST',
    json: payload,
    includeCsrf: true,
  })
}
