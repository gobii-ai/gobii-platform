import { jsonRequest } from './http'

export type UpdateAgentPayload = {
  preferred_llm_tier?: string
}

export function updateAgent(agentId: string, payload: UpdateAgentPayload): Promise<void> {
  return jsonRequest(`/console/api/agents/${agentId}/`, {
    method: 'PATCH',
    json: payload,
    includeCsrf: true,
  })
}
