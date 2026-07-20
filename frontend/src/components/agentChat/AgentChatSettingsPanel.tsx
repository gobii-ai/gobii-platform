import { useCallback, useEffect, useMemo, useState } from 'react'
import { AlertTriangle, ExternalLink, Settings } from 'lucide-react'

import { ImmersiveDialog } from '../common/ImmersiveDialog'
import { AgentIntelligenceSlider } from '../common/AgentIntelligenceSlider'
import { DailyCreditLimitControl } from '../agentSettings/DailyCreditLimitControl'
import { SettingsActionButton, SettingsStatusBadge } from '../agentSettings/SettingsControls'
import { InlineStatusBanner } from '../common/InlineStatusBanner'
import {
  getDailyCreditLimitConfig,
  getDailyCreditLimitMetrics,
  setDailyCreditInputValue,
  setDailyCreditSliderValue,
  setDailyCreditTier,
  type DailyCreditLimitValue,
} from '../agentSettings/dailyCreditLimit'
import type { ConsoleContext } from '../../api/context'
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
  context?: ConsoleContext | null
  onOpenFullSettings?: () => void
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
  context = null,
  onOpenFullSettings,
  onClose,
}: AgentChatSettingsPanelProps) {
  const resolvedTier = (currentLlmTier ?? 'standard') as IntelligenceTierKey
  const [limitValue, setLimitValue] = useState<DailyCreditLimitValue>({ tier: resolvedTier, sliderValue: 0, input: '' })
  const [saveError, setSaveError] = useState<string | null>(null)
  const { tier: stagedTier, input: dailyCreditInput } = limitValue
  const intelligenceDirty = stagedTier !== resolvedTier
  const showIntelligenceSelector = Boolean(llmIntelligence && currentLlmTier && onLlmTierChange)
  const showDailyCreditsSection = Boolean(onSave || dailyCredits || loading || error || status)

  const fallbackSliderMax = dailyCredits?.sliderMax ?? 0
  const fallbackSliderEmptyValue = dailyCredits?.sliderEmptyValue ?? fallbackSliderMax
  const fallbackSliderLimitMax = dailyCredits?.sliderLimitMax ?? fallbackSliderMax
  const limitConfig = useMemo(() => dailyCredits
    ? getDailyCreditLimitConfig(dailyCredits, llmIntelligence, fallbackSliderLimitMax)
    : null, [dailyCredits, fallbackSliderLimitMax, llmIntelligence])
  const limitMetrics = useMemo(() => limitConfig
    ? getDailyCreditLimitMetrics(limitConfig, stagedTier)
    : { min: 0, step: 1, limitMax: 0, max: 0, emptyValue: 0 }, [limitConfig, stagedTier])

  const handleTierChange = useCallback(
    (tier: IntelligenceTierKey) => {
      if (!llmTierSaving && limitConfig) setLimitValue((current) => setDailyCreditTier(current, tier, limitConfig))
    },
    [limitConfig, llmTierSaving],
  )

  const agentSettingsUrl = useMemo(() => {
    if (!agentId) return '/app/agents'
    const query = context
      ? `?context_type=${encodeURIComponent(context.type)}&context_id=${encodeURIComponent(context.id)}`
      : ''
    return `/app/agents/${agentId}/settings${query}`
  }, [agentId, context])

  useEffect(() => {
    if (!open || !dailyCredits) return
    const nextSliderValue = Number.isFinite(dailyCredits.sliderValue)
      ? dailyCredits.sliderValue
      : fallbackSliderEmptyValue
    setLimitValue((current) => ({
      ...current,
      sliderValue: nextSliderValue,
      input: dailyCredits.limit === null ? '' : String(Math.round(dailyCredits.limit)),
    }))
    setSaveError(null)
  }, [open, dailyCredits, fallbackSliderEmptyValue])

  useEffect(() => {
    if (!open) {
      return
    }
    setLimitValue((current) => ({ ...current, tier: resolvedTier }))
  }, [open, resolvedTier])

  const updateSliderValue = useCallback(
    (value: number) => {
      setLimitValue((current) => setDailyCreditSliderValue(current, value, limitMetrics))
    },
    [limitMetrics],
  )

  const handleDailyCreditInputChange = useCallback(
    (value: string) => {
      setLimitValue((current) => setDailyCreditInputValue(current, value, limitMetrics))
    },
    [limitMetrics],
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
          <div className="flex items-center justify-between gap-3">
            <h3 className="text-base font-semibold text-slate-900">Daily task credits</h3>
            {statusLabel ? (
              <SettingsStatusBadge surface="standalone" tone={statusLabel.tone === 'alert' ? 'danger' : 'warning'}>
                {statusLabel.tone === 'alert' ? <AlertTriangle size={14} /> : null}
                {statusLabel.label}
              </SettingsStatusBadge>
            ) : null}
          </div>

          {loading ? (
            <p className="agent-settings-helper">Loading daily credits...</p>
          ) : error ? (
            <InlineStatusBanner variant="error" density="compact">Unable to load daily credits. Try again.</InlineStatusBanner>
          ) : dailyCredits ? (
            <DailyCreditLimitControl
              id="daily-credit-limit"
              value={limitValue}
              metrics={limitMetrics}
              onSliderChange={updateSliderValue}
              onInputChange={handleDailyCreditInputChange}
              label="Adjust soft target"
              helperText="Leave blank to remove the daily target."
            />
          ) : (
            <p className="agent-settings-helper">Daily credits unavailable.</p>
          )}

          {saveError ? <InlineStatusBanner variant="error" density="compact">{saveError}</InlineStatusBanner> : null}
        </div>
      ) : null}
      {showIntelligenceSelector ? (
        <div className="agent-settings-section">
          <h3 className="text-base font-semibold text-slate-900">Intelligence</h3>
          <div className="agent-settings-intelligence">
            <AgentIntelligenceSlider
              config={llmIntelligence as LlmIntelligenceConfig}
              currentTier={stagedTier ?? resolvedTier}
              onTierChange={handleTierChange}
              disabled={!canManageAgent || llmTierSaving}
            />
          </div>
          {llmTierError ? <InlineStatusBanner variant="error" density="compact">{llmTierError}</InlineStatusBanner> : null}
        </div>
      ) : null}
      <div className="agent-settings-actions">
        <SettingsActionButton
          surface="standalone"
          tone="primary"
          onClick={handleSave}
          disabled={!canSave || !hasChanges || updating || loading || llmTierSaving}
        >
          {updating || llmTierSaving ? 'Saving...' : 'Save'}
        </SettingsActionButton>
        {onOpenFullSettings ? (
          <SettingsActionButton surface="standalone" onClick={onOpenFullSettings}>
            More Settings
          </SettingsActionButton>
        ) : (
          <SettingsActionButton as="a" surface="standalone" href={agentSettingsUrl} target="_blank" rel="noreferrer">
            More Settings
            <ExternalLink size={14} />
          </SettingsActionButton>
        )}
      </div>
    </div>
  )

  if (!open) {
    return null
  }

  return (
    <ImmersiveDialog
      open={open}
      onClose={onClose}
      title="Agent settings"
      icon={Settings}
      ariaLabel="Agent settings"
      desktopIconBgClass="bg-amber-100"
      desktopIconColorClass="text-amber-600"
      desktopBodyClassName="pr-0"
    >
      {body}
    </ImmersiveDialog>
  )
}
