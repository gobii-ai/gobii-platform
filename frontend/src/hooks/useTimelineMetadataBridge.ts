import { useEffect } from 'react'

import type { TimelineResponse } from '../api/agentChat'
import { chatActions } from '../store/chatSlice'
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

export function useTimelineMetadataBridge(
  activeAgentId: string | null,
  initialPageResponse: TimelineResponse | null,
) {
  const dispatch = useAppDispatch()

  useEffect(() => {
    if (!initialPageResponse || !activeAgentId) {
      return
    }

    const name = initialPageResponse.agent_name ?? null
    const avatar = initialPageResponse.agent_avatar_url ?? null
    const nextScheduledAt = initialPageResponse.agent_next_scheduled_at ?? null
    const hasNextScheduledAt = Object.prototype.hasOwnProperty.call(
      initialPageResponse,
      'agent_next_scheduled_at',
    )
    const signupPreviewState = normalizeSignupPreviewState(initialPageResponse.signup_preview_state)
    const planningState = normalizePlanningState(initialPageResponse.planning_state)
    if (name || avatar || hasNextScheduledAt || signupPreviewState !== 'none' || planningState !== 'skipped') {
      dispatch(chatActions.agentIdentityUpdated({
        agentId: activeAgentId,
        ...(name ? { agentName: name } : {}),
        ...(avatar ? { agentAvatarUrl: avatar } : {}),
        ...(hasNextScheduledAt ? { agentNextScheduledAt: nextScheduledAt } : {}),
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
