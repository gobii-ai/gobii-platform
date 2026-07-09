import type { ListenerMiddlewareInstance } from '@reduxjs/toolkit'

import { AnalyticsEvent } from '../../constants/analyticsEvents'
import { track } from '../../util/analytics'
import type { AppDispatch, AppStoreExtra, RootState } from '../appStore'
import { selectSubscriptionState, subscriptionActions } from '../subscriptionSlice'

export function registerSubscriptionListeners(
  listenerMiddleware: ListenerMiddlewareInstance<RootState, AppDispatch, AppStoreExtra>,
) {
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
}
