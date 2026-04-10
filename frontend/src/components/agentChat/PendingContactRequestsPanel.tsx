import type { PendingContactRequestsAction } from '../../types/agentChat'
import { PendingActionSectionCard } from './PendingActionSectionCard'

export type PendingContactDraft = {
  allowInbound: boolean
  allowOutbound: boolean
}

type PendingContactRequestsPanelProps = {
  action: PendingContactRequestsAction
  disabled?: boolean
  busy?: boolean
  error?: string | null
  contactDrafts: Record<string, PendingContactDraft>
  onContactDraftChange: (requestId: string, nextDraft: PendingContactDraft) => void
  onSubmit: (decision: 'approve' | 'decline', requestId: string) => Promise<void> | void
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
  const activeRequest = action.requests[0] ?? null

  if (!activeRequest) {
    return null
  }

  const draft = contactDrafts[activeRequest.id] ?? {
    allowInbound: activeRequest.allowInbound,
    allowOutbound: activeRequest.allowOutbound,
  }

  return (
    <PendingActionSectionCard toneClass="border-amber-200 bg-amber-50/65">
      <div className="space-y-3">
        <div className="rounded-xl bg-white px-3 py-3">
          <div className="space-y-3">
            {activeRequest.reason ? (
              <div>
                <p className="text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">Reason</p>
                <p className="mt-1 whitespace-pre-line text-sm text-slate-700">{activeRequest.reason}</p>
              </div>
            ) : null}
            <div className="grid gap-2 md:grid-cols-2">
              <label className="flex items-start gap-2 rounded-xl border border-slate-200 px-3 py-2 text-sm text-slate-700">
                <input
                  type="checkbox"
                  checked={draft.allowInbound}
                  onChange={(event) => onContactDraftChange(activeRequest.id, { ...draft, allowInbound: event.currentTarget.checked })}
                  disabled={disabled || busy}
                  className="mt-0.5 h-4 w-4 rounded border-slate-300 text-amber-600 focus:ring-amber-500"
                />
                <span>Inbound</span>
              </label>
              <label className="flex items-start gap-2 rounded-xl border border-slate-200 px-3 py-2 text-sm text-slate-700">
                <input
                  type="checkbox"
                  checked={draft.allowOutbound}
                  onChange={(event) => onContactDraftChange(activeRequest.id, { ...draft, allowOutbound: event.currentTarget.checked })}
                  disabled={disabled || busy}
                  className="mt-0.5 h-4 w-4 rounded border-slate-300 text-amber-600 focus:ring-amber-500"
                />
                <span>Outbound</span>
              </label>
            </div>
          </div>
        </div>
        <div className="grid grid-cols-2 gap-2">
          <button
            type="button"
            disabled={disabled || busy}
            className="inline-flex w-full items-center justify-center rounded-xl border border-slate-300 bg-white px-3 py-2.5 text-sm font-semibold text-slate-700 transition hover:border-slate-400 hover:text-slate-900 disabled:cursor-not-allowed disabled:opacity-60"
            onClick={() => void onSubmit('decline', activeRequest.id)}
          >
            {busy ? 'Saving...' : 'Deny'}
          </button>
          <button
            type="button"
            disabled={disabled || busy}
            className="inline-flex w-full items-center justify-center rounded-xl bg-amber-600 px-3 py-2.5 text-sm font-semibold text-white transition hover:bg-amber-700 disabled:cursor-not-allowed disabled:opacity-60"
            onClick={() => void onSubmit('approve', activeRequest.id)}
          >
            {busy ? 'Saving...' : 'Approve'}
          </button>
        </div>
        {error ? <p className="text-sm text-rose-600">{error}</p> : null}
      </div>
    </PendingActionSectionCard>
  )
}
