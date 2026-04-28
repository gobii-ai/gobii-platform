import type { QueryClient } from '@tanstack/react-query'

import { normalizePendingActionRequests, normalizePendingHumanInputRequests } from '../api/agentChat'
import type { AgentMessageNotification, ProcessingSnapshot, StreamEventPayload, TimelineEvent } from '../types/agentChat'
import type { PlanningState, SignupPreviewState } from '../types/agentRoster'
import {
  injectRealtimeEventIntoCache,
  replacePendingActionRequestsInCache,
  replacePendingHumanInputRequestsInCache,
  replaceProcessingSnapshotInCache,
  updateAgentIdentityInCache,
} from './useTimelineCacheInjector'
import { extractAgentChatSocketEnvelopeAgentId } from './agentChatSocketProtocol'

type AgentIdentityUpdate = {
  agentId?: string | null
  agentName?: string | null
  agentColorHex?: string | null
  agentAvatarUrl?: string | null
  signupPreviewState?: SignupPreviewState | null
  planningState?: PlanningState | null
}

export type AgentChatSocketMessageOutcome =
  | { type: 'handled' }
  | { type: 'ignored' }
  | { type: 'pong' }
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

function buildAgentIdentityUpdate(payload: Record<string, unknown>): AgentIdentityUpdate {
  const nextIdentity: AgentIdentityUpdate = {}

  if (typeof payload.agent_id === 'string') {
    nextIdentity.agentId = payload.agent_id
  }
  if (Object.prototype.hasOwnProperty.call(payload, 'agent_name')) {
    nextIdentity.agentName = typeof payload.agent_name === 'string' ? payload.agent_name : null
  }
  if (Object.prototype.hasOwnProperty.call(payload, 'agent_color_hex')) {
    nextIdentity.agentColorHex = typeof payload.agent_color_hex === 'string' ? payload.agent_color_hex : null
  }
  if (Object.prototype.hasOwnProperty.call(payload, 'agent_avatar_url')) {
    nextIdentity.agentAvatarUrl = typeof payload.agent_avatar_url === 'string' ? payload.agent_avatar_url : null
  }
  if (Object.prototype.hasOwnProperty.call(payload, 'signup_preview_state')) {
    nextIdentity.signupPreviewState = normalizeSignupPreviewState(payload.signup_preview_state)
  }
  if (Object.prototype.hasOwnProperty.call(payload, 'planning_state')) {
    nextIdentity.planningState = normalizePlanningState(payload.planning_state)
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
  receiveStreamEvent,
  onCreditEvent,
  onAgentProfileEvent,
  onMessageNotificationEvent,
}: {
  payload: unknown
  queryClient: QueryClient
  activeAgentId: string | null
  receiveRealtimeEvent: (event: TimelineEvent) => void
  updateProcessing: (snapshot: ProcessingSnapshot) => void
  updateAgentIdentity: (update: AgentIdentityUpdate) => void
  receiveStreamEvent: (payload: StreamEventPayload) => void
  onCreditEvent?: ((payload: Record<string, unknown>) => void) | null
  onAgentProfileEvent?: ((payload: Record<string, unknown>) => void) | null
  onMessageNotificationEvent?: ((payload: AgentMessageNotification) => void) | null
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

  if (messageType === 'timeline.event' && message.payload) {
    const payloadAgentId = extractAgentChatSocketEnvelopeAgentId(message)
    if (payloadAgentId === activeAgentId) {
      receiveRealtimeEvent(message.payload as TimelineEvent)
    } else if (payloadAgentId) {
      injectRealtimeEventIntoCache(queryClient, payloadAgentId, message.payload as TimelineEvent)
    }
    return { type: 'handled' }
  }

  if (messageType === 'processing' && message.payload) {
    const payloadAgentId = extractAgentChatSocketEnvelopeAgentId(message)
    const processingPayload = message.payload as ProcessingSnapshot
    if (payloadAgentId) {
      replaceProcessingSnapshotInCache(queryClient, payloadAgentId, processingPayload)
    }
    if (payloadAgentId === activeAgentId) {
      updateProcessing(processingPayload)
    }
    return { type: 'handled' }
  }

  if (messageType === 'stream.event' && message.payload) {
    if (extractAgentChatSocketEnvelopeAgentId(message) === activeAgentId) {
      receiveStreamEvent(message.payload as StreamEventPayload)
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
      replacePendingHumanInputRequestsInCache(
        queryClient,
        payloadAgentId,
        normalizePendingHumanInputRequests(
          (message.payload as Record<string, unknown>).pending_human_input_requests,
        ),
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
      replacePendingActionRequestsInCache(
        queryClient,
        payloadAgentId,
        normalizePendingActionRequests(
          (message.payload as Record<string, unknown>).pending_action_requests,
        ),
      )
    }
    return { type: 'handled' }
  }

  if (messageType === 'credit.event' && message.payload && typeof message.payload === 'object' && !Array.isArray(message.payload)) {
    onCreditEvent?.(message.payload as Record<string, unknown>)
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
