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

const LABEL_OVERRIDES: Record<string, string> = {
  standard: 'Smol Brain',
  premium: 'Mid Brain',
  max: 'Big Brain',
  ultra: 'Giga Brain',
  ultra_max: 'Galaxy Brain',
}

const EMOJI_MAP: Record<string, string> = {
  standard: '🌱',
  premium: '💭',
  max: '💡',
  ultra: '⚡',
  ultra_max: '🤯',
}

type IntelligenceSelectorProps = {
  config: LlmIntelligenceConfig
  currentTier: string
  onSelect: (tier: string) => void
  onUpsell?: () => void
  onOpenTaskPacks?: () => void
  disabled?: boolean
  busy?: boolean
  error?: string | null
}

function formatMultiplier(multiplier: number | null | undefined): string {
  if (!Number.isFinite(multiplier)) {
    return '× credits'
  }
  const normalized = Number(multiplier)
  return `${normalized % 1 === 0 ? normalized.toFixed(0) : normalized.toFixed(1)}× credits`
}

export function AgentIntelligenceSelector({
  config,
  currentTier,
  onSelect,
  onUpsell,
  onOpenTaskPacks,
  disabled = false,
  busy = false,
  error,
}: IntelligenceSelectorProps) {
  const [open, setOpen] = useState(false)
  const options = useMemo(
    () =>
      config.options.map((option) => ({
        ...option,
        label: LABEL_OVERRIDES[option.key] ?? option.label,
        locked: !config.canEdit && option.key !== currentTier,
      })),
    [config.canEdit, config.options, currentTier],
  )
  const selectedOption = options.find((option) => option.key === currentTier) ?? options[0]
  const selectedKey = selectedOption?.key ?? options[0]?.key ?? 'standard'
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
    if (option.locked) {
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
        aria-label={`Intelligence (${selectedOption?.label ?? 'Smol Brain'})`}
        data-busy={busy ? 'true' : 'false'}
        isDisabled={disabled}
      >
        <Brain className="composer-intelligence-icon" aria-hidden="true" />
        <span className="composer-intelligence-trigger-label">{selectedOption?.label ?? 'Smol Brain'}</span>
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
                {({ isSelected }) => (
                  <>
                    <span className="composer-intelligence-option-label">
                      {EMOJI_MAP[option.key] ? `${EMOJI_MAP[option.key]} ` : ''}
                      {option.label}
                    </span>
                    <span className="composer-intelligence-option-multiplier">
                      {option.multiplier ? `${option.multiplier}×` : ''}
                    </span>
                    <span className="composer-intelligence-option-indicator">
                      {option.locked ? (
                        <Lock className="composer-intelligence-option-lock-icon" aria-hidden="true" />
                      ) : isSelected ? (
                        <svg className="composer-intelligence-option-check" viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 13l4 4L19 7" />
                        </svg>
                      ) : null}
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
