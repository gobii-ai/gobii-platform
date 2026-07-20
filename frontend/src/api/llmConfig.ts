import { jsonFetch, jsonRequest } from './http'

export type LlmStats = {
  active_providers: number
  persistent_endpoints: number
  browser_endpoints: number
  premium_persistent_tiers: number
}

export type ProviderEndpoint = {
  id: string
  label: string
  key: string
  model: string
  litellm_pricing_model?: string | null
  api_base?: string
  temperature_override?: number | null
  supports_temperature?: boolean
  supports_tool_choice?: boolean
  use_parallel_tool_calls?: boolean
  allow_implied_send?: boolean
  supports_vision?: boolean
  supports_image_to_image?: boolean
  supports_image_to_video?: boolean
  browser_base_url?: string
  max_output_tokens?: number | null
  max_input_tokens?: number | null
  supports_reasoning?: boolean
  reasoning_effort?: string | null
  openrouter_preset?: string | null
  type: 'persistent' | 'browser' | 'embedding' | 'file_handler' | 'image_generation' | 'video_generation'
  low_latency?: boolean
  enabled: boolean
  provider_id: string
  tier_usage?: EndpointTierUsage[]
}

export type EndpointTierUsage = {
  id: string
  source: 'browser_policy' | 'routing_profile' | string
  routing_profile: string
  routing_profile_active: boolean
  tier: string
  tier_order: number
  intelligence_tier: string
  description?: string
  weight?: number
  role?: 'primary' | 'extraction' | string
}

export type Provider = {
  id: string
  name: string
  key: string
  enabled: boolean
  env_var: string
  model_prefix: string
  browser_backend: string
  supports_safety_identifier: boolean
  vertex_project: string
  vertex_location: string
  status: string
  endpoints: ProviderEndpoint[]
}

export type ProviderBrowserBackend = 'OPENAI' | 'ANTHROPIC' | 'GOOGLE' | 'OPENAI_COMPAT'

export type ProviderCreatePayload = {
  display_name: string
  key: string
  api_key?: string
  env_var_name?: string
  model_prefix?: string
  browser_backend: ProviderBrowserBackend
  supports_safety_identifier: boolean
  vertex_project?: string
  vertex_location?: string
  enabled: boolean
}

export type TierEndpoint = {
  id: string
  endpoint_id: string
  label: string
  weight: number
  endpoint_key: string
  reasoning_effort_override?: string | null
  supports_reasoning?: boolean
  endpoint_reasoning_effort?: string | null
  extraction_endpoint_id?: string | null
  extraction_endpoint_key?: string | null
  extraction_label?: string | null
}

export type IntelligenceTier = {
  key: string
  display_name: string
  rank: number
  credit_multiplier: string
}

export type PersistentTier = {
  id: string
  order: number
  description: string
  intelligence_tier: IntelligenceTier
  endpoints: TierEndpoint[]
}

export type TokenRange = {
  id: string
  name: string
  min_tokens: number
  max_tokens: number | null
  tiers: PersistentTier[]
}

export type BrowserTier = {
  id: string
  order: number
  description: string
  intelligence_tier: IntelligenceTier
  endpoints: TierEndpoint[]
}

export type BrowserPolicy = {
  id: string
  name: string
  tiers: BrowserTier[]
}

export type EmbeddingTier = {
  id: string
  order: number
  description: string
  endpoints: TierEndpoint[]
}

export type FileHandlerTier = {
  id: string
  order: number
  description: string
  endpoints: TierEndpoint[]
}

export type ImageGenerationTier = {
  id: string
  order: number
  description: string
  use_case?: 'create_image' | 'avatar' | null
  endpoints: TierEndpoint[]
}

export type VideoGenerationTier = {
  id: string
  order: number
  description: string
  use_case?: 'create_video' | null
  endpoints: TierEndpoint[]
}

export type EndpointChoices = {
  persistent_endpoints: ProviderEndpoint[]
  browser_endpoints: ProviderEndpoint[]
  embedding_endpoints: ProviderEndpoint[]
  file_handler_endpoints: ProviderEndpoint[]
  image_generation_endpoints: ProviderEndpoint[]
  video_generation_endpoints: ProviderEndpoint[]
}

