import { Fragment, useState } from 'react'
import type { ReactNode } from 'react'
import { ChevronDown, ChevronUp } from 'lucide-react'

import { MarkdownViewer } from '../../common/MarkdownViewer'

export function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="space-y-1.5">
      <p className="tool-chip-panel-title">{title}</p>
      <div className="tool-chip-panel-body">{children}</div>
    </div>
  )
}

export function TruncatedMarkdown({ content, maxLines = 3 }: { content: string; maxLines?: number }) {
  const [isExpanded, setIsExpanded] = useState(false)
  const lines = content.split('\n')
  const needsTruncation = lines.length > maxLines || content.length > 200

  if (!needsTruncation) {
    return <MarkdownViewer content={content} className="prose prose-sm max-w-none" />
  }

  const truncatedContent = isExpanded
    ? content
    : lines.slice(0, maxLines).join('\n').slice(0, 180) + (content.length > 180 ? '…' : '')

  return (
    <div className="space-y-2">
      <div className={isExpanded ? '' : 'line-clamp-3'}>
        <MarkdownViewer content={truncatedContent} className="prose prose-sm max-w-none" />
      </div>
      <button
        type="button"
        onClick={() => setIsExpanded(!isExpanded)}
        className="inline-flex items-center gap-1 text-xs font-medium text-indigo-600 hover:text-indigo-700 transition-colors"
      >
        {isExpanded ? (
          <>
            <ChevronUp className="h-3.5 w-3.5" />
            Show less
          </>
        ) : (
          <>
            <ChevronDown className="h-3.5 w-3.5" />
            Read full assignment
          </>
        )}
      </button>
    </div>
  )
}

export function KeyValueList({ items }: { items: Array<{ label: string; value: ReactNode } | null> }) {
  const filtered = items.filter(Boolean) as Array<{ label: string; value: ReactNode }>
  if (!filtered.length) return null
  return (
    <dl className="grid gap-2 text-sm text-slate-600 sm:grid-cols-[auto_minmax(0,1fr)]">
      {filtered.map(({ label, value }) => (
        <Fragment key={label}>
          <dt className="font-semibold text-slate-700 sm:text-right">{label}</dt>
          <dd className="text-slate-600 sm:pl-4">{value}</dd>
        </Fragment>
      ))}
    </dl>
  )
}
