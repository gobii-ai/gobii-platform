import { create } from 'zustand'
import type { QueryClient, InfiniteData } from '@tanstack/react-query'

import type {
  AgentMessage,
  ProcessingSnapshot,
  ProcessingWebTask,
  StreamEventPayload,
  StreamState,
  ThinkingEvent,
  TimelineEvent,
} from '../types/agentChat'
import type { InsightEvent } from '../types/insight'
import { INSIGHT_TIMING } from '../types/insight'
import { sendAgentMessage, fetchProcessingStatus, fetchAgentInsights } from '../api/agentChat'
import { normalizeHexColor, DEFAULT_CHAT_COLOR_HEX } from '../util/color'
import { mergeTimelineEvents, normalizeTimelineEvent } from './agentChatTimeline'
import { injectRealtimeEventIntoCache, flushPendingEventsToCache, updateOptimisticEventInCache } from '../hooks/useTimelineCacheInjector'
import { timelineQueryKey, type TimelinePage } from '../hooks/useAgentTimeline'

// Module-level queryClient reference, set once from AgentChatPage
let queryClientRef: QueryClient | null = null

export function setTimelineQueryClient(client: QueryClient) {
  queryClientRef = client
}

const EMPTY_PROCESSING_SNAPSHOT: ProcessingSnapshot = { active: false, webTasks: [], nextScheduledAt: null }

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

  const hasNextScheduledAt = Object.prototype.hasOwnProperty.call(snapshot, 'nextScheduledAt')
  const nextScheduledAt = typeof snapshot.nextScheduledAt === 'string'
    ? snapshot.nextScheduledAt
    : snapshot.nextScheduledAt === null
      ? null
      : undefined

  return {
    active: Boolean(snapshot.active) || webTasks.length > 0,
    webTasks,
    ...(hasNextScheduledAt ? { nextScheduledAt } : {}),
  }
}

function normalizeProcessingUpdate(input: ProcessingUpdateInput): ProcessingSnapshot {
  if (typeof input === 'boolean') {
    return { active: input, webTasks: [], nextScheduledAt: null }
  }
  return coerceProcessingSnapshot(input)
}

function resolveNextScheduledAt(snapshot: ProcessingSnapshot, fallback: string | null = null): string | null {
  if (typeof snapshot.nextScheduledAt === 'string') {
    return snapshot.nextScheduledAt
  }
  if (snapshot.nextScheduledAt === null) {
    return null
  }
  return fallback
}

