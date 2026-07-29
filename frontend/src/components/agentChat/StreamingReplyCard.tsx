import type { MouseEvent as ReactMouseEvent } from 'react'
import { useCallback, useEffect, useMemo } from 'react'
import { MarkdownViewer } from '../common/MarkdownViewer'
import { AgentAvatarBadge } from '../common/AgentAvatarBadge'
import { looksLikeHtml, sanitizeHtml, stripBlockquoteQuotes } from '../../util/sanitize'
import { useTypewriter } from '../../hooks/useTypewriter'
import { chatActions } from '../../store/chatSlice'
import { useAppDispatch } from '../../store/hooks'
import { repairIncompleteMarkdown, splitMarkdownBlocks } from './streamingMarkdownBlocks'

type StreamingReplyCardProps = {
  content: string
  agentFirstName: string
  agentAvatarUrl?: string | null
  /** More content may still arrive. */
  isStreaming: boolean
  /** The stream finished; the card may still be revealing and awaiting handoff. */
  done?: boolean
  streamId?: string | null
  agentId?: string | null
  /** The persisted message this stream became is rendered (suppressed) in the timeline;
   *  once the reveal catches up the card dispatches the one-commit swap (bug #510). */
  handoffReady?: boolean
  onLinkClick?: (href: string) => boolean | void
}

function shouldInterceptLinkClick(event: ReactMouseEvent<HTMLElement>): boolean {
  return event.button === 0
    && !event.defaultPrevented
    && !event.metaKey
    && !event.ctrlKey
    && !event.altKey
    && !event.shiftKey
}

export function StreamingReplyCard({
  content,
  agentFirstName,
  agentAvatarUrl,
  isStreaming,
  done = false,
  streamId = null,
  agentId = null,
  handoffReady = false,
  onLinkClick,
}: StreamingReplyCardProps) {
  const dispatch = useAppDispatch()

  // One reveal path for every arrival pattern: providers may stream the body
  // incrementally or burst it whole at the end (tool-call arguments often arrive as one
  // chunk) — the typewriter decouples what the user sees from how the network delivered
  // it, and accelerates to finish once the stream is done.
  // ~40fps reveal: every frame feeds the markdown renderer directly (no plain-text
  // tail — the in-flight text must LOOK like markdown), and block memoization bounds the
  // per-frame parse to just the growing tail block.
  const { displayedContent, isWaiting } = useTypewriter(content, isStreaming && !done, {
    charsPerFrame: 6,
    frameIntervalMs: 24,
    waitingThresholdMs: 200,
    finishBoost: 4,
  })

  const hasContent = displayedContent.trim().length > 0 || content.trim().length > 0

  const hasHtmlPrefix = useMemo(() => {
    const trimmed = content.trimStart()
    if (!trimmed.startsWith('<')) {
      return false
    }
    return /[a-zA-Z!?\/]/.test(trimmed.charAt(1))
  }, [content])
  const shouldRenderHtml = hasContent && (looksLikeHtml(content) || hasHtmlPrefix)

  const revealComplete = shouldRenderHtml || displayedContent.length >= content.length

  // The swap: reveal caught up + persisted message rendered (suppressed) → clear the
  // stream. The suppression lifts and this card unmounts in the same reducer commit, so
  // the reply never flashes or double-renders.
  useEffect(() => {
    if (done && handoffReady && revealComplete && streamId && agentId) {
      dispatch(chatActions.streamHandedOff({ agentId, streamId }))
    }
  }, [agentId, dispatch, done, handoffReady, revealComplete, streamId])

  const revealing = !revealComplete

  const normalizedDisplayed = useMemo(
    () => stripBlockquoteQuotes(displayedContent),
    [displayedContent],
  )

  // Completed blocks are stable strings — MarkdownViewer is memoized on content, so only
  // the growing tail block re-parses each reveal frame. The tail block is repaired
  // (fences/bold/italic/code closed, trailing half-links hidden) so the in-flight text
  // renders as styled markdown the whole time, never as raw syntax.
  const blocks = useMemo(() => splitMarkdownBlocks(normalizedDisplayed), [normalizedDisplayed])
  const lastBlockIndex = blocks.length - 1

  const htmlContent = useMemo(() => {
    if (!shouldRenderHtml) {
      return null
    }
    return sanitizeHtml(content)
  }, [content, shouldRenderHtml])

  const handleContentClick = useCallback((event: ReactMouseEvent<HTMLElement>) => {
    if (!onLinkClick || !shouldInterceptLinkClick(event)) {
      return
    }
    const target = event.target
    if (!(target instanceof Element)) {
      return
    }
    const anchor = target.closest('a[href]')
    if (!(anchor instanceof HTMLAnchorElement)) {
      return
    }
    const href = anchor.getAttribute('href')
    if (!href) {
      return
    }
    if (onLinkClick(href)) {
      event.preventDefault()
    }
  }, [onLinkClick])

  if (!hasContent) {
    return null
  }

  return (
    <article
      className="timeline-event chat-event is-agent streaming-reply-event"
      data-streaming={isStreaming && !done ? 'true' : 'false'}
      data-revealing={revealing ? 'true' : 'false'}
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
          />
          {agentFirstName || 'Agent'}
        </div>
        <div className="chat-content prose prose-sm max-w-none leading-relaxed text-slate-800" onClick={handleContentClick}>
          {htmlContent ? (
            <div dangerouslySetInnerHTML={{ __html: htmlContent }} />
          ) : (
            <>
              {blocks.map((block, index) => (
                <MarkdownViewer
                  key={`block-${index}`}
                  content={index === lastBlockIndex && revealing ? repairIncompleteMarkdown(block) : block}
                  enableHighlight={index !== lastBlockIndex || !revealing}
                />
              ))}
            </>
          )}
        </div>
      </div>
    </article>
  )
}
