import { jsonFetch } from './http'

export type TemplateRecommendation = {
  id: string
  name: string
  tagline: string
  description: string
  category: string
  templateCode: string
  templateId: string
  templateSource: 'organization' | 'public'
  likeCount: number
  isOfficial: boolean
}

export type TemplateRecommendationsPayload = {
  category: string
  categories?: string[]
  source: string
  templates: TemplateRecommendation[]
}

export type AgentSpawnIntent = {
  charter: string | null
  charter_override: string | null
  preferred_llm_tier: string | null
  selected_pipedream_app_slugs: string[]
  onboarding_target: 'agent_ui' | 'api_keys' | null
  requires_plan_selection: boolean
  template_recommendations?: TemplateRecommendationsPayload | null
}

export async function fetchAgentSpawnIntent(signal?: AbortSignal): Promise<AgentSpawnIntent> {
  return jsonFetch<AgentSpawnIntent>('/console/api/agents/spawn-intent/', { signal })
}
