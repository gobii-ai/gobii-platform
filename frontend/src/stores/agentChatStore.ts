import { useMemo, useSyncExternalStore } from 'react'

import {
  chatActions,
  normalizeProcessingUpdate,
  persistPendingEventsToCache as persistPendingEventsToCacheThunk,
  receiveRealtimeEvent as receiveRealtimeEventThunk,
  receiveStreamEvent as receiveStreamEventThunk,
  refreshProcessing as refreshProcessingThunk,
  selectActiveChatStoreSnapshot,
  selectCurrentInsight,
  sendMessage as sendMessageThunk,
  setAutoScrollPinned as setAutoScrollPinnedThunk,
  type ChatState,
} from '../store/chatSlice'
import type { AppDispatch, RootState } from '../store/appStore'
import { useAppStore } from '../store/hooks'
import type {
  ProcessingSnapshot,
  ProcessingWebTask,
  StreamEventPayload,
  StreamState,
  PendingActionRequest,
  TimelineEvent,
} from '../types/agentChat'
import type { BurnRateMetadata, InsightEvent } from '../types/insight'
import type { PlanningState, SignupPreviewState } from '../types/agentRoster'

type ProcessingUpdateInput = boolean | Partial<ProcessingSnapshot> | null | undefined

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
  stopProcessingBusy: boolean
  stopProcessingRequested: boolean
  skipPlanningBusy: boolean
  autoScrollPinned: boolean
  autoScrollPinSuppressedUntil: number | null
  pendingEvents: TimelineEvent[]
  realtimeEventCursors: Set<string>
  agentName: string | null
  agentAvatarUrl: string | null
  agentEmail: string | null
  agentSms: string | null
  auditUrl: string | null
  agentIsOrgOwned: boolean
  canManageAgent: boolean
  isCollaborator: boolean
  hideInsightsPanel: boolean
  enabledIntegrationTabs: Record<string, true>
  signupPreviewState: SignupPreviewState
  planningState: PlanningState
  insights: InsightEvent[]
  currentInsightIndex: number
  insightRotationTimer: ReturnType<typeof setTimeout> | null
  insightProcessingStartedAt: number | null
  dismissedInsightIds: Set<string>
  insightsPaused: boolean
  sendMessageError: string | null
  composerDisabledReason: string | null
  pendingActions: PendingActionRequest[]
  setAgentId: (
    agentId: string | null,
    options?: {
      agentName?: string | null
      agentAvatarUrl?: string | null
      agentEmail?: string | null
      agentSms?: string | null
      auditUrl?: string | null
      agentIsOrgOwned?: boolean
      canManageAgent?: boolean
      isCollaborator?: boolean
      hideInsightsPanel?: boolean
      enabledIntegrationTabs?: Record<string, boolean | true> | null
      processingActive?: boolean
      signupPreviewState?: SignupPreviewState | null
      planningState?: PlanningState | null
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
    agentAvatarUrl?: string | null
    agentEmail?: string | null
    agentSms?: string | null
    auditUrl?: string | null
    agentIsOrgOwned?: boolean
    canManageAgent?: boolean
    isCollaborator?: boolean
    hideInsightsPanel?: boolean
    enabledIntegrationTabs?: Record<string, boolean | true> | null
    signupPreviewState?: SignupPreviewState | null
    planningState?: PlanningState | null
  }) => void
  setAutoScrollPinned: (pinned: boolean) => void
  suppressAutoScrollPin: (durationMs?: number) => void
  setStreamingThinkingCollapsed: (collapsed: boolean) => void
  consumeRealtimeEventCursor: (cursor: string) => void
  persistPendingEventsToCache: () => void
  setInsightsForAgent: (agentId: string, insights: InsightEvent[]) => void
  updateUsageInsight: (agentId: string, metadata: BurnRateMetadata) => void
  startInsightRotation: () => void
  stopInsightRotation: () => void
  dismissInsight: (insightId: string) => void
  getCurrentInsight: () => InsightEvent | null
  setInsightsPaused: (paused: boolean) => void
  setCurrentInsightIndex: (index: number) => void
}

