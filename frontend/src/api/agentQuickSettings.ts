import type { AgentQuickSettingsResponse, AgentQuickSettingsUpdatePayload } from '../types/agentQuickSettings'
import { jsonFetch, jsonRequest } from './http'

export async function fetchAgentQuickSettings(agentId: string): Promise<AgentQuickSettingsResponse> {
  return jsonFetch<AgentQuickSettingsResponse>(`/console/api/agents/${agentId}/quick-settings/`)
}

export async function updateAgentQuickSettings(
  agentId: string,
  payload: AgentQuickSettingsUpdatePayload,
): Promise<AgentQuickSettingsResponse> {
  return jsonRequest(`/console/api/agents/${agentId}/quick-settings/`, {
    method: 'POST',
    json: payload,
    includeCsrf: true,
  })
}
