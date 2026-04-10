import type { PendingSpawnRequestAction } from '../../types/agentChat'
import { PendingActionSectionCard } from './PendingActionSectionCard'

type PendingSpawnRequestPanelProps = {
  action: PendingSpawnRequestAction
  disabled?: boolean
  busyDecision?: 'approve' | 'decline' | null
  error?: string | null
  onResolve: (decision: 'approve' | 'decline') => Promise<void> | void
}

export function PendingSpawnRequestPanel({
  action,
  disabled = false,
  busyDecision = null,
  error = null,
  onResolve,
}: PendingSpawnRequestPanelProps) {
  return (
    <PendingActionSectionCard toneClass="border-emerald-200 bg-emerald-50/55" title="Specialist handoff">
      <div className="space-y-3 text-sm text-slate-700">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">Charter</p>
          <p className="mt-1 whitespace-pre-line rounded-xl bg-white px-3 py-3 text-slate-800">{action.requestedCharter}</p>
        </div>
        {action.requestReason ? (
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">Why this is needed</p>
            <p className="mt-1 whitespace-pre-line rounded-xl bg-white px-3 py-3 text-slate-800">{action.requestReason}</p>
          </div>
        ) : null}
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            disabled={disabled || busyDecision !== null}
            className="inline-flex items-center rounded-xl bg-emerald-600 px-3 py-2 text-sm font-semibold text-white transition hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-60"
            onClick={() => void onResolve('approve')}
          >
            {busyDecision === 'approve' ? 'Creating...' : 'Create'}
          </button>
          <button
            type="button"
            disabled={disabled || busyDecision !== null}
            className="inline-flex items-center rounded-xl border border-slate-300 bg-white px-3 py-2 text-sm font-semibold text-slate-700 transition hover:border-slate-400 hover:text-slate-900 disabled:cursor-not-allowed disabled:opacity-60"
            onClick={() => void onResolve('decline')}
          >
            {busyDecision === 'decline' ? 'Declining...' : 'Decline'}
          </button>
        </div>
        {error ? <p className="text-sm text-rose-600">{error}</p> : null}
      </div>
    </PendingActionSectionCard>
  )
}
