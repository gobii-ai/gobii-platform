import { useCallback, useEffect, useMemo, useState } from 'react'
import { AlertTriangle, ExternalLink, Settings } from 'lucide-react'

import { Modal } from '../common/Modal'
import { AgentChatMobileSheet } from './AgentChatMobileSheet'
import { AgentIntelligenceSlider } from '../common/AgentIntelligenceSlider'
import type { DailyCreditsInfo, DailyCreditsStatus, DailyCreditsUpdatePayload } from '../../types/dailyCredits'
import type { IntelligenceTierKey, LlmIntelligenceConfig } from '../../types/llmIntelligence'

type AgentChatSettingsPanelProps = {
  open: boolean
  agentId?: string | null
  dailyCredits?: DailyCreditsInfo | null
  status?: DailyCreditsStatus | null
  loading?: boolean
  error?: string | null
  updating?: boolean
  onSave?: (payload: DailyCreditsUpdatePayload) => Promise<void>
  llmIntelligence?: LlmIntelligenceConfig | null
  currentLlmTier?: string | null
  onLlmTierChange?: (tier: string) => Promise<boolean>
  llmTierSaving?: boolean
  llmTierError?: string | null
  canManageAgent?: boolean
  onClose: () => void
}

function buildStatusLabel(status?: DailyCreditsStatus | null): { tone: 'alert' | 'warning' | 'neutral'; label: string } | null {
  if (!status) return null
  if (status.hardLimitReached || status.hardLimitBlocked) {
    return { tone: 'alert', label: 'Daily task limit reached' }
  }
  if (status.softTargetExceeded) {
    return { tone: 'warning', label: 'Soft target exceeded' }
  }
  return null
}

