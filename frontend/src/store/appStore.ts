import { combineReducers, configureStore, createListenerMiddleware, type UnknownAction } from '@reduxjs/toolkit'
import type { ThunkDispatch } from 'redux-thunk'
import type { QueryClient } from '@tanstack/react-query'

import { auditReducer } from './auditSlice'
import { chatReducer } from './chatSlice'
import { immersiveShellReducer } from './immersiveShellSlice'
import { selectSubscriptionState, subscriptionActions, subscriptionReducer } from './subscriptionSlice'
import { usageReducer } from './usageSlice'
import { track } from '../util/analytics'
import { AnalyticsEvent } from '../constants/analyticsEvents'

export type AppStoreExtra = {
  queryClient: QueryClient | null
}

const rootReducer = combineReducers({
  audit: auditReducer,
  chat: chatReducer,
  immersiveShell: immersiveShellReducer,
  subscription: subscriptionReducer,
  usage: usageReducer,
})

export type RootState = ReturnType<typeof rootReducer>
export type AppDispatch = ThunkDispatch<RootState, AppStoreExtra, UnknownAction>
export type AppStore = ReturnType<typeof configureAppStore>

function configureAppStore({ queryClient = null }: { queryClient?: QueryClient | null } = {}) {
  const listenerMiddleware = createListenerMiddleware<RootState, AppDispatch, AppStoreExtra>({
    extra: { queryClient },
  })

  listenerMiddleware.startListening({
    actionCreator: subscriptionActions.openUpgradeModal,
    effect: (action, listenerApi) => {
      const previousState = selectSubscriptionState(listenerApi.getOriginalState())
      if (previousState.isUpgradeModalOpen || typeof window === 'undefined') {
        return
      }
      track(AnalyticsEvent.UPGRADE_MODAL_OPENED, {
        currentPlan: previousState.currentPlan,
        source: action.payload?.source ?? 'unknown',
        isProprietaryMode: previousState.isProprietaryMode,
      })
    },
  })

  return configureStore({
    reducer: rootReducer,
    middleware: (getDefaultMiddleware) =>
      getDefaultMiddleware({
        thunk: {
          extraArgument: { queryClient },
        },
        serializableCheck: {
          ignoredActionPaths: ['meta.arg.attachments'],
        },
      }).prepend(listenerMiddleware.middleware),
  })
}

export function createAppStore({ queryClient = null }: { queryClient?: QueryClient | null } = {}) {
  return configureAppStore({ queryClient })
}
