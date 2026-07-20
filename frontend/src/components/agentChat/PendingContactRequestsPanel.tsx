import { Inbox, Send } from 'lucide-react'

import type { PendingContactRequestsAction } from '../../types/agentChat'
import { PendingRequestReviewFooter } from './PendingRequestPanelParts'

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
  notice?: string | null
  contactDrafts: Record<string, PendingContactDraft>
  showReviewSummary?: boolean
  onContactDraftChange: (requestId: string, nextDraft: PendingContactDraft) => void
  onSubmit: (decision: 'approve' | 'decline', requestId: string) => Promise<void> | void
}

export function PendingContactRequestsPanel({
  action,
  disabled = false,
  busy = false,
  error = null,
  notice = null,
  contactDrafts,
  showReviewSummary = true,
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
    smsContactPermissionAttested: Boolean(activeRequest.smsContactPermissionAttested),
  }
  const smsApprovalBlocked = (
    activeRequest.channel === 'sms'
    && !draft.smsContactPermissionAttested
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
                <span className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-amber-50 text-amber-700">
                  <Inbox className="h-4 w-4" aria-hidden="true" />
                </span>
                <span className="truncate font-semibold">Allow inbound</span>
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
                <span className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-amber-50 text-amber-700">
                  <Send className="h-4 w-4" aria-hidden="true" />
                </span>
                <span className="truncate font-semibold">Allow outbound</span>
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

      <PendingRequestReviewFooter
        description="You're allowing this contact to message your team."
        showSummary={showReviewSummary}
        disabled={disabled}
        busy={busy}
        secondaryLabel="Deny"
        secondaryBusyLabel="Saving..."
        primaryLabel="Approve"
        primaryBusyLabel="Saving..."
        primaryDisabled={smsApprovalBlocked}
        theme="contact"
        error={error}
        notice={notice}
        onSecondary={() => void onSubmit('decline', activeRequest.id)}
        onPrimary={() => void onSubmit('approve', activeRequest.id)}
      />
    </div>
  )
}
