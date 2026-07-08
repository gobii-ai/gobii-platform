import { combineReducers, configureStore, createListenerMiddleware, type UnknownAction } from '@reduxjs/toolkit'
import type { ThunkDispatch } from 'redux-thunk'
import type { QueryClient } from '@tanstack/react-query'

import { agentResourceMirrorsReducer } from './agentResourceMirrorsSlice'
import { agentRosterPreferencesReducer } from './agentRosterPreferencesSlice'
import { agentSettingsReducer } from './agentSettingsSlice'
import { auditReducer } from './auditSlice'
import { chatReducer } from './chatSlice'
import {
  IMMERSIVE_SIDEBAR_MODE_STORAGE_KEY,
  immersiveShellActions,
  immersiveShellReducer,
} from './immersiveShellSlice'
import { selectSubscriptionState, subscriptionActions, subscriptionReducer } from './subscriptionSlice'
import { usageReducer } from './usageSlice'
import { track } from '../util/analytics'
import { AnalyticsEvent } from '../constants/analyticsEvents'

export type AppStoreExtra = {
  queryClient: QueryClient | null
}

const rootReducer = combineReducers({
  agentResourceMirrors: agentResourceMirrorsReducer,
  agentRosterPreferences: agentRosterPreferencesReducer,
  agentSettings: agentSettingsReducer,
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

  listenerMiddleware.startListening({
    actionCreator: immersiveShellActions.setSidebarMode,
    effect: (action) => {
      if (typeof window === 'undefined') {
        return
      }
      try {
        window.sessionStorage.setItem(IMMERSIVE_SIDEBAR_MODE_STORAGE_KEY, action.payload)
      } catch {
        // Storage failures should not affect shell interaction.
      }
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
