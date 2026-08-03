import type { PendingOutboxReviewsAction } from '../../types/agentChat'
import { PendingRequestReviewFooter } from './PendingRequestPanelParts'

type PendingOutboxReviewPanelProps = {
  action: PendingOutboxReviewsAction
  disabled?: boolean
  busyDecision?: 'approve' | 'deny' | null
  error?: string | null
  onSubmit: (decision: 'approve' | 'deny', reviewId: string, expectedVersion: number) => Promise<void> | void
}

export function PendingOutboxReviewPanel({
  action,
  disabled = false,
  busyDecision = null,
  error = null,
  onSubmit,
}: PendingOutboxReviewPanelProps) {
  const activeReview = action.items[0] ?? null

  if (!activeReview) {
    return null
  }

  return (
    <div className="max-w-3xl space-y-3">
      <p className="text-xs leading-5 text-slate-600">
        The recipient has not received this message. Approve it to send now, or deny it to keep it from being sent.
      </p>
      <PendingRequestReviewFooter
        description="The exact message shown in the timeline will be sent."
        showSummary={false}
        disabled={disabled}
        busy={busyDecision !== null}
        secondaryLabel="Deny"
        secondaryBusyLabel={busyDecision === 'deny' ? 'Denying...' : 'Please wait...'}
        primaryLabel="Approve & send"
        primaryBusyLabel={busyDecision === 'approve' ? 'Sending...' : 'Please wait...'}
        theme="outbox"
        error={error}
        onSecondary={() => void onSubmit('deny', activeReview.id, activeReview.version)}
        onPrimary={() => void onSubmit('approve', activeReview.id, activeReview.version)}
      />
    </div>
  )
}
