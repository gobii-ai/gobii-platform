import { createAsyncThunk, createSlice, type PayloadAction } from '@reduxjs/toolkit'
import type { InfiniteData, QueryClient } from '@tanstack/react-query'

import { sendAgentMessage, fetchProcessingStatus } from '../api/agentChat'
import { flushPendingEventsToCache, injectRealtimeEventIntoCache, refreshTimelineLatestInCache, updateOptimisticEventInCache, updateRosterProcessingInCache } from '../hooks/useTimelineCacheInjector'
import { timelineQueryKey, type TimelinePage } from '../hooks/useAgentTimeline'
import { mergeTimelineEvents, normalizeTimelineEvent } from '../stores/agentChatTimeline'
import type { AgentMessage, PendingActionRequest, ProcessingSnapshot, ProcessingWebTask, StreamEventPayload, StreamState, ThinkingEvent, TimelineEvent } from '../types/agentChat'
import type { PlanningState, SignupPreviewState } from '../types/agentRoster'
import type { BurnRateMetadata, InsightEvent } from '../types/insight'
import { fetchAgentSpawnIntent, type AgentSpawnIntent } from '../api/agentSpawnIntent'
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
  agentMiniDescription: string | null
  agentEmail: string | null
  agentSms: string | null
  agentNextScheduledAt: string | null
  agentIsOrgOwned: boolean
  canManageAgent: boolean
  canSendMessages: boolean
  isCollaborator: boolean
  hideInsightsPanel: boolean
  enabledIntegrationTabs: Record<string, true>
  signupPreviewState: SignupPreviewState
  planningState: PlanningState
}

export type AgentChatProcessingState = {
  processingActive: boolean
  processingStartedAt: number | null
  processingSource: 'none' | 'roster' | 'status' | 'realtime'
  processingLastRealtimeAt: number | null
  processingStatusRequestId: string | null
  awaitingResponse: boolean
  processingWebTasks: ProcessingWebTask[]
  nextScheduledAt: string | null
  stopProcessingBusy: boolean
  stopProcessingRequested: boolean
  skipPlanningBusy: boolean
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
  dismissedInsightIds: Record<string, true>
  insightsPaused: boolean
}

export type AgentChatWorkflowState = {
  sendMessageError: string | null
  pendingActions: PendingActionRequest[]
}

export type AgentChatSession = {
  identity: AgentChatIdentityState
  processing: AgentChatProcessingState
  stream: AgentChatStreamState
  timelineUi: AgentChatTimelineUiState
  insights: AgentChatInsightsState
  workflow: AgentChatWorkflowState
}

type AgentIdentityUpdateInput = {
  agentName?: string | null
  agentAvatarUrl?: string | null
  agentMiniDescription?: string | null
  agentEmail?: string | null
  agentSms?: string | null
  agentNextScheduledAt?: string | null
  agentIsOrgOwned?: boolean
  canManageAgent?: boolean
  canSendMessages?: boolean
  isCollaborator?: boolean
  hideInsightsPanel?: boolean
  enabledIntegrationTabs?: Record<string, boolean | true> | null
  signupPreviewState?: SignupPreviewState | null
  planningState?: PlanningState | null
}

export type SpawnIntentStatus = 'idle' | 'loading' | 'ready' | 'done'
export type TrialOnboardingTarget = Exclude<AgentSpawnIntent['onboarding_target'], null>

export type CreateAgentErrorState = {
  message: string
  showUpgradeCta: boolean
  requiresTrialPlanSelection: boolean
  trialOnboardingTarget: TrialOnboardingTarget | null
}

export type CreateAgentDraftMetadata = {
  body: string
  tier: string
  charterOverride?: string | null
  selectedPipedreamAppSlugs?: string[]
}

export type CreateAgentWorkflowState = {
  error: CreateAgentErrorState | null
  trialOnboardingTarget: TrialOnboardingTarget | null
  spawnIntent: AgentSpawnIntent | null
  spawnIntentStatus: SpawnIntentStatus
  spawnIntentRequestId: string | null
  draftMetadata: CreateAgentDraftMetadata | null
}

