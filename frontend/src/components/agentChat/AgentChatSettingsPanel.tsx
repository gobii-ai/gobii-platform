import { useCallback, useEffect, useMemo, useState } from 'react'
import { AlertTriangle, ExternalLink, Settings } from 'lucide-react'

import { Modal } from '../common/Modal'
import { AgentChatMobileSheet } from './AgentChatMobileSheet'
import type { DailyCreditsInfo, DailyCreditsStatus, DailyCreditsUpdatePayload } from '../../types/dailyCredits'

type AgentChatSettingsPanelProps = {
  open: boolean
  agentId?: string | null
  dailyCredits?: DailyCreditsInfo | null
  status?: DailyCreditsStatus | null
  loading?: boolean
  error?: string | null
  updating?: boolean
  onSave?: (payload: DailyCreditsUpdatePayload) => Promise<void>
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
  onClose,
}: AgentChatSettingsPanelProps) {
  const [isMobile, setIsMobile] = useState(false)
  const [sliderValue, setSliderValue] = useState(0)
  const [dailyCreditInput, setDailyCreditInput] = useState('')
  const [saveError, setSaveError] = useState<string | null>(null)

  const sliderEmptyValue = dailyCredits?.sliderEmptyValue ?? dailyCredits?.sliderMax ?? 0
  const sliderMin = dailyCredits?.sliderMin ?? 0
  const sliderMax = dailyCredits?.sliderMax ?? 0
  const sliderLimitMax = dailyCredits?.sliderLimitMax ?? sliderMax
  const sliderStep = dailyCredits?.sliderStep ?? 1

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
      : sliderEmptyValue
    setSliderValue(nextSliderValue)
    setDailyCreditInput(
      dailyCredits.limit === null ? '' : String(Math.round(dailyCredits.limit)),
    )
    setSaveError(null)
  }, [open, dailyCredits, sliderEmptyValue])

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

  const handleSave = useCallback(async () => {
    if (!dailyCredits || !onSave) return
    setSaveError(null)
    const trimmed = dailyCreditInput.trim()
    if (trimmed) {
      const numeric = Number(trimmed)
      if (!Number.isFinite(numeric)) {
        setSaveError('Enter a whole number or leave blank for unlimited.')
        return
      }
      if (numeric % 1 !== 0) {
        setSaveError('Enter a whole number or leave blank for unlimited.')
        return
      }
    }

    const nextLimit = trimmed ? Math.round(Number(trimmed)) : null
    try {
      await onSave({ daily_credit_limit: nextLimit })
      onClose()
    } catch (err) {
      setSaveError('Unable to update the daily task limit. Try again.')
    }
  }, [dailyCreditInput, dailyCredits, onClose, onSave])

  const statusLabel = buildStatusLabel(status)
  const hasChanges = (() => {
    if (!dailyCredits) return false
    const trimmed = dailyCreditInput.trim()
    const normalized = trimmed ? Math.round(Number(trimmed)) : null
    if (!trimmed) {
      return dailyCredits.limit !== null
    }
    if (!Number.isFinite(normalized)) {
      return false
    }
    return normalized !== dailyCredits.limit
  })()

  const body = (
    <div className="agent-settings-panel">
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
        <div className="agent-settings-actions">
          <button
            type="button"
            className="agent-settings-save"
            onClick={handleSave}
            disabled={!onSave || !hasChanges || updating || loading}
          >
            {updating ? 'Saving...' : 'Save'}
          </button>
          <a href={agentSettingsUrl} className="agent-settings-link" target="_blank" rel="noreferrer">
            More Settings
            <ExternalLink size={14} />
          </a>
        </div>
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
