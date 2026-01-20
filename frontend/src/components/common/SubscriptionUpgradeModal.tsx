import { useEffect, useCallback } from 'react'
import { createPortal } from 'react-dom'
import { X } from 'lucide-react'

import type { PlanTier } from '../../stores/subscriptionStore'
import { SubscriptionUpgradePlans } from './SubscriptionUpgradePlans'

type SubscriptionUpgradeModalProps = {
  currentPlan: PlanTier | null
  onClose: () => void
  onUpgrade: (plan: PlanTier) => void
  dismissible?: boolean
}

export function SubscriptionUpgradeModal({
  currentPlan,
  onClose,
  onUpgrade,
  dismissible = true,
}: SubscriptionUpgradeModalProps) {
  const handleClose = useCallback(() => {
    if (dismissible) {
      onClose()
    }
  }, [dismissible, onClose])

  useEffect(() => {
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && dismissible) {
        onClose()
      }
    }
    document.addEventListener('keydown', handleKey)
    const originalOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.removeEventListener('keydown', handleKey)
      document.body.style.overflow = originalOverflow
    }
  }, [dismissible, onClose])

  if (typeof document === 'undefined') {
    return null
  }

  return createPortal(
    <div className="fixed inset-0 z-50 overflow-y-auto">
      {/* Backdrop */}
      <div
        className="fixed inset-0 z-40 bg-slate-900/50 backdrop-blur-sm"
        onClick={handleClose}
        role="presentation"
        aria-hidden="true"
      />

      {/* Modal */}
      <div className="flex min-h-full items-start justify-center p-4 pb-20 sm:items-center sm:p-6">
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="upgrade-modal-title"
          className="relative z-50 w-full max-w-4xl transform overflow-hidden rounded-2xl bg-white shadow-2xl transition-all"
        >
          {/* Header */}
          <div className="border-b border-slate-100 px-6 py-5 sm:px-8">
            <div className="flex items-center justify-between">
              <div>
                <h2
                  id="upgrade-modal-title"
                  className="text-xl font-semibold text-slate-900"
                >
                  Upgrade your plan
                </h2>
                <p className="mt-1 text-sm text-slate-500">
                  Choose the plan that fits your needs
                </p>
              </div>
              {dismissible && (
                <button
                  type="button"
                  className="rounded-lg p-2 text-slate-400 hover:bg-slate-100 hover:text-slate-500"
                  onClick={onClose}
                  aria-label="Close dialog"
                >
                  <X className="h-5 w-5" strokeWidth={2} />
                </button>
              )}
            </div>
          </div>

          <SubscriptionUpgradePlans
            currentPlan={currentPlan}
            onUpgrade={onUpgrade}
          />
        </div>
      </div>
    </div>,
    document.body,
  )
}
