import { useMemo, useSyncExternalStore } from 'react'

import {
  auditActions,
  initializeAudit,
  jumpAuditToTime,
  loadAuditTimeline,
  loadMoreAudit,
  selectAuditState,
  type AuditState,
} from '../store/auditSlice'
import type { AppDispatch } from '../store/appStore'
import { useAppStore } from '../store/hooks'

type AuditActions = {
  initialize: (agentId: string) => Promise<void>
  loadMore: () => Promise<void>
  loadTimeline: (agentId: string) => Promise<void>
  jumpToTime: (day: string) => Promise<void>
  setSelectedDay: (day: string | null) => void
  receiveRealtimeEvent: (payload: any) => void
  setProcessingActive: (active: boolean) => void
}

type AuditStoreFacade = AuditState & AuditActions

function createAuditActions(dispatch: AppDispatch): AuditActions {
  return {
    initialize: async (agentId) => {
      await dispatch(initializeAudit(agentId)).unwrap()
    },
    loadMore: async () => {
      await dispatch(loadMoreAudit()).unwrap()
    },
    loadTimeline: async (agentId) => {
      await dispatch(loadAuditTimeline(agentId)).unwrap()
    },
    jumpToTime: async (day) => {
      await dispatch(jumpAuditToTime(day)).unwrap()
    },
    setSelectedDay: (day) => dispatch(auditActions.setSelectedDay(day)),
    receiveRealtimeEvent: (payload) => dispatch(auditActions.receiveRealtimeEvent(payload)),
    setProcessingActive: (active) => dispatch(auditActions.setProcessingActive(active)),
  }
}

export function useAgentAuditStore<T = AuditStoreFacade>(selector?: (state: AuditStoreFacade) => T): T {
  const store = useAppStore()
  const rootState = useSyncExternalStore(store.subscribe, store.getState, store.getState)
  const state = selectAuditState(rootState)
  const actions = useMemo(() => createAuditActions(store.dispatch), [store])
  const facade = useMemo(() => ({ ...state, ...actions }), [actions, state])
  return selector ? selector(facade) : (facade as T)
}
