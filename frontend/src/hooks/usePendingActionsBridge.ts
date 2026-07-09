import { useEffect } from 'react'

import type { TimelineResponse } from '../api/agentChat'
import { chatActions, normalizeProcessingUpdate } from '../store/chatSlice'
import { useAppDispatch } from '../store/hooks'
import type { PlanningState, SignupPreviewState } from '../types/agentRoster'

function normalizeSignupPreviewState(value: unknown): SignupPreviewState {
  return value === 'awaiting_first_reply_pause' || value === 'awaiting_signup_completion'
    ? value
    : 'none'
}

function normalizePlanningState(value: unknown): PlanningState {
  return value === 'planning' || value === 'completed' || value === 'skipped'
    ? value
    : 'skipped'
}

export function usePendingActionsBridge(
  activeAgentId: string | null,
  initialPageResponse: TimelineResponse | null,
) {
  const dispatch = useAppDispatch()

  useEffect(() => {
    if (!initialPageResponse || !activeAgentId) {
      return
    }

    const snapshot = initialPageResponse.processing_snapshot
    const processingActive = snapshot?.active ?? initialPageResponse.processing_active
    if (processingActive !== undefined) {
      dispatch(chatActions.processingUpdated({
        agentId: activeAgentId,
        snapshot: normalizeProcessingUpdate(snapshot ?? { active: processingActive, webTasks: [] }),
      }))
    }

    const name = initialPageResponse.agent_name ?? null
    const avatar = initialPageResponse.agent_avatar_url ?? null
    const signupPreviewState = normalizeSignupPreviewState(initialPageResponse.signup_preview_state)
    const planningState = normalizePlanningState(initialPageResponse.planning_state)
    if (name || avatar || signupPreviewState !== 'none' || planningState !== 'skipped') {
      dispatch(chatActions.agentIdentityUpdated({
        agentId: activeAgentId,
        ...(name ? { agentName: name } : {}),
        ...(avatar ? { agentAvatarUrl: avatar } : {}),
        signupPreviewState,
        planningState,
      }))
    }

    dispatch(chatActions.pendingActionsReplaced({
      agentId: activeAgentId,
      pendingActions: initialPageResponse.pending_action_requests ?? [],
    }))
  }, [activeAgentId, dispatch, initialPageResponse])
}
