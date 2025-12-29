import { create } from 'zustand'

import type {
  ProcessingSnapshot,
  ProcessingWebTask,
  StreamEventPayload,
  StreamState,
  TimelineEvent,
} from '../types/agentChat'
import { fetchAgentTimeline, sendAgentMessage, fetchProcessingStatus } from '../api/agentChat'
import { normalizeHexColor, DEFAULT_CHAT_COLOR_HEX } from '../util/color'
import { mergeTimelineEvents, normalizeTimelineEvent, prepareTimelineEvents } from './agentChatTimeline'

const TIMELINE_WINDOW_SIZE = 100
let refreshLatestInFlight = false

const EMPTY_PROCESSING_SNAPSHOT: ProcessingSnapshot = { active: false, webTasks: [] }

type ProcessingUpdateInput = boolean | Partial<ProcessingSnapshot> | null | undefined

function coerceProcessingSnapshot(snapshot: Partial<ProcessingSnapshot> | null | undefined): ProcessingSnapshot {
  if (!snapshot) {
    return EMPTY_PROCESSING_SNAPSHOT
  }

  const webTasks: ProcessingWebTask[] = Array.isArray(snapshot.webTasks)
    ? snapshot.webTasks
        .filter((task): task is ProcessingWebTask => Boolean(task) && typeof task.id === 'string')
        .map((task) => ({
          id: task.id,
          status: task.status,
          statusLabel: task.statusLabel,
          prompt: typeof task.prompt === 'string' ? task.prompt : undefined,
          promptPreview: task.promptPreview,
          startedAt: task.startedAt ?? null,
          updatedAt: task.updatedAt ?? null,
          elapsedSeconds: task.elapsedSeconds ?? null,
        }))
    : []

  return {
    active: Boolean(snapshot.active) || webTasks.length > 0,
    webTasks,
  }
}

function normalizeProcessingUpdate(input: ProcessingUpdateInput): ProcessingSnapshot {
  if (typeof input === 'boolean') {
    return { active: input, webTasks: [] }
  }
  return coerceProcessingSnapshot(input)
}

export type AgentChatState = {
  agentId: string | null
  events: TimelineEvent[]
  streaming: StreamState | null
  streamingLastUpdatedAt: number | null
  streamingClearOnDone: boolean
  streamingThinkingCollapsed: boolean
  thinkingCollapsedByCursor: Record<string, boolean>
  oldestCursor: string | null
  newestCursor: string | null
  hasMoreOlder: boolean
  hasMoreNewer: boolean
  hasUnseenActivity: boolean
  processingActive: boolean
  processingWebTasks: ProcessingWebTask[]
  loading: boolean
  loadingOlder: boolean
  loadingNewer: boolean
  refreshingLatest: boolean
  error: string | null
  autoScrollPinned: boolean
  autoScrollPinSuppressedUntil: number | null
  pendingEvents: TimelineEvent[]
  agentColorHex: string | null
  initialize: (agentId: string, options?: { agentColorHex?: string | null }) => Promise<void>
  refreshProcessing: () => Promise<void>
  refreshLatest: () => Promise<void>
  loadOlder: () => Promise<void>
  loadNewer: () => Promise<void>
  jumpToLatest: () => Promise<void>
  sendMessage: (body: string, attachments?: File[]) => Promise<void>
  receiveRealtimeEvent: (event: TimelineEvent) => void
  receiveStreamEvent: (payload: StreamEventPayload) => void
  finalizeStreaming: () => void
  updateProcessing: (snapshot: ProcessingUpdateInput) => void
  setAutoScrollPinned: (pinned: boolean) => void
  suppressAutoScrollPin: (durationMs?: number) => void
  toggleThinkingCollapsed: (cursor: string) => void
  setStreamingThinkingCollapsed: (collapsed: boolean) => void
}

