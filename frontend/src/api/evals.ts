import { jsonFetch, jsonRequest } from './http'

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
}

export type EvalRun = {
  id: string
  suite_run_id: string | null
  scenario_slug: string
  scenario_version: string
  status: string
  started_at: string | null
  finished_at: string | null
  agent_id: string | null
  tasks?: EvalTask[]
  task_totals?: { total: number; passed: number; failed: number }
}

export type EvalSuiteRun = {
  id: string
  suite_slug: string
  status: string
  agent_strategy: string
  shared_agent_id: string | null
  started_at: string | null
  finished_at: string | null
  runs?: EvalRun[]
  run_totals?: { total_runs: number; completed: number; errored: number }
}

export type EvalSuite = {
  slug: string
  description: string
  scenario_slugs: string[]
}

export function fetchSuites(signal?: AbortSignal): Promise<{ suites: EvalSuite[] }> {
  return jsonFetch('/console/api/evals/suites/', { method: 'GET', signal })
}

export function fetchSuiteRuns(params: { status?: string; suite?: string; limit?: number } = {}): Promise<{
  suite_runs: EvalSuiteRun[]
}> {
  const search = new URLSearchParams()
  if (params.status) search.set('status', params.status)
  if (params.suite) search.set('suite', params.suite)
  if (params.limit) search.set('limit', params.limit.toString())
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
