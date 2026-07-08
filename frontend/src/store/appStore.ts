import { combineReducers, configureStore, createListenerMiddleware, type UnknownAction } from '@reduxjs/toolkit'
import type { ThunkDispatch } from 'redux-thunk'
import type { QueryClient } from '@tanstack/react-query'

import { agentResourceStatusReducer } from './agentResourceStatusSlice'
import { agentRosterPreferencesReducer } from './agentRosterPreferencesSlice'
import { agentSettingsReducer } from './agentSettingsSlice'
import { auditReducer } from './auditSlice'
import { chatReducer } from './chatSlice'
import { immersiveShellReducer } from './immersiveShellSlice'
import { registerImmersiveShellListeners } from './listeners/immersiveShellListeners'
import { registerSubscriptionListeners } from './listeners/subscriptionListeners'
import { subscriptionReducer } from './subscriptionSlice'
import { usageReducer } from './usageSlice'

export type AppStoreExtra = {
  queryClient: QueryClient | null
}

const rootReducer = combineReducers({
  agentResourceStatus: agentResourceStatusReducer,
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

  registerSubscriptionListeners(listenerMiddleware)
  registerImmersiveShellListeners(listenerMiddleware)

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
