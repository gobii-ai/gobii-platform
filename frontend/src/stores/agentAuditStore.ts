import { create } from 'zustand'
import type { AuditEvent, AuditTimelineBucket } from '../types/agentAudit'
import { fetchAuditEvents, fetchAuditTimeline } from '../api/agentAudit'

type AuditState = {
  agentId: string | null
  events: AuditEvent[]
  nextCursor: string | null
  hasMore: boolean
  loading: boolean
  error: string | null
  processingActive: boolean
  timeline: AuditTimelineBucket[]
  timelineLoading: boolean
  timelineError: string | null
  selectedTimestamp: string | null
  initialize: (agentId: string) => Promise<void>
  loadMore: () => Promise<void>
  loadTimeline: (agentId: string) => Promise<void>
  jumpToTime: (day: string) => Promise<void>
  setSelectedDay: (day: string | null) => void
  receiveRealtimeEvent: (payload: any) => void
}

function mergeEvents(existing: AuditEvent[], incoming: AuditEvent[]): AuditEvent[] {
  const map = new Map<string, AuditEvent>()
  for (const event of existing) {
    const key = `${event.kind}:${(event as any).id}`
    map.set(key, event)
  }
  for (const event of incoming) {
    const key = `${event.kind}:${(event as any).id}`
    map.set(key, event)
  }
  const merged = Array.from(map.values())
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
  timeline: [],
  timelineLoading: false,
  timelineError: null,
  selectedTimestamp: null,

  async initialize(agentId: string) {
    set({ loading: true, agentId, error: null, selectedTimestamp: null })
    try {
      const payload = await fetchAuditEvents(agentId, { limit: 40, tzOffsetMinutes: -new Date().getTimezoneOffset() })
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
      const payload = await fetchAuditEvents(state.agentId, {
        cursor: state.nextCursor,
        limit: 40,
        tzOffsetMinutes: -new Date().getTimezoneOffset(),
      })
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

  async loadTimeline(agentId: string) {
    set({ timelineLoading: true, timelineError: null })
    try {
      const payload = await fetchAuditTimeline(agentId)
      set((current) => ({
        timeline: payload.buckets || [],
        timelineLoading: false,
        selectedTimestamp: current.selectedTimestamp || payload.latest || null,
      }))
    } catch (error) {
      set({
        timelineLoading: false,
        timelineError: error instanceof Error ? error.message : 'Failed to load timeline',
      })
    }
  },

  async jumpToTime(timestamp: string) {
    const state = get()
    if (!state.agentId) {
      return
    }
    const targetDate = new Date(timestamp)
    if (Number.isNaN(targetDate.getTime())) {
      set({ error: 'Invalid timestamp' })
      return
    }
    set({ loading: true, error: null, selectedTimestamp: timestamp })
    try {
      const payload = await fetchAuditEvents(state.agentId, {
        limit: 40,
        day: timestamp,
        tzOffsetMinutes: -new Date().getTimezoneOffset(),
      })
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
        error: error instanceof Error ? error.message : 'Failed to jump to time',
      })
    }
  },

  setSelectedDay(day: string | null) {
    set({ selectedTimestamp: day })
  },

  receiveRealtimeEvent(payload: any) {
    const state = get()
    const agentId = state.agentId
    if (!agentId) return
    const kind: string | undefined = payload?.kind
    if (!kind) return

    if (kind === 'processing_status') {
      const active = Boolean(payload?.active)
      set({ processingActive: active })
      return
    }

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
