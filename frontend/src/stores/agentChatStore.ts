import { create } from 'zustand'

import type {
  AgentMessage,
  ProcessingSnapshot,
  ProcessingWebTask,
  StreamEventPayload,
  StreamState,
  TimelineEvent,
} from '../types/agentChat'
import type { InsightEvent } from '../types/insight'
import { INSIGHT_TIMING } from '../types/insight'
import { fetchAgentTimeline, sendAgentMessage, fetchProcessingStatus, fetchAgentInsights } from '../api/agentChat'
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

const OPTIMISTIC_MATCH_WINDOW_MS = 120_000

type MessageSignature = {
  text: string
  attachmentsCount: number
  timestampMs: number | null
}

function buildMessageSignature(message: AgentMessage): MessageSignature {
  const text = (message.bodyText || message.bodyHtml || '').trim()
  const attachmentsCount = message.attachments?.length ?? 0
  const timestampMs = message.timestamp ? Date.parse(message.timestamp) : null
  return {
    text,
    attachmentsCount,
    timestampMs: Number.isNaN(timestampMs ?? NaN) ? null : timestampMs,
  }
}

function isOptimisticMatch(event: TimelineEvent, signature: MessageSignature): boolean {
  if (event.kind !== 'message') {
    return false
  }
  if (event.message.status !== 'sending') {
    return false
  }
  const optimisticSignature = buildMessageSignature(event.message)
  if (!signature.text && signature.attachmentsCount === 0) {
    return false
  }
  if (optimisticSignature.text !== signature.text) {
    return false
  }
  if (optimisticSignature.attachmentsCount !== signature.attachmentsCount) {
    return false
  }
  if (signature.timestampMs === null || optimisticSignature.timestampMs === null) {
    return true
  }
  return Math.abs(signature.timestampMs - optimisticSignature.timestampMs) <= OPTIMISTIC_MATCH_WINDOW_MS
}

function removeOptimisticMatch(events: TimelineEvent[], signature: MessageSignature): { events: TimelineEvent[]; removed: boolean } {
  for (let i = events.length - 1; i >= 0; i -= 1) {
    if (isOptimisticMatch(events[i], signature)) {
      return { events: [...events.slice(0, i), ...events.slice(i + 1)], removed: true }
    }
  }
  return { events, removed: false }
}

function removeOptimisticMatches(events: TimelineEvent[], incoming: TimelineEvent[]): TimelineEvent[] {
  let nextEvents = events
  for (const event of incoming) {
    if (event.kind !== 'message' || event.message.isOutbound || event.message.status === 'sending') {
      continue
    }
    const signature = buildMessageSignature(event.message)
    const updated = removeOptimisticMatch(nextEvents, signature)
    if (updated.removed) {
      nextEvents = updated.events
    }
  }
  return nextEvents
}

function updateOptimisticStatus(
  events: TimelineEvent[],
  clientId: string,
  status: 'sending' | 'failed',
  error?: string,
): { events: TimelineEvent[]; updated: boolean } {
  const index = events.findIndex(
    (event) => event.kind === 'message' && event.message.clientId === clientId,
  )
  if (index < 0) {
    return { events, updated: false }
  }
  const target = events[index]
  if (target.kind !== 'message') {
    return { events, updated: false }
  }
  const next = [...events]
  next[index] = {
    ...target,
    message: {
      ...target.message,
      status,
      error: error ?? target.message.error ?? null,
    },
  }
  return { events: next, updated: true }
}

function hasAgentResponse(events: TimelineEvent[]): boolean {
  return events.some((event) => {
    if (event.kind === 'thinking') {
      return true
    }
    return event.kind === 'message' && Boolean(event.message.isOutbound)
  })
}

