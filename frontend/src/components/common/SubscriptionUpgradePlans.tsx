import { useCallback, useState } from 'react'
import { Check, ChevronRight, Rocket, Sparkles } from 'lucide-react'

import {
  isContinuationUpgradeModalSource,
  useSubscriptionStore,
  type PlanTaskCreditsByPlan,
  type PlanTier,
} from '../../stores/subscriptionStore'
import { appendReturnTo } from '../../util/returnTo'
import { track } from '../../util/analytics'
import { AnalyticsEvent } from '../../constants/analyticsEvents'

type PlanConfig = {
  id: PlanTier
  name: string
  price: string
  priceSubtext: string
  description: string
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
  },
  {
    id: 'scale',
    name: 'Scale',
    price: '$250',
    priceSubtext: '/month',
    description: 'Maximum Intelligence for Reliable Results',
    highlight: true,
    badge: 'Best Value',
  },
]

function formatTaskCredits(count: number): string {
  return count.toLocaleString()
}

function getPlanFeatures(planId: PlanTier, planTaskCreditsByPlan: PlanTaskCreditsByPlan): string[] {
  if (planId === 'startup') {
    const taskCredits = formatTaskCredits(planTaskCreditsByPlan.startup)
    return [
      `${taskCredits} tasks included`,
      'Unlimited always-on agents',
      '10 contacts per agent',
      'Agents never expire',
      'Priority support',
      '$0.10 per extra task',
    ]
  }

  const taskCredits = formatTaskCredits(planTaskCreditsByPlan.scale)
  return [
    `${taskCredits} tasks included`,
    'Unlimited always-on agents',
    '50 contacts per agent',
    'Agents never expire',
    'Priority work queue',
    '$0.04 per extra task',
  ]
}

type SubscriptionUpgradePlansProps = {
  currentPlan: PlanTier | null
  onUpgrade: (plan: PlanTier) => void
  variant?: 'modal' | 'inline'
  pricingLinkLabel?: string
  source?: string
  allowDowngrade?: boolean
  collapseFeaturesByDefault?: boolean
}