export type LlmOverviewResponse = {
  stats: LlmStats
  intelligence_tiers: IntelligenceTier[]
  providers: Provider[]
  persistent: { ranges: TokenRange[] }
  browser: BrowserPolicy | null
  embeddings: { tiers: EmbeddingTier[] }
  file_handlers: { tiers: FileHandlerTier[] }
  image_generations: { create_image_tiers: ImageGenerationTier[]; avatar_tiers: ImageGenerationTier[] }
  video_generations: { create_video_tiers: VideoGenerationTier[] }
  choices: EndpointChoices
}

const base = '/console/api/llm'

export function fetchLlmOverview(signal?: AbortSignal): Promise<LlmOverviewResponse> {
  return jsonFetch<LlmOverviewResponse>(`${base}/overview/`, { signal })
}

function withCsrf(json?: unknown, method: string = 'POST') {
  return {
    method,
    includeCsrf: true,
    json,
  } as const
}

export function updateProvider(providerId: string, payload: Record<string, unknown>) {
  return jsonRequest(`${base}/providers/${providerId}/`, withCsrf(payload, 'PATCH'))
}

export function createProvider(payload: ProviderCreatePayload) {
  return jsonRequest(`${base}/providers/`, withCsrf(payload))
}

const endpointPaths = {
  persistent: `${base}/persistent/endpoints/`,
  browser: `${base}/browser/endpoints/`,
  embedding: `${base}/embeddings/endpoints/`,
  file_handler: `${base}/file-handlers/endpoints/`,
  image_generation: `${base}/image-generations/endpoints/`,
  video_generation: `${base}/video-generations/endpoints/`,
} as const

type EndpointKind = keyof typeof endpointPaths

export function createEndpoint(kind: EndpointKind, payload: Record<string, unknown>) {
  return jsonRequest(endpointPaths[kind], withCsrf(payload))
}

export function updateEndpoint(kind: EndpointKind, endpointId: string, payload: Record<string, unknown>) {
  return jsonRequest(`${endpointPaths[kind]}${endpointId}/`, withCsrf(payload, 'PATCH'))
}

export function deleteEndpoint(kind: EndpointKind, endpointId: string, options: { force?: boolean } = {}) {
  const suffix = options.force ? '?force=1' : ''
  return jsonRequest(`${endpointPaths[kind]}${endpointId}/${suffix}`, withCsrf(undefined, 'DELETE'))
}

export function createTokenRange(payload: { name: string; min_tokens: number; max_tokens: number | null }) {
  return jsonRequest(`${base}/persistent/ranges/`, withCsrf(payload))
}

export function updateTokenRange(rangeId: string, payload: Record<string, unknown>) {
  return jsonRequest(`${base}/persistent/ranges/${rangeId}/`, withCsrf(payload, 'PATCH'))
}

export function deleteTokenRange(rangeId: string) {
  return jsonRequest(`${base}/persistent/ranges/${rangeId}/`, withCsrf(undefined, 'DELETE'))
}

type TierCreatePayload = Record<string, unknown>
export type TierEndpointCreatePayload = { endpoint_id: string; weight: number; extraction_endpoint_id?: string | null }

function createTier(path: string, payload: TierCreatePayload) {
  return jsonRequest(path, withCsrf(payload))
}

function updateTier(path: string, tierId: string, payload: Record<string, unknown>) {
  return jsonRequest(`${path}${tierId}/`, withCsrf(payload, 'PATCH'))
}

function deleteTier(path: string, tierId: string) {
  return jsonRequest(`${path}${tierId}/`, withCsrf(undefined, 'DELETE'))
}

function addTierEndpoint(path: string, tierId: string, payload: TierEndpointCreatePayload) {
  return jsonRequest(`${path}${tierId}/endpoints/`, withCsrf(payload))
}

function updateTierEndpoint(path: string, tierEndpointId: string, payload: Record<string, unknown>) {
  return jsonRequest(`${path}${tierEndpointId}/`, withCsrf(payload, 'PATCH'))
}

function deleteTierEndpoint(path: string, tierEndpointId: string) {
  return jsonRequest(`${path}${tierEndpointId}/`, withCsrf(undefined, 'DELETE'))
}

