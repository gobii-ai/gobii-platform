import { useEffect } from 'react'

import type { TimelineResponse } from '../api/agentChat'
import { chatActions, normalizeProcessingUpdate } from '../store/chatSlice'
import { useAppDispatch } from '../store/hooks'

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
    const signupPreviewState = initialPageResponse.signup_preview_state ?? 'none'
    const planningState = initialPageResponse.planning_state ?? 'skipped'
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
