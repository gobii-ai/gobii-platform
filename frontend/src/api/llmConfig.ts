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
  browser_backend: string
  supports_safety_identifier: boolean
  vertex_project: string
  vertex_location: string
  status: string
  endpoints: ProviderEndpoint[]
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
type TierEndpointCreatePayload = { endpoint_id: string; weight: number; extraction_endpoint_id?: string | null }

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

export function createPersistentTier(rangeId: string, payload: { intelligence_tier: string; description?: string }) {
  return createTier(`${base}/persistent/ranges/${rangeId}/tiers/`, payload)
}

export function updatePersistentTier(tierId: string, payload: Record<string, unknown>) {
  return updateTier(tierPaths.persistent.tiers, tierId, payload)
}

export function deletePersistentTier(tierId: string) {
  return deleteTier(tierPaths.persistent.tiers, tierId)
}

export function addPersistentTierEndpoint(tierId: string, payload: { endpoint_id: string; weight: number }) {
  return addTierEndpoint(tierPaths.persistent.tiers, tierId, payload)
}

export function updatePersistentTierEndpoint(tierEndpointId: string, payload: Record<string, unknown>) {
  return updateTierEndpoint(tierPaths.persistent.tierEndpoints, tierEndpointId, payload)
}

export function deletePersistentTierEndpoint(tierEndpointId: string) {
  return deleteTierEndpoint(tierPaths.persistent.tierEndpoints, tierEndpointId)
}

export function createBrowserTier(payload: { intelligence_tier: string; description?: string }) {
  return createTier(tierPaths.browser.tiers, payload)
}

export function updateBrowserTier(tierId: string, payload: Record<string, unknown>) {
  return updateTier(tierPaths.browser.tiers, tierId, payload)
}

export function deleteBrowserTier(tierId: string) {
  return deleteTier(tierPaths.browser.tiers, tierId)
}

export function addBrowserTierEndpoint(
  tierId: string,
  payload: { endpoint_id: string; weight: number; extraction_endpoint_id?: string | null },
) {
  return addTierEndpoint(tierPaths.browser.tiers, tierId, payload)
}

export function updateBrowserTierEndpoint(tierEndpointId: string, payload: Record<string, unknown>) {
  return updateTierEndpoint(tierPaths.browser.tierEndpoints, tierEndpointId, payload)
}

export function deleteBrowserTierEndpoint(tierEndpointId: string) {
  return deleteTierEndpoint(tierPaths.browser.tierEndpoints, tierEndpointId)
}

export function createEmbeddingTier(payload: { description?: string }) {
  return createTier(tierPaths.embedding.tiers, payload)
}

export function updateEmbeddingTier(tierId: string, payload: Record<string, unknown>) {
  return updateTier(tierPaths.embedding.tiers, tierId, payload)
}

export function deleteEmbeddingTier(tierId: string) {
  return deleteTier(tierPaths.embedding.tiers, tierId)
}

export function addEmbeddingTierEndpoint(tierId: string, payload: { endpoint_id: string; weight: number }) {
  return addTierEndpoint(tierPaths.embedding.tiers, tierId, payload)
}

export function updateEmbeddingTierEndpoint(tierEndpointId: string, payload: Record<string, unknown>) {
  return updateTierEndpoint(tierPaths.embedding.tierEndpoints, tierEndpointId, payload)
}

export function deleteEmbeddingTierEndpoint(tierEndpointId: string) {
  return deleteTierEndpoint(tierPaths.embedding.tierEndpoints, tierEndpointId)
}

export function createFileHandlerTier(payload: { description?: string }) {
  return createTier(tierPaths.file_handler.tiers, payload)
}

export function updateFileHandlerTier(tierId: string, payload: Record<string, unknown>) {
  return updateTier(tierPaths.file_handler.tiers, tierId, payload)
}

export function deleteFileHandlerTier(tierId: string) {
  return deleteTier(tierPaths.file_handler.tiers, tierId)
}

export function addFileHandlerTierEndpoint(tierId: string, payload: { endpoint_id: string; weight: number }) {
  return addTierEndpoint(tierPaths.file_handler.tiers, tierId, payload)
}

export function updateFileHandlerTierEndpoint(tierEndpointId: string, payload: Record<string, unknown>) {
  return updateTierEndpoint(tierPaths.file_handler.tierEndpoints, tierEndpointId, payload)
}

export function deleteFileHandlerTierEndpoint(tierEndpointId: string) {
  return deleteTierEndpoint(tierPaths.file_handler.tierEndpoints, tierEndpointId)
}

export function createImageGenerationTier(payload: { description?: string; use_case?: 'create_image' | 'avatar' }) {
  return createTier(tierPaths.image_generation.tiers, payload)
}

