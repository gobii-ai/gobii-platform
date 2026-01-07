import { useMemo } from 'react'
import { MarkdownViewer } from '../common/MarkdownViewer'
import { looksLikeHtml, sanitizeHtml, stripBlockquoteQuotes } from '../../util/sanitize'
import { useTypewriter } from '../../hooks/useTypewriter'

type StreamingReplyCardProps = {
  content: string
  agentFirstName: string
  isStreaming: boolean
}

export function StreamingReplyCard({ content, agentFirstName, isStreaming }: StreamingReplyCardProps) {
  // Typewriter effect: animate text character-by-character for smoother perceived latency
  const { displayedContent, isWaiting } = useTypewriter(content, isStreaming, {
    charsPerFrame: 3,
    frameIntervalMs: 12,
    waitingThresholdMs: 200,
  })

  const hasContent = displayedContent.trim().length > 0

  const hasHtmlPrefix = useMemo(() => {
    const trimmed = displayedContent.trimStart()
    if (!trimmed.startsWith('<')) {
      return false
    }
    const nextChar = trimmed.charAt(1)
    return /[a-zA-Z!?\/]/.test(nextChar)
  }, [displayedContent])

  const shouldRenderHtml = hasContent && (looksLikeHtml(displayedContent) || (isStreaming && hasHtmlPrefix))

  // Strip redundant quotes from blockquotes (e.g., > "text" → > text)
  const normalizedContent = useMemo(() => stripBlockquoteQuotes(displayedContent), [displayedContent])

  const htmlContent = useMemo(() => {
    if (!shouldRenderHtml) {
      return null
    }
    return sanitizeHtml(normalizedContent)
  }, [normalizedContent, shouldRenderHtml])

  if (!hasContent) {
    return null
  }

  return (
    <article
      className="timeline-event chat-event is-agent streaming-reply-event"
      data-streaming={isStreaming ? 'true' : 'false'}
      data-waiting={isWaiting ? 'true' : 'false'}
    >
      <div className="chat-bubble chat-bubble--agent streaming-reply-bubble">
        <div className="chat-author chat-author--agent">
          {agentFirstName || 'Agent'}
        </div>
        <div className="chat-content prose prose-sm max-w-none leading-relaxed text-slate-800">
          {htmlContent ? (
            <div dangerouslySetInnerHTML={{ __html: htmlContent }} />
          ) : (
            <MarkdownViewer content={normalizedContent} enableHighlight={!isStreaming} />
          )}
        </div>
      </div>
    </article>
  )
}
