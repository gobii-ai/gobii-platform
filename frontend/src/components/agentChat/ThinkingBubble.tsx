import { useEffect, useRef } from 'react'
import { MarkdownViewer } from '../common/MarkdownViewer'

type ThinkingBubbleProps = {
  reasoning: string
  isStreaming: boolean
  collapsed: boolean
  onToggle: () => void
}

export function ThinkingBubble({ reasoning, isStreaming, collapsed, onToggle }: ThinkingBubbleProps) {
  const prevStreamingRef = useRef(isStreaming)
  const hasContent = reasoning.trim().length > 0

  useEffect(() => {
    if (prevStreamingRef.current && !isStreaming && !collapsed) {
      onToggle()
    }
    prevStreamingRef.current = isStreaming
  }, [isStreaming, collapsed, onToggle])

  if (!hasContent && !isStreaming) {
    return null
  }

  return (
    <article className="timeline-event chat-event is-agent thinking-event" data-collapsed={collapsed ? 'true' : 'false'}>
      <div className="thinking-bubble" data-collapsed={collapsed ? 'true' : 'false'} data-streaming={isStreaming ? 'true' : 'false'}>
        <button
          type="button"
          className="thinking-bubble-header"
          onClick={onToggle}
          aria-expanded={!collapsed}
        >
          <span className="thinking-bubble-icon" aria-hidden="true">
            {isStreaming ? (
              <span className="thinking-pulse" />
            ) : (
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path strokeLinecap="round" strokeLinejoin="round" d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
              </svg>
            )}
          </span>
          <span className="thinking-bubble-label">
            {isStreaming ? 'Thinking...' : 'Thinking'}
          </span>
          <span className="thinking-bubble-chevron" aria-hidden="true" data-collapsed={collapsed ? 'true' : 'false'}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
            </svg>
          </span>
        </button>
        {!collapsed && hasContent && (
          <div className="thinking-bubble-content">
            <MarkdownViewer content={reasoning} className="thinking-bubble-markdown" enableHighlight={false} />
          </div>
        )}
      </div>
    </article>
  )
}
