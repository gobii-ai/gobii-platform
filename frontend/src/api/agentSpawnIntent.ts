import { HttpError, jsonFetch } from './http'

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

export type OutcomeEstimate = {
  unit: string
  per: string
  startup: number
  scale: number
}

export type AgentSpawnIntent = {
  charter: string | null
  charter_override: string | null
  preferred_llm_tier: string | null
  selected_pipedream_app_slugs: string[]
  onboarding_target: 'agent_ui' | 'api_keys' | null
  requires_plan_selection: boolean
  template_recommendations?: TemplateRecommendationsPayload | null
  prospective_agent_name?: string | null
  brief_title?: string | null
  outcome_estimate?: OutcomeEstimate | null
}

const RETRY_DELAYS_MS = [500, 1500]

// The spawn intent is the handoff between signup and the first agent: a single
// transient failure here strands the user on an empty /agents/new. The request
// is an idempotent GET, so retry it before giving up.
export async function fetchAgentSpawnIntent(signal?: AbortSignal): Promise<AgentSpawnIntent> {
  for (const delayMs of RETRY_DELAYS_MS) {
    try {
      return await jsonFetch<AgentSpawnIntent>('/console/api/agents/spawn-intent/', { signal })
    } catch (error) {
      const aborted = signal?.aborted || (error instanceof DOMException && error.name === 'AbortError')
      const clientError = error instanceof HttpError && error.status < 500
      if (aborted || clientError) {
        throw error
      }
      await new Promise((resolve) => setTimeout(resolve, delayMs))
      if (signal?.aborted) {
        throw error
      }
    }
  }
  return jsonFetch<AgentSpawnIntent>('/console/api/agents/spawn-intent/', { signal })
}
