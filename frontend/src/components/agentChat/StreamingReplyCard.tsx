import { useMemo } from 'react'
import { MarkdownViewer } from '../common/MarkdownViewer'
import { looksLikeHtml, sanitizeHtml } from '../../util/sanitize'

type StreamingReplyCardProps = {
  content: string
  agentFirstName: string
  isStreaming: boolean
}

export function StreamingReplyCard({ content, agentFirstName, isStreaming }: StreamingReplyCardProps) {
  const hasContent = content.trim().length > 0

  const htmlContent = useMemo(() => {
    if (!hasContent || isStreaming) {
      return null
    }
    if (!looksLikeHtml(content)) {
      return null
    }
    return sanitizeHtml(content)
  }, [content, hasContent, isStreaming])

  return (
    <article className="timeline-event chat-event is-agent streaming-reply-event" data-streaming={isStreaming ? 'true' : 'false'}>
      <div className="chat-bubble chat-bubble--agent streaming-reply-bubble">
        <div className="chat-author chat-author--agent">
          {agentFirstName || 'Agent'}
        </div>
        <div className="chat-content prose prose-sm max-w-none leading-relaxed text-slate-800">
          {hasContent ? (
            htmlContent ? (
              <div dangerouslySetInnerHTML={{ __html: htmlContent }} />
            ) : (
              <MarkdownViewer content={content} enableHighlight={!isStreaming} />
            )
          ) : (
            <div className="streaming-reply-typing">
              <span className="streaming-reply-dot" />
              <span className="streaming-reply-dot" />
              <span className="streaming-reply-dot" />
            </div>
          )}
        </div>
      </div>
    </article>
  )
}
