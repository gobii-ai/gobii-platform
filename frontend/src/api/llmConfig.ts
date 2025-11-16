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
  supports_tool_choice?: boolean
  use_parallel_tool_calls?: boolean
  supports_vision?: boolean
  browser_base_url?: string
  max_output_tokens?: number | null
  type: 'persistent' | 'browser' | 'embedding'
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
}

export type PersistentTier = {
  id: string
  order: number
  description: string
  is_premium: boolean
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
  is_premium: boolean
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

export type EndpointChoices = {
  persistent_endpoints: ProviderEndpoint[]
  browser_endpoints: ProviderEndpoint[]
  embedding_endpoints: ProviderEndpoint[]
}

export type LlmOverviewResponse = {
  stats: LlmStats
  providers: Provider[]
  persistent: { ranges: TokenRange[] }
  browser: BrowserPolicy | null
  embeddings: { tiers: EmbeddingTier[] }
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

export function createPersistentTier(rangeId: string, payload: { is_premium: boolean; description?: string }) {
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

export function createBrowserTier(payload: { is_premium: boolean; description?: string }) {
  return jsonRequest(`${base}/browser/tiers/`, withCsrf(payload))
}

export function updateBrowserTier(tierId: string, payload: Record<string, unknown>) {
  return jsonRequest(`${base}/browser/tiers/${tierId}/`, withCsrf(payload, 'PATCH'))
}

export function deleteBrowserTier(tierId: string) {
  return jsonRequest(`${base}/browser/tiers/${tierId}/`, withCsrf(undefined, 'DELETE'))
}

export function addBrowserTierEndpoint(tierId: string, payload: { endpoint_id: string; weight: number }) {
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
