import { jsonFetch } from './http'

export type AgentSpawnIntent = {
  charter: string | null
  preferred_llm_tier: string | null
}

export async function fetchAgentSpawnIntent(): Promise<AgentSpawnIntent> {
  return jsonFetch<AgentSpawnIntent>('/console/api/agents/spawn-intent/')
}