const tierPaths = {
  persistent: {
    tiers: `${base}/persistent/tiers/`,
    tierEndpoints: `${base}/persistent/tier-endpoints/`,
  },
  browser: {
    tiers: `${base}/browser/tiers/`,
    tierEndpoints: `${base}/browser/tier-endpoints/`,
  },
  embedding: {
    tiers: `${base}/embeddings/tiers/`,
    tierEndpoints: `${base}/embeddings/tier-endpoints/`,
  },
  file_handler: {
    tiers: `${base}/file-handlers/tiers/`,
    tierEndpoints: `${base}/file-handlers/tier-endpoints/`,
  },
  image_generation: {
    tiers: `${base}/image-generations/tiers/`,
    tierEndpoints: `${base}/image-generations/tier-endpoints/`,
  },
  video_generation: {
    tiers: `${base}/video-generations/tiers/`,
    tierEndpoints: `${base}/video-generations/tier-endpoints/`,
  },
} as const

export type TierClient = {
  create: (parentId: string | null, payload: TierCreatePayload) => Promise<unknown>
  update: (tierId: string, payload: Record<string, unknown>) => Promise<unknown>
  remove: (tierId: string) => Promise<unknown>
  addEndpoint: (tierId: string, payload: TierEndpointCreatePayload) => Promise<{ tier_endpoint_id?: string }>
  updateEndpoint: (tierEndpointId: string, payload: Record<string, unknown>) => Promise<unknown>
  removeEndpoint: (tierEndpointId: string) => Promise<unknown>
}

function makeTierClient(
  paths: { tiers: string; tierEndpoints: string },
  createPath: string | ((parentId: string | null) => string) = paths.tiers,
): TierClient {
  return {
    create: (parentId, payload) => createTier(
      typeof createPath === 'function' ? createPath(parentId) : createPath,
      payload,
    ),
    update: (tierId, payload) => updateTier(paths.tiers, tierId, payload),
    remove: (tierId) => deleteTier(paths.tiers, tierId),
    addEndpoint: (tierId, payload) => addTierEndpoint(paths.tiers, tierId, payload) as Promise<{ tier_endpoint_id?: string }>,
    updateEndpoint: (tierEndpointId, payload) => updateTierEndpoint(paths.tierEndpoints, tierEndpointId, payload),
    removeEndpoint: (tierEndpointId) => deleteTierEndpoint(paths.tierEndpoints, tierEndpointId),
  }
}

export type SystemTierScope = keyof typeof tierPaths
export const systemTierClients: Record<SystemTierScope, TierClient> = Object.fromEntries(
  Object.entries(tierPaths).map(([scope, paths]) => [
    scope,
    makeTierClient(paths, scope === 'persistent' ? (rangeId) => `${base}/persistent/ranges/${rangeId}/tiers/` : paths.tiers),
  ]),
) as Record<SystemTierScope, TierClient>

export type EndpointTestResponse = {
  ok: boolean
  message: string
  preview?: string
  latency_ms?: number
  total_tokens?: number | null
  prompt_tokens?: number | null
  completion_tokens?: number | null
  dimensions?: number | null
}

export function testEndpoint(payload: { endpoint_id: string; kind: ProviderEndpoint['type'] }) {
  return jsonRequest<EndpointTestResponse>(`${base}/test-endpoint/`, withCsrf(payload))
}

// =============================================================================
// Routing Profiles
// =============================================================================

export type RoutingProfileListItem = {
  id: string
  name: string
  display_name: string
  description: string
  is_active: boolean
  created_at: string | null
  updated_at: string | null
  cloned_from_id: string | null
  eval_judge_endpoint_id: string | null
  summarization_endpoint_id: string | null
  agent_judge_endpoint_id: string | null
}

export type EvalJudgeEndpoint = {
  endpoint_id: string
  endpoint_key: string
  label: string
  model: string
}

export type ProfilePersistentTier = PersistentTier

export type ProfileTokenRange = TokenRange

export type ProfileBrowserTier = BrowserTier

export type ProfileEmbeddingTier = EmbeddingTier

export type RoutingProfileDetail = {
  id: string
  name: string
  display_name: string
  description: string
  is_active: boolean
  created_at: string | null
  updated_at: string | null
  cloned_from_id: string | null
  eval_judge_endpoint: EvalJudgeEndpoint | null
  summarization_endpoint: EvalJudgeEndpoint | null
  agent_judge_endpoint: EvalJudgeEndpoint | null
  persistent: { ranges: ProfileTokenRange[] }
  browser: { tiers: ProfileBrowserTier[] }
  embeddings: { tiers: ProfileEmbeddingTier[] }
}

