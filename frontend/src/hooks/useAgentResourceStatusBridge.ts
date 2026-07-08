import { useEffect } from 'react'

import { safeErrorMessage } from '../api/safeErrorMessage'
import { agentResourceStatusActions } from '../store/agentResourceStatusSlice'
import { useAppDispatch } from '../store/hooks'

type AgentResourceQueryState<TData> = {
  data: TData | null | undefined
  isLoading: boolean
  error: unknown
}

type AgentMutationState = {
  updating: boolean
}

type UseAgentResourceStatusBridgeOptions<TQuickSettings, TAddons, TUsageSummary> = {
  activeAgentId: string | null
  quickSettings: AgentResourceQueryState<TQuickSettings> & AgentMutationState
  addons: AgentResourceQueryState<TAddons> & AgentMutationState
  usageSummary: AgentResourceQueryState<TUsageSummary> & {
    enabled: boolean
  }
}

export function useAgentResourceStatusBridge<TQuickSettings, TAddons, TUsageSummary>({
  activeAgentId,
  quickSettings,
  addons,
  usageSummary,
}: UseAgentResourceStatusBridgeOptions<TQuickSettings, TAddons, TUsageSummary>) {
  const dispatch = useAppDispatch()

  useEffect(() => {
    if (!activeAgentId) {
      return
    }
    if (quickSettings.isLoading) {
      dispatch(agentResourceStatusActions.quickSettingsLoading(activeAgentId))
    } else if (quickSettings.error) {
      dispatch(agentResourceStatusActions.quickSettingsFailed({
        agentId: activeAgentId,
        message: safeErrorMessage(quickSettings.error, 'Unable to load daily credits.'),
      }))
    } else if (quickSettings.data !== undefined) {
      dispatch(agentResourceStatusActions.quickSettingsLoaded(activeAgentId))
    }
  }, [activeAgentId, dispatch, quickSettings.data, quickSettings.error, quickSettings.isLoading])

  useEffect(() => {
    if (activeAgentId) {
      dispatch(agentResourceStatusActions.quickSettingsUpdatingSet({
        agentId: activeAgentId,
        updating: quickSettings.updating,
      }))
    }
  }, [activeAgentId, dispatch, quickSettings.updating])

  useEffect(() => {
    if (!activeAgentId) {
      return
    }
    if (addons.isLoading) {
      dispatch(agentResourceStatusActions.addonsLoading(activeAgentId))
    } else if (addons.error) {
      dispatch(agentResourceStatusActions.addonsFailed({
        agentId: activeAgentId,
        message: safeErrorMessage(addons.error, 'Unable to load add-ons.'),
      }))
    } else if (addons.data !== undefined) {
      dispatch(agentResourceStatusActions.addonsLoaded(activeAgentId))
    }
  }, [activeAgentId, addons.data, addons.error, addons.isLoading, dispatch])

  useEffect(() => {
    if (activeAgentId) {
      dispatch(agentResourceStatusActions.addonsUpdatingSet({
        agentId: activeAgentId,
        updating: addons.updating,
      }))
    }
  }, [activeAgentId, addons.updating, dispatch])

  useEffect(() => {
    if (!usageSummary.enabled) {
      return
    }
    if (usageSummary.isLoading) {
      dispatch(agentResourceStatusActions.agentChatUsageSummaryLoading())
    } else if (usageSummary.error) {
      dispatch(agentResourceStatusActions.agentChatUsageSummaryFailed(
        safeErrorMessage(usageSummary.error, 'Unable to load usage summary.'),
      ))
    } else if (usageSummary.data !== undefined) {
      dispatch(agentResourceStatusActions.agentChatUsageSummaryLoaded())
    }
  }, [dispatch, usageSummary.data, usageSummary.enabled, usageSummary.error, usageSummary.isLoading])
}