export type ChatState = {
  activeAgentId: string | null
  sessionsByAgentId: Record<string, AgentChatSession>
  createAgent: CreateAgentWorkflowState
}

export function createInitialSession(): AgentChatSession {
  return {
    identity: {
      agentName: null,
      agentAvatarUrl: null,
      agentMiniDescription: null,
      agentEmail: null,
      agentSms: null,
      agentNextScheduledAt: null,
      agentIsOrgOwned: false,
      canManageAgent: true,
      canSendMessages: true,
      isCollaborator: false,
      hideInsightsPanel: false,
      enabledIntegrationTabs: {},
      signupPreviewState: 'none',
      planningState: 'skipped',
    },
    processing: {
      processingActive: false,
      processingStartedAt: null,
      processingSource: 'none',
      processingLastRealtimeAt: null,
      processingStatusRequestId: null,
      awaitingResponse: false,
      processingWebTasks: [],
      nextScheduledAt: null,
      stopProcessingBusy: false,
      stopProcessingRequested: false,
      skipPlanningBusy: false,
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
      dismissedInsightIds: {},
      insightsPaused: false,
    },
    workflow: {
      sendMessageError: null,
      pendingActions: [],
    },
  }
}

export const initialChatState: ChatState = {
  activeAgentId: null,
  sessionsByAgentId: {},
  createAgent: {
    error: null,
    trialOnboardingTarget: null,
    spawnIntent: null,
    spawnIntentStatus: 'idle',
    spawnIntentRequestId: null,
    draftMetadata: null,
  },
}

function ensureSession(state: ChatState, agentId: string): AgentChatSession {
  if (!state.sessionsByAgentId[agentId]) {
    state.sessionsByAgentId[agentId] = createInitialSession()
  }
  return state.sessionsByAgentId[agentId]
}

function getSession(state: ChatState, agentId: string | null | undefined): AgentChatSession | null {
  return agentId ? state.sessionsByAgentId[agentId] ?? null : null
}

function applyIdentityUpdate(session: AgentChatSession, update: AgentIdentityUpdateInput | null | undefined) {
  if (update?.agentName !== undefined) {
    session.identity.agentName = update?.agentName ?? null
  }
  if (update?.agentAvatarUrl !== undefined) {
    session.identity.agentAvatarUrl = update?.agentAvatarUrl ?? null
  }
  if (update?.agentMiniDescription !== undefined) {
    session.identity.agentMiniDescription = update?.agentMiniDescription ?? null
  }
  if (update?.agentEmail !== undefined) {
    session.identity.agentEmail = update?.agentEmail ?? null
  }
  if (update?.agentSms !== undefined) {
    session.identity.agentSms = update?.agentSms ?? null
  }
  if (update?.agentNextScheduledAt !== undefined) {
    session.identity.agentNextScheduledAt = update?.agentNextScheduledAt ?? null
  }
  if (update?.agentIsOrgOwned !== undefined) {
    session.identity.agentIsOrgOwned = Boolean(update?.agentIsOrgOwned)
  }
  if (update?.canManageAgent !== undefined) {
    session.identity.canManageAgent = update?.canManageAgent ?? true
  }
  if (update?.canSendMessages !== undefined) {
    session.identity.canSendMessages = update?.canSendMessages ?? true
  }
  if (update?.isCollaborator !== undefined) {
    session.identity.isCollaborator = Boolean(update?.isCollaborator)
  }
  if (update?.hideInsightsPanel !== undefined) {
    session.identity.hideInsightsPanel = Boolean(update?.hideInsightsPanel)
  }
  if (update?.enabledIntegrationTabs !== undefined) {
    session.identity.enabledIntegrationTabs = normalizeEnabledIntegrationTabs(update?.enabledIntegrationTabs)
  }
  if (update?.signupPreviewState !== undefined) {
    session.identity.signupPreviewState = update?.signupPreviewState ?? 'none'
  }
  if (update?.planningState !== undefined) {
    session.identity.planningState = update?.planningState ?? 'skipped'
  }
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

function normalizeEnabledIntegrationTabs(tabs: Record<string, boolean | true> | null | undefined): Record<string, true> {
  if (!tabs) {
    return {}
  }
  return Object.fromEntries(
    Object.entries(tabs).filter(([, enabled]) => Boolean(enabled)).map(([key]) => [key, true]),
  )
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
  error?: string | null,
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
      error: error === undefined ? target.message.error ?? null : error,
    },
  }
  return next
}