export const useAgentChatStore = create<AgentChatState>((set, get) => ({
  agentId: null,
  events: [],
  streaming: null,
  streamingLastUpdatedAt: null,
  streamingClearOnDone: false,
  streamingThinkingCollapsed: false,
  thinkingCollapsedByCursor: {},
  oldestCursor: null,
  newestCursor: null,
  hasMoreOlder: false,
  hasMoreNewer: false,
  hasUnseenActivity: false,
  processingActive: false,
  processingWebTasks: [],
  loading: false,
  loadingOlder: false,
  loadingNewer: false,
  refreshingLatest: false,
  error: null,
  autoScrollPinned: true,
  autoScrollPinSuppressedUntil: null,
  pendingEvents: [],
  agentColorHex: null,

  async initialize(agentId, options) {
    const providedColor = options?.agentColorHex ? normalizeHexColor(options.agentColorHex) : null
    set({
      loading: true,
      agentId,
      error: null,
      autoScrollPinned: true,
      autoScrollPinSuppressedUntil: null,
      streaming: null,
      streamingLastUpdatedAt: null,
      streamingClearOnDone: false,
      streamingThinkingCollapsed: false,
      thinkingCollapsedByCursor: {},
      agentColorHex: providedColor ?? get().agentColorHex ?? DEFAULT_CHAT_COLOR_HEX,
    })

    try {
      const snapshot = await fetchAgentTimeline(agentId, { direction: 'initial', limit: TIMELINE_WINDOW_SIZE })
      const events = prepareTimelineEvents(snapshot.events)
      const oldestCursor = events.length ? events[0].cursor : null
      const newestCursor = events.length ? events[events.length - 1].cursor : null
      const processingSnapshot = normalizeProcessingUpdate(
        snapshot.processing_snapshot ?? { active: snapshot.processing_active, webTasks: [] },
      )
      const agentColorHex = snapshot.agent_color_hex
        ? normalizeHexColor(snapshot.agent_color_hex)
        : providedColor ?? get().agentColorHex ?? DEFAULT_CHAT_COLOR_HEX

      set({
        events,
        oldestCursor,
        newestCursor,
        hasMoreOlder: snapshot.has_more_older,
        hasMoreNewer: snapshot.has_more_newer,
        processingActive: processingSnapshot.active,
        processingWebTasks: processingSnapshot.webTasks,
        loading: false,
        autoScrollPinned: true,
        autoScrollPinSuppressedUntil: null,
        pendingEvents: [],
        agentColorHex,
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
      const { processing_active, processing_snapshot } = await fetchProcessingStatus(agentId)
      const snapshot = normalizeProcessingUpdate(processing_snapshot ?? { active: processing_active, webTasks: [] })
      set({ processingActive: snapshot.active, processingWebTasks: snapshot.webTasks })
    } catch (error) {
      set({
        error: error instanceof Error ? error.message : 'Failed to refresh processing status',
      })
    }
  },

  async refreshLatest() {
    const state = get()
    if (!state.agentId || refreshLatestInFlight || state.loading || !state.newestCursor) {
      return
    }
    refreshLatestInFlight = true
    set({ refreshingLatest: true })
    try {
      const snapshot = await fetchAgentTimeline(state.agentId, {
        direction: 'newer',
        cursor: state.newestCursor ?? undefined,
        limit: TIMELINE_WINDOW_SIZE,
      })
      const incoming = prepareTimelineEvents(snapshot.events)
      const hasThinkingEvent = incoming.some((event) => event.kind === 'thinking')
      const processingSnapshot = normalizeProcessingUpdate(
        snapshot.processing_snapshot ?? { active: snapshot.processing_active, webTasks: [] },
      )
      set((current) => {
        const agentColorHex = snapshot.agent_color_hex
          ? normalizeHexColor(snapshot.agent_color_hex)
          : current.agentColorHex
        const hasStreamingContent = Boolean(current.streaming?.content?.trim())
        const allowThinkingClear = Boolean(current.streaming) && !hasStreamingContent
        const shouldClearThinkingStream = hasThinkingEvent && current.streaming?.done && allowThinkingClear
        const shouldFlagThinkingClear = hasThinkingEvent && current.streaming && !current.streaming.done && allowThinkingClear
        const nextStreaming = shouldClearThinkingStream ? null : current.streaming
        const nextStreamingClearOnDone =
          shouldClearThinkingStream
            ? false
            : shouldFlagThinkingClear
              ? true
              : allowThinkingClear
                ? current.streamingClearOnDone
                : false
        if (!current.autoScrollPinned) {
          const pendingEvents = mergeTimelineEvents(current.pendingEvents, incoming)
          return {
            processingActive: processingSnapshot.active,
            processingWebTasks: processingSnapshot.webTasks,
            pendingEvents,
            hasUnseenActivity: incoming.length ? true : current.hasUnseenActivity,
            streaming: nextStreaming,
            streamingClearOnDone: nextStreamingClearOnDone,
            refreshingLatest: false,
            agentColorHex,
          }
        }
        const merged = mergeTimelineEvents(current.events, incoming)
        const windowStart = Math.max(0, merged.length - TIMELINE_WINDOW_SIZE)
        const events = merged.slice(windowStart)
        const oldestCursor = events.length ? events[0].cursor : current.oldestCursor
        const newestCursor = events.length ? events[events.length - 1].cursor : current.newestCursor
        const trimmedOlder = merged.length > events.length
        const hasMoreOlder = incoming.length === 0 ? current.hasMoreOlder : snapshot.has_more_older || trimmedOlder
        return {
          events,
          oldestCursor,
          newestCursor,
          hasMoreOlder,
          hasMoreNewer: snapshot.has_more_newer,
          processingActive: processingSnapshot.active,
          processingWebTasks: processingSnapshot.webTasks,
          pendingEvents: [],
          streaming: nextStreaming,
          streamingClearOnDone: nextStreamingClearOnDone,
          refreshingLatest: false,
          agentColorHex,
        }
      })
    } catch (error) {
      set({
        refreshingLatest: false,
        error: error instanceof Error ? error.message : 'Failed to refresh timeline',
      })
    } finally {
      refreshLatestInFlight = false
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
      const incoming = prepareTimelineEvents(snapshot.events)
      const merged = mergeTimelineEvents(state.events, incoming)
      const windowSize = Math.min(TIMELINE_WINDOW_SIZE, merged.length)
      const events = merged.slice(0, windowSize)
      const oldestCursor = events.length ? events[0].cursor : null
      const newestCursor = events.length ? events[events.length - 1].cursor : null
      const trimmedNewer = merged.length > events.length
      const hasMoreNewer = incoming.length === 0 ? state.hasMoreNewer : snapshot.has_more_newer || trimmedNewer
      set((current) => ({
        events,
        oldestCursor,
        newestCursor,
        hasMoreOlder: snapshot.has_more_older,
        hasMoreNewer,
        loadingOlder: false,
        agentColorHex: snapshot.agent_color_hex ? normalizeHexColor(snapshot.agent_color_hex) : current.agentColorHex,
      }))
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
      const incoming = prepareTimelineEvents(snapshot.events)
      const merged = mergeTimelineEvents(state.events, incoming)
      const windowStart = Math.max(0, merged.length - TIMELINE_WINDOW_SIZE)
      const events = merged.slice(windowStart)
      const oldestCursor = events.length ? events[0].cursor : null
      const newestCursor = events.length ? events[events.length - 1].cursor : null
      const trimmedOlder = merged.length > events.length
      const hasMoreOlder = incoming.length === 0 ? state.hasMoreOlder : snapshot.has_more_older || trimmedOlder
      const processingSnapshot = normalizeProcessingUpdate(
        snapshot.processing_snapshot ?? { active: snapshot.processing_active, webTasks: [] },
      )
      set((current) => ({
        events,
        oldestCursor,
        newestCursor,
        hasMoreOlder,
        hasMoreNewer: snapshot.has_more_newer,
        processingActive: processingSnapshot.active,
        processingWebTasks: processingSnapshot.webTasks,
        loadingNewer: false,
        agentColorHex: snapshot.agent_color_hex ? normalizeHexColor(snapshot.agent_color_hex) : current.agentColorHex,
      }))
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
      const events = prepareTimelineEvents(snapshot.events)
      const oldestCursor = events.length ? events[0].cursor : null
      const newestCursor = events.length ? events[events.length - 1].cursor : null
      const processingSnapshot = normalizeProcessingUpdate(
        snapshot.processing_snapshot ?? { active: snapshot.processing_active, webTasks: [] },
      )
      set((current) => ({
        events,
        oldestCursor,
        newestCursor,
        hasMoreOlder: snapshot.has_more_older,
        hasMoreNewer: snapshot.has_more_newer,
        processingActive: processingSnapshot.active,
        processingWebTasks: processingSnapshot.webTasks,
        hasUnseenActivity: false,
        loading: false,
        pendingEvents: [],
        agentColorHex: snapshot.agent_color_hex ? normalizeHexColor(snapshot.agent_color_hex) : current.agentColorHex,
      }))
    } catch (error) {
      set({
        loading: false,
        error: error instanceof Error ? error.message : 'Failed to jump to latest',
      })
    }
  },

  async sendMessage(body, attachments = []) {
    const state = get()
    if (!state.agentId) {
      throw new Error('Agent not initialized')
    }
    const trimmed = body.trim()
    if (!trimmed && attachments.length === 0) {
      return
    }
    try {
      const event = await sendAgentMessage(state.agentId, trimmed, attachments)
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
    const normalized = normalizeTimelineEvent(event)
    const shouldClearStream = normalized.kind === 'message' && normalized.message.isOutbound
    const hasStreamingContent = Boolean(state.streaming?.content?.trim())
    const allowThinkingClear = Boolean(state.streaming) && !hasStreamingContent
    const shouldClearThinkingStream = normalized.kind === 'thinking' && state.streaming?.done && allowThinkingClear
    const shouldFlagThinkingClear = normalized.kind === 'thinking' && state.streaming && !state.streaming.done && allowThinkingClear
    const nextStreaming = shouldClearStream || shouldClearThinkingStream ? null : state.streaming
    const nextStreamingClearOnDone =
      shouldClearStream || shouldClearThinkingStream
        ? false
        : shouldFlagThinkingClear
          ? true
          : allowThinkingClear
            ? state.streamingClearOnDone
            : false
    if (!state.autoScrollPinned) {
      const pendingEvents = mergeTimelineEvents(state.pendingEvents, [normalized])
      set({
        pendingEvents,
        hasUnseenActivity: true,
        streaming: nextStreaming,
        streamingClearOnDone: nextStreamingClearOnDone,
      })
      return
    }
    const events = mergeTimelineEvents(state.events, [normalized])
    const newestCursor = events.length ? events[events.length - 1].cursor : null
    const oldestCursor = events.length ? events[0].cursor : null
    set({
      events,
      newestCursor,
      oldestCursor,
      pendingEvents: [],
      streaming: nextStreaming,
      streamingClearOnDone: nextStreamingClearOnDone,
    })
  },

  receiveStreamEvent(payload) {
    if (!payload?.stream_id) {
      return
    }
    const isStart = payload.status === 'start'
    const isDone = payload.status === 'done'
    const isDelta = payload.status === 'delta'
    const now = Date.now()
    let shouldRefreshLatest = false

    set((state) => {
      const base =
        isStart || !state.streaming || state.streaming.streamId !== payload.stream_id
          ? { streamId: payload.stream_id, reasoning: '', content: '', done: false }
          : state.streaming

      const reasoningDelta = payload.reasoning_delta ?? ''
      const contentDelta = payload.content_delta ?? ''

      const next: StreamState = {
        streamId: base.streamId,
        reasoning: reasoningDelta ? `${base.reasoning}${reasoningDelta}` : base.reasoning,
        content: contentDelta ? `${base.content}${contentDelta}` : base.content,
        done: isDone ? true : isDelta ? false : base.done,
      }

      const hasUnseenActivity = !state.autoScrollPinned
        ? true
        : state.hasUnseenActivity

      if (isDone && !next.reasoning && !next.content) {
        return { streaming: null, hasUnseenActivity, streamingLastUpdatedAt: now, streamingClearOnDone: false }
      }

      const hasStreamingContent = Boolean(next.content.trim())

      if (isDone && next.reasoning && !hasStreamingContent) {
        if (state.streamingClearOnDone) {
          return {
            streaming: null,
            hasUnseenActivity,
            streamingClearOnDone: false,
            streamingLastUpdatedAt: now,
          }
        }
        shouldRefreshLatest = true
        return {
          streaming: next,
          hasUnseenActivity,
          streamingThinkingCollapsed: true,
          streamingClearOnDone: false,
          streamingLastUpdatedAt: now,
        }
      }

      if (isStart) {
        return {
          streaming: next,
          hasUnseenActivity,
          streamingThinkingCollapsed: false,
          streamingClearOnDone: false,
          streamingLastUpdatedAt: now,
        }
      }

      const nextStreamingClearOnDone = hasStreamingContent ? false : state.streamingClearOnDone
      const nextStreamingThinkingCollapsed =
        isDone && next.reasoning ? true : state.streamingThinkingCollapsed
      return {
        streaming: next,
        hasUnseenActivity,
        streamingClearOnDone: nextStreamingClearOnDone,
        streamingThinkingCollapsed: nextStreamingThinkingCollapsed,
        streamingLastUpdatedAt: now,
      }
    })

    if (shouldRefreshLatest) {
      void get().refreshLatest()
    }
  },

  finalizeStreaming() {
    const now = Date.now()
    set((state) => {
      if (!state.streaming || state.streaming.done) {
        return state
      }
      const hasReasoning = Boolean(state.streaming.reasoning?.trim())
      return {
        streaming: { ...state.streaming, done: true },
        streamingThinkingCollapsed: hasReasoning ? true : state.streamingThinkingCollapsed,
        streamingClearOnDone: false,
        streamingLastUpdatedAt: now,
      }
    })
  },

  updateProcessing(snapshotInput) {
    const snapshot = normalizeProcessingUpdate(snapshotInput)
    set((state) => ({
      processingActive: snapshot.active,
      processingWebTasks: snapshot.webTasks,
      hasUnseenActivity: !state.autoScrollPinned && snapshot.active ? true : state.hasUnseenActivity,
    }))
  },

  setAutoScrollPinned(pinned) {
    set((state) => {
      if (pinned && state.pendingEvents.length) {
        const merged = mergeTimelineEvents(state.events, state.pendingEvents)
        const newestCursor = merged.length ? merged[merged.length - 1].cursor : state.newestCursor
        const oldestCursor = merged.length ? merged[0].cursor : state.oldestCursor
        return {
          autoScrollPinned: true,
          hasUnseenActivity: false,
          events: merged,
          newestCursor,
          oldestCursor,
          pendingEvents: [],
          autoScrollPinSuppressedUntil: null,
        }
      }

      return {
        autoScrollPinned: pinned,
        hasUnseenActivity: pinned ? false : state.hasUnseenActivity,
        autoScrollPinSuppressedUntil: pinned ? null : state.autoScrollPinSuppressedUntil,
      }
    })
  },
  suppressAutoScrollPin(durationMs = 1000) {
    const now = Date.now()
    const until = now + Math.max(0, durationMs)
    set((state) => {
      if (state.autoScrollPinSuppressedUntil && state.autoScrollPinSuppressedUntil >= until) {
        return state
      }
      return { autoScrollPinSuppressedUntil: until }
    })
  },

  toggleThinkingCollapsed(cursor) {
    set((state) => {
      const current = state.thinkingCollapsedByCursor[cursor]
      const nextCollapsed = !(current ?? true)
      return {
        thinkingCollapsedByCursor: {
          ...state.thinkingCollapsedByCursor,
          [cursor]: nextCollapsed,
        },
      }
    })
  },

  setStreamingThinkingCollapsed(collapsed) {
    set({ streamingThinkingCollapsed: collapsed })
  },
}))
