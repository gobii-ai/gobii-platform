import { jsonFetch, jsonRequest } from './http'

export type EvalRunType = 'one_off' | 'official'

export type EvalTask = {
  id: number
  sequence: number
  name: string
  status: string
  assertion_type: string
  expected_summary: string
  observed_summary: string
  started_at: string | null
  finished_at: string | null
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  cached_tokens: number
  input_cost_total: number
  input_cost_uncached: number
  input_cost_cached: number
  output_cost: number
  total_cost: number
  credits_cost: number
}

export type EvalTaskTotals = {
  total: number
  completed: number
  passed: number
  failed: number
  pass_rate: number | null
}

export type EvalRun = {
  id: string
  suite_run_id: string | null
  scenario_slug: string
  scenario_version: string
  status: string
  run_type: EvalRunType
  started_at: string | null
  finished_at: string | null
  agent_id: string | null
  llm_routing_profile_name?: string | null
  // Fingerprint fields for comparison
  scenario_fingerprint?: string
  code_version?: string
  code_branch?: string
  primary_model?: string
  // Comparison metadata (only in detail response)
  comparison?: {
    comparable_runs_count: number
    has_comparable_runs: boolean
  }
  tasks?: EvalTask[]
  task_totals?: EvalTaskTotals
  prompt_tokens?: number
  completion_tokens?: number
  cached_tokens?: number
  tokens_used?: number
  input_cost_total?: number
  input_cost_uncached?: number
  input_cost_cached?: number
  output_cost?: number
  total_cost?: number
  credits_cost?: number
  completion_count?: number
  step_count?: number
}

// LLM Routing Profile types for eval snapshots
export type LLMProfileTierEndpoint = {
  id: string
  endpoint_id: string
  label: string
  weight: number
  endpoint_key: string
}

export type LLMProfileIntelligenceTier = {
  key: string
  display_name: string
  rank: number
  credit_multiplier: string
}

export type LLMProfileTier = {
  id: string
  order: number
  description: string
  intelligence_tier?: LLMProfileIntelligenceTier | null
  endpoints: LLMProfileTierEndpoint[]
}

export type LLMProfileTokenRange = {
  id: string
  name: string
  min_tokens: number
  max_tokens: number | null
  tiers: LLMProfileTier[]
}

export type LLMRoutingProfileSnapshot = {
  id: string
  name: string
  display_name: string
  description: string
  is_active: boolean
  is_eval_snapshot: boolean
  created_at: string | null
  updated_at: string | null
  cloned_from_id: string | null
  eval_judge_endpoint: {
    endpoint_id: string
    endpoint_key: string
    label: string
    model: string
  } | null
  persistent: { ranges: LLMProfileTokenRange[] }
  browser: { tiers: LLMProfileTier[] }
  embeddings: { tiers: LLMProfileTier[] }
}

export type EvalSuiteRun = {
  id: string
  suite_slug: string
  status: string
  run_type: EvalRunType
  requested_runs?: number
  agent_strategy: string
  shared_agent_id: string | null
  started_at: string | null
  finished_at: string | null
  runs?: EvalRun[]
  run_totals?: { total_runs: number; completed: number; errored: number }
  task_totals?: EvalTaskTotals | null
  cost_totals?: {
    prompt_tokens: number
    completion_tokens: number
    cached_tokens: number
    tokens_used: number
    input_cost_total: number
    input_cost_uncached: number
    input_cost_cached: number
    output_cost: number
    total_cost: number
    credits_cost: number
  } | null
  llm_routing_profile?: LLMRoutingProfileSnapshot | null
}

export type EvalSuite = {
  slug: string
  description: string
  scenario_slugs: string[]
}

export function fetchSuites(signal?: AbortSignal): Promise<{ suites: EvalSuite[] }> {
  return jsonFetch('/console/api/evals/suites/', { method: 'GET', signal })
}

export function fetchSuiteRuns(params: { status?: string; suite?: string; limit?: number; run_type?: EvalRunType } = {}): Promise<{
  suite_runs: EvalSuiteRun[]
}> {
  const search = new URLSearchParams()
  if (params.status) search.set('status', params.status)
  if (params.suite) search.set('suite', params.suite)
  if (params.limit) search.set('limit', params.limit.toString())
  if (params.run_type) search.set('run_type', params.run_type)
  const query = search.toString()
  const url = `/console/api/evals/suite-runs/${query ? `?${query}` : ''}`
  return jsonFetch(url, { method: 'GET' })
}

export function fetchSuiteRunDetail(suiteRunId: string): Promise<{ suite_run: EvalSuiteRun }> {
  return jsonFetch(`/console/api/evals/suite-runs/${suiteRunId}/`, { method: 'GET' })
}