export function updateImageGenerationTier(tierId: string, payload: Record<string, unknown>) {
  return updateTier(tierPaths.image_generation.tiers, tierId, payload)
}

export function deleteImageGenerationTier(tierId: string) {
  return deleteTier(tierPaths.image_generation.tiers, tierId)
}

export function addImageGenerationTierEndpoint(tierId: string, payload: { endpoint_id: string; weight: number }) {
  return addTierEndpoint(tierPaths.image_generation.tiers, tierId, payload)
}

export function updateImageGenerationTierEndpoint(tierEndpointId: string, payload: Record<string, unknown>) {
  return updateTierEndpoint(tierPaths.image_generation.tierEndpoints, tierEndpointId, payload)
}

export function deleteImageGenerationTierEndpoint(tierEndpointId: string) {
  return deleteTierEndpoint(tierPaths.image_generation.tierEndpoints, tierEndpointId)
}

export function createVideoGenerationTier(payload: { description?: string; use_case?: 'create_video' }) {
  return createTier(tierPaths.video_generation.tiers, payload)
}

export function updateVideoGenerationTier(tierId: string, payload: Record<string, unknown>) {
  return updateTier(tierPaths.video_generation.tiers, tierId, payload)
}

export function deleteVideoGenerationTier(tierId: string) {
  return deleteTier(tierPaths.video_generation.tiers, tierId)
}

export function addVideoGenerationTierEndpoint(tierId: string, payload: { endpoint_id: string; weight: number }) {
  return addTierEndpoint(tierPaths.video_generation.tiers, tierId, payload)
}

export function updateVideoGenerationTierEndpoint(tierEndpointId: string, payload: Record<string, unknown>) {
  return updateTierEndpoint(tierPaths.video_generation.tierEndpoints, tierEndpointId, payload)
}

export function deleteVideoGenerationTierEndpoint(tierEndpointId: string) {
  return deleteTierEndpoint(tierPaths.video_generation.tierEndpoints, tierEndpointId)
}

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

export type LlmPerformanceSample = {
  sample: number
  ok: boolean
  status?: 'pending' | 'running'
  latency_ms?: number | null
  prompt_tokens?: number | null
  completion_tokens?: number | null
  total_tokens?: number | null
  cached_tokens?: number | null
  input_cost_total?: number | null
  input_cost_uncached?: number | null
  input_cost_cached?: number | null
  output_cost?: number | null
  total_cost?: number | null
  completion_tokens_per_second?: number | null
  response_type?: 'content' | 'tool_call' | 'empty' | string
  preview?: string
  error?: string
  usage_returned?: boolean
}

export type LlmPerformanceEndpointSummary = {
  success_count: number
  error_count: number
  latency_ms: {
    min: number | null
    avg: number | null
    p50: number | null
    p95: number | null
    max: number | null
  }
  avg_completion_tokens_per_second: number | null
  avg_prompt_tokens: number | null
  avg_completion_tokens: number | null
  avg_total_tokens: number | null
  avg_input_cost_total: number | null
  avg_output_cost: number | null
  avg_total_cost: number | null
  total_prompt_tokens: number
  total_completion_tokens: number
  total_tokens: number
  total_input_cost: number
  total_output_cost: number
  total_cost: number
}

export type LlmPerformanceEndpointResult = {
  endpoint: {
    id: string
    key: string
    label: string
    provider: string
    model: string
  }
  input_sizes: LlmPerformanceInputSizeResult[]
}

export type LlmPerformanceInputSizeMetadata = {
  requested_input_tokens: number
  estimated_prompt_tokens: number | null
  message_count: number | null
}

export type LlmPerformanceInputSizeResult = {
  requested_input_tokens: number
  estimated_prompt_tokens: number | null
  message_count: number | null
  samples: LlmPerformanceSample[]
  summary: LlmPerformanceEndpointSummary
}

export type LlmPerformanceTestResponse = {
  ok: boolean
  input_token_sizes: number[]
  samples_per_endpoint: number
  endpoints: LlmPerformanceEndpointResult[]
}

export type LlmPerformanceSampleResponse = {
  ok: boolean
  endpoint: LlmPerformanceEndpointResult['endpoint']
  input_size: LlmPerformanceInputSizeMetadata
  sample: LlmPerformanceSample
}

