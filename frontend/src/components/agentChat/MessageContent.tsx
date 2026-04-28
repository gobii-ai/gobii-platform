import type { MouseEvent as ReactMouseEvent } from 'react'
import { useMemo, useState, useEffect, useRef, useCallback } from 'react'

import { sanitizeHtml, stripBlockquoteQuotes } from '../../util/sanitize'
import { MarkdownViewer } from '../common/MarkdownViewer'

type MessageContentProps = {
  bodyHtml?: string | null
  bodyText?: string | null
  showEmptyState?: boolean
  /** Animate text in with fast typewriter effect on mount */
  animateIn?: boolean
  onLinkClick?: (href: string) => boolean | void
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

function shouldInterceptLinkClick(event: ReactMouseEvent<HTMLElement>): boolean {
  return event.button === 0
    && !event.defaultPrevented
    && !event.metaKey
    && !event.ctrlKey
    && !event.altKey
    && !event.shiftKey
}

export function MessageContent({
  bodyHtml,
  bodyText,
  showEmptyState = true,
  animateIn = false,
  onLinkClick,
}: MessageContentProps) {
  // Only use HTML rendering if backend explicitly provided bodyHtml (e.g., for email channel).
  // For other channels, bodyText may contain inline HTML like <br> which the markdown renderer handles.
  const htmlSource = useMemo(() => {
    if (bodyHtml && bodyHtml.trim().length > 0) {
      return sanitizeHtml(bodyHtml)
    }
    return null
  }, [bodyHtml])

  // Strip redundant quotes from blockquotes (e.g., > "text" → > text)
  const normalizedText = useMemo(() => {
    if (!bodyText) return null
    return stripBlockquoteQuotes(bodyText)
  }, [bodyText])

  // Fast reveal animation for markdown content (not HTML)
  const displayedText = useFastReveal(normalizedText || '', animateIn && !htmlSource)

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

  if (htmlSource) {
    return <div onClick={handleContentClick} dangerouslySetInnerHTML={{ __html: htmlSource }} />
  }

  if (normalizedText && normalizedText.trim().length > 0) {
    return (
      <div onClick={handleContentClick}>
        <MarkdownViewer content={displayedText} />
      </div>
    )
  }

  if (!showEmptyState) {
    return null
  }

  return <p className="text-sm text-slate-400">No content provided.</p>
}
