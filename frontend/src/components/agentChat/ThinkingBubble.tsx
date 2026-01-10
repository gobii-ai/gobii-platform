import { useEffect, useRef, useLayoutEffect } from 'react'
import { useTypewriter } from '../../hooks/useTypewriter'

type ThinkingBubbleProps = {
  reasoning: string
  isStreaming: boolean
  collapsed: boolean
  onToggle: () => void
}

/**
 * ThinkingBubble displays AI reasoning/thinking content in a consistent
 * "thinking window" style with typewriter text animation.
 *
 * The component shows a monospace text window that can be expanded/collapsed.
 * Content animates in with a typewriter effect during streaming.
 */
export function ThinkingBubble({ reasoning, isStreaming, collapsed, onToggle }: ThinkingBubbleProps) {
  const prevStreamingRef = useRef(isStreaming)
  const contentRef = useRef<HTMLDivElement>(null)

  // Typewriter effect - always enabled when there's content to animate
  // Uses faster settings for smooth perceived streaming
  const { displayedContent, isWaiting } = useTypewriter(reasoning, isStreaming, {
    charsPerFrame: 4,
    frameIntervalMs: 10,
    waitingThresholdMs: 200,
    disabled: false,
  })

  const hasContent = displayedContent.trim().length > 0
  const hasTargetContent = reasoning.trim().length > 0
  const showContent = hasContent && !collapsed

  // Auto-scroll content to bottom when streaming new content
  useLayoutEffect(() => {
    if (isStreaming && !collapsed && contentRef.current) {
      contentRef.current.scrollTop = contentRef.current.scrollHeight
    }
  }, [isStreaming, collapsed, displayedContent])

  // Auto-collapse when streaming ends (if expanded)
  useEffect(() => {
    if (prevStreamingRef.current && !isStreaming && !collapsed) {
      onToggle()
    }
    prevStreamingRef.current = isStreaming
  }, [isStreaming, collapsed, onToggle])

  // Don't render if no content and not streaming
  if (!hasTargetContent && !isStreaming) {
    return null
  }

  return (
    <article
      className="timeline-event chat-event is-agent thinking-event"
      data-collapsed={collapsed ? 'true' : 'false'}
    >
      <div
        className="thinking-window"
        data-collapsed={collapsed ? 'true' : 'false'}
        data-streaming={isStreaming ? 'true' : 'false'}
        data-waiting={isWaiting ? 'true' : 'false'}
      >
        <button
          type="button"
          className="thinking-window-header"
          onClick={onToggle}
          aria-expanded={!collapsed}
        >
          <span className="thinking-window-icon" aria-hidden="true">
            {isStreaming ? (
              <span className="thinking-window-pulse" />
            ) : (
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path strokeLinecap="round" strokeLinejoin="round" d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
              </svg>
            )}
          </span>
          <span className="thinking-window-label">
            {isStreaming ? 'Thinking...' : 'Thinking'}
          </span>
          <span
            className="thinking-window-chevron"
            aria-hidden="true"
            data-collapsed={collapsed ? 'true' : 'false'}
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
            </svg>
          </span>
        </button>
        {!collapsed && (
          <div ref={contentRef} className="thinking-window-content">
            {displayedContent}
          </div>
        )}
      </div>
    </article>
  )
}
