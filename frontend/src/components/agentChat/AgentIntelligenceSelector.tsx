import { useMemo, useState } from 'react'
import { Brain, ChevronDown, Lock } from 'lucide-react'
import {
  Button,
  Dialog,
  DialogTrigger,
  ListBox,
  ListBoxItem,
  Popover,
  type Key,
  type Selection,
} from 'react-aria-components'

import type { LlmIntelligenceConfig } from '../../types/llmIntelligence'

const EMOJI_MAP: Record<string, string> = {
  standard: '🌱',
  premium: '💭',
  max: '💡',
  ultra: '⚡',
  ultra_max: '🤯',
}

const formatTierLabel = (tier: string) =>
  tier
    .split('_')
    .map((segment) => (segment ? segment[0].toUpperCase() + segment.slice(1) : segment))
    .join(' ')

type IntelligenceSelectorProps = {
  config: LlmIntelligenceConfig
  currentTier: string
  onSelect: (tier: string) => void
  onUpsell?: () => void
  onOpenTaskPacks?: () => void
  allowLockedSelection?: boolean
  disabled?: boolean
  busy?: boolean
  error?: string | null
}

export function AgentIntelligenceSelector({
  config,
  currentTier,
  onSelect,
  onUpsell,
  onOpenTaskPacks,
  allowLockedSelection = false,
  disabled = false,
  busy = false,
  error,
}: IntelligenceSelectorProps) {
  const [open, setOpen] = useState(false)
  const options = useMemo(() => {
    const resolvedMaxRank = typeof config.maxAllowedTierRank === 'number'
      ? config.maxAllowedTierRank
      : null
    const fallbackLocked = !config.canEdit
    return config.options.map((option) => {
      const optionRank = typeof option.rank === 'number' ? option.rank : null
      const lockedByRank = resolvedMaxRank !== null && optionRank !== null && optionRank > resolvedMaxRank
      const locked = lockedByRank || (fallbackLocked && option.key !== currentTier)
      return {
        ...option,
        locked,
      }
    })
  }, [config.canEdit, config.maxAllowedTierRank, config.options, currentTier])
  const selectedOption = options.find((option) => option.key === currentTier) ?? options[0]
  const selectedKey = selectedOption?.key ?? options[0]?.key ?? 'standard'
  const selectedLabel = selectedOption?.label ?? formatTierLabel(selectedKey)
  const selectedKeys = useMemo(() => new Set<Key>([selectedKey]), [selectedKey])

  const handleSelection = (keys: Selection) => {
    if (disabled || busy) {
      return
    }
    const resolvedKey = (() => {
      if (keys === 'all') return null
      if (typeof keys === 'string' || typeof keys === 'number') {
        return String(keys)
      }
      const [first] = keys as Set<Key>
      return first ? String(first) : null
    })()
    if (!resolvedKey) {
      return
    }
    const option = options.find((item) => item.key === resolvedKey)
    if (!option) {
      return
    }
    if (option.locked && !allowLockedSelection) {
      onUpsell?.()
      setOpen(false)
      return
    }
    if (resolvedKey === currentTier) {
      setOpen(false)
      return
    }
    onSelect(resolvedKey)
    setOpen(false)
  }

  return (
    <DialogTrigger isOpen={open} onOpenChange={setOpen}>
      <Button
        className="composer-intelligence-trigger"
        aria-label={`Intelligence (${selectedLabel})`}
        data-busy={busy ? 'true' : 'false'}
        isDisabled={disabled}
      >
        <Brain className="composer-intelligence-icon" aria-hidden="true" />
        <span className="composer-intelligence-trigger-label">{selectedLabel}</span>
        <ChevronDown className="composer-intelligence-trigger-chevron" aria-hidden="true" />
      </Button>
      <Popover className="composer-intelligence-popover">
        <Dialog className="composer-intelligence-menu">
          <div className="composer-intelligence-header">
            <div className="composer-intelligence-title">
              <span>Intelligence</span>
            </div>
            <div className="composer-intelligence-caption">Higher tiers burn credits faster.</div>
          </div>
          <ListBox
            aria-label="Select intelligence level"
            selectionMode="single"
            selectedKeys={selectedKeys as unknown as Selection}
            onSelectionChange={(keys) => handleSelection(keys as Selection)}
            className="composer-intelligence-list"
          >
            {options.map((option) => (
              <ListBoxItem
                key={option.key}
                id={option.key}
                textValue={option.label}
                className="composer-intelligence-option"
                data-tier={option.key}
                data-locked={option.locked ? 'true' : 'false'}
              >
                {() => (
                  <>
                    <span className="composer-intelligence-option-icon" aria-hidden="true">
                      {EMOJI_MAP[option.key] ?? ''}
                    </span>
                    <span className="composer-intelligence-option-label">{option.label}</span>
                    <span className="composer-intelligence-option-multiplier">
                      {option.multiplier ? `${option.multiplier}×` : ''}
                    </span>
                    <span
                      className={`composer-intelligence-option-lock${
                        option.locked ? '' : ' composer-intelligence-option-lock--placeholder'
                      }`}
                      aria-hidden={option.locked ? undefined : 'true'}
                    >
                      <Lock size={12} strokeWidth={2} />
                      <span>Unlock</span>
                    </span>
                  </>
                )}
              </ListBoxItem>
            ))}
          </ListBox>
          {config.disabledReason ? (
            <div className="composer-intelligence-note">{config.disabledReason}</div>
          ) : null}
          {error ? <div className="composer-intelligence-error">{error}</div> : null}
          {onOpenTaskPacks ? (
            <button
              type="button"
              className="composer-intelligence-pack"
              onClick={() => {
                onOpenTaskPacks()
                setOpen(false)
              }}
            >
              Add task pack
            </button>
          ) : null}
        </Dialog>
      </Popover>
    </DialogTrigger>
  )
}
