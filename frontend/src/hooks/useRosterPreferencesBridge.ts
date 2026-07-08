import { useEffect } from 'react'

import type { fetchAgentRoster } from '../api/agents'
import { agentRosterPreferencesActions } from '../store/agentRosterPreferencesSlice'
import { useAppDispatch } from '../store/hooks'

type AgentRosterPreferencesSource = Pick<
  Awaited<ReturnType<typeof fetchAgentRoster>>,
  | 'agentRosterSortMode'
  | 'favoriteAgentIds'
  | 'mutedAgentIds'
  | 'insightsPanelExpanded'
  | 'agentChatNotificationsEnabled'
>

export function useRosterPreferencesBridge(rosterData: AgentRosterPreferencesSource | null | undefined) {
  const dispatch = useAppDispatch()

  useEffect(() => {
    if (!rosterData) {
      return
    }
    dispatch(agentRosterPreferencesActions.hydratedFromRoster({
      sortMode: rosterData.agentRosterSortMode,
      favoriteAgentIds: rosterData.favoriteAgentIds,
      mutedAgentIds: rosterData.mutedAgentIds,
      insightsPanelExpanded: rosterData.insightsPanelExpanded,
      agentChatNotificationsEnabled: rosterData.agentChatNotificationsEnabled,
    }))
  }, [dispatch, rosterData])
}