function createOptimisticClientId(): string {
  const now = Date.now()
  return typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? `local-${crypto.randomUUID()}`
    : `local-${now}-${Math.random().toString(16).slice(2, 10)}`
}

function buildOptimisticMessageEvent(body: string, attachments: File[], requestedClientId?: string): { event: TimelineEvent; clientId: string } {
  const now = Date.now()
  const clientId = requestedClientId || createOptimisticClientId()
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

export const refreshProcessing = createAsyncThunk<void, { agentId: string }, { state: RootState; extra: { queryClient: QueryClient | null } }>(
  'chat/refreshProcessing',
  async ({ agentId }, { dispatch, getState, extra, requestId }) => {
    const requestedAt = Date.now()
    dispatch(chatActions.processingStatusRequested({ agentId, requestId }))
    try {
      const status = await fetchProcessingStatus(agentId)
      const {
        processing_active,
        processing_snapshot,
        signup_preview_state,
        planning_state,
      } = status
      const snapshot = normalizeProcessingUpdate(processing_snapshot ?? { active: processing_active, webTasks: [] })
      const hasNextScheduledAt = Object.prototype.hasOwnProperty.call(status, 'agent_next_scheduled_at')
      const lastRealtimeAt = getState().chat.sessionsByAgentId[agentId]?.processing.processingLastRealtimeAt ?? null
      const latestRequestId = getState().chat.sessionsByAgentId[agentId]?.processing.processingStatusRequestId ?? null
      if (latestRequestId === requestId && (lastRealtimeAt === null || lastRealtimeAt <= requestedAt)) {
        dispatch(chatActions.processingStatusUpdated({
          agentId,
          snapshot,
          requestedAt,
          requestId,
        }))
        if (extra.queryClient) {
          updateRosterProcessingInCache(extra.queryClient, agentId, snapshot.active)
        }
      }
      dispatch(chatActions.agentIdentityUpdated({
        agentId,
        ...(hasNextScheduledAt ? { agentNextScheduledAt: status.agent_next_scheduled_at ?? null } : {}),
        signupPreviewState: signup_preview_state ?? undefined,
        planningState: planning_state ?? undefined,
      }))
    } catch (error) {
      console.error('Failed to refresh processing status:', error)
    }
  },
)

export const updateRealtimeProcessing = (
  agentId: string,
  snapshotInput: ProcessingUpdateInput,
) => (dispatch: AppDispatch, _getState: () => RootState, extra?: { queryClient?: QueryClient | null }) => {
  const snapshot = normalizeProcessingUpdate(snapshotInput)
  dispatch(chatActions.processingRealtimeUpdated({ agentId, snapshot, receivedAt: Date.now() }))
  if (extra?.queryClient) {
    updateRosterProcessingInCache(extra.queryClient, agentId, snapshot.active)
  }
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
  agentId: string,
  event: TimelineEvent,
) => (dispatch: AppDispatch, getState: () => RootState, extra?: { queryClient?: QueryClient | null }) => {
  const normalized = normalizeTimelineEvent(event)
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
  agentId: string,
  payload: StreamEventPayload,
) => (dispatch: AppDispatch, getState: () => RootState, extra?: { queryClient?: QueryClient | null }) => {
  if (!payload?.stream_id) {
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

export const persistPendingEventsToCache = (agentId: string) => (
  dispatch: AppDispatch,
  getState: () => RootState,
  extra?: { queryClient?: QueryClient | null },
) => {
  if (!extra?.queryClient) {
    return
  }
  const pendingEvents = getState().chat.sessionsByAgentId[agentId]?.timelineUi.pendingEvents ?? []
  if (!pendingEvents.length) {
    return
  }
  flushPendingEventsToCache(extra.queryClient, agentId, pendingEvents)
  dispatch(chatActions.pendingEventsPersisted({ agentId }))
}

export const loadAgentSpawnIntent = createAsyncThunk<AgentSpawnIntent, void, { state: RootState }>(
  'chat/loadAgentSpawnIntent',
  async (_, { signal }) => fetchAgentSpawnIntent(signal),
)

export const sendMessage = createAsyncThunk<
  { clientId: string } | null,
  { body: string; attachments?: File[]; clientId?: string; retry?: boolean },
  { state: RootState; extra: { queryClient: QueryClient | null } }
>('chat/sendMessage', async ({ body, attachments = [], clientId: requestedClientId, retry = false }, { dispatch, getState, extra }) => {
  const agentId = getState().chat.activeAgentId
  if (!agentId) {
    throw new Error('Agent not initialized')
  }
  const trimmed = body.trim()
  if (!trimmed && attachments.length === 0) {
    return null
  }

  const { event, clientId } = buildOptimisticMessageEvent(trimmed, attachments, requestedClientId)
  dispatch(chatActions.messageSendStarted({ agentId }))
  if (retry) {
    if (extra.queryClient) {
      updateOptimisticEventInCache(extra.queryClient, agentId, clientId, 'sending', null)
    }
    dispatch(chatActions.optimisticMessageRetryStarted({ agentId, clientId }))
  } else {
    dispatch(receiveRealtimeEvent(agentId, event))
  }
  try {
    const serverEvent = await sendAgentMessage(agentId, trimmed, attachments)
    dispatch(receiveRealtimeEvent(agentId, serverEvent))
    return { clientId }
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
  const nextSession: AgentChatSession = {
    ...session,
    processing: { ...session.processing },
    stream: {
      ...session.stream,
      streaming: session.stream.streaming ? { ...session.stream.streaming } : null,
    },
    timelineUi: { ...session.timelineUi },
  }
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
    agentSelected: (
      state,
      action: PayloadAction<{
        agentId: string | null
        options?: AgentIdentityUpdateInput & { processingActive?: boolean }
      }>,
    ) => {
      const { agentId, options } = action.payload
      const selectionChanged = state.activeAgentId !== agentId
      state.activeAgentId = agentId
      if (!agentId) {
        return
      }
      const session = ensureSession(state, agentId)
      if (selectionChanged) {
        session.timelineUi.hasUnseenActivity = false
      }
      if (options?.processingActive !== undefined) {
        if (session.processing.processingSource === 'none' || session.processing.processingSource === 'roster') {
          applyProcessingUpdate(session, normalizeProcessingUpdate({
            active: Boolean(options.processingActive),
            webTasks: session.processing.processingWebTasks,
            nextScheduledAt: session.processing.nextScheduledAt,
          }))
          session.processing.processingSource = 'roster'
        }
      }
      applyIdentityUpdate(session, options)
    },
    processingStatusUpdated(state, action: PayloadAction<{
      agentId: string
      snapshot: ProcessingSnapshot
      requestedAt: number
      requestId: string
    }>) {
      const session = ensureSession(state, action.payload.agentId)
      if (
        session.processing.processingStatusRequestId !== action.payload.requestId
        || (
          session.processing.processingLastRealtimeAt !== null
          && session.processing.processingLastRealtimeAt > action.payload.requestedAt
        )
      ) {
        return
      }
      applyProcessingUpdate(session, action.payload.snapshot)
      session.processing.processingSource = 'status'
    },
    processingStatusRequested(state, action: PayloadAction<{ agentId: string; requestId: string }>) {
      ensureSession(state, action.payload.agentId).processing.processingStatusRequestId = action.payload.requestId
    },
    processingRealtimeUpdated(state, action: PayloadAction<{
      agentId: string
      snapshot: ProcessingSnapshot
      receivedAt: number
    }>) {
      const session = ensureSession(state, action.payload.agentId)
      applyProcessingUpdate(session, action.payload.snapshot)
      session.processing.processingSource = 'realtime'
      session.processing.processingLastRealtimeAt = action.payload.receivedAt
      session.processing.processingStatusRequestId = null
    },
    stopProcessingStateUpdated(
      state,
      action: PayloadAction<{ agentId: string; busy?: boolean; requested?: boolean }>,
    ) {
      const session = ensureSession(state, action.payload.agentId)
      if (Object.prototype.hasOwnProperty.call(action.payload, 'busy')) {
        session.processing.stopProcessingBusy = Boolean(action.payload.busy)
      }
      if (Object.prototype.hasOwnProperty.call(action.payload, 'requested')) {
        session.processing.stopProcessingRequested = Boolean(action.payload.requested)
      }
    },
    skipPlanningBusySet(state, action: PayloadAction<{ agentId: string; busy: boolean }>) {
      ensureSession(state, action.payload.agentId).processing.skipPlanningBusy = action.payload.busy
    },
    sendMessageErrorSet(state, action: PayloadAction<{ agentId?: string | null; message: string | null }>) {
      const agentId = action.payload.agentId ?? state.activeAgentId
      if (!agentId) return
      ensureSession(state, agentId).workflow.sendMessageError = action.payload.message
    },
    pendingActionsReplaced(state, action: PayloadAction<{ agentId: string; pendingActions: PendingActionRequest[] }>) {
      ensureSession(state, action.payload.agentId).workflow.pendingActions = action.payload.pendingActions
    },
    createAgentErrorSet(state, action: PayloadAction<CreateAgentErrorState | null>) {
      state.createAgent.error = action.payload
      state.createAgent.trialOnboardingTarget = action.payload?.trialOnboardingTarget ?? null
    },
    createAgentTrialOnboardingTargetSet(state, action: PayloadAction<TrialOnboardingTarget | null>) {
      state.createAgent.trialOnboardingTarget = action.payload
    },
    spawnIntentSet(state, action: PayloadAction<AgentSpawnIntent | null>) {
      state.createAgent.spawnIntent = action.payload
    },
    spawnIntentStatusSet(state, action: PayloadAction<SpawnIntentStatus>) {
      state.createAgent.spawnIntentStatus = action.payload
    },
    createAgentDraftMetadataSet(state, action: PayloadAction<CreateAgentDraftMetadata | null>) {
      state.createAgent.draftMetadata = action.payload
    },
    agentIdentityUpdated(
      state,
      action: PayloadAction<{
        agentId?: string | null
      } & AgentIdentityUpdateInput>,
    ) {
      const agentId = action.payload.agentId ?? state.activeAgentId
      if (!agentId) return
      applyIdentityUpdate(ensureSession(state, agentId), action.payload)
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
    optimisticMessageRetryStarted(state, action: PayloadAction<{ agentId: string; clientId: string }>) {
      const session = ensureSession(state, action.payload.agentId)
      session.timelineUi.pendingEvents = updateOptimisticStatus(
        session.timelineUi.pendingEvents,
        action.payload.clientId,
        'sending',
        null,
      )
      session.processing.awaitingResponse = true
      session.processing.processingStartedAt = Date.now()
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
  extraReducers: (builder) => {
    builder
      .addCase(loadAgentSpawnIntent.pending, (state, action) => {
        state.createAgent.spawnIntentRequestId = action.meta.requestId
        state.createAgent.spawnIntentStatus = 'loading'
        state.createAgent.spawnIntent = null
      })
      .addCase(loadAgentSpawnIntent.fulfilled, (state, action) => {
        if (state.createAgent.spawnIntentRequestId !== action.meta.requestId) {
          return
        }
        const intent = action.payload
        state.createAgent.spawnIntent = intent
        state.createAgent.spawnIntentStatus = intent.requires_plan_selection || Boolean(intent.charter?.trim())
          ? 'ready'
          : 'done'
      })
      .addCase(loadAgentSpawnIntent.rejected, (state, action) => {
        if (state.createAgent.spawnIntentRequestId !== action.meta.requestId) {
          return
        }
        state.createAgent.spawnIntentStatus = 'done'
      })
  },
})

export const chatActions = chatSlice.actions
export const chatReducer = chatSlice.reducer

export {
  selectActiveChatAgentId,
  selectActiveChatSession,
  selectCreateAgentWorkflow,
} from './chatSelectors'