export function fetchRunDetail(runId: string): Promise<{ run: EvalRun }> {
  return jsonFetch(`/console/api/evals/runs/${runId}/`, { method: 'GET' })
}

export type CreateSuiteRunPayload = {
  suite_slugs: string[]
  agent_strategy?: string
  agent_id?: string | null
  run_type?: EvalRunType
  official?: boolean
  n_runs?: number
  llm_routing_profile_id?: string | null
}

export function createSuiteRuns(payload: CreateSuiteRunPayload): Promise<{
  suite_runs: EvalSuiteRun[]
  runs: string[]
  agent_strategy: string
}> {
  return jsonRequest('/console/api/evals/suite-runs/create/', {
    method: 'POST',
    json: payload,
    includeCsrf: true,
  })
}

export function updateSuiteRunType(
  suiteRunId: string,
  payload: { run_type?: EvalRunType; official?: boolean },
): Promise<{ suite_run: EvalSuiteRun }> {
  return jsonRequest(`/console/api/evals/suite-runs/${suiteRunId}/run-type/`, {
    method: 'POST',
    json: payload,
    includeCsrf: true,
  })
}

// Comparison types
export type ComparisonTier = 'strict' | 'pragmatic' | 'historical'
export type ComparisonGroupBy = 'code_version' | 'primary_model' | 'llm_profile'

export type ComparisonRunSummary = {
  id: string
  scenario_slug: string
  status: string
  run_type: EvalRunType
  started_at: string | null
  finished_at: string | null
  scenario_fingerprint: string
  code_version: string
  code_branch: string
  primary_model: string
  llm_routing_profile_name: string | null
  task_totals: EvalTaskTotals | null
  total_cost: number | null
  tokens_used: number | null
  completion_count: number | null
  step_count: number | null
}

export type ComparisonGroup = {
  group_by: ComparisonGroupBy
  value: string
  run_count: number
  pass_rate: number // percentage 0-100
  avg_cost: number
  avg_tokens: number
  total_tasks: number
  passed_tasks: number
  is_current: boolean
}

export type ComparisonResponse = {
  // Target run info
  target_run_id: string
  target_fingerprint: string | null
  tier: ComparisonTier
  // Grouped results (when group_by is set)
  group_by?: ComparisonGroupBy | null
  groups?: ComparisonGroup[]
  // Ungrouped results (when no group_by)
  runs?: ComparisonRunSummary[]
  // Warnings
  fingerprint_warning?: string | null
  filters?: {
    code_version: string | null
    primary_model: string | null
    run_type: string | null
  }
}

export type ComparisonParams = {
  tier?: ComparisonTier
  group_by?: ComparisonGroupBy
  run_type?: EvalRunType
  code_version?: string
  primary_model?: string
}

export function fetchRunComparison(
  runId: string,
  params: ComparisonParams = {},
  signal?: AbortSignal,
): Promise<ComparisonResponse> {
  const search = new URLSearchParams()
  if (params.tier) search.set('tier', params.tier)
  if (params.group_by) search.set('group_by', params.group_by)
  if (params.run_type) search.set('run_type', params.run_type)
  if (params.code_version) search.set('code_version', params.code_version)
  if (params.primary_model) search.set('primary_model', params.primary_model)
  const query = search.toString()
  const url = `/console/api/evals/runs/${runId}/compare/${query ? `?${query}` : ''}`
  return jsonFetch(url, { method: 'GET', signal })
}

// Suite-level comparison types
export type SuiteComparisonSummary = {
  id: string
  suite_slug: string
  status: string
  run_type: EvalRunType
  started_at: string | null
  finished_at: string | null
  code_version: string | null
  primary_model: string | null
  llm_profile: string | null
  pass_rate: number
  total_cost: number
  total_tokens: number
  passed_tasks: number
  total_tasks: number
}

export type SuiteComparisonResponse = {
  // Target suite info
  target_suite_run_id: string
  tier: ComparisonTier
  // Grouped results (when group_by is set)
  group_by?: ComparisonGroupBy | null
  groups?: ComparisonGroup[]
  // Ungrouped results (when no group_by)
  suite_runs?: SuiteComparisonSummary[]
  // Warnings
  fingerprint_warning?: string | null
  filters?: {
    run_type: string | null
  }
}

export function fetchSuiteRunComparison(
  suiteRunId: string,
  params: ComparisonParams = {},
  signal?: AbortSignal,
): Promise<SuiteComparisonResponse> {
  const search = new URLSearchParams()
  if (params.tier) search.set('tier', params.tier)
  if (params.group_by) search.set('group_by', params.group_by)
  if (params.run_type) search.set('run_type', params.run_type)
  const query = search.toString()
  const url = `/console/api/evals/suite-runs/${suiteRunId}/compare/${query ? `?${query}` : ''}`
  return jsonFetch(url, { method: 'GET', signal })
}
