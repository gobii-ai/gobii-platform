import { useEffect, useState, type FormEvent } from 'react'
import { Flag, Loader2 } from 'lucide-react'

import type { AgentMessage } from '../../types/agentChat'
import { Modal } from '../common/Modal'

const REPORT_COMMENT_MAX_LENGTH = 2000

type ReportAgentMessageDialogProps = {
  message: AgentMessage | null
  busy?: boolean
  error?: string | null
  onClose: () => void
  onSubmit: (comment: string) => void | Promise<void>
}

export function ReportAgentMessageDialog({
  message,
  busy = false,
  error = null,
  onClose,
  onSubmit,
}: ReportAgentMessageDialogProps) {
  const [comment, setComment] = useState('')

  useEffect(() => {
    if (message) {
      setComment('')
    }
  }, [message?.id, message])

  if (!message) {
    return null
  }

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    void onSubmit(comment.trim())
  }

  return (
    <Modal
      title="Report message"
      subtitle="Tell us what went wrong so we can review this agent response."
      onClose={onClose}
      icon={Flag}
      iconBgClass="bg-white"
      iconColorClass="text-slate-700"
      widthClass="sm:max-w-lg"
      dismissible={!busy}
    >
      <form className="space-y-4" onSubmit={handleSubmit}>
        <label className="block text-sm font-medium text-slate-800" htmlFor="agent-message-report-comment">
          What should we know?
          <textarea
            id="agent-message-report-comment"
            value={comment}
            onChange={(event) => setComment(event.currentTarget.value.slice(0, REPORT_COMMENT_MAX_LENGTH))}
            className="mt-2 block min-h-28 w-full resize-y rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm leading-6 text-slate-800 shadow-none outline-none transition focus:border-slate-400 focus:ring-2 focus:ring-slate-200"
            placeholder="Optional details about what was incorrect, unhelpful, or concerning."
            disabled={busy}
          />
        </label>
        <div className="flex items-center justify-between gap-3 text-xs text-slate-500">
          <span>{comment.length}/{REPORT_COMMENT_MAX_LENGTH}</span>
          {error ? <span className="font-medium text-rose-600">{error}</span> : null}
        </div>
        <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          <button
            type="button"
            className="inline-flex items-center justify-center rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-semibold text-slate-700 transition hover:border-slate-300 hover:text-slate-900 disabled:cursor-not-allowed disabled:opacity-60"
            onClick={onClose}
            disabled={busy}
          >
            Cancel
          </button>
          <button
            type="submit"
            className="inline-flex items-center justify-center gap-2 rounded-lg bg-slate-900 px-3 py-2 text-sm font-semibold text-white transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-60"
            disabled={busy}
          >
            {busy ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : null}
            <span>{busy ? 'Submitting...' : 'Submit report'}</span>
          </button>
        </div>
      </form>
    </Modal>
  )
}
