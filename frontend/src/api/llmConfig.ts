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
  api_base?: string
  temperature_override?: number | null
  supports_temperature?: boolean
  supports_tool_choice?: boolean
  use_parallel_tool_calls?: boolean
  supports_vision?: boolean
  browser_base_url?: string
  max_output_tokens?: number | null
  max_input_tokens?: number | null
  supports_reasoning?: boolean
  reasoning_effort?: string | null
  openrouter_preset?: string | null
  type: 'persistent' | 'browser' | 'embedding' | 'file_handler'
  low_latency?: boolean
  enabled: boolean
  provider_id: string
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

export type EndpointChoices = {
  persistent_endpoints: ProviderEndpoint[]
  browser_endpoints: ProviderEndpoint[]
  embedding_endpoints: ProviderEndpoint[]
  file_handler_endpoints: ProviderEndpoint[]
}

export type LlmOverviewResponse = {
  stats: LlmStats
  intelligence_tiers: IntelligenceTier[]
  providers: Provider[]
  persistent: { ranges: TokenRange[] }
  browser: BrowserPolicy | null
  embeddings: { tiers: EmbeddingTier[] }
  file_handlers: { tiers: FileHandlerTier[] }
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

export function createProvider(payload: {
  display_name: string
  key: string
  env_var_name?: string
  browser_backend?: string
  supports_safety_identifier?: boolean
  vertex_project?: string
  vertex_location?: string
  api_key?: string
}): Promise<{ ok: boolean; provider_id: string }> {
  return jsonRequest(`${base}/providers/`, withCsrf(payload))
}

export function updateProvider(providerId: string, payload: Record<string, unknown>) {
  return jsonRequest(`${base}/providers/${providerId}/`, withCsrf(payload, 'PATCH'))
}

const endpointPaths = {
  persistent: `${base}/persistent/endpoints/`,
  browser: `${base}/browser/endpoints/`,
  embedding: `${base}/embeddings/endpoints/`,
  file_handler: `${base}/file-handlers/endpoints/`,
} as const

type EndpointKind = keyof typeof endpointPaths

export function createEndpoint(kind: EndpointKind, payload: Record<string, unknown>) {
  return jsonRequest(endpointPaths[kind], withCsrf(payload))
}

export function updateEndpoint(kind: EndpointKind, endpointId: string, payload: Record<string, unknown>) {
  return jsonRequest(`${endpointPaths[kind]}${endpointId}/`, withCsrf(payload, 'PATCH'))
}

export function deleteEndpoint(kind: EndpointKind, endpointId: string) {
  return jsonRequest(`${endpointPaths[kind]}${endpointId}/`, withCsrf(undefined, 'DELETE'))
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

export function createPersistentTier(rangeId: string, payload: { intelligence_tier: string; description?: string }) {
  return jsonRequest(`${base}/persistent/ranges/${rangeId}/tiers/`, withCsrf(payload))
}

export function updatePersistentTier(tierId: string, payload: Record<string, unknown>) {
  return jsonRequest(`${base}/persistent/tiers/${tierId}/`, withCsrf(payload, 'PATCH'))
}

export function deletePersistentTier(tierId: string) {
  return jsonRequest(`${base}/persistent/tiers/${tierId}/`, withCsrf(undefined, 'DELETE'))
}

export function addPersistentTierEndpoint(tierId: string, payload: { endpoint_id: string; weight: number }) {
  return jsonRequest(`${base}/persistent/tiers/${tierId}/endpoints/`, withCsrf(payload))
}

export function updatePersistentTierEndpoint(tierEndpointId: string, payload: Record<string, unknown>) {
  return jsonRequest(`${base}/persistent/tier-endpoints/${tierEndpointId}/`, withCsrf(payload, 'PATCH'))
}

export function deletePersistentTierEndpoint(tierEndpointId: string) {
  return jsonRequest(`${base}/persistent/tier-endpoints/${tierEndpointId}/`, withCsrf(undefined, 'DELETE'))
}

export function createBrowserTier(payload: { intelligence_tier: string; description?: string }) {
  return jsonRequest(`${base}/browser/tiers/`, withCsrf(payload))
}

export function updateBrowserTier(tierId: string, payload: Record<string, unknown>) {
  return jsonRequest(`${base}/browser/tiers/${tierId}/`, withCsrf(payload, 'PATCH'))
}

export function deleteBrowserTier(tierId: string) {
  return jsonRequest(`${base}/browser/tiers/${tierId}/`, withCsrf(undefined, 'DELETE'))
}

export function addBrowserTierEndpoint(
  tierId: string,
  payload: { endpoint_id: string; weight: number; extraction_endpoint_id?: string | null },
) {
  return jsonRequest(`${base}/browser/tiers/${tierId}/endpoints/`, withCsrf(payload))
}

export function updateBrowserTierEndpoint(tierEndpointId: string, payload: Record<string, unknown>) {
  return jsonRequest(`${base}/browser/tier-endpoints/${tierEndpointId}/`, withCsrf(payload, 'PATCH'))
}

export function deleteBrowserTierEndpoint(tierEndpointId: string) {
  return jsonRequest(`${base}/browser/tier-endpoints/${tierEndpointId}/`, withCsrf(undefined, 'DELETE'))
}

export function createEmbeddingTier(payload: { description?: string }) {
  return jsonRequest(`${base}/embeddings/tiers/`, withCsrf(payload))
}

export function updateEmbeddingTier(tierId: string, payload: Record<string, unknown>) {
  return jsonRequest(`${base}/embeddings/tiers/${tierId}/`, withCsrf(payload, 'PATCH'))
}

export function deleteEmbeddingTier(tierId: string) {
  return jsonRequest(`${base}/embeddings/tiers/${tierId}/`, withCsrf(undefined, 'DELETE'))
}

export function addEmbeddingTierEndpoint(tierId: string, payload: { endpoint_id: string; weight: number }) {
  return jsonRequest(`${base}/embeddings/tiers/${tierId}/endpoints/`, withCsrf(payload))
}

export function updateEmbeddingTierEndpoint(tierEndpointId: string, payload: Record<string, unknown>) {
  return jsonRequest(`${base}/embeddings/tier-endpoints/${tierEndpointId}/`, withCsrf(payload, 'PATCH'))
}

export function deleteEmbeddingTierEndpoint(tierEndpointId: string) {
  return jsonRequest(`${base}/embeddings/tier-endpoints/${tierEndpointId}/`, withCsrf(undefined, 'DELETE'))
}

export function createFileHandlerTier(payload: { description?: string }) {
  return jsonRequest(`${base}/file-handlers/tiers/`, withCsrf(payload))
}

export function updateFileHandlerTier(tierId: string, payload: Record<string, unknown>) {
  return jsonRequest(`${base}/file-handlers/tiers/${tierId}/`, withCsrf(payload, 'PATCH'))
}

export function deleteFileHandlerTier(tierId: string) {
  return jsonRequest(`${base}/file-handlers/tiers/${tierId}/`, withCsrf(undefined, 'DELETE'))
}

export function addFileHandlerTierEndpoint(tierId: string, payload: { endpoint_id: string; weight: number }) {
  return jsonRequest(`${base}/file-handlers/tiers/${tierId}/endpoints/`, withCsrf(payload))
}

export function updateFileHandlerTierEndpoint(tierEndpointId: string, payload: Record<string, unknown>) {
  return jsonRequest(`${base}/file-handlers/tier-endpoints/${tierEndpointId}/`, withCsrf(payload, 'PATCH'))
}

export function deleteFileHandlerTierEndpoint(tierEndpointId: string) {
  return jsonRequest(`${base}/file-handlers/tier-endpoints/${tierEndpointId}/`, withCsrf(undefined, 'DELETE'))
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
  return jsonRequest(`${profileBase}/token-ranges/${rangeId}/tiers/`, withCsrf(payload))
}

export function updateProfilePersistentTier(tierId: string, payload: Record<string, unknown>) {
  return jsonRequest(`${profileBase}/persistent-tiers/${tierId}/`, withCsrf(payload, 'PATCH'))
}

export function deleteProfilePersistentTier(tierId: string) {
  return jsonRequest(`${profileBase}/persistent-tiers/${tierId}/`, withCsrf(undefined, 'DELETE'))
}

export function addProfilePersistentTierEndpoint(tierId: string, payload: { endpoint_id: string; weight: number }) {
  return jsonRequest(`${profileBase}/persistent-tiers/${tierId}/endpoints/`, withCsrf(payload))
}

export function updateProfilePersistentTierEndpoint(tierEndpointId: string, payload: Record<string, unknown>) {
  return jsonRequest(`${profileBase}/persistent-tier-endpoints/${tierEndpointId}/`, withCsrf(payload, 'PATCH'))
}

export function deleteProfilePersistentTierEndpoint(tierEndpointId: string) {
  return jsonRequest(`${profileBase}/persistent-tier-endpoints/${tierEndpointId}/`, withCsrf(undefined, 'DELETE'))
}

export function createProfileBrowserTier(profileId: string, payload: { intelligence_tier: string; description?: string }) {
  return jsonRequest(`${profileBase}/${profileId}/browser-tiers/`, withCsrf(payload))
}

export function updateProfileBrowserTier(tierId: string, payload: Record<string, unknown>) {
  return jsonRequest(`${profileBase}/browser-tiers/${tierId}/`, withCsrf(payload, 'PATCH'))
}

export function deleteProfileBrowserTier(tierId: string) {
  return jsonRequest(`${profileBase}/browser-tiers/${tierId}/`, withCsrf(undefined, 'DELETE'))
}

export function addProfileBrowserTierEndpoint(
  tierId: string,
  payload: { endpoint_id: string; weight: number; extraction_endpoint_id?: string | null },
) {
  return jsonRequest(`${profileBase}/browser-tiers/${tierId}/endpoints/`, withCsrf(payload))
}

export function updateProfileBrowserTierEndpoint(tierEndpointId: string, payload: Record<string, unknown>) {
  return jsonRequest(`${profileBase}/browser-tier-endpoints/${tierEndpointId}/`, withCsrf(payload, 'PATCH'))
}

export function deleteProfileBrowserTierEndpoint(tierEndpointId: string) {
  return jsonRequest(`${profileBase}/browser-tier-endpoints/${tierEndpointId}/`, withCsrf(undefined, 'DELETE'))
}

export function createProfileEmbeddingTier(profileId: string, payload: { description?: string }) {
  return jsonRequest(`${profileBase}/${profileId}/embeddings-tiers/`, withCsrf(payload))
}

export function updateProfileEmbeddingTier(tierId: string, payload: Record<string, unknown>) {
  return jsonRequest(`${profileBase}/embeddings-tiers/${tierId}/`, withCsrf(payload, 'PATCH'))
}

export function deleteProfileEmbeddingTier(tierId: string) {
  return jsonRequest(`${profileBase}/embeddings-tiers/${tierId}/`, withCsrf(undefined, 'DELETE'))
}

export function addProfileEmbeddingTierEndpoint(tierId: string, payload: { endpoint_id: string; weight: number }) {
  return jsonRequest(`${profileBase}/embeddings-tiers/${tierId}/endpoints/`, withCsrf(payload))
}

export function updateProfileEmbeddingTierEndpoint(tierEndpointId: string, payload: Record<string, unknown>) {
  return jsonRequest(`${profileBase}/embeddings-tier-endpoints/${tierEndpointId}/`, withCsrf(payload, 'PATCH'))
}

export function deleteProfileEmbeddingTierEndpoint(tierEndpointId: string) {
  return jsonRequest(`${profileBase}/embeddings-tier-endpoints/${tierEndpointId}/`, withCsrf(undefined, 'DELETE'))
}
