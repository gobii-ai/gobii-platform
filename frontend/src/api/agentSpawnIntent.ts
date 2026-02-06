import { jsonFetch } from './http'

export type AgentSpawnIntent = {
  charter: string | null
  preferred_llm_tier: string | null
  onboarding_target: 'agent_ui' | 'api_keys' | null
  requires_plan_selection: boolean
}

export async function fetchAgentSpawnIntent(signal?: AbortSignal): Promise<AgentSpawnIntent> {
  return jsonFetch<AgentSpawnIntent>('/console/api/agents/spawn-intent/', { signal })
}
