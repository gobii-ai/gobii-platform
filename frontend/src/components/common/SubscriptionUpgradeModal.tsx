import { useEffect, useCallback } from 'react'
import { createPortal } from 'react-dom'
import { X } from 'lucide-react'

import {
  isContinuationUpgradeModalSource,
  useSubscriptionStore,
  type PlanTier,
} from '../../stores/subscriptionStore'
import { track } from '../../util/analytics'
import { AnalyticsEvent } from '../../constants/analyticsEvents'
import { SubscriptionUpgradePlans } from './SubscriptionUpgradePlans'

type SubscriptionUpgradeModalProps = {
  onUpgrade: (plan: PlanTier) => void
  allowDowngrade?: boolean
}

export function SubscriptionUpgradeModal({
  onUpgrade,
  allowDowngrade = false,
}: SubscriptionUpgradeModalProps) {
  const {
    currentPlan,
    isProprietaryMode,
    upgradeModalSource,
    upgradeModalDismissible,
    trialDaysByPlan,
    trialEligible,
    pricingModalAlmostFullScreen,
    ctaPickAPlan,
    closeUpgradeModal,
  } = useSubscriptionStore()
  const source = upgradeModalSource ?? undefined
  const dismissible = upgradeModalDismissible
  const handleClose = useCallback(() => {
    if (!dismissible) {
      return
    }
    track(AnalyticsEvent.UPGRADE_MODAL_DISMISSED, {
      currentPlan,
      source: upgradeModalSource ?? 'unknown',
      isProprietaryMode,
    })
    closeUpgradeModal()
  }, [closeUpgradeModal, currentPlan, dismissible, isProprietaryMode, upgradeModalSource])

  const maxTrialDays = Math.max(trialDaysByPlan.startup, trialDaysByPlan.scale)
  const useTrialCopy = (
    trialEligible
    && !allowDowngrade
    && maxTrialDays > 0
    && (source === 'trial_onboarding' || currentPlan === 'free')
  )
  const useContinuationTitle = ctaPickAPlan && isContinuationUpgradeModalSource(source)
  const title = useContinuationTitle
    ? 'Finish what you just started'
    : useTrialCopy
      ? `Start ${maxTrialDays}-day Free Trial`
    : (allowDowngrade ? 'Change your plan' : 'Upgrade your plan')
  const subtitle = useTrialCopy
    ? 'Choose your plan to continue'
    : 'Choose the plan that fits your needs'

  useEffect(() => {
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && dismissible) {
        handleClose()
      }
    }
    document.addEventListener('keydown', handleKey)
    const originalOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.removeEventListener('keydown', handleKey)
      document.body.style.overflow = originalOverflow
    }
  }, [dismissible, handleClose])

  if (typeof document === 'undefined') {
    return null
  }

  const modalContainerClass = pricingModalAlmostFullScreen
    ? 'flex min-h-full items-center justify-center p-2 sm:p-4'
    : 'flex min-h-full items-start justify-center p-4 pb-20 sm:items-center sm:p-6'

  const modalClass = pricingModalAlmostFullScreen
    ? 'relative z-50 flex h-[94vh] w-full max-w-[96vw] transform flex-col overflow-hidden rounded-2xl bg-white shadow-2xl transition-all'
    : 'relative z-50 w-full max-w-4xl transform overflow-hidden rounded-2xl bg-white shadow-2xl transition-all'

  return createPortal(
    <div className="fixed inset-0 z-[100] overflow-y-auto">
      {/* Backdrop */}
      <div
        className="fixed inset-0 z-40 bg-slate-900/50 backdrop-blur-sm"
        onClick={handleClose}
        role="presentation"
        aria-hidden="true"
      />

      {/* Modal */}
      <div className={modalContainerClass}>
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="upgrade-modal-title"
          className={modalClass}
        >
          {/* Header */}
          <div className="border-b border-slate-100 px-6 py-5 sm:px-8">
            <div className="flex items-center justify-between">
              <div>
                <h2
                  id="upgrade-modal-title"
                  className="text-xl font-semibold text-slate-900"
                >
                  {title}
                </h2>
                <p className="mt-1 text-sm text-slate-500">
                  {subtitle}
                </p>
              </div>
              {dismissible && (
                <button
                  type="button"
                  className="rounded-lg p-2 text-slate-400 hover:bg-slate-100 hover:text-slate-500"
                  onClick={handleClose}
                  aria-label="Close dialog"
                >
                  <X className="h-5 w-5" strokeWidth={2} />
                </button>
              )}
            </div>
          </div>

          <div className={pricingModalAlmostFullScreen ? 'min-h-0 flex-1' : ''}>
            <SubscriptionUpgradePlans
              onUpgrade={onUpgrade}
              source={source}
              allowDowngrade={allowDowngrade}
            />
          </div>
        </div>
      </div>
    </div>,
    document.body,
  )
}
