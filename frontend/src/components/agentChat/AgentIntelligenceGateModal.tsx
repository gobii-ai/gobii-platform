import { Lock, Zap } from 'lucide-react'

import type { IntelligenceTierKey } from '../../types/llmIntelligence'
import { Modal } from '../common/Modal'

type GateReason = 'plan' | 'credits' | 'both'

type AgentIntelligenceGateModalProps = {
  open: boolean
  reason: GateReason
  selectedTier: IntelligenceTierKey
  allowedTier: IntelligenceTierKey
  multiplier?: number | null
  estimatedRemaining?: number | null
  showUpgrade?: boolean
  showAddPack?: boolean
  onUpgrade?: () => void
  onAddPack?: () => void
  onContinue: () => void
  onClose: () => void
}

const LABEL_OVERRIDES: Record<IntelligenceTierKey, string> = {
  standard: 'Smol Brain',
  premium: 'Mid Brain',
  max: 'Big Brain',
  ultra: 'Giga Brain',
  ultra_max: 'Galaxy Brain',
}

function formatTierLabel(tier: IntelligenceTierKey): string {
  return LABEL_OVERRIDES[tier] ?? tier.replace('_', ' ')
}

function formatRemaining(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return 'a few'
  }
  if (value < 1) return 'less than 1'
  if (value < 10) return value.toFixed(1)
  return Math.round(value).toString()
}

export function AgentIntelligenceGateModal({
  open,
  reason,
  selectedTier,
  allowedTier,
  multiplier,
  estimatedRemaining,
  showUpgrade = false,
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
  const selectedLabel = formatTierLabel(selectedTier)
  const allowedLabel = formatTierLabel(allowedTier)
  const remainingLabel = formatRemaining(estimatedRemaining)

  const title = needsPlanUpgrade
    ? `Unlock ${selectedLabel}`
    : 'Credits running low'
  const subtitle = needsPlanUpgrade
    ? `Your plan does not include ${selectedLabel}.`
    : `At ${selectedLabel}, you have about ${remainingLabel} task${remainingLabel === '1' ? '' : 's'} left.`

  const continueLabel = needsPlanUpgrade ? `Use ${allowedLabel}` : 'Continue anyway'

  return (
    <Modal
      title={title}
      subtitle={subtitle}
      onClose={onClose}
      icon={needsPlanUpgrade ? Lock : Zap}
      iconBgClass={needsPlanUpgrade ? 'bg-amber-100' : 'bg-indigo-100'}
      iconColorClass={needsPlanUpgrade ? 'text-amber-600' : 'text-indigo-600'}
      widthClass="sm:max-w-xl"
      bodyClassName="pr-0"
    >
      <div className="space-y-3 text-sm text-slate-600">
        {needsPlanUpgrade ? (
          <div className="flex items-start gap-2">
            <Lock className="mt-0.5 h-4 w-4 text-amber-500" aria-hidden="true" />
            <span>
              Upgrade to Pro or Scale to unlock this intelligence tier.
            </span>
          </div>
        ) : null}
        {creditsTight ? (
          <div className="flex items-start gap-2">
            <Zap className="mt-0.5 h-4 w-4 text-indigo-500" aria-hidden="true" />
            <span>
              Higher tiers burn credits faster
              {multiplier && Number.isFinite(multiplier) ? ` (${multiplier}× credits).` : '.'}
            </span>
          </div>
        ) : null}
      </div>

      <div className="mt-6 flex flex-col gap-2 sm:flex-row sm:items-center">
        {showUpgrade && onUpgrade ? (
          <button
            type="button"
            className="inline-flex items-center justify-center gap-2 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-indigo-700"
            onClick={onUpgrade}
          >
            Upgrade plan
          </button>
        ) : null}
        {showAddPack && onAddPack ? (
          <button
            type="button"
            className="inline-flex items-center justify-center gap-2 rounded-lg border border-indigo-200 px-4 py-2 text-sm font-semibold text-indigo-600 transition hover:border-indigo-300 hover:text-indigo-700"
            onClick={onAddPack}
          >
            Add task pack
          </button>
        ) : null}
        <button
          type="button"
          className="inline-flex items-center justify-center gap-2 rounded-lg border border-slate-200 px-4 py-2 text-sm font-semibold text-slate-700 transition hover:border-slate-300 hover:text-slate-800"
          onClick={onContinue}
        >
          {continueLabel}
        </button>
      </div>
    </Modal>
  )
}