function buildOptimisticMessageEvent(body: string, attachments: File[]): { event: TimelineEvent; clientId: string } {
  const now = Date.now()
  const clientId = typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? `local-${crypto.randomUUID()}`
    : `local-${now}-${Math.random().toString(16).slice(2, 10)}`
  const cursor = `${now * 1000}:message:${clientId}`
  const attachmentPayload = attachments.map((file, index) => ({
    id: `${clientId}-file-${index}`,
    filename: file.name,
    url: '',
    downloadUrl: null,
    filespacePath: null,
    filespaceNodeId: null,
    fileSizeLabel: null,
  }))

  return {
    clientId,
    event: {
      kind: 'message',
      cursor,
      message: {
        id: clientId,
        cursor,
        bodyText: body,
        isOutbound: false,
        channel: 'web',
        attachments: attachmentPayload,
        timestamp: new Date(now).toISOString(),
        relativeTimestamp: null,
        clientId,
        status: 'sending',
      },
    },
  }
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
  awaitingResponse: boolean
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
  agentName: string | null
  agentAvatarUrl: string | null
  // Insight state
  insights: InsightEvent[]
  currentInsightIndex: number
  insightsFetchedAt: number | null
  insightRotationTimer: ReturnType<typeof setTimeout> | null
  insightProcessingStartedAt: number | null
  dismissedInsightIds: Set<string>
  initialize: (
    agentId: string,
    options?: {
      agentColorHex?: string | null
      agentName?: string | null
      agentAvatarUrl?: string | null
    },
  ) => Promise<void>
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
  // Insight actions
  fetchInsights: () => Promise<void>
  startInsightRotation: () => void
  stopInsightRotation: () => void
  dismissInsight: (insightId: string) => void
  getCurrentInsight: () => InsightEvent | null
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
  awaitingResponse: false,
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
  agentName: null,
  agentAvatarUrl: null,
  // Insight state
  insights: [],
  currentInsightIndex: 0,
  insightsFetchedAt: null,
  insightRotationTimer: null,
  insightProcessingStartedAt: null,
  dismissedInsightIds: new Set(),

  async initialize(agentId, options) {
    const providedColor = options?.agentColorHex ? normalizeHexColor(options.agentColorHex) : null
    const providedName = options?.agentName ?? null
    const providedAvatarUrl = options?.agentAvatarUrl ?? null
    const reuseExisting = get().agentId === agentId
    const fallbackColor = reuseExisting ? get().agentColorHex : DEFAULT_CHAT_COLOR_HEX
    const fallbackName = reuseExisting ? get().agentName : null
    const fallbackAvatarUrl = reuseExisting ? get().agentAvatarUrl : null
    set({
      loading: true,
      agentId,
      events: [],
      oldestCursor: null,
      newestCursor: null,
      hasMoreOlder: false,
      hasMoreNewer: false,
      hasUnseenActivity: false,
      processingActive: false,
      processingWebTasks: [],
      loadingOlder: false,
      loadingNewer: false,
      refreshingLatest: false,
      pendingEvents: [],
      error: null,
      autoScrollPinned: true,
      autoScrollPinSuppressedUntil: null,
      streaming: null,
      streamingLastUpdatedAt: null,
      streamingClearOnDone: false,
      streamingThinkingCollapsed: false,
      thinkingCollapsedByCursor: {},
      awaitingResponse: false,
      agentColorHex: providedColor ?? fallbackColor ?? DEFAULT_CHAT_COLOR_HEX,
      agentName: providedName ?? fallbackName ?? null,
      agentAvatarUrl: providedAvatarUrl ?? fallbackAvatarUrl ?? null,
    })

    const currentAgentId = agentId
    try {
      const snapshot = await fetchAgentTimeline(agentId, { direction: 'initial', limit: TIMELINE_WINDOW_SIZE })
      if (get().agentId !== currentAgentId) {
        return
      }
      const events = prepareTimelineEvents(snapshot.events)
      const oldestCursor = events.length ? events[0].cursor : null
      const newestCursor = events.length ? events[events.length - 1].cursor : null
      const processingSnapshot = normalizeProcessingUpdate(
        snapshot.processing_snapshot ?? { active: snapshot.processing_active, webTasks: [] },
      )
      const agentColorHex = snapshot.agent_color_hex
        ? normalizeHexColor(snapshot.agent_color_hex)
        : providedColor ?? get().agentColorHex ?? DEFAULT_CHAT_COLOR_HEX
      const agentName = snapshot.agent_name ?? providedName ?? get().agentName ?? null
      const agentAvatarUrl = snapshot.agent_avatar_url ?? providedAvatarUrl ?? get().agentAvatarUrl ?? null

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
        agentName,
        agentAvatarUrl,
        awaitingResponse: false,
      })
    } catch (error) {
      if (get().agentId !== currentAgentId) {
        return
      }
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
      set((state) => ({
        processingActive: snapshot.active,
        processingWebTasks: snapshot.webTasks,
        awaitingResponse: snapshot.active ? false : state.awaitingResponse,
      }))
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
      const hasAgentActivity = hasAgentResponse(incoming)
      const processingSnapshot = normalizeProcessingUpdate(
        snapshot.processing_snapshot ?? { active: snapshot.processing_active, webTasks: [] },
      )
      const shouldClearAwaiting = hasAgentActivity || processingSnapshot.active
      set((current) => {
        const agentColorHex = snapshot.agent_color_hex
          ? normalizeHexColor(snapshot.agent_color_hex)
          : current.agentColorHex
        const hasStreamingContent = Boolean(current.streaming?.content?.trim())
        const allowThinkingClear = Boolean(current.streaming) && !hasStreamingContent
        // Clear streaming immediately when thinking event arrives to avoid 2x thinking bubbles
        const shouldClearThinkingStream = hasThinkingEvent && allowThinkingClear
        const nextStreaming = shouldClearThinkingStream ? null : current.streaming
        const nextStreamingClearOnDone =
          shouldClearThinkingStream
            ? false
            : allowThinkingClear
              ? current.streamingClearOnDone
              : false
        const baseEvents = removeOptimisticMatches(current.events, incoming)
        const basePendingEvents = removeOptimisticMatches(current.pendingEvents, incoming)
        if (!current.autoScrollPinned) {
          const pendingEvents = mergeTimelineEvents(basePendingEvents, incoming)
          return {
            events: baseEvents,
            processingActive: processingSnapshot.active,
            processingWebTasks: processingSnapshot.webTasks,
            pendingEvents,
            hasUnseenActivity: incoming.length ? true : current.hasUnseenActivity,
            streaming: nextStreaming,
            streamingClearOnDone: nextStreamingClearOnDone,
            awaitingResponse: shouldClearAwaiting ? false : current.awaitingResponse,
            refreshingLatest: false,
            agentColorHex,
          }
        }
        const merged = mergeTimelineEvents(baseEvents, incoming)
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
          awaitingResponse: shouldClearAwaiting ? false : current.awaitingResponse,
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
      const baseEvents = removeOptimisticMatches(state.events, incoming)
      const merged = mergeTimelineEvents(baseEvents, incoming)
      const windowStart = Math.max(0, merged.length - TIMELINE_WINDOW_SIZE)
      const events = merged.slice(windowStart)
      const oldestCursor = events.length ? events[0].cursor : null
      const newestCursor = events.length ? events[events.length - 1].cursor : null
      const trimmedOlder = merged.length > events.length
      const hasMoreOlder = incoming.length === 0 ? state.hasMoreOlder : snapshot.has_more_older || trimmedOlder
      const processingSnapshot = normalizeProcessingUpdate(
        snapshot.processing_snapshot ?? { active: snapshot.processing_active, webTasks: [] },
      )
      const hasAgentActivity = hasAgentResponse(incoming)
      const shouldClearAwaiting = hasAgentActivity || processingSnapshot.active
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
        awaitingResponse: shouldClearAwaiting ? false : current.awaitingResponse,
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
        awaitingResponse: false,
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
    const { event: optimisticEvent, clientId } = buildOptimisticMessageEvent(trimmed, attachments)
    set({ awaitingResponse: true })
    get().receiveRealtimeEvent(optimisticEvent)
    try {
      const event = await sendAgentMessage(state.agentId, trimmed, attachments)
      get().receiveRealtimeEvent(event)
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Failed to send message'
      set((current) => {
        const updatedEvents = updateOptimisticStatus(current.events, clientId, 'failed', message)
        const updatedPending = updateOptimisticStatus(current.pendingEvents, clientId, 'failed', message)
        return {
          events: updatedEvents.events,
          pendingEvents: updatedPending.events,
          awaitingResponse: false,
          error: message,
        }
      })
      throw error
    }
  },

  receiveRealtimeEvent(event) {
    const normalized = normalizeTimelineEvent(event)
    set((state) => {
      let events = state.events
      let pendingEvents = state.pendingEvents
      let awaitingResponse = state.awaitingResponse

      if (normalized.kind === 'message' && !normalized.message.isOutbound && normalized.message.status !== 'sending') {
        const signature = buildMessageSignature(normalized.message)
        const updatedEvents = removeOptimisticMatch(events, signature)
        if (updatedEvents.removed) {
          events = updatedEvents.events
        }
        const updatedPending = removeOptimisticMatch(pendingEvents, signature)
        if (updatedPending.removed) {
          pendingEvents = updatedPending.events
        }
      }

      const shouldClearStream = normalized.kind === 'message' && normalized.message.isOutbound
      const hasStreamingContent = Boolean(state.streaming?.content?.trim())
      const allowThinkingClear = Boolean(state.streaming) && !hasStreamingContent
      // Clear streaming immediately when thinking event arrives to avoid 2x thinking bubbles
      const shouldClearThinkingStream = normalized.kind === 'thinking' && allowThinkingClear
      const nextStreaming = shouldClearStream || shouldClearThinkingStream ? null : state.streaming
      const nextStreamingClearOnDone =
        shouldClearStream || shouldClearThinkingStream
          ? false
          : allowThinkingClear
            ? state.streamingClearOnDone
            : false

      if (normalized.kind === 'thinking' || normalized.kind === 'steps' || shouldClearStream) {
        awaitingResponse = false
      }

      if (!state.autoScrollPinned) {
        const mergedPending = mergeTimelineEvents(pendingEvents, [normalized])
        return {
          events,
          pendingEvents: mergedPending,
          hasUnseenActivity: true,
          streaming: nextStreaming,
          streamingClearOnDone: nextStreamingClearOnDone,
          awaitingResponse,
        }
      }

      const merged = mergeTimelineEvents(events, [normalized])
      const newestCursor = merged.length ? merged[merged.length - 1].cursor : null
      const oldestCursor = merged.length ? merged[0].cursor : null
      return {
        events: merged,
        newestCursor,
        oldestCursor,
        pendingEvents: [],
        streaming: nextStreaming,
        streamingClearOnDone: nextStreamingClearOnDone,
        awaitingResponse,
      }
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
      const awaitingResponse = false
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
        return {
          streaming: null,
          hasUnseenActivity,
          streamingLastUpdatedAt: now,
          streamingClearOnDone: false,
          awaitingResponse,
        }
      }

      const hasStreamingContent = Boolean(next.content.trim())

      if (isDone && next.reasoning && !hasStreamingContent) {
        if (state.streamingClearOnDone) {
          return {
            streaming: null,
            hasUnseenActivity,
            streamingClearOnDone: false,
            streamingLastUpdatedAt: now,
            awaitingResponse,
          }
        }
        shouldRefreshLatest = true
        return {
          streaming: next,
          hasUnseenActivity,
          streamingThinkingCollapsed: true,
          streamingClearOnDone: false,
          streamingLastUpdatedAt: now,
          awaitingResponse,
        }
      }

      if (isStart) {
        return {
          streaming: next,
          hasUnseenActivity,
          // Start collapsed - shows compact preview with a few lines scrolling by
          streamingThinkingCollapsed: true,
          streamingClearOnDone: false,
          streamingLastUpdatedAt: now,
          awaitingResponse,
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
        awaitingResponse,
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
      awaitingResponse: snapshot.active ? false : state.awaitingResponse,
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

  // Insight actions
  async fetchInsights() {
    const agentId = get().agentId
    if (!agentId) return

    // Check if insights are still fresh (fetched within 5 minutes)
    const now = Date.now()
    const fetchedAt = get().insightsFetchedAt
    if (fetchedAt && now - fetchedAt < 5 * 60 * 1000) {
      return
    }

    try {
      const response = await fetchAgentInsights(agentId)
      set({
        insights: response.insights,
        insightsFetchedAt: now,
        currentInsightIndex: 0,
      })
    } catch (error) {
      console.error('Failed to fetch insights:', error)
    }
  },

  startInsightRotation() {
    const state = get()
    // Clear any existing timer
    if (state.insightRotationTimer) {
      clearTimeout(state.insightRotationTimer)
    }

    // Record when processing started for minimum display logic
    set({ insightProcessingStartedAt: Date.now() })

    // Fetch insights if not already fetched or stale
    void get().fetchInsights()

    // Start rotation timer
    const rotate = () => {
      const current = get()
      const availableInsights = current.insights.filter(
        (insight) => !current.dismissedInsightIds.has(insight.insightId)
      )

      if (availableInsights.length <= 1) {
        return // No rotation needed
      }

      // Move to next insight
      const nextIndex = (current.currentInsightIndex + 1) % availableInsights.length
      set({ currentInsightIndex: nextIndex })

      // Schedule next rotation if still processing
      if (current.processingActive || current.awaitingResponse) {
        const timer = setTimeout(rotate, INSIGHT_TIMING.rotationIntervalMs)
        set({ insightRotationTimer: timer })
      }
    }

    // Start first rotation after initial delay
    const timer = setTimeout(rotate, INSIGHT_TIMING.rotationIntervalMs)
    set({ insightRotationTimer: timer })
  },

  stopInsightRotation() {
    const timer = get().insightRotationTimer
    if (timer) {
      clearTimeout(timer)
      set({ insightRotationTimer: null })
    }
  },

  dismissInsight(insightId) {
    set((state) => {
      const newDismissed = new Set(state.dismissedInsightIds)
      newDismissed.add(insightId)

      // If we dismissed the current insight, move to next
      const availableInsights = state.insights.filter(
        (insight) => !newDismissed.has(insight.insightId)
      )

      let nextIndex = state.currentInsightIndex
      if (availableInsights.length > 0) {
        nextIndex = nextIndex % availableInsights.length
      }

      return {
        dismissedInsightIds: newDismissed,
        currentInsightIndex: nextIndex,
      }
    })
  },

  getCurrentInsight() {
    const state = get()

    // Don't show insights if processing hasn't been active long enough
    const processingStartedAt = state.insightProcessingStartedAt
    if (processingStartedAt && Date.now() - processingStartedAt < INSIGHT_TIMING.showAfterMs) {
      return null
    }

    // Filter out dismissed insights
    const availableInsights = state.insights.filter(
      (insight) => !state.dismissedInsightIds.has(insight.insightId)
    )

    if (availableInsights.length === 0) {
      return null
    }

    const index = state.currentInsightIndex % availableInsights.length
    return availableInsights[index] ?? null
  },
}))