function createActions(dispatch: AppDispatch, getState: () => RootState): Omit<
  AgentChatState,
  | 'agentId'
  | 'streaming'
  | 'streamingLastUpdatedAt'
  | 'streamingClearOnDone'
  | 'streamingThinkingCollapsed'
  | 'hasUnseenActivity'
  | 'processingActive'
  | 'processingStartedAt'
  | 'awaitingResponse'
  | 'processingWebTasks'
  | 'nextScheduledAt'
  | 'stopProcessingBusy'
  | 'stopProcessingRequested'
  | 'skipPlanningBusy'
  | 'autoScrollPinned'
  | 'autoScrollPinSuppressedUntil'
  | 'pendingEvents'
  | 'realtimeEventCursors'
  | 'agentName'
  | 'agentAvatarUrl'
  | 'agentEmail'
  | 'agentSms'
  | 'auditUrl'
  | 'agentIsOrgOwned'
  | 'canManageAgent'
  | 'isCollaborator'
  | 'hideInsightsPanel'
  | 'enabledIntegrationTabs'
  | 'signupPreviewState'
  | 'planningState'
  | 'insights'
  | 'currentInsightIndex'
  | 'insightRotationTimer'
  | 'insightProcessingStartedAt'
  | 'dismissedInsightIds'
  | 'insightsPaused'
  | 'sendMessageError'
  | 'composerDisabledReason'
  | 'pendingActions'
> {
  return {
    setAgentId: (agentId, options) => dispatch(chatActions.agentSelected({ agentId, options })),
    refreshProcessing: async () => {
      await dispatch(refreshProcessingThunk()).unwrap()
    },
    sendMessage: async (body, attachments = []) => {
      await dispatch(sendMessageThunk({ body, attachments })).unwrap()
    },
    receiveRealtimeEvent: (event) => dispatch(receiveRealtimeEventThunk(event)),
    receiveStreamEvent: (payload) => dispatch(receiveStreamEventThunk(payload)),
    finalizeStreaming: () => dispatch(chatActions.streamingFinalized()),
    updateProcessing: (snapshot) => {
      const agentId = getState().chat.activeAgentId
      if (agentId) {
        dispatch(chatActions.processingUpdated({ agentId, snapshot: normalizeProcessingUpdate(snapshot) }))
      }
    },
    updateAgentIdentity: (update) => dispatch(chatActions.agentIdentityUpdated(update)),
    setAutoScrollPinned: (pinned) => dispatch(setAutoScrollPinnedThunk(pinned)),
    suppressAutoScrollPin: (durationMs = 1000) => {
      const agentId = getState().chat.activeAgentId
      if (agentId) {
        dispatch(chatActions.autoScrollPinSuppressed({ agentId, durationMs }))
      }
    },
    setStreamingThinkingCollapsed: (collapsed) => dispatch(chatActions.streamingThinkingCollapsedSet(collapsed)),
    consumeRealtimeEventCursor: (cursor) => dispatch(chatActions.realtimeEventCursorConsumed(cursor)),
    persistPendingEventsToCache: () => dispatch(persistPendingEventsToCacheThunk()),
    setInsightsForAgent: (agentId, insights) => dispatch(chatActions.insightsSetForAgent({ agentId, insights })),
    updateUsageInsight: (agentId, metadata) => dispatch(chatActions.usageInsightUpdated({ agentId, metadata })),
    startInsightRotation: () => dispatch(chatActions.insightRotationStarted()),
    stopInsightRotation: () => dispatch(chatActions.insightRotationStopped()),
    dismissInsight: (insightId) => dispatch(chatActions.insightDismissed(insightId)),
    getCurrentInsight: () => selectCurrentInsight(getState()),
    setInsightsPaused: (paused) => dispatch(chatActions.insightsPausedSet(paused)),
    setCurrentInsightIndex: (index) => dispatch(chatActions.currentInsightIndexSet(index)),
  }
}

export function useAgentChatStore<T = AgentChatState>(selector?: (state: AgentChatState) => T): T {
  const store = useAppStore()
  const rootState = useSyncExternalStore(store.subscribe, store.getState, store.getState)
  const snapshot = selectActiveChatStoreSnapshot(rootState)
  const actions = useMemo(() => createActions(store.dispatch, () => store.getState()), [store])
  const facade = useMemo(() => ({ ...snapshot, ...actions }), [actions, snapshot])
  return selector ? selector(facade) : (facade as T)
}

export type { ChatState }
