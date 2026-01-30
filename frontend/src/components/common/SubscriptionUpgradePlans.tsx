import { useCallback } from 'react'
import { Check, Zap, Rocket } from 'lucide-react'

import type { PlanTier } from '../../stores/subscriptionStore'
import { appendReturnTo } from '../../util/returnTo'
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
  // {
  //   id: 'free',
  //   name: 'Free',
  //   price: '$0',
  //   priceSubtext: 'Free to start',
  //   description: 'Get started with core features',
  //   features: [
  //     '100 tasks (one-time)',
  //     '3 contacts per agent',
  //     '5 always-on agents',
  //     'Agents run up to 30 days',
  //     'Basic API access',
  //     'Community support',
  //   ],
  // },
  {
    id: 'startup',
    name: 'Pro',
    price: '$50',
    priceSubtext: 'per month',
    description: 'Smart Power for Everyday Work',
    badge: 'Great for Everyday Work',
    features: [
      '500 tasks included',
      'Unlimited always-on agents (run 24/7)',
      '10 contacts per agent',
      'Agents never expire',
      'Priority support',
      'Higher rate limits',
      'Optional extra tasks available at $0.10/task over 500',
    ],
  },
  {
    id: 'scale',
    name: 'Scale',
    price: '$250',
    priceSubtext: 'per month',
    description: 'Maximum Intelligence for Reliable Results',
    highlight: true,
    badge: 'Best for Complex Tasks',
    features: [

      '10,000 tasks included per month',
      'Unlimited always-on agents (run 24/7)',
      '50 contacts per agent',
      'Agents never expire',
      'Priority work queue',
      '1,500 req/min API throughput',
      'Optional extra tasks available at $0.04/task over 10k',
    ],
  },
]

type SubscriptionUpgradePlansProps = {
  currentPlan: PlanTier | null
  onUpgrade: (plan: PlanTier) => void
  variant?: 'modal' | 'inline'
  pricingLinkLabel?: string
  source?: string
}

export function SubscriptionUpgradePlans({
  currentPlan,
  onUpgrade,
  variant = 'modal',
  pricingLinkLabel = 'View full comparison',
  source,
}: SubscriptionUpgradePlansProps) {
  const isCurrentPlan = useCallback((planId: PlanTier) => currentPlan === planId, [currentPlan])
  const isUpgrade = useCallback(
    (planId: PlanTier) => {
      if (!currentPlan) return planId !== 'free'
      const order: PlanTier[] = ['free', 'startup', 'scale']
      return order.indexOf(planId) > order.indexOf(currentPlan)
    },
    [currentPlan],
  )

  const handlePlanSelect = useCallback((planId: PlanTier) => {
    track(AnalyticsEvent.UPGRADE_PLAN_SELECTED, {
      currentPlan,
      selectedPlan: planId,
      source: source ?? 'unknown',
    })
    onUpgrade(planId)
  }, [currentPlan, onUpgrade, source])

  const pricingUrl = appendReturnTo('/pricing/')

  const wrapperClass = variant === 'inline' ? 'px-0 py-0' : 'px-6 py-6 sm:px-8'
  const footerClass = variant === 'inline'
    ? 'mt-2 text-center text-xs text-slate-500'
    : 'border-t border-slate-200 bg-white px-6 py-4 sm:px-8'

  return (
    <>
      <div className={wrapperClass}>
        <div className="grid gap-4 sm:grid-cols-2">
          {PLANS.map((plan) => {
            const isCurrent = isCurrentPlan(plan.id)
            const canUpgrade = isUpgrade(plan.id)

            return (
              <div
                key={plan.id}
                className={`relative flex flex-col rounded-xl border p-5 ${
                  plan.highlight
                    ? 'border-blue-200 bg-gradient-to-br from-white via-blue-50/30 to-indigo-50/30'
                    : 'border-slate-200 bg-white'
                } ${isCurrent ? 'ring-2 ring-blue-500 ring-offset-2' : ''}`}
              >
                {plan.badge && (
                  <div
                    className={`absolute -top-2.5 left-1/2 -translate-x-1/2 rounded-full px-3 py-0.5 text-xs font-semibold ${
                      plan.highlight
                        ? 'bg-blue-600 text-white'
                        : 'bg-blue-50 text-blue-700'
                    }`}
                  >
                    {plan.badge}
                  </div>
                )}

                <div className="mb-4 text-center">
                  <h3 className="text-lg font-semibold text-slate-900">
                    {plan.name}
                  </h3>
                  <p className="mt-1 text-sm text-slate-500">
                    {plan.description}
                  </p>
                </div>

                <div className="mb-4 text-center">
                  <span className="text-3xl font-semibold text-slate-900">
                    {plan.price}
                  </span>
                  <span className="ml-1 text-sm text-slate-500">
                    {plan.priceSubtext}
                  </span>
                </div>

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

                {isCurrent ? (
                  <span className="inline-flex w-full items-center justify-center rounded-lg border border-slate-200 bg-white px-4 py-2.5 text-sm font-semibold text-slate-500">
                    Current plan
                  </span>
                ) : canUpgrade ? (
                  <button
                    type="button"
                    onClick={() => handlePlanSelect(plan.id)}
                    className={`inline-flex w-full items-center justify-center gap-2 rounded-lg px-4 py-2.5 text-sm font-semibold transition ${
                      plan.highlight
                        ? 'bg-blue-600 text-white hover:bg-blue-700'
                        : 'border border-slate-200 bg-white text-slate-700 hover:bg-blue-50/40 hover:text-blue-600'
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
                  <span className="inline-flex w-full items-center justify-center rounded-lg border border-slate-200 bg-white px-4 py-2.5 text-sm font-medium text-slate-400">
                    {plan.name}
                  </span>
                )}
              </div>
            )
          })}
        </div>
      </div>

      <div className={footerClass}>
        <p className="text-center text-xs text-slate-500">
          <a
            href={pricingUrl}
            className="font-medium text-blue-600 hover:text-blue-700"
          >
            {pricingLinkLabel}
          </a>
        </p>
      </div>
    </>
  )
}
