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

// Cached state for preserving agent data when switching between agents
type CachedAgentState = {
  events: TimelineEvent[]
  oldestCursor: string | null
  newestCursor: string | null
  hasMoreOlder: boolean
  hasMoreNewer: boolean
  processingActive: boolean
  processingStartedAt: number | null
  awaitingResponse: boolean
  processingWebTasks: ProcessingWebTask[]
  agentColorHex: string | null
  agentName: string | null
  agentAvatarUrl: string | null
  streaming: StreamState | null
  streamingThinkingCollapsed: boolean
}

export type AgentChatState = {
  agentId: string | null
  events: TimelineEvent[]
  agentStateCache: Record<string, CachedAgentState>
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
  processingStartedAt: number | null
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
  insightsPaused: boolean
  initialize: (
    agentId: string,
    options?: {
      agentColorHex?: string | null
      agentName?: string | null
      agentAvatarUrl?: string | null
      syncContext?: boolean
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
  setInsightsPaused: (paused: boolean) => void
  setCurrentInsightIndex: (index: number) => void
}

export const useAgentChatStore = create<AgentChatState>((set, get) => ({
  agentId: null,
  events: [],
  agentStateCache: {},
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
  processingStartedAt: null,
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
  insightsPaused: false,

  async initialize(agentId, options) {
    const providedColor = options?.agentColorHex ? normalizeHexColor(options.agentColorHex) : null
    const providedName = options?.agentName ?? null
    const providedAvatarUrl = options?.agentAvatarUrl ?? null
    const reuseExisting = get().agentId === agentId
    const fallbackColor = reuseExisting ? get().agentColorHex : DEFAULT_CHAT_COLOR_HEX
    const fallbackName = reuseExisting ? get().agentName : null
    const fallbackAvatarUrl = reuseExisting ? get().agentAvatarUrl : null

    // Cache current agent's state before switching (if we have a different agent)
    const currentState = get()
    let nextCache = currentState.agentStateCache
    const currentAgentIdForCache = currentState.agentId
    const shouldCache =
      currentAgentIdForCache &&
      currentAgentIdForCache !== agentId &&
      (
        currentState.events.length > 0 ||
        currentState.processingActive ||
        currentState.awaitingResponse ||
        currentState.processingStartedAt !== null ||
        currentState.processingWebTasks.length > 0 ||
        currentState.streaming !== null
      )
    if (shouldCache && currentAgentIdForCache) {
      nextCache = {
        ...currentState.agentStateCache,
        [currentAgentIdForCache]: {
          events: currentState.events,
          oldestCursor: currentState.oldestCursor,
          newestCursor: currentState.newestCursor,
          hasMoreOlder: currentState.hasMoreOlder,
          hasMoreNewer: currentState.hasMoreNewer,
          processingActive: currentState.processingActive,
          processingStartedAt: currentState.processingStartedAt,
          awaitingResponse: currentState.awaitingResponse,
          processingWebTasks: currentState.processingWebTasks,
          agentColorHex: currentState.agentColorHex,
          agentName: currentState.agentName,
          agentAvatarUrl: currentState.agentAvatarUrl,
          streaming: currentState.streaming,
          streamingThinkingCollapsed: currentState.streamingThinkingCollapsed,
        },
      }
    }

    // Check if we have cached state for the new agent
    const cachedState = nextCache[agentId]

    set({
      loading: true,
      agentId,
      // Use cached state if available, otherwise clear
      events: cachedState?.events ?? [],
      oldestCursor: cachedState?.oldestCursor ?? null,
      newestCursor: cachedState?.newestCursor ?? null,
      hasMoreOlder: cachedState?.hasMoreOlder ?? false,
      hasMoreNewer: cachedState?.hasMoreNewer ?? false,
      hasUnseenActivity: false,
      processingActive: cachedState?.processingActive ?? false,
      processingStartedAt: cachedState?.processingStartedAt ?? null,
      awaitingResponse: cachedState?.awaitingResponse ?? false,
      processingWebTasks: cachedState?.processingWebTasks ?? [],
      loadingOlder: false,
      loadingNewer: false,
      refreshingLatest: false,
      pendingEvents: [],
      error: null,
      autoScrollPinned: true,
      autoScrollPinSuppressedUntil: null,
      streaming: cachedState?.streaming ?? null,
      streamingLastUpdatedAt: null,
      streamingClearOnDone: false,
      streamingThinkingCollapsed: cachedState?.streamingThinkingCollapsed ?? false,
      thinkingCollapsedByCursor: {},
      agentColorHex: providedColor ?? cachedState?.agentColorHex ?? fallbackColor ?? DEFAULT_CHAT_COLOR_HEX,
      agentName: providedName ?? cachedState?.agentName ?? fallbackName ?? null,
      agentAvatarUrl: providedAvatarUrl ?? cachedState?.agentAvatarUrl ?? fallbackAvatarUrl ?? null,
      agentStateCache: nextCache,
    })

    const currentAgentId = agentId
    try {
      const snapshot = await fetchAgentTimeline(agentId, {
        direction: 'initial',
        limit: TIMELINE_WINDOW_SIZE,
        syncContext: options?.syncContext,
      })
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

      // Preserve awaitingResponse on agent switch unless we can confirm activity resumed.
      const cachedAwaiting = cachedState?.awaitingResponse ?? false
      let awaitingResponse = cachedAwaiting
      if (processingSnapshot.active) {
        awaitingResponse = false
      } else if (cachedAwaiting && cachedState?.events?.length) {
        const cachedCursors = new Set(cachedState.events.map((event) => event.cursor))
        const hasNewAgentResponse = events.some((event) => (
          !cachedCursors.has(event.cursor) &&
          (event.kind === 'thinking' || (event.kind === 'message' && event.message.isOutbound))
        ))
        if (hasNewAgentResponse) {
          awaitingResponse = false
        }
      }

      // Determine processingStartedAt for this final state update.
      // We must explicitly handle this to avoid race conditions with WebSocket updates.
      const currentProcessingStartedAt = get().processingStartedAt
      const cachedProcessingStartedAt = cachedState?.processingStartedAt ?? null
      let finalProcessingStartedAt: number | null = null
      if (processingSnapshot.active || awaitingResponse) {
        // Processing is active or we're awaiting a response - ensure we have a timestamp.
        // Prefer current state (may have been set by sendMessage or updateProcessing),
        // fall back to cached state, then to a new timestamp as last resort.
        finalProcessingStartedAt = currentProcessingStartedAt ?? cachedProcessingStartedAt ?? Date.now()
      }
      // If neither processing nor awaiting, finalProcessingStartedAt stays null (progress bar hidden)

      set({
        events,
        oldestCursor,
        newestCursor,
        hasMoreOlder: snapshot.has_more_older,
        hasMoreNewer: snapshot.has_more_newer,
        processingActive: processingSnapshot.active,
        processingStartedAt: finalProcessingStartedAt,
        processingWebTasks: processingSnapshot.webTasks,
        loading: false,
        autoScrollPinned: true,
        autoScrollPinSuppressedUntil: null,
        pendingEvents: [],
        agentColorHex,
        agentName,
        agentAvatarUrl,
        awaitingResponse,
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
      set((state) => {
        // Handle processingStartedAt consistently with updateProcessing
        let processingStartedAt = state.processingStartedAt
        if (snapshot.active && !state.processingActive) {
          // Processing just started - preserve existing timestamp or create new one
          processingStartedAt = state.processingStartedAt ?? Date.now()
        } else if (!snapshot.active && !state.awaitingResponse) {
          // Processing ended and not awaiting - clear timestamp
          processingStartedAt = null
        }
        return {
          processingActive: snapshot.active,
          processingStartedAt,
          processingWebTasks: snapshot.webTasks,
          awaitingResponse: snapshot.active ? false : state.awaitingResponse,
        }
      })
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

        // Handle processingStartedAt consistently
        const nextAwaitingResponse = shouldClearAwaiting ? false : current.awaitingResponse
        let processingStartedAt = current.processingStartedAt
        if (processingSnapshot.active && !current.processingActive) {
          processingStartedAt = current.processingStartedAt ?? Date.now()
        } else if (!processingSnapshot.active && !nextAwaitingResponse) {
          processingStartedAt = null
        }

        if (!current.autoScrollPinned) {
          const pendingEvents = mergeTimelineEvents(basePendingEvents, incoming)
          return {
            events: baseEvents,
            processingActive: processingSnapshot.active,
            processingStartedAt,
            processingWebTasks: processingSnapshot.webTasks,
            pendingEvents,
            hasUnseenActivity: incoming.length ? true : current.hasUnseenActivity,
            streaming: nextStreaming,
            streamingClearOnDone: nextStreamingClearOnDone,
            awaitingResponse: nextAwaitingResponse,
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
          processingStartedAt,
          processingWebTasks: processingSnapshot.webTasks,
          pendingEvents: [],
          streaming: nextStreaming,
          streamingClearOnDone: nextStreamingClearOnDone,
          awaitingResponse: nextAwaitingResponse,
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
      set((current) => {
        // Handle processingStartedAt consistently
        const nextAwaitingResponse = shouldClearAwaiting ? false : current.awaitingResponse
        let processingStartedAt = current.processingStartedAt
        if (processingSnapshot.active && !current.processingActive) {
          processingStartedAt = current.processingStartedAt ?? Date.now()
        } else if (!processingSnapshot.active && !nextAwaitingResponse) {
          processingStartedAt = null
        }
        return {
          events,
          oldestCursor,
          newestCursor,
          hasMoreOlder,
          hasMoreNewer: snapshot.has_more_newer,
          processingActive: processingSnapshot.active,
          processingStartedAt,
          processingWebTasks: processingSnapshot.webTasks,
          loadingNewer: false,
          agentColorHex: snapshot.agent_color_hex ? normalizeHexColor(snapshot.agent_color_hex) : current.agentColorHex,
          awaitingResponse: nextAwaitingResponse,
        }
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
      const events = prepareTimelineEvents(snapshot.events)
      const oldestCursor = events.length ? events[0].cursor : null
      const newestCursor = events.length ? events[events.length - 1].cursor : null
      const processingSnapshot = normalizeProcessingUpdate(
        snapshot.processing_snapshot ?? { active: snapshot.processing_active, webTasks: [] },
      )
      set((current) => {
        // Handle processingStartedAt consistently
        // When jumping to latest, we reset awaitingResponse to false
        let processingStartedAt = current.processingStartedAt
        if (processingSnapshot.active && !current.processingActive) {
          processingStartedAt = current.processingStartedAt ?? Date.now()
        } else if (!processingSnapshot.active) {
          // Not awaiting (always false here), so clear timestamp
          processingStartedAt = null
        }
        return {
          events,
          oldestCursor,
          newestCursor,
          hasMoreOlder: snapshot.has_more_older,
          hasMoreNewer: snapshot.has_more_newer,
          processingActive: processingSnapshot.active,
          processingStartedAt,
          processingWebTasks: processingSnapshot.webTasks,
          hasUnseenActivity: false,
          loading: false,
          pendingEvents: [],
          agentColorHex: snapshot.agent_color_hex ? normalizeHexColor(snapshot.agent_color_hex) : current.agentColorHex,
          awaitingResponse: false,
        }
      })
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
    set({ awaitingResponse: true, processingStartedAt: Date.now() })
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
          processingStartedAt: null,
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

      // Reset progress bar when new activity arrives live (indicates new work cycle)
      // This only affects live events, not cached state when switching agents
      const isOutboundMessage = normalized.kind === 'message' && normalized.message.isOutbound
      const shouldResetProgress = normalized.kind === 'steps' || normalized.kind === 'thinking' || isOutboundMessage
      const nextProcessingStartedAt = shouldResetProgress ? Date.now() : state.processingStartedAt

      if (!state.autoScrollPinned) {
        const mergedPending = mergeTimelineEvents(pendingEvents, [normalized])
        return {
          events,
          pendingEvents: mergedPending,
          hasUnseenActivity: true,
          streaming: nextStreaming,
          streamingClearOnDone: nextStreamingClearOnDone,
          awaitingResponse,
          processingStartedAt: nextProcessingStartedAt,
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
        processingStartedAt: nextProcessingStartedAt,
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
      const existingStream = state.streaming
      let isNewStream = false
      let base: StreamState
      if (isStart || !existingStream || existingStream.streamId !== payload.stream_id) {
        isNewStream = true
        base = { streamId: payload.stream_id, reasoning: '', content: '', done: false }
      } else {
        base = existingStream
      }

      const reasoningDelta = payload.reasoning_delta ?? ''
      const contentDelta = payload.content_delta ?? ''

      // Reset progress when new stream starts OR when reasoning first appears (thinking begins)
      const hadNoReasoning = !base.reasoning?.trim()
      const hasNewReasoning = Boolean(reasoningDelta)
      const isThinkingStart = hadNoReasoning && hasNewReasoning
      const shouldResetProgress = isNewStream || isThinkingStart
      const processingStartedAt = shouldResetProgress ? now : state.processingStartedAt

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
          processingStartedAt,
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
            processingStartedAt,
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
          processingStartedAt,
        }
      }

      if (isStart) {
        return {
          streaming: next,
          hasUnseenActivity,
          // Start expanded so user can see thinking, auto-collapses when done
          streamingThinkingCollapsed: false,
          streamingClearOnDone: false,
          streamingLastUpdatedAt: now,
          awaitingResponse,
          processingStartedAt,
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
        processingStartedAt,
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
    set((state) => {
      // Track when processing started (for progress bar)
      let processingStartedAt = state.processingStartedAt
      if (snapshot.active && !state.processingActive) {
        // Processing just started; preserve the original start time if we're already awaiting a response.
        processingStartedAt = state.processingStartedAt ?? Date.now()
      } else if (!snapshot.active) {
        // Processing ended; keep the timer if we're still awaiting a response.
        processingStartedAt = state.awaitingResponse ? state.processingStartedAt : null
      }
      return {
        processingActive: snapshot.active,
        processingStartedAt,
        processingWebTasks: snapshot.webTasks,
        hasUnseenActivity: !state.autoScrollPinned && snapshot.active ? true : state.hasUnseenActivity,
        awaitingResponse: snapshot.active ? false : state.awaitingResponse,
      }
    })
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

      // Don't rotate if paused
      if (current.insightsPaused) {
        return
      }

      const availableInsights = current.insights.filter(
        (insight) => !current.dismissedInsightIds.has(insight.insightId)
      )

      if (availableInsights.length <= 1) {
        return // No rotation needed
      }

      // Move to next insight
      const nextIndex = (current.currentInsightIndex + 1) % availableInsights.length
      set({ currentInsightIndex: nextIndex })

      // Schedule next rotation if still processing and not paused
      if ((current.processingActive || current.awaitingResponse) && !current.insightsPaused) {
        const timer = setTimeout(rotate, INSIGHT_TIMING.rotationIntervalMs)
        set({ insightRotationTimer: timer })
      }
    }

    // Start first rotation after initial delay (only if not paused)
    if (!state.insightsPaused) {
      const timer = setTimeout(rotate, INSIGHT_TIMING.rotationIntervalMs)
      set({ insightRotationTimer: timer })
    }
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

  setInsightsPaused(paused) {
    const state = get()

    // Clear existing timer when pausing
    if (paused && state.insightRotationTimer) {
      clearTimeout(state.insightRotationTimer)
      set({ insightsPaused: true, insightRotationTimer: null })
      return
    }

    // Resume rotation when unpausing
    if (!paused) {
      set({ insightsPaused: false })
      // Restart rotation if still processing
      if (state.processingActive || state.awaitingResponse) {
        get().startInsightRotation()
      }
    }
  },

  setCurrentInsightIndex(index) {
    const state = get()
    const availableInsights = state.insights.filter(
      (insight) => !state.dismissedInsightIds.has(insight.insightId)
    )
    if (availableInsights.length === 0) return

    // Clamp index to valid range
    const validIndex = Math.max(0, Math.min(index, availableInsights.length - 1))
    set({ currentInsightIndex: validIndex })
  },
}))
