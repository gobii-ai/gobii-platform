import type { QueryClient } from '@tanstack/react-query'

import { normalizePendingActionRequests, normalizePendingHumanInputRequests } from '../api/agentChat'
import type { AgentMessageNotification, PendingActionRequest, ProcessingSnapshot, StreamEventPayload, TimelineEvent } from '../types/agentChat'
import type { PlanningState, SignupPreviewState } from '../types/agentRoster'
import type { BurnRateMetadata, UsageInsightUpdatePayload } from '../types/insight'
import {
  replacePendingActionRequestsInCache,
  replacePendingHumanInputRequestsInCache,
  updateAgentIdentityInCache,
} from './useTimelineCacheInjector'
import { extractAgentChatSocketEnvelopeAgentId } from './agentChatSocketProtocol'
import { nextClientStateOrder } from '../util/clientStateOrder'

type AgentIdentityUpdate = {
  agentId?: string | null
  agentName?: string | null
  agentAvatarUrl?: string | null
  emotion?: string | null
  emotionExpiresAt?: string | null
  agentNextScheduledAt?: string | null
  signupPreviewState?: SignupPreviewState | null
  planningState?: PlanningState | null
}

export type AgentChatSocketMessageOutcome =
  | { type: 'handled' }
  | { type: 'ignored' }
  | { type: 'pong' }
  | {
      type: 'subscription_ready'
      agentId: string
      mode: 'active' | 'background'
    }
  | {
      type: 'subscription_error'
      agentId: string | null
      message: string
    }

function normalizeSignupPreviewState(value: unknown): SignupPreviewState | null {
  return value === 'awaiting_first_reply_pause' || value === 'awaiting_signup_completion' || value === 'none'
    ? value
    : null
}

function normalizePlanningState(value: unknown): PlanningState | null {
  return value === 'planning' || value === 'completed' || value === 'skipped'
    ? value
    : null
}

function normalizeProcessingSnapshot(value: unknown): ProcessingSnapshot | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return null
  }
  const snapshot = value as Record<string, unknown>
  if (typeof snapshot.active !== 'boolean' || !Array.isArray(snapshot.webTasks)) {
    return null
  }
  if (
    Object.prototype.hasOwnProperty.call(snapshot, 'nextScheduledAt')
    && snapshot.nextScheduledAt !== null
    && typeof snapshot.nextScheduledAt !== 'string'
  ) {
    return null
  }
  return snapshot as ProcessingSnapshot
}

function buildAgentIdentityUpdate(payload: Record<string, unknown>): AgentIdentityUpdate {
  const nextIdentity: AgentIdentityUpdate = {}

  if (typeof payload.agent_id === 'string') {
    nextIdentity.agentId = payload.agent_id
  }
  if (Object.prototype.hasOwnProperty.call(payload, 'agent_name')) {
    nextIdentity.agentName = typeof payload.agent_name === 'string' ? payload.agent_name : null
  }
  if (Object.prototype.hasOwnProperty.call(payload, 'agent_avatar_url')) {
    nextIdentity.agentAvatarUrl = typeof payload.agent_avatar_url === 'string' ? payload.agent_avatar_url : null
  }
  if (Object.prototype.hasOwnProperty.call(payload, 'emotion')) {
    nextIdentity.emotion = typeof payload.emotion === 'string' ? payload.emotion : null
  }
  if (Object.prototype.hasOwnProperty.call(payload, 'emotion_expires_at')) {
    nextIdentity.emotionExpiresAt = typeof payload.emotion_expires_at === 'string'
      ? payload.emotion_expires_at
      : null
  }
  if (Object.prototype.hasOwnProperty.call(payload, 'signup_preview_state')) {
    nextIdentity.signupPreviewState = normalizeSignupPreviewState(payload.signup_preview_state)
  }
  if (Object.prototype.hasOwnProperty.call(payload, 'planning_state')) {
    nextIdentity.planningState = normalizePlanningState(payload.planning_state)
  }
  if (Object.prototype.hasOwnProperty.call(payload, 'agent_next_scheduled_at')) {
    nextIdentity.agentNextScheduledAt = typeof payload.agent_next_scheduled_at === 'string'
      ? payload.agent_next_scheduled_at
      : null
  }

  return nextIdentity
}

