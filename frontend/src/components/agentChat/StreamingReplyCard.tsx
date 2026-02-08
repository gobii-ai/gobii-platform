import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { MarkdownViewer } from '../common/MarkdownViewer'
import { AgentAvatarBadge } from '../common/AgentAvatarBadge'
import { looksLikeHtml, sanitizeHtml, stripBlockquoteQuotes } from '../../util/sanitize'
import { useTypewriter } from '../../hooks/useTypewriter'

const COMMIT_INTERVAL_MS = 150

type StreamingReplyCardProps = {
  content: string
  agentFirstName: string
  agentAvatarUrl?: string | null
  agentColorHex?: string | null
  isStreaming: boolean
}

/**
 * During active streaming, split the content into a "committed" portion
 * (rendered through expensive MarkdownViewer at ~7 Hz) and a plain-text
 * "tail" (rendered as a cheap <span>, updated every frame).
 * This prevents markdown re-parsing on every character arrival.
 */
function useThrottledMarkdown(content: string, isStreaming: boolean) {
  const [committedMarkdown, setCommittedMarkdown] = useState(content)
  const commitTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const contentRef = useRef(content)
  contentRef.current = content

  const commit = useCallback(() => {
    setCommittedMarkdown(contentRef.current)
    commitTimerRef.current = null
  }, [])

  useEffect(() => {
    if (!isStreaming) {
      // Streaming ended — final commit of the complete content.
      if (commitTimerRef.current !== null) {
        clearTimeout(commitTimerRef.current)
        commitTimerRef.current = null
      }
      setCommittedMarkdown(content)
      return
    }

    // Schedule the next commit if one isn't already pending.
    if (commitTimerRef.current === null) {
      commitTimerRef.current = setTimeout(commit, COMMIT_INTERVAL_MS)
    }

    return () => {
      if (commitTimerRef.current !== null) {
        clearTimeout(commitTimerRef.current)
        commitTimerRef.current = null
      }
    }
  }, [content, isStreaming, commit])

  // The tail is the text received since the last commit.
  const tailText = isStreaming && content.length > committedMarkdown.length
    ? content.slice(committedMarkdown.length)
    : ''

  return { committedMarkdown, tailText }
}

export function StreamingReplyCard({ content, agentFirstName, agentAvatarUrl, agentColorHex, isStreaming }: StreamingReplyCardProps) {
  // Typewriter for non-streaming reveal of completed messages.
  const { displayedContent, isWaiting } = useTypewriter(content, isStreaming, {
    charsPerFrame: 3,
    frameIntervalMs: 12,
    waitingThresholdMs: 200,
  })

  // Throttled markdown for active streaming — avoids full re-parse each frame.
  const { committedMarkdown, tailText } = useThrottledMarkdown(content, isStreaming)

  // During streaming, use raw content for presence checks; otherwise use typewriter output.
  const effectiveContent = isStreaming ? content : displayedContent
  const hasContent = effectiveContent.trim().length > 0

  const hasHtmlPrefix = useMemo(() => {
    const trimmed = effectiveContent.trimStart()
    if (!trimmed.startsWith('<')) {
      return false
    }
    const nextChar = trimmed.charAt(1)
    return /[a-zA-Z!?\/]/.test(nextChar)
  }, [effectiveContent])

  const shouldRenderHtml = hasContent && (looksLikeHtml(effectiveContent) || (isStreaming && hasHtmlPrefix))

  // Strip redundant quotes from blockquotes (e.g., > "text" → > text)
  const normalizedContent = useMemo(
    () => stripBlockquoteQuotes(isStreaming ? committedMarkdown : displayedContent),
    [isStreaming, committedMarkdown, displayedContent],
  )

  const htmlContent = useMemo(() => {
    if (!shouldRenderHtml) {
      return null
    }
    return sanitizeHtml(isStreaming ? content : normalizedContent)
  }, [isStreaming, content, normalizedContent, shouldRenderHtml])

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
          <AgentAvatarBadge
            name={agentFirstName || 'Agent'}
            avatarUrl={agentAvatarUrl}
            className="chat-author-avatar"
            imageClassName="chat-author-avatar-image"
            textClassName="chat-author-avatar-text"
            style={{ background: agentColorHex || '#6366f1' }}
          />
          {agentFirstName || 'Agent'}
        </div>
        <div className="chat-content prose prose-sm max-w-none leading-relaxed text-slate-800">
          {htmlContent ? (
            <div dangerouslySetInnerHTML={{ __html: htmlContent }} />
          ) : (
            <>
              <MarkdownViewer content={normalizedContent} enableHighlight={!isStreaming} />
              {tailText && <span>{tailText}</span>}
            </>
          )}
        </div>
      </div>
    </article>
  )
}
