import { useEffect, useRef } from 'react'
import { useTypewriter } from '../../hooks/useTypewriter'

type StreamingThinkingCardProps = {
  reasoning: string
  isStreaming: boolean
}

export function StreamingThinkingCard({ reasoning, isStreaming }: StreamingThinkingCardProps) {
  const contentRef = useRef<HTMLDivElement>(null)

  const { displayedContent, isAnimating } = useTypewriter(reasoning, isStreaming, {
    charsPerFrame: 1,
    frameIntervalMs: 18,
    waitingThresholdMs: 120,
  })

  // Auto-scroll to keep the latest content visible in the 3-line window
  useEffect(() => {
    const el = contentRef.current
    if (el) {
      el.scrollTop = el.scrollHeight
    }
  }, [displayedContent])

  if (!displayedContent.trim()) {
    return null
  }

  const showCursor = isStreaming && isAnimating

  return (
    <div className="streaming-thinking-card" data-streaming={isStreaming ? 'true' : 'false'}>
      <div className="streaming-thinking-card__header">
        <span className="streaming-thinking-card__pulse" aria-hidden="true" />
        <span className="streaming-thinking-card__label">Thinking</span>
      </div>
      <div ref={contentRef} className="streaming-thinking-card__content">
        {displayedContent}
        {showCursor ? <span className="streaming-thinking-card__cursor" aria-hidden="true" /> : null}
      </div>
    </div>
  )
}
