import { useMemo } from 'react'

import { looksLikeHtml, sanitizeHtml, stripBlockquoteQuotes } from '../../util/sanitize'
import { MarkdownViewer } from '../common/MarkdownViewer'

type MessageContentProps = {
  bodyHtml?: string | null
  bodyText?: string | null
  showEmptyState?: boolean
}

export function MessageContent({ bodyHtml, bodyText, showEmptyState = true }: MessageContentProps) {
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

  if (htmlSource) {
    return <div dangerouslySetInnerHTML={{ __html: htmlSource }} />
  }

  if (normalizedText && normalizedText.trim().length > 0) {
    return <MarkdownViewer content={normalizedText} />
  }

  if (!showEmptyState) {
    return null
  }

  return <p className="text-sm text-slate-400">No content provided.</p>
}