export function AgentChatSettingsPanel({
  open,
  agentId,
  dailyCredits,
  status,
  loading = false,
  error,
  updating = false,
  onSave,
  llmIntelligence = null,
  currentLlmTier = null,
  onLlmTierChange,
  llmTierSaving = false,
  llmTierError = null,
  canManageAgent = true,
  onClose,
}: AgentChatSettingsPanelProps) {
  const [isMobile, setIsMobile] = useState(false)
  const [sliderValue, setSliderValue] = useState(0)
  const [dailyCreditInput, setDailyCreditInput] = useState('')
  const [saveError, setSaveError] = useState<string | null>(null)
  const resolvedTier = (currentLlmTier ?? 'standard') as IntelligenceTierKey
  const [stagedTier, setStagedTier] = useState<IntelligenceTierKey>(resolvedTier)
  const intelligenceDirty = stagedTier !== resolvedTier
  const showIntelligenceSelector = Boolean(llmIntelligence && currentLlmTier && onLlmTierChange)
  const showDailyCreditsSection = Boolean(onSave || dailyCredits || loading || error || status)

  const tierMultiplierByKey = useMemo(() => {
    const map = new Map<IntelligenceTierKey, number>()
    for (const option of llmIntelligence?.options ?? []) {
      map.set(option.key, option.multiplier)
    }
    return map
  }, [llmIntelligence?.options])
  const hasTierMultipliers = tierMultiplierByKey.size > 0
  const getTierMultiplier = useCallback(
    (tier: IntelligenceTierKey) => {
      const value = tierMultiplierByKey.get(tier)
      if (!Number.isFinite(value) || !value || value <= 0) {
        return 1
      }
      return value
    },
    [tierMultiplierByKey],
  )

  const fallbackSliderMax = dailyCredits?.sliderMax ?? 0
  const fallbackSliderEmptyValue = dailyCredits?.sliderEmptyValue ?? fallbackSliderMax
  const fallbackSliderLimitMax = dailyCredits?.sliderLimitMax ?? fallbackSliderMax
  const standardSliderLimit = Number.isFinite(dailyCredits?.standardSliderLimit)
    ? dailyCredits.standardSliderLimit
    : fallbackSliderLimitMax
  const sliderMin = dailyCredits?.sliderMin ?? 0
  const sliderStep = dailyCredits?.sliderStep ?? 1
  const currentMultiplier = hasTierMultipliers ? getTierMultiplier(stagedTier) : 1
  const sliderLimitMax = hasTierMultipliers
    ? Math.max(sliderMin, Math.round(standardSliderLimit * currentMultiplier))
    : fallbackSliderLimitMax
  const sliderMax = hasTierMultipliers ? sliderLimitMax + sliderStep : fallbackSliderMax
  const sliderEmptyValue = hasTierMultipliers ? sliderMax : fallbackSliderEmptyValue

  const handleTierChange = useCallback(
    (tier: IntelligenceTierKey) => {
      if (llmTierSaving) {
        return
      }
      if (tier !== stagedTier) {
        const previousMultiplier = hasTierMultipliers ? getTierMultiplier(stagedTier) : 1
        const nextMultiplier = hasTierMultipliers ? getTierMultiplier(tier) : 1
        const nextSliderLimitMax = hasTierMultipliers
          ? Math.max(sliderMin, Math.round(standardSliderLimit * nextMultiplier))
          : fallbackSliderLimitMax
        const nextSliderMax = hasTierMultipliers ? nextSliderLimitMax + sliderStep : fallbackSliderMax
        const nextSliderEmptyValue = hasTierMultipliers ? nextSliderMax : fallbackSliderEmptyValue
        const isUnlimited = sliderValue >= sliderEmptyValue || !dailyCreditInput.trim()

        if (isUnlimited) {
          setSliderValue(nextSliderEmptyValue)
          setDailyCreditInput('')
        } else {
          let scaledValue = sliderValue
          if (previousMultiplier > 0 && nextMultiplier > 0 && Number.isFinite(sliderValue)) {
            scaledValue = Math.round((sliderValue * nextMultiplier) / previousMultiplier)
          }
          if (!Number.isFinite(scaledValue) || scaledValue <= 0 || scaledValue > nextSliderLimitMax) {
            setSliderValue(nextSliderEmptyValue)
            setDailyCreditInput('')
          } else {
            if (scaledValue < sliderMin) {
              scaledValue = sliderMin
            }
            setSliderValue(scaledValue)
            setDailyCreditInput(String(Math.round(scaledValue)))
          }
        }
      }
      setStagedTier(tier)
    },
    [
      dailyCreditInput,
      fallbackSliderEmptyValue,
      fallbackSliderLimitMax,
      fallbackSliderMax,
      getTierMultiplier,
      hasTierMultipliers,
      llmTierSaving,
      sliderEmptyValue,
      sliderMin,
      sliderStep,
      sliderValue,
      stagedTier,
      standardSliderLimit,
    ],
  )

  const agentSettingsUrl = useMemo(() => {
    if (!agentId) return '/console/agents/'
    return `/console/agents/${agentId}/`
  }, [agentId])

  useEffect(() => {
    const checkMobile = () => {
      setIsMobile(window.innerWidth < 768)
    }
    checkMobile()
    window.addEventListener('resize', checkMobile)
    return () => window.removeEventListener('resize', checkMobile)
  }, [])

  useEffect(() => {
    if (!open || !dailyCredits) return
    const nextSliderValue = Number.isFinite(dailyCredits.sliderValue)
      ? dailyCredits.sliderValue
      : fallbackSliderEmptyValue
    setSliderValue(nextSliderValue)
    setDailyCreditInput(
      dailyCredits.limit === null ? '' : String(Math.round(dailyCredits.limit)),
    )
    setSaveError(null)
  }, [open, dailyCredits, fallbackSliderEmptyValue])

  useEffect(() => {
    if (!open) {
      return
    }
    setStagedTier(resolvedTier)
  }, [open, resolvedTier])

  const clampSlider = useCallback(
    (value: number) => {
      return Math.min(Math.max(Number.isFinite(value) ? value : sliderEmptyValue, sliderMin), sliderMax)
    },
    [sliderEmptyValue, sliderMax, sliderMin],
  )

  const updateSliderValue = useCallback(
    (value: number) => {
      const normalized = clampSlider(value)
      setSliderValue(normalized)
      setDailyCreditInput(normalized === sliderEmptyValue ? '' : String(Math.round(normalized)))
    },
    [clampSlider, sliderEmptyValue],
  )

  const handleDailyCreditInputChange = useCallback(
    (value: string) => {
      setDailyCreditInput(value)
      if (!value.trim()) {
        updateSliderValue(sliderEmptyValue)
        return
      }
      const numeric = Number(value)
      if (!Number.isFinite(numeric)) {
        updateSliderValue(sliderEmptyValue)
        return
      }
      const clamped = Math.min(Math.max(Math.round(numeric), sliderMin), sliderLimitMax)
      updateSliderValue(clamped)
    },
    [sliderEmptyValue, sliderLimitMax, sliderMin, updateSliderValue],
  )

  const dailyLimitState = useMemo(() => {
    const trimmed = dailyCreditInput.trim()
    if (!dailyCredits) {
      return {
        hasChanges: false,
        nextLimit: null as number | null,
        invalid: false,
      }
    }
    if (!trimmed) {
      return {
        hasChanges: dailyCredits.limit !== null,
        nextLimit: null,
        invalid: false,
      }
    }
    const numeric = Number(trimmed)
    if (!Number.isFinite(numeric)) {
      return {
        hasChanges: false,
        nextLimit: null,
        invalid: true,
      }
    }
    const rounded = Math.round(numeric)
    return {
      hasChanges: rounded !== dailyCredits.limit,
      nextLimit: rounded,
      invalid: numeric % 1 !== 0,
    }
  }, [dailyCreditInput, dailyCredits])

  const handleSave = useCallback(async () => {
    setSaveError(null)
    if (dailyLimitState.hasChanges && dailyLimitState.invalid) {
      setSaveError('Enter a whole number or leave blank for unlimited.')
      return
    }

    if (intelligenceDirty) {
      if (!onLlmTierChange) {
        return
      }
      const tierUpdated = await Promise.resolve(onLlmTierChange(stagedTier))
      if (tierUpdated === false) {
        return
      }
    }

    if (dailyLimitState.hasChanges) {
      if (!onSave) {
        return
      }
      try {
        await onSave({ daily_credit_limit: dailyLimitState.nextLimit })
      } catch (err) {
        setSaveError('Unable to update the daily task limit. Try again.')
        return
      }
    }

    if (intelligenceDirty || dailyLimitState.hasChanges) {
      onClose()
    }
  }, [
    dailyLimitState.hasChanges,
    dailyLimitState.invalid,
    dailyLimitState.nextLimit,
    intelligenceDirty,
    onClose,
    onLlmTierChange,
    onSave,
    stagedTier,
  ])

  const statusLabel = buildStatusLabel(status)
  const hasDailyCreditChanges = dailyLimitState.hasChanges
  const hasChanges = hasDailyCreditChanges || intelligenceDirty
  const canSave = intelligenceDirty || (hasDailyCreditChanges && Boolean(onSave))

  const body = (
    <div className="agent-settings-panel">
      {showDailyCreditsSection ? (
        <div className="agent-settings-section">
          <div className="agent-settings-section-header">
            <div>
              <h3 className="agent-settings-title">Daily task credits</h3>
            </div>
            {statusLabel ? (
              <span className={`agent-settings-status agent-settings-status--${statusLabel.tone}`}>
                {statusLabel.tone === 'alert' ? <AlertTriangle size={14} /> : null}
                {statusLabel.label}
              </span>
            ) : null}
          </div>

          {loading ? (
            <p className="agent-settings-helper">Loading daily credits...</p>
          ) : error ? (
            <p className="agent-settings-error">Unable to load daily credits. Try again.</p>
          ) : dailyCredits ? (
            <>
              <div className="agent-settings-slider">
                <label htmlFor="daily-credit-limit" className="agent-settings-input-label">
                  Adjust soft target
                </label>
                <input
                  id="daily-credit-limit"
                  type="range"
                  min={sliderMin}
                  max={sliderMax}
                  step={sliderStep}
                  value={sliderValue}
                  onChange={(event) => updateSliderValue(Number(event.target.value))}
                  className="agent-settings-range"
                />
                <div className="agent-settings-slider-hint">
                  <span>{sliderValue === sliderEmptyValue ? 'Unlimited' : `${Math.round(sliderValue)} credits/day`}</span>
                  <span>Unlimited</span>
                </div>
                <div className="agent-settings-input-row">
                  <input
                    type="number"
                    min={sliderMin}
                    max={sliderLimitMax}
                    step="1"
                    value={dailyCreditInput}
                    onChange={(event) => handleDailyCreditInputChange(event.target.value)}
                    className="agent-settings-input"
                    placeholder="Unlimited"
                  />
                  <span className="agent-settings-input-suffix">credits/day</span>
                </div>
                <p className="agent-settings-helper">Leave blank to remove the daily target.</p>
              </div>
            </>
          ) : (
            <p className="agent-settings-helper">Daily credits unavailable.</p>
          )}

          {saveError ? <p className="agent-settings-error">{saveError}</p> : null}
        </div>
      ) : null}
      {showIntelligenceSelector ? (
        <div className="agent-settings-section">
          <div className="agent-settings-section-header">
            <div>
              <h3 className="agent-settings-title">Intelligence</h3>
            </div>
          </div>
          <div className="agent-settings-intelligence">
            <AgentIntelligenceSlider
              config={llmIntelligence as LlmIntelligenceConfig}
              currentTier={stagedTier ?? resolvedTier}
              onTierChange={handleTierChange}
              disabled={!canManageAgent || llmTierSaving}
            />
          </div>
          {llmTierError ? <p className="agent-settings-error">{llmTierError}</p> : null}
        </div>
      ) : null}
      <div className="agent-settings-actions">
        <button
          type="button"
          className="agent-settings-save"
          onClick={handleSave}
          disabled={!canSave || !hasChanges || updating || loading || llmTierSaving}
        >
          {updating || llmTierSaving ? 'Saving...' : 'Save'}
        </button>
        <a href={agentSettingsUrl} className="agent-settings-link" target="_blank" rel="noreferrer">
          More Settings
          <ExternalLink size={14} />
        </a>
      </div>
    </div>
  )

  if (!open) {
    return null
  }

  if (!isMobile) {
    return (
      <Modal
        title="Agent settings"
        onClose={onClose}
        icon={Settings}
        iconBgClass="bg-amber-100"
        iconColorClass="text-amber-600"
        bodyClassName="agent-settings-modal-body"
      >
        {body}
      </Modal>
    )
  }

  return (
    <AgentChatMobileSheet
      open={open}
      onClose={onClose}
      title="Agent settings"
      icon={Settings}
      ariaLabel="Agent settings"
    >
      {body}
    </AgentChatMobileSheet>
  )
}
