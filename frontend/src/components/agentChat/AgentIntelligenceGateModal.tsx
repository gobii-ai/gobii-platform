import { Lock, Zap } from 'lucide-react'

import type { IntelligenceTierKey } from '../../types/llmIntelligence'
import type { PlanTier } from '../../stores/subscriptionStore'
import { SubscriptionUpgradePlans } from '../common/SubscriptionUpgradePlans'
import { Modal } from '../common/Modal'

type GateReason = 'plan' | 'credits' | 'both'

type AgentIntelligenceGateModalProps = {
  open: boolean
  reason: GateReason
  selectedTier: IntelligenceTierKey
  allowedTier: IntelligenceTierKey
  tierLabels?: Partial<Record<IntelligenceTierKey, string>>
  multiplier?: number | null
  estimatedDaysRemaining?: number | null
  burnRatePerDay?: number | null
  currentPlan?: PlanTier | null
  showUpgradePlans?: boolean
  showAddPack?: boolean
  onUpgrade?: (plan: PlanTier) => void
  onAddPack?: () => void
  onContinue: () => void
  onClose: () => void
}

const formatTierLabel = (
  tier: IntelligenceTierKey,
  tierLabels?: Partial<Record<IntelligenceTierKey, string>>,
): string => {
  const label = tierLabels?.[tier]
  if (label) {
    return label
  }
  return tier
    .split('_')
    .map((segment) => (segment ? segment[0].toUpperCase() + segment.slice(1) : segment))
    .join(' ')
}

function formatRemaining(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return 'a few days'
  }
  if (value < 1) return 'less than 1'
  if (value < 10) return `${value.toFixed(1)}`
  return `${Math.round(value)}`
}

export function AgentIntelligenceGateModal({
  open,
  reason,
  selectedTier,
  allowedTier,
  tierLabels,
  multiplier,
  estimatedDaysRemaining,
  burnRatePerDay,
  currentPlan = null,
  showUpgradePlans = false,
  showAddPack = false,
  onUpgrade,
  onAddPack,
  onContinue,
  onClose,
}: AgentIntelligenceGateModalProps) {
  if (!open) {
    return null
  }

  const needsPlanUpgrade = reason === 'plan' || reason === 'both'
  const creditsTight = reason === 'credits' || reason === 'both'
  const selectedLabel = formatTierLabel(selectedTier, tierLabels)
  const allowedLabel = formatTierLabel(allowedTier, tierLabels)
  const remainingLabel = formatRemaining(estimatedDaysRemaining)
  const burnRateLabel = burnRatePerDay && Number.isFinite(burnRatePerDay)
    ? burnRatePerDay.toFixed(1)
    : null

  const isPaidUser = currentPlan === 'startup' || currentPlan === 'scale'

  const title = needsPlanUpgrade
    ? `Unlock ${selectedLabel}`
    : 'Credits running low'
  const subtitle = needsPlanUpgrade
    ? `Upgrade your plan to access ${selectedLabel}.`
    : `At ${selectedLabel}, you have about ${remainingLabel} day${remainingLabel === '1' ? '' : 's'} left.`

  const continueLabel = needsPlanUpgrade ? `Continue with ${allowedLabel}` : 'Continue anyway'

  return (
    <Modal
      title={title}
      subtitle={subtitle}
      onClose={onClose}
      icon={needsPlanUpgrade ? Lock : Zap}
      iconBgClass={needsPlanUpgrade ? 'bg-gradient-to-br from-amber-100 to-orange-100' : 'bg-gradient-to-br from-indigo-100 to-blue-100'}
      iconColorClass={needsPlanUpgrade ? 'text-amber-600' : 'text-indigo-600'}
      widthClass="sm:max-w-2xl"
      bodyClassName="pr-1"
    >
      {creditsTight && !needsPlanUpgrade ? (
        <div className="mb-4 flex items-start gap-3 rounded-xl bg-indigo-50/50 p-3 text-sm">
          <Zap className="mt-0.5 h-4 w-4 flex-shrink-0 text-indigo-500" aria-hidden="true" />
          <span className="text-slate-600">
            {burnRateLabel
              ? `Current burn rate is ~${burnRateLabel} credits/day. `
              : ''}
            Higher tiers burn credits faster
            {multiplier && Number.isFinite(multiplier) ? ` (${multiplier}× credits).` : '.'}
          </span>
        </div>
      ) : null}

      {needsPlanUpgrade && showUpgradePlans && onUpgrade ? (
        <div className="mt-4">
          <SubscriptionUpgradePlans
            currentPlan={currentPlan}
            onUpgrade={onUpgrade}
            variant="inline"
            pricingLinkLabel="Pricing details"
            source="intelligence_gate"
          />
        </div>
      ) : null}

      <div className="mt-6 flex flex-col gap-3 border-t border-slate-100 pt-5 sm:flex-row sm:items-center">
        {showAddPack && isPaidUser && onAddPack ? (
          <button
            type="button"
            className="inline-flex items-center justify-center gap-2 rounded-xl border border-indigo-200 bg-indigo-50/50 px-4 py-2.5 text-sm font-semibold text-indigo-600 transition-colors hover:border-indigo-300 hover:bg-indigo-100/50"
            onClick={onAddPack}
          >
            <Zap className="h-4 w-4" aria-hidden="true" />
            Add task pack
          </button>
        ) : null}
        <button
          type="button"
          className="inline-flex items-center justify-center gap-2 rounded-xl border border-slate-200 px-4 py-2.5 text-sm font-medium text-slate-600 transition-colors hover:border-slate-300 hover:bg-slate-50"
          onClick={onContinue}
        >
          {continueLabel}
        </button>
      </div>
    </Modal>
  )
}
