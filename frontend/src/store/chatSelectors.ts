import type { RootState } from './appStore'
import type { AgentChatSession, ChatState } from './chatSlice'
import { INSIGHT_TIMING, type InsightEvent } from '../types/insight'

function createEmptySession(): AgentChatSession {
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

export const selectChatState = (state: RootState): ChatState => state.chat
export const selectActiveChatAgentId = (state: RootState): string | null => state.chat.activeAgentId
export const selectActiveChatSession = (state: RootState): AgentChatSession => {
  const agentId = state.chat.activeAgentId
  return agentId ? state.chat.sessionsByAgentId[agentId] ?? createEmptySession() : createEmptySession()
}

export const selectCurrentInsight = (state: RootState): InsightEvent | null => {
  const session = selectActiveChatSession(state)
  const processingStartedAt = session.insights.insightProcessingStartedAt
  if (processingStartedAt && Date.now() - processingStartedAt < INSIGHT_TIMING.showAfterMs) {
    return null
  }
  const availableIds = session.insights.insightIds.filter((id) => !session.insights.dismissedInsightIds[id])
  if (!availableIds.length) {
    return null
  }
  return session.insights.insightsById[availableIds[session.insights.currentInsightIndex % availableIds.length]] ?? null
}

export function selectActiveChatStoreSnapshot(state: RootState) {
  const session = selectActiveChatSession(state)
  return {
    agentId: state.chat.activeAgentId,
    streaming: session.stream.streaming,
    streamingLastUpdatedAt: session.stream.streamingLastUpdatedAt,
    streamingClearOnDone: session.stream.streamingClearOnDone,
    streamingThinkingCollapsed: session.stream.streamingThinkingCollapsed,
    hasUnseenActivity: session.timelineUi.hasUnseenActivity,
    processingActive: session.processing.processingActive,
    processingStartedAt: session.processing.processingStartedAt,
    awaitingResponse: session.processing.awaitingResponse,
    processingWebTasks: session.processing.processingWebTasks,
    nextScheduledAt: session.processing.nextScheduledAt,
    autoScrollPinned: session.timelineUi.autoScrollPinned,
    autoScrollPinSuppressedUntil: session.timelineUi.autoScrollPinSuppressedUntil,
    pendingEvents: session.timelineUi.pendingEvents,
    realtimeEventCursors: new Set(Object.keys(session.timelineUi.realtimeEventCursorIds)),
    agentName: session.identity.agentName,
    agentAvatarUrl: session.identity.agentAvatarUrl,
    signupPreviewState: session.identity.signupPreviewState,
    planningState: session.identity.planningState,
    insights: session.insights.insightIds.map((id) => session.insights.insightsById[id]).filter(Boolean),
    currentInsightIndex: session.insights.currentInsightIndex,
    insightRotationTimer: null,
    insightProcessingStartedAt: session.insights.insightProcessingStartedAt,
    dismissedInsightIds: new Set(Object.keys(session.insights.dismissedInsightIds)),
    insightsPaused: session.insights.insightsPaused,
  }
}
