import { createAsyncThunk, createSlice, type PayloadAction } from '@reduxjs/toolkit'
import type { InfiniteData, QueryClient } from '@tanstack/react-query'

import { sendAgentMessage, fetchProcessingStatus } from '../api/agentChat'
import {
  flushPendingEventsToCache,
  injectRealtimeEventIntoCache,
  refreshTimelineLatestInCache,
  updateOptimisticEventInCache,
} from '../hooks/useTimelineCacheInjector'
import { timelineQueryKey, type TimelinePage } from '../hooks/useAgentTimeline'
import { mergeTimelineEvents, normalizeTimelineEvent } from '../stores/agentChatTimeline'
import type {
  AgentMessage,
  ProcessingSnapshot,
  ProcessingWebTask,
  StreamEventPayload,
  StreamState,
  ThinkingEvent,
  TimelineEvent,
} from '../types/agentChat'
import type { PlanningState, SignupPreviewState } from '../types/agentRoster'
import type { BurnRateMetadata, InsightEvent } from '../types/insight'
import type { AppDispatch, RootState } from './appStore'

const EMPTY_PROCESSING_SNAPSHOT: ProcessingSnapshot = { active: false, webTasks: [], nextScheduledAt: null }
const OPTIMISTIC_MATCH_WINDOW_MS = 120_000

export type ProcessingUpdateInput = boolean | Partial<ProcessingSnapshot> | null | undefined

type MessageSignature = {
  text: string
  attachmentsCount: number
  timestampMs: number | null
}

export type AgentChatIdentityState = {
  agentName: string | null
  agentAvatarUrl: string | null
  signupPreviewState: SignupPreviewState
  planningState: PlanningState
}

export type AgentChatProcessingState = {
  processingActive: boolean
  processingStartedAt: number | null
  awaitingResponse: boolean
  processingWebTasks: ProcessingWebTask[]
  nextScheduledAt: string | null
}

export type AgentChatStreamState = {
  streaming: StreamState | null
  streamingLastUpdatedAt: number | null
  streamingClearOnDone: boolean
  streamingThinkingCollapsed: boolean
}

export type AgentChatTimelineUiState = {
  hasUnseenActivity: boolean
  autoScrollPinned: boolean
  autoScrollPinSuppressedUntil: number | null
  pendingEvents: TimelineEvent[]
  realtimeEventCursorIds: Record<string, true>
}

export type AgentChatInsightsState = {
  insightsById: Record<string, InsightEvent>
  insightIds: string[]
  currentInsightIndex: number
  insightProcessingStartedAt: number | null
  dismissedInsightIds: Record<string, true>
  insightsPaused: boolean
}

export type AgentChatSession = {
  identity: AgentChatIdentityState
  processing: AgentChatProcessingState
  stream: AgentChatStreamState
  timelineUi: AgentChatTimelineUiState
  insights: AgentChatInsightsState
}

export type ChatState = {
  activeAgentId: string | null
  sessionsByAgentId: Record<string, AgentChatSession>
  recentAgentIds: string[]
}

function createInitialSession(): AgentChatSession {
  return {
    identity: {
      agentName: null,
      agentAvatarUrl: null,
      signupPreviewState: 'none',
      planningState: 'skipped',
    },
    processing: {
      processingActive: false,
      processingStartedAt: null,
      awaitingResponse: false,
      processingWebTasks: [],
      nextScheduledAt: null,
    },
    stream: {
      streaming: null,
      streamingLastUpdatedAt: null,
      streamingClearOnDone: false,
      streamingThinkingCollapsed: false,
    },
    timelineUi: {
      hasUnseenActivity: false,
      autoScrollPinned: true,
      autoScrollPinSuppressedUntil: null,
      pendingEvents: [],
      realtimeEventCursorIds: {},
    },
    insights: {
      insightsById: {},
      insightIds: [],
      currentInsightIndex: 0,
      insightProcessingStartedAt: null,
      dismissedInsightIds: {},
      insightsPaused: false,
    },
  }
}

export const initialChatState: ChatState = {
  activeAgentId: null,
  sessionsByAgentId: {},
  recentAgentIds: [],
}

function ensureSession(state: ChatState, agentId: string): AgentChatSession {
  if (!state.sessionsByAgentId[agentId]) {
    state.sessionsByAgentId[agentId] = createInitialSession()
  }
  if (!state.recentAgentIds.includes(agentId)) {
    state.recentAgentIds.push(agentId)
  }
  return state.sessionsByAgentId[agentId]
}