export function routeAgentChatSocketMessage({
  payload,
  queryClient,
  activeAgentId,
  receiveRealtimeEvent,
  updateProcessing,
  updateAgentIdentity,
  updateUsageInsight,
  receiveStreamEvent,
  replacePendingActions,
  onCreditEvent,
  onAgentProfileEvent,
  onMessageNotificationEvent,
  onDeveloperUpdate,
}: {
  payload: unknown
  queryClient: QueryClient
  activeAgentId: string | null
  receiveRealtimeEvent: (agentId: string, event: TimelineEvent) => void
  updateProcessing: (agentId: string, snapshot: ProcessingSnapshot) => void
  updateAgentIdentity: (update: AgentIdentityUpdate) => void
  updateUsageInsight: (agentId: string, metadata: BurnRateMetadata) => void
  receiveStreamEvent: (agentId: string, payload: StreamEventPayload) => void
  replacePendingActions?: ((agentId: string, pendingActions: PendingActionRequest[], stateOrder: number) => void) | null
  onCreditEvent?: ((payload: Record<string, unknown>) => void) | null
  onAgentProfileEvent?: ((payload: Record<string, unknown>) => void) | null
  onMessageNotificationEvent?: ((payload: AgentMessageNotification) => void) | null
  onDeveloperUpdate?: ((agentId: string) => void) | null
}): AgentChatSocketMessageOutcome {
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
    return { type: 'ignored' }
  }

  const message = payload as Record<string, unknown>
  const messageType = typeof message.type === 'string' ? message.type : null

  if (messageType === 'pong') {
    return { type: 'pong' }
  }

  if (messageType === 'subscription.error') {
    return {
      type: 'subscription_error',
      agentId: extractAgentChatSocketEnvelopeAgentId(message),
      message: typeof message.message === 'string' ? message.message : 'Subscription error.',
    }
  }

  if (messageType === 'subscription.ready') {
    const payloadAgentId = extractAgentChatSocketEnvelopeAgentId(message)
    const mode = message.mode === 'active' || message.mode === 'background' ? message.mode : null
    const readyPayload = message.payload
    if (
      !payloadAgentId
      || !mode
      || !readyPayload
      || typeof readyPayload !== 'object'
      || Array.isArray(readyPayload)
    ) {
      return { type: 'ignored' }
    }
    const readyRecord = readyPayload as Record<string, unknown>
    const processingSnapshot = normalizeProcessingSnapshot(readyRecord.processing_snapshot)
    if (!processingSnapshot || !Array.isArray(readyRecord.pending_action_requests)) {
      return { type: 'ignored' }
    }
    const stateOrder = nextClientStateOrder()
    const pendingActions = normalizePendingActionRequests(readyRecord.pending_action_requests)
    updateProcessing(payloadAgentId, processingSnapshot)
    replacePendingActionRequestsInCache(queryClient, payloadAgentId, pendingActions, stateOrder)
    replacePendingActions?.(payloadAgentId, pendingActions, stateOrder)
    return { type: 'subscription_ready', agentId: payloadAgentId, mode }
  }

  if (messageType === 'timeline.event' && message.payload) {
    const payloadAgentId = extractAgentChatSocketEnvelopeAgentId(message)
    if (payloadAgentId) {
      receiveRealtimeEvent(payloadAgentId, message.payload as TimelineEvent)
    }
    return { type: 'handled' }
  }

  if (messageType === 'developer.updated') {
    const payloadAgentId = extractAgentChatSocketEnvelopeAgentId(message)
    if (payloadAgentId) onDeveloperUpdate?.(payloadAgentId)
    return { type: 'handled' }
  }

  if (messageType === 'processing' && message.payload) {
    const payloadAgentId = extractAgentChatSocketEnvelopeAgentId(message)
    const processingRecord = message.payload as Record<string, unknown>
    const processingPayload = message.payload as ProcessingSnapshot
    if (payloadAgentId) {
      updateProcessing(payloadAgentId, processingPayload)
    }
    if (payloadAgentId === activeAgentId) {
      if (Object.prototype.hasOwnProperty.call(processingRecord, 'agent_next_scheduled_at')) {
        updateAgentIdentity({
          agentId: payloadAgentId,
          agentNextScheduledAt: typeof processingRecord.agent_next_scheduled_at === 'string'
            ? processingRecord.agent_next_scheduled_at
            : null,
        })
      }
    }
    return { type: 'handled' }
  }

  if (messageType === 'stream.event' && message.payload) {
    const payloadAgentId = extractAgentChatSocketEnvelopeAgentId(message)
    if (payloadAgentId) {
      receiveStreamEvent(payloadAgentId, message.payload as StreamEventPayload)
    }
    return { type: 'handled' }
  }

  if (messageType === 'agent.profile' && message.payload && typeof message.payload === 'object' && !Array.isArray(message.payload)) {
    const profilePayload = message.payload as Record<string, unknown>
    const payloadAgentId = extractAgentChatSocketEnvelopeAgentId(message)
    if (payloadAgentId) {
      updateAgentIdentityInCache(queryClient, payloadAgentId, profilePayload)
    }
    if (payloadAgentId === activeAgentId) {
      updateAgentIdentity(buildAgentIdentityUpdate(profilePayload))
    }
    onAgentProfileEvent?.(profilePayload)
    return { type: 'handled' }
  }

  if (
    messageType === 'human_input_requests.updated'
    && message.payload
    && typeof message.payload === 'object'
    && !Array.isArray(message.payload)
  ) {
    const payloadAgentId = extractAgentChatSocketEnvelopeAgentId(message)
    if (payloadAgentId) {
      const stateOrder = nextClientStateOrder()
      replacePendingHumanInputRequestsInCache(
        queryClient,
        payloadAgentId,
        normalizePendingHumanInputRequests(
          (message.payload as Record<string, unknown>).pending_human_input_requests,
        ),
        stateOrder,
      )
    }
    return { type: 'handled' }
  }

  if (
    messageType === 'pending_action_requests.updated'
    && message.payload
    && typeof message.payload === 'object'
    && !Array.isArray(message.payload)
  ) {
    const payloadAgentId = extractAgentChatSocketEnvelopeAgentId(message)
    if (payloadAgentId) {
      const stateOrder = nextClientStateOrder()
      const pendingActions = normalizePendingActionRequests(
        (message.payload as Record<string, unknown>).pending_action_requests,
      )
      replacePendingActionRequestsInCache(
        queryClient,
        payloadAgentId,
        pendingActions,
        stateOrder,
      )
      replacePendingActions?.(payloadAgentId, pendingActions, stateOrder)
    }
    return { type: 'handled' }
  }

  if (messageType === 'credit.event' && message.payload && typeof message.payload === 'object' && !Array.isArray(message.payload)) {
    onCreditEvent?.(message.payload as Record<string, unknown>)
    return { type: 'handled' }
  }

  if (messageType === 'usage.updated' && message.payload && typeof message.payload === 'object' && !Array.isArray(message.payload)) {
    const payloadAgentId = extractAgentChatSocketEnvelopeAgentId(message)
    const usagePayload = message.payload as UsageInsightUpdatePayload
    if (payloadAgentId && payloadAgentId === activeAgentId && usagePayload.metadata) {
      updateUsageInsight(payloadAgentId, usagePayload.metadata)
    }
    return { type: 'handled' }
  }

  if (
    messageType === 'message.notification'
    && message.payload
    && typeof message.payload === 'object'
    && !Array.isArray(message.payload)
  ) {
    onMessageNotificationEvent?.(message.payload as AgentMessageNotification)
    return { type: 'handled' }
  }

  return { type: 'ignored' }
}