export function SubscriptionUpgradePlans({
  currentPlan,
  onUpgrade,
  variant = 'modal',
  pricingLinkLabel = 'View full comparison',
  source,
  allowDowngrade = false,
  collapseFeaturesByDefault = false,
}: SubscriptionUpgradePlansProps) {
  const {
    trialDaysByPlan,
    planTaskCreditsByPlan,
    trialEligible,
    pricingModalAlmostFullScreen,
    ctaPricingCancelTextUnderBtn,
    ctaStartFreeTrial,
    ctaContinueAgentBtn,
    ctaNoChargeDuringTrial,
  } = useSubscriptionStore()
  const isCurrentPlan = useCallback((planId: PlanTier) => currentPlan === planId, [currentPlan])
  const canSelectPlan = useCallback(
    (planId: PlanTier) => {
      if (!currentPlan) return planId !== 'free'
      if (allowDowngrade) return planId !== currentPlan && planId !== 'free'
      const order: PlanTier[] = ['free', 'startup', 'scale']
      return order.indexOf(planId) > order.indexOf(currentPlan)
    },
    [allowDowngrade, currentPlan],
  )

  const handlePlanSelect = useCallback((planId: PlanTier) => {
    track(AnalyticsEvent.UPGRADE_PLAN_SELECTED, {
      currentPlan,
      selectedPlan: planId,
      source: source ?? 'unknown',
    })
    onUpgrade(planId)
  }, [currentPlan, onUpgrade, source])

  const viewComparisonClick = useCallback(() => {
    if (typeof window === 'undefined') {
      return
    }
    window.gobiiTrackCta?.({
      cta_id: 'pricing_modal_view_comparison',
      destination: '/pricing/',
      
    })
  }, [])

  const pricingUrl = appendReturnTo('/pricing/')
  const isExpandedModal = variant === 'modal' && pricingModalAlmostFullScreen
  const isCompactInline = variant === 'inline'

  const wrapperClass = variant === 'inline'
    ? 'px-0 py-0'
    : isExpandedModal
      ? 'min-h-0 flex-1 overflow-y-auto px-6 py-4 sm:px-8 sm:py-5'
      : 'px-6 py-6 sm:px-8'
  const rootClass = isExpandedModal ? 'flex h-full min-h-0 flex-col' : ''
  const gridClass = isExpandedModal
    ? 'grid gap-5 sm:min-h-full sm:grid-cols-2 sm:items-stretch sm:[grid-auto-rows:1fr]'
    : isCompactInline
      ? 'grid gap-3 sm:grid-cols-2'
      : 'grid gap-5 sm:grid-cols-2'
  const footerClass = variant === 'inline'
    ? 'mt-2 text-center'
    : isExpandedModal
      ? 'border-t border-slate-200 bg-white px-6 py-2.5 sm:px-8 sm:py-3'
      : 'border-t border-slate-200 bg-white px-6 py-4 sm:px-8'
  const hasAnyTrialDays = Math.max(trialDaysByPlan.startup, trialDaysByPlan.scale) > 0
  const useTrialCopy = (
    trialEligible
    && !allowDowngrade
    && hasAnyTrialDays
    && (source === 'trial_onboarding' || currentPlan === 'free')
  )
  const useContinuationButtonCopy = ctaContinueAgentBtn && isContinuationUpgradeModalSource(source)
  const [allFeaturesExpanded, setAllFeaturesExpanded] = useState(false)

  const isFeatureListExpanded = useCallback((_planId: PlanTier) => {
    if (!collapseFeaturesByDefault) {
      return true
    }
    return allFeaturesExpanded
  }, [allFeaturesExpanded, collapseFeaturesByDefault])

  const toggleFeatureList = useCallback(() => {
    setAllFeaturesExpanded((current) => !current)
  }, [])

  return (
    <div className={rootClass}>
      <div className={wrapperClass}>
        <div className={gridClass} data-testid="subscription-plans-grid">
          {PLANS.map((plan) => {
            const isCurrent = isCurrentPlan(plan.id)
            const canUpgrade = canSelectPlan(plan.id)
            const featuresExpanded = isFeatureListExpanded(plan.id)
            const features = getPlanFeatures(plan.id, planTaskCreditsByPlan)
            const trialDays = plan.id === 'startup' ? trialDaysByPlan.startup : trialDaysByPlan.scale
            const subscribeLabel = `Subscribe to ${plan.name}`
            const ctaLabel = useTrialCopy
              ? (
                  trialDays > 0
                    ? (
                        useContinuationButtonCopy
                          ? 'Continue Your Agent'
                          : (ctaStartFreeTrial ? 'Start Free Trial' : `Start ${trialDays}-day Free Trial`)
                      )
                    : subscribeLabel
                )
              : (allowDowngrade ? `Select ${plan.name}` : subscribeLabel)
            const shouldShowTrialCancelText = (
              (ctaNoChargeDuringTrial || ctaPricingCancelTextUnderBtn)
              && canUpgrade
              && useTrialCopy
              && trialDays > 0
            )
            const trialCancelText = shouldShowTrialCancelText
              ? (
                  ctaNoChargeDuringTrial
                    ? `No charge if you cancel during the ${trialDays}-day trial. Takes 30 seconds.`
                    : `Cancel anytime during the ${trialDays}-day trial`
                )
              : null

            return (
              <div
                key={plan.id}
                data-testid={`subscription-plan-${plan.id}`}
                className={`group relative flex flex-col overflow-hidden rounded-2xl transition-all duration-200 ${
                  plan.highlight
                    ? 'bg-gradient-to-b from-indigo-600 to-blue-700 p-[2px] shadow-lg shadow-blue-500/20'
                    : 'border border-slate-200 bg-white hover:border-slate-300 hover:shadow-md'
                } ${isCurrent ? 'ring-2 ring-blue-500 ring-offset-2' : ''} ${isExpandedModal ? 'sm:h-full' : ''} ${isCompactInline ? 'rounded-[1.15rem]' : ''}`}
              >
                <div className={`relative flex flex-col ${isExpandedModal ? 'sm:h-full' : 'h-full'} ${plan.highlight ? 'rounded-[14px] bg-white' : ''} ${isCompactInline && plan.highlight ? 'rounded-[1rem]' : ''}`}>
                  {plan.badge && (
                    <div
                      className={`absolute rounded-full px-2 py-1 text-[9px] font-bold uppercase tracking-wide ${
                        plan.highlight
                          ? 'bg-gradient-to-r from-indigo-500 to-blue-500 text-white'
                          : 'bg-slate-100 text-slate-600'
                      } ${isCompactInline ? 'right-2.5 top-2.5' : 'right-3 top-3'}`}
                    >
                      {plan.badge}
                    </div>
                  )}

                  <div className={isCompactInline ? 'px-4 pt-4 pb-3' : 'px-5 pt-5 pb-4'}>
                    <h3 className={isCompactInline ? 'text-lg font-bold text-slate-900' : 'text-xl font-bold text-slate-900'}>
                      {plan.name}
                    </h3>
                    <p className="mt-1 text-xs text-slate-500">
                      {plan.description}
                    </p>
                    <div className={isCompactInline ? 'mt-3 flex items-baseline' : 'mt-4 flex items-baseline'}>
                      <span className={isCompactInline ? 'text-3xl font-extrabold tracking-tight text-slate-900' : 'text-4xl font-extrabold tracking-tight text-slate-900'}>
                        {plan.price}
                      </span>
                      <span className="ml-1 text-sm font-medium text-slate-500">
                        {plan.priceSubtext}
                      </span>
                    </div>
                  </div>

                  <div className={isCompactInline ? 'flex-1 border-t border-slate-100 px-4 py-3' : 'flex-1 border-t border-slate-100 px-5 py-4'}>
                    {collapseFeaturesByDefault ? (
                      <button
                        type="button"
                        onClick={() => toggleFeatureList()}
                        className="inline-flex items-center gap-1.5 text-left text-sm font-medium text-slate-700 transition-colors hover:text-slate-900"
                        aria-expanded={featuresExpanded}
                      >
                        <span>Features</span>
                        <ChevronRight
                          className={`h-4 w-4 text-slate-500 transition-transform ${featuresExpanded ? 'rotate-90' : ''}`}
                        />
                      </button>
                    ) : null}
                    {featuresExpanded ? (
                      <ul className={collapseFeaturesByDefault ? 'mt-3 space-y-2.5' : 'space-y-2.5'}>
                        {features.map((feature, idx) => (
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
                    ) : null}
                  </div>

                  <div className={isCompactInline ? 'px-4 pb-4' : 'px-5 pb-5'}>
                    {isCurrent ? (
                      <span className={`inline-flex w-full items-center justify-center rounded-xl border border-slate-200 bg-slate-50 px-4 text-sm font-semibold text-slate-500 ${isCompactInline ? 'py-2.5' : 'py-3'}`}>
                        Current plan
                      </span>
                    ) : canUpgrade ? (
                      <button
                        type="button"
                        onClick={() => handlePlanSelect(plan.id)}
                        className={`inline-flex w-full items-center justify-center gap-2 rounded-xl px-4 text-sm font-semibold transition-all duration-200 ${
                          plan.highlight
                            ? 'bg-gradient-to-r from-indigo-600 to-blue-600 text-white shadow-md shadow-blue-500/25 hover:from-indigo-700 hover:to-blue-700 hover:shadow-lg hover:shadow-blue-500/30'
                            : 'bg-slate-900 text-white hover:bg-slate-800'
                        } ${isCompactInline ? 'py-2.5' : 'py-3'}`}
                      >
                        {plan.id === 'scale' ? (
                          <Rocket className="h-4 w-4" />
                        ) : (
                          <Sparkles className="h-4 w-4" />
                        )}
                        {ctaLabel}
                      </button>
                    ) : (
                      <span className={`inline-flex w-full items-center justify-center rounded-xl border border-slate-200 bg-white px-4 text-sm font-medium text-slate-400 ${isCompactInline ? 'py-2.5' : 'py-3'}`}>
                        {plan.name}
                      </span>
                    )}
                    {trialCancelText ? (
                      <p className="mt-2 text-center text-xs text-slate-500">
                        {trialCancelText}
                      </p>
                    ) : null}
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
          onClick={viewComparisonClick}
        >
          {pricingLinkLabel} &rarr;
        </a>
      </div>
    </div>
  )
}