function buildTimelineThinkingStream(event: ThinkingEvent): StreamState {
  return {
    streamId: `timeline:${event.cursor}`,
    reasoning: event.reasoning,
    content: '',
    done: true,
    source: 'timeline',
    cursor: event.cursor,
  }
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

/**
 * Remove optimistic matches from the react-query cache.
 * Called when a real event arrives that matches a pending optimistic event.
 */
function removeOptimisticMatchFromCache(agentId: string, signature: MessageSignature) {
  if (!queryClientRef) {
    return
  }
  const key = timelineQueryKey(agentId)
  queryClientRef.setQueryData<InfiniteData<TimelinePage>>(key, (old) => {
    if (!old?.pages?.length) {
      return old
    }
    let changed = false
    const pages = old.pages.map((page) => {
      const result = removeOptimisticMatch(page.events, signature)
      if (result.removed) {
        changed = true
        return { ...page, events: result.events }
      }
      return page
    })
    return changed ? { ...old, pages } : old
  })
}

export type AgentChatState = {
  agentId: string | null
  streaming: StreamState | null
  streamingLastUpdatedAt: number | null
  streamingClearOnDone: boolean
  streamingThinkingCollapsed: boolean
  hasUnseenActivity: boolean
  processingActive: boolean
  processingStartedAt: number | null
  awaitingResponse: boolean
  processingWebTasks: ProcessingWebTask[]
  nextScheduledAt: string | null
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
  setAgentId: (
    agentId: string | null,
    options?: {
      agentColorHex?: string | null
      agentName?: string | null
      agentAvatarUrl?: string | null
    },
  ) => void
  refreshProcessing: () => Promise<void>
  sendMessage: (body: string, attachments?: File[]) => Promise<void>
  receiveRealtimeEvent: (event: TimelineEvent) => void
  receiveStreamEvent: (payload: StreamEventPayload) => void
  finalizeStreaming: () => void
  updateProcessing: (snapshot: ProcessingUpdateInput) => void
  updateAgentIdentity: (update: {
    agentId?: string | null
    agentName?: string | null
    agentColorHex?: string | null
    agentAvatarUrl?: string | null
  }) => void
  setAutoScrollPinned: (pinned: boolean) => void
  suppressAutoScrollPin: (durationMs?: number) => void
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
  streaming: null,
  streamingLastUpdatedAt: null,
  streamingClearOnDone: false,
  streamingThinkingCollapsed: false,
  hasUnseenActivity: false,
  processingActive: false,
  processingStartedAt: null,
  awaitingResponse: false,
  processingWebTasks: [],
  nextScheduledAt: null,
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

  setAgentId(agentId, options) {
    const providedColor = options?.agentColorHex ? normalizeHexColor(options.agentColorHex) : null
    const providedName = options?.agentName ?? null
    const providedAvatarUrl = options?.agentAvatarUrl ?? null
    const reuseExisting = get().agentId === agentId

    // Clear insight rotation timer when switching to a different agent
    if (!reuseExisting) {
      const existingInsightTimer = get().insightRotationTimer
      if (existingInsightTimer) {
        clearTimeout(existingInsightTimer)
      }
    }

    const fallbackColor = reuseExisting ? get().agentColorHex : DEFAULT_CHAT_COLOR_HEX
    const fallbackName = reuseExisting ? get().agentName : null
    const fallbackAvatarUrl = reuseExisting ? get().agentAvatarUrl : null

    set({
      agentId,
      hasUnseenActivity: false,
      processingActive: reuseExisting ? get().processingActive : false,
      processingStartedAt: reuseExisting ? get().processingStartedAt : null,
      awaitingResponse: reuseExisting ? get().awaitingResponse : false,
      processingWebTasks: reuseExisting ? get().processingWebTasks : [],
      nextScheduledAt: reuseExisting ? get().nextScheduledAt : null,
      pendingEvents: [],
      autoScrollPinned: true,
      autoScrollPinSuppressedUntil: null,
      streaming: reuseExisting ? get().streaming : null,
      streamingLastUpdatedAt: reuseExisting ? get().streamingLastUpdatedAt : null,
      streamingClearOnDone: reuseExisting ? get().streamingClearOnDone : false,
      streamingThinkingCollapsed: reuseExisting ? get().streamingThinkingCollapsed : false,
      agentColorHex: providedColor ?? fallbackColor ?? DEFAULT_CHAT_COLOR_HEX,
      agentName: providedName ?? fallbackName ?? null,
      agentAvatarUrl: providedAvatarUrl ?? fallbackAvatarUrl ?? null,
      // Reset insight state only when switching to a different agent
      ...(reuseExisting ? {} : {
        insights: [],
        currentInsightIndex: 0,
        insightsFetchedAt: null,
        insightRotationTimer: null,
        insightProcessingStartedAt: null,
        dismissedInsightIds: new Set(),
        insightsPaused: false,
      }),
    })
  },

  async refreshProcessing() {
    const agentId = get().agentId
    if (!agentId) return
    try {
      const { processing_active, processing_snapshot } = await fetchProcessingStatus(agentId)
      const snapshot = normalizeProcessingUpdate(processing_snapshot ?? { active: processing_active, webTasks: [] })
      set((state) => {
        let processingStartedAt = state.processingStartedAt
        if (snapshot.active && !state.processingActive) {
          processingStartedAt = state.processingStartedAt ?? Date.now()
        } else if (!snapshot.active && !state.awaitingResponse) {
          processingStartedAt = null
        }
        return {
          processingActive: snapshot.active,
          processingStartedAt,
          processingWebTasks: snapshot.webTasks,
          nextScheduledAt: resolveNextScheduledAt(snapshot, state.nextScheduledAt),
          awaitingResponse: snapshot.active ? false : state.awaitingResponse,
        }
      })
    } catch (error) {
      console.error('Failed to refresh processing status:', error)
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
    const agentId = state.agentId
    const { event: optimisticEvent, clientId } = buildOptimisticMessageEvent(trimmed, attachments)
    set({ awaitingResponse: true, processingStartedAt: Date.now() })
    // Inject optimistic event into react-query cache
    get().receiveRealtimeEvent(optimisticEvent)
    try {
      const event = await sendAgentMessage(agentId, trimmed, attachments)
      get().receiveRealtimeEvent(event)
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Failed to send message'
      // Update optimistic event status in cache
      if (queryClientRef) {
        updateOptimisticEventInCache(queryClientRef, agentId, clientId, 'failed', message)
      }
      set((current) => {
        const updatedPending = updateOptimisticStatus(current.pendingEvents, clientId, 'failed', message)
        return {
          pendingEvents: updatedPending.events,
          awaitingResponse: false,
          processingStartedAt: null,
        }
      })
      throw error
    }
  },

  receiveRealtimeEvent(event) {
    const normalized = normalizeTimelineEvent(event)
    const agentId = get().agentId

    set((state) => {
      let pendingEvents = state.pendingEvents
      let awaitingResponse = state.awaitingResponse

      // Remove optimistic matches from both cache and pending events
      if (normalized.kind === 'message' && !normalized.message.isOutbound && normalized.message.status !== 'sending') {
        const signature = buildMessageSignature(normalized.message)
        if (agentId) {
          removeOptimisticMatchFromCache(agentId, signature)
        }
        const updatedPending = removeOptimisticMatch(pendingEvents, signature)
        if (updatedPending.removed) {
          pendingEvents = updatedPending.events
        }
      }

      const isThinkingEvent = normalized.kind === 'thinking'
      const isOutboundMessage = normalized.kind === 'message' && normalized.message.isOutbound
      let nextStreaming = state.streaming
      let nextStreamingClearOnDone = state.streamingClearOnDone
      let nextStreamingThinkingCollapsed = state.streamingThinkingCollapsed

      if (isThinkingEvent) {
        if (nextStreaming?.source === 'stream') {
          if (!nextStreaming.cursor) {
            nextStreaming = { ...nextStreaming, cursor: normalized.cursor }
          }
        } else {
          nextStreaming = buildTimelineThinkingStream(normalized)
          nextStreamingClearOnDone = false
          nextStreamingThinkingCollapsed = false
        }
      } else if (nextStreaming?.source === 'timeline') {
        nextStreaming = null
        nextStreamingClearOnDone = false
      }

      if (isOutboundMessage) {
        nextStreaming = null
        nextStreamingClearOnDone = false
      }

      if (normalized.kind === 'thinking' || normalized.kind === 'steps' || isOutboundMessage) {
        awaitingResponse = false
      }

      const shouldResetProgress = normalized.kind === 'steps' || normalized.kind === 'thinking' || isOutboundMessage
      const nextProcessingStartedAt = shouldResetProgress ? Date.now() : state.processingStartedAt

      if (!state.autoScrollPinned) {
        const mergedPending = mergeTimelineEvents(pendingEvents, [normalized])
        return {
          pendingEvents: mergedPending,
          hasUnseenActivity: true,
          streaming: nextStreaming,
          streamingClearOnDone: nextStreamingClearOnDone,
          streamingThinkingCollapsed: nextStreamingThinkingCollapsed,
          awaitingResponse,
          processingStartedAt: nextProcessingStartedAt,
        }
      }

      // When pinned, inject into react-query cache
      if (queryClientRef && agentId) {
        injectRealtimeEventIntoCache(queryClientRef, agentId, normalized)
      }

      return {
        pendingEvents: [],
        streaming: nextStreaming,
        streamingClearOnDone: nextStreamingClearOnDone,
        streamingThinkingCollapsed: nextStreamingThinkingCollapsed,
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
    let shouldInvalidateQuery = false

    set((state) => {
      const existingStream = state.streaming
      let isNewStream = false
      let base: StreamState
      if (isStart || !existingStream || existingStream.streamId !== payload.stream_id) {
        isNewStream = true
        base = { streamId: payload.stream_id, reasoning: '', content: '', done: false, source: 'stream', cursor: null }
      } else {
        base = existingStream
      }
      const awaitingResponse = state.awaitingResponse || isNewStream

      const reasoningDelta = payload.reasoning_delta ?? ''
      const contentDelta = payload.content_delta ?? ''

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
        source: base.source ?? 'stream',
        cursor: base.cursor ?? null,
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
        shouldInvalidateQuery = true
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
          streamingThinkingCollapsed: false,
          streamingClearOnDone: false,
          streamingLastUpdatedAt: now,
          awaitingResponse,
          processingStartedAt,
        }
      }

      const nextStreamingClearOnDone = hasStreamingContent ? false : state.streamingClearOnDone
      const nextStreamingThinkingCollapsed =
        isNewStream ? false : isDone && next.reasoning ? true : state.streamingThinkingCollapsed
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

    if (shouldInvalidateQuery && queryClientRef) {
      const agentId = get().agentId
      if (agentId) {
        void queryClientRef.invalidateQueries({ queryKey: timelineQueryKey(agentId) })
      }
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
      let processingStartedAt = state.processingStartedAt
      if (snapshot.active && !state.processingActive) {
        processingStartedAt = state.processingStartedAt ?? Date.now()
      } else if (!snapshot.active) {
        processingStartedAt = state.awaitingResponse ? state.processingStartedAt : null
      }
      return {
        processingActive: snapshot.active,
        processingStartedAt,
        processingWebTasks: snapshot.webTasks,
        nextScheduledAt: resolveNextScheduledAt(snapshot, state.nextScheduledAt),
        hasUnseenActivity: !state.autoScrollPinned && snapshot.active ? true : state.hasUnseenActivity,
        awaitingResponse: snapshot.active ? false : state.awaitingResponse,
      }
    })
  },

  updateAgentIdentity(update) {
    set((state) => {
      const targetAgentId = update.agentId ?? state.agentId
      if (!targetAgentId) {
        return state
      }

      const hasName = Object.prototype.hasOwnProperty.call(update, 'agentName')
      const hasColor = Object.prototype.hasOwnProperty.call(update, 'agentColorHex')
      const hasAvatar = Object.prototype.hasOwnProperty.call(update, 'agentAvatarUrl')

      if (!hasName && !hasColor && !hasAvatar) {
        return state
      }

      const isCurrentAgent = state.agentId === targetAgentId
      if (!isCurrentAgent) {
        return state
      }

      return {
        ...(hasName ? { agentName: update.agentName ?? null } : {}),
        ...(hasColor
          ? { agentColorHex: update.agentColorHex ? normalizeHexColor(update.agentColorHex) : null }
          : {}),
        ...(hasAvatar ? { agentAvatarUrl: update.agentAvatarUrl ?? null } : {}),
      }
    })
  },

  setAutoScrollPinned(pinned) {
    set((state) => {
      if (pinned && state.pendingEvents.length) {
        // Flush pending events into react-query cache
        if (queryClientRef && state.agentId) {
          flushPendingEventsToCache(queryClientRef, state.agentId, state.pendingEvents)
        }
        return {
          autoScrollPinned: true,
          hasUnseenActivity: false,
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

  setStreamingThinkingCollapsed(collapsed) {
    set({ streamingThinkingCollapsed: collapsed })
  },

  // Insight actions
  async fetchInsights() {
    const agentId = get().agentId
    if (!agentId) return

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
    if (state.insightRotationTimer) {
      clearTimeout(state.insightRotationTimer)
    }

    set({
      insightProcessingStartedAt: Date.now(),
      insightsPaused: false,
    })

    void get().fetchInsights()

    const rotate = () => {
      const current = get()

      if (current.insightsPaused) {
        return
      }

      const availableInsights = current.insights.filter(
        (insight) => !current.dismissedInsightIds.has(insight.insightId)
      )

      if (availableInsights.length <= 1) {
        return
      }

      const nextIndex = (current.currentInsightIndex + 1) % availableInsights.length
      set({ currentInsightIndex: nextIndex })

      if ((current.processingActive || current.awaitingResponse) && !current.insightsPaused) {
        const timer = setTimeout(rotate, INSIGHT_TIMING.rotationIntervalMs)
        set({ insightRotationTimer: timer })
      }
    }

    const timer = setTimeout(rotate, INSIGHT_TIMING.rotationIntervalMs)
    set({ insightRotationTimer: timer })
  },

  stopInsightRotation() {
    const timer = get().insightRotationTimer
    if (timer) {
      clearTimeout(timer)
    }
    set({ insightRotationTimer: null, insightsPaused: false })
  },

  dismissInsight(insightId) {
    set((state) => {
      const newDismissed = new Set(state.dismissedInsightIds)
      newDismissed.add(insightId)

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

    const processingStartedAt = state.insightProcessingStartedAt
    if (processingStartedAt && Date.now() - processingStartedAt < INSIGHT_TIMING.showAfterMs) {
      return null
    }

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

    if (paused && state.insightRotationTimer) {
      clearTimeout(state.insightRotationTimer)
      set({ insightsPaused: true, insightRotationTimer: null })
      return
    }

    if (!paused) {
      set({ insightsPaused: false })
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

    const validIndex = Math.max(0, Math.min(index, availableInsights.length - 1))
    set({ currentInsightIndex: validIndex })
  },
}))
