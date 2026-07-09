import type { ListenerMiddlewareInstance } from '@reduxjs/toolkit'

import type { AppDispatch, AppStoreExtra, RootState } from '../appStore'
import { IMMERSIVE_SIDEBAR_MODE_STORAGE_KEY, immersiveShellActions } from '../immersiveShellSlice'

export function registerImmersiveShellListeners(
  listenerMiddleware: ListenerMiddlewareInstance<RootState, AppDispatch, AppStoreExtra>,
) {
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
}
