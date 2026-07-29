import type { MouseEvent as ReactMouseEvent } from 'react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { MarkdownViewer } from '../common/MarkdownViewer'
import { AgentAvatarBadge } from '../common/AgentAvatarBadge'
import { looksLikeHtml, sanitizeHtml, stripBlockquoteQuotes } from '../../util/sanitize'
import { useTypewriter } from '../../hooks/useTypewriter'
import { chatActions } from '../../store/chatSlice'
import { useAppDispatch } from '../../store/hooks'
import { repairUnclosedFence, splitMarkdownBlocks } from './streamingMarkdownBlocks'

const COMMIT_INTERVAL_MS = 150

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

/**
 * While text is revealing, split it into markdown "committed" text (re-committed at ~7 Hz)
 * and a plain-text tail span updated every animation frame — the parser never runs at
 * frame rate, and committed text is further split into blocks so completed blocks are
 * never re-parsed at all.
 */
function useThrottledMarkdown(content: string, revealing: boolean) {
  const [committedMarkdown, setCommittedMarkdown] = useState(content)
  const commitTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const contentRef = useRef(content)
  contentRef.current = content

  const commit = useCallback(() => {
    setCommittedMarkdown(contentRef.current)
    commitTimerRef.current = null
  }, [])

  useEffect(() => {
    if (!revealing) {
      if (commitTimerRef.current !== null) {
        clearTimeout(commitTimerRef.current)
        commitTimerRef.current = null
      }
      setCommittedMarkdown(content)
      return
    }
    if (commitTimerRef.current === null) {
      commitTimerRef.current = setTimeout(commit, COMMIT_INTERVAL_MS)
    }
    return () => {
      if (commitTimerRef.current !== null) {
        clearTimeout(commitTimerRef.current)
        commitTimerRef.current = null
      }
    }
  }, [content, revealing, commit])

  const tailText = revealing && content.length > committedMarkdown.length
    ? content.slice(committedMarkdown.length)
    : ''

  return { committedMarkdown: revealing ? committedMarkdown : content, tailText }
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
  const { displayedContent, isWaiting } = useTypewriter(content, isStreaming && !done, {
    charsPerFrame: 3,
    frameIntervalMs: 12,
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
  const { committedMarkdown, tailText } = useThrottledMarkdown(displayedContent, revealing)

  const normalizedCommitted = useMemo(
    () => stripBlockquoteQuotes(committedMarkdown),
    [committedMarkdown],
  )

  // Completed blocks are stable strings — MarkdownViewer is memoized on content, so only
  // the growing tail block ever re-parses (and only at commit cadence, not frame rate).
  const blocks = useMemo(() => splitMarkdownBlocks(normalizedCommitted), [normalizedCommitted])
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
                  content={index === lastBlockIndex && revealing ? repairUnclosedFence(block) : block}
                  enableHighlight={index !== lastBlockIndex || !revealing}
                />
              ))}
              {tailText && <span>{tailText}</span>}
            </>
          )}
        </div>
      </div>
    </article>
  )
}
