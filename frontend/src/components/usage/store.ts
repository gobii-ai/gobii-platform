import { useMemo, useSyncExternalStore } from 'react'

import { selectUsageState, usageActions, type UsageState, type UsageStatus } from '../../store/usageSlice'
import type { AppDispatch } from '../../store/appStore'
import { useAppStore } from '../../store/hooks'
import type { UsageAgent, UsageSummaryResponse } from './types'

type UsageActions = {
  setSummaryLoading: () => void
  setSummaryData: (summary: UsageSummaryResponse) => void
  setSummaryError: (message: string) => void
  setAgentsLoading: () => void
  setAgentsData: (agents: UsageAgent[]) => void
  setAgentsError: (message: string) => void
  reset: () => void
}

type UsageStoreFacade = UsageState & UsageActions

function createUsageActions(dispatch: AppDispatch): UsageActions {
  return {
    setSummaryLoading: () => dispatch(usageActions.summaryLoading()),
    setSummaryData: (summary) => dispatch(usageActions.summaryLoaded(summary)),
    setSummaryError: (message) => dispatch(usageActions.summaryFailed(message)),
    setAgentsLoading: () => dispatch(usageActions.agentsLoading()),
    setAgentsData: (agents) => dispatch(usageActions.agentsLoaded(agents)),
    setAgentsError: (message) => dispatch(usageActions.agentsFailed(message)),
    reset: () => dispatch(usageActions.resetUsageState()),
  }
}

export function useUsageStore<T = UsageStoreFacade>(selector?: (state: UsageStoreFacade) => T): T {
  const store = useAppStore()
  const rootState = useSyncExternalStore(store.subscribe, store.getState, store.getState)
  const state = selectUsageState(rootState)
  const actions = useMemo(() => createUsageActions(store.dispatch), [store])
  const facade = useMemo(() => ({ ...state, ...actions }), [actions, state])
  return selector ? selector(facade) : (facade as T)
}

export type { UsageStatus }
