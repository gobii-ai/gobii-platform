import { Lock } from 'lucide-react'

import type { IntelligenceTierKey, LlmIntelligenceConfig } from '../../types/llmIntelligence'

type AgentIntelligenceSliderProps = {
  currentTier: IntelligenceTierKey
  config: LlmIntelligenceConfig
  onTierChange: (tier: IntelligenceTierKey) => void
  disabled?: boolean
}

export function AgentIntelligenceSlider({
  currentTier,
  config,
  onTierChange,
  disabled = false,
}: AgentIntelligenceSliderProps) {
  const isDisabled = disabled || !config.canEdit

  const handleSelect = (tier: IntelligenceTierKey) => {
    if (isDisabled || tier === currentTier) {
      return
    }
    onTierChange(tier)
  }

  const renderMultiplier = (value: number) => {
    if (!Number.isFinite(value)) {
      return '× credits'
    }
    const formatted = value % 1 === 0 ? value.toFixed(0) : value.toFixed(1)
    return `${formatted}× credits`
  }

  return (
    <div className="space-y-2">
      {!config.canEdit && config.disabledReason && (
        <p className="flex items-center gap-1 text-xs text-gray-500">
          <Lock className="h-3.5 w-3.5 text-gray-400" aria-hidden="true" />
          <span>
            {config.disabledReason}
            {config.upgradeUrl && (
              <>
                {' '}
                <a href={config.upgradeUrl} className="text-indigo-600 underline">
                  Upgrade
                </a>
              </>
            )}
          </span>
        </p>
      )}
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
        {config.options.map((option) => {
          const selected = currentTier === option.key
          return (
            <button
              type="button"
              key={option.key}
              onClick={() => handleSelect(option.key)}
              disabled={isDisabled}
              className={`flex flex-col rounded-lg border p-3 text-left transition focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:ring-offset-2 ${
                selected ? 'border-indigo-500 bg-indigo-50 shadow-sm' : 'border-gray-200 bg-white hover:border-indigo-300'
              } ${isDisabled ? 'cursor-not-allowed opacity-60' : ''}`}
            >
              <span className="text-sm font-semibold text-gray-800">{option.label}</span>
              <span className="mt-1 text-xs text-gray-500">{option.description}</span>
              <span className="mt-2 text-xs font-medium text-gray-500">{renderMultiplier(option.multiplier)}</span>
            </button>
          )
        })}
      </div>
    </div>
  )
}
