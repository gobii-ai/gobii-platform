import { useMemo } from 'react'

import type { StreamState } from '../../types/agentChat'
import { MarkdownViewer } from '../common/MarkdownViewer'
import { looksLikeHtml, sanitizeHtml } from '../../util/sanitize'

type StreamEventCardProps = {
  stream: StreamState
  agentFirstName: string
}

export function StreamEventCard({ stream, agentFirstName }: StreamEventCardProps) {
  const hasReasoning = stream.reasoning.trim().length > 0
  const hasContent = stream.content.trim().length > 0
  const statusLabel = stream.done ? 'Draft ready' : 'Live response'
  const htmlSource = useMemo(() => {
    const raw = stream.content
    if (!raw || !looksLikeHtml(raw)) {
      return null
    }
    return sanitizeHtml(raw)
  }, [stream.content])

  return (
    <article className="timeline-event chat-event is-agent" data-stream-state={stream.done ? 'done' : 'streaming'}>
      <div className="chat-bubble chat-bubble--agent chat-bubble--stream">
        <div className="chat-author chat-author--agent">
          {agentFirstName} - {statusLabel}
        </div>
        {hasReasoning ? (
          <div className="chat-stream-section" data-section="reasoning">
            <div className="chat-stream-label">Thinking</div>
            <MarkdownViewer
              content={stream.reasoning}
              className="chat-stream-markdown chat-stream-markdown--reasoning prose prose-sm max-w-none leading-relaxed"
              enableHighlight={false}
            />
          </div>
        ) : null}
        {hasContent ? (
          <div className="chat-stream-section" data-section="reply">
            <div className="chat-stream-label">Reply</div>
            {htmlSource ? (
              <div className="chat-stream-markdown prose prose-sm max-w-none leading-relaxed" dangerouslySetInnerHTML={{ __html: htmlSource }} />
            ) : (
              <MarkdownViewer
                content={stream.content}
                className="chat-stream-markdown prose prose-sm max-w-none leading-relaxed"
                enableHighlight={false}
              />
            )}
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
