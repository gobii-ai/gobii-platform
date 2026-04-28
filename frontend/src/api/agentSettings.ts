import { jsonFetch } from './http'
import type { AgentSettingsData } from '../types/agentSettings'

export async function fetchAgentSettings(agentId: string): Promise<AgentSettingsData> {
  return jsonFetch<AgentSettingsData>(`/console/api/agents/${agentId}/settings/`)
}
