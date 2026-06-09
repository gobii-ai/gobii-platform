import { createPortal } from 'react-dom'

import type { PendingContactRequestsAction } from '../../types/agentChat'

export type PendingContactDraft = {
  allowInbound: boolean
  allowOutbound: boolean
  smsContactPermissionAttested: boolean
}

type PendingContactRequestsPanelProps = {
  action: PendingContactRequestsAction
  disabled?: boolean
  busy?: boolean
  error?: string | null
  contactDrafts: Record<string, PendingContactDraft>
  onContactDraftChange: (requestId: string, nextDraft: PendingContactDraft) => void
  onSubmit: (decision: 'approve' | 'decline', requestId: string) => Promise<void> | void
  actionsContainer?: Element | null
  suppressInlineActions?: boolean
}

export function PendingContactRequestsPanel({
  action,
  disabled = false,
  busy = false,
  error = null,
  contactDrafts,
  onContactDraftChange,
  onSubmit,
  actionsContainer = null,
  suppressInlineActions = false,
}: PendingContactRequestsPanelProps) {
  const activeRequest = action.requests[0] ?? null

  if (!activeRequest) {
    return null
  }

  const draft = contactDrafts[activeRequest.id] ?? {
    allowInbound: activeRequest.allowInbound,
    allowOutbound: activeRequest.allowOutbound,
    smsContactPermissionAttested: Boolean(activeRequest.smsContactPermissionAttested),
  }
  const smsApprovalBlocked = (
    activeRequest.channel === 'sms'
    && !draft.smsContactPermissionAttested
  )

  const actionRow = (
    <div className="space-y-2">
      <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-start">
        <button
          type="button"
          disabled={disabled || busy}
          className="inline-flex w-full items-center justify-center rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-semibold text-slate-700 transition hover:border-slate-400 hover:text-slate-900 disabled:cursor-not-allowed disabled:opacity-60 sm:w-32"
          onClick={() => void onSubmit('decline', activeRequest.id)}
        >
          {busy ? 'Saving...' : 'Deny'}
        </button>
        <button
          type="button"
          disabled={disabled || busy || smsApprovalBlocked}
          className="inline-flex w-full items-center justify-center rounded-lg bg-amber-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-amber-700 disabled:cursor-not-allowed disabled:opacity-60 sm:w-32"
          onClick={() => void onSubmit('approve', activeRequest.id)}
        >
          {busy ? 'Saving...' : 'Approve'}
        </button>
      </div>
      {error ? <p className="text-sm text-rose-600 sm:text-right">{error}</p> : null}
    </div>
  )

  return (
    <div className="max-w-3xl space-y-3">
      <div className="space-y-3">
        {activeRequest.reason ? (
          <div>
            <p className="text-xs font-semibold text-slate-900">Reason</p>
            <p className="mt-1 whitespace-pre-line text-sm leading-5 text-slate-700">{activeRequest.reason}</p>
          </div>
        ) : null}

        {activeRequest.channel === 'sms' && activeRequest.smsContactPurpose ? (
          <div>
            <p className="text-xs font-semibold text-slate-900">SMS purpose</p>
            <p className="mt-1 text-sm leading-5 text-slate-700">{activeRequest.smsContactPurpose.replace(/_/g, ' ')}</p>
            {activeRequest.smsContactPurposeDetails ? (
              <p className="mt-1 whitespace-pre-line text-sm leading-5 text-slate-600">{activeRequest.smsContactPurposeDetails}</p>
            ) : null}
          </div>
        ) : null}

        <div className="space-y-2">
          <p className="text-xs font-semibold text-slate-900">Permissions</p>
          <div className="max-w-2xl space-y-2">
            <label className="flex min-h-12 items-center justify-between gap-3 rounded-lg border border-slate-200/70 bg-white/56 px-3 py-2.5 text-sm text-slate-800">
              <span className="inline-flex min-w-0 items-center gap-3">
                <input
                  type="checkbox"
                  checked={draft.allowInbound}
                  onChange={(event) => onContactDraftChange(activeRequest.id, { ...draft, allowInbound: event.currentTarget.checked })}
                  disabled={disabled || busy}
                  className="h-4 w-4 rounded border-slate-300 text-amber-600 focus:ring-amber-500"
                />
                <span className="truncate font-semibold">Allow inbound messages</span>
              </span>
              <span className="hidden shrink-0 rounded-md bg-emerald-100 px-2 py-1 text-xs font-semibold text-emerald-700 sm:inline-flex">
                Recommended
              </span>
            </label>
            <label className="flex min-h-12 items-center justify-between gap-3 rounded-lg border border-slate-200/70 bg-white/56 px-3 py-2.5 text-sm text-slate-800">
              <span className="inline-flex min-w-0 items-center gap-3">
                <input
                  type="checkbox"
                  checked={draft.allowOutbound}
                  onChange={(event) => onContactDraftChange(activeRequest.id, { ...draft, allowOutbound: event.currentTarget.checked })}
                  disabled={disabled || busy}
                  className="h-4 w-4 rounded border-slate-300 text-amber-600 focus:ring-amber-500"
                />
                <span className="truncate font-semibold">Allow outbound messages</span>
              </span>
              <span className="hidden shrink-0 rounded-md bg-emerald-100 px-2 py-1 text-xs font-semibold text-emerald-700 sm:inline-flex">
                Recommended
              </span>
            </label>
          </div>
          {activeRequest.channel === 'sms' ? (
            <label className="inline-flex max-w-full items-start gap-2 rounded-lg border border-amber-200 bg-amber-50/60 px-2.5 py-1.5 text-sm text-slate-700">
              <input
                type="checkbox"
                checked={draft.smsContactPermissionAttested}
                onChange={(event) => onContactDraftChange(activeRequest.id, { ...draft, smsContactPermissionAttested: event.currentTarget.checked })}
                disabled={disabled || busy}
                className="mt-0.5 h-4 w-4 rounded border-slate-300 text-amber-600 focus:ring-amber-500"
              />
              <span>I confirm I have permission to contact this number by SMS.</span>
            </label>
          ) : null}
        </div>
      </div>

      {actionsContainer ? createPortal(actionRow, actionsContainer) : suppressInlineActions ? null : actionRow}
    </div>
  )
}
