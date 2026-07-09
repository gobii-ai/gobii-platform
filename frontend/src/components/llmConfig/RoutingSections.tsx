import { AlertCircle, Check, ChevronDown, ChevronUp, Clock3, Loader2, PlugZap, PlusCircle, ShieldCheck, Sparkles, Trash, Trash2, X } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'

import * as llmApi from '../../api/llmConfig'
import { actionKey, type ActivityNotice, buildTierGroups, button, getTierKey, getTierStyle, parseUnitInput, reasoningEffortOptions, roundToDisplayUnit, type Tier, type TierEndpoint, type TierGroup, type TierScope, type TokenRange } from './shared'

export function ActivityDock({
  notices,
  activeLabels,
  onDismiss,
}: {
  notices: ActivityNotice[]
  activeLabels: string[]
  onDismiss: (id: string) => void
}) {
  if (notices.length === 0 && activeLabels.length === 0) return null
  return (
    <div className="pointer-events-none fixed bottom-6 right-6 z-30 flex w-full max-w-sm flex-col gap-3">
      {activeLabels.length > 0 && (
        <div className="pointer-events-auto rounded-2xl border border-blue-100 bg-white/95 p-4 text-sm text-blue-800 shadow-2xl shadow-blue-100/80 backdrop-blur transition" aria-live="polite">
          <div className="flex items-start gap-3">
            <Loader2 className="size-5 animate-spin text-blue-500" aria-hidden />
            <div>
              <p className="text-xs font-semibold uppercase tracking-wide text-blue-500">Working on</p>
              <div className="mt-1 flex flex-wrap gap-1.5">
                {activeLabels.map((label) => (
                  <span key={label} className="rounded-full bg-blue-50 px-2 py-0.5 text-xs font-medium text-blue-700">
                    {label}
                  </span>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}
      {notices.map((notice) => (
        <div
          key={notice.id}
          className={`pointer-events-auto rounded-2xl border px-4 py-3 text-sm shadow-2xl transition ${notice.intent === 'success' ? 'border-emerald-100 bg-white/95 text-emerald-900 shadow-emerald-100/70' : 'border-rose-200 bg-white text-rose-900 shadow-rose-100/70'}`}
          role="status"
          aria-live="polite"
        >
          <div className="flex items-start gap-3">
            {notice.intent === 'success' ? <ShieldCheck className="mt-0.5 size-4 text-emerald-500" /> : <AlertCircle className="mt-0.5 size-4 text-rose-500" />}
            <div className="flex-1 space-y-0.5">
              {notice.context ? <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">{notice.context}</p> : null}
              <p>{notice.message}</p>
            </div>
            <button className={button.icon} onClick={() => onDismiss(notice.id)} aria-label="Dismiss notification">
              <X className="size-4" />
            </button>
          </div>
        </div>
      ))}
    </div>
  )
}

export function TierCard({
  tier,
  pendingWeights,
  scope,
  canMoveUp,
  canMoveDown,
  isDirty,
  isSaving,
  onMove,
  onRemove,
  onAddEndpoint,
  onStageEndpointWeight,
  onCommitEndpointWeights,
  onRemoveEndpoint,
  onUpdateEndpointReasoning,
  onUpdateExtraction,
  browserChoices,
  isActionBusy,
}: {
  tier: Tier
  pendingWeights: Record<string, number>
  scope: TierScope
  canMoveUp: boolean
  canMoveDown: boolean
  isDirty: boolean
  isSaving: boolean
  onMove: (direction: 'up' | 'down') => void
  onRemove: (tier: Tier) => void
  onAddEndpoint: () => void
  onStageEndpointWeight: (tier: Tier, tierEndpointId: string, weight: number, scope: TierScope) => void
  onCommitEndpointWeights: (tier: Tier, scope: TierScope) => void
  onRemoveEndpoint: (tier: Tier, endpoint: TierEndpoint) => void
  onUpdateEndpointReasoning?: (tier: Tier, endpoint: TierEndpoint, value: string | null, scope: TierScope) => void
  onUpdateExtraction?: (tier: Tier, endpoint: TierEndpoint, extractionId: string | null, scope: TierScope) => void
  browserChoices?: llmApi.ProviderEndpoint[]
  isActionBusy: (key: string) => boolean
}) {
  const [openReasoningFor, setOpenReasoningFor] = useState<string | null>(null)
  const tierStyle = getTierStyle(getTierKey(tier))
  const headerIcon = tierStyle.icon
  const canAdjustWeights = tier.endpoints.length > 1
  const disabledHint = canAdjustWeights ? '' : 'At least two endpoints are required to rebalance weights.'
  const handleCommit = () => {
    if (!canAdjustWeights) return
    onCommitEndpointWeights(tier, scope)
  }
  const rangeMoveBusy = scope === 'persistent' ? isActionBusy(actionKey('persistent-range', tier.rangeId, 'move')) : false
  const moveBusy = isActionBusy(actionKey(scope, tier.id, 'move'))
  const moveUpBusy = isActionBusy(actionKey(scope, tier.id, 'move', 'up'))
  const moveDownBusy = isActionBusy(actionKey(scope, tier.id, 'move', 'down'))
  const removeBusy = isActionBusy(actionKey(scope, tier.id, 'remove'))
  const addBusy = isActionBusy(actionKey(scope, tier.id, 'attach-endpoint'))
  const removingEndpoint = tier.endpoints.some((endpoint) => isActionBusy(actionKey('tier-endpoint', endpoint.id, 'remove')))
  const upDisabled = moveBusy || rangeMoveBusy || !canMoveUp
  const downDisabled = moveBusy || rangeMoveBusy || !canMoveDown

  const inlineStatus = (() => {
    if (isSaving) {
      return { icon: <Loader2 className="size-3 animate-spin" aria-hidden />, text: 'Saving…', className: 'text-blue-500' }
    }
    if (isDirty) {
      return { icon: <Clock3 className="size-3 animate-pulse" aria-hidden />, text: 'Pending…', className: 'text-amber-500' }
    }
    if (addBusy) {
      return { icon: <Loader2 className="size-3 animate-spin" aria-hidden />, text: 'Adding endpoint…', className: 'text-blue-500' }
    }
    if (removingEndpoint) {
      return { icon: <Loader2 className="size-3 animate-spin" aria-hidden />, text: 'Removing endpoint…', className: 'text-rose-500' }
    }
    return null
  })()
  return (
    <div className={`rounded-xl border ${tierStyle.borderClass} bg-white`}>
      <div className="flex items-center justify-between p-4 text-xs uppercase tracking-wide text-slate-500">
        <span className="flex items-center gap-2">{headerIcon} {tier.name}</span>
        <div className="flex items-center gap-1 text-xs">
          <button className={button.icon} type="button" onClick={() => onMove('up')} disabled={upDisabled}>
            {moveUpBusy ? <Loader2 className="size-4 animate-spin" /> : <ChevronUp className={`size-4 ${upDisabled ? 'text-slate-300' : ''}`} />}
          </button>
          <button className={button.icon} type="button" onClick={() => onMove('down')} disabled={downDisabled}>
            {moveDownBusy ? <Loader2 className="size-4 animate-spin" /> : <ChevronDown className={`size-4 ${downDisabled ? 'text-slate-300' : ''}`} />}
          </button>
          <button className={button.iconDanger} type="button" onClick={() => onRemove(tier)} disabled={removeBusy}>
            {removeBusy ? <Loader2 className="size-4 animate-spin" /> : <Trash2 className="size-4" />}
          </button>
        </div>
      </div>
      <div className="space-y-3 px-4 pb-4">
        <div className="flex items-center justify-between text-[13px] text-slate-500">
          <span>Weighted endpoints</span>
          {inlineStatus ? (
            <span className={`flex items-center gap-1 text-xs ${inlineStatus.className}`} aria-live="polite">
              {inlineStatus.icon} {inlineStatus.text}
            </span>
          ) : null}
        </div>
        <div className="space-y-3">
          {tier.endpoints.map((endpoint) => {
            const unitWeight = pendingWeights[endpoint.id] ?? endpoint.weight
            const displayWeight = roundToDisplayUnit(unitWeight)
            const reasoningValue = endpoint.reasoningEffortOverride ?? ''
            const reasoningBusy = isActionBusy(actionKey('tier-endpoint', endpoint.id, 'reasoning')) || isActionBusy(actionKey('profile-tier-endpoint', endpoint.id, 'reasoning'))
            const extractionBusy = isActionBusy(actionKey('tier-endpoint', endpoint.id, 'extraction')) || isActionBusy(actionKey('profile-tier-endpoint', endpoint.id, 'extraction'))
            const handleReasoningChange = (value: string) => {
              if (!onUpdateEndpointReasoning) return
              Promise.resolve(onUpdateEndpointReasoning(tier, endpoint, value || null, scope))
                .finally(() => setOpenReasoningFor(null))
                .catch(() => {})
            }
            const handleExtractionChange = (value: string | null) => {
              if (!onUpdateExtraction) return
              Promise.resolve(onUpdateExtraction(tier, endpoint, value, scope)).catch(() => {})
            }
            const effortOptions = reasoningEffortOptions.map((option, index) =>
              index === 0
                ? { ...option, label: `Use default (${endpoint.endpointReasoningEffort || 'none'})` }
                : option
            )
            const isMenuOpen = openReasoningFor === endpoint.id
            return (
              <div key={endpoint.id} className="space-y-2">
                <div className="flex flex-wrap items-center gap-2 text-sm font-medium text-slate-900/90">
                  <span className="flex min-w-0 flex-1 items-center gap-2 truncate" title={endpoint.label}><PlugZap className="size-4 flex-shrink-0 text-slate-400" /> {endpoint.label}</span>
                  <div className="flex items-center gap-2 relative">
                    {endpoint.supportsReasoning ? (
                      <div className="relative">
                        <button
                          type="button"
                          className={`${button.icon} ${reasoningValue ? 'text-blue-600' : ''}`}
                          aria-label="Set reasoning effort"
                          disabled={!onUpdateEndpointReasoning || reasoningBusy}
                          onClick={() => setOpenReasoningFor(isMenuOpen ? null : endpoint.id)}
                        >
                          {reasoningBusy ? <Loader2 className="size-4 animate-spin" /> : <Sparkles className="size-4" />}
                        </button>
                        {isMenuOpen && (
                          <div className="absolute right-0 top-10 z-20 w-48 rounded-xl border border-slate-200 bg-white shadow-xl">
                            {effortOptions.map((option) => (
                              <button
                                key={option.value || 'default'}
                                className="flex w-full items-center justify-between px-3 py-2 text-left text-xs hover:bg-slate-50"
                                onClick={() => handleReasoningChange(option.value)}
                                disabled={reasoningBusy}
                              >
                                <span>{option.label}</span>
                                {option.value === reasoningValue ? <Check className="size-3 text-blue-600" /> : null}
                              </button>
                            ))}
                          </div>
                        )}
                      </div>
                    ) : null}
                    <button onClick={() => onRemoveEndpoint(tier, endpoint)} className={button.iconDanger} aria-label="Remove endpoint">
                      <Trash className="size-4" />
                    </button>
                  </div>
                </div>
                {scope === 'browser' && browserChoices ? (
                  <div className="flex flex-wrap items-center gap-2 text-xs text-slate-600">
                    <span className="rounded-full bg-slate-100 px-2 py-0.5 font-semibold text-slate-700">Extraction</span>
                    <select
                      className="min-w-[180px] rounded-lg border-slate-300 text-sm shadow-sm focus:border-blue-500 focus:ring-blue-500"
                      value={endpoint.extractionEndpointId || ''}
                      onChange={(event) => handleExtractionChange(event.target.value || null)}
                      disabled={extractionBusy}
                    >
                      <option value="">Use primary model</option>
                      {browserChoices.map((choice) => (
                        <option key={choice.id} value={choice.id}>
                          {choice.label || choice.model}
                        </option>
                      ))}
                    </select>
                    {extractionBusy ? <Loader2 className="size-4 animate-spin text-blue-500" /> : null}
                    <span className="text-slate-500">
                      {endpoint.extractionLabel ? `Using ${endpoint.extractionLabel}` : 'Fallbacks to primary if unset'}
                    </span>
                  </div>
                ) : null}
                <div className="grid grid-cols-12 items-center gap-3">
                  <div className="col-span-12 md:col-span-7">
                    <input
                      type="range"
                      min="0"
                      max="1"
                      step="0.01"
                      value={displayWeight}
                      onChange={(event) => {
                        if (!canAdjustWeights) return
                        const decimal = parseUnitInput(event.target.valueAsNumber)
                        onStageEndpointWeight(tier, endpoint.id, decimal, scope)
                      }}
                      disabled={!canAdjustWeights}
                      onMouseUp={handleCommit}
                      onTouchEnd={handleCommit}
                      onPointerUp={handleCommit}
                      className="w-full h-2 bg-slate-200 rounded-lg appearance-none cursor-pointer"
                    />
                  </div>
                  <div className="col-span-12 md:col-span-5 flex items-center gap-2">
                    <input
                      type="number"
                      min="0"
                      max="1"
                      step="0.01"
                      value={displayWeight.toFixed(2)}
                      onChange={(event) => {
                        if (!canAdjustWeights) return
                        const decimal = parseUnitInput(event.target.valueAsNumber)
                        onStageEndpointWeight(tier, endpoint.id, decimal, scope)
                      }}
                      disabled={!canAdjustWeights}
                      onBlur={handleCommit}
                      inputMode="decimal"
                      className="block w-24 rounded-lg border-slate-300 text-right shadow-sm focus:border-blue-500 focus:ring-blue-500 sm:text-sm"
                    />
                  </div>
                </div>
              </div>
            )
          })}
          {!canAdjustWeights && tier.endpoints.length > 0 && (
            <p className="text-xs text-slate-400 text-right">{disabledHint}</p>
          )}
        </div>
        <div className="pt-2">
          <button type="button" className={button.muted} onClick={onAddEndpoint} disabled={addBusy}>
            {addBusy ? <Loader2 className="size-3 animate-spin" /> : <PlusCircle className="size-3" />} Add endpoint
          </button>
        </div>
      </div>
    </div>
  )
}

export function TierGroupSection({
  group,
  scope,
  pendingWeights,
  savingTierIds,
  dirtyTierIds,
  onAddTier,
  onMoveTier,
  onRemoveTier,
  onAddEndpoint,
  onStageEndpointWeight,
  onCommitEndpointWeights,
  onRemoveEndpoint,
  onUpdateEndpointReasoning,
  onUpdateExtraction,
  browserChoices,
  isActionBusy,
}: {
  group: TierGroup
  scope: TierScope
  pendingWeights: Record<string, number>
  savingTierIds: Set<string>
  dirtyTierIds: Set<string>
  onAddTier: (tierKey: string) => void
  onMoveTier: (tierId: string, direction: 'up' | 'down') => void
  onRemoveTier: (tier: Tier) => void
  onAddEndpoint: (tier: Tier) => void
  onStageEndpointWeight: (tier: Tier, tierEndpointId: string, weight: number, scope: TierScope) => void
  onCommitEndpointWeights: (tier: Tier, scope: TierScope) => void
  onRemoveEndpoint: (tier: Tier, endpoint: TierEndpoint) => void
  onUpdateEndpointReasoning?: (tier: Tier, endpoint: TierEndpoint, value: string | null, scope: TierScope) => void
  onUpdateExtraction?: (tier: Tier, endpoint: TierEndpoint, extractionId: string | null, scope: TierScope) => void
  browserChoices?: llmApi.ProviderEndpoint[]
  isActionBusy: (key: string) => boolean
}) {
  const tiers = group.tiers
  const multiplier = group.creditMultiplier && group.creditMultiplier !== '1.00'
    ? `${group.creditMultiplier}x credits`
    : null
  const labelLower = group.label.toLowerCase()

  return (
    <div className={`${group.style.sectionClass} p-4 space-y-3 rounded-xl`}>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <h4 className={`text-sm font-semibold ${group.style.headingClass} flex items-center gap-2`}>
            {group.style.icon}
            <span>{group.label} tiers</span>
          </h4>
          {multiplier ? (
            <span className="text-xs font-mono text-slate-500">{multiplier}</span>
          ) : null}
        </div>
        <button type="button" className={button.secondary} onClick={() => onAddTier(group.key)}>
          <PlusCircle className="size-4" /> Add
        </button>
      </div>
      {tiers.length === 0 && <p className={`text-center text-xs ${group.style.emptyClass} py-4`}>No {labelLower} tiers.</p>}
      {tiers.map((tier, index) => {
        const lastIndex = tiers.length - 1
        return (
          <TierCard
            key={tier.id}
            tier={tier}
            pendingWeights={pendingWeights}
            isDirty={dirtyTierIds.has(`${scope}:${tier.id}`)}
            isSaving={savingTierIds.has(`${scope}:${tier.id}`)}
            scope={scope}
            canMoveUp={index > 0}
            canMoveDown={index < lastIndex}
            onMove={(direction) => onMoveTier(tier.id, direction)}
            onRemove={onRemoveTier}
            onAddEndpoint={() => onAddEndpoint(tier)}
            onStageEndpointWeight={(currentTier, endpointId, weight) => onStageEndpointWeight(currentTier, endpointId, weight, scope)}
            onCommitEndpointWeights={(currentTier) => onCommitEndpointWeights(currentTier, scope)}
            onRemoveEndpoint={onRemoveEndpoint}
            onUpdateEndpointReasoning={(currentTier, endpoint, value) => onUpdateEndpointReasoning?.(currentTier, endpoint, value, scope)}
            onUpdateExtraction={(currentTier, endpoint, extractionId) => onUpdateExtraction?.(currentTier, endpoint, extractionId, scope)}
            browserChoices={browserChoices}
            isActionBusy={isActionBusy}
          />
        )
      })}
    </div>
  )
}

export function RangeSection({
  range,
  tiers,
  intelligenceTiers,
  onUpdate,
  onRemove,
  onAddTier,
  onMoveTier,
  onRemoveTier,
  onAddEndpoint,
  onStageEndpointWeight,
  onCommitEndpointWeights,
  onRemoveEndpoint,
  onUpdateEndpointReasoning,
  pendingWeights,
  savingTierIds,
  dirtyTierIds,
  isActionBusy,
}: {
  range: TokenRange
  tiers: Tier[]
  intelligenceTiers: llmApi.IntelligenceTier[]
  onUpdate: (field: 'name' | 'min_tokens' | 'max_tokens', value: string | number | null) => Promise<void> | void
  onRemove: () => void
  onAddTier: (tierKey: string) => void
  onMoveTier: (tierId: string, direction: 'up' | 'down') => void
  onRemoveTier: (tier: Tier) => void
  onAddEndpoint: (tier: Tier) => void
  onStageEndpointWeight: (tier: Tier, tierEndpointId: string, weight: number, scope: TierScope) => void
  onCommitEndpointWeights: (tier: Tier, scope: TierScope) => void
  onRemoveEndpoint: (tier: Tier, endpoint: TierEndpoint) => void
  onUpdateEndpointReasoning?: (tier: Tier, endpoint: TierEndpoint, value: string | null, scope: TierScope) => void
  pendingWeights: Record<string, number>
  savingTierIds: Set<string>
  dirtyTierIds: Set<string>
  isActionBusy: (key: string) => boolean
}) {
  const tierGroups = useMemo(() => buildTierGroups(tiers, intelligenceTiers), [tiers, intelligenceTiers])
  const [nameInput, setNameInput] = useState(range.name)
  const [minInput, setMinInput] = useState(range.min_tokens.toString())
  const [maxInput, setMaxInput] = useState(range.max_tokens?.toString() ?? '')

  useEffect(() => {
    setNameInput(range.name)
    setMinInput(range.min_tokens.toString())
    setMaxInput(range.max_tokens?.toString() ?? '')
  }, [range])

  const nameBusy = isActionBusy(actionKey('range', range.id, 'name'))
  const minBusy = isActionBusy(actionKey('range', range.id, 'min_tokens'))
  const maxBusy = isActionBusy(actionKey('range', range.id, 'max_tokens'))
  const removeBusy = isActionBusy(actionKey('range', range.id, 'remove'))

  const commitField = (field: 'name' | 'min_tokens' | 'max_tokens') => {
    if (field === 'name') {
      const trimmed = nameInput.trim()
      if (!trimmed || trimmed === range.name) {
        setNameInput(range.name)
        return
      }
      Promise.resolve(onUpdate('name', trimmed)).catch(() => setNameInput(range.name))
      return
    }
    if (field === 'min_tokens') {
      const parsed = Number(minInput)
      if (Number.isNaN(parsed)) {
        setMinInput(range.min_tokens.toString())
        return
      }
      if (parsed === range.min_tokens) return
      Promise.resolve(onUpdate('min_tokens', parsed)).catch(() => setMinInput(range.min_tokens.toString()))
      return
    }
    const parsed = maxInput === '' ? null : Number(maxInput)
    if (maxInput !== '' && Number.isNaN(parsed as number)) {
      setMaxInput(range.max_tokens?.toString() ?? '')
      return
    }
    if (parsed === range.max_tokens) return
    Promise.resolve(onUpdate('max_tokens', parsed)).catch(() => setMaxInput(range.max_tokens?.toString() ?? ''))
  }

  return (
    <div className="rounded-2xl border border-slate-200/80 bg-white">
      <div className="p-4 space-y-3">
        <div className="grid grid-cols-12 items-center gap-3 text-sm">
          <div className="col-span-12 sm:col-span-3 relative">
            <label className="absolute -top-2 left-2 text-xs text-slate-400 bg-white px-1">Range Name</label>
            <input
              type="text"
              value={nameInput}
              disabled={nameBusy}
              onChange={(event) => setNameInput(event.target.value)}
              onBlur={() => commitField('name')}
              className="block w-full rounded-lg border-slate-300 shadow-sm focus:border-blue-500 focus:ring-blue-500 sm:text-sm"
            />
          </div>
          <div className="col-span-6 sm:col-span-3 relative">
            <label className="absolute -top-2 left-2 text-xs text-slate-400 bg-white px-1">Min Tokens</label>
            <input
              type="number"
              value={minInput}
              disabled={minBusy}
              onChange={(event) => setMinInput(event.target.value)}
              onBlur={() => commitField('min_tokens')}
              className="block w-full rounded-lg border-slate-300 shadow-sm focus:border-blue-500 focus:ring-blue-500 sm:text-sm"
            />
          </div>
          <div className="col-span-6 sm:col-span-3 relative">
            <label className="absolute -top-2 left-2 text-xs text-slate-400 bg-white px-1">Max Tokens</label>
            <input
              type="number"
              value={maxInput}
              disabled={maxBusy}
              placeholder="Infinity"
              onChange={(event) => setMaxInput(event.target.value)}
              onBlur={() => commitField('max_tokens')}
              className="block w-full rounded-lg border-slate-300 shadow-sm focus:border-blue-500 focus:ring-blue-500 sm:text-sm"
            />
          </div>
          <div className="col-span-12 sm:col-span-3 text-right">
            <button type="button" className={button.danger} onClick={onRemove} disabled={removeBusy}>
              {removeBusy ? <Loader2 className="size-4 animate-spin" /> : <Trash2 className="size-4" />} Remove Range
            </button>
          </div>
        </div>
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 p-4">
        {tierGroups.map((group) => (
          <TierGroupSection
            key={`${range.id}:${group.key}`}
            group={group}
            scope="persistent"
            pendingWeights={pendingWeights}
            savingTierIds={savingTierIds}
            dirtyTierIds={dirtyTierIds}
            onAddTier={onAddTier}
            onMoveTier={onMoveTier}
            onRemoveTier={onRemoveTier}
            onAddEndpoint={onAddEndpoint}
            onStageEndpointWeight={onStageEndpointWeight}
            onCommitEndpointWeights={onCommitEndpointWeights}
            onRemoveEndpoint={onRemoveEndpoint}
            onUpdateEndpointReasoning={onUpdateEndpointReasoning}
            isActionBusy={isActionBusy}
          />
        ))}
      </div>
    </div>
  )
}
