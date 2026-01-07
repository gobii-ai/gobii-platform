import { useMemo, useState, useEffect, useRef } from 'react'

import { looksLikeHtml, sanitizeHtml, stripBlockquoteQuotes } from '../../util/sanitize'
import { MarkdownViewer } from '../common/MarkdownViewer'

type MessageContentProps = {
  bodyHtml?: string | null
  bodyText?: string | null
  showEmptyState?: boolean
  /** Animate text in with fast typewriter effect on mount */
  animateIn?: boolean
}

/**
 * Fast typewriter for non-streaming messages.
 * Reveals text quickly on mount to feel "streaming" even though it's not.
 */
function useFastReveal(content: string, enabled: boolean) {
  const [displayedLength, setDisplayedLength] = useState(enabled ? 0 : content.length)
  const animationRef = useRef<number | null>(null)
  const hasAnimatedRef = useRef(false)

  useEffect(() => {
    // Only animate once on initial mount
    if (!enabled || hasAnimatedRef.current) {
      setDisplayedLength(content.length)
      return
    }

    hasAnimatedRef.current = true
    let currentLength = 0
    const charsPerFrame = 12 // Fast: ~720 chars/sec at 60fps
    let lastTime = 0
    const frameInterval = 16

    const animate = (timestamp: number) => {
      if (timestamp - lastTime < frameInterval) {
        animationRef.current = requestAnimationFrame(animate)
        return
      }
      lastTime = timestamp

      currentLength = Math.min(currentLength + charsPerFrame, content.length)
      setDisplayedLength(currentLength)

      if (currentLength < content.length) {
        animationRef.current = requestAnimationFrame(animate)
      }
    }

    animationRef.current = requestAnimationFrame(animate)

    return () => {
      if (animationRef.current) {
        cancelAnimationFrame(animationRef.current)
      }
    }
  }, [content, enabled])

  // If content changes after initial animation, show it all
  useEffect(() => {
    if (hasAnimatedRef.current && displayedLength < content.length) {
      setDisplayedLength(content.length)
    }
  }, [content, displayedLength])

  return content.slice(0, displayedLength)
}

export function MessageContent({ bodyHtml, bodyText, showEmptyState = true, animateIn = false }: MessageContentProps) {
  const htmlSource = useMemo(() => {
    if (bodyHtml && bodyHtml.trim().length > 0) {
      return sanitizeHtml(bodyHtml)
    }
    if (bodyText && looksLikeHtml(bodyText)) {
      return sanitizeHtml(bodyText)
    }
    return null
  }, [bodyHtml, bodyText])

  // Strip redundant quotes from blockquotes (e.g., > "text" → > text)
  const normalizedText = useMemo(() => {
    if (!bodyText) return null
    return stripBlockquoteQuotes(bodyText)
  }, [bodyText])

  // Fast reveal animation for markdown content (not HTML)
  const displayedText = useFastReveal(normalizedText || '', animateIn && !htmlSource)

  if (htmlSource) {
    return <div dangerouslySetInnerHTML={{ __html: htmlSource }} />
  }

  if (normalizedText && normalizedText.trim().length > 0) {
    return <MarkdownViewer content={displayedText} />
  }

  if (!showEmptyState) {
    return null
  }

  return <p className="text-sm text-slate-400">No content provided.</p>
}
