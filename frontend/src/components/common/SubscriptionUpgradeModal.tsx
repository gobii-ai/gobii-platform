import { useEffect, useCallback } from 'react'
import { createPortal } from 'react-dom'
import { X, Check, Zap, Rocket } from 'lucide-react'
import type { PlanTier } from '../../stores/subscriptionStore'
import { track } from '../../util/analytics'
import { AnalyticsEvent } from '../../constants/analyticsEvents'

type PlanConfig = {
  id: PlanTier
  name: string
  price: string
  priceSubtext: string
  description: string
  features: string[]
  highlight?: boolean
  badge?: string
}

const PLANS: PlanConfig[] = [
  {
    id: 'free',
    name: 'Free',
    price: '$0',
    priceSubtext: 'Free to start',
    description: 'Get started with core features',
    features: [
      '100 tasks (one-time)',
      '3 contacts per agent',
      '5 always-on agents',
      'Agents run up to 30 days',
      'Basic API access',
      'Community support',
    ],
  },
  {
    id: 'startup',
    name: 'Pro',
    price: '$50',
    priceSubtext: 'per month',
    description: 'For growing teams',
    badge: 'Most popular',
    features: [
      '500 tasks included',
      '10 contacts per agent',
      'Unlimited always-on agents',
      'Agents never expire',
      '$0.10 per task beyond 500',
      'Priority support',
      'Higher rate limits',
    ],
  },
  {
    id: 'scale',
    name: 'Scale',
    price: '$250',
    priceSubtext: 'per month',
    description: 'For teams scaling fast',
    highlight: true,
    badge: 'Best value',
    features: [
      '10,000 tasks included',
      '50 contacts per agent',
      'Unlimited always-on agents',
      'Agents never expire',
      '$0.04 per task beyond 10k',
      'Priority work queue',
      '1,500 req/min API throughput',
    ],
  },
]

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

  const isCurrentPlan = (planId: PlanTier) => currentPlan === planId
  const isUpgrade = (planId: PlanTier) => {
    if (!currentPlan) return planId !== 'free'
    const order: PlanTier[] = ['free', 'startup', 'scale']
    return order.indexOf(planId) > order.indexOf(currentPlan)
  }

  const handlePlanSelect = useCallback((planId: PlanTier) => {
    track(AnalyticsEvent.UPGRADE_PLAN_SELECTED, {
      currentPlan,
      selectedPlan: planId,
    })
    onUpgrade(planId)
  }, [currentPlan, onUpgrade])

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

          {/* Plan cards */}
          <div className="px-6 py-6 sm:px-8">
            <div className="grid gap-4 sm:grid-cols-3">
              {PLANS.map((plan) => {
                const isCurrent = isCurrentPlan(plan.id)
                const canUpgrade = isUpgrade(plan.id)

                return (
                  <div
                    key={plan.id}
                    className={`relative flex flex-col rounded-xl border p-5 ${
                      plan.highlight
                        ? 'border-blue-200 bg-gradient-to-br from-white via-blue-50/30 to-indigo-50/30 shadow-md'
                        : 'border-slate-200 bg-white'
                    } ${isCurrent ? 'ring-2 ring-blue-500 ring-offset-2' : ''}`}
                  >
                    {/* Badge */}
                    {plan.badge && (
                      <div
                        className={`absolute -top-2.5 left-1/2 -translate-x-1/2 rounded-full px-3 py-0.5 text-xs font-semibold ${
                          plan.highlight
                            ? 'bg-blue-600 text-white'
                            : 'bg-slate-100 text-slate-600'
                        }`}
                      >
                        {plan.badge}
                      </div>
                    )}

                    {/* Plan header */}
                    <div className="mb-4 text-center">
                      <h3 className="text-lg font-semibold text-slate-900">
                        {plan.name}
                      </h3>
                      <p className="mt-1 text-sm text-slate-500">
                        {plan.description}
                      </p>
                    </div>

                    {/* Price */}
                    <div className="mb-4 text-center">
                      <span className="text-3xl font-semibold text-slate-900">
                        {plan.price}
                      </span>
                      <span className="ml-1 text-sm text-slate-500">
                        {plan.priceSubtext}
                      </span>
                    </div>

                    {/* Features */}
                    <ul className="mb-6 flex-1 space-y-2">
                      {plan.features.map((feature, idx) => (
                        <li
                          key={idx}
                          className="flex items-start gap-2 text-sm text-slate-600"
                        >
                          <Check
                            className="mt-0.5 h-4 w-4 flex-shrink-0 text-blue-500"
                            strokeWidth={2}
                          />
                          <span>{feature}</span>
                        </li>
                      ))}
                    </ul>

                    {/* CTA */}
                    {isCurrent ? (
                      <span className="inline-flex w-full items-center justify-center rounded-lg bg-slate-100 px-4 py-2.5 text-sm font-semibold text-slate-500">
                        Current plan
                      </span>
                    ) : canUpgrade ? (
                      <button
                        type="button"
                        onClick={() => handlePlanSelect(plan.id)}
                        className={`inline-flex w-full items-center justify-center gap-2 rounded-lg px-4 py-2.5 text-sm font-semibold transition ${
                          plan.highlight
                            ? 'bg-blue-600 text-white shadow-md hover:bg-blue-700'
                            : 'border border-slate-200 bg-white text-slate-700 hover:bg-slate-50 hover:text-blue-600'
                        }`}
                      >
                        {plan.id === 'scale' ? (
                          <Rocket className="h-4 w-4" />
                        ) : (
                          <Zap className="h-4 w-4" />
                        )}
                        Upgrade to {plan.name}
                      </button>
                    ) : (
                      <span className="inline-flex w-full items-center justify-center rounded-lg border border-slate-200 bg-slate-50 px-4 py-2.5 text-sm font-medium text-slate-400">
                        {plan.name}
                      </span>
                    )}
                  </div>
                )
              })}
            </div>
          </div>

          {/* Footer */}
          <div className="border-t border-slate-100 bg-slate-50 px-6 py-4 sm:px-8">
            <p className="text-center text-xs text-slate-500">
              Questions about pricing?{' '}
              <a
                href="/pricing/"
                className="font-medium text-blue-600 hover:text-blue-700"
              >
                View full comparison
              </a>
            </p>
          </div>
        </div>
      </div>
    </div>,
    document.body,
  )
}
