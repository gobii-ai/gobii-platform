import { useMemo } from 'react'

import { looksLikeHtml, sanitizeHtml } from '../../util/sanitize'
import { MarkdownViewer } from '../common/MarkdownViewer'

type MessageContentProps = {
  bodyHtml?: string | null
  bodyText?: string | null
}

export function MessageContent({ bodyHtml, bodyText }: MessageContentProps) {
  const htmlSource = useMemo(() => {
    if (bodyHtml && bodyHtml.trim().length > 0) {
      return sanitizeHtml(bodyHtml)
    }
    if (bodyText && looksLikeHtml(bodyText)) {
      return sanitizeHtml(bodyText)
    }
    return null
  }, [bodyHtml, bodyText])

  if (htmlSource) {
    return <div dangerouslySetInnerHTML={{ __html: htmlSource }} />
  }

  if (bodyText && bodyText.trim().length > 0) {
    return <MarkdownViewer content={bodyText} />
  }

  return <p className="text-sm text-slate-400">No content provided.</p>
}
