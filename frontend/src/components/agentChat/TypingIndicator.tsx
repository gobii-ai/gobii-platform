import type { ProcessingWebTask, StreamState } from '../../types/agentChat'
import '../../styles/simplifiedChat.css'

type TypingIndicatorProps = {
  statusText: string
  agentColorHex?: string
  agentAvatarUrl?: string | null
  agentFirstName: string
  hidden?: boolean
}

export function deriveTypingStatusText({
  streaming,
  processingWebTasks,
  awaitingResponse,
}: {
  streaming: StreamState | null | undefined
  processingWebTasks: ProcessingWebTask[]
  awaitingResponse: boolean
}): string {
  if (streaming && !streaming.done) {
    if (streaming.content?.trim()) return 'Composing...'
    if (streaming.reasoning?.trim()) return 'Thinking...'
  }
  if (processingWebTasks.length > 0) {
    const task = processingWebTasks[0]
    if (task.statusLabel) return task.statusLabel
  }
  if (awaitingResponse) return 'Composing...'
  return 'Working...'
}

export function TypingIndicator({
  statusText,
  agentColorHex,
  agentAvatarUrl,
  agentFirstName,
  hidden,
}: TypingIndicatorProps) {
  const dotColor = agentColorHex || '#8b5cf6'

  return (
    <div
      className="typing-indicator-container"
      hidden={hidden}
      aria-hidden={hidden ? 'true' : undefined}
    >
      <div className="typing-indicator" role="status" aria-label={`${agentFirstName} is ${statusText.toLowerCase().replace('...', '')}`}>
        <div className="typing-indicator__avatar">
          {agentAvatarUrl ? (
            <img src={agentAvatarUrl} alt="" className="typing-indicator__avatar-img" />
          ) : (
            <div
              className="typing-indicator__avatar-fallback"
              style={{ backgroundColor: dotColor }}
            >
              {agentFirstName.charAt(0).toUpperCase()}
            </div>
          )}
        </div>
        <div className="typing-indicator__body">
          <div className="typing-indicator__bubble" style={{ '--dot-color': dotColor } as React.CSSProperties}>
            <span className="typing-indicator__dot" />
            <span className="typing-indicator__dot" />
            <span className="typing-indicator__dot" />
          </div>
          <span className="typing-indicator__status" style={{ '--glow-color': dotColor } as React.CSSProperties}>
            {statusText}
          </span>
        </div>
      </div>
    </div>
  )
}
