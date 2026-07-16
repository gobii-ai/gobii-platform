import { useCallback, useState } from 'react'
import { ThumbsDown, ThumbsUp } from 'lucide-react'
import type { AgentMessage, AgentMessageFeedback } from './types'

type MessageFeedbackActionsProps = {
  message: AgentMessage
  onMessageFeedback?: (message: AgentMessage, feedback: AgentMessageFeedback | null) => Promise<AgentMessageFeedback | null>
}

export function MessageFeedbackActions({ message, onMessageFeedback }: MessageFeedbackActionsProps) {
  const [feedback, setFeedback] = useState<AgentMessageFeedback | null>(message.viewerFeedback ?? null)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleFeedback = useCallback(async (requestedFeedback: AgentMessageFeedback) => {
    if (!onMessageFeedback || submitting) {
      return
    }
    const previousFeedback = feedback
    const nextFeedback = feedback === requestedFeedback ? null : requestedFeedback
    setFeedback(nextFeedback)
    setError(null)
    setSubmitting(true)
    try {
      const savedFeedback = await onMessageFeedback(message, nextFeedback)
      setFeedback(savedFeedback)
    } catch {
      setFeedback(previousFeedback)
      setError('Unable to save feedback. Please try again.')
    } finally {
      setSubmitting(false)
    }
  }, [feedback, message, onMessageFeedback, submitting])

  return (
    <>
      <button
        type="button"
        className="chat-message-action-button"
        data-active={feedback === 'up' ? 'true' : 'false'}
        data-feedback="up"
        onClick={() => void handleFeedback('up')}
        disabled={submitting || !onMessageFeedback}
        title={error || (feedback === 'up' ? 'Remove thumbs up' : 'Thumbs up')}
        aria-label={feedback === 'up' ? 'Remove thumbs up feedback' : 'Give thumbs up feedback'}
        aria-pressed={feedback === 'up'}
      >
        <ThumbsUp className="h-3.5 w-3.5" aria-hidden="true" />
      </button>
      <button
        type="button"
        className="chat-message-action-button"
        data-active={feedback === 'down' ? 'true' : 'false'}
        data-feedback="down"
        onClick={() => void handleFeedback('down')}
        disabled={submitting || !onMessageFeedback}
        title={error || (feedback === 'down' ? 'Remove thumbs down' : 'Thumbs down')}
        aria-label={feedback === 'down' ? 'Remove thumbs down feedback' : 'Give thumbs down feedback'}
        aria-pressed={feedback === 'down'}
      >
        <ThumbsDown className="h-3.5 w-3.5" aria-hidden="true" />
      </button>
      <span className="sr-only" role="status" aria-live="polite">
        {error || (submitting ? 'Saving message feedback' : '')}
      </span>
    </>
  )
}
