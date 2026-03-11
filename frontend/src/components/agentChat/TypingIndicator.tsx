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
  streaming: _streaming,
  processingWebTasks: _processingWebTasks,
  awaitingResponse: _awaitingResponse,
}: {
  streaming: StreamState | null | undefined
  processingWebTasks: ProcessingWebTask[]
  awaitingResponse: boolean
}): string {
  return 'Working...'
}

export function TypingIndicator({
  statusText,
  agentColorHex,
  agentAvatarUrl,
  agentFirstName,
  hidden,
}: TypingIndicatorProps) {
  const avatarColor = agentColorHex || '#475569'

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
              style={{ backgroundColor: avatarColor }}
            >
              {agentFirstName.charAt(0).toUpperCase()}
            </div>
          )}
        </div>
        <div className="typing-indicator__body">
          <div className="typing-indicator__bubble">
            <span className="typing-indicator__dot" />
            <span className="typing-indicator__dot" />
            <span className="typing-indicator__dot" />
          </div>
          <span className="typing-indicator__status">
            {statusText}
          </span>
        </div>
      </div>
    </div>
  )
}
