import { ArrowRight, X } from 'lucide-react'

import type { PendingJudgeSuggestionAction } from '../../types/agentChat'
import { PendingActionSectionCard } from './PendingActionSectionCard'

type PendingJudgeSuggestionPanelProps = {
  action: PendingJudgeSuggestionAction
  disabled?: boolean
  busy?: boolean
  error?: string | null
  onOpenSettings?: (settingsUrl?: string | null) => void
  onDismiss?: (dismissApiUrl: string) => Promise<void> | void
}

function ctaLabel(action: PendingJudgeSuggestionAction): string {
  if (action.suggestionType === 'intelligence_upgrade') {
    return 'Review intelligence'
  }
  return 'Open settings'
}

export function PendingJudgeSuggestionPanel({
  action,
  disabled = false,
  busy = false,
  error = null,
  onOpenSettings,
  onDismiss,
}: PendingJudgeSuggestionPanelProps) {
  const canDismiss = Boolean(action.dismissApiUrl && onDismiss)
  return (
    <PendingActionSectionCard toneClass="border-violet-200 bg-violet-50/60">
      <div className="space-y-3">
        <p className="text-sm leading-6 text-slate-700">{action.message}</p>
        {action.recommendedTier ? (
          <p className="text-xs font-medium uppercase tracking-[0.14em] text-violet-700">
            Recommended: {action.recommendedTier.replace(/_/g, ' ')}
          </p>
        ) : null}
        {error ? <p className="text-sm text-rose-600">{error}</p> : null}
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            className="inline-flex items-center gap-2 rounded-lg bg-violet-600 px-3 py-2 text-sm font-semibold text-white transition hover:bg-violet-700 disabled:cursor-not-allowed disabled:opacity-60"
            onClick={() => onOpenSettings?.(action.settingsUrl)}
            disabled={disabled || busy || !onOpenSettings}
          >
            {ctaLabel(action)}
            <ArrowRight className="h-4 w-4" aria-hidden="true" />
          </button>
          {canDismiss ? (
            <button
              type="button"
              className="inline-flex items-center gap-2 rounded-lg border border-violet-200 bg-white px-3 py-2 text-sm font-medium text-violet-700 transition hover:border-violet-300 hover:bg-violet-50 disabled:cursor-not-allowed disabled:opacity-60"
              onClick={() => {
                if (action.dismissApiUrl) {
                  onDismiss?.(action.dismissApiUrl)
                }
              }}
              disabled={disabled || busy}
            >
              <X className="h-4 w-4" aria-hidden="true" />
              Dismiss
            </button>
          ) : null}
        </div>
      </div>
    </PendingActionSectionCard>
  )
}
