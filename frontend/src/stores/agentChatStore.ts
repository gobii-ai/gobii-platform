import { create } from 'zustand'

import type { TimelineEvent, ToolClusterEvent, ToolCallEntry } from '../types/agentChat'
import { fetchAgentTimeline, sendAgentMessage, fetchProcessingStatus } from '../api/agentChat'

function parseCursorValue(cursor: string): number {
  const [raw] = cursor.split(':', 1)
  const value = Number(raw)
  return Number.isFinite(value) ? value : 0
}

function sortEvents(events: TimelineEvent[]): TimelineEvent[] {
  return [...events].sort((a, b) => parseCursorValue(a.cursor) - parseCursorValue(b.cursor))
}

function mergeClusters(base: ToolClusterEvent, incoming: ToolClusterEvent): ToolClusterEvent {
  const threshold = base.collapseThreshold || incoming.collapseThreshold
  const entryMap = new Map<string, ToolCallEntry>()
  const insert = (entry: ToolCallEntry) => {
    entryMap.set(entry.id, entry)
  }
  base.entries.forEach(insert)
  incoming.entries.forEach(insert)

  const entries = Array.from(entryMap.values()).sort((left, right) => {
    const leftValue = left.cursor ? parseCursorValue(left.cursor) : 0
    const rightValue = right.cursor ? parseCursorValue(right.cursor) : 0
    return leftValue - rightValue
  })

  const earliestTimestamp = entries[0]?.timestamp ?? base.earliestTimestamp ?? incoming.earliestTimestamp
  const latestTimestamp = entries[entries.length - 1]?.timestamp ?? incoming.latestTimestamp ?? base.latestTimestamp

  const cursor = parseCursorValue(base.cursor) <= parseCursorValue(incoming.cursor) ? base.cursor : incoming.cursor

  return {
    ...base,
    cursor,
    entries,
    entryCount: entries.length,
    earliestTimestamp,
    latestTimestamp,
    collapsible: entries.length >= threshold,
    collapseThreshold: threshold,
  }
}

function mergeEvents(existing: TimelineEvent[], incoming: TimelineEvent[]): TimelineEvent[] {
  const map = new Map<string, TimelineEvent>()
  for (const event of existing) {
    map.set(event.cursor, event)
  }
  for (const event of incoming) {
    const current = map.get(event.cursor)
    if (current && current.kind === 'steps' && event.kind === 'steps') {
      map.set(event.cursor, mergeClusters(current, event))
    } else {
      map.set(event.cursor, event)
    }
  }
  return sortEvents(Array.from(map.values()))
}

const TIMELINE_WINDOW_SIZE = 10

export type AgentChatState = {
  agentId: string | null
  events: TimelineEvent[]
  oldestCursor: string | null
  newestCursor: string | null
  hasMoreOlder: boolean
  hasMoreNewer: boolean
  hasUnseenActivity: boolean
  processingActive: boolean
  loading: boolean
  loadingOlder: boolean
  loadingNewer: boolean
  error: string | null
  autoScrollPinned: boolean
  pendingEvents: TimelineEvent[]
  initialize: (agentId: string) => Promise<void>
  refreshProcessing: () => Promise<void>
  loadOlder: () => Promise<void>
  loadNewer: () => Promise<void>
  jumpToLatest: () => Promise<void>
  sendMessage: (body: string) => Promise<void>
  receiveRealtimeEvent: (event: TimelineEvent) => void
  updateProcessing: (active: boolean) => void
  setAutoScrollPinned: (pinned: boolean) => void
}