function getSession(state: ChatState, agentId: string | null | undefined): AgentChatSession | null {
  return agentId ? state.sessionsByAgentId[agentId] ?? null : null
}

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

export function normalizeProcessingUpdate(input: ProcessingUpdateInput): ProcessingSnapshot {
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

function shouldTrackRealtimeAnimationCursor(event: TimelineEvent): boolean {
  return event.kind === 'thinking' || event.kind === 'steps'
}

function normalizePlainSignatureText(value: string): string {
  return value.trim().replace(/\u00a0/g, ' ').replace(/\s+/g, ' ').trim()
}

function decodeHtmlEntities(value: string): string {
  return value
    .replace(/&quot;/gi, '"')
    .replace(/&amp;/gi, '&')
    .replace(/&apos;/gi, "'")
    .replace(/&lt;/gi, '<')
    .replace(/&gt;/gi, '>')
    .replace(/&nbsp;/gi, ' ')
    .replace(/&#(\d+);/g, (_, dec) => String.fromCharCode(Number(dec)))
    .replace(/&#x([0-9a-fA-F]+);/g, (_, hex) => String.fromCharCode(parseInt(hex, 16)))
}

function normalizeHtmlSignatureText(value: string): string {
  const trimmed = value.trim()
  if (!trimmed) {
    return ''
  }
  if (typeof document === 'undefined') {
    return normalizePlainSignatureText(decodeHtmlEntities(trimmed.replace(/<[^>]+>/g, ' ')))
  }

  const container = document.createElement('div')
  container.innerHTML = trimmed
  return normalizePlainSignatureText(container.textContent || container.innerText || '')
}

function buildMessageSignature(message: AgentMessage): MessageSignature {
  const textSource = message.bodyText?.trim() ? message.bodyText : message.bodyHtml || ''
  const text = message.bodyText?.trim()
    ? normalizePlainSignatureText(textSource)
    : normalizeHtmlSignatureText(textSource)
  const attachmentsCount = message.attachments?.length ?? 0
  const timestampMs = message.timestamp ? Date.parse(message.timestamp) : null
  return {
    text,
    attachmentsCount,
    timestampMs: Number.isNaN(timestampMs ?? NaN) ? null : timestampMs,
  }
}

function isOptimisticMatch(event: TimelineEvent, signature: MessageSignature): boolean {
  if (event.kind !== 'message' || event.message.status !== 'sending') {
    return false
  }
  const optimisticSignature = buildMessageSignature(event.message)
  if (!signature.text && signature.attachmentsCount === 0) {
    return false
  }
  if (optimisticSignature.text !== signature.text || optimisticSignature.attachmentsCount !== signature.attachmentsCount) {
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
): TimelineEvent[] {
  const index = events.findIndex((event) => event.kind === 'message' && event.message.clientId === clientId)
  if (index < 0 || events[index].kind !== 'message') {
    return events
  }
  const target = events[index]
  if (target.kind !== 'message') {
    return events
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
  return next
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

function removeOptimisticMatchFromCache(queryClient: QueryClient | null | undefined, agentId: string, signature: MessageSignature) {
  if (!queryClient) {
    return
  }
  const key = timelineQueryKey(agentId)
  queryClient.setQueryData<InfiniteData<TimelinePage>>(key, (old) => {
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

function applyProcessingUpdate(session: AgentChatSession, snapshot: ProcessingSnapshot) {
  let processingStartedAt = session.processing.processingStartedAt
  if (snapshot.active && !session.processing.processingActive) {
    processingStartedAt = session.processing.processingStartedAt ?? Date.now()
  } else if (!snapshot.active) {
    processingStartedAt = session.processing.awaitingResponse ? session.processing.processingStartedAt : null
  }
  session.processing.processingActive = snapshot.active
  session.processing.processingStartedAt = processingStartedAt
  session.processing.processingWebTasks = snapshot.webTasks
  session.processing.nextScheduledAt = resolveNextScheduledAt(snapshot, session.processing.nextScheduledAt)
  session.timelineUi.hasUnseenActivity = !session.timelineUi.autoScrollPinned && snapshot.active
    ? true
    : session.timelineUi.hasUnseenActivity
  session.processing.awaitingResponse = snapshot.active ? false : session.processing.awaitingResponse
}

export const refreshProcessing = createAsyncThunk<void, void, { state: RootState }>(
  'chat/refreshProcessing',
  async (_, { dispatch, getState }) => {
    const agentId = getState().chat.activeAgentId
    if (!agentId) return
    try {
      const { processing_active, processing_snapshot, signup_preview_state, planning_state } = await fetchProcessingStatus(agentId)
      const snapshot = normalizeProcessingUpdate(processing_snapshot ?? { active: processing_active, webTasks: [] })
      dispatch(chatActions.processingUpdated({ agentId, snapshot }))
      dispatch(chatActions.agentIdentityUpdated({
        agentId,
        signupPreviewState: signup_preview_state ?? undefined,
        planningState: planning_state ?? undefined,
      }))
    } catch (error) {
      console.error('Failed to refresh processing status:', error)
    }
  },
)

export const updateActiveProcessing = (
  snapshotInput: ProcessingUpdateInput,
) => (dispatch: AppDispatch, getState: () => RootState) => {
  const agentId = getState().chat.activeAgentId
  if (!agentId) {
    return
  }
  dispatch(chatActions.processingUpdated({ agentId, snapshot: normalizeProcessingUpdate(snapshotInput) }))
}

export const suppressActiveAutoScrollPin = (
  durationMs = 1000,
) => (dispatch: AppDispatch, getState: () => RootState) => {
  const agentId = getState().chat.activeAgentId
  if (!agentId) {
    return
  }
  dispatch(chatActions.autoScrollPinSuppressed({ agentId, durationMs }))
}

export const receiveRealtimeEvent = (
  event: TimelineEvent,
  agentIdOverride?: string | null,
) => (dispatch: AppDispatch, getState: () => RootState, extra?: { queryClient?: QueryClient | null }) => {
  const normalized = normalizeTimelineEvent(event)
  const agentId = agentIdOverride ?? getState().chat.activeAgentId
  if (!agentId) {
    return
  }
  if (normalized.kind === 'message' && !normalized.message.isOutbound && normalized.message.status !== 'sending') {
    removeOptimisticMatchFromCache(extra?.queryClient, agentId, buildMessageSignature(normalized.message))
  }

  const session = getState().chat.sessionsByAgentId[agentId]
  if (session?.timelineUi.autoScrollPinned && extra?.queryClient) {
    injectRealtimeEventIntoCache(extra.queryClient, agentId, normalized)
  }
  dispatch(chatActions.realtimeEventReceived({ agentId, event: normalized }))
}

export const receiveStreamEvent = (
  payload: StreamEventPayload,
) => (dispatch: AppDispatch, getState: () => RootState, extra?: { queryClient?: QueryClient | null }) => {
  const agentId = getState().chat.activeAgentId
  if (!agentId || !payload?.stream_id) {
    return
  }
  const result = reduceStreamEvent(getState().chat.sessionsByAgentId[agentId] ?? createInitialSession(), payload)
  dispatch(chatActions.streamEventReduced({ agentId, session: result.session }))
  if (result.shouldInvalidateQuery && extra?.queryClient) {
    void refreshTimelineLatestInCache(extra.queryClient, agentId)
  }
}

export const setAutoScrollPinned = (
  pinned: boolean,
) => (dispatch: AppDispatch, getState: () => RootState, extra?: { queryClient?: QueryClient | null }) => {
  const agentId = getState().chat.activeAgentId
  if (!agentId) {
    return
  }
  const pendingEvents = getState().chat.sessionsByAgentId[agentId]?.timelineUi.pendingEvents ?? []
  if (pinned && pendingEvents.length && extra?.queryClient) {
    flushPendingEventsToCache(extra.queryClient, agentId, pendingEvents)
  }
  dispatch(chatActions.autoScrollPinnedSet({ agentId, pinned }))
}

export const persistPendingEventsToCache = () => (
  dispatch: AppDispatch,
  getState: () => RootState,
  extra?: { queryClient?: QueryClient | null },
) => {
  const agentId = getState().chat.activeAgentId
  if (!agentId || !extra?.queryClient) {
    return
  }
  const pendingEvents = getState().chat.sessionsByAgentId[agentId]?.timelineUi.pendingEvents ?? []
  if (!pendingEvents.length) {
    return
  }
  flushPendingEventsToCache(extra.queryClient, agentId, pendingEvents)
  dispatch(chatActions.pendingEventsPersisted({ agentId }))
}

export const sendMessage = createAsyncThunk<
  void,
  { body: string; attachments?: File[] },
  { state: RootState; extra: { queryClient: QueryClient | null } }
>('chat/sendMessage', async ({ body, attachments = [] }, { dispatch, getState, extra }) => {
  const agentId = getState().chat.activeAgentId
  if (!agentId) {
    throw new Error('Agent not initialized')
  }
  const trimmed = body.trim()
  if (!trimmed && attachments.length === 0) {
    return
  }

  const { event, clientId } = buildOptimisticMessageEvent(trimmed, attachments)
  dispatch(chatActions.messageSendStarted({ agentId }))
  dispatch(receiveRealtimeEvent(event, agentId))
  try {
    const serverEvent = await sendAgentMessage(agentId, trimmed, attachments)
    dispatch(receiveRealtimeEvent(serverEvent, agentId))
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Failed to send message'
    if (extra.queryClient) {
      updateOptimisticEventInCache(extra.queryClient, agentId, clientId, 'failed', message)
    }
    dispatch(chatActions.optimisticMessageFailed({ agentId, clientId, message }))
    throw error
  }
})

function reduceStreamEvent(session: AgentChatSession, payload: StreamEventPayload): { session: AgentChatSession; shouldInvalidateQuery: boolean } {
  const nextSession: AgentChatSession = JSON.parse(JSON.stringify(session)) as AgentChatSession
  const isStart = payload.status === 'start'
  const isDone = payload.status === 'done'
  const isDelta = payload.status === 'delta'
  const isCanceled = payload.status === 'canceled'
  const now = Date.now()
  let shouldInvalidateQuery = false

  if (isCanceled) {
    if (nextSession.stream.streaming?.streamId && nextSession.stream.streaming.streamId !== payload.stream_id) {
      return { session, shouldInvalidateQuery }
    }
    nextSession.stream.streaming = null
    nextSession.stream.streamingClearOnDone = false
    nextSession.stream.streamingLastUpdatedAt = now
    return { session: nextSession, shouldInvalidateQuery }
  }

  const existingStream = nextSession.stream.streaming
  let isNewStream = false
  let base: StreamState
  if (isStart || !existingStream || existingStream.streamId !== payload.stream_id) {
    isNewStream = true
    base = { streamId: payload.stream_id, reasoning: '', content: '', done: false, source: 'stream', cursor: null }
  } else {
    base = existingStream
  }
  nextSession.processing.awaitingResponse = nextSession.processing.awaitingResponse || isNewStream

  const reasoningDelta = payload.reasoning_delta ?? ''
  const contentDelta = payload.content_delta ?? ''
  const hadNoReasoning = !base.reasoning?.trim()
  const hasNewReasoning = Boolean(reasoningDelta)
  const shouldResetProgress = isNewStream || (hadNoReasoning && hasNewReasoning)
  if (shouldResetProgress) {
    nextSession.processing.processingStartedAt = now
  }

  const next: StreamState = {
    streamId: base.streamId,
    reasoning: reasoningDelta ? `${base.reasoning}${reasoningDelta}` : base.reasoning,
    content: contentDelta ? `${base.content}${contentDelta}` : base.content,
    done: isDone ? true : isDelta ? false : base.done,
    source: base.source ?? 'stream',
    cursor: base.cursor ?? null,
  }

  if (!nextSession.timelineUi.autoScrollPinned) {
    nextSession.timelineUi.hasUnseenActivity = true
  }

  if (isDone && !next.reasoning && !next.content) {
    nextSession.stream.streaming = null
    nextSession.stream.streamingLastUpdatedAt = now
    nextSession.stream.streamingClearOnDone = false
    return { session: nextSession, shouldInvalidateQuery }
  }

  const hasStreamingContent = Boolean(next.content.trim())
  if (isDone && next.reasoning && !hasStreamingContent) {
    if (nextSession.stream.streamingClearOnDone) {
      nextSession.stream.streaming = null
      nextSession.stream.streamingClearOnDone = false
    } else {
      shouldInvalidateQuery = true
      nextSession.stream.streaming = next
      nextSession.stream.streamingThinkingCollapsed = true
      nextSession.stream.streamingClearOnDone = false
    }
    nextSession.stream.streamingLastUpdatedAt = now
    return { session: nextSession, shouldInvalidateQuery }
  }

  nextSession.stream.streaming = next
  nextSession.stream.streamingThinkingCollapsed = isNewStream
    ? false
    : isDone && next.reasoning
      ? true
      : nextSession.stream.streamingThinkingCollapsed
  nextSession.stream.streamingClearOnDone = hasStreamingContent ? false : nextSession.stream.streamingClearOnDone
  if (isStart) {
    nextSession.stream.streamingThinkingCollapsed = false
    nextSession.stream.streamingClearOnDone = false
  }
  nextSession.stream.streamingLastUpdatedAt = now
  return { session: nextSession, shouldInvalidateQuery }
}

const chatSlice = createSlice({
  name: 'chat',
  initialState: initialChatState,
  reducers: {
    resetChatState() {
      return initialChatState
    },
    agentSelected: (
      state,
      action: PayloadAction<{
        agentId: string | null
        options?: {
          agentName?: string | null
          agentAvatarUrl?: string | null
          processingActive?: boolean
          signupPreviewState?: SignupPreviewState | null
          planningState?: PlanningState | null
        }
      }>,
    ) => {
      const { agentId, options } = action.payload
      state.activeAgentId = agentId
      if (!agentId) {
        return
      }
      const session = ensureSession(state, agentId)
      session.timelineUi.hasUnseenActivity = false
      session.timelineUi.pendingEvents = []
      session.timelineUi.realtimeEventCursorIds = {}
      session.timelineUi.autoScrollPinned = true
      session.timelineUi.autoScrollPinSuppressedUntil = null
      if (Object.prototype.hasOwnProperty.call(options ?? {}, 'processingActive')) {
        session.processing.processingActive = Boolean(options?.processingActive)
        session.processing.processingStartedAt = options?.processingActive ? Date.now() : null
      }
      if (Object.prototype.hasOwnProperty.call(options ?? {}, 'agentName')) {
        session.identity.agentName = options?.agentName ?? null
      }
      if (Object.prototype.hasOwnProperty.call(options ?? {}, 'agentAvatarUrl')) {
        session.identity.agentAvatarUrl = options?.agentAvatarUrl ?? null
      }
      if (Object.prototype.hasOwnProperty.call(options ?? {}, 'signupPreviewState')) {
        session.identity.signupPreviewState = options?.signupPreviewState ?? 'none'
      }
      if (Object.prototype.hasOwnProperty.call(options ?? {}, 'planningState')) {
        session.identity.planningState = options?.planningState ?? 'skipped'
      }
    },
    processingUpdated(state, action: PayloadAction<{ agentId: string; snapshot: ProcessingSnapshot }>) {
      applyProcessingUpdate(ensureSession(state, action.payload.agentId), action.payload.snapshot)
    },
    agentIdentityUpdated(
      state,
      action: PayloadAction<{
        agentId?: string | null
        agentName?: string | null
        agentAvatarUrl?: string | null
        signupPreviewState?: SignupPreviewState | null
        planningState?: PlanningState | null
      }>,
    ) {
      const agentId = action.payload.agentId ?? state.activeAgentId
      if (!agentId) return
      const session = ensureSession(state, agentId)
      if (Object.prototype.hasOwnProperty.call(action.payload, 'agentName')) {
        session.identity.agentName = action.payload.agentName ?? null
      }
      if (Object.prototype.hasOwnProperty.call(action.payload, 'agentAvatarUrl')) {
        session.identity.agentAvatarUrl = action.payload.agentAvatarUrl ?? null
      }
      if (Object.prototype.hasOwnProperty.call(action.payload, 'signupPreviewState')) {
        session.identity.signupPreviewState = action.payload.signupPreviewState ?? 'none'
      }
      if (Object.prototype.hasOwnProperty.call(action.payload, 'planningState')) {
        session.identity.planningState = action.payload.planningState ?? 'skipped'
      }
    },
    messageSendStarted(state, action: PayloadAction<{ agentId: string }>) {
      const session = ensureSession(state, action.payload.agentId)
      session.processing.awaitingResponse = true
      session.processing.processingStartedAt = Date.now()
    },
    optimisticMessageFailed(state, action: PayloadAction<{ agentId: string; clientId: string; message: string }>) {
      const session = ensureSession(state, action.payload.agentId)
      session.timelineUi.pendingEvents = updateOptimisticStatus(
        session.timelineUi.pendingEvents,
        action.payload.clientId,
        'failed',
        action.payload.message,
      )
      session.processing.awaitingResponse = false
      session.processing.processingStartedAt = null
    },
    realtimeEventReceived(state, action: PayloadAction<{ agentId: string; event: TimelineEvent }>) {
      const session = ensureSession(state, action.payload.agentId)
      const normalized = action.payload.event
      let pendingEvents = session.timelineUi.pendingEvents
      let awaitingResponse = session.processing.awaitingResponse
      if (shouldTrackRealtimeAnimationCursor(normalized)) {
        session.timelineUi.realtimeEventCursorIds[normalized.cursor] = true
      }

      if (normalized.kind === 'message' && !normalized.message.isOutbound && normalized.message.status !== 'sending') {
        pendingEvents = removeOptimisticMatch(pendingEvents, buildMessageSignature(normalized.message)).events
      }

      const isThinkingEvent = normalized.kind === 'thinking'
      const isOutboundMessage = normalized.kind === 'message' && normalized.message.isOutbound
      if (isThinkingEvent) {
        if (session.stream.streaming?.source === 'stream') {
          if (!session.stream.streaming.cursor) {
            session.stream.streaming = { ...session.stream.streaming, cursor: normalized.cursor }
          }
        } else {
          session.stream.streaming = buildTimelineThinkingStream(normalized)
          session.stream.streamingClearOnDone = false
          session.stream.streamingThinkingCollapsed = false
        }
      } else if (session.stream.streaming?.source === 'timeline') {
        session.stream.streaming = null
        session.stream.streamingClearOnDone = false
      }
      if (isOutboundMessage) {
        session.stream.streaming = null
        session.stream.streamingClearOnDone = false
      }
      if (normalized.kind === 'thinking' || normalized.kind === 'steps' || isOutboundMessage) {
        awaitingResponse = false
      }
      if (normalized.kind === 'steps' || normalized.kind === 'thinking' || isOutboundMessage) {
        session.processing.processingStartedAt = Date.now()
      }
      if (!session.timelineUi.autoScrollPinned) {
        session.timelineUi.pendingEvents = mergeTimelineEvents(pendingEvents, [normalized])
        session.timelineUi.hasUnseenActivity = true
      } else {
        session.timelineUi.pendingEvents = []
      }
      session.processing.awaitingResponse = awaitingResponse
    },
    streamEventReduced(state, action: PayloadAction<{ agentId: string; session: AgentChatSession }>) {
      state.sessionsByAgentId[action.payload.agentId] = action.payload.session
    },
    streamingFinalized(state) {
      const session = getSession(state, state.activeAgentId)
      if (!session?.stream.streaming || session.stream.streaming.done) {
        return
      }
      const hasReasoning = Boolean(session.stream.streaming.reasoning?.trim())
      session.stream.streaming = { ...session.stream.streaming, done: true }
      session.stream.streamingThinkingCollapsed = hasReasoning ? true : session.stream.streamingThinkingCollapsed
      session.stream.streamingClearOnDone = false
      session.stream.streamingLastUpdatedAt = Date.now()
    },
    autoScrollPinnedSet(state, action: PayloadAction<{ agentId: string; pinned: boolean }>) {
      const session = ensureSession(state, action.payload.agentId)
      if (action.payload.pinned && session.timelineUi.pendingEvents.length) {
        session.timelineUi.autoScrollPinned = true
        session.timelineUi.hasUnseenActivity = false
        session.timelineUi.pendingEvents = []
        session.timelineUi.autoScrollPinSuppressedUntil = null
        return
      }
      session.timelineUi.autoScrollPinned = action.payload.pinned
      session.timelineUi.hasUnseenActivity = action.payload.pinned ? false : session.timelineUi.hasUnseenActivity
      session.timelineUi.autoScrollPinSuppressedUntil = action.payload.pinned ? null : session.timelineUi.autoScrollPinSuppressedUntil
    },
    autoScrollPinSuppressed(state, action: PayloadAction<{ agentId: string; durationMs: number }>) {
      const session = ensureSession(state, action.payload.agentId)
      const until = Date.now() + Math.max(0, action.payload.durationMs)
      if (!session.timelineUi.autoScrollPinSuppressedUntil || session.timelineUi.autoScrollPinSuppressedUntil < until) {
        session.timelineUi.autoScrollPinSuppressedUntil = until
      }
    },
    streamingThinkingCollapsedSet(state, action: PayloadAction<boolean>) {
      const session = getSession(state, state.activeAgentId)
      if (session) {
        session.stream.streamingThinkingCollapsed = action.payload
      }
    },
    realtimeEventCursorConsumed(state, action: PayloadAction<string>) {
      const session = getSession(state, state.activeAgentId)
      if (session) {
        delete session.timelineUi.realtimeEventCursorIds[action.payload]
      }
    },
    pendingEventsPersisted(state, action: PayloadAction<{ agentId: string }>) {
      const session = ensureSession(state, action.payload.agentId)
      session.timelineUi.pendingEvents = []
      session.timelineUi.hasUnseenActivity = false
    },
    insightsSetForAgent(state, action: PayloadAction<{ agentId: string; insights: InsightEvent[] }>) {
      if (state.activeAgentId !== action.payload.agentId) {
        return
      }
      const session = ensureSession(state, action.payload.agentId)
      session.insights.insightsById = Object.fromEntries(action.payload.insights.map((insight) => [insight.insightId, insight]))
      session.insights.insightIds = action.payload.insights.map((insight) => insight.insightId)
      session.insights.currentInsightIndex = 0
    },
    usageInsightUpdated(state, action: PayloadAction<{ agentId: string; metadata: BurnRateMetadata }>) {
      if (state.activeAgentId !== action.payload.agentId) {
        return
      }
      const session = ensureSession(state, action.payload.agentId)
      const existingId = session.insights.insightIds.find((id) => session.insights.insightsById[id]?.insightType === 'burn_rate')
      if (existingId) {
        const existing = session.insights.insightsById[existingId]
        session.insights.insightsById[existingId] = {
          ...existing,
          metadata: {
            ...(existing.metadata as BurnRateMetadata),
            ...action.payload.metadata,
          },
        }
        return
      }
      const insight: InsightEvent = {
        insightId: 'burn_rate_live',
        insightType: 'burn_rate',
        priority: 98,
        title: 'Credit usage',
        body: "Track today's agent usage and this month's account usage.",
        metadata: action.payload.metadata,
        dismissible: true,
      }
      session.insights.insightsById[insight.insightId] = insight
      session.insights.insightIds = [...session.insights.insightIds, insight.insightId]
        .sort((left, right) => session.insights.insightsById[right].priority - session.insights.insightsById[left].priority)
      session.insights.currentInsightIndex = Math.min(
        session.insights.currentInsightIndex,
        Math.max(0, session.insights.insightIds.length - 1),
      )
    },
    insightRotationStarted(state) {
      const session = getSession(state, state.activeAgentId)
      if (session) {
        session.insights.insightProcessingStartedAt = Date.now()
        session.insights.insightsPaused = false
      }
    },
    insightRotationStopped(state) {
      const session = getSession(state, state.activeAgentId)
      if (session) {
        session.insights.insightsPaused = false
      }
    },
    insightDismissed(state, action: PayloadAction<string>) {
      const session = getSession(state, state.activeAgentId)
      if (!session) return
      session.insights.dismissedInsightIds[action.payload] = true
      const availableCount = session.insights.insightIds.filter((id) => !session.insights.dismissedInsightIds[id]).length
      if (availableCount > 0) {
        session.insights.currentInsightIndex %= availableCount
      }
    },
    insightsPausedSet(state, action: PayloadAction<boolean>) {
      const session = getSession(state, state.activeAgentId)
      if (session) {
        session.insights.insightsPaused = action.payload
      }
    },
    currentInsightIndexSet(state, action: PayloadAction<number>) {
      const session = getSession(state, state.activeAgentId)
      if (!session) return
      const availableCount = session.insights.insightIds.filter((id) => !session.insights.dismissedInsightIds[id]).length
      if (availableCount === 0) return
      session.insights.currentInsightIndex = Math.max(0, Math.min(action.payload, availableCount - 1))
    },
  },
})

export const chatActions = chatSlice.actions
export const chatReducer = chatSlice.reducer

export {
  selectActiveChatAgentId,
  selectActiveChatSession,
  selectActiveChatStoreSnapshot,
  selectChatState,
  selectCurrentInsight,
} from './chatSelectors'
