import { useState } from 'react'
import { ThumbsDown, ThumbsUp } from 'lucide-react'
import type { AgentMessage, AgentMessageFeedback } from './types'
import { updateAgentMessageFeedback } from '../../api/agentChat'

const FEEDBACK_OPTIONS = [
  { value: 'up', label: 'Thumbs up', Icon: ThumbsUp },
  { value: 'down', label: 'Thumbs down', Icon: ThumbsDown },
] as const

type MessageFeedbackActionsProps = {
  agentId?: string | null
  message: AgentMessage
}

export function MessageFeedbackActions({ agentId, message }: MessageFeedbackActionsProps) {
  const [feedback, setFeedback] = useState<AgentMessageFeedback | null>(message.viewerFeedback ?? null)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleFeedback(requestedFeedback: AgentMessageFeedback) {
    if (!agentId || submitting) {
      return
    }
    const previousFeedback = feedback
    const nextFeedback = feedback === requestedFeedback ? null : requestedFeedback
    setFeedback(nextFeedback)
    setError(null)
    setSubmitting(true)
    try {
      const response = await updateAgentMessageFeedback(agentId, message.id, nextFeedback)
      setFeedback(response.feedback)
    } catch {
      setFeedback(previousFeedback)
      setError('Unable to save feedback. Please try again.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <>
      {FEEDBACK_OPTIONS.map(({ value, label, Icon }) => {
        const active = feedback === value
        return (
          <button
            key={value}
            type="button"
            className="chat-message-action-button"
            data-active={active ? 'true' : 'false'}
            data-feedback={value}
            onClick={() => void handleFeedback(value)}
            disabled={submitting || !agentId}
            title={error || (active ? `Remove ${label.toLowerCase()}` : label)}
            aria-label={active ? `Remove ${label.toLowerCase()} feedback` : `Give ${label.toLowerCase()} feedback`}
            aria-pressed={active}
          >
            <Icon className="h-3.5 w-3.5" aria-hidden="true" />
          </button>
        )
      })}
      {error ? <span className="chat-message-feedback-error absolute right-0 top-[calc(100%+0.3rem)] z-[1] w-max max-w-[min(16rem,80vw)] rounded-md border border-rose-600/35 bg-white px-2 py-1 text-[0.6875rem] font-medium leading-tight text-rose-700" role="alert">{error}</span> : null}
      <span className="sr-only" role="status" aria-live="polite">
        {submitting ? 'Saving message feedback' : ''}
      </span>
    </>
  )
}