export function runPerformanceTest(payload: {
  endpoint_id: string
  input_token_size: number
  sample_number: number
}) {
  return jsonRequest<LlmPerformanceSampleResponse>(`${base}/performance-test/`, withCsrf(payload))
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

export type ProfilePersistentTier = {
  id: string
  order: number
  description: string
  intelligence_tier: IntelligenceTier
  endpoints: TierEndpoint[]
}

export type ProfileTokenRange = {
  id: string
  name: string
  min_tokens: number
  max_tokens: number | null
  tiers: ProfilePersistentTier[]
}

export type ProfileBrowserTier = {
  id: string
  order: number
  description: string
  intelligence_tier: IntelligenceTier
  endpoints: TierEndpoint[]
}

export type ProfileEmbeddingTier = {
  id: string
  order: number
  description: string
  endpoints: TierEndpoint[]
}

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

// Profile-specific tier management
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

export function createProfileTokenRange(profileId: string, payload: { name: string; min_tokens: number; max_tokens: number | null }) {
  return jsonRequest(`${profileBase}/${profileId}/token-ranges/`, withCsrf(payload))
}

export function updateProfileTokenRange(rangeId: string, payload: Record<string, unknown>) {
  return jsonRequest(`${profileBase}/token-ranges/${rangeId}/`, withCsrf(payload, 'PATCH'))
}

export function deleteProfileTokenRange(rangeId: string) {
  return jsonRequest(`${profileBase}/token-ranges/${rangeId}/`, withCsrf(undefined, 'DELETE'))
}

export function createProfilePersistentTier(rangeId: string, payload: { intelligence_tier: string; description?: string }) {
  return createTier(`${profileBase}/token-ranges/${rangeId}/tiers/`, payload)
}

export function updateProfilePersistentTier(tierId: string, payload: Record<string, unknown>) {
  return updateTier(profileTierPaths.persistent.tiers, tierId, payload)
}

export function deleteProfilePersistentTier(tierId: string) {
  return deleteTier(profileTierPaths.persistent.tiers, tierId)
}

export function addProfilePersistentTierEndpoint(tierId: string, payload: { endpoint_id: string; weight: number }) {
  return addTierEndpoint(profileTierPaths.persistent.tiers, tierId, payload)
}

export function updateProfilePersistentTierEndpoint(tierEndpointId: string, payload: Record<string, unknown>) {
  return updateTierEndpoint(profileTierPaths.persistent.tierEndpoints, tierEndpointId, payload)
}

export function deleteProfilePersistentTierEndpoint(tierEndpointId: string) {
  return deleteTierEndpoint(profileTierPaths.persistent.tierEndpoints, tierEndpointId)
}

export function createProfileBrowserTier(profileId: string, payload: { intelligence_tier: string; description?: string }) {
  return createTier(`${profileBase}/${profileId}/browser-tiers/`, payload)
}

export function updateProfileBrowserTier(tierId: string, payload: Record<string, unknown>) {
  return updateTier(profileTierPaths.browser.tiers, tierId, payload)
}

export function deleteProfileBrowserTier(tierId: string) {
  return deleteTier(profileTierPaths.browser.tiers, tierId)
}

export function addProfileBrowserTierEndpoint(
  tierId: string,
  payload: { endpoint_id: string; weight: number; extraction_endpoint_id?: string | null },
) {
  return addTierEndpoint(profileTierPaths.browser.tiers, tierId, payload)
}

export function updateProfileBrowserTierEndpoint(tierEndpointId: string, payload: Record<string, unknown>) {
  return updateTierEndpoint(profileTierPaths.browser.tierEndpoints, tierEndpointId, payload)
}

export function deleteProfileBrowserTierEndpoint(tierEndpointId: string) {
  return deleteTierEndpoint(profileTierPaths.browser.tierEndpoints, tierEndpointId)
}

export function createProfileEmbeddingTier(profileId: string, payload: { description?: string }) {
  return createTier(`${profileBase}/${profileId}/embeddings-tiers/`, payload)
}

export function updateProfileEmbeddingTier(tierId: string, payload: Record<string, unknown>) {
  return updateTier(profileTierPaths.embedding.tiers, tierId, payload)
}

export function deleteProfileEmbeddingTier(tierId: string) {
  return deleteTier(profileTierPaths.embedding.tiers, tierId)
}

export function addProfileEmbeddingTierEndpoint(tierId: string, payload: { endpoint_id: string; weight: number }) {
  return addTierEndpoint(profileTierPaths.embedding.tiers, tierId, payload)
}

export function updateProfileEmbeddingTierEndpoint(tierEndpointId: string, payload: Record<string, unknown>) {
  return updateTierEndpoint(profileTierPaths.embedding.tierEndpoints, tierEndpointId, payload)
}

export function deleteProfileEmbeddingTierEndpoint(tierEndpointId: string) {
  return deleteTierEndpoint(profileTierPaths.embedding.tierEndpoints, tierEndpointId)
}
