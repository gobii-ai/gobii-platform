import type { StreamState } from '../../types/agentChat'

type StreamEventCardProps = {
  stream: StreamState
  agentFirstName: string
}

export function StreamEventCard({ stream, agentFirstName }: StreamEventCardProps) {
  const hasReasoning = stream.reasoning.trim().length > 0
  const hasContent = stream.content.trim().length > 0
  const statusLabel = stream.done ? 'Draft ready' : 'Live response'

  return (
    <article className="timeline-event chat-event is-agent" data-stream-state={stream.done ? 'done' : 'streaming'}>
      <div className="chat-bubble chat-bubble--agent chat-bubble--stream">
        <div className="chat-author chat-author--agent">
          {agentFirstName} - {statusLabel}
        </div>
        {hasReasoning ? (
          <div className="chat-stream-section" data-section="reasoning">
            <div className="chat-stream-label">Thinking</div>
            <div className="chat-stream-body">{stream.reasoning}</div>
          </div>
        ) : null}
        {hasContent ? (
          <div className="chat-stream-section" data-section="reply">
            <div className="chat-stream-label">Reply</div>
            <div className="chat-stream-body">{stream.content}</div>
          </div>
        ) : (
          <div className="chat-stream-placeholder">
            <span className="chat-stream-dot" aria-hidden="true" />
            <span>{stream.done ? 'No message content.' : 'Drafting reply...'}</span>
          </div>
        )}
      </div>
    </article>
  )
}
