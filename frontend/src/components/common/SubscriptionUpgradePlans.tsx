import { useCallback } from 'react'
import { Check, Sparkles, Rocket } from 'lucide-react'

import { useSubscriptionStore, type PlanTier } from '../../stores/subscriptionStore'
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
  {
    id: 'startup',
    name: 'Pro',
    price: '$50',
    priceSubtext: '/month',
    description: 'Smart Power for Everyday Work',
    badge: 'Popular',
    features: [
      '500 tasks included',
      'Unlimited always-on agents',
      '10 contacts per agent',
      'Agents never expire',
      'Priority support',
      '$0.10 per extra task',
    ],
  },
  {
    id: 'scale',
    name: 'Scale',
    price: '$250',
    priceSubtext: '/month',
    description: 'Maximum Intelligence for Reliable Results',
    highlight: true,
    badge: 'Best Value',
    features: [
      '10,000 tasks included',
      'Unlimited always-on agents',
      '50 contacts per agent',
      'Agents never expire',
      'Priority work queue',
      '$0.04 per extra task',
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
  const { trialDaysByPlan } = useSubscriptionStore()
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
    ? 'mt-4 text-center'
    : 'border-t border-slate-200 bg-white px-6 py-4 sm:px-8'
  const isTrialOnboarding = source === 'trial_onboarding'

  return (
    <>
      <div className={wrapperClass}>
        <div className="grid gap-5 sm:grid-cols-2">
          {PLANS.map((plan) => {
            const isCurrent = isCurrentPlan(plan.id)
            const canUpgrade = isUpgrade(plan.id)
            const trialDays = plan.id === 'startup' ? trialDaysByPlan.startup : trialDaysByPlan.scale
            const ctaLabel = isTrialOnboarding
              ? (trialDays > 0 ? `Start ${trialDays}-day Free Trial` : `Get ${plan.name}`)
              : `Get ${plan.name}`

            return (
              <div
                key={plan.id}
                className={`group relative flex flex-col overflow-hidden rounded-2xl transition-all duration-200 ${
                  plan.highlight
                    ? 'bg-gradient-to-b from-indigo-600 to-blue-700 p-[2px] shadow-lg shadow-blue-500/20'
                    : 'border border-slate-200 bg-white hover:border-slate-300 hover:shadow-md'
                } ${isCurrent ? 'ring-2 ring-blue-500 ring-offset-2' : ''}`}
              >
                <div className={`relative flex h-full flex-col ${plan.highlight ? 'rounded-[14px] bg-white' : ''}`}>
                  {plan.badge && (
                    <div
                      className={`absolute right-3 top-3 rounded-full px-2.5 py-1 text-[10px] font-bold uppercase tracking-wide ${
                        plan.highlight
                          ? 'bg-gradient-to-r from-indigo-500 to-blue-500 text-white'
                          : 'bg-slate-100 text-slate-600'
                      }`}
                    >
                      {plan.badge}
                    </div>
                  )}

                  <div className="px-5 pt-5 pb-4">
                    <h3 className="text-xl font-bold text-slate-900">
                      {plan.name}
                    </h3>
                    <p className="mt-1 text-xs text-slate-500">
                      {plan.description}
                    </p>
                    <div className="mt-4 flex items-baseline">
                      <span className="text-4xl font-extrabold tracking-tight text-slate-900">
                        {plan.price}
                      </span>
                      <span className="ml-1 text-sm font-medium text-slate-500">
                        {plan.priceSubtext}
                      </span>
                    </div>
                  </div>

                  <div className="flex-1 border-t border-slate-100 px-5 py-4">
                    <ul className="space-y-2.5">
                      {plan.features.map((feature, idx) => (
                        <li
                          key={idx}
                          className="flex items-center gap-2.5 text-sm text-slate-600"
                        >
                          <div className={`flex h-5 w-5 flex-shrink-0 items-center justify-center rounded-full ${
                            plan.highlight ? 'bg-blue-100' : 'bg-slate-100'
                          }`}>
                            <Check
                              className={`h-3 w-3 ${plan.highlight ? 'text-blue-600' : 'text-slate-600'}`}
                              strokeWidth={3}
                            />
                          </div>
                          <span>{feature}</span>
                        </li>
                      ))}
                    </ul>
                  </div>

                  <div className="px-5 pb-5">
                    {isCurrent ? (
                      <span className="inline-flex w-full items-center justify-center rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm font-semibold text-slate-500">
                        Current plan
                      </span>
                    ) : canUpgrade ? (
                      <button
                        type="button"
                        onClick={() => handlePlanSelect(plan.id)}
                        className={`inline-flex w-full items-center justify-center gap-2 rounded-xl px-4 py-3 text-sm font-semibold transition-all duration-200 ${
                          plan.highlight
                            ? 'bg-gradient-to-r from-indigo-600 to-blue-600 text-white shadow-md shadow-blue-500/25 hover:from-indigo-700 hover:to-blue-700 hover:shadow-lg hover:shadow-blue-500/30'
                            : 'bg-slate-900 text-white hover:bg-slate-800'
                        }`}
                      >
                        {plan.id === 'scale' ? (
                          <Rocket className="h-4 w-4" />
                        ) : (
                          <Sparkles className="h-4 w-4" />
                        )}
                        {ctaLabel}
                      </button>
                    ) : (
                      <span className="inline-flex w-full items-center justify-center rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm font-medium text-slate-400">
                        {plan.name}
                      </span>
                    )}
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      </div>

      <div className={footerClass}>
        <a
          href={pricingUrl}
          className="text-sm font-medium text-slate-500 transition-colors hover:text-blue-600"
        >
          {pricingLinkLabel} &rarr;
        </a>
      </div>
    </>
  )
}
