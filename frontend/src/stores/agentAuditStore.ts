import { create } from 'zustand'
import type { AuditEvent } from '../types/agentAudit'
import { fetchAuditEvents } from '../api/agentAudit'

type AuditState = {
  agentId: string | null
  events: AuditEvent[]
  nextCursor: string | null
  hasMore: boolean
  loading: boolean
  error: string | null
  processingActive: boolean
  initialize: (agentId: string) => Promise<void>
  loadMore: () => Promise<void>
  receiveRealtimeEvent: (payload: any) => void
}

function mergeEvents(existing: AuditEvent[], incoming: AuditEvent[]): AuditEvent[] {
  const seen = new Set(existing.map((e) => `${e.kind}:${(e as any).id}`))
  const merged: AuditEvent[] = [...existing]
  incoming.forEach((ev) => {
    const key = `${ev.kind}:${(ev as any).id}`
    if (!seen.has(key)) {
      seen.add(key)
      merged.push(ev)
    }
  })
  merged.sort((a, b) => {
    const at = (a as any).timestamp || ''
    const bt = (b as any).timestamp || ''
    if (at === bt) {
      return ((b as any).id || '').localeCompare((a as any).id || '')
    }
    return bt.localeCompare(at)
  })
  return merged
}

export const useAgentAuditStore = create<AuditState>((set, get) => ({
  agentId: null,
  events: [],
  nextCursor: null,
  hasMore: false,
  loading: false,
  error: null,
  processingActive: false,

  async initialize(agentId: string) {
    set({ loading: true, agentId, error: null })
    try {
      const payload = await fetchAuditEvents(agentId, { limit: 40 })
      const events = payload.events || []
      set({
        events,
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
      const payload = await fetchAuditEvents(state.agentId, { cursor: state.nextCursor, limit: 40 })
      const incoming = payload.events || []
      set((current) => ({
        events: mergeEvents(current.events, incoming),
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
    const kind: string | undefined = payload?.kind
    if (!kind) return

    if (kind === 'run_started') {
      // Ignore run_started for flattened view
      return
    }

    const event = payload as AuditEvent
    set((current) => ({
      events: mergeEvents(current.events, [event]),
    }))
  },
}))
