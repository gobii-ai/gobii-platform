import type { PendingContactRequestsAction } from '../../types/agentChat'
import { PendingActionSectionCard } from './PendingActionSectionCard'

export type PendingContactDraft = {
  decision: 'approve' | 'decline'
  allowInbound: boolean
  allowOutbound: boolean
  canConfigure: boolean
}

type PendingContactRequestsPanelProps = {
  action: PendingContactRequestsAction
  disabled?: boolean
  busy?: boolean
  error?: string | null
  contactDrafts: Record<string, PendingContactDraft>
  onContactDraftChange: (requestId: string, nextDraft: PendingContactDraft) => void
  onSubmit: () => Promise<void> | void
}

export function PendingContactRequestsPanel({
  action,
  disabled = false,
  busy = false,
  error = null,
  contactDrafts,
  onContactDraftChange,
  onSubmit,
}: PendingContactRequestsPanelProps) {
  return (
    <PendingActionSectionCard toneClass="border-amber-200 bg-amber-50/65" title="Review contact access" meta="Approve or decline in place">
      <div className="space-y-3">
        {action.requests.map((request) => {
          const draft = contactDrafts[request.id] ?? {
            decision: 'approve' as const,
            allowInbound: request.allowInbound,
            allowOutbound: request.allowOutbound,
            canConfigure: request.canConfigure,
          }
          return (
            <div key={request.id} className="rounded-xl bg-white px-3 py-3">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <p className="text-sm font-semibold text-slate-900">{request.name || request.address}</p>
                  <p className="mt-1 text-xs text-slate-600">{request.channel} · {request.address}</p>
                  {request.purpose ? <p className="mt-2 text-sm text-slate-800">Purpose: {request.purpose}</p> : null}
                  {request.reason ? <p className="mt-1 whitespace-pre-line text-sm text-slate-700">{request.reason}</p> : null}
                </div>
                <div className="flex shrink-0 rounded-xl bg-amber-100 p-1">
                  <button
                    type="button"
                    className={`rounded-lg px-3 py-1.5 text-xs font-semibold transition ${draft.decision === 'approve' ? 'bg-white text-amber-900 shadow-sm' : 'text-amber-700'}`}
                    onClick={() => onContactDraftChange(request.id, { ...draft, decision: 'approve' })}
                    disabled={disabled || busy}
                  >
                    Approve
                  </button>
                  <button
                    type="button"
                    className={`rounded-lg px-3 py-1.5 text-xs font-semibold transition ${draft.decision === 'decline' ? 'bg-white text-amber-900 shadow-sm' : 'text-amber-700'}`}
                    onClick={() => onContactDraftChange(request.id, { ...draft, decision: 'decline' })}
                    disabled={disabled || busy}
                  >
                    Decline
                  </button>
                </div>
              </div>
              <div className="mt-3 grid gap-2 md:grid-cols-3">
                <label className="flex items-start gap-2 rounded-xl border border-slate-200 px-3 py-2 text-sm text-slate-700">
                  <input
                    type="checkbox"
                    checked={draft.allowInbound}
                    onChange={(event) => onContactDraftChange(request.id, { ...draft, allowInbound: event.currentTarget.checked })}
                    disabled={disabled || busy}
                    className="mt-0.5 h-4 w-4 rounded border-slate-300 text-amber-600 focus:ring-amber-500"
                  />
                  <span>Allow inbound</span>
                </label>
                <label className="flex items-start gap-2 rounded-xl border border-slate-200 px-3 py-2 text-sm text-slate-700">
                  <input
                    type="checkbox"
                    checked={draft.allowOutbound}
                    onChange={(event) => onContactDraftChange(request.id, { ...draft, allowOutbound: event.currentTarget.checked })}
                    disabled={disabled || busy}
                    className="mt-0.5 h-4 w-4 rounded border-slate-300 text-amber-600 focus:ring-amber-500"
                  />
                  <span>Allow outbound</span>
                </label>
                <label className="flex items-start gap-2 rounded-xl border border-slate-200 px-3 py-2 text-sm text-slate-700">
                  <input
                    type="checkbox"
                    checked={draft.canConfigure}
                    onChange={(event) => onContactDraftChange(request.id, { ...draft, canConfigure: event.currentTarget.checked })}
                    disabled={disabled || busy}
                    className="mt-0.5 h-4 w-4 rounded border-slate-300 text-amber-600 focus:ring-amber-500"
                  />
                  <span>Can configure agent</span>
                </label>
              </div>
            </div>
          )
        })}
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            disabled={disabled || busy}
            className="inline-flex items-center rounded-xl bg-amber-600 px-3 py-2 text-sm font-semibold text-white transition hover:bg-amber-700 disabled:cursor-not-allowed disabled:opacity-60"
            onClick={() => void onSubmit()}
          >
            {busy ? 'Saving...' : 'Submit decisions'}
          </button>
        </div>
        {error ? <p className="text-sm text-rose-600">{error}</p> : null}
      </div>
    </PendingActionSectionCard>
  )
}
