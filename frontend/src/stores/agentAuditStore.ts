import { create } from 'zustand'
import type { AuditEvent, AuditRun, AuditRunStartedEvent, AuditCompletionEvent } from '../types/agentAudit'
import { fetchAuditRuns } from '../api/agentAudit'

type AuditState = {
  agentId: string | null
  runs: AuditRun[]
  nextCursor: string | null
  hasMore: boolean
  loading: boolean
  error: string | null
  processingActive: boolean
  initialize: (agentId: string) => Promise<void>
  loadMore: () => Promise<void>
  receiveRealtimeEvent: (payload: any) => void
}

function ensureRunSkeleton(runId: string, startedAt?: string | null, sequence?: number | null): AuditRun {
  return {
    run_id: runId,
    sequence: sequence ?? 0,
    started_at: startedAt || new Date().toISOString(),
    ended_at: null,
    events: [],
    token_totals: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0, cached_tokens: 0 },
    active: true,
  }
}

function appendEvent(run: AuditRun, event: AuditEvent): AuditRun {
  const exists = run.events.some((existing) => existing.kind === event.kind && (existing as any).id === (event as any).id)
  if (exists) {
    return run
  }
  if (event.kind === 'tool_call' && (event as any).completion_id) {
    const completionIndex = run.events.findIndex(
      (e) => e.kind === 'completion' && (e as any).id === (event as any).completion_id,
    )
    if (completionIndex >= 0) {
      const completion = { ...(run.events[completionIndex] as AuditCompletionEvent) }
      const existingCalls = completion.tool_calls ? [...completion.tool_calls] : []
      const duplicate = existingCalls.some((call) => call.id === (event as any).id)
      if (!duplicate) {
        existingCalls.push(event as any)
        existingCalls.sort((a, b) => (a.timestamp || '').localeCompare(b.timestamp || ''))
        completion.tool_calls = existingCalls
      }
      const updatedEvents = [...run.events]
      updatedEvents[completionIndex] = completion
      return { ...run, events: updatedEvents }
    }
  }
  const merged = [...run.events, event]
  merged.sort((a, b) => {
    const at = (a as any).timestamp || ''
    const bt = (b as any).timestamp || ''
    if (at === bt) {
      return ((a as any).id || '').localeCompare((b as any).id || '')
    }
    return at.localeCompare(bt)
  })
  const tokenTotals = { ...run.token_totals }
  if (event.kind === 'completion') {
    const completion = event as AuditCompletionEvent
    tokenTotals.prompt_tokens += completion.prompt_tokens || 0
    tokenTotals.completion_tokens += completion.completion_tokens || 0
    tokenTotals.total_tokens += completion.total_tokens || 0
    tokenTotals.cached_tokens += completion.cached_tokens || 0
  }
  return { ...run, events: merged, token_totals: tokenTotals }
}

export const useAgentAuditStore = create<AuditState>((set, get) => ({
  agentId: null,
  runs: [],
  nextCursor: null,
  hasMore: false,
  loading: false,
  error: null,
  processingActive: false,

  async initialize(agentId: string) {
    set({ loading: true, agentId, error: null })
    try {
      const payload = await fetchAuditRuns(agentId, { limit: 4 })
      const runs = (payload.runs || []).filter((run) => (run.events || []).length > 0)
      set({
        runs,
        nextCursor: payload.next_cursor,
        hasMore: payload.has_more,
        processingActive: payload.processing_active,
        loading: false,
      })
    } catch (error) {
      set({
        loading: false,
        error: error instanceof Error ? error.message : 'Failed to load audit runs',
      })
    }
  },

  async loadMore() {
    const state = get()
    if (!state.agentId || !state.hasMore || state.loading) {
      return
    }
    set({ loading: true })
    try {
      const payload = await fetchAuditRuns(state.agentId, { cursor: state.nextCursor, limit: 4 })
      const incoming = (payload.runs || []).filter((run) => (run.events || []).length > 0)
      set((current) => ({
        runs: [...current.runs, ...incoming],
        nextCursor: payload.next_cursor,
        hasMore: payload.has_more,
        processingActive: payload.processing_active,
        loading: false,
      }))
    } catch (error) {
      set({
        loading: false,
        error: error instanceof Error ? error.message : 'Failed to load more runs',
      })
    }
  },

  receiveRealtimeEvent(payload: any) {
    const state = get()
    const agentId = state.agentId
    if (!agentId) return
    const runId: string | undefined = payload?.run_id
    const kind: string | undefined = payload?.kind
    if (!runId || !kind) return

    if (kind === 'run_started') {
      const event = payload as AuditRunStartedEvent
      const skeleton = ensureRunSkeleton(runId, event.timestamp, event.sequence ?? undefined)
      set((current) => ({
        runs: [skeleton, ...current.runs],
        processingActive: true,
      }))
      return
    }

    const event = payload as AuditEvent
    set((current) => {
      const runs = [...current.runs]
      const targetIndex = runs.findIndex((run) => run.run_id === runId)
      if (targetIndex === -1) {
        const newRun = appendEvent(ensureRunSkeleton(runId, event.timestamp || new Date().toISOString(), null), event)
        return { runs: [newRun, ...runs] }
      }
      runs[targetIndex] = appendEvent(runs[targetIndex], event)
      return { runs }
    })
  },
}))