export const useAgentChatStore = create<AgentChatState>((set, get) => ({
  agentId: null,
  events: [],
  oldestCursor: null,
  newestCursor: null,
  hasMoreOlder: false,
  hasMoreNewer: false,
  hasUnseenActivity: false,
  processingActive: false,
  loading: false,
  loadingOlder: false,
  loadingNewer: false,
  error: null,
  autoScrollPinned: true,
  pendingEvents: [],

  async initialize(agentId) {
    set({ loading: true, agentId, error: null, autoScrollPinned: true })

    try {
      const snapshot = await fetchAgentTimeline(agentId, { direction: 'initial', limit: TIMELINE_WINDOW_SIZE })
      const events = sortEvents(snapshot.events)
      const oldestCursor = events.length ? events[0].cursor : null
      const newestCursor = events.length ? events[events.length - 1].cursor : null

      set({
        events,
        oldestCursor,
        newestCursor,
        hasMoreOlder: snapshot.has_more_older,
        hasMoreNewer: snapshot.has_more_newer,
        processingActive: snapshot.processing_active,
        loading: false,
        autoScrollPinned: true,
        pendingEvents: [],
      })
    } catch (error) {
      console.error('Failed to initialize agent chat:', error)
      set({
        loading: false,
        error: error instanceof Error ? error.message : 'Failed to load timeline',
      })
    }
  },

  async refreshProcessing() {
    const agentId = get().agentId
    if (!agentId) return
    try {
      const { processing_active } = await fetchProcessingStatus(agentId)
      set({ processingActive: processing_active })
    } catch (error) {
      set({
        error: error instanceof Error ? error.message : 'Failed to refresh processing status',
      })
    }
  },

  async loadOlder() {
    const state = get()
    if (!state.agentId || state.loadingOlder || !state.hasMoreOlder) {
      return
    }
    set({ loadingOlder: true })
    try {
      const snapshot = await fetchAgentTimeline(state.agentId, {
        direction: 'older',
        cursor: state.oldestCursor ?? undefined,
        limit: TIMELINE_WINDOW_SIZE,
      })
      const incoming = sortEvents(snapshot.events)
      const merged = mergeEvents(state.events, incoming)
      const windowSize = Math.min(TIMELINE_WINDOW_SIZE, merged.length)
      const events = merged.slice(0, windowSize)
      const oldestCursor = events.length ? events[0].cursor : null
      const newestCursor = events.length ? events[events.length - 1].cursor : null
      const trimmedNewer = merged.length > events.length
      const hasMoreNewer = incoming.length === 0 ? state.hasMoreNewer : snapshot.has_more_newer || trimmedNewer
      set({
        events,
        oldestCursor,
        newestCursor,
        hasMoreOlder: snapshot.has_more_older,
        hasMoreNewer,
        loadingOlder: false,
      })
    } catch (error) {
      set({
        loadingOlder: false,
        error: error instanceof Error ? error.message : 'Failed to load older history',
      })
    }
  },

  async loadNewer() {
    const state = get()
    if (!state.agentId || state.loadingNewer || !state.hasMoreNewer) {
      return
    }
    set({ loadingNewer: true })
    try {
      const snapshot = await fetchAgentTimeline(state.agentId, {
        direction: 'newer',
        cursor: state.newestCursor ?? undefined,
        limit: TIMELINE_WINDOW_SIZE,
      })
      const incoming = sortEvents(snapshot.events)
      const merged = mergeEvents(state.events, incoming)
      const windowStart = Math.max(0, merged.length - TIMELINE_WINDOW_SIZE)
      const events = merged.slice(windowStart)
      const oldestCursor = events.length ? events[0].cursor : null
      const newestCursor = events.length ? events[events.length - 1].cursor : null
      const trimmedOlder = merged.length > events.length
      const hasMoreOlder = incoming.length === 0 ? state.hasMoreOlder : snapshot.has_more_older || trimmedOlder
      set({
        events,
        oldestCursor,
        newestCursor,
        hasMoreOlder,
        hasMoreNewer: snapshot.has_more_newer,
        processingActive: snapshot.processing_active,
        loadingNewer: false,
      })
    } catch (error) {
      set({
        loadingNewer: false,
        error: error instanceof Error ? error.message : 'Failed to load newer events',
      })
    }
  },

  async jumpToLatest() {
    const state = get()
    if (!state.agentId) {
      return
    }
    set({ loading: true })
    try {
      const snapshot = await fetchAgentTimeline(state.agentId, { direction: 'initial', limit: TIMELINE_WINDOW_SIZE })
      const events = sortEvents(snapshot.events)
      const oldestCursor = events.length ? events[0].cursor : null
      const newestCursor = events.length ? events[events.length - 1].cursor : null
      set({
        events,
        oldestCursor,
        newestCursor,
        hasMoreOlder: snapshot.has_more_older,
        hasMoreNewer: snapshot.has_more_newer,
        processingActive: snapshot.processing_active,
        hasUnseenActivity: false,
        loading: false,
        pendingEvents: [],
      })
    } catch (error) {
      set({
        loading: false,
        error: error instanceof Error ? error.message : 'Failed to jump to latest',
      })
    }
  },

  async sendMessage(body) {
    const state = get()
    if (!state.agentId) {
      throw new Error('Agent not initialized')
    }
    const trimmed = body.trim()
    if (!trimmed) {
      return
    }
    try {
      const event = await sendAgentMessage(state.agentId, trimmed)
      get().receiveRealtimeEvent(event)
    } catch (error) {
      set({
        error: error instanceof Error ? error.message : 'Failed to send message',
      })
      throw error
    }
  },

  receiveRealtimeEvent(event) {
    const state = get()
    if (!state.autoScrollPinned) {
      const pendingEvents = mergeEvents(state.pendingEvents, [event])
      set({
        pendingEvents,
        hasUnseenActivity: true,
      })
      return
    }
    let events: TimelineEvent[]
    if (event.kind === 'steps') {
      const last = state.events[state.events.length - 1]
      if (last && last.kind === 'steps') {
        const mergedLast = mergeClusters(last, event)
        events = sortEvents([...state.events.slice(0, -1), mergedLast])
      } else {
        events = mergeEvents(state.events, [event])
      }
    } else {
      events = mergeEvents(state.events, [event])
    }
    const newestCursor = events.length ? events[events.length - 1].cursor : null
    const oldestCursor = events.length ? events[0].cursor : null
    set({
      events,
      newestCursor,
      oldestCursor,
      pendingEvents: [],
    })
  },

  updateProcessing(active) {
    set((state) => ({
      processingActive: active,
      hasUnseenActivity: !state.autoScrollPinned && active ? true : state.hasUnseenActivity,
    }))
  },

  setAutoScrollPinned(pinned) {
    set((state) => {
      if (pinned && state.pendingEvents.length) {
        const merged = mergeEvents(state.events, state.pendingEvents)
        const newestCursor = merged.length ? merged[merged.length - 1].cursor : state.newestCursor
        const oldestCursor = merged.length ? merged[0].cursor : state.oldestCursor
        return {
          autoScrollPinned: true,
          hasUnseenActivity: false,
          events: merged,
          newestCursor,
          oldestCursor,
          pendingEvents: [],
        }
      }

      return {
        autoScrollPinned: pinned,
        hasUnseenActivity: pinned ? false : state.hasUnseenActivity,
      }
    })
  },
}))