export type RoutingProfilesListResponse = {
  profiles: RoutingProfileListItem[]
}

export type RoutingProfileDetailResponse = {
  profile: RoutingProfileDetail
}

export function fetchRoutingProfiles(signal?: AbortSignal): Promise<RoutingProfilesListResponse> {
  return jsonFetch<RoutingProfilesListResponse>(`${base}/routing-profiles/`, { signal })
}

export function fetchRoutingProfileDetail(profileId: string, signal?: AbortSignal): Promise<RoutingProfileDetailResponse> {
  return jsonFetch<RoutingProfileDetailResponse>(`${base}/routing-profiles/${profileId}/`, { signal })
}

export function createRoutingProfile(payload: {
  name: string
  display_name?: string
  description?: string
}): Promise<{ ok: boolean; profile_id: string }> {
  return jsonRequest(`${base}/routing-profiles/`, withCsrf(payload))
}

export function updateRoutingProfile(profileId: string, payload: Record<string, unknown>) {
  return jsonRequest(`${base}/routing-profiles/${profileId}/`, withCsrf(payload, 'PATCH'))
}

export function deleteRoutingProfile(profileId: string) {
  return jsonRequest(`${base}/routing-profiles/${profileId}/`, withCsrf(undefined, 'DELETE'))
}

export function activateRoutingProfile(profileId: string) {
  return jsonRequest(`${base}/routing-profiles/${profileId}/activate/`, withCsrf({}))
}

export function cloneRoutingProfile(profileId: string, payload?: {
  name?: string
  display_name?: string
  description?: string
}): Promise<{ ok: boolean; profile_id: string; name: string }> {
  return jsonRequest(`${base}/routing-profiles/${profileId}/clone/`, withCsrf(payload ?? {}))
}

const profileBase = `${base}/routing-profiles`
const profileTierPaths = {
  persistent: {
    tiers: `${profileBase}/persistent-tiers/`,
    tierEndpoints: `${profileBase}/persistent-tier-endpoints/`,
  },
  browser: {
    tiers: `${profileBase}/browser-tiers/`,
    tierEndpoints: `${profileBase}/browser-tier-endpoints/`,
  },
  embedding: {
    tiers: `${profileBase}/embeddings-tiers/`,
    tierEndpoints: `${profileBase}/embeddings-tier-endpoints/`,
  },
} as const

export type RoutingTierScope = keyof typeof profileTierPaths
export type TokenRangeClient = {
  create: (payload: { name: string; min_tokens: number; max_tokens: number | null }) => Promise<unknown>
  update: (rangeId: string, payload: Record<string, unknown>) => Promise<unknown>
  remove: (rangeId: string) => Promise<unknown>
}
export type RoutingConfigClient = {
  isProfile: boolean
  ranges: TokenRangeClient
  tiers: Record<RoutingTierScope, TierClient>
}

export function createRoutingConfigClient(profileId: string | null): RoutingConfigClient {
  if (!profileId) {
    return {
      isProfile: false,
      ranges: { create: createTokenRange, update: updateTokenRange, remove: deleteTokenRange },
      tiers: {
        persistent: systemTierClients.persistent,
        browser: systemTierClients.browser,
        embedding: systemTierClients.embedding,
      },
    }
  }

  const tiers = {
    persistent: makeTierClient(
      profileTierPaths.persistent,
      (rangeId) => `${profileBase}/token-ranges/${rangeId}/tiers/`,
    ),
    browser: makeTierClient(profileTierPaths.browser, `${profileBase}/${profileId}/browser-tiers/`),
    embedding: makeTierClient(profileTierPaths.embedding, `${profileBase}/${profileId}/embeddings-tiers/`),
  }
  return {
    isProfile: true,
    ranges: {
      create: (payload) => jsonRequest(`${profileBase}/${profileId}/token-ranges/`, withCsrf(payload)),
      update: (rangeId, payload) => jsonRequest(`${profileBase}/token-ranges/${rangeId}/`, withCsrf(payload, 'PATCH')),
      remove: (rangeId) => jsonRequest(`${profileBase}/token-ranges/${rangeId}/`, withCsrf(undefined, 'DELETE')),
    },
    tiers,
  }
}
